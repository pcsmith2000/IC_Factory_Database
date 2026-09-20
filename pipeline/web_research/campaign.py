"""Frozen, artifact-only research rounds; no database credentials or writes."""
import argparse
import json
import os
import re
from pathlib import Path
from .run import norm, domain


def read_all(root, name):
    return [json.loads(p.read_text()) for p in Path(root).rglob(name)]


def normalized_detail(field, value):
    if field=='website': return domain(value)
    if field=='phone':
        digits=re.sub(r'\D','',str(value or ''))
        return digits[1:] if len(digits)==11 and digits.startswith('1') else digits
    if field=='zip':
        value=str(value or '').strip()
        if re.fullmatch(r'\d{5}(?:-\d{4})?',value):return value[:5]
    if field=='address':
        tokens=re.findall(r'[a-z0-9]+',str(value or '').lower())
        aliases={'street':'st','road':'rd','avenue':'ave','boulevard':'blvd','drive':'dr','lane':'ln','court':'ct','highway':'hwy','parkway':'pkwy','north':'n','south':'s','east':'e','west':'w','suite':'ste'}
        return ''.join(aliases.get(t,t) for t in tokens)
    return norm(value)


def detail_key(fid, p):
    return fid,p['field'],normalized_detail(p['field'],p['value'])


def supported_detail(p):
    # Review may contain useful, supported conflicts; report those separately from accepted fills.
    return (p.get('quote_verified') and p.get('identity_anchor_found') and
            p.get('source_kind') in ('official','registry') and
            (p.get('scope')=='facility' or p['field'] in ('website','phone','email')))


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
    resume_manifests=read_all(Path(root)/'resume','campaign.json')
    if os.environ.get('RESUME_RUN') and (not resume_manifests or any(m['round']!=round_number for m in resume_manifests)):
        raise ValueError('Resume artifacts must belong to the same round')
    completed={r['facility_id'] for block in read_all(Path(root)/'resume','results.json') for r in block}
    original_size=len(selected)
    selected=[r for r in selected if r['facility_id'] not in completed]
    for r in selected:
        # Preserve the baseline values. Prior values are search context, never accepted evidence.
        r['previously_reported_details']=[{'field':p['field'],'value':p['value']} for p in seen.get(r['facility_id'],[])]
        r['research_round']=round_number
    out.mkdir(parents=True,exist_ok=True)
    (out/'input.json').write_text(json.dumps(selected,indent=2))
    (out/'campaign.json').write_text(json.dumps({'round':round_number,'batch':batch,'cohort':241,'batch_size':100,'selected':len(selected),'original_batch_size':original_size,'skipped_completed':original_size-len(selected),'resume_run':os.environ.get('RESUME_RUN',''),'database_writes':0},indent=2))


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
                normalized=normalized_detail(p['field'],old)
                if key in seen or key[2]==normalized:continue
                new[key]={'facility_id':r['facility_id'],**p}
                if p['decision']=='candidate' and p['relationship']=='fill':candidates[key]=new[key]
    completed_ids=[r['facility_id'] for block in blocks for r in block]
    if len(completed_ids)!=len(set(completed_ids)):raise ValueError('Duplicate completed facilities; refuse inflated coverage')
    if set(completed_ids)-set(baseline):raise ValueError('Results outside frozen cohort')
    summaries=read_all(current,'summary.json')
    all_errors=[e for s in summaries for e in s['errors']]
    unresolved_errors=[e for e in all_errors if e.get('facility_id') not in set(completed_ids)]
    value={'coverage_complete':set(completed_ids)==set(baseline) and not unresolved_errors,
           'recovered_errors':[e for e in all_errors if e.get('facility_id') in set(completed_ids)],
           'unique_facilities_completed':len(set(completed_ids)),
           'new_supported_details':len(new),'new_candidate_fills':len(candidates),
           'new_review_details':len(new)-len(candidates),'completed':sum(s['completed'] for s in summaries),
           'selected':sum(s['selected'] for s in summaries),'errors':unresolved_errors,
           'cost_usd':round(sum(s['usage']['known_cost_subtotal_usd'] for s in summaries),6),
           'responses_missing_cost':sum(s['usage']['responses_missing_cost'] for s in summaries),
           'details':list(new.values()),'database_writes':0}
    out.mkdir(parents=True,exist_ok=True)
    (out/'round-report.json').write_text(json.dumps(value,indent=2))
    print(json.dumps({k:v for k,v in value.items() if k!='details'},indent=2))

def execute(out, workers=1):
    """Run independent rows concurrently inside one logical 100-row batch."""
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor
    rows=json.loads((out/'input.json').read_text())
    if not rows:
        (out/'results.json').write_text('[]')
        return
    workers=min(workers,len(rows))
    if not 1<=workers<=4:raise ValueError('Expected 1..4 workers')
    def worker(index):
        folder=out/f'worker-{index}'
        folder.mkdir(parents=True,exist_ok=True)
        (folder/'input.json').write_text(json.dumps(rows[index::workers],indent=2))
        env=dict(os.environ,RESEARCH_OUT=str(folder))
        # Workers must not append concurrently to GitHub's shared summary file.
        env.pop('GITHUB_STEP_SUMMARY',None)
        with (folder/'worker.log').open('w') as log:
            result=subprocess.run([sys.executable,'-m','pipeline.web_research.run','--mode','research'],env=env,stdout=log,stderr=subprocess.STDOUT)
        print(f'Worker {index} finished with status {result.returncode}',flush=True)
        return result.returncode
    from .costs import estimate
    from .run import MODEL,EXTRACT_MODEL
    estimate(len(rows),(MODEL,EXTRACT_MODEL),out)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        codes=list(pool.map(worker,range(workers)))
    if any(codes):raise SystemExit('A research worker failed; preserve and inspect partial artifacts')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','report','execute']);p.add_argument('--root',default='campaign-input');p.add_argument('--out',default='research-output');p.add_argument('--current',default='research-output');p.add_argument('--batch',type=int,default=0);p.add_argument('--round',type=int,default=1);a=p.parse_args()
    if a.command=='prepare':prepare(a.root,Path(a.out),a.batch,a.round)
    elif a.command=='execute':execute(Path(a.out))
    else:report(a.root,a.current,Path(a.out))
