"""Actual CPU V1 dispatch rows, plus explicitly mutated incomplete/violation cases."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.collectors import engine_callbacks
from guard.collectors import gpu_common_inputs as contract
from guard.collectors import transition_dispatch_producer as producer

FIXTURE = Path(__file__).parent / 'fixtures/transition_dispatch_k3d_20261108.json'
POD = {'pod_uid': 'pod-example', 'container_id': 'containerd://' + 'b' * 64, 'restart_count': 0}


def capture():
    return json.loads(FIXTURE.read_text())


def scope():
    return {'environment_ref': {'kind': 'ENV', 'id': 'ENV-EXAMPLE', 'revision': 1, 'sha256': 'a' * 64},
            'target_sha256': 'c' * 64, 'prepared_sha256': 'd' * 64, 'session': capture()['session']}


def build(rows=None, **kw):
    return producer.record(capture()['rows'] if rows is None else rows, pod=POD, **scope(), **kw)


def register(monkeypatch):
    p = producer.producer()
    monkeypatch.setitem(contract.SUPPORTED_PRODUCERS, (p['name'], p['version']), p['source_sha256'])


def test_actual_requests_reach_model_dispatch_and_keep_the_native_reference():
    value, result = build()
    assert capture()['data_origin'] == 'EXAMPLE'
    assert 'GPU_INPUT_PRODUCER_UNSUPPORTED' in result['gaps']
    assert 'GPU_INPUT_PRODUCER_DIAGNOSTIC:TRANSITION_DISPATCH_SOURCE_GAP' in result['gaps']
    assert result['violation_candidates'] == []
    assert {r['event'] for r in value['events']} == {'forward_entered', 'forward_returned'}
    assert len({r['request_sha256'] for r in value['events']}) == 2
    assert all(r['native_call_sha256'] and r['native_process_sha256'] for r in value['events'])
    assert len(value['bindings']) == 1 and value['bindings'][0]['worker_pid'] > 1
    projected = producer.projection(value)
    assert projected['actual_weights_cache_consumption_verified'] is False
    assert projected['missing'] == list(producer.LIMITATIONS)
    assert projected['dispatch_facts'][0]['worker'] == value['bindings'][0]


def test_registered_closed_dispatches_are_complete_only_for_the_dispatch_scope(monkeypatch):
    register(monkeypatch)
    # Explicit derived case without the real run's global GAP rows.
    rows = [r for r in capture()['rows'] if r['phase'] != 'GAP']
    value, result = build(rows)
    assert result['complete'] is True and result['producer_authenticated'] is False
    assert producer.projection(value)['actual_weights_cache_consumption_verified'] is False


def test_registration_does_not_erase_the_real_runs_source_gaps(monkeypatch):
    register(monkeypatch)
    _, result = build()
    assert result['supported'] is True and result['complete'] is False
    assert 'GPU_INPUT_PRODUCER_DIAGNOSTIC:TRANSITION_DISPATCH_SOURCE_GAP' in result['gaps']


@pytest.mark.parametrize('damage', ['source', 'session', 'entry', 'terminal-entry', 'injected'])
def test_unqualified_rows_leave_a_gap_and_cannot_complete(monkeypatch, damage):
    register(monkeypatch)
    rows = capture()['rows']
    target = next(r for r in rows if r['phase'] == ('RETURN' if damage == 'terminal-entry' else 'CALL') and r['fields']['requests'])
    if damage == 'source':
        target['source_file_sha256'] = 'f' * 64
    elif damage == 'session':
        target['session_sha256'] = 'f' * 64
    elif damage == 'injected':
        target['runtime_context_injected'] = True
    else:
        target['entry_qualified_after' if damage == 'terminal-entry' else 'entry_qualified_before'] = False
    value, result = build(rows)
    assert not result['complete'] and not value['completeness']['source_ended']
    assert 'GPU_INPUT_PRODUCER_DIAGNOSTIC:TRANSITION_DISPATCH_ENTRY_UNQUALIFIED' in result['gaps']


def test_model_change_within_one_attempt_survives_later_collection_failure():
    rows = capture()['rows']
    target = next(r for r in rows if r['phase'] == 'RETURN' and r['fields']['requests'])
    target['fields']['model_sha256'] = 'f' * 64
    value, result = build(rows, source_ended=False)
    assert result['violation_candidates'][0]['reason'] == 'dispatch_identity_changed_within_attempt'
    assert 'GPU_INPUT_SOURCE_INCOMPLETE' in result['gaps']
    assert producer.projection(value)['actual_weights_cache_consumption_verified'] is False


def test_later_malformed_rows_and_process_mismatch_preserve_independent_candidates():
    rows = capture()['rows']
    target = next(r for r in rows if r['phase'] == 'RETURN' and r['fields']['requests'])
    target['fields']['model_sha256'] = 'f' * 64
    changed = deepcopy(target)
    changed['process_sha256'] = 'f' * 64
    malformed = deepcopy(target)
    malformed['fields'] = ['invalid']
    rows.extend([changed, malformed, None])
    value, result = build(rows)
    assert result['violation_candidates'][0]['reason'] == 'dispatch_identity_changed_within_attempt'
    assert 'TRANSITION_DISPATCH_PROCESS_MISMATCH' in value['completeness']['diagnostic_codes']
    assert 'TRANSITION_DISPATCH_ROW_INVALID' in value['completeness']['diagnostic_codes']


@pytest.mark.parametrize('damage', ['missing-terminal', 'duplicate-terminal', 'late-call', 'different-attempt'])
def test_incomplete_or_mismatched_call_pairs_do_not_complete(monkeypatch, damage):
    register(monkeypatch)
    value, _ = build()
    rows = value['events']
    terminal = next(r for r in rows if r['event'] == 'forward_returned')
    if damage == 'missing-terminal':
        rows.remove(terminal)
    elif damage == 'duplicate-terminal':
        rows.append(deepcopy(terminal))
    elif damage == 'late-call':
        terminal['at_ms'] = 0
    else:
        terminal['attempt_sha256'] = 'f' * 64
    for index, row in enumerate(rows):
        row['seq'] = index
    result = contract.validate(value, kind='transition-dispatch', **scope())
    assert not result['complete'] and 'GPU_INPUT_DISPATCH_CALL_UNPAIRED' in result['gaps']


def test_a_failed_forward_is_not_a_successful_dispatch_or_rollback():
    rows = capture()['rows']
    for row in rows:
        if row['phase'] == 'RETURN':
            row['phase'] = 'RAISE'
    value, result = build(rows)
    assert 'GPU_INPUT_REQUIRED_EVENT_MISSING:forward_returned' in result['gaps']
    assert 'forward_failed' in {r['event'] for r in value['events']}
    assert producer.projection(value)['actual_weights_cache_consumption_verified'] is False


def test_missing_process_identity_cannot_make_a_worker_binding():
    rows = capture()['rows']
    for row in rows:
        if 'fields' in row:
            row['fields']['start_ticks'] = None
    with pytest.raises(ValueError, match='TRANSITION_DISPATCH_NO_WORKER_IDENTITY'):
        build(rows)


def test_prepared_assets_keep_dispatch_observation_opt_in(tmp_path):
    manifest = engine_callbacks.prepare_assets(tmp_path / 'on', transition_dispatches=True)
    assert manifest['transition_dispatches'] is True
    assert 'guard_engine_callbacks/vllm_transition_dispatch_callback.py' in manifest['assets']
    assert engine_callbacks.prepare_assets(tmp_path / 'off')['transition_dispatches'] is False
