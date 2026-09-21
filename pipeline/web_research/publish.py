"""Import an explicitly reviewed manifest as low-priority assertions, never golden values."""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from pipeline.enrich._db import assertion, _rows_for
from pipeline.web_research.select import FIELDS
from pipeline.web_research.campaign import normalized_detail

SOURCE = 'tako_ai_search'
MANIFEST = Path('research/adl-2026-09-20/approved-assertions.json')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def load_manifest(path=MANIFEST):
    manifest = json.loads(path.read_text())
    if manifest.get('source_id') != SOURCE or not manifest.get('approval'):
        raise ValueError('Expected reviewed Tako approval manifest')
    seen = set()
    for r in manifest['assertions']:
        key = (r['facility_id'], r['field'])
        if key in seen or not r.get('approved') or r['field'] not in FIELDS:
            raise ValueError('Duplicate, unapproved, or unsupported assertion')
        seen.add(key)
        if not r.get('value', '').strip() or not r.get('review_note'):
            raise ValueError('Missing value or review record')
        url = urlsplit(r['source_url'])
        if url.scheme not in ('http', 'https') or not url.hostname:
            raise ValueError('Missing evidence URL')
        if r['field'] in ('address', 'city', 'state', 'zip') and r.get('scope') != 'facility':
            raise ValueError('Location must refer to a reviewed facility')
    return manifest


def to_assertion(r, manifest):
    document = json.dumps({k: r.get(k) for k in ('quote', 'scope', 'review_note', 'source_run', 'evidence_rounds')}, sort_keys=True)
    document += '\nCampaign runs: ' + ','.join(map(str, manifest['campaign_runs']))
    a = assertion(r['facility_id'], r['field'], r['value'], source_id=SOURCE,
                  basis='tako_ai_search_reviewed', evidence=r['source_url'] + ' :: ' + document)
    # A separate class ensures existing enrichment rules cannot accidentally elevate this source.
    a['source_class'] = SOURCE
    a['retrieved_date'] = r['retrieved_date']
    return a


def select_import(manifest, golden, existing):
    current = {r['facility_key']: r for r in golden}
    by_cell = {}
    for r in existing:
        by_cell.setdefault((r['facility_key'], r['field_key']), []).append(r)
    accepted, skipped = [], []
    for r in manifest['assertions']:
        fid, field = r['facility_id'], r['field']
        g = current.get(fid)
        reason = None
        if not g:
            reason = 'Facility no longer in current golden table'
        elif str(g.get(field) or '').strip() and normalized_detail(field, g[field]) != normalized_detail(field, r['value']):
            reason = 'Current golden field contains a different value; hold for review'
        elif any(str(a.get('value') or '').strip() and
                 normalized_detail(field, a['value']) != normalized_detail(field, r['value'])
                 for a in by_cell.get((fid, field), [])):
            reason = 'Another assertion already supplies this field; hold for review'
        if reason:
            skipped.append(dict(facility_id=fid, field=field, proposed=r['value'], reason=reason, current=g.get(field) if g else None, existing=by_cell.get((fid,field), [])))
        else:
            accepted.append(dict(to_assertion(r, manifest), release_tag=g['release_tag']))
    return accepted, skipped


def connection():
    import psycopg
    from psycopg.rows import dict_row
    url = next((os.environ.get(k, '') for k in ('DATABASE_URL_UNPOOLED', 'DATABASE_URL')
                if os.environ.get(k, '') and os.environ[k].isascii()), '')
    if not url:
        raise RuntimeError('No usable database connection configured')
    return psycopg.connect(url, connect_timeout=20, row_factory=dict_row,
                          options='-c statement_timeout=60000 -c lock_timeout=15000')


def run(mode, out, plan_path=None, expected_sha=None):
    manifest = load_manifest()
    ids = sorted({r['facility_id'] for r in manifest['assertions']})
    prior = None
    if mode == 'apply':
        if not plan_path or not expected_sha:
            raise ValueError('Apply requires a saved plan and its exact SHA256')
        prior = json.loads(Path(plan_path).read_text())
        if digest(prior) != expected_sha or prior['manifest_sha256'] != digest(manifest):
            raise ValueError('Plan or approval manifest changed')
    with connection() as db:
        if mode == 'plan':
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        else:
            # Hold competing inserts/promotion while validating and writing the reviewed set.
            db.execute('LOCK TABLE fact_assertions IN SHARE ROW EXCLUSIVE MODE')
            db.execute('LOCK TABLE golden_facility IN SHARE MODE')
        golden = db.execute('SELECT facility_key, release_tag, ' + ','.join(FIELDS) +
                            ' FROM golden_facility WHERE facility_key = ANY(%s) ORDER BY facility_key', (ids,)).fetchall()
        existing = db.execute('SELECT facility_key, field_key, source_key, value FROM fact_assertions '
                              'WHERE facility_key = ANY(%s)', (ids,)).fetchall()
        accepted, skipped = select_import(manifest, golden, existing)
        plan = dict(manifest_sha256=digest(manifest), golden_sha256=digest(golden),
                    assertions=accepted, skipped=skipped, source=SOURCE,
                    golden_writes=0)
        out.mkdir(parents=True, exist_ok=True)
        if mode == 'plan':
            (out/'plan.json').write_text(json.dumps(plan, indent=2))
            result = dict(mode=mode, proposed=len(accepted), skipped=len(skipped),
                          facilities=len({a['facility_id'] for a in accepted}),
                          plan_sha256=digest(plan), database_writes=0, golden_writes=0)
        else:
            if plan != prior:
                raise ValueError('Live eligibility changed since preview; create a fresh plan')
            if not accepted:
                raise ValueError('No approved eligible assertions')
            now = datetime.now(timezone.utc).isoformat(timespec='seconds')
            db.execute("INSERT INTO dim_source (source_key,source_id,name,class,method,status_basis,status) "
                       "VALUES (%s,%s,%s,%s,%s,%s,'active') ON CONFLICT (source_key) DO UPDATE SET "
                       "name=EXCLUDED.name,class=EXCLUDED.class,method=EXCLUDED.method,status_basis=EXCLUDED.status_basis",
                       (SOURCE, SOURCE, 'Tako AI Search', SOURCE, 'Reviewed web-search evidence', 'Lowest precedence; approved missing-field assertions'))
            inserted = 0
            assertion_ids = []
            for a in accepted:
                evidence, fact = _rows_for(a, a['release_tag'], a['retrieved_date'], now)
                db.execute('INSERT INTO ref_source_row (row_hash,source_key,source_url,source_document,retrieved_date,'
                           'facility_key,match_method,match_confidence,last_seen_release) VALUES (' + ','.join(['%s']*9) +
                           ') ON CONFLICT (row_hash) DO NOTHING', evidence)
                cursor = db.execute('INSERT INTO fact_assertions (assertion_id,release_tag,facility_key,source_key,field_key,'
                                    'date_key,value,basis,site_visit,row_hash,confidence,source_class,asserted_at) VALUES (' +
                                    ','.join(['%s']*13) + ') ON CONFLICT (assertion_id,release_tag) DO NOTHING RETURNING assertion_id', fact)
                inserted += len(cursor.fetchall())
                assertion_ids.append(fact[0])
            verified = db.execute('SELECT a.assertion_id,a.release_tag,a.facility_key,a.field_key,a.value,a.source_key,'
                                  'a.source_class,r.source_url,r.source_document FROM fact_assertions a JOIN ref_source_row r '
                                  'ON r.row_hash=a.row_hash WHERE a.assertion_id=ANY(%s)', (assertion_ids,)).fetchall()
            expected = {(a['facility_id'], a['field'], a['value'], a['release_tag']) for a in accepted}
            found = {(r['facility_key'],r['field_key'],r['value'],r['release_tag']) for r in verified}
            if not expected <= found or any(r['source_key'] != SOURCE or r['source_class'] != SOURCE or not r['source_url'] for r in verified):
                raise RuntimeError('Assertion read-back failed; rolling back')
            after = db.execute('SELECT facility_key, release_tag, ' + ','.join(FIELDS) +
                               ' FROM golden_facility WHERE facility_key=ANY(%s) ORDER BY facility_key', (ids,)).fetchall()
            if digest(after) != digest(golden):
                raise RuntimeError('Golden values changed; rolling back')
            result = dict(mode=mode, inserted=inserted, already_present=len(accepted)-inserted,
                          verified=len(expected), facilities=len({a['facility_id'] for a in accepted}),
                          skipped=len(skipped), plan_sha256=expected_sha, golden_writes=0,
                          source=SOURCE, source_name='Tako AI Search')
            (out/'verified-assertions.json').write_text(json.dumps(verified, indent=2))
    # Only produce a success receipt after the transaction commits.
    (out/'receipt.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=['plan','apply'], default='plan')
    p.add_argument('--out', default='assertion-output')
    p.add_argument('--plan')
    p.add_argument('--plan-sha256')
    a = p.parse_args()
    run(a.mode, Path(a.out), a.plan, a.plan_sha256)
