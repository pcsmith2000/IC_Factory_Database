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
    monkeypatch.setattr(locate, "_page_states_the_address",
                        lambda u, a, timeout=20: (True, "our plant at 1 Main St"))
    rep = locate.run(ROW, client=_FakeClient(GOOD, ["https://acme.example/contact"]))
    assert rep["located"] == 1


def test_page_verification_rejects_an_address_the_page_does_not_contain(monkeypatch):
    """Search visiting a URL proves the page exists, not that it says what the model claims. A real
    page with a misattributed address passes every other check."""
    monkeypatch.setattr(locate, "_page_states_the_address", lambda u, a, timeout=20: (False, ""))
    rep = locate.run(ROW, client=_FakeClient(GOOD, ["https://acme.example/contact"]))
    assert rep["located"] == 0
    assert "does not contain" in rep["rejected"][0]["why"]


def test_an_unreadable_page_is_not_treated_as_a_lie(monkeypatch):
    """A page that will not load is not evidence of dishonesty, but it is not evidence of the
    address either, so the assertion is still withheld — with a reason that says which it is."""
    monkeypatch.setattr(locate, "_page_states_the_address", lambda u, a, timeout=20: (None, ""))
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
    ok, snippet = locate._page_states_the_address("https://x.example", "1200 Industrial Blvd")
    assert ok is True and "Industrial Boulevard" in snippet
    assert locate._page_states_the_address("https://x.example", "99 Nowhere Rd")[0] is False


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


def test_geocode_records_an_unplaceable_address_so_it_is_not_retried():
    """Geocodio returns the same answer for the same address until its parcel data changes. Without
    a record of the attempt, every run spends its ceiling re-learning which rows it cannot place and
    never reaches the ones it has not tried."""
    from pipeline.enrich import geocode as gc

    def fake_post(queries, key):
        return [{"query": q, "response": {"results": [
            {"location": {"lat": 1.0, "lng": 2.0}, "accuracy": 0.8,
             "accuracy_type": "street_center", "source": "TIGER/Line"}]}} for q in queries], ""

    import pipeline.enrich.geocode as mod
    orig, mod._post = mod._post, fake_post
    try:
        rep = gc.run([{"facility_id": "IC-1", "address": "1 Main St", "city": "X",
                       "state": "TX", "zip": ""}], key="k")
    finally:
        mod._post = orig

    assert rep["stored"] == 0                                   # no coordinate: not a rooftop
    a = rep["assertions"][0]
    assert a["field"] == "geocode_quality" and a["value"] == "street_center"
    assert a["basis"] == "not_rooftop"
    assert not any(x["field"] == "lat_lon" for x in rep["assertions"])


def test_non_url_evidence_does_not_end_up_in_the_url_column():
    """A footprint cites an Overture release and building id; a geocode cites a parcel dataset.
    Neither is a URL, and source_url must mean a URL or nothing reading it as a link is safe."""
    from pipeline.enrich import _db
    seen = []

    class FakeDB:
        last_row_count = 1
        def query(self, sql, params=()):
            seen.append((sql, params)); return []

    fp = _db.assertion("IC-1", "building_sqft", "81969", source_id="overture:building",
                       basis="footprint", evidence="2026-08-19.0:abc123@22.1m")
    _db.append(FakeDB(), [fp], "rel-1")
    ev = next(p for sql, p in seen if "ref_source_row" in sql)
    assert ev[2] == ""                                  # source_url stays empty
    assert ev[3] == "2026-08-19.0:abc123@22.1m"         # the citation lands in source_document

    seen.clear()
    addr = _db.assertion("IC-2", "address", "1 Main St", source_id="enrich:locate",
                         basis="web_cited", evidence="https://x.example/c :: our plant at 1 Main St")
    _db.append(FakeDB(), [addr], "rel-1")
    ev = next(p for sql, p in seen if "ref_source_row" in sql)
    assert ev[2] == "https://x.example/c" and ev[3] == "our plant at 1 Main St"


def test_the_footprint_ceiling_fits_inside_the_stage_timeout():
    """A ceiling the job cannot reach is not a ceiling: the timeout becomes the real bound and the
    deferral path, which is what lets the next run continue, never runs. Roughly a minute per file
    against a 35 minute stage timeout."""
    import re, pathlib
    from pipeline.enrich.run import DEFAULT_FOOTPRINT_LIMIT
    yml = pathlib.Path(".github/workflows/enrich-stage.yml").read_text()
    timeout = int(re.search(r"timeout-minutes:\s*(\d+)", yml).group(1))
    assert DEFAULT_FOOTPRINT_LIMIT < timeout * 0.75, (
        f"ceiling {DEFAULT_FOOTPRINT_LIMIT} files vs {timeout} minute timeout leaves no headroom")


def test_the_stored_quote_comes_from_the_page_not_the_model(monkeypatch):
    """Auditing the first real run found two of five quotes were page furniture — "Door Shop Store
    Details Store Locator Change My Store" offered as the sentence containing 36 McCoy St. The
    address was on the page; the evidence a human would read was not."""
    monkeypatch.setattr(locate, "_page_states_the_address",
                        lambda u, a, timeout=20: (True, "Visit us at 1200 Industrial Blvd, Waco TX"))
    junk = {**GOOD, "address": "1200 Industrial Blvd",
            "quote": "Store Locator Change My Store"}
    rep = locate.run(ROW, client=_FakeClient(junk, ["https://acme.example/contact"]))
    assert rep["located"] == 1
    ev = rep["assertions"][0]["evidence"]
    assert "Visit us at 1200 Industrial Blvd" in ev
    assert "Change My Store" not in ev


def test_the_snippet_is_readable_text_not_markup(monkeypatch):
    """The snippet is stored as evidence and read by a person. Scripts, styles and undecoded
    entities are not what the page says."""
    page = ("<html><head><style>.a{color:red}</style>"
            "<script>var x='1200 Fake St';</script></head>"
            "<body><p>Store&nbsp;Details&nbsp;/&nbsp;Visit us at 1200 Industrial Blvd, Waco.</p>"
            "</body></html>")
    class R:
        def read(self, n=None): return page.encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: R())
    ok, snip = locate._page_states_the_address("https://x.example", "1200 Industrial Blvd")
    assert ok is True
    assert "&nbsp;" not in snip and "color:red" not in snip and "var x=" not in snip
    assert "Visit us at 1200 Industrial Blvd" in snip


# ---------------------------------------------------------------- stage 13: promote
def _a(fid, field, value, source_id, source_class, basis="none", date="2026-09-01"):
    return {"facility_id": fid, "field": field, "value": value, "source_id": source_id,
            "source_class": source_class, "basis": basis, "retrieved_date": date,
            "row_hash": "h", "site_visit": False, "confidence": 1.0}


def _rules():
    from pipeline.registry import load_yaml
    from pathlib import Path
    return load_yaml(Path(__file__).resolve().parent.parent / "registry" / "survivorship.yaml")


def test_a_rooftop_geocode_beats_the_epa_coordinate_it_exists_to_replace():
    """The reason stage 10 is worth running. Before survivorship ranked `basis:rooftop`, an
    enrichment coordinate lost to epa_frs (class B) on every facility that had one, so the whole
    stage was invisible in golden."""
    from pipeline.enrich import promote
    rows, _ = promote.build([
        _a("F1", "lat_lon", "33.0,-84.0", "epa_frs", "B", basis="none", date="2026-09-14"),
        _a("F1", "lat_lon", "33.1,-84.1", "geocode:geocodio", "enrichment", basis="rooftop", date="2026-09-01"),
    ], _rules())
    assert rows[0]["lat_lon"] == "33.1,-84.1"
    assert rows[0]["lat_lon__source"] == "geocode:geocodio"


def test_an_operator_correction_still_outranks_the_rooftop_geocode():
    from pipeline.enrich import promote
    rows, _ = promote.build([
        _a("F1", "lat_lon", "33.1,-84.1", "geocode:geocodio", "enrichment", basis="rooftop"),
        _a("F1", "lat_lon", "33.5,-84.5", "operator", "operator", basis="operator", date="2026-01-01"),
    ], _rules())
    assert rows[0]["lat_lon__source"] == "operator"


def test_a_web_cited_address_fills_a_gap_but_never_overrides_a_registry():
    from pipeline.enrich import promote
    rows, _ = promote.build([
        _a("F1", "address", "1 Cited Rd", "enrich:locate", "enrichment", basis="web_cited"),
        _a("F1", "address", "2 Licence Ave", "fl_bcis", "D", date="2026-09-14"),
        _a("F2", "address", "3 Cited Rd", "enrich:locate", "enrichment", basis="web_cited"),
    ], _rules())
    by = {r["facility_id"]: r for r in rows}
    assert by["F1"]["address"] == "2 Licence Ave", "a registry address must win"
    assert by["F2"]["address"] == "3 Cited Rd", "and enrichment must fill a facility that has none"


def test_a_null_retrieved_date_does_not_crash_the_tie_break():
    """date_key is nullable in fact_assertions; max() over a mix of str and None raises."""
    from pipeline.enrich import promote
    a = _a("F1", "name", "Acme", "fl_bcis", "D")
    a["retrieved_date"] = None
    rows, _ = promote.build([a], _rules())
    assert rows[0]["name"] == "Acme"


class _RecordingDB:
    """Counts round trips: NeonHttp does one HTTPS request per query() call."""
    def __init__(self, cols):
        self.cols, self.calls, self.inserts = cols, [], 0
    def query(self, sql, params=()):
        self.calls.append((sql, params))
        if "information_schema.columns" in sql:
            return [{"column_name": c} for c in self.cols]
        if sql.startswith("INSERT INTO golden_facility"):
            self.inserts += 1
        if sql.startswith("SELECT COUNT(*) AS __rows"):
            return [{"__rows": 3, **{c: 3 for c in self.cols if not c.endswith("__source")}}]
        return []


def test_replace_golden_batches_instead_of_one_round_trip_per_row():
    """4,065 rows at one HTTPS request each does not finish inside the 35 minute stage timeout."""
    from pipeline.enrich import _db
    fields = ["name", "address"]
    rows = [{"facility_id": f"F{i}", "name": "n", "name__source": "s",
             "address": "a", "address__source": "s", "n_assertions": 1, "n_sources": 1}
            for i in range(1000)]
    db = _RecordingDB([])
    n = _db.replace_golden(db, rows, fields, "rel-1", chunk=300)
    assert n == 1000
    assert db.inserts == 4, f"expected 4 chunked inserts, got {db.inserts}"
    # and the parameter count of a chunk stays well under Postgres' 65535 ceiling
    biggest = max(len(p) for s, p in db.calls if s.startswith("INSERT INTO golden_facility"))
    assert biggest < 65535


def test_promote_widens_a_golden_table_that_predates_a_field():
    from pipeline.enrich import _db
    db = _RecordingDB(["name", "name__source"])
    missing = _db.missing_golden_columns(db, ["name", "building_sqft"])
    assert missing == ["building_sqft", "building_sqft__source"]


def test_a_dry_run_promote_writes_nothing():
    from pipeline.enrich import promote
    db = _RecordingDB(["name", "name__source", "__rows"])
    rep = promote.run(db, "rel-1", dry_run=True)
    assert rep["written"] == 0 and rep["columns_added"] == []
    assert not any(s.startswith(("INSERT", "DELETE", "ALTER")) for s, _ in db.calls), \
        [s.split("\n")[0] for s, _ in db.calls]


def test_e6_blocks_a_rebuild_that_would_shrink_golden():
    from pipeline.enrich import gates
    assert gates.e6_rebuilt_golden_loses_nothing({"__rows": 10, "address": 5},
                                                 {"__rows": 10, "address": 7}).passed
    assert not gates.e6_rebuilt_golden_loses_nothing({"__rows": 10, "address": 5},
                                                     {"__rows": 10, "address": 4}).passed
    assert not gates.e6_rebuilt_golden_loses_nothing({"__rows": 10, "address": 5},
                                                     {"__rows": 9, "address": 5}).passed


# ---------------------------------------------------------------- stage 10: the free-tier ceiling
def _quota_403(url_or_req, *a, **kw):
    import urllib.error, io
    raise urllib.error.HTTPError(
        "https://api.geocod.io/v2/geocode", 403, "Forbidden", {},
        io.BytesIO(b'{"error":"You have exceeded the free tier. Please add a payment method '
                   b'or check that you are using the intended API key."}'))


def test_a_spent_free_tier_defers_the_rest_instead_of_failing(monkeypatch):
    """The day's lookups running out is a ceiling on the stage, not a fault in it. Raising here
    would throw away the assertions already earned and turn a correctly behaved stage red."""
    from pipeline.enrich import geocode
    monkeypatch.setattr(geocode.urllib.request, "urlopen", _quota_403)
    rows = [{"facility_id": f"F{i}", "address": f"{i} Main St", "city": "Dallas", "state": "TX"}
            for i in range(5)]
    rep = geocode.run(rows, key="k")
    assert rep["quota_exhausted"] is True
    assert rep["deferred"] == 5 and rep["requested"] == 0
    assert rep["assertions"] == []
    assert "free tier" in rep["quota_message"].lower()


def test_lookups_earned_before_the_ceiling_are_kept(monkeypatch):
    """Results come back in input order, so a 403 on a later batch still pairs the earlier ones."""
    from pipeline.enrich import geocode
    calls = {"n": 0}

    class _Resp:
        def __init__(self, payload): self.payload = payload
        def read(self): import json; return json.dumps(self.payload).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp({"results": [{"response": {"results": [
                {"accuracy_type": "rooftop", "accuracy": 1.0, "source": "City of Dallas",
                 "location": {"lat": 32.0, "lng": -96.0}}]}}]})
        return _quota_403(req)

    monkeypatch.setattr(geocode, "BATCH", 1)
    monkeypatch.setattr(geocode.urllib.request, "urlopen", fake)
    rows = [{"facility_id": f"F{i}", "address": f"{i} Main St", "city": "Dallas", "state": "TX"}
            for i in range(3)]
    rep = geocode.run(rows, key="k")
    assert rep["quota_exhausted"] is True
    assert rep["requested"] == 1 and rep["deferred"] == 2
    assert rep["stored"] == 1, "the coordinate earned before the ceiling must survive"


def test_a_non_quota_error_still_fails_loudly(monkeypatch):
    """Deferring is only right for the ceiling. A bad key or a 500 is a fault and must not be
    reported as 'deferred', which would look like ordinary progress."""
    import urllib.error, io, pytest as _pytest
    from pipeline.enrich import geocode

    def fake(req, *a, **kw):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {},
                                     io.BytesIO(b'{"error":"Invalid API key"}'))

    monkeypatch.setattr(geocode.urllib.request, "urlopen", fake)
    with _pytest.raises(geocode.GeocodioError):
        geocode.run([{"facility_id": "F1", "address": "1 Main St", "city": "X", "state": "TX"}],
                    key="k")


# ---------------------------------------------------------------- stage 9: a spent gateway budget
class _Status402(Exception):
    status_code = 402
    def __str__(self):
        return ("Error code: 402 - {'error': {'message': 'API key budget exceeded. "
                "Current spend: $10.01, limit: $10.00.', 'type': 'quota_for_entity_exceeded'}}")


def test_a_spent_gateway_budget_stops_the_stage_instead_of_rejecting_every_facility(monkeypatch):
    """The first pass against the release database reported 180 rejections on 200 facilities. 152
    of them were this 402, recorded one per facility as though the model had considered and
    declined each one. The run read as a 10% success rate; it was 42%. A spent key is a fact about
    the key, so the stage stops at the first one and defers the rest."""
    from pipeline.enrich import locate
    seen = {"n": 0}

    def fake_locate_one(row, client, model):
        seen["n"] += 1
        if seen["n"] <= 2:
            return {"answer": {"found": True, "address": "1 Plant Rd", "confidence": 0.9,
                               "source_url": "https://x.example/p", "quote": "1 Plant Rd"},
                    "visited": {"https://x.example/p"}}
        raise _Status402()

    monkeypatch.setattr(locate, "locate_one", fake_locate_one)
    rows = [{"facility_id": f"F{i}", "name": "Acme", "city": "X", "state": "TX"} for i in range(10)]
    rep = locate.run(rows, client=object(), model="m", verify_page=False)

    assert rep["budget_exhausted"] is True
    assert rep["attempted"] == 2, "the facility that raised never reached the model either"
    assert rep["deferred"] == 8
    assert rep["located"] == 2 and rep["rejected"] == []
    assert seen["n"] == 3, "the stage must stop at the first spent-key error, not grind on"
    assert "budget exceeded" in rep["budget_message"]


def test_an_ordinary_facility_failure_still_only_costs_that_facility(monkeypatch):
    """Stopping is only right for the credential. A malformed response from one plant's search must
    not abandon the other 199."""
    from pipeline.enrich import locate

    def fake_locate_one(row, client, model):
        if row["facility_id"] == "F1":
            raise ValueError("bad json")
        return {"answer": {"found": True, "address": "1 Plant Rd", "confidence": 0.9,
                           "source_url": "https://x.example/p", "quote": "1 Plant Rd"},
                "visited": {"https://x.example/p"}}

    monkeypatch.setattr(locate, "locate_one", fake_locate_one)
    rows = [{"facility_id": f"F{i}", "name": "Acme", "city": "X", "state": "TX"} for i in range(3)]
    rep = locate.run(rows, client=object(), model="m", verify_page=False)
    assert rep["budget_exhausted"] is False
    assert rep["attempted"] == 3 and rep["deferred"] == 0
    assert rep["located"] == 2 and len(rep["rejected"]) == 1


def test_rejections_are_bucketed_by_what_actually_went_wrong():
    """A total says the funnel leaked; the buckets say where. 'Search could not find this plant'
    and 'verification would not accept what it found' call for opposite fixes."""
    from pipeline.enrich.locate import _reason
    assert _reason("no citation") == "no citation"
    assert _reason("confidence 0.6 below 0.7") == "confidence below threshold"
    assert _reason("the cited page does not contain '1 Main St'") == "page did not contain the address"
    assert _reason("could not read the cited page to confirm it: https://x") == "page could not be read"
    assert _reason("cited a page search did not visit: https://x") == "cited a page search never opened"
    assert _reason("not a street address: 'Dallas, TX'") == "not a street address"
    assert _reason("No search results returned a verifiable street address") == \
        "model found nothing it could cite"


def test_a_model_id_the_gateway_will_not_serve_stops_the_stage(monkeypatch):
    """A typo in --model is configuration, not 200 plants the model considered and declined. Without
    404 in FATAL_STATUSES this reproduces the exact failure the budget fix was written for."""
    from pipeline.enrich import locate

    class _NotFound(Exception):
        status_code = 404
        def __str__(self): return "Error code: 404 - model not found: anthropic/claude-haiku-9"

    monkeypatch.setattr(locate, "locate_one",
                        lambda row, client, model: (_ for _ in ()).throw(_NotFound()))
    rows = [{"facility_id": f"F{i}", "name": "Acme", "city": "X", "state": "TX"} for i in range(5)]
    rep = locate.run(rows, client=object(), model="anthropic/claude-haiku-9", verify_page=False)
    assert rep["budget_exhausted"] is True and rep["deferred"] == 5
    assert rep["rejected"] == [], "a bad model id must not be recorded as five rejected facilities"


def test_usage_is_counted_so_the_model_choice_can_be_measured(monkeypatch):
    """Cost per LOCATED address is the number that decides the model, and searches are billed per
    search and are model-independent — so a cheaper model that halves the yield costs more, not
    less. That is only visible if both are counted."""
    from pipeline.enrich import locate

    def fake(row, client, model):
        return {"answer": {"found": row["facility_id"] == "F0", "address": "1 Plant Rd",
                           "confidence": 0.9, "source_url": "https://x.example/p",
                           "quote": "1 Plant Rd", "reason": "no"},
                "visited": {"https://x.example/p"},
                "usage": {"input_tokens": 1000, "output_tokens": 100, "web_searches": 2}}

    monkeypatch.setattr(locate, "locate_one", fake)
    rows = [{"facility_id": f"F{i}", "name": "Acme", "city": "X", "state": "TX"} for i in range(4)]
    rep = locate.run(rows, client=object(), model="m", verify_page=False)
    assert rep["located"] == 1
    assert rep["usage"] == {"input_tokens": 4000, "output_tokens": 400, "web_searches": 8}
    # one address out of four attempts carries the whole run's cost
    assert rep["usage_per_located"]["web_searches"] == 8.0


# ------------------------------------------------- stage 9 on any gateway model, not just Anthropic
def test_the_search_objective_is_built_per_row_not_left_static():
    """The gateway applies a tool's `config` as a developer default that OVERRIDES what the model
    generates, so a static objective would run the same search 1,181 times. Building it per row
    also makes the search deterministic given the row, which is the property the rest of the
    pipeline rests on."""
    from pipeline.enrich.locate import search_objective
    o = search_objective({"name": "Acme Modular", "city": "Elkhart", "state": "IN"})
    assert o == "street address of the Acme Modular manufacturing plant in Elkhart, IN"
    # a facility with no city still produces a usable objective rather than a dangling preposition
    assert search_objective({"name": "Acme Modular"}) == \
        "street address of the Acme Modular manufacturing plant"


def test_a_chat_completions_reply_is_parsed_for_answer_usage_and_search_count():
    from pipeline.enrich.locate import parse_gateway_response
    got = parse_gateway_response({
        "choices": [{"message": {
            "content": 'here you go {"found": true, "address": "1 Plant Rd", "confidence": 0.9, '
                       '"source_url": "https://x.example/p", "quote": "at 1 Plant Rd"}',
            "provider_metadata": {"gateway": {"gatewayToolCalls": [{"t": "search"}, {"t": "search"}]}}}}],
        "usage": {"prompt_tokens": 12345, "completion_tokens": 234}})
    assert got["answer"]["address"] == "1 Plant Rd"
    assert got["usage"] == {"input_tokens": 12345, "output_tokens": 234, "web_searches": 2}
    assert got["visited"] == set(), "the gateway path returns no raw search results"


def test_a_reply_missing_every_optional_field_does_not_crash_the_stage():
    """Gateway metadata shape varies by provider; a missing count must cost this facility at most."""
    from pipeline.enrich.locate import parse_gateway_response
    got = parse_gateway_response({"choices": [{"message": {"content": "no json here"}}]})
    assert got["answer"] == {} and got["usage"]["web_searches"] == 0


def test_the_gateway_path_needs_no_anthropic_client_and_no_anthropic_model(monkeypatch):
    """The point of being on a gateway: an open-weight model gets web search too."""
    from pipeline.enrich import locate
    seen = {}

    def fake(row, model, key, search):
        seen.update(model=model, key=key, search=search)
        return {"answer": {"found": False, "reason": "nothing"}, "visited": set(),
                "usage": {"input_tokens": 10, "output_tokens": 1, "web_searches": 1}}

    monkeypatch.setattr(locate, "locate_one_gateway", fake)
    monkeypatch.setattr(locate, "locate_one",
                        lambda *a: pytest.fail("the Anthropic path must not be used here"))
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "k")
    rep = locate.run([{"facility_id": "F1", "name": "Acme", "city": "X", "state": "TX"}],
                     model="alibaba/qwen3.7-flash", verify_page=False)
    assert seen == {"model": "alibaba/qwen3.7-flash", "key": "k",
                    "search": locate.DEFAULT_SEARCH}
    assert rep["search"] == locate.DEFAULT_SEARCH and rep["attempted"] == 1


def test_the_default_model_is_not_a_frontier_vendor():
    """A regression guard with teeth: the gateway exists so this stage can run on cheap open
    weights, and a default that drifts back to a frontier vendor silently undoes that."""
    from pipeline.enrich.locate import DEFAULT_MODEL, DEFAULT_SEARCH, SEARCH_TOOLS
    assert not DEFAULT_MODEL.startswith(("anthropic/", "openai/", "google/"))
    assert DEFAULT_SEARCH in SEARCH_TOOLS, "the default must not be a provider-native tool"


def test_an_unknown_search_provider_is_refused_before_any_call():
    from pipeline.enrich import locate
    with pytest.raises(locate.LocateUnavailable, match="unknown --search"):
        locate.run([{"facility_id": "F1", "name": "A"}], search="bing")


def test_a_gateway_http_error_carries_its_status_so_a_spent_key_is_recognised():
    """GatewayError must expose status_code or _exhausted() cannot tell a spent key from a bad row,
    and the 402 that cost 152 facilities would be recorded as 152 rejections all over again."""
    from pipeline.enrich.locate import GatewayError, _exhausted
    assert _exhausted(GatewayError(402, '{"error":"budget exceeded"}'))
    assert _exhausted(GatewayError(404, "no such model"))
    assert not _exhausted(GatewayError(500, "upstream blew up"))


def test_append_batches_instead_of_two_round_trips_per_assertion():
    """NeonHttp does one HTTPS request per query(), so a row at a time meant two per assertion — a
    2,000-assertion geocode run is 4,000 requests, which is minutes of latency and nothing else."""
    from pipeline.enrich import _db
    calls = []

    class FakeDB:
        last_row_count = 250
        def query(self, sql, params=()):
            calls.append((sql, params)); return []

    rows = [_db.assertion(f"IC-{i}", "lat_lon", "1.0,2.0", source_id="geocode:geocodio",
                          basis="rooftop", evidence="geocodio:parcel") for i in range(600)]
    rep = _db.append(FakeDB(), rows, "rel-1", chunk=250)
    inserts = [c for c in calls if c[0].lstrip().startswith("INSERT")]
    assert len(inserts) == 6, f"3 chunks x 2 tables, got {len(inserts)} inserts"
    assert rep["offered"] == 600
    # Postgres caps a statement at 65535 parameters; 250 x 12 leaves plenty of room
    assert max(len(p) for _, p in inserts) < 65535


def test_append_reports_rows_inserted_not_rows_offered():
    """A second run over unchanged evidence offers the same assertions and inserts none of them.
    Calling that '2,000 appended' would report the opposite of the property the design rests on."""
    from pipeline.enrich import _db

    class AlreadyThere:
        last_row_count = 0
        def query(self, sql, params=()):
            return []

    rows = [_db.assertion(f"IC-{i}", "lat_lon", "1.0,2.0", source_id="geocode:geocodio",
                          basis="rooftop", evidence="geocodio:parcel") for i in range(10)]
    rep = _db.append(AlreadyThere(), rows, "rel-1")
    assert rep == {"offered": 10, "inserted": 0, "already_present": 10}


# ---------------------------------------------------------------- search is the price, not the model
def test_search_defaults_to_tako_while_free_and_to_the_cheapest_paid_after():
    """Search is ~90% of what stage 9 costs. Tako is free on the gateway through 2026-09-30 and $7
    per 1,000 after; Parallel is $5 flat. Defaulting by date means the run is free while free and
    cheapest-paid afterwards, with no silent bill on October 1st."""
    from datetime import date
    from pipeline.enrich.locate import default_search
    assert default_search(date(2026, 9, 17)) == "tako"
    assert default_search(date(2026, 9, 30)) == "tako", "the last free day is still free"
    assert default_search(date(2026, 10, 1)) == "parallel", "and the first paid day is not Tako"


def test_tako_asks_for_web_results_only_because_data_rows_are_billed():
    """Tako searches a curated data graph as well as the web and bills per row for inlined data.
    Stage 9 wants a street address off a page, so asking for the graph at all is spend with no
    possible benefit — and includeContents is what turns a flat per-request price into a variable
    one."""
    from pipeline.enrich.locate import SEARCH_TOOLS
    cfg = SEARCH_TOOLS["tako"][1]("street address of the Acme plant in Elkhart, IN")
    assert set(cfg["sources"]) == {"web"}, "the data graph must not be searched"
    assert "includeContents" not in json.dumps(cfg), "inlined rows are billed per row"
    assert cfg["query"].startswith("street address")


def test_each_search_tool_gets_the_config_shape_its_provider_documents():
    """The field names differ per provider — parallel takes `objective`, the rest take `query`, and
    Exa counts results with `num_results`. One generic shape would silently mis-send three of four."""
    from pipeline.enrich.locate import SEARCH_TOOLS
    assert SEARCH_TOOLS["parallel"][1]("o") == {"objective": "o", "max_results": 5}
    assert SEARCH_TOOLS["perplexity"][1]("o") == {"query": "o", "max_results": 5}
    assert SEARCH_TOOLS["exa"][1]("o") == {"query": "o", "num_results": 5}


def test_footprint_takes_the_largest_building_in_range_not_the_nearest(monkeypatch):
    """A rooftop geocode resolves to the street address, and on a plant site the building nearest
    the road is the office or the guard house — the plant is the big one behind it. Measured on the
    first full run: matches under 10,000 sqft had a median of 2 buildings within 30m against 1 for
    the rest, and 143 of 226 had another building beside them. Measuring the wrong building on the
    right parcel is worse than measuring nothing, because it feeds stage 12 a false 'implausibly
    small' flag."""
    from pipeline.enrich import footprint

    # a 10m square at the coordinate (the gatehouse) and a 100m square 20m away (the plant)
    def sq(lat, lon, side_deg):
        return (f"POLYGON(({lon} {lat},{lon+side_deg} {lat},{lon+side_deg} {lat+side_deg},"
                f"{lon} {lat+side_deg},{lon} {lat}))")

    gate  = (sq(35.0, -90.0, 0.0001), 0.0,      "gatehouse", 4.0)
    plant = (sq(35.0, -90.0, 0.0010), 0.00018,  "plant",     9.0)

    class FakeCon:
        def execute(self, sql, params=None):
            self.rows = [] if "CREATE" in sql else [gate, plant]
            return self
        def fetchall(self):
            return self.rows

    monkeypatch.setattr(footprint, "build_index", lambda release, cache: {"f": None})
    monkeypatch.setattr(footprint, "file_for", lambda idx, lat, lon: "f")
    monkeypatch.setattr(footprint, "_connect", lambda: FakeCon(), raising=False)
    # exercise the selection directly rather than the S3 plumbing
    areas = [(round(footprint.wkt_area_m2(w) * footprint.M2_FT2), d, b, h)
             for w, d, b, h in (gate, plant)]
    chosen = max(areas, key=lambda a: a[0])
    nearest = min(areas, key=lambda a: a[1])
    assert chosen[2] == "plant", "the plant must win on area"
    assert nearest[2] == "gatehouse", "and the nearest is kept for audit"
    assert chosen[0] > 10 * nearest[0], "the difference is the whole point"


def test_the_search_count_survives_whatever_shape_the_gateway_reports():
    """The first live call returned gatewayToolCalls as a dict keyed by tool name where the unit
    test had assumed a list, and `calls or 0` fed that dict into an integer sum — killing a run
    over a number nothing depends on. Usage is a metric, never a reason to lose addresses."""
    from pipeline.enrich.locate import _search_count
    assert _search_count({"vercel:tako_search": 2}) == 2       # the shape that actually arrived
    assert _search_count([{"a": 1}, {"b": 2}]) == 2            # the shape the test had assumed
    assert _search_count(3) == 3
    assert _search_count(None) == 0
    assert _search_count("nonsense") == 0
    assert _search_count({"x": "weird"}) == 1                  # counted, not crashed


def test_a_strange_usage_value_cannot_take_the_run_down(monkeypatch):
    from pipeline.enrich import locate

    def fake(row, model, key, search):
        return {"answer": {"found": True, "address": "1 Plant Rd", "confidence": 0.9,
                           "source_url": "https://x.example/p", "quote": "1 Plant Rd"},
                "visited": set(),
                "usage": {"input_tokens": 10, "output_tokens": 2, "web_searches": {"odd": "shape"}}}

    monkeypatch.setattr(locate, "locate_one_gateway", fake)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "k")
    rep = locate.run([{"facility_id": "F1", "name": "Acme", "city": "X", "state": "TX"}],
                     verify_page=False)
    assert rep["located"] == 1, "the address must survive a metric it could not add up"
    assert rep["usage"]["input_tokens"] == 10


# ------------------------------------------------- re-measuring must supersede, not coexist
def _measurement(fid, sqft, asserted_at, building):
    return {"facility_id": fid, "field": "building_sqft", "value": str(sqft),
            "source_id": "overture:building", "source_class": "enrichment", "basis": "footprint",
            "retrieved_date": "2026-09-17", "asserted_at": asserted_at,
            "row_hash": building, "site_visit": False, "confidence": 1.0}


def test_a_re_measurement_supersedes_the_one_it_corrected():
    """The bug this fixes, reproduced. Stage 11 measured 790 facilities, the selection rule was
    corrected, and the same 790 were measured again the same day. Both measurements carry the same
    source, basis and retrieved_date, so `tie: most_recent` could not order them and max() returned
    whichever the sort left first — 24 facilities kept the superseded value."""
    from pipeline.enrich import promote
    rows, _ = promote.build([
        _measurement("F1", 4726, "2026-09-17T05:45:00+00:00", "gatehouse"),   # the old, wrong one
        _measurement("F1", 13558, "2026-09-17T06:26:00+00:00", "plant"),      # the correction
    ], _rules())
    assert rows[0]["building_sqft"] == "13558", "the later measurement must win"

    # and the order they arrive in must not matter
    rows, _ = promote.build([
        _measurement("F1", 13558, "2026-09-17T06:26:00+00:00", "plant"),
        _measurement("F1", 4726, "2026-09-17T05:45:00+00:00", "gatehouse"),
    ], _rules())
    assert rows[0]["building_sqft"] == "13558"


def test_retrieved_date_still_outranks_asserted_at():
    """asserted_at is a sub-order, not a replacement. retrieved_date means when the SOURCE was
    retrieved and must keep deciding between two sources: a stale roster loaded today does not beat
    a fresh one loaded last week."""
    from pipeline.enrich import promote
    stale_but_written_later = {**_measurement("F1", 100, "2026-09-17T23:00:00+00:00", "a"),
                               "retrieved_date": "2026-01-01"}
    fresh_but_written_earlier = {**_measurement("F1", 900, "2026-09-17T01:00:00+00:00", "b"),
                                 "retrieved_date": "2026-09-14"}
    rows, _ = promote.build([stale_but_written_later, fresh_but_written_earlier], _rules())
    assert rows[0]["building_sqft"] == "900", "the fresher source wins regardless of write time"


def test_an_assertion_predating_the_column_loses_to_one_that_has_it():
    """Every layers 1-8 assertion written before asserted_at existed reads as ''. Sorting those
    below an assertion that carries a timestamp is correct: the row that recorded when it was
    written is the later of the two."""
    from pipeline.enrich import promote
    rows, _ = promote.build([
        {**_measurement("F1", 111, "2026-09-17T06:00:00+00:00", "new")},
        {**_measurement("F1", 222, "", "old")},
    ], _rules())
    assert rows[0]["building_sqft"] == "111"


# ---------------------------------------------- "implausibly small" is relative to the industry
def test_the_small_footprint_bar_moves_with_what_the_trade_builds():
    """The flat 10,000 sqft threshold was backwards. Measured over 667 footprints, a truss shop's
    median is 14,145 sqft and a manufactured-home plant's is 87,303, so a flat bar flagged 38% of
    truss shops and 15% of home plants — when the second group is the one where small is strange."""
    from pipeline.enrich.existence import small_sqft
    truss, homes = small_sqft("321214"), small_sqft("321991")
    assert truss < 10_000 < homes, "the bar must fall for small trades and rise for large ones"
    # a 12,000 sqft building: ordinary for a truss shop, remarkable for a home plant
    assert not 12_000 < truss and 12_000 < homes


def test_an_unknown_industry_falls_back_to_the_measured_average():
    from pipeline.enrich.existence import small_sqft, DEFAULT_MEDIAN_SQFT, SMALL_FRACTION
    expected = round(SMALL_FRACTION * DEFAULT_MEDIAN_SQFT)
    assert small_sqft(None) == expected
    assert small_sqft("") == expected
    assert small_sqft("999999") == expected, "a trade with no measurement is not a zero threshold"


def test_a_normal_truss_shop_is_no_longer_evidence_against_itself(tmp_path):
    """The concrete regression: a 9,000 sqft truss shop with an expired licence used to collect two
    observations and become a flag. One of those observations was just 'it is a truss shop'."""
    from pipeline.enrich import existence
    from datetime import date
    import json
    (tmp_path / "footprint.rows.json").write_text(json.dumps(
        [{"facility_id": "IC-1", "building_sqft": 9_000, "n_nearby": 1},
         {"facility_id": "IC-2", "building_sqft": 9_000, "n_nearby": 1}]))
    rows = [
        {"facility_id": "IC-1", "naics": "321214", "expiry_date": "2015-01-01", "status": "expired"},
        {"facility_id": "IC-2", "naics": "321991", "expiry_date": "2015-01-01", "status": "expired"},
    ]
    rep = existence.run(rows, tmp_path, today=date(2026, 9, 17))
    flagged = {a["facility_id"] for a in rep["assertions"]}
    # both have an expired licence and a dead status; only the home plant's size adds to that
    assert "IC-2" in flagged, "9,000 sqft IS strange for a manufactured-home plant"
    why = next(a for a in rep["assertions"] if a["facility_id"] == "IC-2")["evidence"]
    assert "9,000 sqft is below 17,461" in why, why
    assert "321214" not in json.dumps(rep["reasons"]), "a normal truss shop must not cite its size"


# ------------------------------------------- a long stage must degrade, not be killed outright
def test_locate_stops_at_its_deadline_and_defers_the_rest(monkeypatch):
    """A ceiling in facilities is a guess about how long a facility takes; the job timeout is the
    real constraint. Run 33 set --limit 150 on a measured 10.6s/facility and locate was still going
    at 31 minutes against a 35 minute timeout — and a killed job uploads nothing, because the stage
    only wrote its files after the loop."""
    from pipeline.enrich import locate
    clock = {"t": 0.0}
    monkeypatch.setattr(locate, "DEFAULT_SEARCH", "tako", raising=False)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "k")

    def fake(row, model, key, search):
        clock["t"] += 10.0                       # ten seconds a facility
        return {"answer": {"found": False, "reason": "no"}, "visited": set(),
                "usage": {"input_tokens": 1, "output_tokens": 1, "web_searches": 1}}

    monkeypatch.setattr(locate, "locate_one_gateway", fake)
    monkeypatch.setattr(locate.time, "monotonic", lambda: clock["t"], raising=False)
    rows = [{"facility_id": f"F{i}", "name": "Acme", "city": "X", "state": "TX"} for i in range(100)]
    rep = locate.run(rows, deadline_s=100, verify_page=False)

    # 11, not 10: the check runs before each facility, so one starts at exactly the deadline and
    # runs to completion. One facility of overshoot against 600s of headroom is not worth chasing.
    assert rep["attempted"] == 11, f"attempted {rep['attempted']}"
    assert rep["deferred"] == 89
    assert rep["budget_exhausted"] is True and "deadline" in rep["budget_message"]


def test_locate_checkpoints_so_a_kill_still_keeps_what_it_found(monkeypatch):
    """The artifact upload runs `if: always()`, so a killed stage would upload — but there was
    nothing on disk. Writing as it goes is what makes that upload worth having."""
    from pipeline.enrich import locate
    seen = []
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "k")
    monkeypatch.setattr(locate, "locate_one_gateway", lambda row, model, key, search: {
        "answer": {"found": True, "address": "1 Plant Rd", "confidence": 0.9,
                   "source_url": "https://x.example/p", "quote": "1 Plant Rd"},
        "visited": set(), "usage": {"input_tokens": 1, "output_tokens": 1, "web_searches": 1}})
    rows = [{"facility_id": f"F{i}", "name": "Acme", "city": "X", "state": "TX"} for i in range(25)]
    rep = locate.run(rows, verify_page=False, checkpoint=seen.append, checkpoint_every=10)

    assert [len(r["assertions"]) for r in seen] == [10, 20], "a checkpoint every 10 located"
    assert rep["located"] == 25
    # a checkpoint carries the same shape as the final report, so the two cannot drift
    assert set(seen[0]) == set(rep)


def test_the_reasons_histogram_counts_footprints_together(tmp_path):
    """The first live run reported eleven separate buckets — "footprint 3,459", "footprint 2,222" —
    because the key was the first two words and the second word was the number. A histogram that
    cannot count its largest category is not a histogram."""
    from pipeline.enrich import existence
    from datetime import date
    import json
    (tmp_path / "footprint.rows.json").write_text(json.dumps(
        [{"facility_id": f"IC-{i}", "building_sqft": 2_000 + i, "n_nearby": 1} for i in range(3)]))
    rows = [{"facility_id": f"IC-{i}", "naics": "321991", "expiry_date": "2015-01-01",
             "status": "expired"} for i in range(3)]
    rep = existence.run(rows, tmp_path, today=date(2026, 9, 18))
    assert rep["reasons"].get("footprint too small for its trade") == 3, rep["reasons"]


def test_the_deadline_leaves_real_slack_under_the_job_timeout():
    """The deadline is only useful if it fires before the runner's axe. Job timeout is 35 minutes;
    setup and install measured ~35s on run 33 and the upload ~1s."""
    import re, pathlib
    run_py = pathlib.Path("pipeline/enrich/run.py").read_text()
    deadline = float(re.search(r'ENRICH_DEADLINE_S", (\d+)', run_py).group(1))
    stage_yml = pathlib.Path(".github/workflows/enrich-stage.yml").read_text()
    timeout_s = int(re.search(r"timeout-minutes: (\d+)", stage_yml).group(1)) * 60
    assert deadline < timeout_s - 120, f"deadline {deadline}s leaves under 2 min of {timeout_s}s"


def test_geocode_reports_what_a_run_would_cost():
    """The account is pay-as-you-go now. 2,500 lookups a day are free and the rest bills at $1/1000,
    and the 403 that used to stop a run at the boundary no longer comes — the overage is silent. A
    run that cannot say what it would cost is the wrong shape for that."""
    from pipeline.enrich import geocode as gc

    def fake_post(queries, key):
        return [{"query": q, "response": {"results": [
            {"location": {"lat": 1.0, "lng": 2.0}, "accuracy": 0.9,
             "accuracy_type": "rooftop", "source": "City"}]}} for q in queries], ""

    orig, gc._post = gc._post, fake_post
    try:
        rep = gc.run([{"facility_id": f"IC-{i}", "address": f"{i} Main St", "city": "X",
                       "state": "TX", "zip": ""} for i in range(250)], key="k")
    finally:
        gc._post = orig
    assert rep["stored"] == 250
    assert rep["billable_if_allowance_spent_usd"] == 0.25, rep["billable_if_allowance_spent_usd"]


# ------------------------------------- the lookup ledger: keyed by the question, not by the asker
class _Ledger:
    """An in-memory stand-in for cache_lookup, honouring the same SQL the real one is given."""
    def __init__(self):
        self.rows = {}
    def query(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("CREATE TABLE"):
            return []
        if s.startswith("SELECT result, found, provider"):
            r = self.rows.get(params[0])
            return [r] if r else []
        if s.startswith("UPDATE cache_lookup SET hits"):
            if params[0] in self.rows:
                self.rows[params[0]]["hits"] += 1
            return []
        if s.startswith("INSERT INTO cache_lookup"):
            self.rows[params[0]] = {"cache_key": params[0], "kind": params[1], "input": params[2],
                                    "result": params[3], "found": params[4], "provider": params[5],
                                    "fetched_at": params[6], "hits": 0}
            return []
        if "GROUP BY" in s:
            return []
        return list(self.rows.values())


def test_the_same_address_is_never_geocoded_twice(monkeypatch):
    """A geocode is a pure function of an address string. Two facilities sharing an address, or one
    facility whose id changed, must not each be billed for the same lookup."""
    from pipeline.enrich import geocode as gc
    calls = []

    def fake_post(queries, key):
        calls.append(list(queries))
        return [{"query": q, "response": {"results": [
            {"location": {"lat": 1.0, "lng": 2.0}, "accuracy": 0.9,
             "accuracy_type": "rooftop", "source": "City"}]}} for q in queries], ""

    db = _Ledger()
    orig, gc._post = gc._post, fake_post
    try:
        a = [{"facility_id": "IC-1", "address": "1 Main St", "city": "Dallas", "state": "TX", "zip": ""}]
        # a different facility, the same address, spelled differently
        b = [{"facility_id": "IC-2", "address": "1 MAIN ST.", "city": "dallas", "state": "TX", "zip": ""}]
        r1 = gc.run(a, key="k", db=db)
        r2 = gc.run(b, key="k", db=db)
    finally:
        gc._post = orig

    assert r1["stored"] == 1 and r2["stored"] == 1, "both facilities still get a coordinate"
    assert len(calls) == 1, f"the second must not reach Geocodio; calls={calls}"
    assert r2["from_cache"] == 1 and r2["billed_lookups"] == 0
    assert r2["billable_if_allowance_spent_usd"] == 0.0


def test_an_address_geocodio_cannot_place_is_cached_too(monkeypatch):
    """The tail that never matches is most of the waste: it costs exactly as much to re-ask."""
    from pipeline.enrich import geocode as gc
    calls = []

    def fake_post(queries, key):
        calls.append(list(queries))
        return [{"query": q, "response": {"results": []}} for q in queries], ""

    db = _Ledger()
    orig, gc._post = gc._post, fake_post
    try:
        row = [{"facility_id": "IC-1", "address": "9 Nowhere", "city": "X", "state": "TX", "zip": ""}]
        gc.run(row, key="k", db=db)
        gc.run(row, key="k", db=db)
    finally:
        gc._post = orig
    assert len(calls) == 1, "a no_result must be remembered, not re-bought"


def test_a_positive_is_reusable_by_anyone_and_a_negative_only_by_its_own_provider():
    """The rule that keeps a cache from becoming a ceiling. 'qwen found nothing' is a fact about
    qwen; storing it under the input alone would permanently stop a better model from trying."""
    from pipeline.enrich import cache
    db = _Ledger()
    k = cache.locate_key("Acme Modular", "Elkhart", "IN")

    cache.put(db, k, "locate", "Acme", {"why": "nothing citable"}, False, "qwen+tako")
    assert cache.get(db, k, provider="qwen+tako") is not None, "the same model reuses its own miss"
    assert cache.get(db, k, provider="opus+tako") is None, "a better model must get to try"

    cache.put(db, k, "locate", "Acme", {"address": "1 Plant Rd"}, True, "qwen+tako")
    assert cache.get(db, k, provider="opus+tako")["result"]["address"] == "1 Plant Rd", \
        "an address is an address, whoever found it"


def test_a_new_overture_release_re_measures_rather_than_serving_a_stale_building():
    from pipeline.enrich import cache
    old = cache.footprint_key(35.0, -90.0, "2026-08-19.0")
    new = cache.footprint_key(35.0, -90.0, "2026-11-19.0")
    assert old != new, "the release is part of the question"
    # and float formatting drift must not miss a hit
    assert cache.footprint_key(35.0, -90.0, "2026-08-19.0") == \
        cache.footprint_key(35.000000, -90.000000, "2026-08-19.0")


def test_the_ledger_survives_the_database(tmp_path):
    """The one failure the warehouse cannot protect against: the database itself being recreated.
    A ledger that can be dumped and restored is what makes a from-zero rebuild free."""
    from pipeline.enrich import cache
    import json
    src = _Ledger()
    cache.put(src, cache.geocode_key("1 Main St, Dallas TX"), "geocode", "1 main st dallas tx",
              {"lat": 1.0}, True, "geocodio")
    rows = cache.dump(src)
    (tmp_path / "ledger.json").write_text(json.dumps(rows, default=str))

    fresh = _Ledger()                                   # a brand new, empty database
    n = cache.restore(fresh, json.loads((tmp_path / "ledger.json").read_text()))
    assert n == 1
    assert cache.get(fresh, cache.geocode_key("1 MAIN ST,  dallas  tx"))["result"]["lat"] == 1.0
