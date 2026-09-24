"""The street check accepts USPS spellings of the same street and nothing looser."""
import pytest

from pipeline.recovery.streets import canonical, street_equivalent
from pipeline.recovery.run import validate_rooftop
from pipeline.web_research.rooftops import street_matches, street_rule

# Every pair below was rejected as street_name_mismatch by the verbatim check in campaign pass
# paid-1 although Geocodio had returned a rooftop with the same house number, city and state.
SAME_STREET = [
    ('Commercial Circle', 'Commercial Cir'), ('E. First St.', 'E 1st St'), ('SW 252 Street', 'SW 252nd St'),
    ('Ninth Avenue', '9th Ave E'), ('Saint Johns Road', 'St Johns Rd'), ('E. Fort Wayne Rd', 'Ft Wayne Rd'),
    ('Highway 231', 'US-231'), ('N Hwy 231', 'US-231'), ('Route 522', 'US-522'), ('SR 17 N', 'State Rte 17 N'),
    ('US Hwy 221 South', 'US-221 S'), ('S Highway 319', 'U S Hwy 319 S'), ('GA Highway 3', 'GA Hwy 3'),
    ('cr 31', 'County Rd 31'), ('C R # 3 South', 'County Rd 3'), ('I-45 South', 'I-45'), ('East I 20', 'E I-20'),
    ('Route 442 Highway', 'State Rte 442'), ('NW US Highway 24', 'NW US-24 Hwy'),
    ('Old U.S. Highway 54', 'Old United States Highway 54'), ('Old 421 Road', 'Old 421st Rd'),
    ('120 Fairview', 'Fairview St'), ('Clarcona', 'Clarcona Rd'), ('Airport Road', 'S Airport Rd'),
    ('Gees Mill Road', 'Gees Mill Rd NE'), ('NW 32 Ave Bay B', 'NW 32nd Ave'), ('Space Blvd Lot#104', 'Space Blvd'),
    ('D N.W. 16 Terrace', 'NW 16th Ter'), ('E. 27th St. Terrace', 'E 27th Ter'), ('Clary Connector', 'Clary Conn'),
    ('Park Place', 'Park Pl'), ('Industrial Park Place', 'Industrial Park Pl'),
    ('Delany RD', 'Delaney Rd'), ('Satrun Blvd', 'Saturn Blvd'), ('Etheridge Rd', 'Ethridge Rd'),
]
# Different streets, or not knowably the same one.
DIFFERENT_STREET = [
    ('Alamo Dr', 'Alamo Rd'), ('Eisenhower Dr.', 'Eisenhower Rd'), ('Austin Street', 'Austin Dr'), ('Oak St', 'Oak Ave'),
    ('SW Silver Springs Blvd.', 'W Silver Springs Blvd'), ('N Main St', 'S Main St'),
    ('GA Highway 3', 'US-19'), ('Highway 11W', 'Appalachian Hwy'), ('Hwy. 17 N.', 'Jones St'),
    ('Main St', 'Maine St'), ('Jones St', 'Jonas St'), ('New Tamap Highway', 'New Tampa Hwy'),
    ('Anthony Grove CH Rd.', 'Anthony Grove Rd'), ('Main St', ''),
]


@pytest.mark.parametrize('left,right', SAME_STREET)
def test_same_street_under_usps_spelling(left, right):
    ok, rule = street_equivalent(left, right)
    assert ok, (left, right, rule)
    assert rule in ('exact', 'canonical', 'one_edit')


@pytest.mark.parametrize('left,right', DIFFERENT_STREET)
def test_different_streets_are_refused(left, right):
    ok, rule = street_equivalent(left, right)
    assert not ok, (left, right, rule)


def test_one_edit_tolerance_needs_a_long_word():
    assert street_equivalent('Delaney Rd', 'Delany Rd') == (True, 'one_edit')
    assert not street_equivalent('Jones Rd', 'Jonas Rd')[0]          # five letters: a different name
    assert not street_equivalent('Delaney Rd', 'Delany Ave')[0]      # the suffix still has to agree


def test_canonical_parts():
    assert canonical('N Hwy 231') == {'core': [], 'route': '231', 'suffix': '', 'dirs': {'n'}}
    assert canonical('East First Street') == {'core': ['1'], 'route': '', 'suffix': 'st', 'dirs': {'e'}}
    assert canonical('Loop Rd') == {'core': ['loop'], 'route': '', 'suffix': 'rd', 'dirs': set()}
    assert canonical('W Loop 340') == {'core': [], 'route': '340', 'suffix': '', 'dirs': {'w'}}


def test_rooftop_validation_uses_the_canonical_street():
    row = {'address': '2676 Highway 231', 'city': 'Cottondale', 'state': 'FL', 'zip': '32431'}
    hit = {'accuracy_type': 'rooftop', 'accuracy': 1,
           'address_components': {'number': '2676', 'formatted_street': 'US-231', 'city': 'Cottondale',
                                  'state_province': 'FL', 'postal_code': '32431'},
           'location': {'lat': 30.8, 'lng': -85.4}}
    assert validate_rooftop(row, {'response': {'results': [hit]}})[1] == 'rooftop_verified'
    assert street_rule(row['address'], hit['address_components']) == (True, 'canonical')
    assert street_matches('2676 Highway 231', hit['address_components'])
    other = {**hit, 'address_components': {**hit['address_components'], 'formatted_street': 'US-90'}}
    assert validate_rooftop(row, {'response': {'results': [other]}})[1] == 'street_name_mismatch'


# With house number, city, state and five-digit ZIP already matched against a rooftop result, the
# suffix or a one-sided directional may differ; a directional conflict or a different name may not.
PARCEL_SAME = [('Alamo Dr', 'Alamo Rd'), ('Eisenhower Dr.', 'Eisenhower Rd'), ('PALM AVE', 'Palm St'),
               ('INDUSTRIAL RD', 'E Industrial Dr'), ('Imperial Loop Drive', 'Imperial Loop'),
               ('McNaughton Street', 'Mc Naughton Ave'), ('W SAM HOUSTON PARKWAY N STE 500', 'W Sam Houston Pkwy'),
               ('Martin Luther King Ave', 'Martin Luther King Jr Ave'), ('FM-2100', 'Farm To Market Rd 2100th Rd'),
               ('US HWY 6', 'US-Rte 6')]
PARCEL_DIFFERENT = [('SOUTH LAKE STREET', 'N Lake St'), ('NORTH HERITAGE ROAD', 'S Heritage Rd'),
                    ('Iris Drive SW', 'Iris Dr SE'), ('SW Silver Springs Blvd.', 'W Silver Springs Blvd'),
                    ('New Tamap Highway', 'New Tampa Hwy'), ('Delany Rd', 'Delaney Ave'), ('GA Highway 3', 'US-19')]


@pytest.mark.parametrize('left,right', PARCEL_SAME)
def test_parcel_rule_tolerates_suffix_and_one_sided_directional(left, right):
    ok, rule = street_equivalent(left, right, parcel=True)
    assert ok, (left, right, rule)


@pytest.mark.parametrize('left,right', PARCEL_DIFFERENT)
def test_parcel_rule_refuses_direction_conflicts_and_other_names(left, right):
    assert not street_equivalent(left, right, parcel=True)[0]


def test_parcel_rule_needs_the_zip():
    assert not street_equivalent('Alamo Dr', 'Alamo Rd')[0]
    assert street_equivalent('Alamo Dr', 'Alamo Rd', parcel=True) == (True, 'parcel')
    row = {'address': '4746 Alamo Dr', 'city': 'Bowie', 'state': 'TX', 'zip': ''}
    parts = {'number': '4746', 'formatted_street': 'Alamo Rd', 'city': 'Bowie', 'state': 'TX', 'zip': '76230'}
    result = {'response': {'results': [{'accuracy_type': 'rooftop', 'address_components': parts, 'location': {'lat': 33.5, 'lng': -97.8}}]}}
    assert validate_rooftop(row, result)[1] == 'street_name_mismatch'
    assert validate_rooftop({**row, 'zip': '76230'}, result)[1] == 'rooftop_verified'
    assert validate_rooftop({**row, 'zip': '76231'}, result)[1] == 'zip_mismatch'
