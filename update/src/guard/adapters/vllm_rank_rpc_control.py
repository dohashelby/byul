"""소유 lab의 정확 활성 RPC 경계에만 거는 일회용 제어 후보.

READY/RELEASE는 barrier 사건이며 물리 신호 적용/해제를 대신하지 않는다.
파일·세션·실제 세대·원 CALL/API ID 근거가 맞지 않으면 원 호출을 계속한다.
"""
from hashlib import sha256
import hmac
import json
import os
import re
import stat
import time

from . import vllm_rank_ipc_identity as identity
from . import vllm_rank_ipc_sources as sources
from . import vllm_lifecycle_sensor as base

DOMAIN = b'guard-rank-rpc-control/1\0'
ARM_FIELDS = frozenset({'schema', 'session', 'image', 'settings_sha256', 'assets_sha256',
    'target_sha256', 'nonce', 'fault_kind', 'issued_ns', 'expires_ns', 'target_context',
    'parent_object_hmac_sha256', 'target_point', 'source_sha256', 'body_code_sha256',
    'header_hmac_sha256', 'external_hmac_sha256', 'header_context', 'assignment_context',
    'initial_capture_sha256'})
CONTEXT_FIELDS = frozenset({'pid', 'start_ticks', 'uid', 'boot_sha256', 'pid_namespace',
    'time_namespace', 'time_offsets_sha256'})


class NativeLabFault(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(value):
    return sha256(canonical(value)).hexdigest()


def fingerprint(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('RANK_RPC_CONTROL_DUPLICATE')
        value[key] = item
    return value


def context(value):
    if (type(value) is not dict or set(value) != CONTEXT_FIELDS
            or any(type(value[k]) is not int or not 0 <= value[k] < 2**63
                   for k in ('pid', 'start_ticks', 'uid', 'pid_namespace', 'time_namespace'))
            or not value['pid'] or not all(fingerprint(value[k]) for k in ('boot_sha256', 'time_offsets_sha256'))):
        raise ValueError('RANK_RPC_CONTROL_CONTEXT')
    return value


def clock(value):
    context(value)
    return {k: value[k] for k in ('boot_sha256', 'time_namespace', 'time_offsets_sha256')}


class Control:
    def __init__(self, settings, settings_sha256, profile, emit, recheck):
        self.settings, self.settings_sha256, self.profile = settings, settings_sha256, profile
        self.key = bytes.fromhex(settings['key'])
        self.emit, self.recheck = emit, recheck

    def _directory(self):
        directory = base._open_directory(self.settings['output_dir'])
        status = os.fstat(directory)
        if status.st_uid != os.geteuid() or stat.S_IMODE(status.st_mode) != 0o700:
            os.close(directory)
            raise ValueError('RANK_RPC_CONTROL_DIRECTORY')
        return directory

    def read_raw(self, name, bound=32768):
        if (type(name) is not str or re.fullmatch('[a-zA-Z0-9_.-]{1,128}', name) is None
                or name in {'.', '..'}):
            raise ValueError('RANK_RPC_CONTROL_PATH')
        directory = self._directory()
        fd = None
        try:
            before = os.stat(name, dir_fd=directory, follow_symlinks=False)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                    or stat.S_IMODE(opened.st_mode) != 0o600 or not 1 <= opened.st_size <= bound):
                raise ValueError('RANK_RPC_CONTROL_FILE')
            raw = os.read(fd, bound + 1)
            after = os.fstat(fd)
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (len(raw) != opened.st_size or len({sources._identity(v) for v in (before, opened, after, current)}) != 1):
                raise ValueError('RANK_RPC_CONTROL_FILE')
            return raw
        finally:
            if fd is not None:
                os.close(fd)
            os.close(directory)

    def read(self, name):
        raw = self.read_raw(name)
        value = json.loads(raw, object_pairs_hook=_pairs)
        if (type(value) is not dict or set(value) != {'payload', 'hmac_sha256'}
                or not fingerprint(value['hmac_sha256'])
                or not hmac.compare_digest(value['hmac_sha256'],
                    hmac.new(self.key, DOMAIN + canonical(value['payload']), sha256).hexdigest())):
            raise ValueError('RANK_RPC_CONTROL_HMAC')
        return value['payload'], sha256(raw).hexdigest()

    def write(self, name, payload):
        if type(name) is not str or re.fullmatch(r'control-[a-z0-9-]+\.json', name) is None:
            raise ValueError('RANK_RPC_CONTROL_PATH')
        envelope = {'payload': payload, 'hmac_sha256': hmac.new(self.key, DOMAIN + canonical(payload), sha256).hexdigest()}
        raw = canonical(envelope)
        if len(raw) > 32768:
            raise ValueError('RANK_RPC_CONTROL_FILE')
        directory = self._directory()
        fd = None
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            if os.write(fd, raw) != len(raw):
                raise OSError('RANK_RPC_CONTROL_PARTIAL')
        finally:
            if fd is not None:
                os.close(fd)
            os.close(directory)
        return sha256(raw).hexdigest()

    def _snapshot(self, name, include_ipc=False):
        raw = self.read_raw(name, 131072)
        envelope = json.loads(raw, object_pairs_hook=_pairs)
        if (type(envelope) is not dict or set(envelope) != {'snapshot', 'hmac_sha256'}
                or not fingerprint(envelope['hmac_sha256'])):
            raise ValueError('RANK_RPC_CONTROL_SOURCE')
        value = envelope['snapshot']
        if (type(value) is not dict or value.get('schema') != 'guard-rank-ipc-snapshot-candidate/1'
                or value.get('settings_sha256') != self.settings_sha256
                or value.get('session') != self.settings['session'] or value.get('image') != self.settings['image']
                or value.get('observer_assets') != self.settings['assets']
                or not hmac.compare_digest(envelope['hmac_sha256'],
                    hmac.new(self.key, canonical(value), sha256).hexdigest())):
            raise ValueError('RANK_RPC_CONTROL_SOURCE')
        ctx, payload = context(value['context']), value['payload']
        schemas = {'guard-rank-rpc-hook-candidate/1'}
        if include_ipc:
            schemas.add('guard-rank-ipc-hook-candidate/1')
        if (type(value.get('ordinal')) is not int or not 1 <= value['ordinal'] <= 256
                or name != 'pid-{}-{}-{}.json'.format(ctx['pid'], ctx['start_ticks'], value['ordinal'])
                or type(payload) is not dict or payload.get('schema') not in schemas
                or payload.get('context') != ctx or payload.get('gaps') != []
                or any(payload.get(k) is not False for k in ('source_authenticated', 'engine_group_verified', 'producer_registered'))):
            raise ValueError('RANK_RPC_CONTROL_SOURCE')
        table = self.profile['rpc_sources'] if payload['schema'] == 'guard-rank-rpc-hook-candidate/1' else self.profile['sources']
        expected = table[payload['source_module']]
        hook = expected['hooks'][payload['point']]
        required = hook.get('source_modules', list(table))
        graph = payload['source_graph']
        if (payload['image'] != self.settings['image'] or payload['profile'] != self.profile['id']
                or payload['source_sha256'] != expected['sha256']
                or payload['body_code_sha256'] != expected['body_code_sha256'][payload['point']]
                or payload['phase'] != hook['phase'] or payload['role'] != hook['role']
                or not set(required) <= set(graph) or not set(graph) <= set(table)
                or any(digest != table[n]['sha256'] for n, digest in graph.items())):
            raise ValueError('RANK_RPC_CONTROL_SOURCE')
        return payload, sha256(raw).hexdigest(), hook

    def _records(self, include_ipc=False):
        directory = self._directory()
        try:
            names = os.listdir(directory)
        finally:
            os.close(directory)
        if len(names) > 512:
            raise ValueError('RANK_RPC_CONTROL_RECORD_BOUND')
        records = []
        for name in names:
            if re.fullmatch(r'pid-[0-9]+-[0-9]+-[0-9]+\.json', name) is not None:
                try:
                    records.append(self._snapshot(name, include_ipc))
                except (ValueError, KeyError, OSError):
                    # 다른 모듈/gap/쓰는 중인 기록은 자격 근거로 사용하지 않는다.
                    continue
        return records

    def _arm(self, arm, proof, now):
        if type(arm) is not dict or set(arm) != ARM_FIELDS or arm['schema'] != 'guard-rank-rpc-arm/1':
            raise ValueError('RANK_RPC_CONTROL_ARM')
        if (arm['session'] != self.settings['session'] or arm['image'] != self.settings['image']
                or arm['settings_sha256'] != self.settings_sha256
                or arm['assets_sha256'] != digest(self.settings['assets'])
                or type(arm['nonce']) is not str or re.fullmatch('[0-9a-f]{32}', arm['nonce']) is None
                or arm['fault_kind'] not in {'delay', 'death', 'exception'}
                or not all(fingerprint(arm[k]) for k in ('target_sha256', 'initial_capture_sha256',
                    'parent_object_hmac_sha256', 'source_sha256', 'body_code_sha256',
                    'header_hmac_sha256', 'external_hmac_sha256'))
                or any(type(arm[k]) is not int or not 0 <= arm[k] < 2**63 for k in ('issued_ns', 'expires_ns'))
                or not arm['issued_ns'] <= now < arm['expires_ns'] <= arm['issued_ns'] + 120_000_000_000):
            raise ValueError('RANK_RPC_CONTROL_ARM')
        context(arm['target_context']); context(arm['header_context'])
        if arm['assignment_context'] is not None:
            context(arm['assignment_context'])
        if (arm['target_context'] != proof['context'] or arm['target_point'] != proof['point']
                or arm['parent_object_hmac_sha256'] != proof['rpc_parent_object_hmac_sha256']
                or arm['source_sha256'] != proof['source_sha256']
                or arm['body_code_sha256'] != proof['body_code_sha256']
                or proof.get('event') != 'CALL' or proof.get('gaps') != []
                or proof.get('rpc_parent_frame_verified') is not True
                or proof.get('rpc_parent_worker_verified') is not True
                or proof.get('original_invoked') is not False
                or type(proof.get('scalars', {}).get('gpu_rank')) is not int
                or proof.get('scalars', {}).get('gpu_rank') != 0
                or type(proof.get('scalars', {}).get('configured_tp_size')) is not int
                or proof.get('scalars', {}).get('configured_tp_size') != 2
                or type(proof.get('request_hmac_sha256')) is not list or len(proof['request_hmac_sha256']) != 1
                or not fingerprint(proof['request_hmac_sha256'][0])):
            return False
        expected = self.profile['rpc_sources'][proof['source_module']]['hooks'][proof['point']]
        if expected.get('fault_eligible') is not True:
            return False
        if (clock(arm['header_context']) != clock(proof['context'])
                or arm['header_context']['pid_namespace'] != proof['context']['pid_namespace']
                or arm['header_context']['uid'] != proof['context']['uid']):
            raise ValueError('RANK_RPC_CONTROL_CONTEXT')
        return True

    def _selection(self, arm, proof, records):
        headers = [(p, raw) for p, raw, hook in records if hook['kind'] == 'external-id'
            and p.get('event') == 'RETURN' and p.get('external_request_hmac_sha256') == arm['header_hmac_sha256']
            and p['context'] == arm['header_context']
            and arm['issued_ns'] <= p['observation_ns'] <= proof['observation_ns']]
        calls = [(p, raw) for p, raw, _ in records if p == proof]
        if len(headers) != 1 or len(calls) != 1:
            return None
        request = proof['request_hmac_sha256'][0]
        binding = self.profile['request_binding']['kind']
        mapping = None
        if binding == 'assigned':
            matches = [(p, raw) for p, raw, hook in records if hook['kind'] == 'assignment'
                and p.get('event') == 'RETURN' and p['context'] == arm['assignment_context']
                and headers[0][0]['observation_ns'] <= p['observation_ns'] <= proof['observation_ns']
                and p.get('assignment', {}).get('external_hmac_sha256') == arm['external_hmac_sha256']
                and p.get('assignment', {}).get('internal_hmac_sha256') == request]
            if (len(matches) != 1 or clock(matches[0][0]['context']) != clock(proof['context'])
                    or matches[0][0]['context']['pid_namespace'] != proof['context']['pid_namespace']
                    or matches[0][0]['context']['uid'] != proof['context']['uid']):
                return None
            mapping = matches[0][1]
        elif binding != 'direct' or arm['assignment_context'] is not None or request != arm['external_hmac_sha256']:
            return None
        return {'header_capture_sha256': headers[0][1], 'assignment_capture_sha256': mapping,
                'call_capture_sha256': calls[0][1]}

    def _phase(self, proof, phase, arm, arm_sha, ready_sha, action_sha=None):
        self.emit({**proof, 'event': phase, 'observation_ns': time.monotonic_ns(),
            'control': {'nonce': arm['nonce'], 'fault_kind': arm['fault_kind'], 'arm_sha256': arm_sha,
                'ready_sha256': ready_sha, 'action_sha256': action_sha,
                'target_sha256': arm['target_sha256'],
                'control_code_sha256': self.settings['assets']['guard/adapters/vllm_rank_rpc_control.py']['sha256'],
                'barrier_only': phase in {'CONTROL_READY', 'CONTROL_RELEASE', 'CONTROL_EXPIRED', 'CONTROL_CANCELLED'}}})

    def _gap(self):
        try:
            self.emit({'schema': 'guard-rank-rpc-control-gap/1',
                'source_authenticated': False, 'engine_group_verified': False,
                'producer_registered': False, 'gaps': ['RANK_RPC_CONTROL_UNAVAILABLE']})
        except BaseException:
            pass

    def maybe_hold(self, proof):
        try:
            arm, arm_sha = self.read('control-arm.json')
        except FileNotFoundError:
            return
        except BaseException:
            self._gap()
            return
        try:
            if not self._arm(arm, proof, time.monotonic_ns()):
                return
            refs = self._selection(arm, proof, self._records())
            if refs is None:
                return
            self.recheck()
            claim = 'control-claimed-' + arm['nonce'] + '.json'
            self.write(claim, {'nonce': arm['nonce'], 'call_hmac_sha256': proof['call_hmac_sha256']})
            ready = {'schema': 'guard-rank-rpc-ready/1', 'nonce': arm['nonce'], 'fault_kind': arm['fault_kind'],
                'arm_sha256': arm_sha, 'context': proof['context'], 'call_hmac_sha256': proof['call_hmac_sha256'],
                'parent_call_hmac_sha256': proof['rpc_parent_call_hmac_sha256'],
                'target_sha256': arm['target_sha256'], 'source_point': proof['point'],
                'prepared_assets_sha256': digest(self.settings['assets']), 'source_refs': refs,
                'ready_ns': time.monotonic_ns(), 'expires_ns': arm['expires_ns'], 'rpc_active': True}
            ready_sha = self.write('control-ready.json', ready)
            self._phase(proof, 'CONTROL_READY', arm, arm_sha, ready_sha)
            while time.monotonic_ns() < arm['expires_ns']:
                try:
                    action, action_sha = self.read('control-action.json')
                except FileNotFoundError:
                    time.sleep(0.01)
                    continue
                if (type(action) is not dict or set(action) != {'schema', 'nonce', 'arm_sha256', 'ready_sha256', 'action', 'receipt_sha256'}
                        or action['schema'] != 'guard-rank-rpc-action/1' or action['nonce'] != arm['nonce']
                        or action['arm_sha256'] != arm_sha or action['ready_sha256'] != ready_sha
                        or not fingerprint(action['receipt_sha256'])):
                    raise ValueError('RANK_RPC_CONTROL_ACTION')
                self.recheck()
                if action['action'] == 'raise' and arm['fault_kind'] == 'exception':
                    self._phase(proof, 'CONTROL_EXCEPTION', arm, arm_sha, ready_sha, action_sha)
                    raise NativeLabFault('BYUL_RANK_LAB_FAULT')
                if action['action'] == 'release' and arm['fault_kind'] == 'delay':
                    self._phase(proof, 'CONTROL_RELEASE', arm, arm_sha, ready_sha, action_sha)
                    return
                if action['action'] == 'cancel':
                    self._phase(proof, 'CONTROL_CANCELLED', arm, arm_sha, ready_sha, action_sha)
                    return
                raise ValueError('RANK_RPC_CONTROL_ACTION')
            self._phase(proof, 'CONTROL_EXPIRED', arm, arm_sha, ready_sha)
        except NativeLabFault:
            raise
        except BaseException:
            # 제어 자격 실패는 장애 적용이 아니다. 원 함수를 막지 않는다.
            self._gap()
