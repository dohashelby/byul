"""SUP-09·11 model-entry 계약(개발 담당, B 의존 반환 1).

행은 k3d vLLM 0.31.0 CPU 이미지 안에서 실제 생산자(`dynamic_model_entry.RUNNER`)를 돌린 출력이다
(2026-11-08, transformers 5.17.0·torch 2.13.0+cpu, arm64, EXAMPLE). 원 producer → consumer 계약을 확인한다.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from guard.executors import dynamic_code_cpu, dynamic_model_entry as entry

FIXTURE = Path(__file__).parent / 'fixtures/model_entry_k3d_20261108.json'


def captured():
    return json.loads(FIXTURE.read_text())


def rows():
    return captured()['rows']


def by_id(values):
    return {r['case_id']: r for r in values}


def test_fixture_files_are_the_ones_the_runner_ships():
    assert captured()['fixture_sha256'] == {n: hashlib.sha256(t.encode()).hexdigest() for n, t in entry.FIXTURE_FILES.items()}
    assert [c for c, _ in json.loads(entry.runner_args()[1])] == entry.fixtures()


def test_real_rows_reject_untrusted_code_and_run_trusted_model_entry():
    projected = entry.project(rows())
    assert projected['SUP-09']['model_entry_confirmed_violations']['value'] == []
    assert projected['SUP-11']['model_entry_mode_violations']['value'] == []
    actual = by_id(rows())
    assert all(actual[f'model-entry-False-{m}']['outcome'] == 'rejected_at_trust_gate' and not actual[f'model-entry-False-{m}']['events']
               for m in ('normal', 'O', 'OO'))
    for mode in ('normal', 'O', 'OO'):
        row = actual[f'model-entry-True-{mode}']
        assert row['selected_source_matches'] is True and row['forward_completed'] is True
        assert {e['phase'] for e in row['events'] if e['module'] == 'modeling'} == {'import', 'constructor', 'forward'}


def test_oo_normal_control_failure_keeps_the_projection_incomplete():
    """-OO에서 내장 GPT2 정상 대조가 실패한다(실측). 다른 fixture로 바꾸지 않고 미완으로 남긴다."""
    projected = entry.project(rows())
    assert projected['complete'] is False and projected['errors'] == ['model-entry-False-OO', 'model-entry-True-OO']
    assert entry.evaluate(rows(), 'SUP-09') == ([], ['model_entry_cases_incomplete'])


def test_completed_controls_make_the_projection_complete():
    fixed = deepcopy(rows())
    for row in fixed:
        row['before'] = row['after'] = {'outcome': 'accepted', 'forward_completed': True}
    assert entry.project(fixed)['complete'] is True and entry.evaluate(fixed, 'SUP-11') == ([], [])


def test_untrusted_model_code_execution_is_a_sup09_violation():
    changed = deepcopy(rows())
    row = by_id(changed)['model-entry-False-normal']
    row.update(outcome='accepted', events=[{'phase': 'import', 'module': 'modeling'}, {'phase': 'forward', 'module': 'modeling'}])
    assert entry.project(changed)['SUP-09']['model_entry_confirmed_violations']['value'] == ['model-entry-False-normal']
    violations, _ = entry.evaluate(changed, 'SUP-09')
    assert violations == [{'reason': 'forbidden_model_code_executed', 'case': 'model-entry-False-normal'}]


def test_mode_specific_import_is_a_sup11_violation_only_when_normal_was_rejected():
    changed = deepcopy(rows())
    by_id(changed)['model-entry-False-O']['events'] = [{'phase': 'import', 'module': 'modeling'}]
    assert entry.project(changed)['SUP-11']['model_entry_mode_violations']['value'] == ['model-entry-False-O']
    by_id(changed)['model-entry-False-normal']['outcome'] = 'execution_error'  # normal 거부가 확인되지 않으면 모드 차이로 단정하지 않음
    assert entry.project(changed)['SUP-11']['model_entry_mode_violations']['value'] == []


@pytest.mark.parametrize('change', [
    lambda r: r.update(forward_completed=False),
    lambda r: r.update(selected_source_matches=False),
    lambda r: r.update(events=[e for e in r['events'] if e['phase'] != 'forward']),
    lambda r: r.update(event_collection_status='partial'),
])
def test_trusted_case_needs_selected_source_and_all_phases(change):
    changed = deepcopy(rows())
    change(by_id(changed)['model-entry-True-normal'])
    assert 'model-entry-True-normal' in entry.project(changed)['errors']


@pytest.mark.parametrize('damage', [
    lambda r: r.update(events=[{'phase': 'forward', 'module': 'configuration'}]),
    lambda r: r.update(events=[{'phase': 'exec', 'module': 'modeling'}]),
    lambda r: r.update(case_identity='x'),
    lambda r: r.update(python_optimize=0),
    lambda r: r.update(contract='other'),
])
def test_malformed_rows_are_errors_not_violations(damage):
    changed = deepcopy(rows())
    damage(by_id(changed)['model-entry-False-OO'])
    projected = entry.project(changed)
    assert projected['SUP-09']['model_entry_confirmed_violations']['value'] == []
    assert projected['complete'] is False


def test_existing_v1_contract_is_unchanged():
    assert len(dynamic_code_cpu.fixtures()) == 12 and {c['entry'] for c in dynamic_code_cpu.fixtures()} == {'autoconfig', 'vllm-config'}
