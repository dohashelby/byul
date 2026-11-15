"""소유 Pod 안의 정상 요청·pidfd 신호·해제 영수증을 만드는 lab 후보.

새 자원/이미지를 만들지 않고 같은 PID namespace/UID의 검증한 대상만 다룬다.
READY는 적용 증거가 아니며 결과를 기존 판정/등록으로 자동 승격하지 않는다.
"""
import argparse
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import select
import signal
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import vllm_rank_ipc_identity as identity
from . import vllm_lifecycle_sensor as base
from . import vllm_rank_rpc_control as control_code


def _id(value, key):
    return hmac.new(key, b'guard-rank-request-id/1\0' + value.encode(), sha256).hexdigest()


def _state(pid):
    raw = Path('/proc/{}/status'.format(pid)).read_text()
    values = dict(line.split(':', 1) for line in raw.splitlines() if ':' in line)
    return values['State'].strip().split()[0], int(values['SigCgt'].strip(), 16)


class SignalTarget:
    def __init__(self, expected):
        self.expected, self.pid, self.fd = control_code.context(expected), expected['pid'], None
        if self.pid == os.getpid():
            raise ValueError('RANK_RPC_SIGNAL_SELF')
        if identity.process_context(self.pid) != expected:
            raise ValueError('RANK_RPC_SIGNAL_GENERATION')
        self.fd = os.pidfd_open(self.pid, 0)
        try:
            if identity.process_context(self.pid) != expected or self.exited():
                raise ValueError('RANK_RPC_SIGNAL_GENERATION')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def exited(self):
        poll = select.poll(); poll.register(self.fd, select.POLLIN)
        return bool(poll.poll(0))

    def current(self):
        if self.exited() or identity.process_context(self.pid) != self.expected:
            raise ValueError('RANK_RPC_SIGNAL_GENERATION')
        return _state(self.pid)

    def send(self, number):
        if number not in {signal.SIGSTOP, signal.SIGCONT, signal.SIGKILL}:
            raise ValueError('RANK_RPC_SIGNAL_KIND')
        self.current()
        signal.pidfd_send_signal(self.fd, number)

    def resume_cleanup(self):
        if not self.exited():
            # D 상태의 pending SIGSTOP도 지운다. PID 재사용에는 보유 pidfd를 쓰지 않는다.
            self.send(signal.SIGCONT)


class ResumeLease:
    """신호 전 stop 의도를 받고 actor 사망/기한 만료 시 같은 pidfd에 CONT한다."""
    def __init__(self, target, expires_ns, control=None):
        read_fd, self.write_fd = os.pipe()
        self.pid = os.fork()
        self.intent = False
        if self.pid == 0:
            os.close(self.write_fd)
            potential, resumed, reason = False, False, 'LEASE_EXPIRED'
            try:
                poll = select.poll(); poll.register(read_fd, select.POLLIN | select.POLLHUP)
                while time.monotonic_ns() < expires_ns:
                    wait_ms = max(1, min(200, (expires_ns - time.monotonic_ns()) // 1_000_000))
                    if not poll.poll(wait_ms):
                        continue
                    raw = os.read(read_fd, 32)
                    if not raw:
                        reason = 'ACTOR_PIPE_CLOSED'
                        break
                    potential = potential or b's' in raw
                    if b'd' in raw:
                        reason = 'ACTOR_COMPLETED'
                        potential = False
                        break
                if potential and not target.exited():
                    target.resume_cleanup(); resumed = True
                if control is not None:
                    control.write('control-lease-guard.json', {'schema': 'guard-rank-rpc-resume-guard/1',
                        'target_context': target.expected, 'reason': reason, 'sigcont_sent': resumed,
                        'observed_ns': time.monotonic_ns()})
            except BaseException:
                # 마지막 정리도 보유 pidfd/같은 세대만 대상으로 한다.
                try:
                    if potential:
                        target.resume_cleanup()
                except BaseException:
                    pass
            finally:
                os.close(read_fd)
                os._exit(0)
        os.close(read_fd)

    def before_stop(self):
        os.write(self.write_fd, b's')
        self.intent = True

    def finish(self):
        if self.write_fd is not None:
            try:
                os.write(self.write_fd, b'd')
            except BrokenPipeError:
                pass
            finally:
                os.close(self.write_fd); self.write_fd = None
        os.waitpid(self.pid, 0)


def post(model, header, key):
    started = time.monotonic_ns()
    body = json.dumps({'model': model, 'prompt': 'test', 'max_tokens': 1, 'temperature': 0}).encode()
    request = urllib.request.Request('http://127.0.0.1:8000/v1/completions', data=body,
        headers={'Content-Type': 'application/json', 'X-Request-Id': header})
    result = {'request_started_ns': started, 'context': base._context(),
              'header_hmac_sha256': _id(header, key), 'status': None, 'complete': False,
              'response_sha256': None, 'response_id_hmac_sha256': None}
    try:
        try:
            response = urllib.request.urlopen(request, timeout=100)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(1_048_577)
            result['status'], result['complete'] = response.status, len(raw) <= 1_048_576
            if result['complete']:
                result['response_sha256'] = sha256(raw).hexdigest()
                try:
                    value = json.loads(raw)
                    if type(value) is dict and type(value.get('id')) is str:
                        result['response_id_hmac_sha256'] = _id(value['id'], key)
                except (ValueError, UnicodeError):
                    pass
    except (OSError, ValueError):
        result['transport_error'] = True
    result['response_finished_ns'] = time.monotonic_ns()
    return result


def _endpoint(value, name):
    point = value['endpoints'][name]
    if (point['schema'] != 'guard-rank-ipc-identity/1' or point['source_authenticated'] is not False
            or point['engine_group_verified'] is not False or not control_code.fingerprint(point['identity_hmac_sha256'])):
        raise ValueError('RANK_RPC_INITIAL_IPC')
    return point['identity_hmac_sha256']


def initial_topology(profile, records, proof):
    topology = profile['ipc_topology']
    parents = [p for p, _, _ in records if p['point'] == topology['parent_point']]
    workers = [p for p, _, _ in records if p['point'] == topology['worker_point']]
    if len(parents) != 1:
        raise ValueError('RANK_RPC_INITIAL_IPC')
    parent = parents[0]
    if parent['scalars']['configured_tp_size'] != 2 or len(parent['members']) != len(topology['child_ranks']):
        raise ValueError('RANK_RPC_INITIAL_IPC')
    seen = set()
    for member in parent['members']:
        matches = [w for w in workers if w['context'] == member['processes']['worker']]
        if len(matches) != 1:
            raise ValueError('RANK_RPC_INITIAL_IPC')
        worker = matches[0]; rank = worker['scalars']['rank']
        if rank in seen or rank not in topology['child_ranks']:
            raise ValueError('RANK_RPC_INITIAL_IPC')
        seen.add(rank)
        if topology['kind'] == 'task-pipe-driver':
            for name in ('task_read', 'task_write', 'result_read', 'result_write'):
                if _endpoint(member, name) != _endpoint(worker, name):
                    raise ValueError('RANK_RPC_INITIAL_IPC')
            if (_endpoint(worker, 'task_read') != _endpoint(worker, 'task_write')
                    or _endpoint(worker, 'result_read') != _endpoint(worker, 'result_write')
                    or _endpoint(worker, 'task_read') == _endpoint(worker, 'result_read')):
                raise ValueError('RANK_RPC_INITIAL_IPC')
        elif topology['kind'] == 'broadcast-workers':
            if (member['scalars']['rank'] != rank or worker['scalars']['reader_rank'] != rank
                    or worker['scalars']['configured_tp_size'] != 2
                    or _endpoint(parent, 'broadcast') != _endpoint(worker, 'broadcast')
                    or _endpoint(member, 'response') != _endpoint(worker, 'response')):
                raise ValueError('RANK_RPC_INITIAL_IPC')
        else:
            raise ValueError('RANK_RPC_INITIAL_IPC')
    if seen != set(topology['child_ranks']):
        raise ValueError('RANK_RPC_INITIAL_IPC')
    if topology['target_parent_kind'] == 'executor':
        expected = parent
    else:
        matches = [w for w in workers if w['scalars']['rank'] == 0]
        if len(matches) != 1 or len({_endpoint(w, 'response') for w in workers}) != len(workers):
            raise ValueError('RANK_RPC_INITIAL_IPC')
        expected = matches[0]
    if (proof['context'] != expected['context']
            or proof['rpc_parent_object_hmac_sha256'] != expected['object_hmac_sha256']):
        raise ValueError('RANK_RPC_INITIAL_IPC')
    return {'initial_ipc_metadata_correlated': True, 'engine_group_verified': False,
            'source_authenticated': False, 'producer_registered': False}


def calibrate(control, model):
    header = 'byul-normal-' + uuid.uuid4().hex
    normal = post(model, header, control.key)
    binding = control.profile['request_binding']
    external = _id(binding['external_prefix'] + header + binding['external_suffix'], control.key)
    if (normal['status'] != 200 or not normal['complete']
            or normal['response_id_hmac_sha256'] != _id(binding['external_prefix'] + header, control.key)):
        raise ValueError('RANK_RPC_NORMAL_CLIENT_UNVERIFIED')
    records = control._records(include_ipc=True)
    headers = [p for p, _, h in records if h.get('kind') == 'external-id'
        and p.get('event') == 'RETURN' and p.get('external_request_hmac_sha256') == normal['header_hmac_sha256']]
    if len(headers) != 1:
        raise ValueError('RANK_RPC_NORMAL_HEADER_UNVERIFIED')
    assignment = None; internal = external
    if binding['kind'] == 'assigned':
        matches = [p for p, _, h in records if h.get('kind') == 'assignment' and p.get('event') == 'RETURN'
            and p.get('assignment', {}).get('external_hmac_sha256') == external]
        if len(matches) != 1:
            raise ValueError('RANK_RPC_NORMAL_ASSIGNMENT_UNVERIFIED')
        assignment = matches[0]; internal = assignment['assignment']['internal_hmac_sha256']
    candidates = [p for p, _, h in records if h.get('fault_eligible') is True and p.get('event') == 'CALL'
        and p.get('request_hmac_sha256') == [internal] and p.get('rpc_parent_frame_verified') is True
        and p.get('rpc_parent_worker_verified') is True and p['scalars'].get('gpu_rank') == 0
        and p['scalars'].get('configured_tp_size') == 2]
    if not candidates or len({control_code.digest([p['context'], p['rpc_parent_object_hmac_sha256']]) for p in candidates}) != 1:
        raise ValueError('RANK_RPC_NORMAL_ACTIVE_FRAME_UNVERIFIED')
    proof = min(candidates, key=lambda p: p['observation_ns'])
    topology = initial_topology(control.profile, records, proof)
    return {'normal_client': normal, 'proof': proof, 'header_context': headers[0]['context'],
        'assignment_context': None if assignment is None else assignment['context'],
        'topology': topology, 'initial_capture_sha256': control_code.digest(sorted(raw for _, raw, _ in records))}


def exercise(control, calibration, model, kind, owner, hold_ms=1000):
    if kind not in {'delay', 'death', 'exception'} or type(hold_ms) is not int or not 1 <= hold_ms <= 5000:
        raise ValueError('RANK_RPC_CASE_KIND')
    proof = calibration['proof']; header = 'byul-fault-' + uuid.uuid4().hex
    binding = control.profile['request_binding']; now = time.monotonic_ns()
    target = {'schema': 'guard-rank-rpc-lab-target/1', 'owner': owner,
              'image': control.settings['image'], 'model': model, 'context': proof['context'],
              'parent_object_hmac_sha256': proof['rpc_parent_object_hmac_sha256'],
              'initial_capture_sha256': calibration['initial_capture_sha256']}
    arm = {'schema': 'guard-rank-rpc-arm/1', 'session': control.settings['session'],
        'image': control.settings['image'], 'settings_sha256': control.settings_sha256,
        'assets_sha256': control_code.digest(control.settings['assets']), 'target_sha256': control_code.digest(target),
        'nonce': uuid.uuid4().hex, 'fault_kind': kind, 'issued_ns': now, 'expires_ns': now + 80_000_000_000,
        'target_context': proof['context'], 'parent_object_hmac_sha256': proof['rpc_parent_object_hmac_sha256'],
        'target_point': proof['point'], 'source_sha256': proof['source_sha256'],
        'body_code_sha256': proof['body_code_sha256'], 'header_hmac_sha256': _id(header, control.key),
        'external_hmac_sha256': _id(binding['external_prefix'] + header + binding['external_suffix'], control.key),
        'header_context': calibration['header_context'], 'assignment_context': calibration['assignment_context'],
        'initial_capture_sha256': calibration['initial_capture_sha256']}
    target_fd = SignalTarget(proof['context']); lease = None; potential_stop = False
    client_result = []; receipt = None; ready = None; ready_sha = None; resumed = None
    try:
        state, mask = target_fd.current()
        if state not in {'R', 'S', 'D', 'I'} or mask & (1 << (signal.SIGCONT - 1)):
            raise ValueError('RANK_RPC_SIGNAL_INITIAL_STATE')
        if kind == 'delay':
            lease = ResumeLease(target_fd, arm['expires_ns'] + 5_000_000_000, control)
        arm_sha = control.write('control-arm.json', arm)
        client = threading.Thread(target=lambda: client_result.append(post(model, header, control.key)), daemon=True)
        client.start()
        while time.monotonic_ns() < arm['expires_ns']:
            try:
                ready, ready_sha = control.read('control-ready.json'); break
            except FileNotFoundError:
                time.sleep(0.01)
        if (ready is None or ready.get('schema') != 'guard-rank-rpc-ready/1'
                or ready.get('arm_sha256') != arm_sha or ready.get('nonce') != arm['nonce']
                or ready.get('context') != proof['context'] or ready.get('rpc_active') is not True
                or ready.get('prepared_assets_sha256') != arm['assets_sha256']):
            raise ValueError('RANK_RPC_READY_UNVERIFIED')
        receipt = {'schema': 'guard-rank-rpc-action-receipt/1', 'arm_sha256': arm_sha,
            'ready_sha256': ready_sha, 'context': proof['context'], 'fault_kind': kind,
            'source_refs': ready['source_refs'], 'before_started_ns': time.monotonic_ns()}
        state, _ = target_fd.current(); receipt.update(before_state=state, before_finished_ns=time.monotonic_ns())
        if state not in {'R', 'S', 'D', 'I'}:
            raise ValueError('RANK_RPC_SIGNAL_INITIAL_STATE')
        if kind == 'exception':
            receipt.update(action='source-exception', signal=None, published_ns=time.monotonic_ns())
            receipt_sha = control.write('control-physical-action.json', receipt)
            control.write('control-action.json', {'schema': 'guard-rank-rpc-action/1', 'nonce': arm['nonce'],
                'arm_sha256': arm_sha, 'ready_sha256': ready_sha, 'action': 'raise', 'receipt_sha256': receipt_sha})
        else:
            if lease is not None:
                lease.before_stop(); potential_stop = True
            number = signal.SIGSTOP if kind == 'delay' else signal.SIGKILL
            receipt.update(action='pidfd-signal', signal=int(number), signal_started_ns=time.monotonic_ns())
            target_fd.send(number); receipt['signal_finished_ns'] = time.monotonic_ns()
            deadline = time.monotonic_ns() + 3_000_000_000
            while time.monotonic_ns() < deadline:
                if kind == 'death' and target_fd.exited():
                    receipt['after_state'] = 'EXITED'; break
                if kind == 'delay' and target_fd.current()[0] == 'T':
                    receipt['after_state'] = 'T'; break
                time.sleep(0.005)
            else:
                raise ValueError('RANK_RPC_SIGNAL_EFFECT_UNVERIFIED')
            receipt['after_observed_ns'] = time.monotonic_ns()
            receipt_sha = control.write('control-physical-action.json', receipt)
            if kind == 'delay':
                time.sleep(hold_ms / 1000)
                resumed = {'schema': 'guard-rank-rpc-resume-receipt/1', 'context': proof['context'],
                    'arm_sha256': arm_sha, 'ready_sha256': ready_sha, 'signal': int(signal.SIGCONT),
                    'before_started_ns': time.monotonic_ns()}
                resumed['before_state'] = target_fd.current()[0]; resumed['before_finished_ns'] = time.monotonic_ns()
                if resumed['before_state'] != 'T':
                    raise ValueError('RANK_RPC_SIGNAL_RESUME_UNVERIFIED')
                resumed['signal_started_ns'] = time.monotonic_ns(); target_fd.send(signal.SIGCONT)
                resumed['signal_finished_ns'] = time.monotonic_ns()
                deadline = time.monotonic_ns() + 3_000_000_000
                while time.monotonic_ns() < deadline:
                    resumed['after_state'] = target_fd.current()[0]
                    if resumed['after_state'] in {'R', 'S', 'D', 'I'}:
                        break
                    time.sleep(0.005)
                else:
                    raise ValueError('RANK_RPC_SIGNAL_RESUME_UNVERIFIED')
                resumed['after_observed_ns'] = time.monotonic_ns()
                resume_sha = control.write('control-physical-resume.json', resumed)
                control.write('control-action.json', {'schema': 'guard-rank-rpc-action/1', 'nonce': arm['nonce'],
                    'arm_sha256': arm_sha, 'ready_sha256': ready_sha, 'action': 'release', 'receipt_sha256': resume_sha})
        client.join(105)
        if client.is_alive() or len(client_result) != 1:
            raise ValueError('RANK_RPC_CLIENT_UNOBSERVED')
        result = {'schema': 'guard-rank-rpc-lab-case/1', 'target': target, 'arm': arm,
            'ready': ready, 'physical_action': receipt, 'physical_resume': resumed,
            'client': client_result[0], 'source_authenticated': False, 'engine_group_verified': False,
            'producer_registered': False, 'scope_complete': False}
        control.write('control-case-result.json', result)
        return result
    finally:
        try:
            if potential_stop:
                try:
                    target_fd.resume_cleanup()
                finally:
                    if lease is not None:
                        lease.finish()
            elif lease is not None:
                lease.finish()
        finally:
            target_fd.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=['delay', 'death', 'exception'], required=True)
    parser.add_argument('--model', default='test-model')
    parser.add_argument('--model-receipt-sha256', required=True)
    args = parser.parse_args(argv)
    if not control_code.fingerprint(args.model_receipt_sha256):
        raise ValueError('RANK_RPC_MODEL_RECEIPT')
    from . import vllm_rank_ipc_runtime as runtime
    settings, settings_sha = runtime._settings(); runtime._freeze(settings)
    observer = runtime._INSTALLED.observer
    control = control_code.Control(settings, settings_sha, observer.ipc.profile, lambda value: None,
                                   lambda: runtime._freeze(settings))
    calibration = calibrate(control, args.model)
    control.write('control-calibration.json', calibration)
    owner = {'pod_uid': os.environ.get('GUARD_POD_UID'), 'model_receipt_sha256': args.model_receipt_sha256}
    if type(owner['pod_uid']) is not str or not owner['pod_uid']:
        raise ValueError('RANK_RPC_POD_OWNER')
    result = exercise(control, calibration, args.model, args.kind, owner)
    print(json.dumps({'사례': args.kind, '클라이언트_HTTP': result['client']['status'],
                      '물리_신호': result['physical_action']['signal'], '전체_수락': False}, ensure_ascii=False))
