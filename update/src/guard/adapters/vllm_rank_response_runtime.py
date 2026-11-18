"""Separate sealed response-trace sink, installed after the unchanged rank SDK."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys

from . import vllm_rank_ipc_runtime as shared
from . import vllm_rank_response_trace as tracing

ROOT = Path('/guard-response-trace')
ASSETS = frozenset({'guard/adapters/vllm_rank_response_trace.py',
                   'guard/adapters/vllm_rank_response_runtime.py',
                   'guard/adapters/assets/vllm_rank_response_trace_profiles.json', 'sitecustomize.py'})
_INSTALLED = None


def read(path, bound):
    before = path.lstat()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or not 0 < opened.st_size <= bound:
            raise ValueError('RANK_RESPONSE_FILE_BOUND')
        raw = os.read(fd, bound + 1)
        after = os.fstat(fd)
        if (len(raw) != opened.st_size
                or len({shared.sources._identity(s) for s in (before, opened, after, path.lstat())}) != 1):
            raise ValueError('RANK_RESPONSE_FILE_CHANGED')
        return raw
    finally:
        os.close(fd)


def settings(root, *, expected_uid=None):
    expected_uid = os.geteuid() if expected_uid is None else expected_uid
    if type(expected_uid) is not int or not 0 <= expected_uid < 2**31:
        raise ValueError('RANK_RESPONSE_OWNER_INVALID')
    path = root / 'settings.json'
    status = path.lstat()
    if status.st_uid != expected_uid or stat.S_IMODE(status.st_mode) not in {0o400, 0o600}:
        raise ValueError('RANK_RESPONSE_PRIVATE_SETTINGS_REQUIRED')
    raw = read(path, 32768)
    cfg = json.loads(raw, object_pairs_hook=shared._pairs)
    if (type(cfg) is not dict or set(cfg) != {'schema', 'session', 'image', 'key', 'output_dir', 'assets', 'rank_settings_sha256'}
            or cfg['schema'] != 'guard-rank-response-trace-settings/1'
            or type(cfg['session']) is not str or re.fullmatch('[0-9a-f]{32}', cfg['session']) is None
            or type(cfg['key']) is not str or re.fullmatch('[0-9a-f]{64}', cfg['key']) is None
            or cfg['output_dir'] != str(ROOT / 'data' / cfg['session'])
            or type(cfg['assets']) is not dict or set(cfg['assets']) != ASSETS
            or type(cfg['rank_settings_sha256']) is not str or re.fullmatch('[0-9a-f]{64}', cfg['rank_settings_sha256']) is None):
        raise ValueError('RANK_RESPONSE_SETTINGS_INVALID')
    return cfg, sha256(raw).hexdigest()


def freeze(cfg, root):
    for name, pin in cfg['assets'].items():
        shared._asset(root / name, pin)
    modules = {'vllm_rank_response_trace': tracing, 'vllm_rank_response_runtime': sys.modules[__name__]}
    for name, module in modules.items():
        shared._freeze_module(module, cfg['assets']['guard/adapters/' + name + '.py'])
    if tracing.sources is not shared.sources or tracing.rpc is not shared.rpc_hooks or tracing.identity is not shared.identity:
        raise ValueError('RANK_RESPONSE_ALIAS_CHANGED')


class SnapshotWriter(shared.SnapshotWriter):
    schema = 'guard-rank-response-trace-snapshot-candidate/1'


def install():
    global _INSTALLED
    if _INSTALLED is not None:
        return True
    cfg, digest = settings(ROOT)
    freeze(cfg, ROOT)
    rank, rank_digest = shared._settings()
    shared._freeze(rank)
    finder = shared._INSTALLED
    if (type(finder) is not shared.IPCSourceFinder or type(finder.observer) is not shared.CombinedObserver
            or rank_digest != cfg['rank_settings_sha256']
            or any(rank[k] != cfg[k] for k in ('session', 'image', 'key'))
            or any(name in sys.modules for name in tracing.profile(cfg['image'])['sources'])):
        raise ValueError('RANK_RESPONSE_UNCHANGED_SDK_REQUIRED')
    writer = SnapshotWriter(cfg, digest)
    def emit(row):
        freeze(cfg, ROOT)
        shared._freeze(rank)
        writer(row)
    def gap(code):
        emit({'schema': 'guard-rank-response-trace-gap/1', 'diagnostic': code,
              'producer_registered': False, 'fault_causality_verified': False,
              'upper_delivery_verified': False, 'recovery_verified': False, 'whole_resources_released': False})
    observer = tracing.ResponseTrace(observer=finder.observer.rpc, key=bytes.fromhex(cfg['key']), emit=emit, on_gap=gap)
    observer.install()
    _INSTALLED = observer
    return True
