import hashlib
import json
from pathlib import Path

import pytest

from pipeline.recovery.tako_address import bundles
from pipeline.recovery.tako_manifest import build
from pipeline.recovery.tako_select import DESTINATION, digest


def proposal(field, value, quote, scope='facility'):
    return {'field': field, 'value': value, 'source_url': 'https://acme.example/locations',
            'quote': quote, 'scope': scope, 'source_kind': 'official',
            'relationship': 'fill' if field == 'address' else 'corroborates',
            'decision': 'review', 'quote_verified': True, 'identity_anchor_found': True,
            'name_anchor_found': True, 'location_anchor_found': True}


def research_artifact(root: Path, *, status='matched', bad_scope=False):
    rows = [{'facility_id': 'IC-1', 'name': 'Acme Factory', 'address': None,
             'city': 'Boston', 'state': 'MA', 'zip': '02101',
             'website': 'https://acme.example'}]
    text = json.dumps(rows, indent=2)
    (root / 'input.json').write_text(text)
    plan={'input_sha256': hashlib.sha256(text.encode()).hexdigest(), 'max_cost_usd': .5,
          'destination': DESTINATION, 'commit': 'abc', 'created_at': 'ignored'}
    plan['plan_sha256']=digest({k:v for k,v in plan.items() if k!='created_at'})
    (root / 'approved-plan.json').write_text(json.dumps(plan))
    (root / 'run-manifest.json').write_text(json.dumps({'run_id': '123', 'database_writes': 0,
        'input_sha256': hashlib.sha256(text.encode()).hexdigest(), 'started_at': '2026-09-21T12:00:00Z', 'commit':'abc'}))
    (root / 'summary.json').write_text(json.dumps({'database_writes': 0, 'errors': [], 'completed': 1,
        'selected': 1, 'source':'tako_ai_search', 'mode':'research', 'usage': {'reported_cost_usd': .01}}))
    proposals = [proposal('website', 'https://acme.example', 'Acme Factory', 'company'),
                 proposal('address', '10 Main St', 'Acme Factory 10 Main St Boston MA 02101', 'office' if bad_scope else 'facility'),
                 proposal('city', 'Boston', '10 Main St Boston MA 02101'),
                 proposal('state', 'MA', '10 Main St Boston MA 02101'),
                 proposal('zip', '02101', '10 Main St Boston MA 02101')]
    (root / 'results.json').write_text(json.dumps([{'facility_id': 'IC-1', 'status': status, 'proposals': proposals}]))


def test_strict_bundle_becomes_low_priority_manifest(tmp_path):
    research_artifact(tmp_path)
    manifest, report = build(tmp_path, tmp_path / 'out', '123')
    assert report['facilities_approved'] == 1
    assert {a['field'] for a in manifest['assertions']} == {'address', 'city', 'state', 'zip'}
    assert all(a['approved'] and a['expected_identity']['name'] == 'Acme Factory' for a in manifest['assertions'])
    assert set(bundles(manifest)) == {'IC-1'}


@pytest.mark.parametrize('kwargs,reason', [({'status': 'review'}, 'result_not_matched'),
                                            ({'bad_scope': True}, 'address_not_facility_scope')])
def test_ambiguous_or_nonfacility_results_are_held(tmp_path, kwargs, reason):
    research_artifact(tmp_path, **kwargs)
    manifest, report = build(tmp_path, tmp_path / 'out', '123')
    assert not manifest['assertions']
    assert report['rejected_reasons'][reason] == 1


def test_incomplete_or_over_budget_research_is_rejected(tmp_path):
    research_artifact(tmp_path)
    summary = json.loads((tmp_path / 'summary.json').read_text())
    summary['usage']['reported_cost_usd'] = .6
    (tmp_path / 'summary.json').write_text(json.dumps(summary))
    with pytest.raises(ValueError, match='cost'):
        build(tmp_path, tmp_path / 'out', '123')

def test_mailing_zip_and_small_city_typo_can_be_replaced(tmp_path):
    research_artifact(tmp_path)
    rows=json.loads((tmp_path/'input.json').read_text())
    rows[0].update(address='P.O. Box 7',city='Remond',state='OR',zip='97701')
    text=json.dumps(rows,indent=2)
    (tmp_path/'input.json').write_text(text)
    plan=json.loads((tmp_path/'approved-plan.json').read_text())
    plan['input_sha256']=hashlib.sha256(text.encode()).hexdigest()
    plan['plan_sha256']=digest({k:v for k,v in plan.items() if k not in ('created_at','plan_sha256')})
    (tmp_path/'approved-plan.json').write_text(json.dumps(plan))
    run=json.loads((tmp_path/'run-manifest.json').read_text())
    run['input_sha256']=hashlib.sha256(text.encode()).hexdigest()
    (tmp_path/'run-manifest.json').write_text(json.dumps(run))
    results=json.loads((tmp_path/'results.json').read_text())
    by_field={p['field']:p for p in results[0]['proposals']}
    by_field['address'].update(value='2405 SW 1st St',quote='Acme Factory 2405 SW 1st St Redmond OR 97756')
    by_field['city'].update(value='Redmond',quote='2405 SW 1st St Redmond OR 97756',relationship='correction')
    by_field['state'].update(value='OR',quote='2405 SW 1st St Redmond OR 97756')
    by_field['zip'].update(value='97756',quote='2405 SW 1st St Redmond OR 97756')
    (tmp_path/'results.json').write_text(json.dumps(results))
    manifest,report=build(tmp_path,tmp_path/'out','123')
    assert report['facilities_approved']==1
    assert {a['value'] for a in manifest['assertions']} >= {'2405 SW 1st St','Redmond','OR','97756'}
