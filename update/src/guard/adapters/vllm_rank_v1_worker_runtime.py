"""현재 V1의 읽기 전용 /2 관측을 동결 자산으로 설치한다. 장애 제어는 설치하지 않는다."""
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys

from . import vllm_rank_ipc_runtime as common
from . import vllm_rank_v1_worker_binding as binding
from . import vllm_rank_private_bootstrap as bootstrap

ROOT = common.ROOT
ASSETS = common.ASSETS | frozenset({
    'guard/adapters/vllm_rank_v1_worker_binding.py', 'guard/adapters/vllm_rank_v1_worker_runtime.py',
    'guard/adapters/vllm_rank_private_bootstrap.py',
    'guard/adapters/assets/vllm_rank_v1_source_reviews.json'})
SCHEMA = 'guard-rank-ipc-settings/2'
SETTINGS_FIELDS = frozenset({'schema', 'session', 'image', 'key', 'output_dir', 'assets',
    'fresh_profile', 'fresh_prepared_sha256', 'binding_key_asset_sha256', 'binding_key_path'})
_INSTALLED = None


def asset_bytes(update=None):
    """준비/검토의 같은 정확 SDK 자산을 반환한다. 엔진을 import하지 않는다."""
    package_root = Path(update) / 'src' if update is not None else Path(__file__).resolve().parents[2]
    return {name: (bootstrap.render('vllm_rank_v1_worker_runtime').encode() if name == 'sitecustomize.py'
                   else (package_root / name).read_bytes()) for name in sorted(ASSETS)}


def private_key(path, *, expected_sha256, session):
    """고정 키 자산의 owner/0600·dirfd/NOFOLLOW·세션·정확 bytes만 대조한다. 출력하지 않는다."""
    path = Path(path)
    directory = common.base._open_directory(str(path.parent))
    fd = None
    try:
        parent = os.fstat(directory)
        if (parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) & 0o022
                or path.name != 'request-binding-key.json' or not binding._fingerprint(expected_sha256)):
            raise ValueError('RANK_V1_KEY_UNAVAILABLE')
        before = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600 or not 0 < opened.st_size <= 256):
            raise ValueError('RANK_V1_KEY_UNAVAILABLE')
        raw, after = os.read(fd, 257), os.fstat(fd)
        current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if (len(raw) != opened.st_size or sha256(raw).hexdigest() != expected_sha256
                or len({common.sources._identity(v) for v in (before, opened, after, current)}) != 1):
            raise ValueError('RANK_V1_KEY_UNAVAILABLE')
        value = json.loads(raw, object_pairs_hook=common._pairs)
        if (type(value) is not dict or set(value) != {'schema', 'session', 'key_hex'}
                or value['schema'] != 'guard-lifecycle-request-key/1' or value['session'] != session
                or type(value['key_hex']) is not str or re.fullmatch('[0-9a-f]{64}', value['key_hex']) is None):
            raise ValueError('RANK_V1_KEY_UNAVAILABLE')
        return bytes.fromhex(value['key_hex'])
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def validate_settings(value):
    if (type(value) is not dict or set(value) != SETTINGS_FIELDS or value['schema'] != SCHEMA
            or type(value['session']) is not str or re.fullmatch('[0-9a-f]{32}', value['session']) is None
            or type(value['key']) is not str or re.fullmatch('[0-9a-f]{64}', value['key']) is None
            or type(value['image']) is not str or value['fresh_profile'] != 'vllm-native-lifecycle-metadata/28'
            or not binding._fingerprint(value['fresh_prepared_sha256'])
            or not binding._fingerprint(value['binding_key_asset_sha256'])
            or value['binding_key_path'] != '/guard-lifecycle/request-binding-key.json'
            or value['output_dir'] != str(ROOT / 'data' / value['session'])
            or type(value['assets']) is not dict or set(value['assets']) != ASSETS):
        raise ValueError('RANK_V1_SETTINGS_INVALID')
    return value


def _settings():
    path = ROOT / 'settings.json'
    before = path.lstat()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600 or not 0 < opened.st_size <= 32768):
            raise ValueError('RANK_V1_SETTINGS_INVALID')
        raw, after = os.read(fd, 32769), os.fstat(fd)
        if (len(raw) != opened.st_size or len({common.sources._identity(s)
                for s in (before, opened, after, path.lstat())}) != 1):
            raise ValueError('RANK_V1_SETTINGS_INVALID')
    finally:
        os.close(fd)
    value = validate_settings(json.loads(raw, object_pairs_hook=common._pairs))
    key = private_key(value['binding_key_path'], expected_sha256=value['binding_key_asset_sha256'], session=value['session'])
    if not hmac.compare_digest(key, bytes.fromhex(value['key'])):
        raise ValueError('RANK_V1_KEY_MISMATCH')
    return value, sha256(raw).hexdigest()


def freeze(value):
    common._freeze(value)
    for name, module in (('vllm_rank_v1_worker_binding', binding), ('vllm_rank_v1_worker_runtime', sys.modules[__name__]),
                         ('vllm_rank_private_bootstrap', bootstrap)):
        common._freeze_module(module, value['assets']['guard/adapters/' + name + '.py'])
    if (binding.sources is not common.sources or binding.ipc is not common.hooks
            or binding.request_identity is not common.request_identity or ROOT != common.ROOT
            or SnapshotWriter.schema != 'guard-rank-ipc-snapshot-candidate/2'
            or common.SnapshotWriter.schema != 'guard-rank-ipc-snapshot-candidate/1'
            or common.sources.V1_REVIEWS != Path(common.sources.__file__).with_name('assets') / 'vllm_rank_v1_source_reviews.json'):
        raise ValueError('RANK_V1_OBSERVER_ALIAS_UNAVAILABLE')


class SnapshotWriter(common.SnapshotWriter):
    schema = 'guard-rank-ipc-snapshot-candidate/2'


def install():
    global _INSTALLED
    if _INSTALLED is not None:
        raise ValueError('RANK_V1_ALREADY_INSTALLED')
    value, digest = _settings()
    freeze(value)
    writer = SnapshotWriter(value, digest)
    observer = binding.WorkerObserver(image=value['image'], key=bytes.fromhex(value['key']), session=value['session'],
        fresh_prepared_sha256=value['fresh_prepared_sha256'], binding_key_asset_sha256=value['binding_key_asset_sha256'],
        emit=writer, verify_observer=lambda: freeze(value))
    if any(name in sys.modules for name in observer.profile['sources']):
        raise ValueError('RANK_V1_MODULE_PRELOADED')
    # 재사용한 loader/finder는 원 import 선택과 원 import 예외를 보존한다.
    finder = common.IPCSourceFinder(observer, writer)
    sys.meta_path.insert(0, finder)
    _INSTALLED = observer, finder
    return {'observer_installed': True, 'engine_group_verified': False, 'producer_registered': False,
            'scope_complete': False, 'source_authenticated': False}
