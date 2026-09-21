import copy
from pathlib import Path
from pipeline.registry import load_yaml
from pipeline.web_research.publish_rooftops import prepare, GEO, SOURCE


def inputs():
    golden=[dict(facility_key='IC-1',release_tag='current',name='Plant',address=None,city='Boston',state='MA',zip='02101',lat_lon=None)]
    fact=dict(facility_id='IC-1',source_id=SOURCE,source_class=SOURCE,retrieved_date='2026-09-20',basis='none',field='address',value='10 Main St')
    rooftop=dict(facility_id='IC-1',address='10 Main Street',city='Boston',state='MA',zip='02101',decision='rooftop_candidate',
                 accuracy_type='rooftop',address_matches=True,coordinates={'lat':42.1,'lng':-71.1},accuracy=1,
                 evidence_url='https://example.com/plant',query='10 Main Street, Boston MA 02101',review_note='Plant verified')
    ids={'IC-1':{'name':'Plant','city':'Boston','state':'MA'}}
    approved={'assertions':[dict(facility_id='IC-1',field='address',value='10 Main St',expected_identity=ids['IC-1'])]}
    return [golden,[fact],[rooftop],ids,approved,load_yaml(Path('registry/survivorship.yaml'))]


def test_missing_address_and_cached_rooftop_are_promoted_together():
    plan=prepare(*inputs())
    assert len(plan['assertions'])==1
    assert plan['assertions'][0]['source_id']==GEO
    assert plan['assertions'][0]['basis']=='rooftop'
    assert plan['updates']['IC-1']=={'address':'10 Main St','address__source':SOURCE,'lat_lon':'42.1,-71.1','lat_lon__source':GEO}
    assert plan['new_api_calls']==0 and not plan['held']


def test_wrong_address_unresolved_geocode_and_identity_are_held():
    for key,value in [('address','99 Other Rd'),('city','Cambridge'),('zip','99999'),('accuracy_type','range_interpolation'),('requires_database_resolution',True)]:
        args=inputs();args[2][0][key]=value;plan=prepare(*args)
        assert not plan['assertions'] and len(plan['held'])==1
        assert 'lat_lon' not in plan['updates'].get('IC-1',{})
    args=inputs();args[0][0]['name']='Different Plant';plan=prepare(*args)
    assert not plan['updates'] and not plan['assertions']


def test_human_coordinate_preserved_even_if_search_rooftop_is_newer():
    args=inputs();args[1].append({**args[1][0],'source_id':'operator','source_class':'operator','field':'lat_lon','value':'40,-70','retrieved_date':'2000-01-01'})
    plan=prepare(*args)
    assert not plan['assertions'] and 'lat_lon' not in plan['updates']['IC-1']
    assert 'Human' in plan['held'][0]['reason']


def test_conflicting_rooftop_held_but_identical_existing_rooftop_can_promote():
    args=inputs();args[1].append({**args[1][0],'source_id':GEO,'source_class':'enrichment','basis':'rooftop','field':'lat_lon','value':'40,-70'})
    plan=prepare(*args);assert not plan['assertions'] and 'Existing rooftop differs' in plan['held'][0]['reason']
    args[1][-1]['value']='42.1,-71.1';args[0][0]['lat_lon']='39,-69';args[0][0]['lat_lon__source']='epa_frs'
    plan=prepare(*args);assert not plan['assertions'] and plan['already_asserted']==['IC-1']
    assert plan['updates']['IC-1']['lat_lon']=='42.1,-71.1'


def test_existing_epa_coordinate_can_be_upgraded_using_normal_rules():
    args=inputs();args[1].append({**args[1][0],'source_id':'epa_frs','source_class':'B','field':'lat_lon','value':'40,-70'})
    args[0][0].update(lat_lon='40,-70',lat_lon__source='epa_frs')
    plan=prepare(*args)
    assert len(plan['assertions'])==1 and plan['updates']['IC-1']['lat_lon']=='42.1,-71.1'


def test_stronger_address_and_unrelated_fields_are_never_overwritten():
    args=inputs();args[1].append({**args[1][0],'source_id':'operator','source_class':'operator','value':'55 Factory Rd'})
    args[1].append({**args[1][0],'field':'phone','value':'5551212'})
    plan=prepare(*args)
    assert not plan['assertions'] and not plan['updates']


def test_invalid_coordinate_rejected():
    import pytest
    args=inputs();args[2][0]['coordinates']['lat']=float('nan')
    with pytest.raises(ValueError):prepare(*args)


def test_atomic_publication_in_postgres(monkeypatch,tmp_path):
    import os,json,uuid
    import pytest
    if not os.environ.get('TEST_DATABASE_URL'):pytest.skip('Postgres integration runs in CI')
    import psycopg
    from psycopg.rows import dict_row
    from pipeline.warehouse import DDL
    from pipeline.web_research import publish_rooftops as pub
    schema='rooftop_test_'+uuid.uuid4().hex
    uri=os.environ['TEST_DATABASE_URL']
    with psycopg.connect(uri,autocommit=True) as admin:admin.execute('CREATE SCHEMA '+schema)
    def connect():return psycopg.connect(uri,row_factory=dict_row,options='-c search_path='+schema)
    args=inputs();root=tmp_path/'inputs';root.mkdir()
    (root/'reviewed-addresses.json').write_text('[]')
    (root/'rooftop-identities.json').write_text(json.dumps(args[3]))
    def replay(rows,out,prior):
        out.mkdir(parents=True,exist_ok=True)
        (out/'rooftop-results.json').write_text(json.dumps(args[2]))
        (out/'geocode-estimate.json').write_text('{"new_lookups":0}')
    monkeypatch.setattr(pub,'ROOT',root);monkeypatch.setattr(pub,'connection',connect)
    monkeypatch.setattr(pub,'load_manifest',lambda:args[4]);monkeypatch.setattr(pub,'replay_cache',replay)
    try:
        with connect() as db:
            for table in ('fact_assertions','golden_facility','ref_source_row','dim_source'):
                db.execute(next(sql for sql in DDL if sql.startswith('CREATE TABLE IF NOT EXISTS '+table+' ')))
            db.execute("INSERT INTO golden_facility(facility_key,release_tag,name,city,state,zip) VALUES ('IC-1','current','Plant','Boston','MA','02101'),('IC-2','current','Unrelated','Dallas','TX','75001')")
            db.execute("INSERT INTO fact_assertions(assertion_id,release_tag,facility_key,source_key,source_class,field_key,value,date_key) VALUES ('address','current','IC-1',%s,%s,'address','10 Main St','2026-09-20')",(SOURCE,SOURCE))
        preview=tmp_path/'preview';pub.run('plan',tmp_path,preview)
        saved=preview/'plan.json';sha=json.loads((preview/'receipt.json').read_text())['plan_sha256']
        # A changed golden row must invalidate the exact preview without writing coordinates.
        with connect() as db:db.execute("UPDATE golden_facility SET phone='555' WHERE facility_key='IC-1'")
        with pytest.raises(ValueError,match='Live data changed'):pub.run('apply',tmp_path,tmp_path/'stale',saved,sha)
        with connect() as db:db.execute("UPDATE golden_facility SET phone=NULL WHERE facility_key='IC-1'")
        # Simulate a trigger that changes an unapproved field: the complete transaction rolls back.
        with connect() as db:
            db.execute("CREATE FUNCTION tamper() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.name:='tampered'; RETURN NEW; END; $$")
            db.execute('CREATE TRIGGER tamper BEFORE UPDATE ON golden_facility FOR EACH ROW EXECUTE FUNCTION tamper()')
        with pytest.raises(RuntimeError,match='Golden read-back'):pub.run('apply',tmp_path,tmp_path/'rollback',saved,sha)
        with connect() as db:
            assert db.execute("SELECT count(*) AS n FROM fact_assertions WHERE source_key=%s",(GEO,)).fetchone()['n']==0
            assert db.execute("SELECT name FROM golden_facility WHERE facility_key='IC-1'").fetchone()['name']=='Plant'
            db.execute('DROP TRIGGER tamper ON golden_facility')
        dest=tmp_path/'applied';pub.run('apply',tmp_path,dest,saved,sha)
        receipt=json.loads((dest/'receipt.json').read_text());assert receipt['inserted']==1 and receipt['coordinate_coverage_after']==1
        with connect() as db:
            row=db.execute("SELECT * FROM golden_facility WHERE facility_key='IC-1'").fetchone()
            assert row['lat_lon']=='42.1,-71.1' and row['address']=='10 Main St'
            other=db.execute("SELECT * FROM golden_facility WHERE facility_key='IC-2'").fetchone()
            assert other['name']=='Unrelated' and other['lat_lon'] is None
        preview2=tmp_path/'preview2';pub.run('plan',tmp_path,preview2)
        sha2=json.loads((preview2/'receipt.json').read_text())['plan_sha256']
        pub.run('apply',tmp_path,tmp_path/'repeat',preview2/'plan.json',sha2)
        repeat=json.loads((tmp_path/'repeat/receipt.json').read_text());assert repeat['inserted']==0 and repeat['golden_rows_updated']==0
    finally:
        with psycopg.connect(uri,autocommit=True) as admin:admin.execute('DROP SCHEMA '+schema+' CASCADE')
