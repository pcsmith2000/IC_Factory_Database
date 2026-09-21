from copy import deepcopy
from pipeline.recovery.run import eligible,validate_rooftop

ROW={'address':'10 Main Street','city':'Boston','state':'MA','zip':'02101'}
HIT={'accuracy_type':'rooftop','accuracy':1,'address_components':{'number':'10','formatted_street':'Main St','city':'Boston','state_province':'MA','postal_code':'02101'},'location':{'lat':42.1,'lng':-71.1}}

def test_complete_physical_addresses_only():
    assert eligible(ROW)
    for k,v in [('address','PO Box 10'),('city',''),('state',''),('state','XX')]:assert not eligible({**ROW,k:v})

def test_verified_rooftop_requires_all_address_components():
    assert validate_rooftop(ROW,{'response':{'results':[HIT]}})[1]=='rooftop_verified'
    for k,v in [('number','11'),('formatted_street','Other St'),('city','Cambridge'),('state_province','NH'),('postal_code','02102')]:
        h=deepcopy(HIT);h['address_components'][k]=v
        assert validate_rooftop(ROW,{'response':{'results':[h]}})[0] is None

def test_coarse_and_invalid_points_are_never_counted():
    for quality in ('range_interpolation','street_center','nearest_rooftop_match'):
        h={**HIT,'accuracy_type':quality};assert validate_rooftop(ROW,{'response':{'results':[h]}})[0] is None
    h={**HIT,'location':{'lat':float('nan'),'lng':-71.1}}
    assert validate_rooftop(ROW,{'response':{'results':[h]}})[0] is None
    assert validate_rooftop(ROW,{'response':{'results':[]}})[1]=='no_result'
