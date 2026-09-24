import json

import pytest

from pipeline.recovery.tako_select import choose, digest, verify_plan


def cfg(scope='missing_street_known_locality'):
    return {'limit': 10, 'offset': 0, 'seed': 'stable', 'states': [],
            'address_scope': scope, 'pass_id': 'p1', 'max_cost_usd': .5}


def rows():
    return [
        {'facility_id': 'IC-2', 'baseline': {'name': 'Needs Street', 'city': 'Phoenix', 'state': 'AZ'},
         'recovered_address': None, 'live_name': 'Needs Street', 'live_city': 'Phoenix', 'live_state': 'AZ', 'live_release': 'r1'},
        {'facility_id': 'IC-1', 'baseline': {'name': 'Complete', 'address': '10 Main St', 'city': 'Boston', 'state': 'MA'},
         'recovered_address': None, 'live_name': 'Complete', 'live_city': 'Boston', 'live_state': 'MA', 'live_release': 'r1'},
        {'facility_id': 'IC-3', 'baseline': {'name': 'Changed', 'city': 'Austin', 'state': 'TX'},
         'recovered_address': None, 'live_name': 'Other Name', 'live_city': 'Austin', 'live_state': 'TX', 'live_release': 'r1'},
    ]


def test_selection_is_stable_and_scoped_to_unchanged_unresolved_identity():
    first, eligible = choose(rows(), cfg())
    second, _ = choose(list(reversed(rows())), cfg())
    assert first == second
    assert eligible == 1 and first[0]['facility_id'] == 'IC-2'
    assert 'address' in first[0]['research_focus_fields']
    complete, eligible = choose(rows(), cfg('complete_address'))
    assert eligible == 1 and complete[0]['facility_id'] == 'IC-1'


def test_high_confidence_default_requires_known_city_state_and_missing_street():
    extra = rows() + [{'facility_id': 'IC-4', 'baseline': {'name': 'No Locality', 'phone': '5551234567'},
                       'recovered_address': None, 'live_name': 'No Locality', 'live_city': None,
                       'live_state': None, 'live_release': 'r1'}]
    selected, eligible = choose(extra, cfg())
    assert eligible == 1 and selected[0]['facility_id'] == 'IC-2'
    broad, eligible = choose(extra, cfg('needs_address'))
    assert eligible == 2 and {row['facility_id'] for row in broad} == {'IC-2', 'IC-4'}


def test_source_filter_selects_by_primary_name_source():
    extra = rows() + [{'facility_id': 'IC-5', 'baseline': {'name': 'ADL Plant', 'city': 'Walpole', 'state': 'NH',
                                                          'name__source': 'adl_july'},
                       'recovered_address': None, 'live_name': 'ADL Plant', 'live_city': 'Walpole',
                       'live_state': 'NH', 'live_release': 'r1'}]
    selected, eligible = choose(extra, {**cfg(), 'sources': ['adl_4ward', 'adl_july']})
    assert eligible == 1 and selected[0]['facility_id'] == 'IC-5'
    selected, eligible = choose(extra, {**cfg(), 'sources': []})
    assert eligible == 2


def test_research_requires_exact_unchanged_plan(tmp_path, monkeypatch):
    plan_dir = tmp_path / 'plan'; out = tmp_path / 'out'; plan_dir.mkdir()
    input_text = json.dumps([{'facility_id': 'IC-1'}], indent=2)
    (plan_dir / 'input.json').write_text(input_text)
    plan = {'destination': 'Vercel AI Gateway / Tako Search', 'database_writes': 0,
            'input_sha256': __import__('hashlib').sha256(input_text.encode()).hexdigest(),
            'estimated_cost': {'expected_range_usd': [0.01, 0.02]}, 'max_cost_usd': .5,
            'created_at': 'ignored'}
    sha = digest({k: v for k, v in plan.items() if k != 'created_at'})
    plan['plan_sha256'] = sha
    (plan_dir / 'plan.json').write_text(json.dumps(plan))
    github_env = tmp_path / 'github-env'
    monkeypatch.setenv('GITHUB_ENV', str(github_env))
    assert verify_plan(plan_dir, out, sha)['database_writes'] == 0
    assert github_env.read_text() == 'RESEARCH_MAX_COST_USD=0.5\n'
    (plan_dir / 'input.json').write_text(input_text + ' ')
    with pytest.raises(ValueError, match='input changed'):
        verify_plan(plan_dir, out, sha)
