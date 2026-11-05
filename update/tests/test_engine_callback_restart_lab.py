"""REQ-21 lab 재기동: 같은 Pod 안의 엔진 컨테이너만 한 번 다시 시작하고, 확인된 새 세대로만 바인딩을 옮긴다."""
from copy import deepcopy
import types

import pytest

from guard.executors import engine_callback_projection as projection
from guard.runtime.kubernetes_http import ENGINE_RESTART, KubernetesBindingError, KubernetesHTTP

ENGINE = {'namespace': 'byul-lab', 'namespace_uid': '00000000-0000-0000-0000-000000000001', 'pod': 'engine-pod',
          'pod_uid': '10000000-0000-0000-0000-000000000001', 'container': 'engine', 'container_id': 'containerd://' + 'a' * 64,
          'image': 'registry.example/vllm@sha256:' + 'b' * 64, 'image_id': 'registry.example/vllm@sha256:' + 'b' * 64,
          'restart_count': 0, 'node': 'node-1', 'pod_ip': '10.0.0.5', 'started_at': '2026-11-05T00:00:00Z'}


def pod(*, restart=0, container='a', started='2026-11-05T00:00:00Z', policy='Always', ready=True, image=None):
    return {'kind': 'Pod', 'metadata': {'name': 'engine-pod', 'namespace': 'byul-lab', 'uid': ENGINE['pod_uid'], 'labels': {'guard.run': 'run-1'}},
            'spec': {'restartPolicy': policy, 'nodeName': 'node-1', 'containers': [{'name': 'engine', 'image': image or ENGINE['image']}]},
            'status': {'phase': 'Running', 'podIP': '10.0.0.5', 'conditions': [{'type': 'Ready', 'status': 'True' if ready else 'False'}],
                       'containerStatuses': [{'name': 'engine', 'containerID': 'containerd://' + container * 64, 'imageID': ENGINE['image_id'],
                                              'restartCount': restart, 'ready': ready, 'state': {'running': {'startedAt': started}}}]}}


def fake(sequence, *, exec_fails=False):
    calls = []
    state = types.SimpleNamespace(binding={'engine': deepcopy(ENGINE), 'run_id': 'run-1'}, deadline=10**9, verified=0)
    pods = iter(sequence)
    def call(args, **kwargs):
        calls.append(args)
        if 'exec' in args:
            assert args[-1] == ENGINE_RESTART
            if exec_fails:
                raise KubernetesBindingError()
            return {'schema': 'guard-lab-engine-restart/1', 'signal': 'SIGTERM'}
        if args[:2] == ['get', 'namespace']:
            return {'kind': 'Namespace', 'metadata': {'name': 'byul-lab', 'uid': ENGINE['namespace_uid']}}
        return next(pods)
    state._call = call
    state._verify = lambda: setattr(state, 'verified', state.verified + 1)
    state.clock = iter(range(0, 10**6)).__next__
    return state, calls


def test_restart_moves_the_binding_only_to_the_verified_next_generation(monkeypatch):
    monkeypatch.setattr('time.sleep', lambda s: None)
    state, calls = fake([pod(), pod(ready=False), pod(restart=1, container='c', started='2026-11-05T00:01:00Z')], exec_fails=True)
    facts = KubernetesHTTP._engine_restart(state)
    assert facts == {'restart_count_before': 0, 'restart_count_after': 1, 'container_generation_changed': True, 'pod_uid_unchanged': True}
    moved = state.binding['engine']
    assert moved['restart_count'] == 1 and moved['container_id'] == 'containerd://' + 'c' * 64 and moved['started_at'] == '2026-11-05T00:01:00Z'
    assert {k: v for k, v in moved.items() if k not in {'restart_count', 'container_id', 'started_at'}} == \
        {k: v for k, v in ENGINE.items() if k not in {'restart_count', 'container_id', 'started_at'}}
    assert state.verified == 2


@pytest.mark.parametrize('sequence', [
    [pod(policy='Never')],
    [pod(), pod(restart=2, container='c', started='2026-11-05T00:01:00Z')],
    [pod(), pod(restart=1, container='c', started='2026-11-05T00:01:00Z', image='registry.example/other@sha256:' + 'd' * 64)],
])
def test_restart_refuses_unowned_or_unexpected_generations_and_keeps_the_old_binding(monkeypatch, sequence):
    monkeypatch.setattr('time.sleep', lambda s: None)
    state, _ = fake(sequence)
    with pytest.raises(KubernetesBindingError):
        KubernetesHTTP._engine_restart(state)
    assert state.binding['engine'] == ENGINE


def test_restart_is_a_req21_lab_switch_with_a_fixed_declaration():
    cases = [{'case_id': 'b', 'role': 'before', 'prompt': 'x'}, {'case_id': 's', 'role': 'switch', 'action': 'engine_restart', 'variant': 'once'},
             {'case_id': 'a', 'role': 'after', 'prompt': 'x'}]
    projection.validate_cases('REQ-21', cases)
    projection.validate_lab_contexts({'engine_restart': {'variants': {'once': 'container'}}}, cases)
    with pytest.raises(ValueError):
        projection.validate_lab_contexts({'engine_restart': {'variants': {'twice': 'container'}}})
    with pytest.raises(ValueError):
        projection.validate_cases('REQ-16', [{'case_id': 's', 'role': 'switch', 'action': 'engine_restart', 'variant': 'once'}])
