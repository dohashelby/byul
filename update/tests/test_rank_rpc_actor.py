"""시험이 만든 Python 자식만 대상으로 pidfd·실제 해제와 actor 사망 정리를 검증한다."""
import os
import signal
import subprocess
import sys
import time

import pytest

from guard.adapters import vllm_rank_ipc_identity as identity
from guard.adapters import vllm_rank_rpc_actor as actor
from tests.test_rank_rpc_control import configured as configured_control
from tests.test_rank_rpc_hooks import setup as rpc_setup


@pytest.fixture
def child():
    process = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import time;time.sleep(30)'])
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def wait_state(target, predicate):
    limit = time.monotonic() + 3
    while time.monotonic() < limit:
        state = target.current()[0]
        if predicate(state):
            return state
        time.sleep(0.005)
    pytest.fail('시험 소유 자식의 실제 상태가 확인되지 않았다')


@pytest.fixture
def lease_clock(monkeypatch):
    # The lease's 200 ms starts before fork. Keep setup scheduling independent
    # of that semantic deadline; both real processes share the controlled clock.
    # pidfd signals, /proc state checks and the outer wait bound remain real.
    from multiprocessing import Value
    clock = Value('q', time.monotonic_ns())
    monkeypatch.setattr(actor.time, 'monotonic_ns', lambda: clock.value)
    return clock


def test_pidfd_stop_and_same_generation_resume_are_real(child):
    expected = identity.process_context(child.pid)
    target = actor.SignalTarget(expected)
    try:
        assert target.current()[0] in {'R', 'S', 'D', 'I'}
        target.send(signal.SIGSTOP)
        assert wait_state(target, lambda state: state == 'T') == 'T'
        target.send(signal.SIGCONT)
        assert wait_state(target, lambda state: state in {'R', 'S', 'D', 'I'}) != 'T'
        assert identity.process_context(child.pid) == expected and child.poll() is None
    finally:
        target.resume_cleanup(); target.close()


def test_wrong_generation_and_self_signal_are_denied(child):
    expected = identity.process_context(child.pid)
    with pytest.raises(ValueError, match='RANK_RPC_SIGNAL_GENERATION'):
        actor.SignalTarget({**expected, 'start_ticks': expected['start_ticks'] + 1})
    with pytest.raises(ValueError, match='RANK_RPC_SIGNAL_SELF'):
        actor.SignalTarget(identity.process_context(os.getpid()))
    assert actor._state(child.pid)[0] != 'T' and child.poll() is None


def test_lease_expiry_resumes_exact_owned_target(child, lease_clock):
    target = actor.SignalTarget(identity.process_context(child.pid))
    lease = actor.ResumeLease(target, time.monotonic_ns() + 200_000_000)
    try:
        lease.before_stop(); target.send(signal.SIGSTOP)
        assert wait_state(target, lambda state: state == 'T') == 'T'
        lease_clock.value = lease.expires_ns
        assert wait_state(target, lambda state: state in {'R', 'S', 'D', 'I'}) != 'T'
    finally:
        target.resume_cleanup(); lease.finish(); target.close()


def test_guard_scheduled_after_expiry_drains_buffered_stop_intent(child,monkeypatch,lease_clock):
    fork=os.fork
    read_fd,write_fd=os.pipe()
    def delayed_fork():
        pid=fork()
        if pid==0:
            os.close(write_fd)
            assert os.read(read_fd,1)==b'R'
            os.close(read_fd)
        return pid
    monkeypatch.setattr(actor.os,'fork',delayed_fork)
    target=actor.SignalTarget(identity.process_context(child.pid))
    lease=actor.ResumeLease(target,time.monotonic_ns()+200_000_000)
    os.close(read_fd)
    try:
        lease.before_stop();target.send(signal.SIGSTOP)
        assert wait_state(target,lambda state:state=='T')=='T'
        lease_clock.value=lease.expires_ns
        os.write(write_fd,b'R');os.close(write_fd);write_fd=None
        assert wait_state(target,lambda state:state in {'R','S','D','I'})!='T'
    finally:
        if write_fd is not None:os.write(write_fd,b'R');os.close(write_fd)
        target.resume_cleanup();lease.finish();target.close()


def test_stop_dispatch_and_expiry_cleanup_are_serialized(child,monkeypatch,lease_clock):
    target=actor.SignalTarget(identity.process_context(child.pid))
    lease=actor.ResumeLease(target,time.monotonic_ns()+200_000_000)
    original=target.current
    def delayed_current():
        value=original();lease_clock.value=lease.expires_ns;return value
    try:
        lease.before_stop()
        monkeypatch.setattr(target,'current',delayed_current)
        target.send(signal.SIGSTOP)
        assert wait_state(target,lambda state:state in {'R','S','D','I'})!='T'
    finally:
        monkeypatch.setattr(target,'current',original)
        target.resume_cleanup();lease.finish();target.close()


def test_expired_lease_cannot_dispatch_a_new_stop(child):
    target=actor.SignalTarget(identity.process_context(child.pid))
    lease=actor.ResumeLease(target,time.monotonic_ns()+50_000_000)
    try:
        time.sleep(.08)
        with pytest.raises(ValueError,match='STOP_LEASE_EXPIRED'):lease.before_stop()
        with pytest.raises(ValueError,match='STOP_LEASE_EXPIRED'):target.send(signal.SIGSTOP)
        assert target.current()[0]!='T' and child.poll() is None
    finally:target.resume_cleanup();lease.finish();target.close()


def test_actor_death_while_holding_dispatch_lock_releases_kernel_lock(child):
    expected=identity.process_context(child.pid);read_fd,write_fd=os.pipe();worker=os.fork()
    if worker==0:
        os.close(read_fd)
        try:
            target=actor.SignalTarget(expected)
            lease=actor.ResumeLease(target,time.monotonic_ns()+2_000_000_000)
            lease.before_stop()
            with target.stop_guard():
                signal.pidfd_send_signal(target.fd,signal.SIGSTOP)
                os.write(write_fd,b'T');os._exit(77)
        except BaseException:os.write(write_fd,b'E');os._exit(78)
    os.close(write_fd)
    target=actor.SignalTarget(expected)
    try:
        assert os.read(read_fd,1)==b'T'
        _,status=os.waitpid(worker,0);assert os.waitstatus_to_exitcode(status)==77
        assert wait_state(target,lambda state:state in {'R','S','D','I'})!='T'
    finally:os.close(read_fd);target.resume_cleanup();target.close()


def test_actor_exit_without_finally_still_resumes_target(child):
    expected = identity.process_context(child.pid)
    read_fd, write_fd = os.pipe()
    worker = os.fork()
    if worker == 0:
        os.close(read_fd)
        try:
            target = actor.SignalTarget(expected)
            lease = actor.ResumeLease(target, time.monotonic_ns() + 2_000_000_000)
            lease.before_stop(); target.send(signal.SIGSTOP)
            wait_state(target, lambda state: state == 'T')
            os.write(write_fd, b'T')
            # finally/finish 없이 죽는 외부 actor를 재현한다.
            os._exit(77)
        except BaseException:
            os.write(write_fd, b'E'); os._exit(78)
    os.close(write_fd)
    target = actor.SignalTarget(expected)
    try:
        assert os.read(read_fd, 1) == b'T'
        _, status = os.waitpid(worker, 0)
        assert os.waitstatus_to_exitcode(status) == 77
        assert wait_state(target, lambda state: state in {'R', 'S', 'D', 'I'}) != 'T'
        assert identity.process_context(child.pid) == expected and child.poll() is None
    finally:
        os.close(read_fd); target.resume_cleanup(); target.close()


def test_death_is_exit_and_never_sigcont_release(child, monkeypatch):
    target = actor.SignalTarget(identity.process_context(child.pid))
    try:
        target.send(signal.SIGKILL)
        deadline = time.monotonic() + 3
        while not target.exited() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert target.exited()
        calls = []
        monkeypatch.setattr(target, 'send', lambda number: calls.append(number))
        target.resume_cleanup()
        assert calls == []
        with pytest.raises(ValueError, match='RANK_RPC_SIGNAL_GENERATION'):
            target.current()
    finally:
        target.close()


@pytest.mark.parametrize('kind', ['delay', 'death', 'exception'])
@pytest.mark.parametrize('bound', [False, True])
def test_full_actor_uses_real_source_barrier_and_owned_child_pidfd(configured_control, monkeypatch, kind, bound):
    """HTTP만 대역이고 원 RPC/barrier·자식 PID/신호·해제는 실제로 실행한다."""
    from multiprocessing import Pipe
    module, observer, rows, inner, outer, scheduled, control, _ = configured_control
    product = None
    if bound:
        from tests.test_rank_rpc_product_binding import supply
        product = supply(control, monkeypatch)
    control.profile['request_binding'].update(external_prefix='cmpl-', external_suffix='-0')
    parent, child_end = Pipe()
    worker = os.fork()
    if worker == 0:
        parent.close()
        try:
            while True:
                request = child_end.recv()
                scheduled.num_scheduled_tokens = {request: 1}
                try:
                    outer.execute(scheduled)
                    child_end.send(200)
                except actor.control_code.NativeLabFault:
                    child_end.send(500)
        except (EOFError, OSError):
            pass
        finally:
            child_end.close(); os._exit(0)
    child_end.close()
    def client(model, header, key):
        started = time.monotonic_ns()
        module.External.header(header)
        parent.send('cmpl-' + header + '-0')
        try:
            status = parent.recv()
        except (EOFError, OSError):
            status = None
        return {'status': status, 'complete': status is not None,
            'request_started_ns': started, 'response_finished_ns': time.monotonic_ns()}
    monkeypatch.setattr(actor, 'post', client)
    calibration = None
    try:
        assert client('EXAMPLE-model', 'EXAMPLE-normal', control.key)['status'] == 200
        records = control._records()
        proof, = [p for p, _, _ in records if p['point'] == 'Inner.execute' and p['event'] == 'CALL'
                   and p['context']['pid'] == worker]
        header, = [p for p, _, _ in records if p['point'] == 'External.header' and p['event'] == 'RETURN']
        calibration = {'proof': proof, 'header_context': header['context'], 'assignment_context': None,
                       'initial_capture_sha256': '9' * 64}
        owner = {'scope': 'EXAMPLE'}
        if bound:
            owner['pod_uid'] = product['pod_uid']
        result = actor.exercise(control, calibration, 'EXAMPLE-model', kind, owner, hold_ms=10, product_binding=product)
        if bound:
            from tools.rank_rpc_product_binding import require_case_binding
            assert require_case_binding(result, product) is None
            signed, _ = control.read('control-case-result.json')
            assert signed == result and signed['arm']['schema'] == 'guard-rank-rpc-arm/2'
        if kind == 'delay':
            assert result['physical_action']['after_state'] == 'T'
            assert result['physical_resume']['before_state'] == 'T'
            assert result['physical_resume']['after_state'] in {'R', 'S', 'D', 'I'}
            assert result['client']['status'] == 200
        elif kind == 'death':
            assert result['physical_action']['after_state'] == 'EXITED'
            assert result['physical_resume'] is None and result['client']['status'] is None
        else:
            assert result['physical_action']['signal'] is None
            assert result['client']['status'] == 500
        assert result['scope_complete'] is result['producer_registered'] is result['engine_group_verified'] is False
    finally:
        parent.close()
        # 시험이 생성하고 아직 회수하지 않은 자식만 정리한다.
        if kind != 'death' or calibration is None:
            try:
                os.kill(worker, signal.SIGKILL)
            except ProcessLookupError:
                pass
        os.waitpid(worker, 0)
