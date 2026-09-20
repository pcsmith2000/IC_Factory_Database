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


def test_campaign_counts_unique_new_supported_values(tmp_path):
    import json
    from pipeline.web_research.campaign import report
    baseline=tmp_path/'baseline';baseline.mkdir()
    (baseline/'input.json').write_text(json.dumps([{'facility_id':'IC-1','phone':'111'}]))
    prior=tmp_path/'prior';prior.mkdir()
    def p(value,verified=True):
        return dict(field='phone',value=value,source_url='https://registry.gov/contact',scope='facility',source_kind='registry',quote_verified=verified,identity_anchor_found=True,decision='candidate',relationship='fill')
    (prior/'results.json').write_text(json.dumps([{'facility_id':'IC-1','proposals':[p('222')]}]))
    current=tmp_path/'current';current.mkdir()
    (current/'results.json').write_text(json.dumps([{'facility_id':'IC-1','proposals':[p('111'),p('222'),p('333'),p('333'),p('444',False)]}]))
    (current/'summary.json').write_text(json.dumps(dict(completed=1,selected=1,errors=[],usage=dict(known_cost_subtotal_usd=.01,responses_missing_cost=0))))
    report(tmp_path,current,tmp_path/'out')
    result=json.loads((tmp_path/'out'/'round-report.json').read_text())
    assert result['new_supported_details']==1
    assert result['details'][0]['value']=='333'


def test_rooftop_preview_retains_only_rooftop_and_deduplicates(tmp_path,monkeypatch):
    import json
    from pipeline.web_research import rooftops
    rows=[dict(facility_id='IC-1',address='123 First St',city='Test',state='AZ',reviewed=True,evidence_url='https://example.com',review_note='Official plant page'),dict(facility_id='IC-2',address='124 First St',city='Test',state='AZ',reviewed=True,evidence_url='https://example.com',review_note='Official plant page')]
    monkeypatch.setenv('GEOCODIO_API_KEY','test')
    calls=[]
    def fake(queries,key):
        calls.append(queries)
        return ([{'response':{'results':[{'accuracy_type':kind,'location':{'lat':1,'lng':2},'address_components':{'number':'123','formatted_street':'First St','city':'Test','state_province':'AZ'}}]}} for kind in ['rooftop','nearest_rooftop_match']],'')
    monkeypatch.setattr(rooftops,'_post',fake)
    rooftops.run(rows,tmp_path/'first')
    out=json.loads((tmp_path/'first'/'rooftop-results.json').read_text())
    assert out[0]['coordinates']=={'lat':1,'lng':2}
    assert out[1]['coordinates'] is None
    rooftops.run(rows,tmp_path/'second',tmp_path/'first')
    assert len(calls)==1
    rows[0]['reviewed']=False
    with pytest.raises(ValueError):rooftops.run(rows,tmp_path/'third')


def test_campaign_does_not_count_contact_formatting_as_new():
    from pipeline.web_research.campaign import normalized_detail
    assert normalized_detail('phone','+1 (818) 555-1234')==normalized_detail('phone','8185551234')
    assert normalized_detail('address','123 North First Street')==normalized_detail('address','123 N First St.')
    assert normalized_detail('zip','85001-1234')==normalized_detail('zip','85001')
    assert normalized_detail('address','123 First St Suite 2')!=normalized_detail('address','123 First St Suite 3')


def test_redacted_search_link_does_not_abort_facility():
    from pipeline.web_research.run import domain
    assert domain('https://[link removed]')==''
    assert domain('https://www.example.com/contact')=='example.com'


def test_campaign_private_registry_is_not_authoritative():
    from pipeline.web_research.campaign import checked_proposals, supported_detail
    base=dict(field='address',value='123 Main',scope='facility',quote_verified=True,identity_anchor_found=True,source_kind='registry',source_url='https://private-registry.com/contact')
    assert not supported_detail(next(checked_proposals({'proposals':[base]})))
    base['source_url']='https://state.gov/registry'
    assert supported_detail(next(checked_proposals({'proposals':[base]})))


def test_public_contact_email_decoding_preserves_exact_addresses():
    from bs4 import BeautifulSoup
    from pipeline.web_research.run import decode_public_email_links
    email='office@example.com';key=42
    encoded=bytes([key]+[ord(c)^key for c in email]).hex()
    soup=BeautifulSoup(f'<span data-cfemail="{encoded}">[email protected]</span><a href="mailto:plant%40example.com?subject=Hello">Email us</a><span data-cfemail="bad">Bad</span>','html.parser')
    decode_public_email_links(soup)
    assert email in soup.get_text()
    assert 'plant@example.com' in soup.get_text()
    assert 'subject=Hello' not in soup.get_text()


def test_rooftop_requires_matching_street_not_only_house_number():
    from pipeline.web_research.rooftops import street_matches
    assert street_matches('123 North First Street Suite 2',{'formatted_street':'N First St','unit_number':'2'})
    assert street_matches('5301 Polk St 20',{'formatted_street':'Polk St','unit_number':'20'})
    assert not street_matches('123 First St',{'formatted_street':'Second St'})
    assert not street_matches('831 New York 67 Bldg 46',{'formatted_street':'Church Ave','unit_number':'46'})


def test_campaign_ledger_holds_competing_fills(tmp_path):
    import json
    from pipeline.web_research.ledger import consolidate
    r=tmp_path/'round';r.mkdir()
    def phone(value):
        return dict(field='phone',value=value,source_url='https://state.gov/contact',quote='Plant phone '+value,scope='facility',source_kind='registry',quote_verified=True,identity_anchor_found=True,decision='candidate',relationship='fill')
    (r/'results.json').write_text(json.dumps([dict(facility_id='IC-1',database_writes=0,proposals=[phone('111'),phone('222')])]))
    result=consolidate([dict(facility_id='IC-1',name='Plant',phone=None)],[(1,r)],tmp_path/'out')
    assert result['distinct_proposed_values']==2
    assert result['cells_with_competing_proposals']==1
    assert result['noncompeting_candidate_fills']==0


def test_evidence_cache_keeps_original_age_and_retries_failed_pages(tmp_path):
    import json
    from datetime import datetime, timezone
    from pipeline.web_research.evidence_cache import recent_pages
    p=tmp_path/'IC-1';p.mkdir()
    (tmp_path/'run-manifest.json').write_text(json.dumps(dict(started_at='2026-09-20T12:00:00+00:00',run_id='1')))
    (p/'evidence.json').write_text(json.dumps({'https://fresh.example':{'text':'Public plant address'},'https://old.example':{'text':'Old address','fetched_at':'2026-09-19T12:00:00+00:00'},'https://blocked.example':{'error':'HTTPError'}}))
    pages=recent_pages(tmp_path,now=datetime(2026,9,20,13,tzinfo=timezone.utc))
    assert set(pages)=={'https://fresh.example'}
    assert pages['https://fresh.example']['fetched_at']=='2026-09-20T12:00:00+00:00'
