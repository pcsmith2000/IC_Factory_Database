"""Reserve and settle Tako spend against the shared coordinate-recovery ceiling."""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

from pipeline.recovery.run import CAMPAIGN, connect, run_url
from pipeline.recovery.tako_select import verify_plan

DDL = """CREATE TABLE IF NOT EXISTS coordinate_recovery_external_spend (
 campaign_id TEXT NOT NULL REFERENCES coordinate_recovery_campaigns(campaign_id),
 provider TEXT NOT NULL, external_run_id TEXT NOT NULL, plan_run_id TEXT NOT NULL,
 plan_sha256 TEXT NOT NULL, reserved_usd NUMERIC NOT NULL, actual_usd NUMERIC,
 status TEXT NOT NULL, run_url TEXT NOT NULL, artifact_sha256 TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), settled_at TIMESTAMPTZ,
 PRIMARY KEY(campaign_id,provider,external_run_id))"""


def money(value):
    return Decimal(str(value)).quantize(Decimal('0.000001'))


def settlement_delta(reserved, actual):
    """Amount to add to campaign spend when replacing a reservation with actual cost."""
    return money(actual) - money(reserved)


def _receipt(out, name, value):
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(json.dumps(value, indent=2, default=str))
    print(json.dumps(value, indent=2, default=str))
    return value


def reserve(plan_dir: Path, expected_sha: str, external_run_id: str, out: Path):
    plan = verify_plan(plan_dir, out, expected_sha)
    cap = money(plan['max_cost_usd'])
    if not str(external_run_id).isdigit():
        raise ValueError('Exact external workflow run ID required')
    with connect() as db:
        db.execute(DDL)
        db.execute('SELECT pg_advisory_xact_lock(73941668)')
        campaign = db.execute('SELECT * FROM coordinate_recovery_campaigns WHERE campaign_id=%s FOR UPDATE', (CAMPAIGN,)).fetchone()
        if not campaign:
            raise RuntimeError('Coordinate recovery campaign does not exist')
        existing = db.execute("SELECT * FROM coordinate_recovery_external_spend WHERE campaign_id=%s AND provider='vercel_tako' AND external_run_id=%s",
                              (CAMPAIGN, external_run_id)).fetchone()
        inserted = 0
        if existing:
            if existing['plan_sha256'] != expected_sha or money(existing['reserved_usd']) != cap:
                raise RuntimeError('Existing reservation belongs to another reviewed plan')
        else:
            if money(campaign['reserved_usd']) + cap > money(campaign['api_ceiling']):
                raise RuntimeError('Shared campaign budget guard stopped before external research')
            db.execute("""INSERT INTO coordinate_recovery_external_spend
                       (campaign_id,provider,external_run_id,plan_run_id,plan_sha256,reserved_usd,status,run_url)
                       VALUES(%s,'vercel_tako',%s,%s,%s,%s,'reserved',%s)""",
                       (CAMPAIGN, external_run_id, str(plan.get('workflow')), expected_sha, cap, run_url()))
            db.execute('UPDATE coordinate_recovery_campaigns SET reserved_usd=reserved_usd+%s WHERE campaign_id=%s', (cap, CAMPAIGN))
            inserted = 1
        current = db.execute('SELECT reserved_usd,api_ceiling FROM coordinate_recovery_campaigns WHERE campaign_id=%s', (CAMPAIGN,)).fetchone()
    return _receipt(out, 'budget-reservation.json', {
        'campaign_id': CAMPAIGN, 'provider': 'vercel_tako', 'external_run_id': int(external_run_id),
        'plan_sha256': expected_sha, 'reserved_usd': float(cap), 'new_reservation': bool(inserted),
        'campaign_reserved_usd': float(current['reserved_usd']),
        'campaign_ceiling_usd': float(current['api_ceiling']), 'assertion_writes': 0, 'golden_writes': 0,
    })


def settle(research_dir: Path, external_run_id: str, out: Path):
    summary_path = research_dir / 'summary.json'
    manifest_path = research_dir / 'run-manifest.json'
    plan_path = research_dir / 'approved-plan.json'
    summary = json.loads(summary_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    plan = json.loads(plan_path.read_text())
    if str(manifest.get('run_id')) != str(external_run_id):
        raise ValueError('Research artifact belongs to another workflow run')
    actual_value = summary.get('usage', {}).get('reported_cost_usd')
    artifact_sha = hashlib.sha256(summary_path.read_bytes() + manifest_path.read_bytes() + plan_path.read_bytes()).hexdigest()
    with connect() as db:
        db.execute(DDL)
        db.execute('SELECT pg_advisory_xact_lock(73941668)')
        spend = db.execute("SELECT * FROM coordinate_recovery_external_spend WHERE campaign_id=%s AND provider='vercel_tako' AND external_run_id=%s FOR UPDATE",
                           (CAMPAIGN, external_run_id)).fetchone()
        if not spend or spend['plan_sha256'] != plan.get('plan_sha256'):
            raise RuntimeError('Matching research budget reservation not found')
        changed = False
        over_cap = False
        if actual_value is not None:
            actual = money(actual_value)
            over_cap = actual > money(spend['reserved_usd'])
            if spend['status'] in ('settled', 'over_cap'):
                if money(spend['actual_usd']) != actual or spend.get('artifact_sha256') != artifact_sha:
                    raise RuntimeError('Settled cost record differs from this artifact')
            else:
                delta = settlement_delta(spend['reserved_usd'], actual)
                db.execute('UPDATE coordinate_recovery_campaigns SET reserved_usd=reserved_usd+%s WHERE campaign_id=%s', (delta, CAMPAIGN))
                db.execute("""UPDATE coordinate_recovery_external_spend SET actual_usd=%s,status=%s,
                           artifact_sha256=%s,settled_at=now() WHERE campaign_id=%s AND provider='vercel_tako' AND external_run_id=%s""",
                           (actual, 'over_cap' if over_cap else 'settled', artifact_sha, CAMPAIGN, external_run_id))
                changed = True
        current = db.execute('SELECT reserved_usd,api_ceiling FROM coordinate_recovery_campaigns WHERE campaign_id=%s', (CAMPAIGN,)).fetchone()
    result = _receipt(out, 'budget-settlement.json', {
        'campaign_id': CAMPAIGN, 'provider': 'vercel_tako', 'external_run_id': int(external_run_id),
        'actual_usd': float(money(actual_value)) if actual_value is not None else None,
        'reservation_retained_for_missing_cost': actual_value is None,
        'over_cap': over_cap, 'settlement_changed': changed,
        'campaign_reserved_usd': float(current['reserved_usd']),
        'campaign_ceiling_usd': float(current['api_ceiling']), 'assertion_writes': 0, 'golden_writes': 0,
    })
    if over_cap:
        raise RuntimeError('Actual research cost exceeded its reviewed cap; accurate spend was retained')
    return result

