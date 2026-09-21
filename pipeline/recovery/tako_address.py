"""Idempotently hand reviewed Tako address assertions to rooftop recovery."""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

from pipeline.recovery.run import CAMPAIGN, connect
from pipeline.web_research.campaign import normalized_detail
from pipeline.web_research.publish import load_manifest


def bundles(manifest):
    grouped = defaultdict(dict)
    for item in manifest['assertions']:
        if item['field'] in ('address', 'city', 'state', 'zip'):
            grouped[item['facility_id']][item['field']] = item
    return {fid: fields for fid, fields in grouped.items()
            if all(field in fields for field in ('address', 'city', 'state'))}


def run(mode: str, manifest_path: Path, out: Path):
    manifest = load_manifest(manifest_path)
    grouped = bundles(manifest)
    if not grouped:
        raise ValueError('No complete approved facility address bundles')
    accepted = []
    skipped = []
    with connect() as db:
        if mode == 'plan':
            db.execute('SET TRANSACTION READ ONLY')
        for fid, fields in sorted(grouped.items()):
            row = db.execute("""SELECT c.baseline,c.status,g.name,g.city,g.state,g.zip,g.release_tag
                                FROM coordinate_recovery_rows c
                                JOIN golden_facility g ON g.facility_key=c.facility_id
                                WHERE c.campaign_id=%s AND c.facility_id=%s""", (CAMPAIGN, fid)).fetchone()
            reason = None
            expected = fields['address'].get('expected_identity', {})
            if not row or row['status'] != 'unresolved':
                reason = 'row_missing_or_already_recovered'
            elif any(normalized_detail(field, row.get(field)) != normalized_detail(field, value)
                     for field, value in expected.items()):
                reason = 'live_identity_changed'
            evidence = []
            if not reason and mode == 'apply':
                for field, item in fields.items():
                    saved = db.execute("""SELECT a.value,r.source_url,r.source_document,a.assertion_id
                                          FROM fact_assertions a JOIN ref_source_row r ON r.row_hash=a.row_hash
                                          WHERE a.facility_key=%s AND a.release_tag=%s AND a.source_key='tako_ai_search'
                                            AND a.field_key=%s AND a.value=%s""",
                                       (fid, row['release_tag'], field, item['value'])).fetchone()
                    if saved and saved['source_url'] == item['source_url']:
                        evidence.append({'source_key': 'tako_ai_search', 'field': field,
                                         'value': item['value'], 'source_url': saved['source_url'],
                                         'source_document': saved['source_document'],
                                         'assertion_id': saved['assertion_id']})
                    elif field == 'address' or normalized_detail(field, row.get(field)) != normalized_detail(field, item['value']):
                        reason = 'published_assertion_missing_or_changed'; break
            if reason:
                skipped.append({'facility_id': fid, 'reason': reason})
                continue
            recovered = {field: item['value'] for field, item in fields.items()}
            recovered['_source'] = 'tako_ai_search'
            recovered['_research_runs'] = manifest['campaign_runs']
            if mode == 'apply':
                recovered['_evidence'] = evidence
                from psycopg.types.json import Jsonb
                db.execute("UPDATE coordinate_recovery_rows SET recovered_address=%s WHERE campaign_id=%s AND facility_id=%s AND status='unresolved'",
                           (Jsonb(recovered), CAMPAIGN, fid))
            accepted.append({'facility_id': fid, 'fields': sorted(fields)})
    result = {'mode': mode, 'eligible_address_bundles': len(accepted), 'skipped': skipped,
              'campaign_rows_updated': len(accepted) if mode == 'apply' else 0,
              'golden_writes': 0}
    out.mkdir(parents=True, exist_ok=True)
    (out / f'address-sync-{mode}.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    if mode == 'apply' and skipped:
        raise RuntimeError('Some reviewed address bundles could not be handed to rooftop recovery')
    return result
