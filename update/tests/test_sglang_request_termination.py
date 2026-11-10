"""Owned source-bound facts; CPU fixtures do not represent GPU acceptance."""
from copy import deepcopy
import hashlib
import json

import pytest

from guard.adapters import sglang_request_termination as evidence
from guard.adapters import sglang_request_termination_sensor as sensor

G, N, I = (hashlib.sha256(v).hexdigest() for v in (b'gateway', b'native', b'invocation'))


def native(point, *, phase='RETURN', present=None, kv=None, registered=None, mamba=None, operation=1):
    return {'point': point, 'phase': phase, 'operation': operation,
            'source_module': evidence.POINT_MODULE[point], 'source_verified': True,
            'asgi_request_sha256': G if point.startswith('TokenizerManager.') else None,
            'asgi_invocation_sha256': I if point.startswith('TokenizerManager.') else None,
            'qualified_single_scheduler': point.startswith('Scheduler.'),
            'requests': [{'request_sha256': N, 'state_present': present,
                          'kv_registered': registered, 'kv_allocated': kv, 'mamba_present': mamba}]}


def recording(pid, values):
    context = {'pid': pid, 'start_ticks': pid * 11, 'uid': 2000, 'boot_sha256': 'b' * 64,
               'time_namespace': 5, 'time_offsets_sha256': 'a' * 64}
    rows = [{'event': 'START', 'monotonic_ns': 0, 'payload': {
        'profile': sensor.PROFILE, 'schema': sensor.SCHEMA, 'commit': sensor.COMMIT, 'context': context}}]
    rows += [{'event': 'MODULE', 'monotonic_ns': 0, 'payload': {
        'module': module, 'verified': True, 'source_sha256': sha, 'code_sha256': 'c' * 64}}
        for module, sha in sensor.SOURCE_SHA.items()]
    rows += [{'event': event, 'monotonic_ns': ms * 1_000_000, 'payload': value}
             for ms, event, value in values]
    rows.append({'event': 'END', 'monotonic_ns': 200_000_000, 'payload': {'reason': 'SEALED'}})
    return {'pid': pid, 'start_ticks': pid * 11, 'session_owner_match': True,
            'listed_file_identity_match': True, 'capture': {'records': rows,
            'summary': {'recording_closed': True, 'recording_end': 'SEALED',
                        'incomplete_final_row': False, 'discarded_tail_bytes': 0},
            'process_context_verified': True, 'image_digest_verified': True}}


def packet():
    front = [(1, 'SG_NATIVE', native('TokenizerManager._init_req_state', present=True)),
             (100, 'SG_ASGI', {'phase': 'DISCONNECT_RECEIVED', 'request_sha256': G, 'invocation_sha256': I}),
             (160, 'SG_NATIVE', native('TokenizerManager._handle_batch_output', present=False, operation=2)),
             (170, 'SG_ASGI', {'phase': 'APP_RETURN', 'request_sha256': G, 'invocation_sha256': I})]
    gpu = [(110, 'SG_NATIVE', native('Scheduler.abort_request', present=True)),
           (120, 'SG_NATIVE', native('common.release_kv_cache', kv=0, registered=False, mamba=False, operation=2)),
           (125, 'SG_NATIVE', native('Scheduler.process_batch_result', present=True, operation=3)),
           (130, 'SG_NATIVE', native('ScheduleBatch.filter_batch', present=False, operation=4)),
           (150, 'SG_NATIVE', native('Scheduler.on_idle', present=False, operation=5))]
    gpu += [(140 + i, 'SG_GPU_FENCE', {'operation': 3, 'stream': stream, 'requests': [N],
            'completed': True, 'timed_out': False, 'qualified_single_scheduler': True})
            for i, stream in enumerate(sorted(evidence.STREAMS))]
    gpu.sort(key=lambda r: r[0])
    return {'live_census_stable': True, 'recorded_files_covered': True,
            'before': {'metadata': {'sealed': True}}, 'after': {'metadata': {'sealed': True}},
            'recordings': [recording(11, front), recording(22, gpu)]}


def project(value, **kwargs):
    return evidence.project_native(value, G, kwargs.get('max_delay_ms', 1000), kwargs.get('required_scopes', evidence.SCOPES))


def row(value, point):
    return next(r for capture in value['recordings'] for r in capture['capture']['records']
                if r['event'] == 'SG_NATIVE' and r['payload']['point'] == point)


def test_complete_source_bound_fixture_retains_unknown_security():
    raw = packet(); before = json.dumps(raw, sort_keys=True)
    result = project(raw)
    assert result['binding_observed'] and result['native_termination_verified']
    assert result['worker_gpu_release_verified'] and all(s['termination_verified'] for s in result['scope_status'])
    assert result['diagnostic_codes'] == []
    assert result['security'] == 'UNKNOWN' and result['producer_authenticated'] is False
    assert json.dumps(raw, sort_keys=True) == before


@pytest.mark.parametrize('fault', ['coverage', 'census', 'physical', 'image', 'clock', 'source', 'module-point',
    'state', 'kv', 'mamba', 'one-stream', 'gpu-timeout', 'future-operation', 'mode', 'observer-gap', 'extra-field'])
def test_no_positive_verdict_with_missing_or_contradictory_boundary(fault):
    value = packet(); gpu = value['recordings'][1]['capture']['records']
    if fault == 'coverage': value['recorded_files_covered'] = False
    elif fault == 'census': value['live_census_stable'] = False
    elif fault == 'physical': value['recordings'][1]['listed_file_identity_match'] = False
    elif fault == 'image': value['recordings'][1]['capture']['image_digest_verified'] = False
    elif fault == 'clock': gpu[0]['payload']['context']['time_namespace'] = 99
    elif fault == 'source':
        next(r for r in gpu if r['event'] == 'MODULE')['payload']['source_sha256'] = '0' * 64
        # Alter the module actually used by a selected state observation.
        module = row(value, 'Scheduler.on_idle')['payload']['source_module']
        next(r for r in gpu if r['event'] == 'MODULE' and r['payload']['module'] == module)['payload']['verified'] = False
    elif fault == 'module-point': row(value, 'Scheduler.on_idle')['payload']['source_module'] = 'sglang.srt.runtime_context'
    elif fault == 'state': row(value, 'Scheduler.on_idle')['payload']['requests'][0]['state_present'] = True
    elif fault == 'kv': row(value, 'common.release_kv_cache')['payload']['requests'][0]['kv_allocated'] = 1
    elif fault == 'mamba': row(value, 'common.release_kv_cache')['payload']['requests'][0]['mamba_present'] = True
    elif fault == 'one-stream': gpu.remove(next(r for r in gpu if r['event'] == 'SG_GPU_FENCE'))
    elif fault == 'gpu-timeout':
        r = next(r for r in gpu if r['event'] == 'SG_GPU_FENCE'); r['payload'].update(completed=False, timed_out=True)
    elif fault == 'future-operation':
        r = deepcopy(row(value, 'Scheduler.process_batch_result')); r['monotonic_ns'] = 190_000_000
        r['payload']['operation'] = 9; gpu.append(r)
    elif fault == 'mode': row(value, 'Scheduler.on_idle')['payload']['qualified_single_scheduler'] = False
    elif fault == 'observer-gap': gpu.append({'event': 'SG_GAP', 'monotonic_ns': 155_000_000,
                                           'payload': {'code': 'GPU_FENCE_UNAVAILABLE'}})
    elif fault == 'extra-field': row(value, 'Scheduler.on_idle')['payload']['secret'] = 'must-never-appear'
    result = project(value)
    assert result['native_termination_verified'] is False
    assert result['security'] == 'UNKNOWN'
    assert 'must-never-appear' not in json.dumps(result)


def test_deadline_is_not_replaced_by_next_request_success():
    result = project(packet(), max_delay_ms=20)
    assert result['binding_observed'] is True
    assert result['native_termination_verified'] is False


@pytest.mark.parametrize('scopes', [None, [], ['unknown'], [None], 'scheduler_state'])
def test_invalid_required_scope_is_unverified(scopes):
    assert project(packet(), required_scopes=scopes)['native_termination_verified'] is False


def test_duplicate_selection_is_unverified():
    value = packet(); value['recordings'][0]['capture']['records'].append(deepcopy(row(value, 'TokenizerManager._init_req_state')))
    assert project(value)['binding_observed'] is False


def test_late_disconnect_after_app_return_is_unverified():
    value = packet()
    r = next(r for r in value['recordings'][0]['capture']['records'] if r['event'] == 'SG_ASGI' and r['payload']['phase'] == 'APP_RETURN')
    r['monotonic_ns'] = 50_000_000
    assert project(value)['binding_observed'] is False


def test_diagnostic_scope_selection_cannot_weaken_complete_native_result():
    value = packet()
    value['recordings'][1]['capture']['records'] = [r for r in value['recordings'][1]['capture']['records']
                                                  if r['event'] != 'SG_GPU_FENCE']
    result = project(value, required_scopes={'asgi_transport'})
    assert result['native_termination_verified'] is False
    assert result['worker_gpu_release_verified'] is False


@pytest.mark.parametrize('fault', ['missing-session', 'open-before', 'open-after',
    'missing-summary', 'open-summary', 'bound-reached', 'process-exit', 'control-unavailable',
    'incomplete-tail', 'tail-bytes', 'missing-end', 'forged-end', 'after-end'])
def test_unsealed_or_incomplete_capture_preserves_observations_without_completion(fault):
    value = packet(); capture = value['recordings'][1]['capture']
    if fault == 'missing-session': value.pop('before')
    elif fault == 'open-before': value['before']['metadata']['sealed'] = False
    elif fault == 'open-after': value['after']['metadata']['sealed'] = False
    elif fault == 'missing-summary': capture.pop('summary')
    elif fault == 'open-summary': capture['summary']['recording_closed'] = False
    elif fault in {'bound-reached', 'process-exit', 'control-unavailable'}:
        reason = fault.replace('-', '_').upper()
        capture['summary']['recording_end'] = reason
        capture['records'][-1]['payload']['reason'] = reason
    elif fault == 'incomplete-tail': capture['summary']['incomplete_final_row'] = True
    elif fault == 'tail-bytes': capture['summary']['discarded_tail_bytes'] = 1
    elif fault == 'missing-end': capture['records'].pop()
    elif fault == 'forged-end': capture['records'][-1]['payload']['reason'] = 'BOUND_REACHED'
    elif fault == 'after-end': capture['records'].append(deepcopy(capture['records'][-2]))
    result = project(value)
    assert result['binding_observed'] is True
    assert result['events']
    assert result['native_termination_verified'] is False
    assert result['worker_gpu_release_verified'] is False
    assert not any(s['termination_verified'] for s in result['scope_status'])
