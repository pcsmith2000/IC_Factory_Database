"""Frozen, artifact-only research rounds; no database credentials or writes."""
import argparse
import json
import os
from pathlib import Path
from .run import norm, domain


def read_all(root, name):
    return [json.loads(p.read_text()) for p in Path(root).rglob(name)]


def detail_key(fid, p):
    value=domain(p['value']) if p['field']=='website' else norm(p['value'])
    return fid,p['field'],value


def supported_detail(p):
    # Review may contain useful, supported conflicts; report those separately from accepted fills.
    return (p.get('quote_verified') and p.get('identity_anchor_found') and
            p.get('source_kind') in ('official','registry') and
            (p.get('scope')=='facility' or p['field']=='website'))


def prepare(root, out, batch, round_number):
    rows={}
    for block in read_all(Path(root)/'baseline','input.json'):
        for r in block:
            if r['facility_id'] in rows and rows[r['facility_id']]!=r:
                raise ValueError('Conflicting frozen baseline')
            rows[r['facility_id']]=r
    if len(rows)!=241: raise ValueError(f'Expected frozen 241-row cohort, got {len(rows)}')
    prior=read_all(Path(root)/'prior','results.json')
    seen={}
    for block in prior:
        for r in block:
            seen.setdefault(r['facility_id'],[]).extend(p for p in r['proposals'] if supported_detail(p))
    selected=sorted(rows.values(),key=lambda r:r['facility_id'])[batch*100:(batch+1)*100]
    if not selected: raise ValueError('Empty batch')
    for r in selected:
        # Preserve the baseline values. Prior values are search context, never accepted evidence.
        r['previously_reported_details']=[{'field':p['field'],'value':p['value']} for p in seen.get(r['facility_id'],[])]
        r['research_round']=round_number
    out.mkdir(parents=True,exist_ok=True)
    (out/'input.json').write_text(json.dumps(selected,indent=2))
    (out/'campaign.json').write_text(json.dumps({'round':round_number,'batch':batch,'cohort':241,'batch_size':100,'selected':len(selected),'database_writes':0},indent=2))


def report(root, current, out):
    prior=read_all(Path(root)/'prior','results.json')
    seen={detail_key(r['facility_id'],p) for block in prior for r in block for p in r['proposals'] if supported_detail(p)}
    baseline={r['facility_id']:r for block in read_all(Path(root)/'baseline','input.json') for r in block}
    new={}; candidates={}
    blocks=read_all(current,'results.json')
    for block in blocks:
        for r in block:
            for p in r['proposals']:
                if not supported_detail(p):continue
                key=detail_key(r['facility_id'],p)
                old=baseline[r['facility_id']].get(p['field'])
                normalized=domain(old) if p['field']=='website' else norm(old)
                if key in seen or key[2]==normalized:continue
                new[key]={'facility_id':r['facility_id'],**p}
                if p['decision']=='candidate' and p['relationship']=='fill':candidates[key]=new[key]
    summaries=read_all(current,'summary.json')
    value={'new_supported_details':len(new),'new_candidate_fills':len(candidates),
           'new_review_details':len(new)-len(candidates),'completed':sum(s['completed'] for s in summaries),
           'selected':sum(s['selected'] for s in summaries),'errors':[e for s in summaries for e in s['errors']],
           'cost_usd':round(sum(s['usage']['known_cost_subtotal_usd'] for s in summaries),6),
           'responses_missing_cost':sum(s['usage']['responses_missing_cost'] for s in summaries),
           'details':list(new.values()),'database_writes':0}
    out.mkdir(parents=True,exist_ok=True)
    (out/'round-report.json').write_text(json.dumps(value,indent=2))
    print(json.dumps({k:v for k,v in value.items() if k!='details'},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','report']);p.add_argument('--root',default='campaign-input');p.add_argument('--out',default='research-output');p.add_argument('--current',default='research-output');p.add_argument('--batch',type=int,default=0);p.add_argument('--round',type=int,default=1);a=p.parse_args()
    if a.command=='prepare':prepare(a.root,Path(a.out),a.batch,a.round)
    else:report(a.root,a.current,Path(a.out))
