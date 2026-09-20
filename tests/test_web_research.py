import pytest
from pipeline.web_research.run import assess, safe_url
from pipeline.web_research.select import query, settings

def result(**fields):
    return {'status':'matched','fields':fields}

def candidate(value, quote=None, scope='facility'):
    return dict(value=value,quote=quote or value,scope=scope,source_kind='official',source_url='https://example.com/contact')

def test_field_needs_evidence_and_entity_anchor():
    r={'city':'Elma','facility_id':'IC-1'}
    c=candidate('360-482-2521','Elma phone 360-482-2521')
    pages={c['source_url']:{'text':c['quote'],'final_url':c['source_url']}}
    assert assess(r,result(phone=c),pages)['proposals'][0]['decision']=='candidate'
    assert assess(r,result(phone=c),{})['proposals'][0]['decision']=='review'

def test_state_conflict_blocks_all_candidates():
    r={'city':'Brigham City','state':'VA','facility_id':'IC-1'}
    c=candidate('UT','Brigham City UT')
    p={c['source_url']:{'text':c['quote']}}
    out=assess(r,result(state=c),p)
    assert out['status']=='conflict'
    assert out['proposals'][0]['decision']=='review'

def test_different_phone_retains_old_value_and_conflict():
    c=candidate('4109287700','Millington 4109287700')
    out=assess({'facility_id':'IC-1','city':'Millington','phone':'4109287732'},result(phone=c),{c['source_url']:{'text':c['quote']}})
    assert out['proposals'][0]['relationship']=='conflict'
    assert out['proposals'][0]['existing']=='4109287732'
    assert out['proposals'][0]['decision']=='review'

@pytest.mark.parametrize('url',['file:///etc/passwd','http://127.0.0.1','http://169.254.169.254','https://user:pw@example.com','http://[::1]'])
def test_private_and_credentialed_urls_blocked(url):
    with pytest.raises(ValueError): safe_url(url)

def test_filters_parameterized(monkeypatch):
    monkeypatch.setenv('FACILITY_IDS',"IC-1'; DROP TABLE golden_facility;--")
    monkeypatch.setenv('MISSING_FIELDS','phone,website')
    sql,params=query(settings())
    assert 'DROP TABLE' not in sql
    assert "IC-1'; DROP TABLE golden_facility;--" in params[0]
    assert 'g.phone' in sql and 'g.website' in sql

def test_unknown_field_rejected(monkeypatch):
    monkeypatch.setenv('MISSING_FIELDS','phone); DELETE')
    with pytest.raises(ValueError): settings()
