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
import tempfile
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


class _StopGuard:
    """Plain source-verifiable methods; no foreign decorator wrapper code."""
    def __init__(self,fd):self.fd=fd
    def __enter__(self):
        import fcntl
        fcntl.flock(self.fd,fcntl.LOCK_EX)
        return self
    def __exit__(self,kind,value,traceback):
        import fcntl
        fcntl.flock(self.fd,fcntl.LOCK_UN)


class SignalTarget:
    def __init__(self, expected):
        self.expected, self.pid, self.fd = control_code.context(expected), expected['pid'], None
        self._stop_lease_fd,self._stop_expires_ns=None,None
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
        if number==signal.SIGSTOP and self._stop_lease_fd is not None:
            with self.stop_guard():
                if time.monotonic_ns()>=self._stop_expires_ns:raise ValueError('RANK_RPC_STOP_LEASE_EXPIRED')
                self.current()
                signal.pidfd_send_signal(self.fd,number)
        else:
            self.current()
            signal.pidfd_send_signal(self.fd, number)

    def stop_guard(self):
        return _StopGuard(self._stop_lease_fd)

    def resume_cleanup(self):
        if not self.exited():
            # D 상태의 pending SIGSTOP도 지운다. PID 재사용에는 보유 pidfd를 쓰지 않는다.
            self.send(signal.SIGCONT)


class ResumeLease:
    """신호 전 stop 의도를 받고 actor 사망/기한 만료 시 같은 pidfd에 CONT한다."""
    def __init__(self, target, expires_ns, control=None):
        import fcntl  # Load before fork; no import lock is acquired by the child.
        self.target,self.expires_ns=target,expires_ns
        self.lock_file=tempfile.TemporaryFile()
        target._stop_lease_fd=self.lock_file.fileno();target._stop_expires_ns=expires_ns
        # Open both descriptions before fork. Failure cannot leave a parent
        # able to STOP a target without an operational cleanup description.
        guard_fd=os.open('/proc/self/fd/'+str(self.lock_file.fileno()),os.O_RDWR)
        read_fd, self.write_fd = os.pipe()
        try:self.pid = os.fork()
        except BaseException:
            os.close(guard_fd);os.close(read_fd);os.close(self.write_fd);self.lock_file.close()
            target._stop_lease_fd=target._stop_expires_ns=None
            raise
        self.intent = False
        if self.pid == 0:
            os.close(self.write_fd)
            # A separate open-file description is essential: flock inherited
            # through fork would otherwise have the same owner on both sides.
            self.lock_file.close()
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
                fcntl.flock(guard_fd,fcntl.LOCK_EX)
                try:
                    # The guard may first run after its deadline. Drain the
                    # buffered intent while the parent cannot dispatch STOP.
                    while poll.poll(0):
                        raw=os.read(read_fd,32)
                        if not raw:
                            reason='ACTOR_PIPE_CLOSED';break
                        potential=potential or b's' in raw
                        if b'd' in raw:
                            reason='ACTOR_COMPLETED';potential=False;break
                    if potential and not target.exited():
                        target.resume_cleanup(); resumed = True
                finally:fcntl.flock(guard_fd,fcntl.LOCK_UN)
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
                os.close(guard_fd)
                os.close(read_fd)
                os._exit(0)
        os.close(guard_fd)
        os.close(read_fd)

    def before_stop(self):
        with self.target.stop_guard():
            if time.monotonic_ns()>=self.expires_ns:raise ValueError('RANK_RPC_STOP_LEASE_EXPIRED')
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
        try:os.waitpid(self.pid, 0)
        finally:
            self.target._stop_lease_fd=self.target._stop_expires_ns=None
            self.lock_file.close()


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
    spec = profile['rpc_sources'][proof['source_module']]['hooks'][proof['point']]
    endpoint = spec.get('required_initial_endpoint')
    if endpoint is not None and _endpoint(proof,endpoint) != _endpoint(expected,endpoint):
        raise ValueError('RANK_RPC_INITIAL_IPC')
    return {'initial_ipc_metadata_correlated': True, 'engine_group_verified': False,
            'source_authenticated': False, 'producer_registered': False}


def normal_rpc_returns(profile, records, proof, client):
    """Correlate each declared broadcast member's original CALL/RETURN pair.

    The caller supplies records already verified by Control._records. This
    proves bounded Python RPC returns, not GPU completion, failure detection,
    authenticated producer identity or whole-group recovery.
    """
    result = {'schema': 'guard-rank-normal-rpc-returns/1', 'members': [],
        'all_declared_member_returns_observed': False, 'gpu_completion_verified': False,
        'engine_group_verified': False, 'source_authenticated': False,
        'producer_registered': False, 'scope_complete': False, 'diagnostic_codes': []}
    try:
        topology = profile['ipc_topology']
        if topology['kind'] != 'broadcast-workers' or topology['target_parent_kind'] != 'worker':
            raise ValueError('RANK_NORMAL_RPC_TOPOLOGY_UNSUPPORTED')
        initial_topology(profile, records, proof)
        expected = topology['child_ranks']
        request = proof['request_hmac_sha256']
        if (type(request) is not list or len(request) != 1 or not control_code.fingerprint(request[0])
                or type(client.get('request_started_ns')) is not int or type(client.get('response_finished_ns')) is not int
                or not 0 <= client['request_started_ns'] <= client['response_finished_ns']
                or client.get('status') != 200 or client.get('complete') is not True):
            raise ValueError('RANK_NORMAL_RPC_CLIENT_UNVERIFIED')
        initial = [row for row, _, _ in records if row['point'] == topology['worker_point']]
        if len(initial) != len(expected):
            raise ValueError('RANK_NORMAL_RPC_MEMBERS_UNVERIFIED')
        rows = [row for row, _, _ in records if row.get('point') == proof['point']
            and row.get('request_hmac_sha256') == request]
        parent_rows = [row for row, _, _ in records if row.get('point') ==
            profile['rpc_sources'][proof['source_module']]['hooks'][proof['point']]['parent']['point']
            and row.get('request_hmac_sha256') == request]
        used, used_parents = set(), set()
        for rank in expected:
            member, = [row for row in initial if row['scalars']['rank'] == rank]
            if (control_code.clock(member['context']) != control_code.clock(client['context'])
                    or any(member['context'][key] != client['context'][key] for key in ('pid_namespace', 'uid'))):
                raise ValueError('RANK_NORMAL_RPC_CONTEXT_UNVERIFIED')
            selected = [row for row in rows if row.get('context') == member['context']]
            calls = [row for row in selected if row.get('event') == 'CALL']
            if not calls or any(row.get('event') not in {'CALL', 'RETURN'} for row in selected):
                raise ValueError('RANK_NORMAL_RPC_RETURN_UNOBSERVED')
            paired = set()
            for call in calls:
                fingerprint = call['call_hmac_sha256']
                if fingerprint in used:
                    raise ValueError('RANK_NORMAL_RPC_CALL_AMBIGUOUS')
                used.add(fingerprint)
                returned, = [row for row in selected if row.get('event') == 'RETURN'
                    and row.get('call_hmac_sha256') == fingerprint]
                parent_call, = [row for row in parent_rows if row.get('event') == 'CALL'
                    and row.get('call_hmac_sha256') == call['rpc_parent_call_hmac_sha256']]
                parent_return, = [row for row in parent_rows if row.get('event') == 'RETURN'
                    and row.get('call_hmac_sha256') == parent_call['call_hmac_sha256']]
                if parent_call['call_hmac_sha256'] in used_parents:
                    raise ValueError('RANK_NORMAL_RPC_CALL_AMBIGUOUS')
                used_parents.add(parent_call['call_hmac_sha256'])
                stable = ('context', 'point', 'source_module', 'source_sha256', 'body_code_sha256',
                    'request_hmac_sha256', 'object_hmac_sha256', 'rpc_parent_call_hmac_sha256',
                    'rpc_parent_object_hmac_sha256', 'operation_started_ns')
                if (any(call[key] != returned[key] or parent_call[key] != parent_return[key] for key in stable)
                        or call.get('gaps') or returned.get('gaps')
                        or call.get('original_invoked') is not False or returned.get('original_invoked') is not True
                        or any(row.get('rpc_parent_frame_verified') is not True
                            or row.get('rpc_parent_worker_verified') is not True for row in (call, returned))
                        or any(row.get('scalars', {}).get('gpu_rank') != rank
                            or type(row.get('scalars', {}).get('gpu_rank')) is not int
                            or type(row['scalars'].get('configured_tp_size')) is not int
                            or row['scalars'].get('configured_tp_size') != len(expected) for row in (call, returned))
                        or call['rpc_parent_object_hmac_sha256'] != member['object_hmac_sha256']
                        or any(row.get('context') != member['context'] or row.get('gaps')
                            or row.get('object_hmac_sha256') != member['object_hmac_sha256']
                            or type(row.get('scalars', {}).get('rpc_rank')) is not int
                            or row['scalars']['rpc_rank'] != rank for row in (parent_call, parent_return))
                        or parent_call.get('original_invoked') is not False or parent_return.get('original_invoked') is not True
                        or not client['request_started_ns'] <= parent_call['observation_ns'] <= call['observation_ns']
                            <= returned['observation_ns'] <= parent_return['observation_ns'] <= client['response_finished_ns']):
                    raise ValueError('RANK_NORMAL_RPC_LINEAGE_UNVERIFIED')
                paired.update((id(call), id(returned)))
            if len(paired) != len(selected):
                raise ValueError('RANK_NORMAL_RPC_CALL_AMBIGUOUS')
            result['members'].append({'rank': rank, 'context': member['context'], 'completed_rpc_calls': len(calls),
                'worker_object_hmac_sha256': member['object_hmac_sha256']})
        if (len(rows) != sum(member['completed_rpc_calls'] * 2 for member in result['members'])
                or len(parent_rows) != 2 * len(used_parents)):
            raise ValueError('RANK_NORMAL_RPC_FOREIGN_MEMBER')
        result['all_declared_member_returns_observed'] = True
    except (KeyError, TypeError, ValueError) as error:
        code = error.args[0] if type(error) is ValueError and len(error.args) == 1 else None
        result['diagnostic_codes'] = [code if type(code) is str and code.startswith('RANK_NORMAL_RPC_')
            else 'RANK_NORMAL_RPC_PROOF_INVALID']
    return result


def calibrate(control, model, *, require_all_rank_rpc_returns=False, fault_kind=None):
    if (type(require_all_rank_rpc_returns) is not bool
            or fault_kind is not None and fault_kind not in {'delay', 'death', 'exception', 'communication-stall'}):
        raise ValueError('RANK_NORMAL_RPC_PREFLIGHT_INVALID')
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
    eligible = [(p, h) for p, _, h in records if h.get('fault_eligible') is True and p.get('event') == 'CALL'
        and p.get('request_hmac_sha256') == [internal] and p.get('rpc_parent_frame_verified') is True
        and p.get('rpc_parent_worker_verified') is True and p['scalars'].get('gpu_rank') == 0
        and p['scalars'].get('configured_tp_size') == 2]
    candidates = [p for p,h in eligible if (fault_kind is None and h.get('kind') == 'rpc'
        or fault_kind is not None and fault_kind in h.get('fault_kinds', ['delay', 'death', 'exception']))]
    if not candidates or len({control_code.digest([p['context'], p['rpc_parent_object_hmac_sha256']]) for p in candidates}) != 1:
        raise ValueError('RANK_RPC_NORMAL_ACTIVE_FRAME_UNVERIFIED')
    proof = min(candidates, key=lambda p: p['observation_ns'])
    topology = initial_topology(control.profile, records, proof)
    computation = [p for p,h in eligible if h.get('kind') == 'rpc']
    cohort = normal_rpc_returns(control.profile, records,
        min(computation,key=lambda p:p['observation_ns']) if computation else {}, normal)
    if require_all_rank_rpc_returns and not cohort['all_declared_member_returns_observed']:
        raise ValueError('RANK_NORMAL_RPC_PREFLIGHT_INCOMPLETE')
    return {'normal_client': normal, 'proof': proof, 'header_context': headers[0]['context'],
        'assignment_context': None if assignment is None else assignment['context'],
        'topology': topology, 'normal_rpc_returns': cohort,
        'initial_capture_sha256': control_code.digest(sorted(raw for _, raw, _ in records))}


def _ready_recorded(control, ready, arm, arm_sha, ready_sha, *, phase_name='CONTROL_READY', action_sha=None):
    """READY 파일보다 늦게 닫히는 원 snapshot까지 확인한 뒤에만 장애를 적용한다."""
    if (phase_name not in {'CONTROL_READY','CONTROL_STALL'}
            or phase_name == 'CONTROL_READY' and action_sha is not None
            or phase_name == 'CONTROL_STALL' and not control_code.fingerprint(action_sha)):
        raise ValueError('RANK_RPC_READY_UNVERIFIED')
    matches = [p for p, _, _ in control._records() if p.get('event') == phase_name
        and p.get('call_hmac_sha256') == ready['call_hmac_sha256']]
    if not matches:
        return False
    if len(matches) != 1:
        raise ValueError('RANK_RPC_READY_UNVERIFIED')
    phase, = matches
    expected = {'nonce': arm['nonce'], 'fault_kind': arm['fault_kind'], 'arm_sha256': arm_sha,
        'ready_sha256': ready_sha, 'action_sha256': action_sha, 'target_sha256': arm['target_sha256'],
        'control_code_sha256': control.settings['assets']['guard/adapters/vllm_rank_rpc_control.py']['sha256'],
        'barrier_only': phase_name == 'CONTROL_READY'}
    if (phase.get('control') != expected or phase.get('context') != arm['target_context']
            or phase.get('rpc_parent_call_hmac_sha256') != ready['parent_call_hmac_sha256']
            or phase.get('rpc_parent_object_hmac_sha256') != arm['parent_object_hmac_sha256']
            or phase.get('point') != arm['target_point'] or phase.get('source_sha256') != arm['source_sha256']
            or phase.get('body_code_sha256') != arm['body_code_sha256']
            or phase.get('original_invoked') is not False or phase.get('rpc_parent_frame_verified') is not True
            or phase.get('rpc_parent_worker_verified') is not True
            or type(phase.get('observation_ns')) is not int
            or not ready['ready_ns'] <= phase['observation_ns'] < arm['expires_ns']):
        raise ValueError('RANK_RPC_READY_UNVERIFIED')
    if arm['fault_kind'] == 'communication-stall':
        control_code.reply_binding(arm.get('reply_binding'),phase)
    return True


def exercise(control, calibration, model, kind, owner, hold_ms=1000, *, product_binding=None):
    if (kind not in {'delay', 'death', 'exception', 'communication-stall'} or type(hold_ms) is not int
            or not 1 <= hold_ms <= (30000 if kind == 'communication-stall' else 5000)):
        raise ValueError('RANK_RPC_CASE_KIND')
    proof = calibration['proof']; header = 'byul-fault-' + uuid.uuid4().hex
    if kind == 'communication-stall':
        hook = control.profile['rpc_sources'][proof['source_module']]['hooks'][proof['point']]
        if (hook.get('kind') != 'rpc-reply' or kind not in hook.get('fault_kinds', [])
                or calibration.get('normal_rpc_returns', {}).get('all_declared_member_returns_observed') is not True):
            raise ValueError('RANK_RPC_STALL_NORMAL_PREFLIGHT_UNVERIFIED')
        if product_binding is None:
            raise ValueError('RANK_RPC_STALL_PRODUCT_BINDING_REQUIRED')
    binding = control.profile['request_binding']; now = time.monotonic_ns()
    target = {'schema': 'guard-rank-rpc-lab-target/1', 'owner': owner,
              'image': control.settings['image'], 'model': model, 'context': proof['context'],
              'parent_object_hmac_sha256': proof['rpc_parent_object_hmac_sha256'],
              'initial_capture_sha256': calibration['initial_capture_sha256']}
    if product_binding is not None:
        control_code.product_binding(product_binding, control.settings['session'])
        if (product_binding != control.product_binding()
                or owner.get('pod_uid') != product_binding['pod_uid']):
            raise ValueError('RANK_RPC_PRODUCT_BINDING_MISMATCH')
        target.update(schema='guard-rank-rpc-lab-target/2', product_binding=product_binding)
    reply_scope = None
    if kind == 'communication-stall':
        reply_scope = control_code.reply_binding({'object_hmac_sha256':proof['object_hmac_sha256'],
            'response_hmac_sha256':_endpoint(proof,'response')},proof)
        target.update(schema='guard-rank-rpc-lab-target/3',reply_binding=reply_scope)
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
    if product_binding is not None:
        arm.update(schema='guard-rank-rpc-arm/2', product_binding=product_binding)
    if reply_scope is not None:
        arm.update(schema='guard-rank-rpc-arm/3',reply_binding=reply_scope)
    target_fd = SignalTarget(proof['context']); lease = None; potential_stop = False
    client_result = []; receipt = None; ready = None; ready_sha = None; resumed = None
    stall_action_published = False; stall_release_published = False
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
        # 원 제어는 READY 파일을 쓴 뒤 snapshot을 저장한다. 파일만 보고 STOP/KILL하면
        # snapshot 저장 자체를 막을 수 있으므로 같은 호출의 닫힌 원 기록을 기다린다.
        while time.monotonic_ns() < arm['expires_ns']:
            if _ready_recorded(control, ready, arm, arm_sha, ready_sha):
                break
            time.sleep(0.01)
        else:
            raise ValueError('RANK_RPC_READY_UNVERIFIED')
        if time.monotonic_ns() >= arm['expires_ns']:
            raise ValueError('RANK_RPC_READY_UNVERIFIED')
        receipt = {'schema': 'guard-rank-rpc-action-receipt/1', 'arm_sha256': arm_sha,
            'ready_sha256': ready_sha, 'context': proof['context'], 'fault_kind': kind,
            'source_refs': ready['source_refs'], 'before_started_ns': time.monotonic_ns()}
        state, _ = target_fd.current(); receipt.update(before_state=state, before_finished_ns=time.monotonic_ns())
        if state not in {'R', 'S', 'D', 'I'}:
            raise ValueError('RANK_RPC_SIGNAL_INITIAL_STATE')
        if kind == 'communication-stall':
            receipt.update(action='source-reply-stall', signal=None, published_ns=time.monotonic_ns())
            receipt_sha = control.write('control-physical-action.json', receipt)
            action_sha = control.write('control-action.json', {'schema': 'guard-rank-rpc-action/1', 'nonce': arm['nonce'],
                'arm_sha256': arm_sha, 'ready_sha256': ready_sha, 'action': 'stall', 'receipt_sha256': receipt_sha})
            stall_action_published = True
            while time.monotonic_ns() < arm['expires_ns']:
                if _ready_recorded(control, ready, arm, arm_sha, ready_sha, phase_name='CONTROL_STALL', action_sha=action_sha):
                    break
                time.sleep(.01)
            else:
                raise ValueError('RANK_RPC_STALL_ACTIVATION_UNVERIFIED')
            time.sleep(hold_ms/1000)
            resumed = {'schema': 'guard-rank-rpc-stall-release/1', 'context': proof['context'],
                'arm_sha256': arm_sha, 'ready_sha256': ready_sha, 'published_ns': time.monotonic_ns()}
            resume_sha = control.write('control-physical-resume.json', resumed)
            control.write('control-stall-release.json', {'schema': 'guard-rank-rpc-action/1', 'nonce': arm['nonce'],
                'arm_sha256': arm_sha, 'ready_sha256': ready_sha, 'action': 'release', 'receipt_sha256': resume_sha})
            stall_release_published = True
        elif kind == 'exception':
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
                # A native timeout can terminate this exact pidfd target while
                # it is stopped. Keep the applied fact and client result; a dead
                # target cannot have a verified resume or recovery receipt.
                try:
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
                except ValueError as error:
                    if error.args != ('RANK_RPC_SIGNAL_GENERATION',) or not target_fd.exited():
                        raise
                    resumed = None
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
            if stall_action_published and not stall_release_published:
                # A failed actor does not wait for the source lease to expire.
                # This signed cancellation is not a verified release receipt.
                cleanup_sha = control.write('control-stall-cleanup.json', {'schema': 'guard-rank-rpc-stall-cleanup/1',
                    'context': proof['context'], 'arm_sha256': arm_sha, 'ready_sha256': ready_sha,
                    'published_ns': time.monotonic_ns()})
                try:
                    control.write('control-stall-release.json', {'schema': 'guard-rank-rpc-action/1', 'nonce': arm['nonce'],
                        'arm_sha256': arm_sha, 'ready_sha256': ready_sha, 'action': 'cancel', 'receipt_sha256': cleanup_sha})
                except FileExistsError:
                    pass
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
    parser.add_argument('--kind', choices=['delay', 'death', 'exception', 'communication-stall'], required=True)
    parser.add_argument('--model', default='test-model')
    parser.add_argument('--model-receipt-sha256', required=True)
    parser.add_argument('--for-common-input', action='store_true',
        help='사전 공급된 제품 입력 결합을 요구한다. 생산자 등록·그룹 수락을 뜻하지 않는다.')
    args = parser.parse_args(argv)
    if not control_code.fingerprint(args.model_receipt_sha256):
        raise ValueError('RANK_RPC_MODEL_RECEIPT')
    from . import vllm_rank_ipc_runtime as runtime
    settings, settings_sha = runtime._settings(); runtime._freeze(settings)
    observer = runtime._INSTALLED.observer
    control = control_code.Control(settings, settings_sha, observer.ipc.profile, lambda value: None,
                                   lambda: runtime._freeze(settings))
    product_binding = control.product_binding() if args.for_common_input else None
    calibration = calibrate(control, args.model, fault_kind=args.kind,
        require_all_rank_rpc_returns=args.kind == 'communication-stall')
    control.write('control-calibration.json', calibration)
    owner = {'pod_uid': os.environ.get('GUARD_POD_UID'), 'model_receipt_sha256': args.model_receipt_sha256}
    if type(owner['pod_uid']) is not str or not owner['pod_uid']:
        raise ValueError('RANK_RPC_POD_OWNER')
    result = exercise(control, calibration, args.model, args.kind, owner, product_binding=product_binding)
    print(json.dumps({'사례': args.kind, '클라이언트_HTTP': result['client']['status'],
                      '물리_신호': result['physical_action']['signal'], '전체_수락': False}, ensure_ascii=False))
