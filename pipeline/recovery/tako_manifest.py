"""Turn one completed Tako recovery artifact into a strictly gated address manifest."""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from pipeline.web_research.campaign import normalized_detail
from pipeline.web_research.run import domain
from pipeline.recovery.tako_select import DESTINATION, digest

LOCATION = ('address', 'city', 'state', 'zip')


def _read(path):
    return json.loads(Path(path).read_text())


def _input_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _strict_bundle(row, result):
    reasons = []
    if result.get('status') != 'matched':
        reasons.append('result_not_matched')
    proposals = {p['field']: p for p in result.get('proposals', [])}
    required = ('address', 'city', 'state')
    if any(field not in proposals for field in required):
        reasons.append('incomplete_location_bundle')
        return None, reasons
    bundle = {field: proposals[field] for field in LOCATION if field in proposals}
    if not re.match(r'^\d+[A-Za-z]?\s', str(bundle['address'].get('value') or '').strip()):
        reasons.append('not_physical_street')
    if re.search(r'\bP\.?\s*O\.?\s+Box\b', str(bundle['address'].get('value') or ''), re.I):
        reasons.append('postal_box')
    if not re.fullmatch(r'[A-Za-z]{2}', str(bundle['state'].get('value') or '').strip()):
        reasons.append('invalid_state')
    if 'zip' in bundle and not re.fullmatch(r'\d{5}(?:-\d{4})?', str(bundle['zip'].get('value') or '').strip()):
        reasons.append('invalid_zip')
    for field, proposal in bundle.items():
        if proposal.get('scope') != 'facility': reasons.append(f'{field}_not_facility_scope')
        if not proposal.get('quote_verified'): reasons.append(f'{field}_quote_not_verified')
        if not proposal.get('identity_anchor_found'): reasons.append(f'{field}_identity_not_anchored')
        if not proposal.get('name_anchor_found'): reasons.append(f'{field}_name_not_anchored')
        if not proposal.get('location_anchor_found'): reasons.append(f'{field}_location_not_anchored')
        if proposal.get('relationship') == 'conflict': reasons.append(f'{field}_conflicts')
        if proposal.get('source_kind') not in ('official', 'registry'): reasons.append(f'{field}_source_not_authoritative')
    existing_city = row.get('city')
    existing_state = row.get('state')
    if existing_city and normalized_detail('city', existing_city) != normalized_detail('city', bundle['city']['value']):
        reasons.append('city_changed')
    if existing_state and normalized_detail('state', existing_state) != normalized_detail('state', bundle['state']['value']):
        reasons.append('state_changed')
    if row.get('zip') and 'zip' in bundle and normalized_detail('zip', row['zip']) != normalized_detail('zip', bundle['zip']['value']):
        reasons.append('zip_changed')
    website = proposals.get('website', {}).get('value') or row.get('website')
    official = domain(website)
    for field, proposal in bundle.items():
        host = domain(proposal.get('source_url'))
        trusted = (proposal.get('source_kind') == 'registry' and host.endswith('.gov')) or (
            proposal.get('source_kind') == 'official' and official and host == official)
        if not trusted: reasons.append(f'{field}_source_domain_unverified')
    if reasons:
        return None, sorted(set(reasons))
    return bundle, []


def build(research_dir: Path, out: Path, research_run: str):
    approved_plan = _read(research_dir / 'approved-plan.json')
    run_manifest = _read(research_dir / 'run-manifest.json')
    summary = _read(research_dir / 'summary.json')
    rows = _read(research_dir / 'input.json')
    results = _read(research_dir / 'results.json')
    if not re.fullmatch(r'\d+', str(research_run)):
        raise ValueError('Exact research run ID required')
    calculated_plan_sha = digest({k: v for k, v in approved_plan.items() if k not in ('created_at', 'plan_sha256')})
    if approved_plan.get('plan_sha256') != calculated_plan_sha or approved_plan.get('destination') != DESTINATION:
        raise ValueError('Reviewed selection plan is invalid or targets another destination')
    if str(run_manifest.get('run_id')) != str(research_run) or run_manifest.get('database_writes') != 0:
        raise ValueError('Research manifest does not match the requested read-only run')
    if run_manifest.get('commit') != approved_plan.get('commit'):
        raise ValueError('Research code commit differs from the reviewed selection plan')
    if run_manifest.get('input_sha256') != _input_digest(research_dir / 'input.json'):
        raise ValueError('Research input digest mismatch')
    if approved_plan.get('input_sha256') != run_manifest.get('input_sha256'):
        raise ValueError('Research did not use the reviewed selection')
    if summary.get('source') != 'tako_ai_search' or summary.get('mode') != 'research' or summary.get('database_writes') != 0 or summary.get('errors') or summary.get('completed') != summary.get('selected'):
        raise ValueError('Research run is incomplete or violated its read-only contract')
    actual = summary.get('usage', {}).get('reported_cost_usd')
    if actual is None or actual > approved_plan['max_cost_usd']:
        raise ValueError('Research cost is missing or exceeded the reviewed cap')
    by_id = {row['facility_id']: row for row in rows}
    if len(by_id) != len(rows) or {r['facility_id'] for r in results} != set(by_id):
        raise ValueError('Research results do not exactly cover the reviewed cohort')
    assertions = []
    rejected = Counter()
    retrieved = str(run_manifest.get('started_at') or date.today().isoformat())[:10]
    for result in sorted(results, key=lambda r: r['facility_id']):
        row = by_id[result['facility_id']]
        bundle, reasons = _strict_bundle(row, result)
        if not bundle:
            rejected.update(reasons or ['no_strict_bundle'])
            continue
        expected = {field: row.get(field) for field in ('name', 'city', 'state') if row.get(field)}
        for field in LOCATION:
            proposal = bundle.get(field)
            if not proposal:
                continue
            assertions.append({
                'facility_id': result['facility_id'], 'field': field,
                'value': proposal['value'].strip(), 'approved': True,
                'source_url': proposal['source_url'], 'quote': proposal['quote'],
                'scope': 'facility', 'retrieved_date': retrieved,
                'expected_identity': expected, 'source_run': int(research_run),
                'evidence_rounds': [1],
                'review_note': 'Strict automatic Tako facility-address bundle: matched result, unchanged locality, exact fetched quote, facility scope, and official or government source domain.',
            })
    manifest = {
        'source_id': 'tako_ai_search',
        'approval': 'strict_automatic_address_bundle_v1',
        'campaign_runs': [int(research_run)],
        'research_input_sha256': run_manifest['input_sha256'],
        'assertions': assertions,
    }
    report = {
        'research_run': int(research_run), 'selected': len(rows),
        'facilities_approved': len({a['facility_id'] for a in assertions}),
        'assertions_approved': len(assertions), 'rejected_reasons': dict(rejected),
        'actual_cost_usd': actual, 'database_writes': 0,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / 'approved-assertions.json').write_text(json.dumps(manifest, indent=2))
    (out / 'manifest-report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return manifest, report
