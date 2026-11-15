"""nonce/원 CALL/API 근거가 맞을 때만 일회용 barrier를 만들고 실제 적용과 구분한다."""
from copy import deepcopy
from hashlib import sha256
import hmac
import json
import os
import threading
import time

import pytest

from guard.adapters import vllm_rank_ipc_runtime as runtime
from guard.adapters import vllm_rank_rpc_control as code
from tests.test_rank_rpc_hooks import setup as rpc_setup


def fp(value):
    return hmac.new(b'K' * 32, b'guard-rank-request-id/1\0' + value.encode(), sha256).hexdigest()


@pytest.fixture
def configured(tmp_path, rpc_setup):
    module, observer, rows, inner, outer, scheduled, _ = rpc_setup
    directory = tmp_path / 'data'; directory.mkdir(mode=0o700)
    settings = {'session': 'a' * 32, 'image': 'fixture-image', 'key': (b'K' * 32).hex(),
        'output_dir': str(directory), 'assets': {'guard/adapters/vllm_rank_rpc_control.py': {
            'bytes': 1, 'sha256': 'b' * 64}}}
    settings_sha = code.digest(settings)
    writer = runtime.SnapshotWriter(settings, settings_sha)
    def emit(payload):
        rows.append(payload); writer(payload)
    observer.emit = emit
    observer.profile['request_binding'] = {'kind': 'direct'}
    hook = observer.sources[module.__name__]['hooks']['Inner.execute']
    hook['fault_eligible'] = True
    hook['scalars'] = {'gpu_rank': {'root': 'self', 'path': ['rank']},
                       'configured_tp_size': {'root': 'self', 'path': ['tp']}}
    inner.rank, inner.tp = 0, 2
    control = code.Control(settings, settings_sha, observer.profile, emit, lambda: None)
    # 정상 호출의 원 프레임/객체를 먼저 확인한다. 물리 신호는 보내지 않는다.
    outer.execute(scheduled)
    initial = next(p for p in rows if p['point'] == 'Inner.execute' and p['event'] == 'CALL')
    observer.control = control
    return module, observer, rows, inner, outer, scheduled, control, initial


def arm_request(configured, kind='delay', change=None, lifetime_ns=4_000_000_000):
    module, _, _, _, _, scheduled, control, initial = configured
    header = 'EXAMPLE-new-request'; now = time.monotonic_ns()
    arm = {'schema': 'guard-rank-rpc-arm/1', 'session': control.settings['session'],
        'image': control.settings['image'], 'settings_sha256': control.settings_sha256,
        'assets_sha256': code.digest(control.settings['assets']), 'target_sha256': '1' * 64,
        'nonce': '2' * 32, 'fault_kind': kind, 'issued_ns': now, 'expires_ns': now + lifetime_ns,
        'target_context': initial['context'], 'parent_object_hmac_sha256': initial['rpc_parent_object_hmac_sha256'],
        'target_point': initial['point'], 'source_sha256': initial['source_sha256'],
        'body_code_sha256': initial['body_code_sha256'], 'header_hmac_sha256': fp(header),
        'external_hmac_sha256': fp(header), 'header_context': initial['context'],
        'assignment_context': None, 'initial_capture_sha256': '3' * 64}
    if change is not None:
        change(arm)
    arm_sha = control.write('control-arm.json', arm)
    assert module.External.header(header) == header
    scheduled.num_scheduled_tokens = {header: 1}
    return arm, arm_sha


def begin(configured):
    module, _, _, _, outer, scheduled, _, _ = configured
    outcome = []
    def run():
        try:
            outcome.append(('returned', outer.execute(scheduled)))
        except BaseException as error:
            outcome.append(('raised', error))
    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome


def ready(control):
    limit = time.monotonic() + 3
    while time.monotonic() < limit:
        try:
            return control.read('control-ready.json')
        except FileNotFoundError:
            time.sleep(0.005)
    pytest.fail('자격 있는 원 RPC barrier가 만들어지지 않았다')


def test_no_arm_preserves_normal_call_without_control_event(configured):
    module, _, rows, inner, outer, scheduled, _, _ = configured
    before = len(module.calls)
    assert outer.execute(scheduled) is inner.result
    assert len(module.calls) == before + 2
    assert not any(p.get('event', '').startswith('CONTROL_') for p in rows)


def test_ready_and_release_are_one_call_barrier_not_physical_signal(configured):
    module, _, rows, inner, _, _, control, _ = configured
    arm, arm_sha = arm_request(configured)
    before = len(module.calls); thread, outcome = begin(configured)
    receipt, receipt_sha = ready(control)
    assert receipt['rpc_active'] is True and len(module.calls) == before + 1
    assert thread.is_alive() and outcome == []
    control.write('control-action.json', {'schema': 'guard-rank-rpc-action/1', 'nonce': arm['nonce'],
        'arm_sha256': arm_sha, 'ready_sha256': receipt_sha, 'action': 'release', 'receipt_sha256': '4' * 64})
    thread.join(3)
    assert not thread.is_alive() and outcome == [('returned', inner.result)]
    assert len(module.calls) == before + 2
    phases = [p for p in rows if p.get('event', '').startswith('CONTROL_')]
    assert [p['event'] for p in phases] == ['CONTROL_READY', 'CONTROL_RELEASE']
    assert all(p['control']['barrier_only'] is True and p['original_invoked'] is False for p in phases)
    original = control.read_raw('control-ready.json')
    _, second = begin(configured)
    # 이 nonce는 이미 claim되어 새 READY나 두 번째 barrier를 만들지 않는다.
    limit = time.monotonic() + 3
    while not second and time.monotonic() < limit:
        time.sleep(0.005)
    assert second == [('returned', inner.result)]
    assert control.read_raw('control-ready.json') == original
    assert not any(p.get('schema') == 'guard-rank-rpc-control-gap/1' for p in rows)
    claim, _ = control.read('control-claimed-' + arm['nonce'] + '.json')
    assert claim['arm_sha256'] == arm_sha and claim['context'] == receipt['context']
    assert claim['call_hmac_sha256'] == receipt['call_hmac_sha256']


def test_wrong_claim_is_not_a_consumed_nonce_or_a_second_ready(configured):
    _, _, rows, inner, _, _, control, initial = configured
    arm, arm_sha = arm_request(configured)
    control.write('control-claimed-' + arm['nonce'] + '.json', {
        'schema': 'guard-rank-rpc-claim/1', 'nonce': arm['nonce'], 'arm_sha256': '0' * 64,
        'context': initial['context'], 'parent_object_hmac_sha256': initial['rpc_parent_object_hmac_sha256'],
        'call_hmac_sha256': 'a' * 64})
    thread, outcome = begin(configured); thread.join(3)
    assert not thread.is_alive() and outcome == [('returned', inner.result)]
    assert any(p.get('schema') == 'guard-rank-rpc-control-gap/1' for p in rows)
    with pytest.raises(FileNotFoundError):
        control.read('control-ready.json')


def test_exception_is_explicit_instrumented_boundary_not_original_execution(configured):
    module, _, rows, _, _, _, control, _ = configured
    arm, arm_sha = arm_request(configured, kind='exception')
    before = len(module.calls); thread, outcome = begin(configured)
    _, ready_sha = ready(control)
    control.write('control-action.json', {'schema': 'guard-rank-rpc-action/1', 'nonce': arm['nonce'],
        'arm_sha256': arm_sha, 'ready_sha256': ready_sha, 'action': 'raise', 'receipt_sha256': '4' * 64})
    thread.join(3)
    assert not thread.is_alive() and len(outcome) == 1
    assert outcome[0][0] == 'raised' and type(outcome[0][1]) is code.NativeLabFault
    assert len(module.calls) == before + 1
    boundary = [p for p in rows if p.get('event') == 'CONTROL_RAISE']
    assert len(boundary) == 1 and boundary[0]['original_invoked'] is False
    assert any(p.get('event') == 'RAISE' and p['point'] == 'Outer.execute' and p['original_invoked'] for p in rows)


@pytest.mark.parametrize('field', ['session', 'settings_sha256', 'assets_sha256', 'body_code_sha256',
                                  'source_sha256', 'parent_object_hmac_sha256', 'header_hmac_sha256',
                                  'external_hmac_sha256', 'target_point'])
def test_wrong_scope_never_creates_ready_or_blocks_original(configured, field):
    _, _, _, inner, _, _, control, _ = configured
    def change(value):
        value[field] = 'wrong' if field in {'session', 'target_point'} else '0' * 64
    arm_request(configured, change=change)
    thread, outcome = begin(configured); thread.join(3)
    assert not thread.is_alive() and outcome == [('returned', inner.result)]
    with pytest.raises(FileNotFoundError):
        control.read('control-ready.json')


@pytest.mark.parametrize('field', ['pid', 'start_ticks', 'pid_namespace', 'time_namespace', 'uid'])
def test_wrong_process_generation_is_not_selected(configured, field):
    _, _, _, inner, _, _, control, initial = configured
    def change(value):
        value['target_context'] = {**initial['context'], field: initial['context'][field] + 1}
    arm_request(configured, change=change)
    thread, outcome = begin(configured); thread.join(3)
    assert not thread.is_alive() and outcome == [('returned', inner.result)]
    with pytest.raises(FileNotFoundError):
        control.read('control-ready.json')


def test_expiry_is_not_release_or_applied_fault(configured):
    _, _, rows, inner, _, _, control, _ = configured
    arm_request(configured, lifetime_ns=80_000_000)
    thread, outcome = begin(configured); thread.join(3)
    assert not thread.is_alive() and outcome == [('returned', inner.result)]
    assert any(p.get('event') == 'CONTROL_EXPIRED' for p in rows)
    assert not any(p.get('event') in {'CONTROL_RELEASE', 'CONTROL_EXCEPTION', 'CONTROL_RAISE'} for p in rows)


def test_private_file_hmac_mode_symlink_and_duplicates_are_rejected(configured, tmp_path):
    *_, control, _ = configured
    control.write('control-sample.json', {'value': 1})
    path = tmp_path / 'data/control-sample.json'; raw = path.read_bytes()
    envelope = json.loads(raw); envelope['hmac_sha256'] = '0' * 64
    path.write_bytes(code.canonical(envelope))
    with pytest.raises(ValueError, match='RANK_RPC_CONTROL_HMAC'):
        control.read('control-sample.json')
    path.write_bytes(raw); path.chmod(0o644)
    with pytest.raises(ValueError, match='RANK_RPC_CONTROL_FILE'):
        control.read('control-sample.json')
    path.unlink(); path.symlink_to(tmp_path / 'fixture.py')
    with pytest.raises((OSError, ValueError)):
        control.read('control-sample.json')
    path.unlink(); path.write_text('{"payload":1,"payload":2}'); path.chmod(0o600)
    with pytest.raises(ValueError, match='RANK_RPC_CONTROL_DUPLICATE'):
        control.read('control-sample.json')
