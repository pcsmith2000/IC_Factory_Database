from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

import pytest

from pipeline.recovery import tako_budget
from pipeline.recovery.tako_budget import money, settlement_delta
from pipeline.recovery.tako_select import DESTINATION, digest


def test_budget_money_is_decimal_and_settlement_releases_unused_reservation():
    assert money(.5) == Decimal('0.500000')
    assert settlement_delta(.5, .15) == Decimal('-0.350000')
    assert settlement_delta(.5, .6) == Decimal('0.100000')


def test_postgres_reservation_and_settlement_are_idempotent(monkeypatch, tmp_path):
    uri = os.environ.get('TEST_DATABASE_URL')
    if not uri:
        pytest.skip('Postgres integration runs in CI')
    import psycopg
    from psycopg.rows import dict_row

    campaign = 'test-tako-budget'
    external_run = '999123'
    monkeypatch.setattr(tako_budget, 'CAMPAIGN', campaign)
    monkeypatch.setattr(tako_budget, 'connect', lambda: psycopg.connect(uri, row_factory=dict_row))
    with psycopg.connect(uri) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS coordinate_recovery_campaigns (
                    campaign_id TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    api_ceiling NUMERIC NOT NULL, reserved_usd NUMERIC NOT NULL DEFAULT 0,
                    frozen_count INTEGER NOT NULL, freeze_run TEXT NOT NULL)""")
        spend_table = db.execute("SELECT to_regclass('coordinate_recovery_external_spend')").fetchone()[0]
        if spend_table:
            db.execute('DELETE FROM coordinate_recovery_external_spend WHERE campaign_id=%s', (campaign,))
        db.execute('DELETE FROM coordinate_recovery_campaigns WHERE campaign_id=%s', (campaign,))
        db.execute('INSERT INTO coordinate_recovery_campaigns(campaign_id,api_ceiling,reserved_usd,frozen_count,freeze_run) VALUES(%s,9.5,.65,1,%s)', (campaign, 'test'))

    plan_dir = tmp_path / 'plan'; plan_dir.mkdir()
    input_text = json.dumps([{'facility_id': 'IC-1'}], indent=2)
    (plan_dir / 'input.json').write_text(input_text)
    plan = {'max_cost_usd': .5, 'destination': DESTINATION, 'database_writes': 0,
            'estimated_cost': {'expected_range_usd': [.1, .3]}, 'workflow': '123',
            'input_sha256': hashlib.sha256(input_text.encode()).hexdigest(), 'created_at': 'ignored'}
    plan['plan_sha256'] = digest({k: v for k, v in plan.items() if k != 'created_at'})
    (plan_dir / 'plan.json').write_text(json.dumps(plan))
    first = tako_budget.reserve(plan_dir, plan['plan_sha256'], external_run, tmp_path / 'reserve')
    second = tako_budget.reserve(plan_dir, plan['plan_sha256'], external_run, tmp_path / 'reserve-2')
    assert first['new_reservation'] and not second['new_reservation']
    assert second['campaign_reserved_usd'] == 1.15

    research = tmp_path / 'research'; research.mkdir()
    (research / 'summary.json').write_text(json.dumps({'usage': {'reported_cost_usd': .15}}))
    (research / 'run-manifest.json').write_text(json.dumps({'run_id': external_run}))
    (research / 'approved-plan.json').write_text(json.dumps(plan))
    settled = tako_budget.settle(research, external_run, tmp_path / 'settled')
    repeated = tako_budget.settle(research, external_run, tmp_path / 'settled-2')
    assert settled['settlement_changed'] and not repeated['settlement_changed']
    assert repeated['campaign_reserved_usd'] == .8

    with psycopg.connect(uri) as db:
        db.execute('DELETE FROM coordinate_recovery_external_spend WHERE campaign_id=%s', (campaign,))
        db.execute('DELETE FROM coordinate_recovery_campaigns WHERE campaign_id=%s', (campaign,))
