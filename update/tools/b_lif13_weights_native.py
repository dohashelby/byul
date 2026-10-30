"""LIF13 선정 zero-copy daemon 실측 준비. 기본 실행은 RPC/GPU import 없는 preflight다.

단독 script 또는 python -m tools.b_lif13_weights_native로 실행한다.
실행에는 기존 승인 시험 daemon, 명시 peer 세대와 종료 empty 허용·복구 담당이 필요하다.
그 신고는 기업 인가 증거가 아니다. 원 daemon 시작·복구·GPU reset은 수행하지 않는다.
"""
from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import hmac
import secrets
import importlib
import inspect
import json
import os
from pathlib import Path
import platform
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import types
import uuid
from datetime import datetime, timezone

CONFIG_SCHEMA = 'byul-lif13-native-config/1'
SCHEMA = 'byul-lif13-native-capture/1'
SOURCE_COMMIT = 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
PREFIX = 'vllm/model_executor/model_loader/weight_cache/'
SOURCE_FILES = {PREFIX + 'daemon.py': 'f4fc42549b0cce117cc006506d80b0d493f095f58825f89494a8d6ce43849e63',
    PREFIX + 'protocol.py': 'cee8d02d5d5bdfbcd4a8e873cc9095008f3cd0f4280e7eb4f74369b6bec654db',
    PREFIX + 'ipc_loader.py': '45c3a633a1556deab41404b5f5a4fb049f5f9a4dd1a21fb3a61bea16eea9f2da'}
KEY_FIELDS = {'checkpoint', 'model_arch', 'tp_size', 'tp_rank', 'dtype', 'quantization',
    'quant_config_hash', 'revision', 'vllm_version', 'is_draft', 'dp_size', 'dp_rank'}
DAEMON_FIELDS = {'socket_path', 'pid', 'uid', 'start_ticks', 'boot_id_sha256',
                 'pid_namespace_inode', 'mount_namespace_inode', 'gpu_uuid'}
CONFIG_FIELDS = {'schema', 'data_origin', 'environment_ref', 'policy_ref', 'plan_ref', 'target_sha256',
    'source_commit', 'mode', 'owner', 'recovery_owner', 'allow_global_release', 'allow_empty_after',
    'daemon', 'cache_key', 'entry_name', 'device_index', 'read_elements', 'max_response_bytes',
    'duration_seconds', 'rpc_timeout_seconds'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _sha(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _text(value, limit=1024):
    return type(value) is str and 0 < len(value) <= limit and not any(c in value for c in '\0\r\n')


def validate_config(config):
    if type(config) is not dict or set(config) != CONFIG_FIELDS:
        raise ValueError('LIF13_CONFIG_FIELDS')
    if (config['schema'] != CONFIG_SCHEMA or config['data_origin'] not in ('DECLARED', 'EXAMPLE')
            or config['source_commit'] != SOURCE_COMMIT or config['mode'] != 'zero_copy'):
        raise ValueError('LIF13_PROFILE_UNSUPPORTED')
    for key, kind in [('environment_ref', 'ENV'), ('policy_ref', 'POL'), ('plan_ref', 'PLAN')]:
        ref = config[key]
        if (type(ref) is not dict or set(ref) != {'kind', 'id', 'revision', 'sha256'} or ref['kind'] != kind
                or not _text(ref['id']) or not ref['id'].startswith(kind + '-')
                or type(ref['revision']) is not int or ref['revision'] < 1 or not _sha(ref['sha256'])):
            raise ValueError('LIF13_EXACT_REF_REQUIRED')
    if (not _sha(config['target_sha256']) or not _text(config['owner'], 200)
            or not _text(config['recovery_owner'], 200) or not _text(config['entry_name'])
            or config['allow_global_release'] is not True or config['allow_empty_after'] is not True):
        raise ValueError('LIF13_EXPLICIT_TEST_SCOPE_REQUIRED')
    daemon = config['daemon']
    if type(daemon) is not dict or set(daemon) != DAEMON_FIELDS:
        raise ValueError('LIF13_PEER_FIELDS')
    if (not _text(daemon['socket_path']) or not Path(daemon['socket_path']).is_absolute()
            or not _text(daemon['gpu_uuid'], 200) or not _sha(daemon['boot_id_sha256'])):
        raise ValueError('LIF13_PEER_IDENTITY_REQUIRED')
    for key in ('pid', 'uid', 'start_ticks', 'pid_namespace_inode', 'mount_namespace_inode'):
        if type(daemon[key]) is not int or daemon[key] < (0 if key == 'uid' else 1):
            raise ValueError('LIF13_PEER_IDENTITY_REQUIRED')
    key = config['cache_key']
    if type(key) is not dict or set(key) != KEY_FIELDS:
        raise ValueError('LIF13_NATIVE_CACHE_KEY_REQUIRED')
    for name in KEY_FIELDS - {'tp_size', 'tp_rank', 'dp_size', 'dp_rank', 'is_draft', 'quantization', 'revision'}:
        if type(key[name]) is not str or len(key[name]) > 1024 or any(c in key[name] for c in '\0\r\n'):
            raise ValueError('LIF13_CACHE_KEY_INVALID')
    for name in ('quantization', 'revision'):
        if key[name] is not None and not _text(key[name]):
            raise ValueError('LIF13_CACHE_KEY_INVALID')
    if type(key['is_draft']) is not bool:
        raise ValueError('LIF13_CACHE_KEY_INVALID')
    for name in ('tp_size', 'dp_size'):
        if type(key[name]) is not int or not 1 <= key[name] <= 1024:
            raise ValueError('LIF13_CACHE_KEY_INVALID')
    for rank, size in [('tp_rank', 'tp_size'), ('dp_rank', 'dp_size')]:
        if type(key[rank]) is not int or not 0 <= key[rank] < key[size]:
            raise ValueError('LIF13_CACHE_KEY_INVALID')
    for name, lower, upper in [('device_index', 0, 1024), ('read_elements', 1, 16),
            ('max_response_bytes', 1024, 8 * 1024 * 1024), ('duration_seconds', 10, 300), ('rpc_timeout_seconds', 1, 30)]:
        if type(config[name]) is not int or not lower <= config[name] <= upper:
            raise ValueError('LIF13_BOUND_INVALID')
    return config


def verify_source(source_root):
    root = Path(source_root)
    for relative, expected in SOURCE_FILES.items():
        path = root / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ValueError('LIF13_CLIENT_SOURCE_UNAVAILABLE')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('LIF13_CLIENT_SOURCE_MISMATCH')
    return {'status': 'verified', 'files': dict(SOURCE_FILES)}


def _status_source(root):
    try:
        return verify_source(root)
    except Exception:
        return {'status': 'unavailable'}


def preflight(config, source_root):
    validate_config(config)
    source = verify_source(source_root)
    return {'schema': SCHEMA, 'executed': False, 'rpc_attempts': 0, 'gpu_imported': False,
        'config_sha256': digest(config), 'source_validation': {'pre': source['status']},
        'client_source_verified': True, 'daemon_source_verified': False,
        'release_authorization_verified': False, 'mode': 'zero_copy'}


def _code(module_code, names):
    current = module_code
    for name in names:
        current = next((v for v in current.co_consts if type(v) is types.CodeType and v.co_name == name), None)
        if current is None:
            return None
    return current


def _load_native(root):
    verify_source(root)
    root = Path(root).resolve()
    sys.path.insert(0, str(root))
    result = {}
    functions = {'protocol': [('send_msg',), ('recv_msg',), ('verify_socket_owner',), ('TensorEntry', 'rebuild')],
                 'ipc_loader': [('IpcModelLoader', '_request_state')],
                 'daemon': [('WeightCacheDaemon', '_handle_get_state'), ('WeightCacheDaemon', '_handle_release')]}
    for name in ('protocol', 'ipc_loader', 'daemon'):
        relative = PREFIX + name + '.py';path = root / relative
        module = importlib.import_module('vllm.model_executor.model_loader.weight_cache.' + name)
        if not Path(module.__file__).resolve().samefile(path):
            raise ValueError('LIF13_CLIENT_MODULE_ORIGIN')
        compiled = compile(path.read_bytes(), str(path), 'exec', dont_inherit=True, optimize=0)
        for chain in functions[name]:
            fn = module
            for item in chain:
                fn = inspect.getattr_static(fn, item)
            if type(fn) is not types.FunctionType or fn.__code__ != _code(compiled, chain):
                raise ValueError('LIF13_CLIENT_CODE_ORIGIN')
        result[name] = module
    return result


def _proc_identity(pid):
    base = Path('/proc') / str(pid)
    text = (base / 'stat').read_text()
    start = int(text.rpartition(')')[2].split()[19])
    return {'pid': pid, 'uid': base.stat().st_uid, 'start_ticks': start,
        'boot_id_sha256': hashlib.sha256(Path('/proc/sys/kernel/random/boot_id').read_bytes().strip()).hexdigest(),
        'pid_namespace_inode': (base / 'ns/pid').stat().st_ino,
        'mount_namespace_inode': (base / 'ns/mnt').stat().st_ino}


def _socket_identity(config):
    path = Path(config['daemon']['socket_path'])
    info = path.lstat();parent = path.parent.lstat()
    if (not stat.S_ISSOCK(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != config['daemon']['uid']
            or info.st_mode & 0o077 or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != info.st_uid
            or parent.st_mode & 0o077):
        raise ValueError('LIF13_PRIVATE_SOCKET_REQUIRED')
    return (info.st_dev, info.st_ino, info.st_uid)


def _daemon_epoch(config, identity):
    if _socket_identity(config) != tuple(identity):
        raise ValueError('LIF13_SOCKET_CHANGED')
    expected = config['daemon']
    actual = _proc_identity(expected['pid'])
    if any(actual[k] != expected[k] for k in actual):
        raise ValueError('LIF13_PEER_CHANGED')
    return digest({**actual, 'socket_identity': list(identity)})


def _check_peer(sock, config, identity):
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
    pid, uid, _ = struct.unpack('3i', creds)
    if pid != config['daemon']['pid'] or uid != config['daemon']['uid']:
        raise ValueError('LIF13_PEER_CHANGED')
    return _daemon_epoch(config, identity)


class BoundedSocket:
    """원 recv_msg의 length 처리 전에 수신 budget·선정 peer를 강제한다."""
    def __init__(self, sock, *, limit, deadline, timeout, check):
        self.sock, self.limit, self.deadline, self.timeout, self.check = sock, limit, deadline, timeout, check
        self.received = 0
    def _before(self):
        self.check()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('LIF13_RPC_DEADLINE')
        self.sock.settimeout(min(self.timeout, remaining))
    def sendall(self, data):
        self._before()
        self.sock.sendall(data)
        self.check()
    def recv(self, count):
        self._before()
        if type(count) is not int or count < 0 or count > self.limit - self.received:
            raise ValueError('LIF13_RESPONSE_BOUND')
        value = self.sock.recv(count)
        self.received += len(value)
        self.check()
        if time.monotonic() > self.deadline:
            raise TimeoutError('LIF13_RPC_DEADLINE')
        return value


def _rpc(config, native, command, identity, deadline, request_note=None):
    protocol = native['protocol'];verify_source(config['_source_root'])
    protocol.verify_socket_owner(config['daemon']['socket_path'], strict_perms=True)
    if _socket_identity(config) != tuple(identity):
        raise ValueError('LIF13_SOCKET_CHANGED')
    raw = None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
            raw.settimeout(min(config['rpc_timeout_seconds'], max(0.001, deadline - time.monotonic())))
            raw.connect(config['daemon']['socket_path'])
            generation = _check_peer(raw, config, identity)
            bounded = BoundedSocket(raw, limit=config['max_response_bytes'], deadline=deadline,
                timeout=config['rpc_timeout_seconds'], check=lambda: _check_peer(raw, config, identity))
            request = {'cmd': command}
            if command == 'get_state':
                request['cache_config'] = protocol.WeightCacheKey(**config['cache_key'])
            if request_note is not None:
                request_note.update(attempted=True, actor_real_uid=os.getuid(), actor_effective_uid=os.geteuid())
            protocol.send_msg(bounded, request)
            response = protocol.recv_msg(bounded)
            _check_peer(raw, config, identity)
    finally:
        if request_note is not None:
            request_note['socket_closed'] = raw is None or raw.fileno() == -1
    if type(response) is not dict:
        raise ValueError('LIF13_NATIVE_RESPONSE_INVALID')
    status = response.get('status')
    status = status if status in ('ok', 'error', 'mismatch') else 'unavailable'
    reason = ('weights_released' if status == 'error' and response.get('message') == 'Weights were released'
              else 'cache_key_mismatch' if status == 'mismatch' else 'daemon_error' if status == 'error' else None)
    return response, {'attempted': True, 'response_received': True, 'status': status, 'reason': reason,
                      'peer_generation_verified': True, 'daemon_generation_sha256': generation,
                      'actor_real_uid': os.getuid(), 'actor_effective_uid': os.geteuid(), 'socket_closed': True}


def _unavailable_read(reason='unavailable'):
    return {'collected': False, 'status': reason, 'cuda_ipc_rebuilt': None, 'is_cuda': None,
            'device_index': None, 'selected_count': None, 'digest_sha256': None,
            'read_completion': None, 'shape': None, 'dtype': None}


def _read_cuda(torch, tensor, config):
    if type(tensor) is not torch.Tensor or tensor.is_cuda is not True or tensor.device.index != config['device_index']:
        return _unavailable_read('unsupported_non_cuda')
    if not tensor.is_contiguous() or tensor.layout != torch.strided or not 1 <= tensor.dim() <= 8:
        return _unavailable_read('unsupported_layout')
    count = min(config['read_elements'], tensor.numel())
    if count <= 0:
        return _unavailable_read('unsupported_empty')
    # view+slice는 원 CUDA allocation의 bounded 구간이다. CPU 사본은 읽기 산출용일 뿐 copy loader가 아니다.
    host = tensor.detach().view(-1)[:count].to(device='cpu', non_blocking=False, copy=True).contiguous()
    raw = host.view(torch.uint8).numpy().tobytes()
    value = {'collected': True, 'status': 'read', 'cuda_ipc_rebuilt': True, 'is_cuda': True,
        'device_index': tensor.device.index, 'selected_count': count, 'digest_sha256': hmac.new(bytes.fromhex(config['_digest_key']), raw, hashlib.sha256).hexdigest(),
        'read_completion': True, 'shape': list(tensor.shape), 'dtype': str(tensor.dtype)}
    del raw, host
    return value


def _consume_exports(response, protocol, device_index, selected_name, deadline):
    """각 export의 sent counter를 원 rebuild 경로로 소비한다. storage/handle dedup은 하지 않는다."""
    entries = response.get('entries')
    facts = {'received_cuda_entries': 0, 'rebuilt_cuda_entries': 0, 'cpu_by_value_entries': 0, 'complete': False}
    selected = None
    if type(entries) is not dict or len(entries) > 4096:
        return selected, facts
    complete = True
    for name, entry in entries.items():
        if type(entry) is not protocol.TensorEntry:
            complete = False;continue
        if entry.ipc_args is None:
            facts['cpu_by_value_entries'] += 1
            continue
        facts['received_cuda_entries'] += 1
        if time.monotonic() >= deadline:
            complete = False;continue
        rebuilt = None
        try:
            rebuilt = entry.rebuild(device_index)
            facts['rebuilt_cuda_entries'] += 1
            if selected_name is not None and name == selected_name:
                selected = rebuilt
        except Exception:
            complete = False
        finally:
            # selected만 별도 참조로 보관한다. 나머지 매핑은 정상 destructor 경로에 맡긴다.
            rebuilt = None
    facts['complete'] = complete and facts['received_cuda_entries'] == facts['rebuilt_cuda_entries']
    return selected, facts


def _torch_runtime(torch):
    result = {'torch_version': None, 'torch_reductions_file_sha256': None}
    version = getattr(torch, '__version__', None)
    if isinstance(version, str) and len(version) <= 100 and all(c.isalnum() or c in '.+-_' for c in version):
        result['torch_version'] = str(version)
    reductions = sys.modules.get('torch.multiprocessing.reductions')
    try:
        path = Path(reductions.__file__)
        if path.is_file() and path.stat().st_size <= 1024 * 1024:
            result['torch_reductions_file_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        pass
    return result


def _held_read(torch, tensor, config, identity):
    source_pre = _status_source(config['_source_root'])['status']
    if source_pre != 'verified':
        return {**_unavailable_read('source_unavailable'), 'peer_generation_verified': None}
    try:
        generation = _daemon_epoch(config, identity)
    except ValueError:
        return {**_unavailable_read('daemon_epoch_changed'), 'peer_generation_verified': False}
    except Exception:
        return {**_unavailable_read('daemon_epoch_unavailable'), 'peer_generation_verified': None}
    try:
        result = _read_cuda(torch, tensor, config)
    except Exception:
        result = _unavailable_read('read_error')
    result['daemon_generation_sha256'] = generation
    try:
        result['peer_generation_verified'] = _daemon_epoch(config, identity) == generation
    except Exception:
        result['peer_generation_verified'] = False
    result['source_validation'] = {'pre': source_pre, 'post': _status_source(config['_source_root'])['status']}
    return result


def _followup_state(config, native, identity, deadline, note):
    response = None
    try:
        response, receipt = _rpc(config, native, 'get_state', identity, deadline, note)
        note.update(receipt)
        if note['status'] == 'ok':
            _, note['exports'] = _consume_exports(response, native['protocol'], config['device_index'], None, deadline)
    except Exception:
        # _rpc의 finally가 남긴 socket_closed·실제 dispatch/actor facts를 덮어쓰지 않는다.
        note.update(status='unavailable', reason='rpc_error')
    finally:
        response = None
    return note


def _child_emit(stream, slot, stage, facts):
    stream.write(json.dumps({'slot': slot, 'stage': stage, 'facts': facts}, allow_nan=False) + '\n');stream.flush()


def _child_main():
    packet = json.loads(sys.stdin.buffer.readline(65537))
    config = packet['config'];slot = packet['slot'];deadline = packet['deadline'];identity = packet['identity']
    safe_out = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
    with open(os.devnull, 'w') as null:
        os.dup2(null.fileno(), 1);os.dup2(null.fileno(), 2)
    tensor = response = entry = native = torch = None
    baseline = _unavailable_read();held = _unavailable_read();follow = {'attempted': False, 'status': 'unavailable', 'reason': None}
    cleanup = {'references_dropped': False, 'owned_sockets_closed': True, 'device_sync_complete': None,
               'daemon_native_cleanup': None}
    state = {'attempted': False, 'status': 'unavailable', 'response_received': False}
    try:
        validate_config({k: config[k] for k in CONFIG_FIELDS})
        native = _load_native(config['_source_root']);torch = importlib.import_module('torch')
        baseline['runtime'] = _torch_runtime(torch)
        if not torch.cuda.is_available():
            raise ValueError('LIF13_CUDA_REQUIRED')
        torch.cuda.set_device(config['device_index'])
        response, state = _rpc(config, native, 'get_state', identity, deadline, state)
        baseline['get_state'] = state
        if state['status'] != 'ok' or response.get('gpu_uuid') != config['daemon']['gpu_uuid']:
            raise ValueError('LIF13_STATE_UNAVAILABLE')
        if native['protocol'].get_current_device_uuid() != config['daemon']['gpu_uuid']:
            raise ValueError('LIF13_DEVICE_MISMATCH')
        tensor, exports = _consume_exports(response, native['protocol'], config['device_index'], config['entry_name'], deadline)
        runtime = baseline.get('runtime')
        baseline.update(exports=exports, get_state=state, runtime=runtime, cuda_ipc_rebuilt=tensor is not None)
        if tensor is None:
            baseline.update(_unavailable_read('unsupported_cpu_by_value'));baseline['cuda_ipc_rebuilt'] = False
        else:
            baseline.update(_read_cuda(torch, tensor, config))
            baseline['cuda_ipc_rebuilt'] = True
        baseline.update(get_state=state, exports=exports, runtime=runtime,
            source_validation={'pre': 'verified', 'post': _status_source(config['_source_root'])['status']},
            peer_generation_verified=state['peer_generation_verified'],
            daemon_generation_sha256=state['daemon_generation_sha256'])
        _child_emit(safe_out, slot, 'baseline', baseline)
        command = json.loads(sys.stdin.buffer.readline(4097))
        if command.get('action') == 'after' and tensor is not None:
            held = _held_read(torch, tensor, config, identity)
            if held['status'] in ('daemon_epoch_changed', 'daemon_epoch_unavailable'):
                follow.update(status='unavailable', reason='peer_epoch_unavailable')
            else:
                _followup_state(config, native, identity, deadline, follow)
            equal = baseline['digest_sha256'] == held['digest_sha256'] if baseline['collected'] and held['collected'] else None
            _child_emit(safe_out, slot, 'after', {'held_after': held, 'digest_equal': equal, 'followup_get_state': follow})
    except Exception:
        baseline.setdefault('get_state', state)
        _child_emit(safe_out, slot, 'baseline', baseline)
    finally:
        try:
            if torch is not None and tensor is not None:
                torch.cuda.synchronize(config['device_index']);cleanup['device_sync_complete'] = True
        except Exception:
            cleanup['device_sync_complete'] = False
        cleanup['owned_sockets_closed'] = all(item.get('socket_closed') is True
            for item in (state, follow) if 'socket_closed' in item or item.get('attempted') is True)
        tensor = response = entry = None
        if 'entries' in locals():entries = None
        gc.collect();cleanup['references_dropped'] = True
        _child_emit(safe_out, slot, 'cleanup', cleanup)
        safe_out.close()


def _receive(jobs, stage, deadline, record):
    pending = {j['slot'] for j in jobs if stage not in j['received']}
    while pending and time.monotonic() < deadline:
        for job in jobs:
            if job['slot'] not in pending:
                continue
            while job['messages']:
                value = job['messages'].pop(0)
                incoming = value.get('stage')
                if value.get('slot') != job['slot'] or incoming not in ('baseline', 'after', 'cleanup') or type(value.get('facts')) is not dict:
                    raise ValueError('LIF13_CHILD_MESSAGE_INVALID')
                row = record['consumers'][job['slot']]
                if incoming not in job['received']:
                    if incoming == 'after':
                        row.update({key: value['facts'][key] for key in ('held_after', 'digest_equal', 'followup_get_state')})
                    else:
                        row[incoming] = value['facts']
                    job['received'].add(incoming)
                if incoming == stage:
                    pending.discard(job['slot']);break
        if not pending:
            break
        readers = [j for j in jobs if j['slot'] in pending and not j['eof']]
        if not readers:
            break
        with selectors.DefaultSelector() as selector:
            for job in readers:selector.register(job['process'].stdout, selectors.EVENT_READ, job)
            for key, _ in selector.select(min(0.2, max(0, deadline - time.monotonic()))):
                job = key.data;chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:job['eof'] = True;continue
                job['buffer'] += chunk
                if len(job['buffer']) > 65536:raise ValueError('LIF13_CHILD_OUTPUT_BOUND')
                while b'\n' in job['buffer']:
                    line, job['buffer'] = job['buffer'].split(b'\n', 1)
                    value = json.loads(line)
                    if type(value) is not dict:raise ValueError('LIF13_CHILD_MESSAGE_INVALID')
                    job['messages'].append(value)
    return not pending


def _send_fd(fd, data, deadline):
    """non-reader도 전체 기한 안에 중단한다. parent pipe에는 buffered writer를 쓰지 않는다."""
    os.set_blocking(fd, False)
    remaining = memoryview(data)
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_WRITE)
        while remaining:
            budget = deadline - time.monotonic()
            if budget <= 0 or not selector.select(budget):
                raise TimeoutError('LIF13_CHILD_SEND_DEADLINE')
            try:
                count = os.write(fd, remaining)
            except BlockingIOError:
                continue
            if count <= 0:
                raise BrokenPipeError('LIF13_CHILD_PIPE_CLOSED')
            remaining = remaining[count:]


def _send_child(process, packet, deadline):
    data = json.dumps(packet, ensure_ascii=False, allow_nan=False).encode() + b'\n'
    if len(data) > 65536:
        raise ValueError('LIF13_CHILD_INPUT_BOUND')
    _send_fd(process.stdin.fileno(), data, deadline)


def _finish_child(process, deadline, remaining_children):
    facts = {'joined': False, 'forced': False, 'exit_ok': False,
             'stdin_closed': None, 'stdout_closed': None, 'errors': []}
    # Popen(bufsize=0)의 FileIO close이므로 숨은 buffered flush가 없다.
    try:
        process.stdin.close();facts['stdin_closed'] = True
    except Exception:
        facts['stdin_closed'] = False;facts['errors'].append('STDIN_CLOSE_UNAVAILABLE')
    try:
        if process.poll() is None:
            process.wait(timeout=max(0, (deadline - time.monotonic()) / max(2, remaining_children * 2)))
    except subprocess.TimeoutExpired:
        facts['forced'] = True
    except Exception:
        facts['errors'].append('WAIT_UNAVAILABLE');facts['forced'] = True
    try:
        if process.poll() is None:
            facts['forced'] = True
            try:
                if os.getpgid(process.pid) == process.pid:
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    facts['errors'].append('OWNED_GROUP_UNAVAILABLE')
            except ProcessLookupError:
                pass
            except Exception:
                facts['errors'].append('KILL_UNAVAILABLE')
            try:
                process.wait(timeout=max(0, (deadline - time.monotonic()) / max(1, remaining_children)))
            except Exception:
                facts['errors'].append('JOIN_UNAVAILABLE')
    finally:
        try:
            process.stdout.close();facts['stdout_closed'] = True
        except Exception:
            facts['stdout_closed'] = False;facts['errors'].append('STDOUT_CLOSE_UNAVAILABLE')
    try:
        facts['joined'] = process.poll() is not None
        facts['exit_ok'] = process.returncode == 0
    except Exception:
        facts['errors'].append('PROCESS_STATUS_UNAVAILABLE')
    return facts


def _run_native(config, source_root, record):
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64') or not hasattr(socket, 'SO_PEERCRED'):
        record['gaps'].append('LINUX_AMD64_CUDA_REQUIRED');return
    config = dict(config, _source_root=str(Path(source_root).resolve()), _digest_key=secrets.token_hex(32))
    identity = _socket_identity(config)
    actual_peer = _proc_identity(config['daemon']['pid'])
    if any(actual_peer[k] != config['daemon'][k] for k in actual_peer):
        record['gaps'].append('PEER_GENERATION_UNAVAILABLE');return
    deadline = time.monotonic() + config['duration_seconds'];collection_deadline = deadline - 3
    jobs = []
    try:
        for slot in range(2):
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--child', '--execute'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True, bufsize=0)
            job = {'slot': slot, 'process': process, 'messages': [], 'received': set(), 'buffer': b'', 'eof': False};jobs.append(job)
            packet = {'slot': slot, 'config': config, 'identity': list(identity), 'deadline': collection_deadline}
            _send_child(process, packet, collection_deadline)
        ready = _receive(jobs, 'baseline', collection_deadline, record)
        baselines = [row['baseline'] for row in record['consumers']]
        ready = ready and all(b.get('collected') is True and b.get('is_cuda') is True and b.get('cuda_ipc_rebuilt') is True and b.get('exports', {}).get('complete') is True
            and b.get('source_validation', {}).get('post') == 'verified' for b in baselines)
        ready = ready and baselines[0].get('digest_sha256') == baselines[1].get('digest_sha256')
        if ready:
            try:
                with open(os.devnull, 'w') as null, contextlib.redirect_stdout(null), contextlib.redirect_stderr(null):
                    native = _load_native(source_root)
                    _, record['release'] = _rpc(config, native, 'release', identity, collection_deadline, record['release'])
                record['peer_generation_verified'] = record['release']['peer_generation_verified']
                record['daemon_generation_sha256'] = record['release']['daemon_generation_sha256']
            except Exception:
                record['gaps'].append('RELEASE_RESPONSE_UNAVAILABLE')
            for job in jobs:
                try:_send_child(job['process'], {'action': 'after'}, collection_deadline)
                except OSError:record['gaps'].append('CHILD_COMMAND_UNAVAILABLE')
            if not _receive(jobs, 'after', collection_deadline, record):record['gaps'].append('AFTER_READ_INCOMPLETE')
        else:
            record['gaps'].append('BASELINE_COHORT_UNAVAILABLE')
            for job in jobs:
                try:_send_child(job['process'], {'action': 'close'}, deadline)
                except OSError:pass
        _receive(jobs, 'cleanup', deadline, record)
    except Exception:
        record['gaps'].append('COLLECTION_INCOMPLETE')
    finally:
        complete = True
        for index, job in enumerate(jobs):
            try:
                facts = _finish_child(job['process'], deadline, len(jobs) - index)
            except Exception:
                facts = {'joined': False, 'forced': False, 'exit_ok': False, 'errors': ['CHILD_CLEANUP_UNAVAILABLE']}
            record['consumers'][job['slot']]['process_cleanup'] = facts
            complete = complete and facts['joined']
        config.pop('_digest_key', None)
        record['cleanup']['owned_children_complete'] = complete
        record['cleanup']['created_children'] = len(jobs)
        record['cleanup']['collection_gap'] = any(row.get('process_cleanup', {}).get('forced')
            or row.get('process_cleanup', {}).get('errors')
            or row.get('cleanup', {}).get('references_dropped') is not True for row in record['consumers'])


def capture(config, source_root, *, execute=False, _example_backend=None):
    checked = preflight(config, source_root)
    if _example_backend is not None and config['data_origin'] != 'EXAMPLE':
        raise ValueError('LIF13_EXAMPLE_BACKEND_REQUIRES_EXAMPLE')
    if not execute:
        return checked
    now = datetime.now(timezone.utc).isoformat()
    record = {'schema': SCHEMA, 'executed': True, 'mode': 'zero_copy', 'data_origin': config['data_origin'],
        'config_sha256': digest(config), 'environment_ref': config['environment_ref'], 'policy_ref': config['policy_ref'],
        'plan_ref': config['plan_ref'], 'target_sha256': config['target_sha256'], 'source_commit': SOURCE_COMMIT,
        'source_file_bytes_sha256': dict(SOURCE_FILES), 'own_producer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_validation': {'pre': 'verified', 'post': {'status': 'unavailable'}},
        'client_source_verified': True, 'daemon_source_verified': False, 'release_authorization_verified': False,
        'peer_generation_verified': False, 'daemon_generation_sha256': None,
        'collection_id': 'lif13-' + uuid.uuid4().hex, 'started_at': now, 'finished_at': now,
        'consumers': [{'slot': i, 'baseline': _unavailable_read(), 'held_after': _unavailable_read(),
            'digest_equal': None, 'followup_get_state': {'attempted': False, 'status': 'unavailable', 'reason': None}} for i in range(2)],
        'release': {'attempted': False, 'status': 'unavailable', 'response_received': False},
        'cleanup': {'owned_children_complete': False, 'daemon_recovery_complete': None,
                    'daemon_left_empty_allowed': True, 'collection_gap': True, 'daemon_native_cleanup': None},
        'fallback_consumption_verified': None, 'copy_loader_observed': False, 'model_inference_verified': False,
        'gaps': ['DAEMON_NATIVE_CLEANUP_UNOBSERVED']}
    try:
        (_example_backend or _run_native)(config, source_root, record)
    except Exception:
        record['gaps'].append('COLLECTION_INCOMPLETE')
    finally:
        record['source_validation']['post'] = _status_source(source_root)
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--config', type=Path);parser.add_argument('--source-root', type=Path)
    parser.add_argument('--output', type=Path);parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    if args.child:
        if not args.execute:parser.exit(1, 'LIF13_CHILD_EXECUTE_REQUIRED\n')
        _child_main();return
    try:
        if args.config is None or args.source_root is None or args.output is None:
            raise ValueError('LIF13_INPUT_REQUIRED')
        if args.output.exists() or args.output.is_symlink():
            raise ValueError('LIF13_FRESH_OUTPUT_REQUIRED')
        raw = args.config.read_bytes()
        if len(raw) > 65536:raise ValueError('LIF13_CONFIG_BOUND')
        config = json.loads(raw)
        # 원본 source/config 확인과 출력 예약을 파괴적 RPC보다 먼저 수행한다.
        preflight(config, args.source_root)
        with args.output.open('x', encoding='utf-8') as stream:
            result = capture(config, args.source_root, execute=args.execute)
            try:
                json.dump(result, stream, sort_keys=True, ensure_ascii=False, allow_nan=False)
                stream.flush()
            except OSError:
                result.setdefault('gaps', []).append('CAPTURE_WRITE_UNAVAILABLE')
                print(json.dumps(result, ensure_ascii=False, allow_nan=False))
                parser.exit(1, 'LIF13_CAPTURE_WRITE_UNAVAILABLE\n')
        print(json.dumps({'executed': result['executed'], 'schema': SCHEMA}))
    except Exception:
        parser.exit(1, 'LIF13_NATIVE_CAPTURE_UNAVAILABLE\n')


if __name__ == '__main__':
    main()
