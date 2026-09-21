"""Freeze and verify a repeatable Tako search plan for unresolved rooftop rows."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from pipeline.recovery.run import CAMPAIGN, connect
from pipeline.web_research.costs import estimate
from pipeline.web_research.run import EXTRACT_MODEL, MODEL, norm

PUBLIC_FIELDS = ('name', 'address', 'city', 'state', 'zip', 'website', 'phone', 'email')
DESTINATION = 'Vercel AI Gateway / Tako Search'


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def settings():
    cfg = {
        'limit': int(os.environ.get('ROW_LIMIT', '10')),
        'offset': int(os.environ.get('ROW_OFFSET', '0')),
        'seed': os.environ.get('SAMPLE_SEED', '20260921').strip(),
        'states': [v.strip().upper() for v in os.environ.get('STATES', '').split(',') if v.strip()],
        'address_scope': os.environ.get('ADDRESS_SCOPE', 'needs_address').strip(),
        'pass_id': os.environ.get('PASS_ID', 'tako-address-1').strip(),
        'max_cost_usd': float(os.environ.get('MAX_COST_USD', '0.50')),
    }
    if not 1 <= cfg['limit'] <= 100 or cfg['offset'] < 0:
        raise ValueError('row_limit must be 1..100 and row_offset must be nonnegative')
    if cfg['address_scope'] not in ('needs_address', 'complete_address', 'all'):
        raise ValueError('address_scope must be needs_address, complete_address, or all')
    if any(not re.fullmatch(r'[A-Z]{2}', state) for state in cfg['states']):
        raise ValueError('states must be comma-separated two-letter codes')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', cfg['pass_id']):
        raise ValueError('pass_id must be a short stable identifier')
    if not 0 < cfg['max_cost_usd'] <= 9.50:
        raise ValueError('max_cost_usd must be positive and no more than the campaign ceiling')
    return cfg


def complete_address(row):
    return bool(re.match(r'^\d+[A-Za-z]?\s', str(row.get('address') or '').strip()) and
                str(row.get('city') or '').strip() and re.fullmatch(r'[A-Za-z]{2}', str(row.get('state') or '').strip()))


def choose(rows, cfg):
    eligible = []
    for raw in rows:
        baseline = dict(raw['baseline'])
        baseline.update(raw.get('recovered_address') or {})
        if norm(baseline.get('name')) != norm(raw.get('live_name')):
            continue
        if any(baseline.get(k) and norm(baseline.get(k)) != norm(raw.get('live_' + k)) for k in ('city', 'state')):
            continue
        if cfg['states'] and str(baseline.get('state') or '').upper() not in cfg['states']:
            continue
        is_complete = complete_address(baseline)
        if cfg['address_scope'] == 'needs_address' and is_complete:
            continue
        if cfg['address_scope'] == 'complete_address' and not is_complete:
            continue
        row = {'facility_id': raw['facility_id'], 'release_tag': raw['live_release']}
        row.update({field: baseline.get(field) for field in PUBLIC_FIELDS})
        row['research_focus_fields'] = [field for field in ('address', 'city', 'state', 'zip', 'website', 'phone')
                                        if not str(row.get(field) or '').strip()]
        if not row['research_focus_fields']:
            row['research_focus_fields'] = ['address', 'city', 'state', 'zip']
        row['research_round'] = 1
        eligible.append(row)
    eligible.sort(key=lambda row: (hashlib.md5((row['facility_id'] + cfg['seed']).encode()).hexdigest(), row['facility_id']))
    return eligible[cfg['offset']:cfg['offset'] + cfg['limit']], len(eligible)


def create_plan(out: Path):
    cfg = settings()
    with connect() as db:
        db.read_only = True
        db.execute('SET TRANSACTION READ ONLY')
        rows = db.execute("""SELECT c.facility_id,c.baseline,c.recovered_address,
                                    g.name AS live_name,g.city AS live_city,g.state AS live_state,
                                    g.release_tag AS live_release
                             FROM coordinate_recovery_rows c
                             JOIN golden_facility g ON g.facility_key=c.facility_id
                             WHERE c.campaign_id=%s AND c.status='unresolved'
                             ORDER BY c.facility_id""", (CAMPAIGN,)).fetchall()
        campaign = db.execute('SELECT frozen_count,reserved_usd,api_ceiling FROM coordinate_recovery_campaigns WHERE campaign_id=%s',
                              (CAMPAIGN,)).fetchone()
    if not campaign:
        raise RuntimeError('Coordinate recovery campaign does not exist')
    remaining = float(campaign['api_ceiling'] - campaign['reserved_usd'])
    if cfg['max_cost_usd'] > remaining:
        raise RuntimeError('Requested cost cap exceeds the campaign allowance remaining before this run')
    selected, eligible_count = choose(rows, cfg)
    if not selected:
        raise RuntimeError('Selection matched zero unresolved rows')
    out.mkdir(parents=True, exist_ok=True)
    input_text = json.dumps(selected, indent=2, default=str)
    (out / 'input.json').write_text(input_text)
    cost = estimate(len(selected), (MODEL, EXTRACT_MODEL), out)
    if cost['expected_range_usd'][1] > cfg['max_cost_usd']:
        raise RuntimeError('Estimated upper cost exceeds max_cost_usd; reduce row_limit or raise the explicit cap')
    plan = {
        'campaign_id': CAMPAIGN,
        'pass_id': cfg['pass_id'],
        'selection': {k: v for k, v in cfg.items() if k != 'max_cost_usd'},
        'selected': len(selected),
        'eligible_count': eligible_count,
        'frozen_count': campaign['frozen_count'],
        'campaign_geocoding_reserved_usd': float(campaign['reserved_usd']),
        'campaign_ceiling_usd': float(campaign['api_ceiling']),
        'max_cost_usd': cfg['max_cost_usd'],
        'estimated_cost': cost,
        'destination': DESTINATION,
        'external_fields': list(PUBLIC_FIELDS),
        'input_sha256': hashlib.sha256(input_text.encode()).hexdigest(),
        'database_read_only': True,
        'database_writes': 0,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'commit': os.environ.get('GITHUB_SHA'),
        'workflow': os.environ.get('GITHUB_RUN_ID'),
    }
    plan['plan_sha256'] = digest({k: v for k, v in plan.items() if k not in ('created_at', 'plan_sha256')})
    (out / 'plan.json').write_text(json.dumps(plan, indent=2))
    print(json.dumps({k: plan[k] for k in ('campaign_id', 'pass_id', 'selected', 'eligible_count', 'max_cost_usd', 'input_sha256', 'plan_sha256', 'database_writes')}, indent=2))
    return plan


def verify_plan(plan_dir: Path, out: Path, expected_sha: str):
    plan = json.loads((plan_dir / 'plan.json').read_text())
    input_text = (plan_dir / 'input.json').read_text()
    calculated = digest({k: v for k, v in plan.items() if k not in ('created_at', 'plan_sha256')})
    if not re.fullmatch(r'[0-9a-f]{64}', expected_sha or '') or calculated != expected_sha or plan.get('plan_sha256') != expected_sha:
        raise ValueError('Exact plan SHA256 is required and must match the downloaded plan')
    if hashlib.sha256(input_text.encode()).hexdigest() != plan.get('input_sha256'):
        raise ValueError('Plan input changed after review')
    if plan.get('destination') != DESTINATION or plan.get('database_writes') != 0:
        raise ValueError('Unexpected plan destination or write policy')
    if plan['estimated_cost']['expected_range_usd'][1] > plan['max_cost_usd']:
        raise ValueError('Reviewed plan exceeds its cost cap')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'input.json').write_text(input_text)
    (out / 'approved-plan.json').write_text(json.dumps(plan, indent=2))
    if os.environ.get('GITHUB_ENV'):
        with open(os.environ['GITHUB_ENV'], 'a') as env:
            env.write(f"RESEARCH_MAX_COST_USD={plan['max_cost_usd']}\n")
    return plan
