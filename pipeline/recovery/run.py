"""Campaign state and cell-level evidence remain in the database; only totals are exported."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlparse

from pipeline.enrich import cache
from pipeline.enrich.geocode import one_line, _post
from pipeline.enrich._db import assertion, _rows_for
from pipeline.web_research.rooftops import street_matches
from pipeline.web_research.run import norm

CAMPAIGN = 'missing-rooftops-2026-09-21'
LIMIT = Decimal('9.50')  # leave $0.50 below the user's $10 limit
US_STATES=set('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split())
SCHEMA=[
"""CREATE TABLE IF NOT EXISTS coordinate_recovery_campaigns (
 campaign_id TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 api_ceiling NUMERIC NOT NULL, reserved_usd NUMERIC NOT NULL DEFAULT 0,
 frozen_count INTEGER NOT NULL, freeze_run TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS coordinate_recovery_rows (
 campaign_id TEXT NOT NULL REFERENCES coordinate_recovery_campaigns(campaign_id),
 facility_id TEXT NOT NULL, baseline JSONB NOT NULL, evidence JSONB NOT NULL,
 status TEXT NOT NULL DEFAULT 'unresolved', PRIMARY KEY(campaign_id,facility_id))""",
"""CREATE TABLE IF NOT EXISTS coordinate_recovery_attempts (
 campaign_id TEXT NOT NULL, facility_id TEXT NOT NULL, pass_id TEXT NOT NULL, stage TEXT NOT NULL,
 query_key TEXT NOT NULL, request JSONB NOT NULL, result JSONB,
 outcome TEXT NOT NULL, reserved_usd NUMERIC NOT NULL, run_url TEXT NOT NULL,
 started_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ,
 PRIMARY KEY(campaign_id,facility_id,pass_id,stage))""",
"ALTER TABLE coordinate_recovery_rows ADD COLUMN IF NOT EXISTS recovered_address JSONB"
]


def connect():
    # Keep the pure validation functions importable in lightweight development
    # environments. The workflow installs the postgres extra before any database
    # operation; unit tests for matching should not need a database driver.
    import psycopg
    from psycopg.rows import dict_row
    url=next((os.environ[k] for k in ('DATABASE_URL_UNPOOLED','DATABASE_URL') if os.environ.get(k) and os.environ[k].isascii()),'')
    if not url:raise RuntimeError('No usable database connection')
    return psycopg.connect(url,row_factory=dict_row,connect_timeout=20,
                          options='-c statement_timeout=60000 -c lock_timeout=15000')


def run_url():
    return 'https://github.com/'+os.environ.get('GITHUB_REPOSITORY','pcsmith2000/IC_Factory_Database')+'/actions/runs/'+os.environ.get('GITHUB_RUN_ID','local')


def freeze(db):
    from psycopg.types.json import Jsonb
    for sql in SCHEMA:db.execute(sql)
    db.execute(cache.DDL)
    db.execute('SELECT pg_advisory_xact_lock(73941668)')
    campaign=db.execute('SELECT * FROM coordinate_recovery_campaigns WHERE campaign_id=%s',(CAMPAIGN,)).fetchone()
    if campaign:return campaign
    rows=db.execute("SELECT g.facility_key AS facility_id,g.release_tag,g.name,g.address,g.city,g.state,g.zip,g.website,g.phone,g.email,g.name__source,g.address__source FROM golden_facility g WHERE NULLIF(btrim(g.lat_lon),'') IS NULL ORDER BY g.facility_key").fetchall()
    if not rows:raise ValueError('No missing-coordinate cohort')
    db.execute('INSERT INTO coordinate_recovery_campaigns(campaign_id,api_ceiling,frozen_count,freeze_run) VALUES(%s,%s,%s,%s)',(CAMPAIGN,LIMIT,len(rows),run_url()))
    ids=[r['facility_id'] for r in rows]
    evidence=db.execute("SELECT a.facility_key,a.field_key,a.value,a.source_key,r.source_url,r.source_document,a.row_hash FROM fact_assertions a JOIN golden_facility g ON g.facility_key=a.facility_key LEFT JOIN ref_source_row r ON r.row_hash=a.row_hash WHERE a.facility_key=ANY(%s) AND a.release_tag=g.release_tag AND a.field_key=ANY(%s)",(ids,['name','address','city','state','zip','website'])).fetchall()
    grouped={fid:[] for fid in ids}
    for ev in evidence:grouped[ev['facility_key']].append(ev)
    for row in rows:
        db.execute('INSERT INTO coordinate_recovery_rows(campaign_id,facility_id,baseline,evidence) VALUES(%s,%s,%s,%s)',(CAMPAIGN,row['facility_id'],Jsonb(row),Jsonb(grouped[row['facility_id']])))
    return db.execute('SELECT * FROM coordinate_recovery_campaigns WHERE campaign_id=%s',(CAMPAIGN,)).fetchone()


def eligible(row):
    return bool(row.get('city') and str(row.get('state') or '').upper() in US_STATES and
                re.match(r'^\d+[a-zA-Z]?\s',str(row.get('address') or '').strip()))


def validate_rooftop(row,result):
    hits=(result.get('response') or {}).get('results') or []
    if not hits:return None,'no_result'
    hit=hits[0];parts=hit.get('address_components') or {};loc=hit.get('location') or {}
    if hit.get('accuracy_type')!='rooftop':return None,'not_rooftop'
    number=re.match(r'^\d+[a-zA-Z]?',row['address'].strip())
    if not number or norm(parts.get('number'))!=norm(number.group()):return None,'street_number_mismatch'
    if not street_matches(row['address'].strip(),parts):return None,'street_name_mismatch'
    if norm(parts.get('city'))!=norm(row['city']):return None,'city_mismatch'
    if norm(parts.get('state_province') or parts.get('state'))!=norm(row['state']):return None,'state_mismatch'
    if row.get('zip') and re.fullmatch(r'\d{5}(?:-\d{4})?',row['zip'].strip()):
        returned=str(parts.get('postal_code') or parts.get('zip') or '')
        if returned and row['zip'][:5]!=returned[:5]:return None,'zip_mismatch'
    lat,lng=loc.get('lat'),loc.get('lng')
    if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (lat,lng)) or not -90<=lat<=90 or not -180<=lng<=180:return None,'invalid_coordinate'
    return hit,'rooftop_verified'


def select_rows(db, pass_id, row_limit):
    # Retry flags from other workflows are deliberately not a selection criterion.
    rows=db.execute("SELECT r.facility_id,r.baseline,r.recovered_address,r.evidence,g.name AS live_name,g.city AS live_city,g.state AS live_state,g.release_tag AS live_release FROM coordinate_recovery_rows r JOIN golden_facility g ON g.facility_key=r.facility_id WHERE r.campaign_id=%s AND r.status='unresolved' AND NOT EXISTS (SELECT 1 FROM coordinate_recovery_attempts a WHERE a.campaign_id=r.campaign_id AND a.facility_id=r.facility_id AND a.pass_id=%s AND a.stage='geocode') ORDER BY r.facility_id",(CAMPAIGN,pass_id)).fetchall()
    out=[]; reasons=Counter();eligible_sources=Counter();blocked_sources=Counter();address_origin=Counter();completeness=Counter()
    completeness_by_source={};evidence_hosts={}
    for r in rows:
        frozen=r['baseline']
        source=frozen.get('name__source') or 'unattributed'
        if norm(frozen.get('name'))!=norm(r.get('live_name')) or any(frozen.get(k) and norm(frozen.get(k))!=norm(r.get('live_'+k)) for k in ('city','state')):
            reasons['changed_identity']+=1;blocked_sources[source]+=1;continue
        recovered=r.get('recovered_address') or {}
        b={**frozen,**recovered};r['frozen']=frozen;r['baseline']=b
        fields=[]
        if not re.match(r'^\d+[a-zA-Z]?\s',str(b.get('address') or '').strip()):fields.append('street')
        if not str(b.get('city') or '').strip():fields.append('city')
        if str(b.get('state') or '').upper() not in US_STATES:fields.append('state')
        if fields:
            reasons['needs_full_street_city_state']+=1;blocked_sources[source]+=1
            gap='missing_'+'_'.join(fields);completeness[gap]+=1
            completeness_by_source.setdefault(source,Counter())[gap]+=1
            hosts=set()
            for ev in r.get('evidence') or []:
                try:
                    host=urlparse(str(ev.get('source_url') or '')).hostname
                except ValueError:
                    host=None
                if host:hosts.add(host.lower())
            for host in hosts:evidence_hosts.setdefault(source,Counter())[host]+=1
            continue
        eligible_sources[source]+=1
        address_origin['recovered_source_detail' if recovered else 'frozen_golden_row']+=1
        out.append(r)
    diagnostics={'eligible_by_primary_name_source':dict(eligible_sources),
                 'blocked_by_primary_name_source':dict(blocked_sources),
                 'blocked_input_completeness':dict(completeness),
                 'blocked_inputs_by_primary_name_source':{k:dict(v) for k,v in completeness_by_source.items()},
                 'blocked_evidence_hosts_by_primary_name_source':{k:dict(v) for k,v in evidence_hosts.items()},
                 'eligible_address_origin':dict(address_origin)}
    return out[:row_limit],dict(reasons),len(out),diagnostics


def reserve(db,r,pass_id,query,cost):
    from psycopg.types.json import Jsonb
    db.execute('SELECT pg_advisory_xact_lock(73941668)')
    c=db.execute('SELECT * FROM coordinate_recovery_campaigns WHERE campaign_id=%s FOR UPDATE',(CAMPAIGN,)).fetchone()
    if c['reserved_usd']+cost>c['api_ceiling']:raise RuntimeError('Campaign budget guard stopped before API request')
    db.execute("INSERT INTO coordinate_recovery_attempts(campaign_id,facility_id,pass_id,stage,query_key,request,outcome,reserved_usd,run_url) VALUES(%s,%s,%s,'geocode',%s,%s,'reserved',%s,%s)",
               (CAMPAIGN,r['facility_id'],pass_id,cache.geocode_key(query),Jsonb({'address':query}),cost,run_url()))
    db.execute('UPDATE coordinate_recovery_campaigns SET reserved_usd=reserved_usd+%s WHERE campaign_id=%s',(cost,CAMPAIGN))


def append_coordinate(db,r,hit):
    point=hit['location'];b=r['baseline'];workflow=run_url()
    document=json.dumps({'campaign_id':CAMPAIGN,'workflow':workflow,'address':one_line(b),'address_evidence':r['evidence'],
                         'geocodio':hit,'checks':['rooftop accuracy','street number','street name','city','state','postal code when available']},sort_keys=True)
    a=assertion(r['facility_id'],'lat_lon',f"{point['lat']},{point['lng']}",source_id='geocode:geocodio',basis='rooftop',confidence=hit.get('accuracy'),evidence=workflow+' :: '+document)
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    ev,fact=_rows_for(a,r['live_release'],now[:10],now)
    db.execute("INSERT INTO dim_source(source_key,source_id,name,class,status) VALUES('geocode:geocodio','geocode:geocodio','Enrichment 10 — Geocodio rooftop geocode','enrichment','active') ON CONFLICT(source_key) DO NOTHING")
    db.execute('INSERT INTO ref_source_row(row_hash,source_key,source_url,source_document,retrieved_date,facility_key,match_method,match_confidence,last_seen_release) VALUES('+','.join(['%s']*9)+') ON CONFLICT(row_hash) DO NOTHING',ev)
    cur=db.execute('INSERT INTO fact_assertions(assertion_id,release_tag,facility_key,source_key,field_key,date_key,value,basis,site_visit,row_hash,confidence,source_class,asserted_at) VALUES('+','.join(['%s']*13)+') ON CONFLICT(assertion_id,release_tag) DO NOTHING RETURNING assertion_id',fact)
    inserted=len(cur.fetchall())
    saved=db.execute('SELECT a.value,a.basis,r.source_url FROM fact_assertions a JOIN ref_source_row r ON r.row_hash=a.row_hash WHERE a.assertion_id=%s AND a.release_tag=%s',(fact[0],r['live_release'])).fetchone()
    if not saved or saved['value']!=a['value'] or saved['basis']!='rooftop' or saved['source_url']!=workflow:raise RuntimeError('Coordinate provenance verification failed')
    db.execute("UPDATE coordinate_recovery_rows SET status='rooftop_asserted' WHERE campaign_id=%s AND facility_id=%s",(CAMPAIGN,r['facility_id']))
    return inserted


def historical_cached(db, pass_id):
    """Recover a rooftop only when one cited historical source row supplies the
    complete address and that exact address already has a cached rooftop result.

    A facility can have several historical sites. Distinct rooftop coordinates are
    treated as a conflict and left unresolved; this pass never guesses which site is
    current and never calls an external service.
    """
    from psycopg.types.json import Jsonb
    rows=db.execute("""SELECT c.facility_id,c.baseline,a.row_hash,a.release_tag,a.field_key,a.value,
                              a.source_key,r.source_url,r.source_document,r.retrieved_date,
                              r.source_identifier
                       FROM coordinate_recovery_rows c
                       JOIN fact_assertions a ON a.facility_key=c.facility_id
                       LEFT JOIN ref_source_row r ON r.row_hash=a.row_hash
                       WHERE c.campaign_id=%s AND c.status='unresolved'
                         AND a.field_key=ANY(%s)
                         AND NOT EXISTS (SELECT 1 FROM coordinate_recovery_attempts x
                           WHERE x.campaign_id=c.campaign_id AND x.facility_id=c.facility_id
                             AND x.pass_id=%s AND x.stage='historical_cached')
                       ORDER BY r.retrieved_date DESC NULLS LAST,a.release_tag DESC""",
                    (CAMPAIGN,['name','address','city','state','zip'],pass_id)).fetchall()
    grouped={}
    baselines={}
    for row in rows:
        fid=row['facility_id'];baselines[fid]=row['baseline']
        key=(fid,row['row_hash'],row['release_tag'],row['source_key'])
        evidence={}
        for k in ('row_hash','release_tag','source_key','source_url','source_document','retrieved_date','source_identifier'):
            value=row.get(k)
            evidence[k]=value.isoformat() if hasattr(value,'isoformat') else value
        item=grouped.setdefault(key,{'values':{},'evidence':evidence})
        item['values'][row['field_key']]=row['value']
    candidates=[]
    rejected=Counter()
    for (fid,*_),item in grouped.items():
        values=item['values'];baseline=baselines[fid]
        if not eligible(values):rejected['incomplete_historical_source_row']+=1;continue
        if norm(values.get('name'))!=norm(baseline.get('name')):
            rejected['historical_name_mismatch']+=1;continue
        address={**baseline,**{k:values.get(k) or '' for k in ('address','city','state','zip')}}
        query=one_line(address)
        candidates.append((fid,address,item['evidence'],query,cache.geocode_key(query)))
    keys=list(dict.fromkeys(c[4] for c in candidates))
    saved=db.execute("SELECT * FROM cache_lookup WHERE cache_key=ANY(%s) AND provider='geocodio'",(keys,)).fetchall() if keys else []
    cached={r['cache_key']:r for r in saved}
    evaluated={}
    for fid,address,evidence,query,key in candidates:
        if key not in cached:continue
        result=cached[key]['result'];result=json.loads(result) if isinstance(result,str) else result
        hit,outcome=validate_rooftop(address,result)
        evaluated.setdefault(fid,[]).append(dict(address=address,evidence=evidence,query=query,key=key,
                                                  hit=hit,outcome=outcome))
    inserted=0;outcomes=Counter()
    for fid,items in evaluated.items():
        rooftops={f"{i['hit']['location']['lat']},{i['hit']['location']['lng']}":i for i in items if i['hit']}
        if len(rooftops)>1:
            outcome='conflicting_historical_rooftops';chosen=None
        elif len(rooftops)==1:
            outcome='historical_rooftop_verified';chosen=next(iter(rooftops.values()))
        else:
            outcome='historical_cache_not_rooftop';chosen=None
        with connect() as tx:
            live=tx.execute("SELECT name,release_tag,lat_lon FROM golden_facility WHERE facility_key=%s FOR SHARE",(fid,)).fetchone()
            baseline=baselines[fid]
            if not live or live['release_tag']!=baseline['release_tag'] or norm(live['name'])!=norm(baseline['name']) or str(live.get('lat_lon') or '').strip():
                outcome='live_row_changed';chosen=None
            tx.execute("""INSERT INTO coordinate_recovery_attempts
                       (campaign_id,facility_id,pass_id,stage,query_key,request,result,outcome,reserved_usd,run_url,completed_at)
                       VALUES(%s,%s,%s,'historical_cached',%s,%s,%s,%s,0,%s,now())
                       ON CONFLICT(campaign_id,facility_id,pass_id,stage) DO NOTHING""",
                       (CAMPAIGN,fid,pass_id,hashlib.sha256('|'.join(sorted(i['key'] for i in items)).encode()).hexdigest()[:32],
                        Jsonb({'queries':[i['query'] for i in items]}),
                        Jsonb({'outcomes':[i['outcome'] for i in items],'distinct_rooftops':len(rooftops)}),outcome,run_url()))
            if chosen:
                record={'facility_id':fid,'baseline':chosen['address'],'evidence':[chosen['evidence']],
                        'live_release':live['release_tag']}
                inserted+=append_coordinate(tx,record,chosen['hit'])
        outcomes[outcome]+=1
    return dict(historical_source_rows=len(candidates),historical_cached_rows=len(evaluated),
                historical_rejections=dict(rejected),outcomes=dict(outcomes),
                coordinate_assertions_inserted=inserted,new_api_calls=0)


def execute(mode,pass_id,row_limit):
    from psycopg.types.json import Jsonb
    with connect() as db:
        campaign=freeze(db)
        if mode=='fl_bcis':
            from pipeline.recovery.fl_bcis import recover
            summary=dict(campaign_id=CAMPAIGN,mode=mode,pass_id=pass_id,frozen_count=campaign['frozen_count'],
                         budget_ceiling_usd=float(campaign['api_ceiling']),
                         budget_reserved_before_usd=float(campaign['reserved_usd']),workflow=run_url(),golden_writes=0)
            summary.update(recover(db,CAMPAIGN,pass_id,row_limit,run_url()))
            statuses=db.execute('SELECT status,count(*) AS n FROM coordinate_recovery_rows WHERE campaign_id=%s GROUP BY status',(CAMPAIGN,)).fetchall()
            summary['campaign_status_counts']={r['status']:r['n'] for r in statuses}
            out=Path('recovery-summary');out.mkdir(exist_ok=True);(out/'summary.json').write_text(json.dumps(summary,indent=2))
            print(json.dumps(summary,indent=2));return
        if mode=='historical_cached':
            summary=dict(campaign_id=CAMPAIGN,mode=mode,pass_id=pass_id,frozen_count=campaign['frozen_count'],
                         budget_ceiling_usd=float(campaign['api_ceiling']),
                         budget_reserved_before_usd=float(campaign['reserved_usd']),workflow=run_url(),golden_writes=0)
            summary.update(historical_cached(db,pass_id))
            statuses=db.execute('SELECT status,count(*) AS n FROM coordinate_recovery_rows WHERE campaign_id=%s GROUP BY status',(CAMPAIGN,)).fetchall()
            summary['campaign_status_counts']={r['status']:r['n'] for r in statuses}
            out=Path('recovery-summary');out.mkdir(exist_ok=True);(out/'summary.json').write_text(json.dumps(summary,indent=2))
            print(json.dumps(summary,indent=2));return
        selected,blocked,eligible_count,diagnostics=select_rows(db,pass_id,row_limit)
        queries=[one_line(r['baseline']) for r in selected]
        keys=[cache.geocode_key(q) for q in queries]
        saved=db.execute("SELECT * FROM cache_lookup WHERE cache_key=ANY(%s) AND provider='geocodio'",(keys,)).fetchall()
        cached={r['cache_key']:r for r in saved}
    scanned=len(selected)
    if mode=='cached':
        kept=[(r,q,k) for r,q,k in zip(selected,queries,keys) if k in cached]
        selected=[x[0] for x in kept];queries=[x[1] for x in kept];keys=[x[2] for x in kept]
    fresh=len({cache.geocode_key(q) for q in queries}-set(cached))
    summary=dict(campaign_id=CAMPAIGN,mode=mode,pass_id=pass_id,frozen_count=campaign['frozen_count'],
                 eligible_remaining=eligible_count,selected=len(selected),blocked=blocked,
                 scanned_for_cached=scanned if mode=='cached' else None,
                 cached_unique_queries=len(set(keys)&set(cached)),estimated_new_lookups=fresh,
                 estimated_api_upper_bound_usd=round(fresh*.001,3),budget_ceiling_usd=float(campaign['api_ceiling']),
                 budget_reserved_before_usd=float(campaign['reserved_usd']),workflow=run_url(),golden_writes=0,
                 recovery_input_diagnostics=diagnostics)
    print(json.dumps(summary),flush=True)
    outcomes=Counter();inserted=0;calls=0
    if mode in ('geocode','cached'):
        if fresh and not os.environ.get('GEOCODIO_API_KEY'):raise RuntimeError('Geocodio key absent; no calls made')
        for r,q,k in zip(selected,queries,keys):
            is_cached=k in cached
            with connect() as db:reserve(db,r,pass_id,q,Decimal('0') if is_cached else Decimal('0.001'))
            try:
                if is_cached:
                    result=cached[k]['result'];result=json.loads(result) if isinstance(result,str) else result
                else:
                    calls+=1;response,stopped=_post([q],os.environ['GEOCODIO_API_KEY'])
                    if stopped or len(response)!=1:raise RuntimeError('Incomplete provider response')
                    result=response[0]
                hit,outcome=validate_rooftop(r['baseline'],result)
                with connect() as db:
                    # Recheck identity immediately before attaching an assertion to the live release.
                    live=db.execute('SELECT name,city,state,release_tag FROM golden_facility WHERE facility_key=%s FOR SHARE',(r['facility_id'],)).fetchone()
                    frozen=r.get('frozen') or r['baseline']
                    if not live or live['release_tag']!=r['live_release'] or norm(live.get('name'))!=norm(frozen.get('name')) or any(frozen.get(f) and norm(live.get(f))!=norm(frozen.get(f)) for f in ('city','state')):raise RuntimeError('Facility identity/release changed during lookup')
                    if not is_cached:
                        db.execute('INSERT INTO cache_lookup(cache_key,kind,input,result,found,provider,fetched_at,hits) VALUES(%s,%s,%s,%s,%s,%s,%s,0) ON CONFLICT(cache_key) DO UPDATE SET result=EXCLUDED.result,found=EXCLUDED.found,provider=EXCLUDED.provider,fetched_at=EXCLUDED.fetched_at',
                                   (k,'geocode',q,json.dumps(result),int(bool((result.get('response') or {}).get('results'))),'geocodio',datetime.now(timezone.utc).isoformat()))
                    if hit:inserted+=append_coordinate(db,r,hit)
                    db.execute("UPDATE coordinate_recovery_attempts SET result=%s,outcome=%s,completed_at=now() WHERE campaign_id=%s AND facility_id=%s AND pass_id=%s AND stage='geocode'",(Jsonb(result),outcome,CAMPAIGN,r['facility_id'],pass_id))
                cached[k]={'result':result};outcomes[outcome]+=1
            except Exception as exc:
                with connect() as db:
                    db.execute("UPDATE coordinate_recovery_attempts SET outcome='error',result=%s,completed_at=now() WHERE campaign_id=%s AND facility_id=%s AND pass_id=%s AND stage='geocode'",(Jsonb({'error_type':type(exc).__name__}),CAMPAIGN,r['facility_id'],pass_id))
                outcomes['error']+=1
            if sum(outcomes.values())%10==0:print(json.dumps({'processed':sum(outcomes.values()),'outcomes':dict(outcomes)}),flush=True)
    with connect() as db:
        current=db.execute('SELECT * FROM coordinate_recovery_campaigns WHERE campaign_id=%s',(CAMPAIGN,)).fetchone()
        statuses=db.execute('SELECT status,count(*) AS n FROM coordinate_recovery_rows WHERE campaign_id=%s GROUP BY status',(CAMPAIGN,)).fetchall()
    summary.update(outcomes=dict(outcomes),coordinate_assertions_inserted=inserted,new_api_calls=calls,
                   campaign_budget_reserved_usd=float(current['reserved_usd']),campaign_status_counts={r['status']:r['n'] for r in statuses})
    out=Path('recovery-summary');out.mkdir(exist_ok=True);(out/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))
    if outcomes.get('error'):raise RuntimeError('Some rows failed; private attempt records retained for diagnosis')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['plan','cached','historical_cached','fl_bcis','geocode'],default='plan');p.add_argument('--pass-id',default='1');p.add_argument('--limit',type=int,default=100);a=p.parse_args()
    maximum=2000 if a.mode in ('cached','historical_cached') else 100
    if not 1<=a.limit<=maximum:raise ValueError(f'{a.mode} batches are limited to {maximum} rows')
    execute(a.mode,a.pass_id,a.limit)
