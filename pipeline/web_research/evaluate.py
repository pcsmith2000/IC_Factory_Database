"""Post-run reference comparison; this module is never used to construct search prompts."""
import json
from pathlib import Path
from .run import domain, norm

def evaluate(results, reference):
    checks=[]
    by_id={r['facility_id']:r for r in results}
    for ref in reference['rows']:
        row=by_id.get(ref['facility_id'])
        if not row:
            checks.append({'facility_id':ref['facility_id'],'check':'completed','pass':False}); continue
        proposals={p['field']:p for p in row['proposals']}
        if ref['expected'] in ('conflict','review'):
            checks.append({'facility_id':ref['facility_id'],'check':'conflict/review held',
                'pass':row['status'] in ('conflict','review','not_found') and all(p['decision']=='review' for p in row['proposals'])})
        if ref['expected']=='review': continue
        for field,expected in [('website',ref['website_domain']),('phone',ref['phones']),('state',ref['state']),('city',ref['city'])]:
            p=proposals.get(field); v=p['value'] if p else None
            passed=bool(v) and (domain(v) in [expected]+ref.get('website_alternatives',[]) if field=='website' else norm(v)[-10:] in expected if field=='phone' else norm(v)==norm(expected))
            checks.append({'facility_id':ref['facility_id'],'check':field,'actual':v,'pass':passed})
    return {'passed':sum(c['pass'] for c in checks),'total':len(checks),'checks':checks,
            'note':'Same ten-row development cohort, not a held-out accuracy estimate. Quotes and all additional fields require separate review.'}

if __name__=='__main__':
    out=Path('research-output')
    report=evaluate(json.loads((out/'results.json').read_text()),json.loads(Path('tests/reference/tako_basic/reference.json').read_text()))
    (out/'evaluation.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
