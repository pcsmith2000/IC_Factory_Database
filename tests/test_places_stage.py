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


# ---- a coordinate is a property of the SITE, a website is a property of the COMPANY

def test_a_coordinate_is_taken_from_any_tenant_of_the_building():
    """Address match alone. Whoever else is listed there, the building is still where it is."""
    idx = places.index_places([place("Mobile Modular-Eugene", "2802 142nd Ave E", 47.2313, -122.2440,
                                     website="https://mobilemodular.example", phone="214-555-1212")])
    got, _, cands = places.choose(fac(name="The Truss Company"), idx)
    fields = {a["field"] for a in places.assertions_for(fac(name="The Truss Company"), got, cands)}
    assert fields == {"lat_lon"}


def test_a_website_is_not_taken_from_the_wrong_tenant():
    """The measured failure: Mobile Modular's site published as The Truss Company's, because the
    two share an address in Eugene. One row of 18, and the name gate cut exactly that one."""
    idx = places.index_places([place("Mobile Modular-Eugene", "2802 142nd Ave E", 47.2313, -122.2440,
                                     website="https://mobilemodular.example")])
    got, _, cands = places.choose(fac(name="The Truss Company"), idx)
    got_fields = [a for a in places.assertions_for(fac(name="The Truss Company"), got, cands)
                  if a["field"] == "website"]
    assert got_fields == []


def test_the_same_company_under_a_different_domain_is_still_taken():
    """bldr.com and bldrwashington.com are one company; so are thetrussco.com and medfordtruss.com.
    Disagreeing with the roster is not the same as being wrong."""
    idx = places.index_places([place("Builders FirstSource", "2802 142nd Ave E", 47.2313, -122.2440,
                                     website="https://bldrwashington.example")])
    f = fac(name="Builders FirstSource — Woodland")
    got, _, cands = places.choose(f, idx)
    assert any(a["field"] == "website" for a in places.assertions_for(f, got, cands))


def test_a_facility_that_already_has_a_coordinate_gets_the_contact_details_only():
    """Stage 13 must never restate a location a rooftop geocode already settled — but the website
    is the reason most of these matches are worth making at all."""
    rep = places.run([fac(fid="IC-9")], [place("Acme Truss", "2802 142nd Ave E", 47.2313, -122.2440,
                                               website="https://acme.example")], need_coord=set())
    assert rep["by_field"] == {"website": 1}


def test_the_contact_evidence_names_the_place_and_the_score_that_admitted_it():
    idx = places.index_places([place("Acme Truss", "2802 142nd Ave E", 47.2313, -122.2440,
                                     website="https://acme.example")])
    got, _, cands = places.choose(fac(), idx)
    ev = [a for a in places.assertions_for(fac(), got, cands) if a["field"] == "website"][0]["evidence"]
    assert "place named 'Acme Truss'" in ev and "name score" in ev


def test_a_facility_geocode_already_failed_on_is_exactly_who_this_stage_is_for():
    """Run 58 asked for 13 coordinates while 894 facilities in the states it read had an address
    and none. It had inherited geocode's eligibility, which excludes anything geocode has already
    TRIED — the right rule for not spending a second Geocodio lookup, and the exact inverse of the
    population stage 13 exists to serve. A street_center answer is a tried address that still has
    no coordinate."""
    from pipeline.enrich import run as enrich_run
    src = __import__("inspect").getsource(enrich_run.main)
    block = src[src.index('if args.stage == "places"'):src.index('if args.stage == "footprint"')]
    assert "need_ids" in block and "geocode_tried" not in block
    # and it must not simply reuse the list geocode built
    assert "need_ids = {r[\"facility_id\"] for r in need_coord}" not in block


def test_the_city_filter_uses_the_same_key_the_matcher_does():
    """The read filters on locality and `choose` keys on locality; if the two ever spell it
    differently the filter silently drops rows that would have matched. Run 59 died at 35:18
    reading 3.8M places for six states when the matcher could only look at the cities we hold
    facilities in, so the filter is worth having — and worth pinning."""
    import inspect
    from pipeline.enrich import run as enrich_run
    src = inspect.getsource(enrich_run.main)
    block = src[src.index('if args.stage == "places"'):src.index('if args.stage == "footprint"')]
    assert 'cities = {(x.get("city") or "").strip().upper() for x in sel}' in block
    assert "localities=cities" in block
    # and choose() must key on the same upper-cased city
    assert '(fac.get("city") or "").upper()' in inspect.getsource(places.choose)


def test_fetch_without_a_filter_set_still_reads_the_whole_box():
    """Passing no sets must not silently read nothing — an empty IN () matches nothing at all,
    which would look exactly like 'Overture has no places here'."""
    import inspect
    src = inspect.getsource(places.fetch)
    assert 'city = f" AND ({\' OR \'.join(ors)})" if ors else ""' in src


def test_the_read_lets_a_matching_postcode_in_as_well_as_a_matching_city():
    """Filtering on the city alone would read past exactly the rows the ZIP fallback exists to
    find — a plant filed under a different town than Overture files it under."""
    import inspect
    src = inspect.getsource(places.fetch)
    assert "addresses[1].postcode" in src and "' OR '.join(ors)" in src


def test_a_state_already_read_for_this_release_is_not_read_again():
    """The state ranking is by eligible facilities — a number a RUN does not change. Without a
    memory of which states were read, run 61 picks the same six as run 60 and the other 36 never
    get a turn. The key carries the Overture release, so a new release correctly re-reads."""
    import inspect
    from pipeline.enrich import run as enrich_run, cache
    src = inspect.getsource(enrich_run.main)
    block = src[src.index('if args.stage == "places"'):src.index('if args.stage == "footprint"')]
    assert "places_state_key" in block and "s not in done" in block
    assert cache.places_state_key("or", "2026-08-19.0") == cache.places_state_key("OR", "2026-08-19.0")
    assert cache.places_state_key("OR", "2026-08-19.0") != cache.places_state_key("OR", "2027-01-01.0")


def test_the_states_it_read_are_recorded_before_the_assertions_are_written():
    """If the mark were written after a crash-prone step the state would be re-read; if it were
    never written the batch never advances."""
    import inspect
    from pipeline.enrich import run as enrich_run
    src = inspect.getsource(enrich_run.main)
    block = src[src.index('if args.stage == "places"'):src.index('if args.stage == "footprint"')]
    assert block.index("lookup_cache.put") < block.index('places.assertions.json')


# ---- the ZIP fallback: the city is the field most likely to disagree

def test_the_postcode_finds_a_plant_filed_under_a_different_town():
    """A plant in an unincorporated area gets filed under the nearest town by one roster and the
    county seat by another, and neither is wrong. 9 of the 112 control facilities the city key
    missed were recovered this way, every one within 500m of the truth."""
    idx = places.index_places([{**place("Acme Truss", "2802 142nd Ave E", 47.2313, -122.2440,
                                        loc="SUMNER", reg="WA"), "zip": "98390"}])
    f = {"facility_id": "IC-1", "name": "Acme Truss", "address": "2802 142nd Ave E",
         "city": "BONNEY LAKE", "state": "WA", "zip": "98390-1234"}
    got, why, _ = places.choose(f, idx)
    assert got is not None and why == ""


def test_the_postcode_fallback_will_not_cross_a_state_line():
    """Ignoring the city entirely recovered 16 but 7 were a coincidental house number elsewhere,
    one 207km away. The postcode is the version of that which cannot do it."""
    idx = places.index_places([{**place("Somebody Else", "2802 142nd Ave E", 40.0, -100.0,
                                        loc="ELSEWHERE", reg="NE"), "zip": "98390"}])
    f = {"facility_id": "IC-1", "name": "Acme Truss", "address": "2802 142nd Ave E",
         "city": "BONNEY LAKE", "state": "WA", "zip": "98390"}
    assert places.choose(f, idx)[0] is None


def test_the_city_key_is_tried_before_the_postcode():
    """The city is the primary key; the postcode only answers when it found nothing."""
    idx = places.index_places([
        {**place("Right One", "100 Mill Rd", 47.0, -122.0, loc="TROY", reg="TX"), "zip": "75001"},
        {**place("Wrong One", "100 Mill Rd", 48.0, -123.0, loc="OTHER", reg="TX"), "zip": "75001"}])
    f = {"facility_id": "IC-1", "name": "Anything", "address": "100 Mill Rd",
         "city": "TROY", "state": "TX", "zip": "75001"}
    assert places.choose(f, idx)[0]["nm"] == "Right One"


def test_a_zip_plus_four_is_the_same_postcode():
    assert places.zip5("97205-1234") == places.zip5("97205") == "97205"
    assert places.zip5("") == "" and places.zip5("ABC") == ""
