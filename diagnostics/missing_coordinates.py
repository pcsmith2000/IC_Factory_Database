"""Read-only aggregate audit. Row-level inputs stay in memory and are never exported."""
import json, re
from collections import Counter, defaultdict
from pathlib import Path
import os
import psycopg
from psycopg.rows import dict_row

def connection():
    url=next((os.environ[k] for k in ('DATABASE_URL_UNPOOLED','DATABASE_URL') if os.environ.get(k) and os.environ[k].isascii()),'')
    if not url:raise RuntimeError('No database connection available')
    return psycopg.connect(url,connect_timeout=20,row_factory=dict_row,
                          options='-c default_transaction_read_only=on -c statement_timeout=60000')
from pipeline.enrich.cache import locate_key, geocode_key, places_state_key
from pipeline.enrich.geocode import one_line
from pipeline.enrich.places import RELEASE
from pipeline.enrich.anchor import zip_agrees


def present(v):return bool(str(v or '').strip())

def decode(r):
    result=(r or {}).get('result')
    if isinstance(result,str):
        try:return json.loads(result)
        except ValueError:return {}
    return result or {}


def locate_failure(why):
    why=str(why).lower()
    if 'could not read' in why:return 'source_page_unreadable'
    if 'does not contain' in why:return 'address_not_on_cited_page'
    if 'confidence' in why:return 'low_confidence'
    if 'not a street address' in why:return 'not_a_street_address'
    if 'no citation' in why or 'did not visit' in why:return 'citation_failed'
    if 'not found' in why or 'cannot find' in why or 'could not find' in why:return 'not_found'
    return 'other_rejection'


def classify(g,facts,caches):
    applicable=[a for a in facts if a['release_tag']==g['release_tag'] or a.get('source_class') in ('enrichment','tako_ai_search')]
    coords=[a for a in applicable if a['field_key']=='lat_lon' and present(a['value'])]
    addresses=[a for a in applicable if a['field_key']=='address' and present(a['value'])]
    quality=[a for a in facts if a['field_key']=='geocode_quality']
    roofs=[a for a in facts if a['field_key']=='lat_lon' and a.get('basis')=='rooftop']
    loc=caches.get(locate_key(g.get('name'),g.get('city'),g.get('state')))
    geo=caches.get(geocode_key(one_line(g)))
    hits=(decode(geo).get('response') or {}).get('results') or []
    best=hits[0] if hits else {}
    accuracy=best.get('accuracy_type','no_result' if geo else 'no_current_address_cache')
    if accuracy not in ('rooftop','range_interpolation','street_center','nearest_rooftop_match','place','county','state','no_result','no_current_address_cache','intersection','point'):accuracy='other'
    has_address=present(g.get('address'))
    if coords:category='coordinate_assertion_not_in_golden'
    elif not has_address:
        if addresses:category='address_assertion_not_in_golden'
        elif loc and loc.get('found'):category='address_found_in_cache_not_in_golden'
        elif loc:category='address_search_rejected'
        else:category='no_address_no_saved_search'
    elif roofs:category='blocked_by_out_of_release_rooftop_flag'
    elif geo and accuracy=='rooftop':category='cached_rooftop_not_asserted'
    elif geo and accuracy in ('range_interpolation','street_center'):
        category='interpolated_postcode_matches_needs_building' if zip_agrees(g,best.get('formatted_address','')) else 'interpolated_postcode_missing_or_mismatch'
    elif geo:category='geocode_no_acceptable_result'
    elif quality:category='prior_failure_flag_without_current_address_cache'
    else:category='address_ready_no_saved_geocode'
    return dict(category=category,has_address=has_address,eligible=has_address and not roofs and not quality,
                accuracy=accuracy,has_quality=bool(quality),has_roof=bool(roofs),loc=loc,geo=geo,best=best,
                applicable=applicable,coords=coords,addresses=addresses)


def run():
    with connection() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        assert db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']=='on'
        totals=db.execute("SELECT count(*) AS total,count(*) FILTER (WHERE NULLIF(btrim(lat_lon),'') IS NOT NULL) AS with_coordinates,count(*) FILTER (WHERE NULLIF(btrim(lat_lon),'') IS NULL) AS missing_coordinates FROM golden_facility").fetchone()
        rows=db.execute("SELECT g.facility_key AS facility_id,g.release_tag,g.name,g.address,g.city,g.state,g.zip,g.address__source,g.name__source,d.tier FROM golden_facility g LEFT JOIN dim_facility d ON g.facility_key=d.facility_key WHERE NULLIF(btrim(g.lat_lon),'') IS NULL ORDER BY g.facility_key").fetchall()
        ids=[r['facility_id'] for r in rows]
        facts=db.execute('SELECT a.release_tag,a.facility_key,a.source_key,a.source_class,a.field_key,a.value,a.basis,a.date_key,a.asserted_at FROM fact_assertions a WHERE a.facility_key=ANY(%s) AND a.field_key=ANY(%s)',(ids,['name','address','city','state','zip','lat_lon','geocode_quality'])).fetchall()
        keys=set()
        for r in rows:keys.update((locate_key(r.get('name'),r.get('city'),r.get('state')),geocode_key(one_line(r))))
        cache_rows=db.execute("SELECT cache_key,kind,input,result,found,provider,fetched_at FROM cache_lookup WHERE cache_key=ANY(%s) OR kind IN ('places_state','geocode')",(sorted(keys),)).fetchall()
        events=[]
        if db.execute("SELECT to_regclass('pipeline_run_events') AS name").fetchone()['name']:
            events=db.execute("SELECT run_id,stage,state,updated_at,data FROM pipeline_run_events WHERE stage IN ('locate','geocode','anchor','places','load','promote') ORDER BY updated_at DESC LIMIT 350").fetchall()
        when=str(db.execute('SELECT current_timestamp AS now').fetchone()['now'])
    by_id=defaultdict(list)
    for f in facts:by_id[f['facility_key']].append(f)
    caches={r['cache_key']:r for r in cache_rows}
    counts=Counter();acc=Counter();reject=Counter();flags=Counter();states=defaultdict(Counter);tiers=Counter();promotion=Counter();postal=Counter();sources=Counter()
    cache_gaps=Counter();gap_quality=Counter();completeness=Counter();primary=defaultdict(Counter);reason_signals=Counter();places_gaps=Counter()
    def normalized_line(text,abbreviate=False):
        text=re.sub(r'\s+\d{5}(?:-\d{4})?\s*$','',text or '')
        words=re.findall(r'[a-z0-9]+',text.lower())
        aliases={'street':'st','road':'rd','avenue':'ave','boulevard':'blvd','drive':'dr','lane':'ln','court':'ct','highway':'hwy','parkway':'pkwy','trail':'trl','north':'n','south':'s','east':'e','west':'w','suite':'ste'}
        return ' '.join(aliases.get(w,w) if abbreviate else w for w in words)
    geos=[c for c in cache_rows if c['kind']=='geocode']
    no_postal={normalized_line(c.get('input')) for c in geos}
    abbreviations={normalized_line(c.get('input'),True) for c in geos}
    earliest_cache=min((str(c['fetched_at']) for c in geos),default='')
    for g in rows:
        d=classify(g,by_id[g['facility_id']],caches);cat=d['category'];counts[cat]+=1
        flags['has_address' if d['has_address'] else 'missing_address']+=1
        primary[g.get('name__source') or 'unattributed'][cat]+=1
        completeness[('address_present' if d['has_address'] else 'address_missing')+' / '+('city_present' if present(g['city']) else 'city_missing')+' / '+('state_present' if present(g['state']) else 'state_missing')]+=1
        if cat=='prior_failure_flag_without_current_address_cache':
            line=one_line(g)
            if normalized_line(line) in no_postal:cache_gaps['matches_saved_query_when_postcode_ignored']+=1
            elif normalized_line(line,True) in abbreviations:cache_gaps['matches_saved_query_with_abbreviation_and_postcode_normalization']+=1
            else:cache_gaps['no_matching_query_even_after_normalization']+=1
            qualities=[a for a in by_id[g['facility_id']] if a['field_key']=='geocode_quality']
            for value in {str(a['value']) for a in qualities}:
                gap_quality[value if value in ('range_interpolation','street_center','nearest_rooftop_match','place','state','no_result','point','county') else 'other']+=1
            if earliest_cache and any(str(a.get('asserted_at') or a.get('date_key') or '')<earliest_cache for a in qualities):cache_gaps['flag_predates_oldest_saved_geocode']+=1
            if any(a['release_tag']==g['release_tag'] for a in qualities):cache_gaps['has_current_release_quality_flag']+=1
            else:cache_gaps['quality_flags_only_in_older_releases']+=1
        if not d['has_address'] and d['loc']:
            why=str(decode(d['loc']).get('why','')).lower()
            signals={'ambiguous_or_insufficient_identity':('ambigu','insufficient','multiple','identify','unclear','not enough','missing'),
                     'no_verified_location':('not found','cannot','could not','unable','no verified','no reliable','no physical','no public','no street'),
                     'inactive_or_historical':('closed','defunct','historical','inactive','dissolv'),
                     'non_factory_contact':('retail','dealer','distributor','office','headquarter')}
            for label,words in signals.items():reason_signals[label]+=any(w in why for w in words)
        flags['eligible_for_default_geocode']+=d['eligible']
        flags['has_any_release_rooftop_flag']+=d['has_roof'];flags['has_any_release_quality_flag']+=d['has_quality']
        if not d['has_address']:
            flags['missing_address_and_city_or_state']+=not present(g['city']) or not present(g['state'])
            if d['loc']:reject[locate_failure(decode(d['loc']).get('why'))]+=1
        else:
            acc[d['accuracy']]+=1
            state=caches.get(places_state_key((g.get('state') or '').upper(),RELEASE))
            flags['address_rows_places_state_marked_done']+=bool(state)
            if state:
                address_facts=[a for a in d['applicable'] if a['field_key']=='address' and a.get('source_key')==g.get('address__source') and a['value']==g['address']]
                flags['address_asserted_after_places_state_cache']+=any(str(a.get('asserted_at') or '')>str(state['fetched_at']) for a in address_facts)
            else:
                flags['address_rows_places_state_not_done']+=1
                places_gaps['state_missing' if not present(g['state']) else ('two_letter_state' if len(g['state'])==2 else 'state_nonstandard')]+=1
        if d['accuracy'] in ('range_interpolation','street_center'):
            if not present(g['zip']):postal['missing_our_zip']+=1
            elif not zip_agrees(g,d['best'].get('formatted_address','')):postal['returned_zip_missing_or_different']+=1
            else:postal['zip_matches']+=1
        for a in d['coords']:promotion[a['source_key']+' / '+str(a.get('basis'))]+=1
        sources.update(set(a['source_key'] for a in d['applicable'] if a['release_tag']==g['release_tag']))
        state=(g.get('state') or '').upper();states[state if len(state)==2 and state.isalpha() else 'missing_or_nonstandard'][cat]+=1
        tier=g.get('tier');tiers[tier if tier in ('T0','T1','T2','T3') else 'other_or_missing']+=1
    safe_events=[]
    for e in events:
        data=e.get('data') or {}
        if data.get('target')!='main':continue
        numeric={k:v for k,v in data.items() if k in ('attempted','located','from_cache','deferred','requested','inserted','already_present','written','stored','halted','billed_lookups') and isinstance(v,(int,float,bool))}
        if numeric:safe_events.append(dict(run_id=e['run_id'],stage=e['stage'],state=e['state'],updated_at=str(e['updated_at']),metrics=numeric))
    summary={**totals,'queried_at':when,'by_diagnosis':dict(counts),'selection_flags':dict(flags),'geocode_accuracy_for_address_rows':dict(acc),
             'address_search_rejection_groups':dict(reject),'postcode_gate':dict(postal),
             'coordinate_assertions_missing_from_golden_by_source_basis':dict(promotion),
             'cache_gap_analysis':dict(cache_gaps),'cache_gap_quality_labels':dict(gap_quality),'address_city_state_completeness':dict(completeness),'locate_reason_signals_overlapping':dict(reason_signals),'places_unprocessed_state_groups':dict(places_gaps),'by_primary_name_source':{k:dict(v) for k,v in primary.items()},'by_state':{k:dict(v) for k,v in states.items()},'by_tier':dict(tiers),'current_sources_facility_counts':dict(sources),
             'recent_main_stage_metrics':safe_events[:50],'database_writes':0,'external_api_calls':0,
             'privacy':'Aggregate outputs only; no facility IDs, names, addresses, coordinates, raw assertions or cached evidence exported.'}
    assert sum(counts.values())==totals['missing_coordinates']==len(rows)
    out=Path('coordinate-audit');out.mkdir(exist_ok=True);(out/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps({k:v for k,v in summary.items() if k not in ('by_state','recent_main_stage_metrics')},indent=2))

if __name__=='__main__':run()
