"""Select a reproducible golden-table cohort using a database-enforced read-only session."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path

FIELDS = ('name', 'address', 'city', 'state', 'zip', 'website', 'phone', 'email')

def split(value):
    return [x.strip() for x in value.split(',') if x.strip()]

def settings():
    out = {k: os.environ.get(k.upper(), '').strip() for k in
           ('states', 'facility_ids', 'missing_fields', 'source_ids', 'tiers', 'release_tag')}
    out.update(limit=int(os.environ.get('ROW_LIMIT', '10')), offset=int(os.environ.get('ROW_OFFSET', '0')),
               seed=int(os.environ.get('SAMPLE_SEED', '20260920')))
    if not 1 <= out['limit'] <= 200 or out['offset'] < 0:
        raise ValueError('row_limit must be 1..200; offset must be nonnegative')
    if set(split(out['missing_fields'])) - set(FIELDS):
        raise ValueError('unsupported missing field')
    if set(split(out['tiers'])) - {'T0', 'T1', 'T2', 'T3'}:
        raise ValueError('tiers must be T0,T1,T2,T3')
    if any(len(s) != 2 or not s.isalpha() for s in split(out['states'])):
        raise ValueError('states must be comma-separated two-letter codes')
    return out

def query(cfg):
    clauses, params = [], []
    for key, column in [('states', 'g.state'), ('facility_ids', 'g.facility_key'),
                        ('tiers', 'd.tier')]:
        values = split(cfg[key])
        if values:
            clauses.append(f'{column} = ANY(%s)')
            params.append([v.upper() for v in values] if key == 'states' else values)
    if cfg['release_tag']:
        clauses.append('g.release_tag = %s'); params.append(cfg['release_tag'])
    missing = split(cfg['missing_fields'])
    if missing:
        clauses.append('(' + ' OR '.join(f"NULLIF(TRIM(g.{f}), '') IS NULL" for f in missing) + ')')
    sources = split(cfg['source_ids'])
    if sources:
        clauses.append('EXISTS (SELECT 1 FROM fact_assertions a WHERE a.facility_key=g.facility_key '
                       'AND a.release_tag=g.release_tag AND a.source_key=ANY(%s))')
        params.append(sources)
    where = ' AND '.join(clauses) or 'TRUE'
    columns = ', '.join('g.' + f for f in FIELDS)
    sql = f'''SELECT g.facility_key AS facility_id, g.release_tag, d.tier, {columns},
        COUNT(*) OVER() AS eligible_count
        FROM golden_facility g LEFT JOIN dim_facility d ON d.facility_key=g.facility_key
        WHERE {where} ORDER BY md5(g.facility_key || %s), g.facility_key LIMIT %s OFFSET %s'''
    params.extend([str(cfg['seed']), cfg['limit'], cfg['offset']])
    return sql, params

def snapshot(out: Path):
    import psycopg
    from psycopg.rows import dict_row
    cfg = settings()
    urls = [os.environ.get(k, '').strip() for k in ('DATABASE_URL_UNPOOLED', 'DATABASE_URL')]
    url = next((u for u in urls if u and u.isascii()), '')
    if not url:
        raise RuntimeError('No usable database URL; refusing to select a different database')
    sql, params = query(cfg)
    with psycopg.connect(url, connect_timeout=20, row_factory=dict_row,
                         options='-c default_transaction_read_only=on -c statement_timeout=30000') as db:
        db.read_only = True
        readonly = db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']
        if readonly != 'on':
            raise RuntimeError('Database read-only protection was not enabled')
        rows = db.execute(sql, params).fetchall()
    if not rows:
        raise RuntimeError('Selection matched zero rows; change the filters')
    eligible = rows[0].get('eligible_count')
    for row in rows:
        row.pop('eligible_count', None)
    out.mkdir(parents=True, exist_ok=True)
    data = json.dumps(rows, indent=2, default=str)
    (out / 'input.json').write_text(data)
    manifest = dict(selection=cfg, eligible_count=eligible, selected=len(rows),
                    input_sha256=hashlib.sha256(data.encode()).hexdigest(),
                    database_read_only=True, database_writes=0)
    (out / 'selection.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest))
    for r in rows:
        print(f"{r['facility_id']} | {r['name']} | {r['city']}, {r['state']} | website={bool(r['website'])} phone={bool(r['phone'])}")
    return rows

if __name__ == '__main__':
    snapshot(Path(os.environ.get('RESEARCH_OUT', 'research-output')))
