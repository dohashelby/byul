"""소유 lab의 IPC 관측 후보를 기동하는 표준 라이브러리 기반 준비 자산.

닫힌 단일 snapshot마다 HMAC을 남기며 원 engine 함수/전송/장애를 바꾸지 않는다.
관측 실패는 gap이고 이 기록을 공통 생산자 인증·그룹 전체 수락으로 쓰지 않는다.
"""
import hmac
from hashlib import sha256
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import types

from . import vllm_lifecycle_sensor as base
from . import vllm_rank_ipc_identity as identity
from . import vllm_rank_ipc_sources as sources
from . import vllm_rank_ipc_hooks as hooks
from . import vllm_worker_generation_contract as relative

ROOT = Path('/guard-rank-ipc')
MODULES = {'vllm_lifecycle_sensor': base, 'vllm_rank_ipc_identity': identity,
           'vllm_rank_ipc_sources': sources, 'vllm_rank_ipc_hooks': hooks,
           'vllm_worker_generation_contract': relative}
ASSETS = frozenset({'guard/__init__.py', 'guard/adapters/__init__.py', 'sitecustomize.py',
    'guard/adapters/vllm_rank_ipc_runtime.py', 'guard/adapters/assets/vllm_rank_ipc_profiles.json',
    *('guard/adapters/' + name + '.py' for name in MODULES)})
_INSTALLED = None


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('RANK_IPC_SETTINGS_DUPLICATE')
        result[key] = value
    return result


def _settings():
    path = ROOT / 'settings.json'
    before = os.stat(path, follow_symlinks=False)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) not in {0o400, 0o600} or not 1 <= opened.st_size <= 32768):
            raise ValueError('RANK_IPC_SETTINGS_UNAVAILABLE')
        raw = os.read(fd, 32769)
        after = os.fstat(fd)
        current = os.stat(path, follow_symlinks=False)
        if len(raw) != opened.st_size or len({sources._identity(v) for v in (before, opened, after, current)}) != 1:
            raise ValueError('RANK_IPC_SETTINGS_UNAVAILABLE')
    finally:
        os.close(fd)
    value = json.loads(raw, object_pairs_hook=_pairs)
    if (type(value) is not dict or set(value) != {'schema', 'session', 'image', 'key', 'output_dir', 'assets'}
            or value['schema'] != 'guard-rank-ipc-settings/1'
            or type(value['session']) is not str or re.fullmatch('[0-9a-f]{32}', value['session']) is None
            or type(value['key']) is not str or re.fullmatch('[0-9a-f]{64}', value['key']) is None
            or value['output_dir'] != str(ROOT / 'data' / value['session'])
            or type(value['assets']) is not dict or set(value['assets']) != ASSETS):
        raise ValueError('RANK_IPC_SETTINGS_INVALID')
    return value, sha256(raw).hexdigest()


def _asset(path, expected):
    if type(expected) is not dict or set(expected) != {'bytes', 'sha256'}:
        raise ValueError('RANK_IPC_ASSET_UNAVAILABLE')
    if type(expected['bytes']) is not int or not 0 <= expected['bytes'] <= 262144:
        raise ValueError('RANK_IPC_ASSET_UNAVAILABLE')
    if expected['bytes']:
        return sources._bytes(str(path), expected)
    # 빈 파일도 정확한 빈 파일로 검증한다. 원 source 함수의 비어 있지 않은 조건은 유지한다.
    before = os.stat(path, follow_symlinks=False)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        current = os.fstat(fd)
        raw = os.read(fd, 1)
        after = os.fstat(fd)
        path_after = os.stat(path, follow_symlinks=False)
        if (not stat.S_ISREG(current.st_mode) or current.st_size != 0 or raw
                or len({sources._identity(v) for v in (before, current, after, path_after)}) != 1
                or expected['sha256'] != sha256(b'').hexdigest()):
            raise ValueError('RANK_IPC_ASSET_UNAVAILABLE')
        return b''
    finally:
        os.close(fd)


def _freeze_module(module, expected, selected=None):
    path = vars(module)['__file__']
    raw = sources._bytes(path, expected)
    codes = sources._codes(compile(raw, path, 'exec', dont_inherit=True, optimize=0))
    values = []
    for name, value in tuple(vars(module).items()):
        if selected is not None and name not in selected:
            continue
        if type(value) is types.FunctionType and value.__module__ == vars(module)['__name__']:
            values.append(value)
        elif isinstance(value, type) and value.__module__ == vars(module)['__name__']:
            for item in type.__getattribute__(value, '__dict__').values():
                if type(item) in (staticmethod, classmethod):
                    item = item.__func__
                if type(item) is types.FunctionType:
                    values.append(item)
    if not values:
        raise ValueError('RANK_IPC_OBSERVER_CODE_UNAVAILABLE')
    for function in values:
        if (function.__globals__ is not vars(module) or function.__qualname__ not in codes
                or base.code_digest(function.__code__) != base.code_digest(codes[function.__qualname__])):
            raise ValueError('RANK_IPC_OBSERVER_CODE_UNAVAILABLE')


def _freeze(value):
    for name, expected in value['assets'].items():
        _asset(ROOT / name, expected)
    modules = {**MODULES, 'vllm_rank_ipc_runtime': sys.modules[__name__]}
    for name, module in modules.items():
        selected = {'sha', 'code_digest', '_context', '_open_directory'} if name == 'vllm_lifecycle_sensor' else None
        _freeze_module(module, value['assets']['guard/adapters/' + name + '.py'], selected)
    if (sources.code_digest is not base.code_digest or sources.relative_code is not relative.relative_code
            or hooks.base is not base or hooks.identity is not identity or hooks.sources is not sources):
        raise ValueError('RANK_IPC_OBSERVER_ALIAS_UNAVAILABLE')


class SnapshotWriter:
    def __init__(self, settings, settings_sha256):
        self.settings, self.settings_sha256 = settings, settings_sha256
        self.key = bytes.fromhex(settings['key'])
        self.pid, self.serial = os.getpid(), 0
        self.lock = threading.RLock()

    def __call__(self, payload):
        if self.pid != os.getpid():
            self.pid, self.serial, self.lock = os.getpid(), 0, threading.RLock()
        with self.lock:
            if self.serial >= 256:
                raise ValueError('RANK_IPC_SNAPSHOT_BOUND')
            context = base._context()
            self.serial += 1
            value = {'schema': 'guard-rank-ipc-snapshot-candidate/1', 'session': self.settings['session'],
                     'image': self.settings['image'], 'settings_sha256': self.settings_sha256,
                     'observer_assets': self.settings['assets'], 'context': context,
                     'ordinal': self.serial, 'payload': payload}
            canonical = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
            record = {'snapshot': value, 'hmac_sha256': hmac.new(self.key, canonical, sha256).hexdigest()}
            raw = (json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()
            if len(raw) > 131072:
                raise ValueError('RANK_IPC_SNAPSHOT_BOUND')
            directory = base._open_directory(self.settings['output_dir'])
            fd = None
            try:
                status = os.fstat(directory)
                if status.st_uid != os.geteuid() or stat.S_IMODE(status.st_mode) != 0o700:
                    raise ValueError('RANK_IPC_SINK_UNAVAILABLE')
                name = 'pid-{}-{}-{}.json'.format(context['pid'], context['start_ticks'], self.serial)
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                if os.write(fd, raw) != len(raw):
                    raise OSError('RANK_IPC_PARTIAL_SNAPSHOT')
            finally:
                if fd is not None:
                    os.close(fd)
                os.close(directory)


class IPCSourceLoader:
    def __init__(self, delegate, observer, writer):
        self.delegate, self.observer, self.writer = delegate, observer, writer

    def create_module(self, spec):
        return self.delegate.create_module(spec)

    def exec_module(self, module):
        self.delegate.exec_module(module)
        try:
            self.observer.attach(module)
        except Exception:
            try:
                self.writer({'source_module': vars(module).get('__name__'),
                             'source_authenticated': False,
                             'engine_group_verified': False, 'producer_registered': False,
                             'gaps': ['RANK_IPC_SOURCE_UNAVAILABLE']})
            except Exception:
                pass

    def get_code(self, fullname):
        return self.delegate.get_code(fullname)

    def get_source(self, fullname):
        return self.delegate.get_source(fullname)

    def get_filename(self, fullname):
        return self.delegate.get_filename(fullname)

    def get_data(self, path):
        return self.delegate.get_data(path)

    def get_resource_reader(self, fullname):
        return self.delegate.get_resource_reader(fullname)

    def is_package(self, fullname):
        return self.delegate.is_package(fullname)


class IPCSourceFinder:
    def __init__(self, observer, writer):
        self.observer, self.writer = observer, writer
        self.local = threading.local()

    def find_spec(self, fullname, path=None, target=None):
        if (fullname not in self.observer.profile['sources'] or target is not None
                or getattr(self.local, 'busy', False)):
            return None
        # 재진입 때 자신만 건너뛰어 기존 finder 순서와 원 loader 선택을 보존한다.
        self.local.busy = True
        try:
            spec = importlib.util.find_spec(fullname)
        finally:
            self.local.busy = False
        if spec is not None and type(spec.loader) is importlib.machinery.SourceFileLoader:
            spec.loader = IPCSourceLoader(spec.loader, self.observer, self.writer)
        return spec


def install():
    global _INSTALLED
    if _INSTALLED is not None:
        return True
    value, digest = _settings()
    _freeze(value)
    writer = SnapshotWriter(value, digest)
    observer = hooks.IPCObserver(image=value['image'], key=bytes.fromhex(value['key']), emit=writer)
    runtime = sys.implementation.name + '-' + '.'.join(map(str, sys.version_info[:3]))
    if observer.profile['runtime'] != runtime or sys.flags.optimize != 0:
        raise ValueError('RANK_IPC_RUNTIME_UNAVAILABLE')
    if any(name in sys.modules for name in observer.profile['sources']):
        raise ValueError('RANK_IPC_SOURCE_ALREADY_LOADED')
    finder = IPCSourceFinder(observer, writer)
    sys.meta_path.insert(0, finder)
    _INSTALLED = finder
    return True
