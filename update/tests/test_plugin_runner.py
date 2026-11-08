"""SUP-10 plugin runner 계약(개발 담당, B 의존 반환 2).

행은 k3d vLLM 0.31.0 CPU 이미지의 임시 생산자 Pod에서 실제 `EngineArgs → VllmConfig`·`renderer_from_config`·`get_io_processor`로
돌린 출력이다(2026-11-08, arm64, EXAMPLE, 모델 opt-125m). 원 producer → consumer 계약을 확인한다.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from guard.executors import plugin_loading, plugin_runner as runner

FIXTURE = Path(__file__).parent / 'fixtures/plugin_runner_k3d_20261108.json'


def captured():
    return json.loads(FIXTURE.read_text())


def rows():
    return captured()['rows']


def by_id(values):
    return {r['case_id']: r for r in values}


def test_fixture_files_are_the_ones_the_runner_ships():
    assert captured()['fixture_sha256'] == {n: hashlib.sha256(t.encode()).hexdigest() for n, t in runner.FIXTURE_FILES.items()}
    assert [c for c, _ in json.loads(runner.runner_args('/models/x')[1])] == runner.fixtures()
    with pytest.raises(ValueError):
        runner.runner_args('../x')


def test_real_selection_binds_model_config_request_and_effect():
    projected = runner.project(rows())
    assert projected['complete'] is True and projected['plugin_runner_confirmed_violations']['value'] == []
    row = by_id(rows())['io-config-approved']
    constructor = next(e for e in row['events'] if e['phase'] == 'constructor')
    assert constructor['model_sha256'] == row['selected_model_sha256'] and row['renderer_built'] is True
    pre = next(e for e in row['events'] if e['phase'] == 'io_preprocess')
    assert pre['request_sha256'] == row['request_sha256'] and pre['effect_sha256'] == row['preprocess_output_sha256']


def test_allowlist_blocks_the_unapproved_model_request_and_unset_allowlist_is_a_fact():
    actual = by_id(rows())
    assert actual['io-config-unapproved']['outcome'] == 'rejected_not_loadable'
    assert {e['plugin'] for e in actual['io-config-unapproved']['events']} == {'byul-approved'}
    assert runner.project(rows())['plugin_runner_facts'] == {'allowlist_unset_registrations': ['byul-approved', 'byul-unapproved']}
    assert actual['io-config-none']['events'] == [] and actual['io-config-none']['outcome'] == 'none_requested'


@pytest.mark.parametrize('case,change,reason', [
    ('io-config-unapproved', lambda r: r['events'].append({'plugin': 'byul-unapproved', 'phase': 'registration',
        'model_sha256': None, 'request_sha256': None, 'effect_sha256': None}), 'unapproved_plugin_code_executed'),
    ('io-config-unapproved', lambda r: r.update(selected_plugin='byul-unapproved'), 'unapproved_plugin_selected'),
    ('io-config-approved', lambda r: next(e for e in r['events'] if e['phase'] == 'constructor').update(model_sha256='0' * 64),
     'plugin_bound_to_other_model_config'),
    ('io-config-approved', lambda r: next(e for e in r['events'] if e['phase'] == 'io_postprocess').update(request_sha256='0' * 64),
     'io_effect_request_mismatch'),
    ('io-config-none', lambda r: r['events'].append({'plugin': 'byul-approved', 'phase': 'constructor',
        'model_sha256': r['selected_model_sha256'], 'request_sha256': None, 'effect_sha256': None}),
     'plugin_constructed_without_model_request'),
])
def test_violations_from_mutated_real_rows(case, change, reason):
    changed = deepcopy(rows())
    change(by_id(changed)[case])
    found = runner.project(changed)['plugin_runner_confirmed_violations']['value']
    assert {'case': case, 'reason': reason} in found
    violations, _ = runner.evaluate(changed)
    assert {'case': case, 'reason': reason} in violations


def test_unset_allowlist_registration_is_not_a_violation_by_itself():
    assert all(v['case'] != 'io-config-allowlist-unset' for v in runner.project(rows())['plugin_runner_confirmed_violations']['value'])


@pytest.mark.parametrize('change', [
    lambda r: r.update(renderer_built=False),
    lambda r: r.update(event_collection_status='partial'),
    lambda r: r.update(events=[e for e in r['events'] if e['phase'] != 'io_postprocess']),
    lambda r: r.update(preprocess_output_sha256='0' * 64),
    lambda r: r.update(outcome='execution_error'),
])
def test_incomplete_selected_case_is_an_error(change):
    changed = deepcopy(rows())
    change(by_id(changed)['io-config-approved'])
    projected = runner.project(changed)
    assert projected['complete'] is False and 'io-config-approved' in projected['errors']
    assert runner.evaluate(changed)[1] == ['plugin_runner_cases_incomplete']


@pytest.mark.parametrize('damage', [
    lambda r: r.update(events=[{'plugin': 'other', 'phase': 'registration', 'model_sha256': None, 'request_sha256': None, 'effect_sha256': None}]),
    lambda r: r.update(events=[{'plugin': 'byul-approved', 'phase': 'exec', 'model_sha256': None, 'request_sha256': None, 'effect_sha256': None}]),
    lambda r: r.update(case_identity='x'),
    lambda r: r.update(contract='other'),
])
def test_malformed_rows_are_errors_not_violations(damage):
    changed = deepcopy(rows())
    damage(by_id(changed)['io-config-unapproved'])
    projected = runner.project(changed)
    assert projected['plugin_runner_confirmed_violations']['value'] == [] and projected['complete'] is False


def test_existing_v1_plugin_contract_is_unchanged():
    assert len(plugin_loading.CASES) == 10 and plugin_loading.CASES[-1][0] == 'io-two-allowed-one-selected'
