"""Consolidate campaign artifacts into a review ledger. No database access."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from .campaign import checked_proposals, detail_key, normalized_detail, supported_detail


def consolidate(cohort, round_paths, out):
    baseline={r['facility_id']:r for r in cohort}
    review_file=Path('research/adl-2026-09-20/review-overrides.json')
    overrides={detail_key(r['facility_id'],r):r['reason'] for r in json.loads(review_file.read_text())} if review_file.exists() else {}
    values={}
    observations=0
    for round_number,root in round_paths:
        for file in sorted(Path(root).rglob('results.json')):
            manifest=file.parent/'run-manifest.json'
            run_id=json.loads(manifest.read_text()).get('run_id') if manifest.exists() else None
            for result in json.loads(file.read_text()):
                fid=result['facility_id']
                if fid not in baseline:raise ValueError('Facility outside frozen cohort')
                if result.get('database_writes')!=0:raise ValueError('Unexpected write declaration')
                for p in checked_proposals(result):
                    if not supported_detail(p):continue
                    key=detail_key(fid,p)
                    if key[2]==normalized_detail(p['field'],baseline[fid].get(p['field'])):continue
                    observations+=1
                    entry=values.setdefault(key,dict(facility_id=fid,name=baseline[fid].get('name'),field=p['field'],value=p['value'],existing=baseline[fid].get(p['field']),first_round=round_number,evidence=[],has_candidate_fill=False))
                    if p['decision']=='candidate' and p['relationship']=='fill':entry['has_candidate_fill']=True
                    evidence=dict(round=round_number,run_id=run_id,url=p['source_url'],quote=p.get('quote'),scope=p.get('scope'),decision=p['decision'])
                    if evidence not in entry['evidence']:entry['evidence'].append(evidence)
    cells=defaultdict(list)
    for key,value in values.items():
        value['manual_review_reason']=overrides.get(key)
        cells[key[:2]].append(value)
    for alternatives in cells.values():
        for value in alternatives:
            value['competing_values']=len(alternatives)>1
            value['review_status']='noncompeting_candidate_fill' if value['has_candidate_fill'] and len(alternatives)==1 and not value['manual_review_reason'] else 'review'
    details=sorted(values.values(),key=lambda x:(x['facility_id'],x['field'],x['value']))
    summary=dict(frozen_facilities=len(baseline),supported_observations=observations,
                 distinct_proposed_values=len(details),affected_facilities=len({x['facility_id'] for x in details}),
                 distinct_cells=len(cells),noncompeting_candidate_fills=sum(x['review_status']=='noncompeting_candidate_fill' for x in details),
                 cells_with_competing_proposals=sum(len(v)>1 for v in cells.values()),
                 by_field=dict(sorted(Counter(x['field'] for x in details).items())),database_writes=0,
                 note='Review ledger only. Candidate means evidence-qualified, not approved or written. Multiple proposed values for one field remain review. This inventory is not the diminishing-return metric; use full-round reports for that.')
    out.mkdir(parents=True,exist_ok=True)
    (out/'review-ledger.json').write_text(json.dumps(details,indent=2))
    (out/'ledger-summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--cohort',required=True);p.add_argument('--root',required=True);p.add_argument('--rounds',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    rounds=[int(n) for n in a.rounds.split(',')]
    consolidate(json.loads(Path(a.cohort).read_text()),[(n,Path(a.root)/f'campaign-round-{n}') for n in rounds],Path(a.out))
