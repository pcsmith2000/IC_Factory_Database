"""Recover plant streets from Maryland's current approved-manufacturer PDF.

The Maryland Department of Labor document has one row per approved plant and a
dedicated ``Number_Street`` column.  It does not publish city/state in that
table, so this pass supplies only the street and leaves locality fields to the
already-cited source rows.  Ambiguous names and rows without a physical street
are retained as unresolved rather than guessed.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile

from pipeline.enrich._db import assertion, _rows_for
from pipeline.sources._common import http_get,pdf_lines


URL='https://labor.maryland.gov/labor/build/buildactivemanu.pdf'
SOURCE='md_labor_active_manufacturers'
US_STATES=set('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split())


def norm(value):
    return re.sub(r'[^a-z0-9]','',str(value or '').lower())


def parse_lines(lines: list[list[dict]]) -> list[dict]:
    """Read the three visual columns from pdfplumber word coordinates."""
    columns={}
    for line in lines:
        words={w['text']:w for w in line}
        if 'Contact' in words and 'Number_Street' in words:
            columns[line[0]['page']]=(words['Contact']['x0'],words['Number_Street']['x0'])
    out=[];pending={}
    for line in lines:
        if not line or line[0]['page'] not in columns:continue
        page=line[0]['page'];contact,street=columns[page]
        name_words=[w['text'] for w in line if w['x0']<contact]
        address=' '.join(w['text'] for w in line if w['x0']>=street-1).strip()
        begins=bool(name_words and name_words[0].startswith('P-'))
        if begins:
            name=' '.join(name_words).removeprefix('P-').strip();pending[page]=name
        elif page in pending and address:
            # One current row wraps the last word of its company name onto the
            # next visual line; retain that fragment before reading its street.
            name=(pending[page]+' '+' '.join(name_words)).strip()
        else:
            continue
        # PO boxes and blank plant rows are not physical rooftop inputs.
        if name and re.match(r'^\d+[A-Za-z]?\s',address):
            out.append({'name':name,'address':address});pending.pop(page,None)
    return out


def parse_pdf(path: Path) -> list[dict]:
    rows=parse_lines(pdf_lines(path))
    if len(rows)<50:raise RuntimeError('Maryland manufacturer PDF layout changed or returned too few plant rows')
    return rows


def unique_addresses(rows: list[dict]) -> dict[str,dict]:
    """Only names resolving to one distinct official street are matchable."""
    grouped={}
    for row in rows:grouped.setdefault(norm(row['name']),{})[norm(row['address'])]=row
    return {name:next(iter(items.values())) for name,items in grouped.items() if name and len(items)==1}


def crossmatch_candidates(rows,matches,golden_counts,limit):
    out=[]
    for row in rows:
        b=row['baseline'];key=norm(b.get('name'))
        if (key in matches and golden_counts[key]==1 and str(b.get('city') or '').strip()
                and str(b.get('state') or '').upper() in US_STATES
                and not re.match(r'^\d+[A-Za-z]?\s',str(b.get('address') or '').strip())):
            out.append(row)
    return out[:limit]


def _append_address(db,fid,release,address,workflow):
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    db.execute("""INSERT INTO dim_source(source_key,source_id,name,class,status)
                  VALUES(%s,%s,%s,'A','active') ON CONFLICT(source_key) DO NOTHING""",
               (SOURCE,SOURCE,'Maryland Labor — active approved manufacturers'))
    evidence=URL+' :: '+json.dumps({'workflow':workflow,'document':'buildactivemanu.pdf',
        'column':'Number_Street','retrieved_at':now},sort_keys=True)
    item=assertion(fid,'address',address,source_id=SOURCE,
                   basis='official_approved_manufacturer_plant_address',confidence=1.0,evidence=evidence)
    item['source_class']='A'
    ev,fact=_rows_for(item,release,now[:10],now)
    db.execute('INSERT INTO ref_source_row(row_hash,source_key,source_url,source_document,retrieved_date,facility_key,match_method,match_confidence,last_seen_release) VALUES('+','.join(['%s']*9)+') ON CONFLICT(row_hash) DO NOTHING',ev)
    inserted=len(db.execute('INSERT INTO fact_assertions(assertion_id,release_tag,facility_key,source_key,field_key,date_key,value,basis,site_visit,row_hash,confidence,source_class,asserted_at) VALUES('+','.join(['%s']*13)+') ON CONFLICT(assertion_id,release_tag) DO NOTHING RETURNING assertion_id',fact).fetchall())
    saved=db.execute('SELECT a.value,a.basis,r.source_url FROM fact_assertions a JOIN ref_source_row r ON r.row_hash=a.row_hash WHERE a.assertion_id=%s AND a.release_tag=%s',(fact[0],release)).fetchone()
    if not saved or saved['value']!=address or saved['basis']!='official_approved_manufacturer_plant_address' or saved['source_url']!=URL:
        raise RuntimeError('Maryland address provenance verification failed')
    return inserted


def recover(db,campaign,pass_id,limit,workflow):
    from psycopg.types.json import Jsonb
    with tempfile.TemporaryDirectory(prefix='md-labor-recovery-') as temp:
        pdf=http_get(URL,Path(temp),'buildactivemanu.pdf',timeout=60,retries=2)
        official=parse_pdf(pdf)
    matches=unique_addresses(official)
    rows=db.execute("""SELECT c.facility_id,c.baseline,g.release_tag,
                              c.baseline->>'name' AS source_name
                       FROM coordinate_recovery_rows c
                       JOIN golden_facility g ON g.facility_key=c.facility_id
                       WHERE c.campaign_id=%s AND c.status='unresolved' AND c.recovered_address IS NULL
                         AND c.baseline->>'name__source'='ic_directories_more'
                         AND EXISTS (SELECT 1 FROM fact_assertions e
                           JOIN ref_source_row er ON er.row_hash=e.row_hash
                           WHERE e.facility_key=c.facility_id AND e.release_tag=g.release_tag
                             AND lower(COALESCE(er.source_url,'')) LIKE '%%labor.maryland.gov/%%')
                         AND NOT EXISTS (SELECT 1 FROM coordinate_recovery_attempts x
                           WHERE x.campaign_id=c.campaign_id AND x.facility_id=c.facility_id
                             AND x.pass_id=%s AND x.stage='md_labor')
                       ORDER BY c.facility_id LIMIT %s""",(campaign,pass_id,limit)).fetchall()
    outcomes=Counter();assertions=0;recovered=0
    for row in rows:
        hit=matches.get(norm(row.get('source_name')))
        address=hit['address'] if hit else None
        outcome='recovered_official_plant_street' if address else 'official_name_not_uniquely_matched'
        db.execute("""INSERT INTO coordinate_recovery_attempts
                   (campaign_id,facility_id,pass_id,stage,query_key,request,result,outcome,reserved_usd,run_url,completed_at)
                   VALUES(%s,%s,%s,'md_labor',%s,%s,%s,%s,0,%s,now())
                   ON CONFLICT(campaign_id,facility_id,pass_id,stage) DO NOTHING""",
                   (campaign,row['facility_id'],pass_id,norm(row.get('source_name')),
                    Jsonb({'source_url':URL}),Jsonb({'address':address}),outcome,workflow))
        if address:
            assertions+=_append_address(db,row['facility_id'],row['release_tag'],address,workflow)
            evidence={'source_key':SOURCE,'source_url':URL,'source_document':'buildactivemanu.pdf',
                      'column':'Number_Street','workflow':workflow,'fields':{'address':address}}
            db.execute('UPDATE coordinate_recovery_rows SET recovered_address=%s,evidence=evidence || %s WHERE campaign_id=%s AND facility_id=%s',
                       (Jsonb({'address':address}),Jsonb([evidence]),campaign,row['facility_id']))
            recovered+=1
        outcomes[outcome]+=1
    return {'official_pdf_rows':len(official),'unique_official_name_addresses':len(matches),
            'selected':len(rows),'addresses_recovered':recovered,
            'address_assertions_inserted':assertions,'outcomes':dict(outcomes),
            'new_api_calls':0,'external_service':'official Maryland Department of Labor public registry'}


def recover_crossmatch(db,campaign,pass_id,limit,workflow):
    """Supply a missing street when both datasets contain one unique plant name.

    Locality always comes from the frozen cited row.  To avoid attaching a
    company-level address to the wrong branch, the name must occur exactly once
    across the whole golden table as well as once in the official Maryland PDF.
    """
    from psycopg.types.json import Jsonb
    with tempfile.TemporaryDirectory(prefix='md-labor-crossmatch-') as temp:
        pdf=http_get(URL,Path(temp),'buildactivemanu.pdf',timeout=60,retries=2)
        official=parse_pdf(pdf)
    matches=unique_addresses(official)
    golden_counts=Counter(norm(r['name']) for r in db.execute('SELECT name FROM golden_facility').fetchall())
    raw=db.execute("""SELECT c.facility_id,c.baseline,g.release_tag
                      FROM coordinate_recovery_rows c
                      JOIN golden_facility g ON g.facility_key=c.facility_id
                      WHERE c.campaign_id=%s AND c.status='unresolved' AND c.recovered_address IS NULL
                        AND NOT EXISTS (SELECT 1 FROM coordinate_recovery_attempts x
                          WHERE x.campaign_id=c.campaign_id AND x.facility_id=c.facility_id
                            AND x.pass_id=%s AND x.stage='md_labor_crossmatch')
                      ORDER BY c.facility_id""",(campaign,pass_id)).fetchall()
    rows=crossmatch_candidates(raw,matches,golden_counts,limit);outcomes=Counter();assertions=0
    for row in rows:
        key=norm(row['baseline'].get('name'));address=matches[key]['address']
        db.execute("""INSERT INTO coordinate_recovery_attempts
                   (campaign_id,facility_id,pass_id,stage,query_key,request,result,outcome,reserved_usd,run_url,completed_at)
                   VALUES(%s,%s,%s,'md_labor_crossmatch',%s,%s,%s,'recovered_unique_name_plant_street',0,%s,now())
                   ON CONFLICT(campaign_id,facility_id,pass_id,stage) DO NOTHING""",
                   (campaign,row['facility_id'],pass_id,key,Jsonb({'source_url':URL}),
                    Jsonb({'address':address}),workflow))
        assertions+=_append_address(db,row['facility_id'],row['release_tag'],address,workflow)
        evidence={'source_key':SOURCE,'source_url':URL,'source_document':'buildactivemanu.pdf',
                  'match_method':'unique_exact_normalized_name_across_golden','workflow':workflow,
                  'fields':{'address':address}}
        db.execute('UPDATE coordinate_recovery_rows SET recovered_address=%s,evidence=evidence || %s WHERE campaign_id=%s AND facility_id=%s',
                   (Jsonb({'address':address}),Jsonb([evidence]),campaign,row['facility_id']))
        outcomes['recovered_unique_name_plant_street']+=1
    return {'official_pdf_rows':len(official),'unique_official_name_addresses':len(matches),
            'candidate_rows_scanned':len(raw),'selected':len(rows),'addresses_recovered':len(rows),
            'address_assertions_inserted':assertions,'outcomes':dict(outcomes),
            'new_api_calls':0,'external_service':'official Maryland Department of Labor public registry'}
