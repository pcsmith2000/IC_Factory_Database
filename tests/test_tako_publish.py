import copy
from pathlib import Path
import pytest
from pipeline.golden import build_golden, _rank
from pipeline.web_research.publish import load_manifest, select_import, to_assertion, SOURCE, digest
from pipeline.registry import load_yaml


def manifest():
    return {'campaign_runs':[1], 'assertions':[{'facility_id':'IC-1','field':'phone','value':'5551234567','approved':True,
            'source_url':'https://example.org/contact','quote':'5551234567','scope':'facility',
            'review_note':'Verified direct facility contact','retrieved_date':'2026-09-20'}]}


def test_tako_is_below_every_rank_even_unlisted_and_more_recent():
    rules=load_yaml(Path('registry/survivorship.yaml'))
    for field, spec in rules['fields'].items():
        a={'facility_id':'IC-1','field':field,'value':'search','source_id':SOURCE,'source_class':SOURCE,
           'retrieved_date':'2099-01-01','basis':'rooftop','site_visit':True}
        assert _rank(a,spec['order'])>len(spec['order'])
        other={**a,'source_id':'future-unlisted-source','source_class':'unknown','value':'stronger','retrieved_date':'2000-01-01','basis':'none','site_visit':False}
        for ordered in ([a,other],[other,a]):
            golden,_=build_golden(ordered,rules)
            assert golden[0][field]=='stronger'
        assert build_golden([a],rules)[0][0][field]=='search'


def test_only_current_nonconflicting_fields_can_be_imported():
    m=manifest();g=[{'facility_key':'IC-1','release_tag':'current','phone':None}]
    accepted,skipped=select_import(m,g,[])
    assert len(accepted)==1 and not skipped
    assert accepted[0]['release_tag']=='current'
    assert accepted[0]['source_class']==SOURCE
    assert not select_import(m,[],[])[0]
    assert not select_import(m,[{**g[0],'phone':'existing'}],[])[0]
    assert select_import(m,[{**g[0],'phone':'+1 (555) 123-4567'}],[])[0]==accepted
    existing=[{'facility_key':'IC-1','field_key':'phone','source_key':'operator','value':'other'}]
    assert not select_import(m,g,existing)[0]
    existing[0].update(source_key=SOURCE,value='other')
    assert not select_import(m,g,existing)[0]
    existing[0].update(value='5551234567',source_key='other-source')
    assert select_import(m,g,existing)[0]==accepted  # idempotent retries


def test_provenance_and_idempotency_are_stable():
    m=manifest();a=to_assertion(m['assertions'][0],m)
    assert a==to_assertion(m['assertions'][0],m)
    assert a['evidence'].startswith('https://example.org/contact :: ')
    assert 'Verified direct facility contact' in a['evidence']
    assert 'Campaign runs: 1' in a['evidence']
    changed=copy.deepcopy(m);changed['assertions'][0]['value']='other'
    assert a['row_hash']!=to_assertion(changed['assertions'][0],changed)['row_hash']
    assert digest(m)!=digest(changed)


def test_committed_manifest_is_reviewed_unique_and_has_no_rooftops():
    m=load_manifest()
    assert m['assertions']
    assert all(r['approved'] and r['field']!='lat_lon' for r in m['assertions'])
    assert ('IC-95284','phone') not in {(r['facility_id'],r['field']) for r in m['assertions']}


def test_unapproved_manifest_is_rejected(tmp_path):
    import json
    m=manifest();m.update(source_id=SOURCE,approval='approved');m['assertions'][0]['approved']=False
    p=tmp_path/'input.json';p.write_text(json.dumps(m))
    with pytest.raises(ValueError):load_manifest(p)


def test_recycled_or_changed_facility_identity_is_held():
    m=manifest();m['assertions'][0]['expected_identity']={'name':'Original Plant','city':'Boston','state':'MA'}
    g=[{'facility_key':'IC-1','release_tag':'current','name':'Different Plant','city':'Boston','state':'MA'}]
    accepted,skipped=select_import(m,g,[])
    assert not accepted and 'identity changed' in skipped[0]['reason']

def test_strict_address_bundle_can_record_bounded_correction_at_lowest_precedence():
    m=manifest();m['approval']='strict_automatic_address_bundle_v1'
    r=m['assertions'][0]
    r.update(field='city',value='Redmond',expected_current_value='Remond',
             correction_kind='minor_city_spelling',expected_identity={'name':'Plant','city':'Remond','state':'OR'})
    g=[{'facility_key':'IC-1','release_tag':'current','name':'Plant','city':'Remond','state':'OR'}]
    existing=[{'facility_key':'IC-1','field_key':'city','source_key':'directory','value':'Remond'}]
    accepted,skipped=select_import(m,g,existing)
    assert len(accepted)==1 and not skipped

def test_correction_authorization_does_not_cover_unrelated_change():
    m=manifest();m['approval']='strict_automatic_address_bundle_v1'
    r=m['assertions'][0]
    r.update(field='city',value='Garland',expected_current_value='Dallas',
             correction_kind='minor_city_spelling')
    g=[{'facility_key':'IC-1','release_tag':'current','city':'Dallas'}]
    accepted,skipped=select_import(m,g,[])
    assert not accepted and skipped
