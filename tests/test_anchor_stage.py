"""Stage 14 replaces Geocodio's label with two measurements the database can make itself.

`accuracy_type` is Geocodio's opinion of its own METHOD. A range_interpolation point for County
Prestress & Precast in Salem IL sits about twenty feet from the plant and was discarded for it,
while stage 11 — which reads the Overture building under a coordinate — was never pointed at it.
"""
import pytest
from pipeline.enrich import anchor, gates


def hit(at="range_interpolation", formatted="749 W Commercial St, Salem, IL 62881", acc=1):
    return {"response": {"results": [{
        "accuracy_type": at, "accuracy": acc, "formatted_address": formatted,
        "source": "TIGER/Line", "location": {"lat": 38.620864, "lng": -88.952418}}]}}


class FakeCache:
    def __init__(self, by_line): self.by_line = by_line
    def ensure(self, db): pass
    def geocode_key(self, line): return line
    def get(self, db, key, provider=None):
        r = self.by_line.get(key)
        return {"result": r} if r else None


def ledger(monkeypatch, by_line):
    import pipeline.enrich.cache as real
    fake = FakeCache(by_line)
    monkeypatch.setattr(real, "geocode_key", fake.geocode_key)
    monkeypatch.setattr(real, "get", fake.get)


FAC = {"facility_id": "IC-1", "name": "County Prestress", "address": "749 W. Commercial Ave.",
       "city": "Salem", "state": "IL", "zip": "62881-2326"}
LINE = lambda f: f["address"] + ", " + f["city"] + " " + f["state"] + " " + f["zip"]


# ---- the postcode guard

def test_the_postcode_is_read_from_the_END_of_the_address():
    """"749 W Commercial St, Salem, IL 62881" starts with a house number. Reading the first five
    digits as a postcode reported an 88% mismatch rate where the truth was 11%."""
    assert anchor.theirs_zip("749 W Commercial St, Salem, IL 62881") == "62881"
    assert anchor.theirs_zip("3101 Garden City Hwy, Midland, TX 79701-1234") == "79701"
    assert anchor.theirs_zip("no postcode here") == ""


def test_a_zip_plus_four_on_our_side_still_agrees():
    assert anchor.zip_agrees(FAC, "749 W Commercial St, Salem, IL 62881")


def test_a_result_that_moved_to_another_town_is_refused(monkeypatch):
    """300 Wright Road, Poca WV came back as 300 Wrights Ln, Buffalo WV with accuracy 0.88.
    Geocodio's own score does not flag it; the postcode does."""
    f = {**FAC, "zip": "25159"}
    ledger(monkeypatch, {LINE(f): hit(formatted="300 Wrights Ln, Buffalo, WV 25033", acc=0.88)})
    cands, why = anchor.from_ledger(None, [f], LINE)
    assert cands == [] and why["the postcode it returned is not ours"] == 1


def test_a_rooftop_answer_is_not_this_stages_business(monkeypatch):
    ledger(monkeypatch, {LINE(FAC): hit(at="rooftop")})
    cands, why = anchor.from_ledger(None, [FAC], LINE)
    assert cands == [] and why["already a rooftop coordinate"] == 1


@pytest.mark.parametrize("at", ["place", "nearest_rooftop_match", "state"])
def test_the_accuracy_types_that_measured_badly_stay_refused(monkeypatch, at):
    """`place` is a town centroid — it would drop a factory pin downtown and look precise.
    `nearest_rooftop_match` verified against imagery 30% of the time and its failures were a
    different parcel entirely (gate E2's own docstring)."""
    ledger(monkeypatch, {LINE(FAC): hit(at=at)})
    cands, _ = anchor.from_ledger(None, [FAC], LINE)
    assert cands == []


def test_a_facility_never_geocoded_is_counted_separately(monkeypatch):
    """"no cached answer" and "answered badly" are different facts about a facility."""
    ledger(monkeypatch, {})
    cands, why = anchor.from_ledger(None, [FAC], LINE)
    assert cands == [] and why["never geocoded"] == 1


def test_it_makes_no_provider_call(monkeypatch):
    """The coordinates were computed and paid for by stage 10. This stage only reads them."""
    import inspect
    src = inspect.getsource(anchor)
    assert "requests" not in src and "urlopen" not in src and "GEOCODIO" not in src


# ---- what it writes

CONFIRMED = {"facility_id": "IC-1", "lat": 38.620864, "lon": -88.952418,
             "accuracy_type": "range_interpolation", "accuracy": 1, "dataset": "TIGER/Line",
             "formatted": "749 W Commercial St, Salem, IL 62881", "building_id": "ov-abc",
             "offset_m": 6.1, "building_sqft": 12000, "overture_release": "2026-08-19.0"}


def test_the_evidence_carries_the_building_that_confirmed_it():
    ev = anchor.assertions_for(CONFIRMED)[0]["evidence"]
    assert "building:ov-abc" in ev and "6.1m" in ev and "confirmed by" in ev
    assert "range_interpolation" in ev          # what Geocodio called it is kept, not hidden


def test_the_coordinate_is_not_called_a_rooftop_fix():
    a = anchor.assertions_for(CONFIRMED)[0]
    assert a["basis"] == "interpolated_on_building" and a["field"] == "lat_lon"


def test_gate_e8_refuses_an_anchored_coordinate_with_no_building():
    bad = [{"field": "lat_lon", "basis": "interpolated_on_building", "evidence": "trust me"}]
    assert not gates.e8_every_anchored_coordinate_names_its_building(bad).passed
    ok = anchor.assertions_for(CONFIRMED)
    assert gates.e8_every_anchored_coordinate_names_its_building(ok).passed


def test_gate_e2_admits_the_new_basis_and_still_refuses_a_bare_interpolation():
    assert gates.e2_no_coordinate_from_a_non_rooftop_geocode(
        [{"field": "lat_lon", "basis": "interpolated_on_building"}]).passed
    assert not gates.e2_no_coordinate_from_a_non_rooftop_geocode(
        [{"field": "lat_lon", "basis": "range_interpolation"}]).passed


def test_survivorship_ranks_it_below_rooftop_and_place_match():
    import yaml
    order = yaml.safe_load(open("registry/survivorship.yaml"))["fields"]["lat_lon"]["order"]
    assert order.index("basis:rooftop") < order.index("basis:place_match") \
        < order.index("basis:interpolated_on_building")


def test_it_does_not_reuse_the_footprint_cache_at_a_different_radius():
    """footprint.measure caches under (lat, lon, release). A building found with a 30m radius must
    not be served to a 50m question, so this stage asks Overture itself."""
    import ast, inspect
    fn = ast.parse(inspect.getsource(anchor.with_buildings)).body[0]
    body = ast.unparse(fn.body[1:])          # skip the docstring, which explains the decision
    assert "footprint.measure" not in body and "lookup_cache" not in body
