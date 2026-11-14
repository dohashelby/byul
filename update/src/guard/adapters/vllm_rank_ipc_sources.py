"""M-A3 IPC 관측 후보의 정확 설치 소스와 원 함수 본문만 대조한다.

기존 native24 고정값·등록·판정 경로는 바꾸지 않는다. 이 대조는 엔진 호출,
감싸기 설치, 장애 주입 또는 프로세스 그룹 인증을 수행하지 않는다.
"""
from abc import ABCMeta
import json
import os
from pathlib import Path
import stat
import sys
import types

from .vllm_lifecycle_sensor import code_digest
from .vllm_worker_generation_contract import relative_code

PROFILES = Path(__file__).with_name('assets') / 'vllm_rank_ipc_profiles.json'


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _bytes(path, expected):
    before = os.stat(path, follow_symlinks=False)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size != expected['bytes']:
            raise ValueError
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(262145)
        after = os.fstat(fd)
        current = os.stat(path, follow_symlinks=False)
        from hashlib import sha256
        if (not 0 < len(raw) <= 262144 or len(raw) != expected['bytes']
                or len({_identity(v) for v in (before, opened, after, current)}) != 1
                or sha256(raw).hexdigest() != expected['sha256']):
            raise ValueError
        return raw
    finally:
        os.close(fd)


def _codes(code):
    result = {code.co_qualname: code}
    for child in code.co_consts:
        if type(child) is types.CodeType:
            for name, value in _codes(child).items():
                if name not in result:
                    result[name] = value
    return result


def _function(module, name):
    owner = module
    parts = name.split('.')
    if not 1 <= len(parts) <= 2:
        raise ValueError
    for part in parts:
        if type(owner) is types.ModuleType:
            owner = vars(owner).get(part)
        elif type(owner) in (type, ABCMeta):
            owner = type.__getattribute__(owner, '__dict__').get(part)
        else:
            raise ValueError
    if type(owner) in (staticmethod, classmethod):
        owner = owner.__func__
    if type(owner) is not types.FunctionType or owner.__globals__ is not vars(module):
        raise ValueError
    return owner


def qualify(module, *, image, scope='sources'):
    """고정 이미지 행·정확 파일·현재 원 함수가 모두 맞을 때 후보 자료만 반환한다."""
    try:
        if (type(module) is not types.ModuleType or type(image) is not str
                or type(scope) is not str or scope not in {'sources', 'rpc_sources'}):
            raise ValueError
        profiles = json.loads(PROFILES.read_bytes())
        if profiles['schema'] != 'guard-rank-ipc-source-candidates/1':
            raise ValueError
        matches = [p for p in profiles['profiles'] if p['image'] == image]
        if len(matches) != 1:
            raise ValueError
        profile, = matches
        runtime = sys.implementation.name + '-' + '.'.join(map(str, sys.version_info[:3]))
        if profile['runtime'] != runtime or sys.flags.optimize != 0:
            raise ValueError
        expected = profile[scope][vars(module).get('__name__')]
        path = vars(module).get('__file__')
        if type(path) is not str:
            raise ValueError
        raw = _bytes(path, expected)
        compiled = _codes(compile(raw, path, 'exec', dont_inherit=True, optimize=0))
        functions, digests = {}, {}
        for name in expected['functions']:
            actual = _function(module, name)
            digest = code_digest(relative_code(compiled[name], expected['path']))
            if (actual.__qualname__ != name or digest != expected['body_code_sha256'][name]
                    or code_digest(relative_code(actual.__code__, expected['path'])) != digest):
                raise ValueError
            functions[name] = actual
            digests[name] = digest
        return {'profile': profile['id'], 'source_sha256': expected['sha256'],
                'functions': functions, 'body_code_sha256': digests,
                'pin_status': 'candidate_pin', 'engine_group_verified': False,
                'observer_installed': False, 'producer_registered': False}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SyntaxError):
        raise ValueError('RANK_IPC_SOURCE_UNAVAILABLE') from None
