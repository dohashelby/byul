"""lab 활성화 시나리오 계약. kubectl 대역만 쓰며 실제 클러스터는 없다."""
from copy import deepcopy
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from guard.executors import lab_activation as lab
from guard.executors import registry
from guard.models import DataOrigin, ExecStatus, canonical_json
from guard.models.objects import Observation

BASE = {'namespace': 'lab', 'selector': 'app=vllm', 'container': 'engine', 'probe_pod': 'probe', 'probe_container': 'probe',
    'service_url': 'http://vllm.lab.svc:8000', 'model': 'm', 'deployment': 'vllm', 'process_pattern': 'VLLM', 'deadline_ms': 10000,
    'burst_requests': 4, 'allowed_models': ['m', 'm2'], 'faulty_manifest': 'faulty.yaml', 'good_manifest': 'good.yaml',
    'update_manifest': 'update.yaml', 'trace_directory': '/trace', 'fault_directory': '/data',
    'generation_models': {'before': 'm', 'after': 'm2'}, 'change_path': 'rolling', 'failure_marker': 'EXAMPLE shard missing',
    'restore_objects': {'configmap/engine-config': hashlib.sha256(canonical_json(
        {'data': {'k': 'v'}, 'binaryData': None, 'stringData': None})).hexdigest()},
    'tampered_objects': ['configmap/engine-config-tampered'], 'worker_pid': 7, 'worker_start_ticks': 100}
POLICY = {'buffer_max_process_rss_bytes': 10 ** 9, 'buffer_max_send_wait_ms': 5000, 'buffer_release_deadline_ms': 2000,
    'termination_max_delay_ms': 2000, 'retry_policy_mode': 'forbidden'}


class Cluster:
    def __init__(self, **modes):
        self.now = 0.0; self.modes = modes; self.calls = []; self.running = 0; self.counter = 0; self.traces = [1, 1, 2]
        self.manifest = 'good'; self.worker_state = 'S'; self.request_models = []
        self.progress_written = False; self.transition = threading.Event(); self.rollout_generation = 0
        self.apply_generation = 0; self.container_epoch = 0

    def clock(self):
        self.now += 0.01
        return self.now

    def wait(self, seconds):
        self.now += seconds

    def result(self, value):
        raw = value if type(value) is bytes else json.dumps(value).encode()
        return {'returncode': 0, 'stdout_bytes': raw, 'stderr_bytes': b''}

    def pods(self):
        volumes = [{'name': 'config', 'configMap': {'name': 'engine-config'}}]
        if self.manifest == 'faulty' and self.modes.get('tampered_ref'):
            volumes.append({'name': 'trial', 'configMap': {'name': 'engine-config-tampered'}})
        if self.manifest == 'faulty' and self.modes.get('tampered_secret_ref'):
            volumes.append({'name': 'trial', 'secret': {'secretName': 'engine-config-tampered'}})
        if self.modes.get('residual'):
            volumes.append({'name': 'old', 'configMap': {'name': 'engine-config-tampered'}})
        uid = 'pod-0' if self.modes.get('apply_no_new') or (
            self.modes.get('rollout_no_new') or self.rollout_generation == 0) and self.apply_generation == 0 else (
            f'pod-0-r{self.rollout_generation}-a{self.apply_generation}')
        engine_status = {'name': 'engine', 'ready': True,
            'containerID': f'containerd://engine-{self.apply_generation}-{self.rollout_generation}-{self.container_epoch}',
            'restartCount': (1 if self.modes.get('old_container_marker') else 0) + self.container_epoch,
            'state': {'running': {'startedAt': f'2026-11-02T00:00:{self.apply_generation:02}{self.container_epoch}Z'}}}
        label = 'good' if self.manifest == 'faulty' and self.modes.get('faulty_pod_absent') else self.manifest
        owner = [{'kind': 'ReplicaSet', 'name': 'vllm-rs', 'uid': 'vllm-rs-uid', 'controller': True}]
        pods = [{'metadata': {'name': 'engine-0', 'uid': uid, 'labels': {'byul.lab/generation': label},
                'ownerReferences': owner},
            'spec': {'volumes': volumes, 'containers': [{'name': 'engine',
                'ports': [{'containerPort': 8000, 'protocol': 'TCP'}]}]},
            'status': {'podIP': '10.0.0.1',
                'containerStatuses': [] if self.modes.get('faulty_status_missing') and self.manifest == 'faulty'
                else [engine_status], 'conditions': [{'type': 'Ready', 'status': 'True' if self.ready()
                and not self.modes.get('new_unready') and not (self.modes.get('faulty_split_pods')
                and self.manifest == 'faulty') else 'False'}]}}]
        if self.modes.get('two_pods'):
            pods.append({'metadata': {'name': 'engine-1', 'uid': 'pod-1', 'ownerReferences': owner},
                'spec': {'containers': [{'name': 'engine', 'ports': [{'containerPort': 8000}]}]},
                'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}})
        if self.modes.get('faulty_split_pods') and self.manifest == 'faulty':
            pods.append({'metadata': {'name': 'engine-1', 'uid': 'pod-1',
                'labels': {'byul.lab/generation': 'faulty'}},
                'status': {'containerStatuses': [{**engine_status, 'containerID': 'containerd://engine-side'}],
                    'conditions': [{'type': 'Ready', 'status': 'True'}]}})
        return pods

    def ready(self):
        return self.manifest != 'faulty' or self.modes.get('faulty_ready', False)

    def http(self, item):
        row = {'outcome': 200, 'elapsed_ms': 10, 'sha256': 'a' * 64, 'model': 'm', 'closed_early': False, 'complete': True, 'bytes': 10, 'answer': 'b' * 64}
        if item['path'] == '/metrics':
            running = 1 if self.modes.get('stuck') else 0
            return {**row, 'metrics': f"vllm:num_requests_running {running}\nvllm:prompt_tokens_total {self.counter}\n"}
        if item['path'] == '/health':
            return row
        if item['path'] in ('/start_profile', '/stop_profile'):
            return row
        if self.manifest == 'updated':
            row = {**row, 'model': 'm2'}
            if item['body']['model'] != 'm2':
                return {**row, 'outcome': 404}
        elif self.rollout_generation and item['body']['model'] == 'm2':
            row = {**row, 'model': 'm2'}
        if item['body'] and item['body']['messages'][0]['content'].startswith('retry') and not item.get('close_after_bytes'):
            self.counter += 10
        if item['body'] and item['body']['messages'][0]['content'].startswith('inflight') and self.modes.get('inflight_timeout'):
            return {**row, 'outcome': 'timeout', 'complete': False}
        if item.get('close_after_bytes'):
            self.counter += 30 if self.modes.get('retry') else 10
            return {**row, 'closed_early': True, 'complete': False}
        if item['body']['messages'][0]['content'].startswith('fault'):
            return {**row, 'outcome': 'timeout', 'complete': False} if self.modes.get('hang') else {**row, 'elapsed_ms': 2000}
        if item['body']['stream']:
            row = {**row, 'models': ['m', 'm2'] if self.modes.get('mixed') else ['m']}
        if self.modes.get('model'):
            return {**row, 'model': self.modes['model']}
        if self.modes.get('not_serving') and self.modes['not_serving'] > 0:
            self.modes['not_serving'] -= 1
            return {**row, 'outcome': 503}
        return row

    def __call__(self, argv, *, timeout, env_extra, max_stdout_bytes, max_stderr_bytes, input_bytes=None):
        self.calls.append(argv)
        text = ' '.join(argv)
        if 'get namespace' in text:
            labels = {lab.OWNED_LABEL: 'true'} if not self.modes.get('foreign') else {}
            return self.result({'metadata': {'labels': labels}})
        if 'get deployment' in text:
            return self.result({'metadata': {'namespace': 'lab', 'uid': 'deployment-uid'}})
        if 'get replicaset' in text:
            return self.result({'metadata': {'namespace': 'lab', 'uid': 'vllm-rs-uid',
                'ownerReferences': [{'kind': 'Deployment', 'name': 'vllm',
                    'uid': 'deployment-uid', 'controller': True}]}})
        if 'get service' in text:
            return self.result({'metadata': {'namespace': 'lab'},
                'spec': {'ports': [{'port': 8000, 'targetPort': 8000, 'protocol': 'TCP'}]}})
        if 'get pods' in text:
            return self.result({'items': self.pods()})
        if 'rollout restart' in text:
            self.transition.set()
            self.rollout_generation += 1
            return self.result({})
        if 'get configmap' in text:
            restored = self.manifest == 'good' and self.calls.count(argv) > 1
            value = 'changed' if self.modes.get('restore_differs') and restored else 'v'
            return self.result({'data': {'k': value}})
        if ' logs ' in text:
            if self.modes.get('no_marker') or self.modes.get('faulty_split_pods') and 'engine-1' in argv:
                return self.result(b'')
            if self.modes.get('sidecar_marker'):
                return self.result(b'' if '--container=engine' in argv else b'ERROR EXAMPLE shard missing\n')
            if self.modes.get('old_container_marker'):
                return self.result(b'ERROR EXAMPLE shard missing\n' if '--previous' in argv else b'')
            if self.modes.get('container_restart_during_log') and '--container=engine' in argv:
                if self.container_epoch == 0:
                    self.container_epoch = 1
                    return self.result(b'ERROR EXAMPLE shard missing\n')
                return self.result(b'')
            return self.result(b'INFO start\nERROR EXAMPLE shard missing\n')
        if ' apply ' in text:
            self.transition.set()
            self.apply_generation += 1
            self.manifest = 'faulty' if b'faulty' in input_bytes else 'updated' if b'update' in input_bytes else 'good'
            return self.result({})
        if lab.PROGRESS in argv:
            action = argv[-1]
            valid = action == 'directory' or self.progress_written and action in ('file', 'remove')
            if action == 'remove': self.progress_written = False
            return self.result({'valid': valid})
        if lab.CLIENT in argv:
            spec = json.loads(input_bytes)
            self.request_models.extend((self.manifest, item['body']['model']) for item in spec['requests'] if item.get('body'))
            if spec.get('progress_path'):
                self.progress_written = True
                assert self.transition.wait(timeout=5)
            return self.result([self.http(item) for item in spec['requests']])
        if lab.CENSUS in argv:
            if self.modes.get('two_pods') and 'engine-1' in argv:
                return self.result({'processes': [{'pid': 8, 'state': self.worker_state, 'rss_pages': 200,
                    'start_ticks': 200, 'command': 'VLLM rank=1', 'comm': 'VLLM'}],
                    'shm_entries': 2, 'shm_names': ['other', 'owned'], 'page_bytes': 4096})
            count = 0 if self.modes.get('gone') else 2
            processes = [{'pid': 7, 'state': self.worker_state, 'rss_pages': self.modes.get('rss', 100),
                'start_ticks': 100, 'command': 'VLLM rank=0', 'comm': 'VLLM'}] * count
            if self.modes.get('two_workers'):
                processes = [*processes, {'pid': 8, 'state': 'S', 'rss_pages': 100, 'start_ticks': 200,
                    'command': 'VLLM rank=1', 'comm': 'VLLM'}]
            return self.result({'processes': processes, 'process_scope_complete': True, 'process_scope_errors': [],
                'process_count_before': len({p['pid'] for p in processes}), 'process_count_after': len({p['pid'] for p in processes}), 'shm_entries': 1, 'shm_names': ['shm0'], 'page_bytes': 4096})
        if lab.FILE_COUNT in argv:
            return self.result({'count': self.traces.pop(0)})
        if lab.SIGNAL in argv and self.modes.get('die_on_signal'):
            self.modes['gone'] = True
        if lab.SIGNAL in argv:
            number = int(argv[-2]); self.worker_state = 'T' if number == lab.POSIX_SIGSTOP else 'S'
        return self.result({})


@pytest.fixture(autouse=True)
def manifests(tmp_path, monkeypatch):
    for name in ('faulty', 'good', 'update'):
        (tmp_path / f'{name}.yaml').write_text(name)
    monkeypatch.chdir(tmp_path)


def scenario(check_id, *, config=None, policy=None, **modes):
    world = Cluster(**modes)
    facts, _ = lab.run_scenario(check_id, {**BASE, **(config or {})}, {**POLICY, **(policy or {})},
        runner=world, clock=world.clock, wait=world.wait)
    return facts, world


@pytest.mark.parametrize('check_id', sorted(lab.SCENARIOS))
def test_clean_target_completes_scope_with_no_violation(check_id):
    facts, _ = scenario(check_id)
    assert facts['activation_violations'] == [], facts
    assert facts['activation_scope_complete'] is (check_id not in {'LIF-03', 'LIF-05', 'LIF-06', 'LIF-07', 'LIF-08', 'LIF-09', 'LIF-10', 'LIF-11', 'LIF-12', 'LIF-14', 'LIF-16', 'OBS-02'}), facts


@pytest.mark.parametrize('check_id,modes,code', [
    ('LIF-03', {'rss': 10 ** 9}, 'PROCESS_RSS_OVER_BUDGET'),
    ('LIF-04', {'stuck': True}, 'SLOW_READER_NOT_RELEASED_BEFORE_DEADLINE'),
    ('LIF-05', {'model': 'other'}, 'DISALLOWED_MODEL_SERVED_DURING_TRANSITION'),
    ('LIF-06', {'faulty_ready': True}, 'PARTIAL_CHANGE_ACCEPTED'),
    ('LIF-09', {'faulty_ready': True}, 'PARTIAL_INITIALIZATION_REPORTED_READY'),
])
def test_activated_fault_produces_the_named_violation(check_id, modes, code):
    facts, _ = scenario(check_id, **modes)
    assert code in {row['code'] for row in facts['activation_violations']}, facts


def test_request_that_ended_before_the_fault_is_not_evidence(monkeypatch):
    world = Cluster()
    original = world.http
    monkeypatch.setattr(world, 'http', lambda item: {**original(item), 'elapsed_ms': 1})
    facts, _ = lab.run_scenario('LIF-08', BASE, POLICY, runner=world, clock=world.clock, wait=world.wait)
    assert facts['activation_scope_complete'] is False and 'FAULT_AFTER_REQUEST_FINISHED:death' in facts['activation_missing']


def test_remote_timeout_without_fault_overlap_is_unverified_detection_timing():
    facts, _ = scenario('LIF-08', hang=True)
    assert facts['activation_scope_complete'] is False
    assert 'FAULT_REQUEST_OVERLAP_UNOBSERVED:death' in facts['activation_missing']
    assert 'FAULT_DETECTION_TIMING_UNVERIFIED:death' in facts['activation_missing']
    assert 'FAULT_NOT_DETECTED_BEFORE_DEADLINE' not in {v['code'] for v in facts['activation_violations']}


@pytest.mark.parametrize('modes,code', [
    ({'residual': True}, 'TAMPERED_MATERIAL_STILL_REFERENCED'),
    ({'restore_differs': True}, 'RESTORED_MATERIAL_DIFFERS_FROM_APPROVAL'),
])
def test_restore_checks_rejection_approval_and_residual_state(modes, code):
    facts, _ = scenario('LIF-16', **modes)
    assert code in {row['code'] for row in facts['activation_violations']}, facts


@pytest.mark.parametrize('modes,missing', [
    ({}, 'TAMPERED_RESTORE_REFERENCE_UNOBSERVED'),
    ({'faulty_pod_absent': True}, 'NEW_FAULTY_POD_UID_UNOBSERVED'),
    ({'apply_no_new': True}, 'NEW_FAULTY_POD_UID_UNOBSERVED'),
    ({'faulty_ready': True}, 'TAMPERED_RESTORE_REFERENCE_UNOBSERVED'),
    ({'faulty_ready': True, 'tampered_ref': True}, 'RESTORE_CONSUMPTION_AND_EXTERNAL_GATE_UNOBSERVED'),
    ({'faulty_ready': True, 'tampered_secret_ref': True}, 'TAMPERED_RESTORE_REFERENCE_UNOBSERVED'),
    ({'residual': True}, 'TAMPERED_MATERIAL_SPEC_REFERENCE_REMAINS'),
])
def test_restore_spec_and_ready_are_diagnostic_only(modes, missing):
    facts, _ = scenario('LIF-16', **modes)
    assert facts['activation_scope_complete'] is False
    assert missing in facts['activation_missing']
    if modes.get('residual'):
        assert {row['code'] for row in facts['activation_violations']} == {'TAMPERED_MATERIAL_STILL_REFERENCED'}
    else:
        assert not facts['activation_violations'], facts
    assert 'RESTORE_NONREADY_VOLUME_GATEWAY_RESIDUAL_UNOBSERVED' in facts['activation_missing']


def test_restore_ready_tampered_ref_tracks_new_uid_without_claiming_consumption():
    facts, _ = scenario('LIF-16', faulty_ready=True, tampered_ref=True)
    step = next(row for row in facts['activation_steps'] if row['step'] == 'faulty-generation')
    assert step['new_faulty_uids'] == ['pod-0-r0-a1']
    assert step['restore_reference_uids'] == ['pod-0-r0-a1']
    assert step['ready_uids'] == ['pod-0-r0-a1']
    assert facts['activation_violations'] == []


def test_restore_hash_violation_survives_missing_consumption_and_followup_error(monkeypatch):
    original_restore = lab.Lab.restore
    restore_calls = 0
    def cleanup_fails(self):
        nonlocal restore_calls
        restore_calls += 1
        if restore_calls > 1:
            raise OSError('cleanup failed')
        return original_restore(self)
    def failed_references(self, names):
        self.mutated = True
        raise OSError('followup failed')
    monkeypatch.setattr(lab.Lab, 'restore', cleanup_fails)
    monkeypatch.setattr(lab.Lab, 'references', failed_references)
    facts, _ = scenario('LIF-16', restore_differs=True)
    assert 'RESTORED_MATERIAL_DIFFERS_FROM_APPROVAL' in {row['code'] for row in facts['activation_violations']}
    assert 'SCENARIO_INTERRUPTED:OSError' in facts['activation_missing']
    assert 'FINAL_RESTORE_FAILED:OSError' in facts['activation_missing']


def test_restore_reference_identity_distinguishes_same_named_kinds():
    pod = {'spec': {'volumes': [
        {'configMap': {'name': 'same'}}, {'secret': {'secretName': 'same'}},
        {'projected': {'sources': [{'configMap': {'name': 'other'}}, {'secret': {'name': 'third'}}]}}],
        'containers': [{'envFrom': [{'configMapRef': {'name': 'fourth'}}],
            'env': [{'valueFrom': {'secretKeyRef': {'name': 'fifth'}}}]}]}}
    assert lab._restore_references(pod) == {'configmap/same', 'secret/same', 'configmap/other',
        'secret/third', 'configmap/fourth', 'secret/fifth'}


def test_restore_targets_must_match_approval_before_the_trial():
    facts, _ = scenario('LIF-16', config={'restore_objects': {'configmap/engine-config': '0' * 64}})
    assert facts['activation_scope_complete'] is False and 'RESTORE_TARGETS_NOT_APPROVED_AT_START' in facts['activation_missing']


def test_volume_restore_targets_are_not_claimed_as_hashed():
    facts, _ = scenario('LIF-16', config={'restore_objects': {'persistentvolumeclaim/models': '0' * 64}})
    assert 'RESTORE_OBJECT_KIND_UNSUPPORTED:persistentvolumeclaim' in facts['activation_missing']


def test_partial_failure_needs_its_declared_marker():
    facts, _ = scenario('LIF-09', no_marker=True)
    assert facts['activation_scope_complete'] is False and 'PARTIAL_FAILURE_ACTIVATION_UNVERIFIED' in facts['activation_missing']


def test_live_change_needs_distinguishable_generations():
    facts, _ = scenario('LIF-06', config={'generation_models': {'before': 'm', 'after': 'm'}})
    assert facts['activation_scope_complete'] is False and 'GENERATION_NOT_DISTINGUISHABLE' in facts['activation_missing']


def test_live_change_uses_after_alias_and_restores_before_alias():
    facts, world = scenario('LIF-06')
    assert ('updated', 'm2') in world.request_models
    assert ('updated', 'm') not in world.request_models
    assert world.request_models[-1] == ('good', 'm')
    assert 'STREAM_FIRST_BODY_BEFORE_CHANGE_UNOBSERVED' in facts['activation_missing']
    assert not any(v['code'] == 'DISALLOWED_MODEL_SERVED_DURING_TRANSITION' for v in facts['activation_violations'])


@pytest.mark.parametrize('check_id', ['LIF-05', 'LIF-06'])
def test_pending_or_completed_future_without_first_body_probe_cannot_claim_overlap(check_id):
    facts, _ = scenario(check_id)
    assert facts['activation_scope_complete'] is False
    assert 'STREAM_FIRST_BODY_BEFORE_CHANGE_UNOBSERVED' in facts['activation_missing']
    assert next(step for step in facts['activation_steps'] if step['step'] == 'inflight')['first_chunk_elapsed_ms'] is None


def test_prepared_probe_observes_first_body_before_change():
    facts, world = scenario('LIF-06', config={'probe_progress_directory': '/probe-progress'})
    before = next(step for step in facts['activation_steps'] if step['step'] == 'inflight-before-change')
    assert before['first_body_seen'] is True and before['request_pending'] is True
    assert 'STREAM_FIRST_BODY_BEFORE_CHANGE_UNOBSERVED' not in facts['activation_missing']
    assert 'INFLIGHT_FINISHED_BEFORE_CHANGE' not in facts['activation_missing']
    assert 'SERVER_INFLIGHT_REQUEST_AND_GENERATION_UNOBSERVED' in facts['activation_missing']
    assert facts['activation_scope_complete'] is False
    assert world.progress_written is False


def test_inflight_timeout_without_server_overlap_is_not_false_transition_failure():
    facts, _ = scenario('LIF-05', inflight_timeout=True)
    assert 'INFLIGHT_TIMEOUT_WITHOUT_SERVER_ATTRIBUTION' in facts['activation_missing']
    assert not any(v['code'] == 'INFLIGHT_REQUEST_HUNG_ACROSS_TRANSITION' for v in facts['activation_violations'])


def test_faulty_ready_label_without_failure_marker_does_not_prove_partial_acceptance():
    facts, _ = scenario('LIF-06', faulty_ready=True, no_marker=True)
    assert 'PARTIAL_FAILURE_ACTIVATION_UNVERIFIED' in facts['activation_missing']
    assert 'FAULTY_REQUEST_GENERATION_UNATTRIBUTED' in facts['activation_missing']
    assert not any(v['code'] == 'PARTIAL_CHANGE_ACCEPTED' for v in facts['activation_violations'])


def test_faulty_marker_and_ready_without_new_pod_uid_do_not_prove_partial_acceptance():
    facts, _ = scenario('LIF-06', faulty_ready=True, apply_no_new=True)
    assert 'NEW_FAULTY_POD_UID_UNOBSERVED' in facts['activation_missing']
    assert not any(v['code'] == 'PARTIAL_CHANGE_ACCEPTED' for v in facts['activation_violations'])


def test_faulty_marker_on_one_new_pod_and_ready_on_another_are_not_combined():
    facts, _ = scenario('LIF-06', faulty_split_pods=True)
    row = next(step for step in facts['activation_steps'] if step['step'] == 'faulty-generation')
    assert len(row['new_faulty_uids']) == 2
    assert row['marker_uids'] and row['ready_uids'] and not set(row['marker_uids']) & set(row['ready_uids'])
    assert 'FAULTY_MARKER_READY_UID_MISMATCH' in facts['activation_missing']
    assert not any(v['code'] == 'PARTIAL_CHANGE_ACCEPTED' for v in facts['activation_violations'])


@pytest.mark.parametrize('mode', ['old_container_marker', 'sidecar_marker'])
def test_previous_engine_or_sidecar_marker_is_diagnostic_not_current_ready_failure(mode):
    facts, world = scenario('LIF-06', faulty_ready=True, **{mode: True})
    row = next(step for step in facts['activation_steps'] if step['step'] == 'faulty-generation')
    assert row['diagnostic_marker_uids'] and row['marker_uids'] == []
    assert 'FAULTY_MARKER_ONLY_IN_PREVIOUS_OR_OTHER_CONTAINER' in facts['activation_missing']
    assert not any(v['code'] == 'PARTIAL_CHANGE_ACCEPTED' for v in facts['activation_violations'])
    current_logs = [call for call in world.calls if 'logs' in call and '--container=engine' in call]
    assert current_logs and all('--previous' not in call and '--all-containers' not in call for call in current_logs)


def test_engine_restart_between_current_log_and_status_cannot_bind_marker():
    facts, _ = scenario('LIF-06', faulty_ready=True, container_restart_during_log=True)
    assert 'FAULTY_CURRENT_ENGINE_CONTAINER_UNVERIFIED' in facts['activation_missing']
    assert not any(v['code'] == 'PARTIAL_CHANGE_ACCEPTED' for v in facts['activation_violations'])


def test_ready_faulty_pod_without_engine_status_or_marker_cannot_match_none_binding():
    facts, _ = scenario('LIF-06', faulty_ready=True, faulty_status_missing=True, no_marker=True)
    assert 'FAULTY_CURRENT_ENGINE_CONTAINER_UNVERIFIED' in facts['activation_missing']
    assert not any(v['code'] == 'PARTIAL_CHANGE_ACCEPTED' for v in facts['activation_violations'])


@pytest.mark.parametrize('check_id', ['LIF-05', 'LIF-06'])
def test_one_stream_with_two_generations_is_a_violation(check_id):
    facts, _ = scenario(check_id, mixed=True)
    assert 'MIXED_GENERATIONS_IN_ONE_REQUEST' in {row['code'] for row in facts['activation_violations']}


def test_ready_and_404_without_response_pod_attribution_is_incomplete():
    facts, _ = scenario('LIF-07', not_serving=2)
    assert 'STARTUP_NATIVE_RANK_MODEL_BACKEND_UNOBSERVED' in facts['activation_missing']
    assert 'STARTUP_RESPONSE_POD_UNATTRIBUTED' in facts['activation_missing']
    assert 'READY_WITHOUT_SERVICE_REQUEST_POD_UNATTRIBUTED' in facts['activation_missing']
    assert not any(v['code'] == 'READY_BEFORE_INFERENCE_AVAILABLE' for v in facts['activation_violations'])


def test_old_ready_pod_cannot_complete_new_generation_startup():
    facts, _ = scenario('LIF-07', rollout_no_new=True)
    assert facts['activation_scope_complete'] is False
    assert 'STARTUP_NOT_READY_WITHIN_DEADLINE' in facts['activation_missing']


def test_unready_new_pod_and_after_response_without_pod_attribution_is_incomplete():
    facts, _ = scenario('LIF-07', new_unready=True)
    assert 'EARLY_ADMISSION_POD_UNATTRIBUTED' in facts['activation_missing']
    assert not any(v['code'] == 'NEW_GENERATION_SERVED_BEFORE_READY' for v in facts['activation_violations'])
    assert 'STARTUP_NOT_READY_WITHIN_DEADLINE' in facts['activation_missing']


def test_retry_global_token_ratio_does_not_prove_retry_attempts():
    facts, _ = scenario('LIF-12', retry=True)
    assert facts['activation_scope_complete'] is False
    assert 'REQUEST_ATTEMPT_ID_AND_TOKEN_ATTRIBUTION_UNOBSERVED' in facts['activation_missing']
    assert not any(v['code'] == 'FORBIDDEN_AUTOMATIC_RETRY_OBSERVED' for v in facts['activation_violations'])


def test_trace_growth_after_stop_is_a_violation():
    world = Cluster(); world.traces = [1, 2, 3]
    facts, _ = lab.run_scenario('LIF-14', BASE, POLICY, runner=world, clock=world.clock, wait=world.wait)
    assert 'COLLECTION_CONTINUED_AFTER_STOP' in {row['code'] for row in facts['activation_violations']}


def test_profiler_next_start_runs_normal_request_and_failure_branch_remains_missing():
    facts, world = scenario('LIF-14')
    assert {'step': 'normal-next-active', 'outcome': 200, 'succeeded': True} in facts['activation_steps']
    assert 'PROFILER_STOP_FAILURE_BRANCH_UNOBSERVED' in facts['activation_missing']
    assert facts['activation_scope_complete'] is False
    assert next(row for row in facts['activation_steps'] if row['step'] == 'profiler')['trace_files'] == [1, 1, 2]


def test_service_health_after_selected_workers_gone_requires_response_attribution():
    facts, _ = scenario('OBS-02', die_on_signal=True,
        config={'service_url': 'http://vllm.lab.svc:9000'})
    assert facts['activation_violations'] == []
    assert 'STATE_SERVICE_HEALTH_WITHOUT_SELECTED_POD_ATTRIBUTION' in facts['activation_missing']
    assert facts['activation_scope_complete'] is False


def test_unowned_namespace_refuses_before_any_change():
    world = Cluster(foreign=True)
    with pytest.raises(lab.ActivationRefused, match='NOT_OWNED'):
        lab.run_scenario('LIF-11', BASE, POLICY, runner=world, clock=world.clock, wait=world.wait)
    assert len(world.calls) == 1 and 'namespace' in world.calls[0]


@pytest.mark.parametrize('check_id,key', [('LIF-16', 'tampered_objects'), ('LIF-09', 'failure_marker'), ('LIF-06', 'change_path'),
    ('LIF-09', 'faulty_manifest'), ('LIF-14', 'trace_directory'), ('LIF-06', 'update_manifest')])
def test_missing_operator_input_keeps_scope_incomplete(check_id, key):
    config = {k: v for k, v in BASE.items() if k != key}
    world = Cluster()
    facts, _ = lab.run_scenario(check_id, config, POLICY, runner=world, clock=world.clock, wait=world.wait)
    assert facts['activation_scope_complete'] is False and 'CONFIG_REQUIRED:' + key in facts['activation_missing']


def test_missing_fault_hook_keeps_rank_scope_incomplete():
    config = {k: v for k, v in BASE.items() if k != 'fault_directory'}
    world = Cluster()
    facts, _ = lab.run_scenario('LIF-08', config, POLICY, runner=world, clock=world.clock, wait=world.wait)
    assert 'CONFIG_REQUIRED:fault_directory' in facts['activation_missing']


def test_rank_selector_binds_pid_and_start_generation_for_stop_and_release():
    facts, world = scenario('LIF-08', config={'worker_pid': 8, 'worker_start_ticks': 200}, two_workers=True)
    signals = [call for call in world.calls if lab.SIGNAL in call]
    assert signals and all(call[-3] == '8' and call[-1] == '200' for call in signals)
    assert 'COMMUNICATION_STALL_INJECTION_UNOBSERVED' in facts['activation_missing']


def test_exception_file_uses_selected_pod_but_rank_ack_is_unobserved():
    facts, world = scenario('LIF-08', config={'worker_pod': 'engine-1', 'worker_pid': 8,
        'worker_start_ticks': 200}, two_pods=True)
    touch = [call for call in world.calls if lab.TOUCH in call]
    assert touch and all('engine-1' in call for call in touch)
    assert 'EXCEPTION_RANK_ATTRIBUTION_UNOBSERVED' in facts['activation_missing']


def test_worker_cleanup_censuses_the_selected_pod_and_generation():
    facts, world = scenario('LIF-11', config={'worker_pod': 'engine-1', 'worker_pid': 8,
        'worker_start_ticks': 200}, two_pods=True)
    row = next(step for step in facts['activation_steps'] if step['step'] == 'worker-failure')
    assert row['target']['pod'] == 'engine-1' and row['target']['pod_uid'] == 'pod-1'
    assert row['process_identities_before'] == [(8, 200)]
    assert row['shm_names_before'] == ['other', 'owned']
    assert all('engine-1' in call for call in world.calls if lab.CENSUS in call)


def test_ambiguous_rank_target_stays_incomplete_without_injecting():
    config = {key: value for key, value in BASE.items() if key not in ('worker_pid', 'worker_start_ticks')}
    world = Cluster(two_workers=True)
    facts, _ = lab.run_scenario('LIF-08', config, POLICY, runner=world, clock=world.clock, wait=world.wait)
    assert facts['activation_scope_complete'] is False
    assert 'FAULT_NOT_INJECTED:death' in facts['activation_missing']
    assert not [call for call in world.calls if lab.SIGNAL in call]


def test_stale_start_generation_refuses_signal():
    facts, world = scenario('LIF-08', config={'worker_start_ticks': 999})
    assert 'FAULT_NOT_INJECTED:death' in facts['activation_missing']
    assert not [call for call in world.calls if lab.SIGNAL in call]


@pytest.mark.parametrize('check_id,missing', [
    ('LIF-03', 'REQUEST_AND_STAGE_BUDGET_UNOBSERVED'),
    ('LIF-10', 'REQUEST_SCOPED_CORE_WORKER_AND_REFERENCE_RELEASE_UNOBSERVED'),
    ('LIF-11', 'IPC_OWNERSHIP_AND_GPU_RESOURCE_RELEASE_UNOBSERVED'),
])
def test_aggregate_or_http_only_observation_cannot_complete_scope(check_id, missing):
    facts, _ = scenario(check_id)
    assert facts['activation_scope_complete'] is False
    assert missing in facts['activation_missing']


def test_missing_metric_is_not_a_clean_result(monkeypatch):
    world = Cluster()
    monkeypatch.setattr(lab.Lab, 'metric', lambda self, name: None)
    facts, _ = lab.run_scenario('LIF-10', BASE, POLICY, runner=world, clock=world.clock, wait=world.wait)
    assert facts['activation_scope_complete'] is False and 'RUNNING_REQUEST_METRIC_UNAVAILABLE' in facts['activation_missing']


def test_unrelated_running_request_is_not_attributed_to_disconnected_request():
    facts, _ = scenario('LIF-10', stuck=True)
    assert 'AGGREGATE_RUNNING_NOT_REQUEST_ATTRIBUTED' in facts['activation_missing']
    assert not any(v['code'] == 'WORK_CONTINUED_PAST_TERMINATION_DEADLINE' for v in facts['activation_violations'])
    assert facts['activation_scope_complete'] is False


def test_config_without_required_keys_is_refused():
    with pytest.raises(lab.ActivationRefused, match='CONFIG_INVALID'):
        lab.Lab({'namespace': 'lab'})


class Passive:
    name = 'stream_buffer'; version = '1'

    def __init__(self, failure=None, cleanup_errors=None):
        self.failure = failure; self.cleanup_errors = cleanup_errors or []

    def run(self, check, env, values, adapter, workdir):
        if self.failure:
            raise self.failure
        return SimpleNamespace(observations={'passive': Observation(value=True, collected=True)}, artifacts=[],
            config={}, cleanup_errors=self.cleanup_errors)


CHECK = SimpleNamespace(id='LIF-10', challenge_steps=['one', 'two'])


def test_passive_and_active_facts_merge_without_claiming_full_termination_scope(tmp_path):
    world = Cluster()
    executor = lab.ActivatedExecutor(Passive(), BASE, runner=world, clock=world.clock, wait=world.wait)
    draft = executor.run(CHECK, None, POLICY, None, tmp_path / 'work')
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    assert draft.observations['activation_scope_complete'].value is False and draft.observations['passive'].value is True
    assert not any(step.attempted for step in draft.challenges) and draft.normal_control.succeeded is True


def test_passive_failure_does_not_block_active_measurement(tmp_path):
    world = Cluster()
    executor = lab.ActivatedExecutor(Passive(RuntimeError('x')), BASE, runner=world, clock=world.clock, wait=world.wait)
    draft = executor.run(CHECK, None, POLICY, None, tmp_path / 'work')
    assert draft.status == ExecStatus.ABORTED
    assert {'step': 'passive-collection', 'unavailable': 'RuntimeError'} in draft.observations['activation_steps'].value


def test_profiler_passive_cleanup_error_cannot_complete_scope_or_challenges(tmp_path):
    world = Cluster()
    executor = lab.ActivatedExecutor(Passive(cleanup_errors=['PROFILER_CONTROL_SESSION_SEAL_UNAVAILABLE']),
        BASE, runner=world, clock=world.clock, wait=world.wait)
    draft = executor.run(SimpleNamespace(id='LIF-14', challenge_steps=['normal', 'stop', 'failure']),
        None, POLICY, None, tmp_path / 'work')
    assert draft.status == ExecStatus.ABORTED
    assert draft.observations['activation_scope_complete'].value is False
    assert 'PASSIVE_CLEANUP_ERRORS' in draft.observations['activation_missing'].value
    assert draft.cleanup_errors == ['PROFILER_CONTROL_SESSION_SEAL_UNAVAILABLE']
    assert all(not step.attempted for step in draft.challenges)


def test_incomplete_scope_cannot_clear_the_pass_gate(tmp_path):
    world = Cluster()
    config = {k: v for k, v in BASE.items() if k != 'faulty_manifest'}
    executor = lab.ActivatedExecutor(Passive(), config, runner=world, clock=world.clock, wait=world.wait)
    draft = executor.run(SimpleNamespace(id='LIF-09', challenge_steps=['one']), None, POLICY, None, tmp_path / 'work')
    assert draft.status == ExecStatus.ABORTED and not draft.challenges[0].attempted


def test_fault_violation_survives_final_cleanup_failure_and_attempts_stay_incomplete(tmp_path, monkeypatch):
    def broken_restore(self):
        raise OSError('restore refused')
    monkeypatch.setattr(lab.Lab, 'restore', broken_restore)
    world = Cluster()
    original_http = world.http
    def incomplete_response(item):
        row = original_http(item)
        if item.get('body') and item['body']['messages'][0]['content'].startswith('fault'):
            return {**row, 'outcome': 200, 'complete': False}
        return row
    monkeypatch.setattr(world, 'http', incomplete_response)
    executor = lab.ActivatedExecutor(Passive(), BASE, runner=world, clock=world.clock, wait=world.wait)
    draft = executor.run(SimpleNamespace(id='LIF-08', challenge_steps=['death', 'delay', 'stall', 'exception']),
        None, POLICY, None, tmp_path / 'work')
    facts = json.loads((tmp_path / 'work' / 'lab-activation.json').read_text(encoding='utf-8'))
    assert any(v['code'] == 'INCOMPLETE_WORK_REPORTED_AS_SUCCESS' for v in facts['activation_violations'])
    assert 'FINAL_RESTORE_FAILED:OSError' in facts['activation_missing']
    assert draft.status == ExecStatus.COMPLETED
    assert 'LAB_ACTIVATION_FINAL_RESTORE_FAILED:OSError' in draft.cleanup_errors
    assert 'LAB_ACTIVATION_FAULT_RESTORE_FAILED:death:OSError' in draft.cleanup_errors
    assert all(not step.attempted for step in draft.challenges)


def test_unfinished_real_run_is_never_measured(tmp_path, monkeypatch):
    world = Cluster()
    config = {k: v for k, v in BASE.items() if k != 'faulty_manifest'}
    executor = lab.ActivatedExecutor(Passive(), config)
    monkeypatch.setattr(lab, 'capture_pipes', world)
    executor.clock = world.clock; executor.wait = world.wait
    draft = executor.run(SimpleNamespace(id='LIF-09', challenge_steps=['one']), None, POLICY, None, tmp_path / 'work')
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.DECLARED


def test_registry_wraps_only_with_explicit_activation_settings():
    assert not isinstance(registry.build_executor('stream_buffer', 'kubernetes', {}), lab.ActivatedExecutor)
    wrapped = registry.build_executor('stream_buffer', 'kubernetes', {'lab_activation': deepcopy(BASE)})
    assert isinstance(wrapped, lab.ActivatedExecutor) and wrapped.name == 'stream_buffer'
