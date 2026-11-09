"""Versioned worker coverage fixes; legacy /1 remains byte-compatible."""
from copy import deepcopy
import hashlib

import pytest

from guard.adapters import vllm_consumption_callback as callback
from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.collectors import scoped_consumption as scoped, gpu_common_inputs as legacy, engine_callbacks
from tests.test_gpu_input_producers import api_rows, POD, R1, SCOPE
from tests.test_runner_consumption import worker_rows


def native(processes):
    """Explicit EXAMPLE qualification metadata; never an engine measurement."""
    for process in processes:
        rows = []
        for index, raw in enumerate(process['events']):
            row = deepcopy(raw)
            module = callback.INPUT if row['point'].startswith('InputProcessor.') else callback.RUNNER
            row.update(process_sha256=hashlib.sha256(str(process['pid']).encode()).hexdigest(),
                       call_sha256=hashlib.sha256(str(index).encode()).hexdigest(),
                       entry_code_sha256='a' * 64, source_file_sha256=callback.cache.base.SOURCE_SHA[module],
                       session_sha256=hashlib.sha256(SCOPE['session'].encode()).hexdigest(), runtime_context_injected=False)
            if 'rank' in process:
                row['consumption_scope'] = {'rank': process['rank'], 'admissions': {R1: process.get('admission', 'b' * 64)}, 'pid': process['pid'], 'start_ticks': process['start_ticks']}
            else:
                row['consumption_scope'] = {'pid': process['pid'], 'start_ticks': process['start_ticks']}
            rows.append(row)
            # Existing API fixture pairs calls. Worker fixture explicitly holds
            # CALL-only rows; add its EXAMPLE successful terminal separately.
            if module == callback.RUNNER:
                rows.append(dict(row, phase='RETURN', fields={}, operation_finished_ns=row['operation_started_ns'] + 1))
        # Match the original API CALL to its terminal (fixture keys are cN).
        if process['pid'] == 1:
            calls = {}
            for row in rows:
                key = row['point']
                if row['phase'] == 'CALL': calls[key] = row['call_sha256']
                else: row['call_sha256'] = calls[key]
        process['events'] = rows
    return processes


EXAMPLE_SCOPE = {'engine': 'vllm', 'engine_version': '0.31.0', 'image_digest': 'EXAMPLE',
                 'backend': 'EXAMPLE', 'tp_size': 2, 'origin': 'EXAMPLE'}


def capture(**kwargs):
    processes = native([{'pid': 1, 'start_ticks': 1, 'events': api_rows()},
                        {'pid': 112, 'start_ticks': 2, 'rank': 0, 'events': worker_rows(logit_bias=0)},
                        {'pid': 113, 'start_ticks': 3, 'rank': 1, 'events': worker_rows(logit_bias=0)}])
    return scoped.record(processes, pod=POD, qualification_scope=EXAMPLE_SCOPE, expected_requests=[R1], expected_stage_ranks={'runner_received': [0, 1], 'sampler_applied': [0, 1]}, **SCOPE, **kwargs)


def register(monkeypatch):
    p = scoped.producer()
    monkeypatch.setitem(scoped.SUPPORTED_PRODUCERS, (p['name'], p['version']), p['source_sha256'])
    monkeypatch.setattr(scoped, 'QUALIFICATIONS', (scoped.Qualification('EXAMPLE', scoped.SCHEMA, p['source_sha256'], **EXAMPLE_SCOPE,
        stage_ranks=(('runner_received', (0, 1)), ('sampler_applied', (0, 1)))),))


def test_two_identical_workers_are_preserved_and_each_rank_has_closed_calls(monkeypatch):
    register(monkeypatch)
    record, result = capture()
    workers = [r for r in record['events'] if r['event'] in ('runner_received', 'sampler_applied')]
    assert len(workers) == 4
    assert {record['bindings'][r['binding']]['rank'] for r in workers} == {0, 1}
    assert result['complete'] and result['violation_candidates'] == []
    assert legacy.validate(record, kind='runner-consumption', **SCOPE) == result


@pytest.mark.parametrize('damage', ['missing-worker', 'missing-sampler', 'rank-unknown', 'attempt-mismatch', 'missing-plan', 'later-error'])
def test_missing_worker_rank_admission_and_source_end_cannot_be_complete(monkeypatch, damage):
    register(monkeypatch); value, _ = capture()
    if damage in ('missing-worker', 'missing-sampler'):
        value['events'] = [r for r in value['events'] if not (value['bindings'][r['binding']]['worker_pid'] == 113
                           and (damage == 'missing-worker' or r['event'] == 'sampler_applied'))]
        for index, row in enumerate(value['events']): row['seq'] = index
    elif damage == 'rank-unknown': value['bindings'][-1]['rank'] = None
    elif damage == 'attempt-mismatch': value['events'][-1]['admission_sha256'] = 'f' * 64
    elif damage == 'missing-plan': value['expected_stage_ranks'] = None
    else: value['completeness']['source_ended'] = False
    assert not scoped.validate(value, **SCOPE)['complete']


def test_changed_worker_option_survives_missing_another_rank_and_later_error(monkeypatch):
    register(monkeypatch); value, _ = capture()
    row = next(r for r in value['events'] if r['event'] == 'sampler_applied')
    row['top_k'] += 1
    value['completeness']['source_ended'] = False
    value['expected_stage_ranks']['sampler_applied'].append(2)
    result = scoped.validate(value, **SCOPE)
    assert not result['complete']
    assert any(v['field'] == 'top_k' for v in result['violation_candidates'])


def test_schema_v2_writer_does_not_use_mutable_legacy_schema(monkeypatch):
    identity = scoped.producer()
    monkeypatch.setattr(legacy, 'SCHEMA', 'guard-gpu-common-input/999')
    value, _ = capture()
    assert value['schema'] == 'guard-gpu-common-input/2' and value['producer'] == identity


def test_new_captures_are_unregistered_and_do_not_change_legacy_registration():
    before = deepcopy(legacy.SUPPORTED_PRODUCERS)
    _, result = capture()
    assert not result['supported'] and not result['complete']
    assert legacy.SUPPORTED_PRODUCERS == before and len(before) == 8


def test_prepared_sensor_selects_scoped_observers_without_overwriting_legacy_flag(tmp_path):
    prepared = engine_callbacks.prepare_assets(tmp_path / 'assets', scoped_runner_consumption=True)
    assert prepared['scoped_runner_consumption'] and not prepared['runner_consumption']
    callbacks = sensor.EngineCallbacks('a' * 32, b'k' * 32, lambda row: None, scoped_runner_consumption=True,
                                       context_provider=lambda: {'pid': 1, 'fixture': True})
    assert isinstance(callbacks.consumption, sensor.ScopedConsumptionObserver)
    with pytest.raises(ValueError, match='SCOPED_CONSUMPTION_EXCLUSIVE'):
        engine_callbacks.prepare_assets(tmp_path / 'bad', scoped_runner_consumption=True, runner_consumption=True)


def test_scoped_observer_preserves_actual_rank_and_changes_admission_only_after_native_return(monkeypatch):
    from guard.adapters import vllm_scoped_consumption_callback as native
    rows = []
    observer = native.ScopedConsumptionObserver('a' * 32, context_provider=lambda: {'EXAMPLE': True}, sink=rows.append)
    monkeypatch.setattr(native, '_tp_rank', lambda: 1)
    monkeypatch.setattr(native, '_start_ticks', lambda: 123)
    call = {'schema': callback.SCHEMA, 'point': 'GPUModelRunner._update_states', 'phase': 'CALL',
            'call_sha256': 'b' * 64, 'fields': {'received': [{'request_sha256': R1}]}}
    observer._emit(call)
    assert rows[-1]['consumption_scope']['admissions'] == {}
    observer._emit({**call, 'phase': 'RETURN', 'fields': {}})
    first = rows[-1]['consumption_scope']
    assert first['rank'] == 1 and first['start_ticks'] == 123 and first['admissions'][R1]
    observer._emit({**call, 'call_sha256': 'c' * 64})
    observer._emit({**call, 'call_sha256': 'c' * 64, 'phase': 'RAISE'})
    assert rows[-1]['consumption_scope']['admissions'][R1] == first['admissions'][R1]
    observer._emit({**call, 'call_sha256': 'd' * 64})
    observer._emit({**call, 'call_sha256': 'd' * 64, 'phase': 'RETURN'})
    assert rows[-1]['consumption_scope']['admissions'][R1] != first['admissions'][R1]


@pytest.mark.parametrize('damage', ['missing-request', 'narrowed-ranks', 'unknown-generation'])
def test_planned_scope_cannot_be_narrowed_to_the_rows_that_survived(monkeypatch, damage):
    register(monkeypatch)
    value, _ = capture()
    if damage == 'missing-request':
        value['expected_requests'].append('f' * 64)
    elif damage == 'narrowed-ranks':
        value['expected_stage_ranks']['runner_received'] = [0]
    else:
        value['bindings'][-1]['start_ticks'] = None
    assert not scoped.validate(value, **SCOPE)['complete']
