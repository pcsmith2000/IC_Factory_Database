import pytest
from pipeline.web_research.run import assess, safe_url
from pipeline.web_research.select import query, settings

@pytest.fixture(autouse=True)
def isolated_selector_settings(monkeypatch):
    # Workflow inputs must not alter test fixtures or parameter ordering.
    for key in ('STATES','FACILITY_IDS','MISSING_FIELDS','SOURCE_IDS','TIERS','RELEASE_TAG','ROW_LIMIT','ROW_OFFSET','SAMPLE_SEED'):
        monkeypatch.delenv(key,raising=False)

def result(**fields):
    return {'status':'matched','fields':fields}

def candidate(value, quote=None, scope='facility'):
    return dict(value=value,quote=quote or value,scope=scope,source_kind='official',source_url='https://example.com/contact')

def test_field_needs_evidence_and_entity_anchor():
    r={'city':'Elma','facility_id':'IC-1'}
    c=candidate('360-482-2521','Elma phone 360-482-2521')
    pages={c['source_url']:{'text':c['quote'],'final_url':c['source_url']}}
    assert assess(r,result(phone=c,website=candidate('https://example.com','Elma phone 360-482-2521')),pages)['proposals'][0]['decision']=='candidate'
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

def test_corporate_contact_not_facility_candidate():
    c=candidate('3604822521','Elma 3604822521',scope='company')
    out=assess({'facility_id':'IC-1','city':'Elma'},result(phone=c),{c['source_url']:{'text':c['quote']}})
    assert out['proposals'][0]['decision']=='review'

def test_evaluation_keeps_denominator_for_missing_results():
    from pipeline.web_research.evaluate import evaluate
    import json
    from pathlib import Path
    ref=json.loads(Path('tests/reference/tako_basic/reference.json').read_text())
    report=evaluate([],ref)
    assert report['passed']==0 and report['total']==39

@pytest.mark.parametrize('calls',[None,{}, {'tako_search':0},{'parallel_search':2},{'tako_search':-1}])
def test_requires_successful_tako_call(calls):
    from pipeline.web_research.run import confirmed_search_count
    raw={'choices':[{'message':{'provider_metadata':{'gateway':{'gatewayToolCalls':calls}}}}]}
    assert confirmed_search_count(raw)==0

def test_counts_successful_tako_calls():
    from pipeline.web_research.run import confirmed_search_count
    raw={'choices':[{'message':{'provider_metadata':{'gateway':{'gatewayToolCalls':{'tako_search':2}}}}}]}
    assert confirmed_search_count(raw)==2

def test_cost_estimate_separates_search_and_expires_promotion():
    from datetime import date
    from pipeline.web_research.costs import calculate
    catalog={'data':[{'id':'cheap','pricing':{'input':'0.00000025','output':'0.0000015'}}]}
    current=calculate(10,('cheap','cheap'),catalog,date(2026,9,20))
    later=calculate(10,('cheap','cheap'),catalog,date(2026,10,1))
    assert current['model_estimate_usd']==.15
    assert current['tako']['estimated_usd']==0
    assert later['tako']['estimated_usd']==.21
    assert later['estimated_total_usd']==.36

def test_null_field_object_means_unknown_not_failed_row():
    out=assess({'facility_id':'IC-1'},result(phone={'value':None}),{})
    assert out['proposals']==[]

def test_public_encoded_contact_is_decoded_without_running_script():
    from bs4 import BeautifulSoup
    from pipeline.web_research.run import decode_contact_spans
    text='Telephone: 908-561-3484'
    alphabet=''.join(sorted(set(text)))
    encoded=''.join(chr(alphabet.index(c)+48) for c in text)
    soup=BeautifulSoup(f'<span id="contact"></span><script>var ml="{alphabet}",mi="{encoded}";document.getElementById("contact");throw new Error("do not execute")</script>','html.parser')
    decode_contact_spans(soup)
    assert soup.find(id='contact').get_text()==text


def test_all_fields_removes_missing_filter_but_keeps_sources(monkeypatch):
    monkeypatch.setenv("MISSING_FIELDS", "all")
    monkeypatch.setenv("SOURCE_IDS", "adl_4ward,adl_july")
    sql, params = query(settings())
    assert "NULLIF" not in sql
    assert "a.source_key=ANY(%s)" in sql
    assert params[0] == ["adl_4ward", "adl_july"]
