"""Stage 13 matches on the ADDRESS, and refuses when the address names more than one site.

The numbers quoted here were measured against 241 Oregon and Washington facilities whose
coordinate came from a verified rooftop geocode, so the stage's accuracy is knowable rather than
asserted — see the module docstring of pipeline/enrich/places.py.
"""
import pytest
from pipeline.enrich import places, gates


def place(nm, addr, lat, lon, loc="TROY", reg="TX", **kw):
    return {"id": f"ov-{nm}", "nm": nm, "addr": addr, "loc": loc, "reg": reg,
            "lat": lat, "lon": lon, "website": kw.get("website"), "phone": kw.get("phone")}


def fac(name="Acme Truss", address="2802 142nd Ave E", city="Troy", state="TX", fid="IC-1"):
    return {"facility_id": fid, "name": name, "address": address, "city": city, "state": state}


# ---- the join key

def test_the_same_address_written_two_ways_still_agrees():
    """"2802 142nd Ave. East PO Box 878" and "2802 142nd Ave E" are one building. Suffixes, unit
    markers and punctuation differ between a state roster and a POI listing on every other row."""
    a = places.street_key("2802 142nd Ave. East PO Box 878")
    b = places.street_key("2802 142nd Ave E")
    assert a[0] == b[0] == "2802" and a[1] & b[1]


def test_a_different_house_number_is_a_different_place():
    assert places.street_key("100 Mill Rd")[0] != places.street_key("200 Mill Rd")[0]


def test_an_address_with_no_house_number_is_not_matched():
    got, why, _ = places.choose(fac(address="Industrial Park"), places.index_places([]))
    assert got is None and "house number" in why


# ---- the ambiguity rule, which is what the accuracy rests on

def test_one_place_at_the_address_is_taken():
    idx = places.index_places([place("Acme Truss", "2802 142nd Ave E", 47.2313, -122.2440)])
    got, why, cands = places.choose(fac(), idx)
    assert got and why == "" and len(cands) == 1


def test_several_tenants_of_one_building_are_still_one_building():
    """Within 150m the candidates are one site, and 32 such cases measured 0% beyond a kilometre."""
    idx = places.index_places([
        place("Acme Truss", "2802 142nd Ave E", 47.2313, -122.2440),
        place("Suite B Dental", "2802 142nd Ave E Ste B", 47.2314, -122.2441)])
    got, why, cands = places.choose(fac(), idx)
    assert got is not None and why == "" and len(cands) == 2
    assert places.spread_m(cands) <= places.AMBIGUOUS_SPREAD_M


def test_places_spread_across_a_business_park_are_refused():
    """15.4% of this bucket landed over a kilometre from truth — the one failure mode worth losing
    yield to avoid, because it puts a pin on another company's parcel."""
    idx = places.index_places([
        place("Somebody Else Ltd", "2802 142nd Ave E", 47.2313, -122.2440),
        place("Another Tenant", "2802 142nd Ave E", 47.2450, -122.2600)])
    got, why, _ = places.choose(fac(), idx)
    assert got is None and "spread over" in why


def test_a_name_match_may_still_pick_one_out_of_a_spread_out_set():
    idx = places.index_places([
        place("Somebody Else Ltd", "2802 142nd Ave E", 47.2313, -122.2440),
        place("Acme Truss Inc", "2802 142nd Ave E", 47.2450, -122.2600)])
    got, why, _ = places.choose(fac(), idx)
    assert got is not None and got["nm"] == "Acme Truss Inc" and why == ""


# ---- what it writes

def test_the_coordinate_is_not_called_a_rooftop_fix():
    """63m median is not a rooftop geocode, and survivorship ranks basis:place_match below one."""
    idx = places.index_places([place("Acme Truss", "2802 142nd Ave E", 47.2313, -122.2440)])
    got, _, cands = places.choose(fac(), idx)
    a = places.assertions_for(fac(), got, cands)[0]
    assert a["field"] == "lat_lon" and a["basis"] == "place_match"
    assert a["value"] == "47.231300,-122.244000"


def test_the_evidence_carries_the_overture_id_and_both_addresses():
    idx = places.index_places([place("Acme Truss", "2802 142nd Ave E", 47.2313, -122.2440)])
    got, _, cands = places.choose(fac(), idx)
    ev = places.assertions_for(fac(), got, cands)[0]["evidence"]
    assert "overture:" in ev and "2802 142nd Ave E" in ev and ":: matched " in ev


def test_a_website_and_a_phone_ride_along_as_separate_assertions():
    """Separate because survivorship judges each field on its own — a registry's phone should
    still outrank a POI listing's, while the coordinate may be the only one there is."""
    idx = places.index_places([place("Acme Truss", "2802 142nd Ave E", 47.2313, -122.2440,
                                     website="https://acme.example", phone="+1 214-555-1212")])
    got, _, cands = places.choose(fac(), idx)
    fields = {a["field"] for a in places.assertions_for(fac(), got, cands)}
    assert fields == {"lat_lon", "website", "phone"}


def test_a_phone_that_is_not_a_us_number_is_not_published():
    idx = places.index_places([place("Acme Truss", "2802 142nd Ave E", 47.2313, -122.2440,
                                     phone="not a number")])
    got, _, cands = places.choose(fac(), idx)
    assert {a["field"] for a in places.assertions_for(fac(), got, cands)} == {"lat_lon"}


def test_rerunning_the_stage_writes_the_same_row_hash():
    """ON CONFLICT DO NOTHING only makes a re-run a no-op if the hash is stable."""
    idx = places.index_places([place("Acme Truss", "2802 142nd Ave E", 47.2313, -122.2440)])
    got, _, cands = places.choose(fac(), idx)
    a = places.assertions_for(fac(), got, cands)[0]
    b = places.assertions_for(fac(), got, cands)[0]
    assert a["row_hash"] == b["row_hash"]


# ---- the gates

def test_gate_e2_admits_a_place_match_and_still_refuses_anything_else():
    ok = [{"field": "lat_lon", "basis": "place_match", "evidence": "overture:x :: a :: matched b"}]
    assert gates.e2_no_coordinate_from_a_non_rooftop_geocode(ok).passed
    bad = [{"field": "lat_lon", "basis": "street_center"}]
    assert not gates.e2_no_coordinate_from_a_non_rooftop_geocode(bad).passed


def test_gate_e7_refuses_a_place_match_that_cannot_be_rechecked():
    assert not gates.e7_every_place_match_cites_the_address_that_agreed(
        [{"field": "lat_lon", "basis": "place_match", "evidence": "trust me"}]).passed


def test_run_reports_why_each_refusal_happened():
    rep = places.run([fac(fid="IC-1"), fac(fid="IC-2", address="No Number Road")],
                     [place("Acme Truss", "2802 142nd Ave E", 47.2313, -122.2440)])
    assert rep["matched"] == 1
    assert "no house number in the address" in rep["refused"]


# ---- the state boxes

def test_a_state_box_is_derived_from_coordinates_the_release_already_holds():
    rows = [{"state": "OR", "lat_lon": "45.5,-122.6"}, {"state": "OR", "lat_lon": "44.0,-123.1"},
            {"state": "WA", "lat_lon": "47.6,-122.3"}]
    boxes = places.state_boxes(rows, pad_deg=1.0)
    assert set(boxes) == {"OR", "WA"}
    xmin, ymin, xmax, ymax = boxes["OR"]
    assert xmin == pytest.approx(-124.1) and ymax == pytest.approx(46.5)


def test_a_state_with_no_coordinates_yet_gets_no_box_rather_than_a_guess():
    assert "AK" not in places.state_boxes([{"state": "OR", "lat_lon": "45.5,-122.6"}])
