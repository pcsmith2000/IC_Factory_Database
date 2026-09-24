"""Gate E10: a golden coordinate must lie in the facility's own state."""
from pipeline.enrich import geo
from pipeline.enrich.gates import e6_rebuilt_golden_loses_nothing, e10_no_coordinate_outside_its_state
from pipeline.recovery.run import identity_for


def test_inside_state_is_generous_at_the_border_and_strict_across_the_country():
    assert geo.inside_state(38.98, -77.02, 'DC')
    assert geo.inside_state(45.6, -122.7, 'OR')          # Portland / Vancouver: the Columbia is the line
    assert geo.inside_state(45.6, -122.7, 'WA')          # a point near a state line passes both
    assert geo.inside_state(40.58, -77.36, 'NJ') is False  # a Pennsylvania point is not New Jersey
    assert geo.inside_state(40.58, -77.36, 'XX') is None
    assert geo.inside_state(40.58, -77.36, None) is None


def test_out_of_state_reports_only_judgeable_rows():
    rows = [{'facility_id': 'IC-1', 'state': 'NJ', 'lat_lon': '40.58085,-77.364448', 'lat_lon__source': 'geocode:geocodio'},
            {'facility_id': 'IC-2', 'state': 'PA', 'lat_lon': '40.58085,-77.364448', 'lat_lon__source': 'geocode:geocodio'},
            {'facility_id': 'IC-3', 'state': None, 'lat_lon': '40.58085,-77.364448'},
            {'facility_id': 'IC-4', 'state': 'NJ', 'lat_lon': 'nan,1'},
            {'facility_id': 'IC-5', 'state': 'NJ', 'lat_lon': ''}]
    bad = geo.out_of_state(rows)
    assert [b['facility_id'] for b in bad] == ['IC-1']
    assert bad[0]['source'] == 'geocode:geocodio'


def test_e6_tolerates_exactly_the_coordinates_e10_withheld():
    before = {'__rows': 10, 'lat_lon': 8, 'address': 9}
    assert not e6_rebuilt_golden_loses_nothing(before, {'__rows': 10, 'lat_lon': 6, 'address': 9}).passed
    assert e6_rebuilt_golden_loses_nothing(before, {'__rows': 10, 'lat_lon': 6, 'address': 9}, {'lat_lon': 2}).passed
    assert not e6_rebuilt_golden_loses_nothing(before, {'__rows': 10, 'lat_lon': 5, 'address': 9}, {'lat_lon': 2}).passed
    assert not e6_rebuilt_golden_loses_nothing(before, {'__rows': 10, 'lat_lon': 8, 'address': 8}, {'lat_lon': 2}).passed
    assert e10_no_coordinate_outside_its_state([]).passed
    assert 'withheld' in e10_no_coordinate_outside_its_state([{'facility_id': 'IC-1'}]).summary


def test_campaign_row_is_worked_under_a_verified_live_identity_only():
    frozen = {'name': 'Cavco - Durango', 'city': 'Phoenix', 'state': 'AZ'}
    live = {'name': 'Clayton - TRU Halls', 'city': 'Knoxville', 'state': 'TN'}
    assert identity_for(frozen, frozen, None) == frozen
    assert identity_for(frozen, live, None) is None
    assert identity_for(frozen, live, {'address': '1 Main St'}) is None
    verified = {'address': '3926 Fountain Valley Road', '_identity': dict(live)}
    assert identity_for(frozen, live, verified) == live
    stale = {'address': '3926 Fountain Valley Road', '_identity': {'name': 'Someone Else', 'city': 'Knoxville', 'state': 'TN'}}
    assert identity_for(frozen, live, stale) is None


def test_e11_withholds_assertions_carried_from_a_release_that_named_another_plant():
    from pipeline.enrich.identity import split_carried
    cur = 'rel.now'; old = 'rel.then'
    a = [{'facility_id': 'IC-95293', 'field': 'name', 'value': 'Ladabuild', 'release_tag': cur, 'source_id': 'adl_july'},
         {'facility_id': 'IC-95293', 'field': 'name', 'value': 'Blueprint Robotics - Baltimore', 'release_tag': old, 'source_id': 'adl_july'},
         {'facility_id': 'IC-95293', 'field': 'address', 'value': '1500 Broening Hwy', 'release_tag': old, 'source_id': 'tako_ai_search'},
         {'facility_id': 'IC-95293', 'field': 'lat_lon', 'value': '39.27,-76.54', 'release_tag': old, 'source_id': 'geocode:geocodio'},
         {'facility_id': 'IC-1', 'field': 'name', 'value': 'Same Plant', 'release_tag': cur, 'source_id': 'x'},
         {'facility_id': 'IC-1', 'field': 'name', 'value': 'SAME PLANT', 'release_tag': old, 'source_id': 'x'},
         {'facility_id': 'IC-1', 'field': 'address', 'value': '1 Main St', 'release_tag': old, 'source_id': 'enrich:locate'},
         {'facility_id': 'IC-2', 'field': 'name', 'value': 'No Name Then', 'release_tag': cur, 'source_id': 'x'},
         {'facility_id': 'IC-2', 'field': 'lat_lon', 'value': '1,1', 'release_tag': 'rel.enrich-only', 'source_id': 'geocode:geocodio'}]
    kept, withheld, unjudged = split_carried(a, cur)
    assert {(w['facility_id'], w['field']) for w in withheld} == {('IC-95293', 'name'), ('IC-95293', 'address'), ('IC-95293', 'lat_lon')}
    assert unjudged == 1
    assert ('IC-1', 'address') in {(k['facility_id'], k['field']) for k in kept}
    assert ('IC-2', 'lat_lon') in {(k['facility_id'], k['field']) for k in kept}


def test_carry_over_is_by_identity_not_by_id():
    from pipeline.enrich.identity import carry_by_identity
    cur = 'rel.now'; old = 'rel.then'
    def row(fid, field, value, tag, src='x'): return {'facility_id': fid, 'field': field, 'value': value, 'release_tag': tag, 'source_id': src}
    a = [row('IC-95293', 'name', 'Ladabuild', cur), row('IC-95293', 'city', 'GRAND JUNCTION', cur), row('IC-95293', 'state', 'CO', cur),
         row('IC-95295', 'name', 'Blueprint Robotics - Baltimore', cur), row('IC-95295', 'city', 'Baltimore', cur), row('IC-95295', 'state', 'MD', cur),
         # in the old release IC-95293 WAS Blueprint Robotics Baltimore, and its address and rooftop were written under that id
         row('IC-95293', 'name', 'Blueprint Robotics - Baltimore', old), row('IC-95293', 'city', 'BALTIMORE', old), row('IC-95293', 'state', 'MD', old),
         row('IC-95293', 'address', '1500 Broening Hwy', old, 'tako_ai_search'), row('IC-95293', 'lat_lon', '39.27,-76.54', old, 'geocode:geocodio'),
         # a plant that kept its id
         row('IC-1', 'name', 'Same Plant', cur), row('IC-1', 'city', 'X', cur), row('IC-1', 'state', 'TX', cur),
         row('IC-1', 'name', 'SAME PLANT', old), row('IC-1', 'city', 'x', old), row('IC-1', 'state', 'tx', old), row('IC-1', 'address', '1 Main St', old, 'enrich:locate'),
         # a plant the current release no longer contains
         row('IC-9', 'name', 'Gone Plant', old), row('IC-9', 'city', 'Y', old), row('IC-9', 'state', 'OK', old), row('IC-9', 'lat_lon', '2,2', old, 'geocode:geocodio')]
    kept, withheld, counts = carry_by_identity(a, cur)
    by = {(k['facility_id'], k['field'], k['value']) for k in kept}
    assert ('IC-95295', 'address', '1500 Broening Hwy') in by and ('IC-95295', 'lat_lon', '39.27,-76.54') in by
    assert not any(k['facility_id'] == 'IC-95293' and k['field'] in ('address', 'lat_lon') for k in kept)
    assert ('IC-1', 'address', '1 Main St') in by
    assert {(w['facility_id'], w['field']) for w in withheld if w['field'] == 'lat_lon'} == {('IC-9', 'lat_lon')}
    assert counts['carried_rekeyed_by_identity'] == 5 and counts['carried_same_id_same_plant'] == 4
    moved = [k for k in kept if k.get('carried_from_facility_id') == 'IC-95293']
    assert all(k['facility_id'] == 'IC-95295' for k in moved) and len(moved) == 5


def test_identity_carry_prefers_the_written_id_when_the_current_release_holds_duplicates():
    from pipeline.enrich.identity import carry_by_identity
    cur = 'rel.now'; old = 'rel.then'
    def row(fid, field, value, tag, src='x'): return {'facility_id': fid, 'field': field, 'value': value, 'release_tag': tag, 'source_id': src}
    a = [row('IC-1', 'name', 'Builders FirstSource', cur), row('IC-1', 'city', 'Orlando', cur), row('IC-1', 'state', 'FL', cur),
         row('IC-2', 'name', 'Builders FirstSource', cur), row('IC-2', 'city', 'Orlando', cur), row('IC-2', 'state', 'FL', cur),
         row('IC-2', 'name', 'Builders FirstSource', old), row('IC-2', 'city', 'Orlando', old), row('IC-2', 'state', 'FL', old),
         row('IC-2', 'lat_lon', '28.5,-81.4', old, 'geocode:geocodio'),
         row('IC-7', 'name', 'Builders FirstSource', old), row('IC-7', 'city', 'Orlando', old), row('IC-7', 'state', 'FL', old),
         row('IC-7', 'lat_lon', '28.6,-81.3', old, 'geocode:geocodio')]
    kept, withheld, counts = carry_by_identity(a, cur)
    assert ('IC-2', 'lat_lon') in {(k['facility_id'], k['field']) for k in kept}
    assert {w['facility_id'] for w in withheld} == {'IC-7'}
    assert all(w['reason'] == 'identity_ambiguous_in_current_release' for w in withheld)


def test_identity_rows_of_other_releases_are_what_lets_carried_enrichment_through():
    """fetch_assertions returns only the current release plus carried enrichment; the old release's
    own name/city/state arrive separately as identity_rows. Without them everything is withheld."""
    from pipeline.enrich.identity import carry_by_identity
    cur = 'rel.now'; old = 'rel.then'
    def row(fid, field, value, tag, src='x'): return {'facility_id': fid, 'field': field, 'value': value, 'release_tag': tag, 'source_id': src}
    asserts = [row('IC-1', 'name', 'Same Plant', cur), row('IC-1', 'city', 'X', cur), row('IC-1', 'state', 'TX', cur),
               row('IC-1', 'building_sqft', '84210', old, 'overture:building')]
    kept, withheld, _ = carry_by_identity(asserts, cur)
    assert [w['reason'] for w in withheld] == ['no_identity_in_that_release']
    ident = [row('IC-1', 'name', 'SAME PLANT', old), row('IC-1', 'city', 'x', old), row('IC-1', 'state', 'tx', old)]
    kept, withheld, counts = carry_by_identity(asserts, cur, ident)
    assert not withheld and ('IC-1', 'building_sqft') in {(k['facility_id'], k['field']) for k in kept}
    assert counts['carried_same_id_same_plant'] == 1


def test_any_shared_spelling_identifies_the_plant():
    from pipeline.enrich.identity import carry_by_identity
    cur = 'rel.now'; old = 'rel.then'
    def row(fid, field, value, tag, src='x'): return {'facility_id': fid, 'field': field, 'value': value, 'release_tag': tag, 'source_id': src}
    # the current release spells the city two ways; the old release used the second one
    asserts = [row('IC-5', 'name', 'Clayton Homes', cur), row('IC-5', 'city', 'Ft Worth', cur), row('IC-5', 'city', 'Fort Worth', cur), row('IC-5', 'state', 'TX', cur),
               row('IC-9', 'lat_lon', '32.7,-97.3', old, 'geocode:geocodio')]
    ident = [row('IC-9', 'name', 'CLAYTON HOMES', old), row('IC-9', 'city', 'FORT WORTH', old), row('IC-9', 'state', 'TX', old)]
    kept, withheld, counts = carry_by_identity(asserts, cur, ident)
    assert not withheld and counts['carried_rekeyed_by_identity'] == 1
    assert [k for k in kept if k['field'] == 'lat_lon'][0]['facility_id'] == 'IC-5'


def test_a_plant_the_current_release_knows_by_a_unique_name_alone_keeps_its_coordinate():
    """65 current facilities assert a name but no city and no state, and an earlier release located
    exactly that name with a locality. Nothing contradicts; a unique name carries. A name two current
    facilities share, or one the current release places somewhere else, does not."""
    from pipeline.enrich.identity import carry_by_identity
    cur = 'rel.now'; old = 'rel.then'
    def row(fid, field, value, tag, src='x'): return {'facility_id': fid, 'field': field, 'value': value, 'release_tag': tag, 'source_id': src}
    asserts = [row('IC-1', 'name', 'Smart Sheds Industries LLC', cur),
               row('IC-2', 'name', 'Rapid Home', cur), row('IC-3', 'name', 'Rapid Home', cur),
               row('IC-4', 'name', 'Moved Plant', cur), row('IC-4', 'city', 'Austin', cur), row('IC-4', 'state', 'TX', cur),
               row('IC-1', 'lat_lon', '33.1,-87.2', old, 'geocode:geocodio'),
               row('IC-2', 'lat_lon', '1,1', old, 'geocode:geocodio'),
               row('IC-4', 'lat_lon', '2,2', old, 'geocode:geocodio')]
    ident = [row('IC-1', 'name', 'SMART SHEDS INDUSTRIES, LLC', old), row('IC-1', 'city', 'Cullman', old), row('IC-1', 'state', 'AL', old),
             row('IC-2', 'name', 'Rapid Home', old), row('IC-2', 'city', 'X', old), row('IC-2', 'state', 'OK', old),
             row('IC-4', 'name', 'Moved Plant', old), row('IC-4', 'city', 'Tulsa', old), row('IC-4', 'state', 'OK', old)]
    kept, withheld, counts = carry_by_identity(asserts, cur, ident)
    carried = {(k['facility_id'], k['field']) for k in kept if k.get('release_tag') == old}
    assert ('IC-1', 'lat_lon') in carried and counts['carried_by_unique_name'] == 1
    assert {w['facility_id'] for w in withheld} == {'IC-2', 'IC-4'}


def test_an_address_bound_fact_follows_the_street_address_to_the_current_plant_standing_at_it():
    """84 unlocated current facilities stand at a numbered address an earlier release located under
    another id and another name. The coordinate belongs to the parcel; the name does not travel."""
    from pipeline.enrich.identity import carry_by_identity
    cur = 'rel.now'; old = 'rel.then'
    def row(fid, field, value, tag, src='x'): return {'facility_id': fid, 'field': field, 'value': value, 'release_tag': tag, 'source_id': src}
    asserts = [row('IC-1', 'name', 'New Owner LLC', cur), row('IC-1', 'city', 'Desoto', cur), row('IC-1', 'state', 'TX', cur), row('IC-1', 'address', '1200 Industrial Blvd', cur),
               row('IC-2', 'name', 'Twin A', cur), row('IC-2', 'state', 'OH', cur), row('IC-2', 'address', '5 Main St', cur),
               row('IC-3', 'name', 'Twin B', cur), row('IC-3', 'state', 'OH', cur), row('IC-3', 'address', '5 Main St', cur),
               row('IC-7', 'lat_lon', '32.6,-96.9', old, 'geocode:geocodio'), row('IC-7', 'website', 'old-owner.example', old, 'enrich:locate'),
               row('IC-8', 'lat_lon', '40.1,-82.9', old, 'geocode:geocodio')]
    ident = [row('IC-7', 'name', 'Old Owner Inc', old), row('IC-7', 'city', 'DeSoto', old), row('IC-7', 'state', 'TX', old), row('IC-7', 'address', '1200 INDUSTRIAL BLVD.', old),
             row('IC-8', 'name', 'Gone', old), row('IC-8', 'state', 'OH', old), row('IC-8', 'address', '5 Main St', old)]
    kept, withheld, counts = carry_by_identity(asserts, cur, ident)
    moved = {(k['facility_id'], k['field']): k for k in kept if k.get('carried_from_facility_id')}
    assert ('IC-1', 'lat_lon') in moved and moved[('IC-1', 'lat_lon')]['carried_by'] == 'address'
    assert ('IC-1', 'website') not in moved                       # a website is the business's, not the parcel's
    assert {(w['facility_id'], w['field']) for w in withheld} == {('IC-7', 'website'), ('IC-8', 'lat_lon')}   # 5 Main St is two current plants
    assert counts['carried_by_address'] == 1
