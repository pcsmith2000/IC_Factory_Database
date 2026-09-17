"""Enrichment stages. Network-free: the area maths is pinned against hand-computed truth."""
import math
import pytest
from pipeline.enrich.footprint import wkt_area_m2, file_for, M2_FT2


def _box(lat, lon, d):
    return f"POLYGON(({lon} {lat},{lon+d} {lat},{lon+d} {lat+d},{lon} {lat+d},{lon} {lat}))"


@pytest.mark.parametrize("lat", [0.0, 34.6, 45.0, 60.0])
def test_area_tracks_latitude(lat):
    """DuckDB's ST_Area_Spheroid returns one area for this polygon at every latitude — right at the
    equator, 21% high at 34N, 99% high at 60N. A national dataset would inflate silently, so the
    area must actually shrink as the meridians converge."""
    d = 0.001
    got = wkt_area_m2(_box(lat, -82.6, d))
    truth = (d * 111320) * (d * 111320 * math.cos(math.radians(lat + d / 2)))
    assert abs(got - truth) / truth < 0.005, f"at {lat}N: {got:,.0f} vs {truth:,.0f} m2"


def test_area_at_60N_is_half_that_at_the_equator():
    a0 = wkt_area_m2(_box(0.0, 0.0, 0.001))
    a60 = wkt_area_m2(_box(60.0, 0.0, 0.001))
    assert 0.48 < a60 / a0 < 0.52


def test_holes_are_subtracted():
    outer = "0 0,0.001 0,0.001 0.001,0 0.001,0 0"
    inner = "0.0002 0.0002,0.0002 0.0008,0.0008 0.0008,0.0008 0.0002,0.0002 0.0002"
    solid = wkt_area_m2(f"POLYGON(({outer}))")
    holed = wkt_area_m2(f"POLYGON(({outer}),({inner}))")
    assert holed < solid and abs(holed - solid * 0.64) / solid < 0.02


def test_multipolygon_sums_its_parts():
    one = wkt_area_m2(_box(34.6, -82.6, 0.001))
    two = wkt_area_m2(f"MULTIPOLYGON(({_box(34.6,-82.6,0.001)[8:-1]}),({_box(34.6,-82.5,0.001)[8:-1]}))")
    assert abs(two - 2 * one) / one < 0.01


def test_file_index_lookup_picks_the_covering_file():
    idx = [{"file": "a", "xmin": -180, "xmax": -175, "ymin": -78, "ymax": -21},
           {"file": "b", "xmin": -84.6, "xmax": -81.9, "ymin": 30.0, "ymax": 35.8}]
    assert file_for(idx, 34.611077, -82.604892) == "b"
    assert file_for(idx, 47.6, -122.3) is None     # not covered -> caller records a reason


def test_square_feet_conversion():
    assert abs(wkt_area_m2(_box(34.6, -82.6, 0.001)) * M2_FT2 - 109_500) < 3_000


# --- stage 9: a located address is only as good as its citation ------------------------------

class _Block:
    def __init__(self, text=None, content=None):
        if text is not None: self.text = text
        if content is not None: self.content = content


class _Result:
    def __init__(self, url): self.url = url


class _FakeClient:
    """Stands in for the gateway. `reply` is the JSON the model returns; `visited` is what the
    web_search tool actually opened."""
    def __init__(self, reply, visited=()):
        self._reply, self._visited = reply, visited
        self.messages = self

    def create(self, **kw):
        self.kw = kw
        blocks = [_Block(content=[_Result(u) for u in self._visited]),
                  _Block(text=json.dumps(self._reply))]
        return type("M", (), {"content": blocks})()


import json
from pipeline.enrich import locate

ROW = [{"facility_id": "IC-00001", "name": "Acme Modular", "city": "Elkhart", "state": "IN"}]
GOOD = {"found": True, "address": "1200 Industrial Blvd", "city": "Elkhart", "state": "IN",
        "source_url": "https://acme.example/contact", "quote": "Our plant at 1200 Industrial Blvd.",
        "confidence": 0.9, "reason": "contact page"}


def test_a_cited_address_is_stored_with_its_evidence():
    # page verification is exercised separately below; this pins what gets stored
    rep = locate.run(ROW, client=_FakeClient(GOOD, ["https://acme.example/contact"]),
                     verify_page=False)
    assert rep["located"] == 1
    a = rep["assertions"][0]
    assert a["field"] == "address" and a["value"] == "1200 Industrial Blvd"
    assert a["source_id"] == "enrich:locate" and a["basis"] == "web_cited"
    assert "https://acme.example/contact" in a["evidence"] and "Our plant at" in a["evidence"]


def test_the_search_tool_is_actually_offered():
    c = _FakeClient(GOOD, ["https://acme.example/contact"])
    locate.run(ROW, client=c, verify_page=False)
    assert c.kw["tools"] == [locate.WEB_SEARCH_TOOL]


@pytest.mark.parametrize("reply,visited,why", [
    ({**GOOD, "source_url": "", "quote": ""}, ["https://acme.example/contact"], "no citation"),
    ({**GOOD, "source_url": "https://elsewhere.example/x"}, ["https://acme.example/contact"],
     "cited a page search did not visit"),
    ({**GOOD, "confidence": 0.3}, ["https://acme.example/contact"], "confidence"),
    ({**GOOD, "address": "PO Box 12"}, ["https://acme.example/contact"], "not a street address"),
    ({"found": False, "reason": "no plant page found"}, [], "no plant page found"),
])
def test_an_uncited_or_unsupported_answer_is_never_stored(reply, visited, why):
    """E1. A recalled address is indistinguishable from a read one once it is in golden_facility,
    so everything that cannot show its source is dropped here."""
    rep = locate.run(ROW, client=_FakeClient(reply, visited), verify_page=False)
    assert rep["located"] == 0 and rep["assertions"] == []
    assert why in rep["rejected"][0]["why"]


def test_one_failing_facility_does_not_fail_the_stage():
    class Boom(_FakeClient):
        def create(self, **kw): raise RuntimeError("gateway 429")
    rep = locate.run(ROW, client=Boom(GOOD), verify_page=False)
    assert rep["located"] == 0 and "gateway 429" in rep["rejected"][0]["why"]


# --- the gates block rather than advise ------------------------------------------------------

from pipeline.enrich import gates

CITED = {"source_id": "enrich:locate", "field": "address", "value": "1 Main St",
         "evidence": "https://x.example/c :: our plant at 1 Main St"}
ROOFTOP = {"source_id": "geocode:geocodio", "field": "lat_lon", "value": "1,2", "basis": "rooftop"}
FOOTPRINT = {"source_id": "overture:building", "field": "building_sqft", "value": "40000",
             "evidence": "2026-08-19.0:abc123@0.0m"}
FLAG = {"source_id": "enrich:existence", "field": "existence_flag", "value": "review"}


def test_all_gates_pass_on_well_formed_assertions():
    rows = [{"address": "1 Main St", "lat_lon": "1,2"}]
    assert all(r.passed for r in gates.run_all([CITED, ROOFTOP, FOOTPRINT, FLAG], rows, rows))


def test_e1_fails_an_address_with_no_quote():
    bad = {**CITED, "evidence": "https://x.example/c"}
    assert not gates.e1_every_located_address_is_cited([bad]).passed


def test_e2_fails_a_coordinate_that_is_not_a_rooftop_geocode():
    """A nearest_rooftop_match landed on a different parcel 60% of the time in the verified set."""
    bad = {**ROOFTOP, "basis": "nearest_rooftop_match"}
    assert not gates.e2_no_coordinate_from_a_non_rooftop_geocode([bad]).passed


def test_e3_fails_a_footprint_with_no_building_id():
    assert not gates.e3_every_footprint_names_its_building([{**FOOTPRINT, "evidence": ""}]).passed


def test_e4_fails_when_a_run_would_lose_a_field():
    before = [{"address": "1 Main St", "lat_lon": "1,2"}, {"address": "2 Main St", "lat_lon": ""}]
    after = [{"address": "1 Main St", "lat_lon": "1,2"}, {"address": "", "lat_lon": ""}]
    r = gates.e4_enrichment_never_removes_a_field(before, after)
    assert not r.passed and "a field was lost" in r.summary


def test_e4_passes_when_a_run_only_adds():
    before = [{"address": "1 Main St", "lat_lon": ""}]
    after = [{"address": "1 Main St", "lat_lon": "1,2"}]
    assert gates.e4_enrichment_never_removes_a_field(before, after).passed


def test_e5_fails_anything_stage_12_writes_that_is_not_advisory():
    assert not gates.e5_existence_is_advisory([{**FLAG, "value": "retired"}]).passed
    assert not gates.e5_existence_is_advisory([{**FLAG, "field": "status"}]).passed


def test_append_records_the_citation_so_provenance_can_be_walked():
    """fact_assertions has no evidence column. Without a ref_source_row entry, gate E1 checks a
    citation and the database then forgets it, and nothing can answer why an address was believed."""
    from pipeline.enrich import _db
    seen = []

    class FakeDB:
        last_row_count = 1
        def query(self, sql, params=()):
            seen.append((sql, params)); return []

    a = _db.assertion("IC-1", "address", "1 Main St", source_id="enrich:locate",
                      basis="web_cited", confidence=0.9,
                      evidence="https://x.example/c :: our plant at 1 Main St")
    _db.append(FakeDB(), [a], "rel-1")

    ev = next(p for sql, p in seen if "ref_source_row" in sql)
    assert ev[0] == a["row_hash"] and ev[1] == "enrich:locate"
    assert ev[2] == "https://x.example/c"                 # the page
    assert ev[3] == "our plant at 1 Main St"              # the sentence on it
    assert ev[5] == "IC-1"
    assert any("fact_assertions" in sql for sql, _ in seen)


# --- the check E1 cannot make: does the cited page really say this? --------------------------

def test_page_verification_accepts_an_address_that_is_on_the_page(monkeypatch):
    monkeypatch.setattr(locate, "_page_states_the_address", lambda u, a, timeout=20: True)
    rep = locate.run(ROW, client=_FakeClient(GOOD, ["https://acme.example/contact"]))
    assert rep["located"] == 1


def test_page_verification_rejects_an_address_the_page_does_not_contain(monkeypatch):
    """Search visiting a URL proves the page exists, not that it says what the model claims. A real
    page with a misattributed address passes every other check."""
    monkeypatch.setattr(locate, "_page_states_the_address", lambda u, a, timeout=20: False)
    rep = locate.run(ROW, client=_FakeClient(GOOD, ["https://acme.example/contact"]))
    assert rep["located"] == 0
    assert "does not contain" in rep["rejected"][0]["why"]


def test_an_unreadable_page_is_not_treated_as_a_lie(monkeypatch):
    """A page that will not load is not evidence of dishonesty, but it is not evidence of the
    address either, so the assertion is still withheld — with a reason that says which it is."""
    monkeypatch.setattr(locate, "_page_states_the_address", lambda u, a, timeout=20: None)
    rep = locate.run(ROW, client=_FakeClient(GOOD, ["https://acme.example/contact"]))
    assert rep["located"] == 0 and "could not read" in rep["rejected"][0]["why"]


def test_a_street_suffix_spelling_difference_is_not_a_fabrication(monkeypatch):
    """"1200 Industrial Blvd" against a page saying "1200 Industrial Boulevard" is a formatting
    difference, not a wrong address."""
    page = "<p>Our plant is at 1200 Industrial Boulevard, Waco TX.</p>"
    class R:
        def read(self, n=None): return page.encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: R())
    assert locate._page_states_the_address("https://x.example", "1200 Industrial Blvd") is True
    assert locate._page_states_the_address("https://x.example", "99 Nowhere Rd") is False


def test_footprint_defers_rather_than_drops_when_the_file_ceiling_is_hit(monkeypatch):
    """Cost is one S3 read per distinct region, about a minute each, so a nationally spread run
    overruns the job long before it runs out of facilities. Points beyond the ceiling must come
    back with a reason so the next run takes them, not vanish."""
    from pipeline.enrich import footprint
    idx = [{"file": f"s3://f{i}", "xmin": i, "xmax": i + 1, "ymin": 0, "ymax": 1} for i in range(5)]
    monkeypatch.setattr(footprint, "build_index", lambda release=None, cache=None: idx)
    monkeypatch.setattr(footprint, "_connect", lambda: (_ for _ in ()).throw(
        AssertionError("must not open a connection when every point is deferred")))
    pts = [{"facility_id": f"IC-{i}", "lat": 0.5, "lon": i + 0.5} for i in range(5)]
    res = footprint.measure(pts, cache=None, max_files=0)
    assert len(res) == 5
    assert all(r["building_sqft"] is None for r in res)
    assert all(r["reason"] == "deferred: file ceiling reached" for r in res)
    assert {r["facility_id"] for r in res} == {p["facility_id"] for p in pts}
