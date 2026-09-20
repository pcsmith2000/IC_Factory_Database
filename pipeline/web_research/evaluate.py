"""Post-run reference comparison; this module is never used to construct search prompts."""
import json
import os
from pathlib import Path
from .run import domain, norm

def evaluate(results, reference):
    checks=[]
    by_id={r['facility_id']:r for r in results}
    for ref in reference['rows']:
        row=by_id.get(ref['facility_id'])
        if not row:
            row={'proposals':[], 'status':'error'}
        proposals={p['field']:p for p in row['proposals']}
        if ref['expected'] in ('conflict','review'):
            checks.append({'facility_id':ref['facility_id'],'check':'conflict/review held',
                'pass':row['status'] in ('conflict','review','not_found') and all(p['decision']=='review' for p in row['proposals'])})
        if ref['expected']=='review': continue
        for field,expected in [('website',ref['website_domain']),('phone',ref['phones']),('state',ref['state']),('city',ref['city'])]:
            p=proposals.get(field); v=p['value'] if p else None
            passed=bool(v) and (domain(v) in [expected]+ref.get('website_alternatives',[]) if field=='website' else norm(v)[-10:] in expected if field=='phone' else norm(v)==norm(expected))
            checks.append({'facility_id':ref['facility_id'],'check':field,'actual':v,'pass':passed,'evidence_verified':bool(p and p.get('quote_verified'))})
    hazard_checks=[c for c in checks if c['check']=='conflict/review held']
    coverage={f:sum(c['pass'] for c in checks if c['check']==f) for f in ('website','phone')}
    bad_candidates=[]
    indexed={(c['facility_id'],c['check']):c for c in checks}
    for row in results:
        for proposal in row['proposals']:
            if proposal['decision']!='candidate': continue
            check=indexed.get((row['facility_id'],proposal['field']))
            if not proposal['quote_verified'] or (check and not check['pass']):
                bad_candidates.append({'facility_id':row['facility_id'],'field':proposal['field']})
    gates={'all_ten_completed':len(by_id)==len(reference['rows']) and set(by_id)=={r['facility_id'] for r in reference['rows']},
           'all_identity_hazards_held':all(c['pass'] for c in hazard_checks),
           'no_incorrect_or_unsupported_scored_candidates':not bad_candidates,
           'at_least_six_correct_websites':coverage['website']>=6,
           'at_least_six_correct_plant_phones':coverage['phone']>=6}
    return {'quality_pass':all(gates.values()),'quality_gates':gates,'coverage':coverage,'bad_candidates':bad_candidates,
            'passed':sum(c['pass'] for c in checks),'total':len(checks),'checks':checks,
            'note':'Same ten-row development cohort, not a held-out accuracy estimate. Quotes and all additional fields require separate review.'}

if __name__=='__main__':
    out=Path('research-output')
    report=evaluate(json.loads((out/'results.json').read_text()),json.loads(Path('tests/reference/tako_basic/reference.json').read_text()))
    (out/'evaluation.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        lines=['## Tako read-only pilot', f"Reference checks: {report['passed']}/{report['total']}. Quality gate: {'PASS' if report['quality_pass'] else 'FAIL'}. Database writes: 0.", '', '| Facility | Website | Phone | Status |', '|---|---|---|---|']
        for row in json.loads((out/'results.json').read_text()):
            fields={p['field']:p['value'] for p in row['proposals']}
            lines.append('| '+ ' | '.join(str(v or 'Unverified').replace('|','/') for v in [row['name'],fields.get('website'),fields.get('phone'),row['status']])+' |')
        lines+=['', 'Review-only values are not approved assertions. See the artifact for evidence, scopes, and field-level decisions.']
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f: f.write('\n'.join(lines)+'\n')
    if not report['quality_pass']: raise SystemExit('Pilot quality gate failed; inspect evaluation.json')
