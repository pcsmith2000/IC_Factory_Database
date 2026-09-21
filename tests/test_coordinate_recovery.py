from copy import deepcopy
from pipeline.recovery.run import eligible,validate_rooftop
from pipeline.recovery.overture_rooftop import strict_address_match,strict_choose,control_gate
from pipeline.recovery.internal_crossmatch import choose as choose_internal,distinctive

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


def test_overture_rooftop_requires_exact_address_and_name():
    fac={'name':'Acme Components LLC','address':'10 Main Street','city':'Boston','state':'MA','zip':'02101'}
    place={'id':'p1','nm':'Acme Components','addr':'10 Main St','loc':'BOSTON','reg':'MA',
           'zip':'02101-1234','lat':42.1,'lon':-71.1}
    assert strict_address_match(fac,place)==(True,'matched')
    for key,value,reason in [('addr','11 Main St','street_number_mismatch'),
                             ('addr','10 Other St','street_name_mismatch'),
                             ('loc','CAMBRIDGE','city_mismatch'),
                             ('reg','NH','state_mismatch'),
                             ('zip','02102','zip_mismatch'),
                             ('nm','Different Tenant','name_mismatch')]:
        ok,why=strict_address_match(fac,{**place,key:value})
        assert not ok and why==reason


def test_overture_rooftop_refuses_distinct_place_points():
    fac={'facility_id':'IC-1','name':'Acme Components','address':'10 Main St',
         'city':'Boston','state':'MA','zip':'02101'}
    one={'id':'p1','nm':'Acme Components','addr':'10 Main Street','loc':'BOSTON','reg':'MA',
         'zip':'02101','lat':42.1,'lon':-71.1}
    assert strict_choose(fac,[one])[0]['id']=='p1'
    assert strict_choose(fac,[one,{**one,'id':'p2','lat':42.2}])==(None,'ambiguous_place_points')


def test_overture_control_gate_is_pre_registered_and_strict():
    assert control_gate([20.0]*10)[0]
    assert not control_gate([20.0]*9)[0]
    assert not control_gate([20.0]*9+[600.0])[0]


def test_internal_crossmatch_requires_exact_distinctive_site_and_one_coordinate():
    target={'facility_id':'IC-T','name':'Acme Components LLC','city':'Boston','state':'MA'}
    donor={'facility_id':'IC-D','name':'ACME COMPONENTS INC','city':'BOSTON','state':'ma',
           'value':'42.1,-71.1','assertion_id':'a1'}
    picked,refused=choose_internal([target],[donor])
    assert len(picked)==1 and picked[0]['donor']['facility_id']=='IC-D'
    assert not refused
    conflicting={**donor,'facility_id':'IC-D2','value':'42.2,-71.2','assertion_id':'a2'}
    assert choose_internal([target],[donor,conflicting])[0]==[]
    assert choose_internal([{**target,'city':'Cambridge'}],[donor])[0]==[]
    assert distinctive('Acme Components') and not distinctive('ABC')
