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
    rep = locate.run(ROW, client=_FakeClient(GOOD, ["https://acme.example/contact"]))
    assert rep["located"] == 1
    a = rep["assertions"][0]
    assert a["field"] == "address" and a["value"] == "1200 Industrial Blvd"
    assert a["source_id"] == "enrich:locate" and a["basis"] == "web_cited"
    assert "https://acme.example/contact" in a["evidence"] and "Our plant at" in a["evidence"]


def test_the_search_tool_is_actually_offered():
    c = _FakeClient(GOOD, ["https://acme.example/contact"])
    locate.run(ROW, client=c)
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
    rep = locate.run(ROW, client=_FakeClient(reply, visited))
    assert rep["located"] == 0 and rep["assertions"] == []
    assert why in rep["rejected"][0]["why"]


def test_one_failing_facility_does_not_fail_the_stage():
    class Boom(_FakeClient):
        def create(self, **kw): raise RuntimeError("gateway 429")
    rep = locate.run(ROW, client=Boom(GOOD))
    assert rep["located"] == 0 and "gateway 429" in rep["rejected"][0]["why"]
