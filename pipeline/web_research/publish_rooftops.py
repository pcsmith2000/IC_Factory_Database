"""Publish cached, reviewed rooftops with scoped promotion using the normal survivorship rules."""
import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from pipeline.enrich._db import assertion, _rows_for
from pipeline.enrich.promote import build
from pipeline.registry import load_yaml
from pipeline.web_research.campaign import normalized_detail
from pipeline.web_research.publish import connection, digest, load_manifest, SOURCE
from pipeline.web_research.rooftops import run as replay_cache

ROOT = Path('research/adl-2026-09-20')
GEO = 'geocode:geocodio'


def coord_equal(a, b):
    try:
        x, y = [float(v) for v in a.split(',')], [float(v) for v in b.split(',')]
        return len(x) == len(y) == 2 and all(abs(i-j) < 1e-7 for i,j in zip(x,y))
    except (TypeError, ValueError, AttributeError):
        return False


def prepare(golden, facts, rooftops, identities, approved, rules):
    current = {r['facility_key']: r for r in golden}
    baseline = {**identities}
    for r in approved['assertions']:
        baseline.setdefault(r['facility_id'],r['expected_identity'])
    valid_ids = {fid for fid, identity in baseline.items() if fid in current and
                 all(normalized_detail(k,current[fid].get(k)) == normalized_detail(k,v) for k,v in identity.items())}
    winners = {r['facility_id']:r for r in build(facts,rules)[0]}
    updates = defaultdict(dict)
    # Only previously authorized Tako cells can be promoted. Unrelated fields are untouched.
    for r in approved['assertions']:
        fid, f = r['facility_id'], r['field']
        if fid not in valid_ids: continue
        win, old = winners.get(fid,{}), current[fid].get(f)
        if win.get(f+'__source') != SOURCE or normalized_detail(f,win.get(f)) != normalized_detail(f,r['value']):continue
        if old and normalized_detail(f,old) != normalized_detail(f,r['value']):continue
        if not old:
            updates[fid][f] = win[f]
            updates[fid][f+'__source'] = SOURCE
    pending, held, already = [], [], []
    for r in rooftops:
        fid = r['facility_id']; reason = None
        effective = {**current.get(fid,{}), **updates.get(fid,{})}
        if fid not in valid_ids:reason = 'Current facility identity does not match reviewed snapshot'
        elif r.get('requires_database_resolution'):reason = 'Address correction requires separate resolution'
        elif r.get('decision') != 'rooftop_candidate' or r.get('accuracy_type') != 'rooftop' or not r.get('address_matches'):
            reason = 'Geocoder did not return a matching rooftop'
        elif any(normalized_detail(f,effective.get(f)) != normalized_detail(f,r.get(f)) for f in ('address','city','state')):
            reason = 'Current winning address differs from geocoded address'
        elif effective.get('zip') and normalized_detail('zip',effective['zip']) != normalized_detail('zip',r.get('zip')):
            reason = 'Current postal code differs from geocoded address'
        if reason:
            held.append(dict(facility_id=fid,reason=reason));continue
        loc = r.get('coordinates') or {}; lat, lng = loc.get('lat'), loc.get('lng')
        if not isinstance(lat,(int,float)) or not isinstance(lng,(int,float)) or not math.isfinite(lat) or not math.isfinite(lng) or not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError('Invalid cached coordinate')
        value = f'{lat},{lng}'
        existing_roofs = [a for a in facts if a['facility_id']==fid and a['field']=='lat_lon' and a.get('basis')=='rooftop']
        win = winners.get(fid,{})
        if win.get('lat_lon__source') in ('operator','adl_employee_feedback') or effective.get('lat_lon__source') in ('operator','adl_employee_feedback'):
            held.append(dict(facility_id=fid,reason='Human coordinate takes precedence'));continue
        if existing_roofs:
            if any(not coord_equal(a['value'],value) for a in existing_roofs):
                held.append(dict(facility_id=fid,reason='Existing rooftop differs; retain for review'));continue
            already.append(fid)
            if coord_equal(win.get('lat_lon'),value) and (not coord_equal(effective.get('lat_lon'),value) or effective.get('lat_lon__source') != win.get('lat_lon__source')):
                updates[fid].update(lat_lon=win['lat_lon'],lat_lon__source=win['lat_lon__source'])
            continue
        if effective.get('lat_lon') and not win.get('lat_lon'):
            held.append(dict(facility_id=fid,reason='Existing coordinate has no applicable assertion; preserve it'));continue
        evidence = r['evidence_url'] + ' :: ' + json.dumps({k:r.get(k) for k in
                    ('query','returned_address','dataset','accuracy_type','accuracy','review_note')},sort_keys=True)
        evidence += '; cached Geocodio run 35545590293'
        a = assertion(fid,'lat_lon',value,source_id=GEO,basis='rooftop',confidence=r.get('accuracy'),evidence=evidence)
        a['retrieved_date'] = '2026-09-20'
        a['release_tag'] = current[fid]['release_tag']
        projected = build([f for f in facts if f['facility_id']==fid]+[a],rules)[0][0]
        if projected.get('lat_lon__source') != GEO or not coord_equal(projected.get('lat_lon'),value):
            held.append(dict(facility_id=fid,reason='Stronger coordinate assertion wins survivorship'));continue
        pending.append(a)
        if not coord_equal(effective.get('lat_lon'),value) or effective.get('lat_lon__source') != GEO:
            updates[fid].update(lat_lon=value,lat_lon__source=GEO)
    return dict(assertions=pending,updates=dict(updates),held=held,already_asserted=already,
                golden_sha256=digest(golden),facts_sha256=digest(facts),
                inputs_sha256=digest([rooftops,identities,approved,rules]),new_api_calls=0)


def read_snapshot(db, ids):
    rows = db.execute('SELECT * FROM golden_facility WHERE facility_key=ANY(%s) ORDER BY facility_key',(ids,)).fetchall()
    facts = db.execute("SELECT DISTINCT ON (a.assertion_id) a.assertion_id, a.facility_key AS facility_id, "
                       "a.source_key AS source_id,COALESCE(a.source_class,'') AS source_class, "
                       "COALESCE(a.date_key,'') AS retrieved_date,COALESCE(a.asserted_at,'') AS asserted_at, "
                       "COALESCE(a.row_hash,'') AS row_hash,COALESCE(a.basis,'none') AS basis, "
                       "a.site_visit,a.field_key AS field,a.value FROM fact_assertions a "
                       "JOIN golden_facility g ON g.facility_key=a.facility_key "
                       "WHERE a.facility_key=ANY(%s) AND (a.release_tag=g.release_tag OR "
                       "a.source_class IN ('enrichment','tako_ai_search') OR a.source_key='adl_employee_feedback') "
                       "ORDER BY a.assertion_id,(a.release_tag=g.release_tag) DESC,a.release_tag DESC",(ids,)).fetchall()
    return rows, facts


def run(mode, cache_root, out, prior_path=None, plan_sha=None):
    approved = load_manifest()
    reviewed = json.loads((ROOT/'reviewed-addresses.json').read_text())
    identities = json.loads((ROOT/'rooftop-identities.json').read_text())
    # No API key is provided: incomplete cache fails closed, rather than spending on new lookups.
    replay_cache(reviewed,out/'revalidated-cache',cache_root)
    rooftops = json.loads((out/'revalidated-cache/rooftop-results.json').read_text())
    estimate = json.loads((out/'revalidated-cache/geocode-estimate.json').read_text())
    if estimate['new_lookups']:raise ValueError('Only complete cached results may be published')
    ids = sorted(set(identities) | {r['facility_id'] for r in approved['assertions']})
    rules = load_yaml(Path('registry/survivorship.yaml'))
    prior = None
    if mode == 'apply':
        if not prior_path or not plan_sha:raise ValueError('An exact saved preview is required')
        prior = json.loads(Path(prior_path).read_text())
        if digest(prior) != plan_sha:raise ValueError('Preview hash mismatch')
    with connection() as db:
        if mode == 'plan':db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        else:
            db.execute('SELECT pg_advisory_xact_lock(7419026)')
            db.execute('LOCK TABLE fact_assertions IN SHARE ROW EXCLUSIVE MODE')
            db.execute('LOCK TABLE golden_facility IN SHARE ROW EXCLUSIVE MODE')
        before, facts = read_snapshot(db,ids)
        plan = prepare(before,facts,rooftops,identities,approved,rules)
        if mode == 'plan':
            (out/'plan.json').write_text(json.dumps(plan,indent=2))
            receipt = dict(mode=mode,plan_sha256=digest(plan),coordinate_assertions=len(plan['assertions']),
                           golden_rows_to_update=len(plan['updates']),
                           golden_cells_by_field=dict(Counter(k for v in plan['updates'].values() for k in v if not k.endswith('__source'))),
                           held=len(plan['held']),already_asserted=len(plan['already_asserted']),database_writes=0,new_api_calls=0)
        else:
            if plan != prior:raise ValueError('Live data changed since preview; rerun plan')
            now = datetime.now(timezone.utc).isoformat(timespec='seconds'); inserted = 0; new_ids = []
            db.execute("INSERT INTO dim_source(source_key,source_id,name,class,status) VALUES (%s,%s,%s,'enrichment','active') "
                       "ON CONFLICT(source_key) DO UPDATE SET name=EXCLUDED.name,class=EXCLUDED.class",(GEO,GEO,'Enrichment 10 — Geocodio rooftop geocode'))
            for a in plan['assertions']:
                ev, fact = _rows_for(a,a['release_tag'],a['retrieved_date'],now)
                db.execute('INSERT INTO ref_source_row(row_hash,source_key,source_url,source_document,retrieved_date,facility_key,'
                           'match_method,match_confidence,last_seen_release) VALUES ('+','.join(['%s']*9)+') ON CONFLICT(row_hash) DO NOTHING',ev)
                cur = db.execute('INSERT INTO fact_assertions(assertion_id,release_tag,facility_key,source_key,field_key,date_key,value,basis,'
                                 'site_visit,row_hash,confidence,source_class,asserted_at) VALUES ('+','.join(['%s']*13)+') '
                                 'ON CONFLICT(assertion_id,release_tag) DO NOTHING RETURNING assertion_id',fact)
                inserted += len(cur.fetchall());new_ids.append(fact[0])
            allowed = {'name','address','city','state','zip','website','phone','email','lat_lon'}
            old = {r['facility_key']:r for r in before}
            for fid, changes in plan['updates'].items():
                if any(k.removesuffix('__source') not in allowed for k in changes):raise ValueError('Unexpected promotion column')
                cols = ','.join('"'+k+'"=%s' for k in changes)
                db.execute('UPDATE golden_facility SET '+cols+' WHERE facility_key=%s AND release_tag=%s',
                           tuple(changes.values())+(fid,old[fid]['release_tag']))
                db.execute('UPDATE golden_facility SET n_assertions=(SELECT count(*) FROM fact_assertions WHERE facility_key=%s AND release_tag=%s),'
                           'n_sources=(SELECT count(DISTINCT source_key) FROM fact_assertions WHERE facility_key=%s AND release_tag=%s) WHERE facility_key=%s',
                           (fid,old[fid]['release_tag'],fid,old[fid]['release_tag'],fid))
            after,_ = read_snapshot(db,ids)
            for row in after:
                expected = {**old[row['facility_key']],**plan['updates'].get(row['facility_key'],{})}
                if any(row[k]!=v for k,v in expected.items() if k not in ('n_assertions','n_sources')):
                    raise RuntimeError('Golden read-back differs from exact preview; rollback')
            verified = db.execute('SELECT a.assertion_id,a.facility_key,a.field_key,a.value,a.basis,a.source_key,r.source_url '
                                  'FROM fact_assertions a JOIN ref_source_row r ON r.row_hash=a.row_hash '
                                  'WHERE a.assertion_id=ANY(%s)',(new_ids,)).fetchall()
            if set(new_ids)!={r['assertion_id'] for r in verified} or any(r['source_key']!=GEO or r['basis']!='rooftop' or not r['source_url'] for r in verified):
                raise RuntimeError('Coordinate assertion read-back failed; rollback')
            (out/'verified-assertions.json').write_text(json.dumps(verified,indent=2))
            (out/'golden-after.json').write_text(json.dumps(after,indent=2))
            receipt = dict(mode=mode,inserted=inserted,verified_coordinate_assertions=len(set(new_ids)),
                           golden_rows_updated=len(plan['updates']),
                           golden_cells_by_field=dict(Counter(k for v in plan['updates'].values() for k in v if not k.endswith('__source'))),
                           coordinate_coverage_before=sum(bool(r.get('lat_lon')) for r in before),
                           coordinate_coverage_after=sum(bool(r.get('lat_lon')) for r in after),
                           held=len(plan['held']),already_asserted=len(plan['already_asserted']),new_api_calls=0,plan_sha256=plan_sha)
    (out/'receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['plan','apply'],default='plan');p.add_argument('--cache',required=True)
    p.add_argument('--out',default='rooftop-publish-output');p.add_argument('--plan');p.add_argument('--plan-sha256');a=p.parse_args()
    run(a.mode,Path(a.cache),Path(a.out),a.plan,a.plan_sha256)
