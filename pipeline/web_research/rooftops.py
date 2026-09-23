"""Geocode explicitly reviewed address records; outputs files only, never database writes."""
from __future__ import annotations
import argparse
import json
import os
import re
from pathlib import Path
from .run import norm
from .campaign import normalized_detail
from pipeline.enrich.geocode import _post, one_line
from pipeline.recovery.streets import street_equivalent


def validate(rows):
    if not rows or len(rows)>241:raise ValueError('Expected 1..241 reviewed addresses')
    if len({r['facility_id'] for r in rows})!=len(rows):raise ValueError('Duplicate facility')
    for r in rows:
        if not r.get('reviewed') or not r.get('evidence_url') or not r.get('review_note'):
            raise ValueError('Each address requires explicit review with evidence and rationale')
        if not all(r.get(k) for k in ('facility_id','address','city','state')):
            raise ValueError('Incomplete facility address')
        if 'pobox' in norm(r['address']):raise ValueError('Mailing address cannot locate a plant')


def street_rule(address, components, parcel=False):
    """The rule under which the source street and the geocoder's street are the same street, or
    the reason they are not — see pipeline.recovery.streets.  A verbatim comparison rejected 85
    rooftop results in the first campaign pass on spellings like "Hwy 231" / "US-231"."""
    street=re.sub(r'^\d+[a-zA-Z]?\s*','',address).strip()
    street=re.split(r'(?i)\b(?:suite|ste|unit|bldg|building)\b|#',street,maxsplit=1)[0].strip()
    unit=str(components.get('unit_number') or '')
    if unit:
        street=re.sub(r'\s+'+re.escape(unit)+r'$','',street).strip()
    returned=components.get('formatted_street')
    if not returned:return False,'missing_street'
    if normalized_detail('address',street)==normalized_detail('address',returned):return True,'exact'
    return street_equivalent(street,returned,parcel=parcel)


def street_matches(address, components, parcel=False):
    return street_rule(address, components, parcel=parcel)[0]


def run(rows, out, prior=None):
    validate(rows)
    out.mkdir(parents=True,exist_ok=True)
    cache={}
    if prior:
        for p in Path(prior).rglob('geocode-cache.json'):cache.update(json.loads(p.read_text()))
    queries={}
    for r in rows:queries.setdefault(one_line(r),[]).append(r)
    fresh=[q for q in queries if q not in cache]
    estimate={'addresses':len(rows),'unique_addresses':len(queries),'cached':len(queries)-len(fresh),
              'new_lookups':len(fresh),'cost_if_free_allowance_spent_usd':round(len(fresh)*.001,3),
              'pricing':'https://www.geocod.io/pricing','database_writes':0}
    (out/'geocode-estimate.json').write_text(json.dumps(estimate,indent=2));print(json.dumps(estimate),flush=True)
    if fresh and not os.environ.get('GEOCODIO_API_KEY'):raise ValueError('Geocodio credential is unavailable; no API calls made')
    for start in range(0,len(fresh),100):
        batch=fresh[start:start+100]
        results,stopped=_post(batch,os.environ['GEOCODIO_API_KEY'])
        for query,result in zip(batch,results):cache[query]=result
        (out/'geocode-cache.json').write_text(json.dumps(cache,indent=2))
        if stopped or len(results)!=len(batch):raise RuntimeError('Incomplete geocoding; cached completed lookups for resume')
    (out/'geocode-cache.json').write_text(json.dumps(cache,indent=2))
    results=[]
    for query,facilities in queries.items():
        hits=(cache[query].get('response') or {}).get('results') or []
        best=hits[0] if hits else {}
        for r in facilities:
            components=best.get('address_components') or {}
            number=re.match(r'\d+[a-zA-Z]?',r['address'])
            address_matches=(norm(components.get('state_province') or components.get('state'))==norm(r['state']) and
                             norm(components.get('city'))==norm(r['city']) and
                             number is not None and norm(components.get('number'))==norm(number.group()) and
                             street_matches(r['address'],components))
            rooftop=best.get('accuracy_type')=='rooftop' and address_matches
            results.append({**r,'query':query,'accuracy_type':best.get('accuracy_type','no_result'),
                'accuracy':best.get('accuracy'),'dataset':best.get('source'),'address_matches':address_matches,
                'returned_address':best.get('formatted_address'),
                'coordinates':best.get('location') if rooftop else None,
                'decision':'rooftop_candidate' if rooftop else 'review',
                'database_writes':0})
    (out/'rooftop-results.json').write_text(json.dumps(results,indent=2))
    (out/'rooftop-summary.json').write_text(json.dumps({**estimate,'rooftop_candidates':sum(r['coordinates'] is not None for r in results),'unresolved':sum(r['coordinates'] is None for r in results)},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--out',default='rooftop-output');p.add_argument('--prior');a=p.parse_args()
    run(json.loads(Path(a.input).read_text()),Path(a.out),a.prior)
