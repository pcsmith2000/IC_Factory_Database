from pathlib import Path
from pipeline import golden, warehouse
from pipeline.registry import load_yaml

ROOT = Path(__file__).resolve().parent.parent
RULES = load_yaml(ROOT / 'registry/survivorship.yaml')


def assertion(value, field='name', source='adl_employee_feedback', at='2026-09-18T12:00:00.000001Z', row='first'):
    return dict(facility_id='IC-1', field=field, value=value, source_id=source,
                source_class='human_feedback' if source=='adl_employee_feedback' else 'A',
                retrieved_date=at[:10], asserted_at=at, row_hash=row, basis='human_verified', site_visit=False)


def test_feedback_ranks_first_for_every_golden_field():
    for field in warehouse.GOLDEN_FIELDS:
        a = assertion('human', field=field)
        other = assertion('automated', field=field, source='operator', at='2027-01-01T00:00:00Z')
        rows, conflicts = golden.build_golden([other,a], RULES)
        assert rows[0][field] == 'human'
        assert conflicts[0]['winner_source'] == 'adl_employee_feedback'


def test_latest_human_assertion_wins_with_history_preserved():
    rows, conflicts = golden.build_golden([assertion('old'), assertion('new', at='2026-09-18T12:00:00.000002Z', row='second')], RULES)
    assert rows[0]['name'] == 'new'
    assert rows[0]['n_assertions'] == 2
    assert conflicts[0]['n_values'] == 2


def load(wh, tag, assertions, facilities):
    rows, conflicts = golden.build_golden(assertions, RULES)
    return wh.load_release(dict(release=dict(tag=tag), started='2026-09-19T00:00:00Z'),
        assertions=assertions,golden=rows,conflicts=conflicts,facilities=facilities,rows=[],
        registry={},registry_text='',rules=RULES,control_rows=[],control_sha=None,
        known_gaps={},survivorship_hash='test')


def seed_feedback(wh, fid='IC-1', created=0):
    with wh.transaction() as c:
        c.execute("INSERT INTO employee_feedback VALUES(?,?,?,?,?,?,?,?,?,?)",('feedback',fid,'Peter','shared_passcode','chat','Original note','[]','2026-09-18T12:00:00Z','hash',created))
        c.execute("INSERT INTO ref_source_row(row_hash,source_key,source_document,facility_key) VALUES(?,?,?,?)",('feedback','adl_employee_feedback','Original note',fid))
        c.execute("INSERT INTO fact_assertions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",('assertion','old-release',fid,'adl_employee_feedback','name','2026-09-18','Employee name','human_verified',0,'feedback',None,'human_feedback','2026-09-18T12:00:00Z'))


def test_release_carries_feedback_once_with_original_lineage(tmp_path):
    wh = warehouse.SqliteWarehouse(tmp_path/'test.sqlite')
    seed_feedback(wh)
    for tag in ['release-1','release-1','release-2']:
        load(wh,tag,[assertion('Registry name',source='registry')],[dict(facility_id='IC-1')])
        row=wh.query('SELECT * FROM golden_facility')[0]
        assert row['name']=='Employee name' and row['n_assertions']==2
        history=wh.query("SELECT * FROM fact_assertions WHERE release_tag=? AND source_key=?",(tag,'adl_employee_feedback'))
        assert len(history)==1 and history[0]['assertion_id']=='assertion'
        assert wh.provenance('IC-1','name')[0]['source_document']=='Original note'
    wh.close()


def test_dropped_external_facility_not_resurrected_but_employee_created_survives(tmp_path):
    wh=warehouse.SqliteWarehouse(tmp_path/'test.sqlite')
    seed_feedback(wh)
    load(wh,'release-1',[],[])
    assert not wh.query('SELECT * FROM golden_facility')
    with wh.transaction() as c:c.execute('UPDATE employee_feedback SET creates_facility=1')
    load(wh,'release-2',[],[])
    assert wh.query('SELECT name FROM golden_facility')==[{'name':'Employee name'}]
    assert wh.query('SELECT facility_key FROM dim_facility')==[{'facility_key':'IC-1'}]
    wh.close()
