"""READY 파일과 원 snapshot 사이의 순서를 강제한다. 실제 신호/엔진 실행은 없다."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from guard.adapters import vllm_rank_rpc_actor as actor


@pytest.mark.parametrize('damage', [None, 'missing', 'duplicate', 'nonce', 'ready_sha256',
    'context', 'parent_call', 'source', 'time'])
def test_actor_waits_for_exact_ready_snapshot_before_fault(monkeypatch, damage):
    context = {'pid': 123, 'start_ticks': 1, 'uid': 1000, 'boot_sha256': '1' * 64,
        'pid_namespace': 2, 'time_namespace': 3, 'time_offsets_sha256': '2' * 64}
    proof = {'context': context, 'rpc_parent_object_hmac_sha256': '3' * 64,
        'point': 'Inner.execute', 'source_sha256': '4' * 64, 'body_code_sha256': '5' * 64}
    calibration = {'proof': proof, 'header_context': context, 'assignment_context': None,
        'initial_capture_sha256': '6' * 64}
    now = 100; snapshots = []; signals = []; writes = {}; waits = []

    class Control:
        key = b'K' * 32
        settings = {'session': 'a' * 32, 'image': 'EXAMPLE', 'assets': {
            'guard/adapters/vllm_rank_rpc_control.py': {'sha256': '7' * 64}}}
        settings_sha256 = '8' * 64
        profile = {'request_binding': {'kind': 'direct', 'external_prefix': '', 'external_suffix': ''}}

        def write(self, name, payload):
            if name == 'control-physical-action.json':
                assert snapshots, '원 READY snapshot 저장 전 장애 적용'
            writes[name] = payload
            return actor.control_code.digest(payload)

        def read(self, name):
            assert name == 'control-ready.json'
            arm = writes['control-arm.json']
            ready = {'schema': 'guard-rank-rpc-ready/1', 'arm_sha256': actor.control_code.digest(arm),
                'nonce': arm['nonce'], 'context': context, 'rpc_active': True,
                'prepared_assets_sha256': arm['assets_sha256'], 'source_refs': {},
                'call_hmac_sha256': '9' * 64, 'parent_call_hmac_sha256': 'b' * 64,
                'ready_ns': now, 'expires_ns': arm['expires_ns']}
            self.ready = ready
            return ready, actor.control_code.digest(ready)

        def _records(self):
            return [(value, 'c' * 64, {}) for value in snapshots]

    control = Control()

    class Target:
        def __init__(self, expected): assert expected == context
        def current(self): return 'R', 0
        def exited(self): return bool(signals)
        def send(self, number):
            assert snapshots, '원 READY snapshot 저장 전 장애 적용'
            signals.append(number)
        def close(self): pass

    def finish_snapshot(_):
        nonlocal now
        assert signals == [] and 'control-physical-action.json' not in writes
        waits.append(True)
        arm, ready = writes['control-arm.json'], control.ready
        if damage == 'missing':
            now = arm['expires_ns']
            return
        value = {**deepcopy(proof), 'event': 'CONTROL_READY', 'original_invoked': False,
            'rpc_parent_frame_verified': True, 'rpc_parent_worker_verified': True,
            'call_hmac_sha256': ready['call_hmac_sha256'],
            'rpc_parent_call_hmac_sha256': ready['parent_call_hmac_sha256'], 'observation_ns': now,
            'control': {'nonce': arm['nonce'], 'fault_kind': arm['fault_kind'],
                'arm_sha256': actor.control_code.digest(arm), 'ready_sha256': actor.control_code.digest(ready),
                'action_sha256': None, 'target_sha256': arm['target_sha256'],
                'control_code_sha256': '7' * 64, 'barrier_only': True}}
        if damage in {'nonce', 'ready_sha256'}: value['control'][damage] = '0' * 64
        elif damage == 'context': value['context']['start_ticks'] += 1
        elif damage == 'parent_call': value['rpc_parent_call_hmac_sha256'] = '0' * 64
        elif damage == 'source': value['source_sha256'] = '0' * 64
        elif damage == 'time': value['observation_ns'] = ready['ready_ns'] - 1
        snapshots.append(value)
        if damage == 'duplicate': snapshots.append(deepcopy(value))
        now += 1

    monkeypatch.setattr(actor, 'SignalTarget', Target)
    monkeypatch.setattr(actor, 'post', lambda *args: {'status': None, 'complete': False})
    monkeypatch.setattr(actor, 'time', SimpleNamespace(monotonic_ns=lambda: now, sleep=finish_snapshot))
    if damage is None:
        result = actor.exercise(control, calibration, 'EXAMPLE', 'death', {'scope': 'EXAMPLE'})
        assert signals == [actor.signal.SIGKILL] and waits == [True]
        assert result['physical_action']['after_state'] == 'EXITED'
        assert result['physical_resume'] is None and result['scope_complete'] is False
    else:
        with pytest.raises(ValueError, match='RANK_RPC_READY_UNVERIFIED'):
            actor.exercise(control, calibration, 'EXAMPLE', 'death', {'scope': 'EXAMPLE'})
        assert signals == [] and 'control-physical-action.json' not in writes
        assert waits == [True]
