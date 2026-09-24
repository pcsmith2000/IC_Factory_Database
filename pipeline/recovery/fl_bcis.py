"""Recover manufacturing-site addresses from Florida BCIS application details.

The source list contains encrypted links to public application pages. Those pages
separate the business/mailing address from a dedicated Manufacturing Facility
section. Only the latter is admitted here. Raw pages stay on the runner; the
database receives the extracted cells, their public URL, and workflow provenance.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import tempfile
import urllib.parse

from pipeline import archive
from pipeline.enrich._db import assertion, _rows_for
from pipeline.recovery.run import eligible, norm
from pipeline.registry import load_yaml
from pipeline.sources._common import http_get

ROOT=Path(__file__).resolve().parents[2]
MENU='https://floridabuilding.org/mb/mb_default.aspx'
LINK=re.compile(r'<a(?=[^>]*\bid="grdReport__ctl(\d+)_hlnkOrgName")(?=[^>]*\bhref="([^"]+)")[^>]*>(.*?)</a>',re.I|re.S)
ORGNUM=re.compile(r'FBC\s*Organization\s*Number\s*</b>\s*([A-Za-z0-9\-]+)',re.I)


def _text(value):
    return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',value or ''))).strip()


def detail_links(body):
    """Return list rows with the opaque official detail URL retained."""
    matches=list(LINK.finditer(body));out=[]
    for i,m in enumerate(matches):
        block=body[m.end():matches[i+1].start() if i+1<len(matches) else len(body)]
        number=ORGNUM.search(block)
        url=urllib.parse.urljoin(MENU,html.unescape(m.group(2)))
        parsed=urllib.parse.urlparse(url)
        if parsed.scheme not in ('http','https') or (parsed.hostname or '').lower() not in ('floridabuilding.org','www.floridabuilding.org') or parsed.username or parsed.password or parsed.port not in (None,80,443) or not parsed.path.lower().endswith('/mb_orgapp_dtl2.aspx'):
            continue
        out.append({'name':_text(m.group(3)),'source_identifier':number.group(1) if number else '',
                    'url':url})
    return out


def _input(body, element_id):
    tag=re.search(r'<input\b[^>]*\bid="'+re.escape(element_id)+r'"[^>]*>',body,re.I)
    if not tag:return ''
    value=re.search(r'\bvalue="([^"]*)"',tag.group(),re.I)
    return html.unescape(value.group(1)).strip() if value else ''


def _selected(body, element_id):
    select=re.search(r'<select\b[^>]*\bid="'+re.escape(element_id)+r'"[^>]*>(.*?)</select>',body,re.I|re.S)
    if not select:return ''
    option=re.search(r'<option\b[^>]*\bselected(?:="selected")?[^>]*\bvalue="([^"]+)"',select.group(1),re.I)
    if not option:
        option=re.search(r'<option\b[^>]*\bvalue="([^"]+)"[^>]*\bselected(?:="selected")?',select.group(1),re.I)
    return html.unescape(option.group(1)).strip() if option else ''


def manufacturing_address(body):
    """Read only the dedicated Manufacturing Facility controls."""
    if not re.search(r'id="lblMFFacility"[^>]*>\s*Manufacturing Facility',body,re.I):return None
    lines=[_input(body,'txtStreetAddr1_txtTextbox'),_input(body,'txtStreetAddr2_txtTextbox')]
    street=next((v for v in lines if re.match(r'^\d+[A-Za-z]?\s',v)), '')
    row={'address':street,'city':_input(body,'txtCity_txtTextbox'),
         'state':_selected(body,'lstMFState_drpCustomDropdown'),'zip':_input(body,'txtZip_txtTextbox')}
    return row if eligible(row) else None


def _archive_results():
    cfg=load_yaml(ROOT/'registry'/'config.yaml');store=archive.open_archive(cfg)
    if store is None:raise RuntimeError('Private source archive credential is unavailable')
    dates=store.dates_for('fl_bcis')
    if not dates:raise RuntimeError('No Florida BCIS source archive exists')
    temp=tempfile.TemporaryDirectory(prefix='fl-bcis-recovery-')
    files=store.fetch_folder('fl_bcis',dates[0],Path(temp.name))
    result=next((p for p in files if p.suffix.lower() in ('.html','.htm') and
                 'grdReport' in p.read_text(encoding='utf-8',errors='ignore')),None)
    if result is None:
        temp.cleanup();raise RuntimeError('Latest Florida BCIS archive has no organization results grid')
    return temp,result,dates[0]


def _append_fields(db,fid,release,address,detail_url,source_id,workflow):
    now=datetime.now(timezone.utc).isoformat(timespec='seconds');inserted=0
    evidence=detail_url+' :: '+json.dumps({'workflow':workflow,'section':'Manufacturing Facility',
        'source_identifier':source_id,'labels':['Street Address','City','State','Zip Code']},sort_keys=True)
    for field,value in address.items():
        if not value:continue
        item=assertion(fid,field,value,source_id='fl_bcis',basis='registry_manufacturing_facility',
                       confidence=1.0,evidence=evidence)
        item['source_class']='A'
        ev,fact=_rows_for(item,release,now[:10],now)
        db.execute('INSERT INTO ref_source_row(row_hash,source_key,source_url,source_document,retrieved_date,facility_key,match_method,match_confidence,last_seen_release) VALUES('+','.join(['%s']*9)+') ON CONFLICT(row_hash) DO NOTHING',ev)
        inserted+=len(db.execute('INSERT INTO fact_assertions(assertion_id,release_tag,facility_key,source_key,field_key,date_key,value,basis,site_visit,row_hash,confidence,source_class,asserted_at) VALUES('+','.join(['%s']*13)+') ON CONFLICT(assertion_id,release_tag) DO NOTHING RETURNING assertion_id',fact).fetchall())
    return inserted


def recover(db,campaign,pass_id,limit,workflow):
    from psycopg.types.json import Jsonb
    temp,result,archive_date=_archive_results()
    try:
        links=detail_links(result.read_text(encoding='utf-8',errors='replace'))
        by_id={norm(x['source_identifier']):x for x in links if x['source_identifier']}
        by_name={}
        for x in links:by_name.setdefault(norm(x['name']),[]).append(x)
        rows=db.execute("""SELECT c.facility_id,c.baseline,g.release_tag,r.source_identifier
                           FROM coordinate_recovery_rows c JOIN golden_facility g ON g.facility_key=c.facility_id
                           JOIN fact_assertions a ON a.facility_key=c.facility_id AND a.release_tag=g.release_tag
                             AND a.source_key='fl_bcis' AND a.field_key='name'
                           LEFT JOIN ref_source_row r ON r.row_hash=a.row_hash
                           WHERE c.campaign_id=%s AND c.status='unresolved' AND c.recovered_address IS NULL
                             AND NOT EXISTS (SELECT 1 FROM coordinate_recovery_attempts x
                               WHERE x.campaign_id=c.campaign_id AND x.facility_id=c.facility_id
                                 AND x.pass_id=%s AND x.stage='fl_bcis')
                           ORDER BY c.facility_id LIMIT %s""",(campaign,pass_id,limit)).fetchall()
        outcomes=Counter();assertions=0;recovered=0
        for row in rows:
            link=by_id.get(norm(row.get('source_identifier')))
            if not link:
                matches=by_name.get(norm(row['baseline'].get('name')),[])
                link=matches[0] if len(matches)==1 else None
            address=None;error=''
            if link:
                try:
                    filename='detail-'+row['facility_id']+'.html'
                    page=http_get(link['url'],Path(temp.name)/'details',filename,timeout=45,retries=2)
                    address=manufacturing_address(page.read_text(encoding='utf-8',errors='replace'))
                except Exception as exc:error=type(exc).__name__
            outcome='recovered_manufacturing_address' if address else ('detail_unreadable' if error else ('detail_has_no_physical_facility' if link else 'detail_link_not_found'))
            db.execute("""INSERT INTO coordinate_recovery_attempts
                       (campaign_id,facility_id,pass_id,stage,query_key,request,result,outcome,reserved_usd,run_url,completed_at)
                       VALUES(%s,%s,%s,'fl_bcis',%s,%s,%s,%s,0,%s,now())
                       ON CONFLICT(campaign_id,facility_id,pass_id,stage) DO NOTHING""",
                       (campaign,row['facility_id'],pass_id,norm(row.get('source_identifier')) or norm(row['baseline'].get('name')),
                        Jsonb({'detail_url':link['url'] if link else None}),Jsonb({'address':address,'error_type':error}),outcome,workflow))
            if address:
                assertions+=_append_fields(db,row['facility_id'],row['release_tag'],address,link['url'],link['source_identifier'],workflow)
                added_evidence={'source_key':'fl_bcis','source_url':link['url'],
                    'source_identifier':link['source_identifier'],'section':'Manufacturing Facility',
                    'workflow':workflow,'fields':address}
                db.execute('UPDATE coordinate_recovery_rows SET recovered_address=%s,evidence=evidence || %s WHERE campaign_id=%s AND facility_id=%s',
                           (Jsonb(address),Jsonb([added_evidence]),campaign,row['facility_id']))
                recovered+=1
            outcomes[outcome]+=1
        return {'archive_date':archive_date,'registry_detail_links':len(links),'selected':len(rows),
                'addresses_recovered':recovered,'address_assertions_inserted':assertions,
                'outcomes':dict(outcomes),'new_api_calls':0,'external_service':'official Florida BCIS public registry'}
    finally:
        temp.cleanup()
