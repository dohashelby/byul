"""Standalone lifecycle composition; release facts are supplied as pinned data.

The rendered asset appends SPEC. The immutable /8 observer remains unchanged.
This profile records native snapshots only, without request-selection, cache-hit,
worker readiness, resource-release or GPU producer claims.
"""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import types

SPEC = None
_BASE = None


def _dependency():
    directory = os.open('/guard-lifecycle', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open('guard_lifecycle_base.py', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 262144:
            raise ValueError('CONFIGURED_LIFECYCLE_BASE_UNAVAILABLE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(262145)
        after = os.fstat(fd)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if (any(getattr(before, k) != getattr(after, k) for k in fields) or len(raw) != before.st_size
                or hashlib.sha256(raw).hexdigest() != SPEC['base_sha256']):
            raise ValueError('CONFIGURED_LIFECYCLE_BASE_UNAVAILABLE')
        return raw
    finally:
        os.close(fd)


def _relative(code):
    return code.replace(co_filename='guard/adapters/vllm_lifecycle_sensor.py', co_consts=tuple(
        _relative(value) if type(value) is types.CodeType else value for value in code.co_consts))


def _private_base():
    runtime = sys.implementation.name + '-' + '.'.join(map(str, sys.version_info[:3]))
    expected = SPEC['runtime_code'].get(runtime, {}).get(os.name)
    if expected is None or sys.flags.optimize != 0:
        raise ValueError('CONFIGURED_LIFECYCLE_RUNTIME_UNAVAILABLE')
    raw = _dependency()
    base = types.ModuleType('_guard_configured_lifecycle_private_base')
    base.__file__ = '/guard-lifecycle/guard_lifecycle_base.py'
    code = compile(raw, base.__file__, 'exec', dont_inherit=True, optimize=0)
    exec(code, base.__dict__)
    if base.code_digest(_relative(code)) != expected:
        raise ValueError('CONFIGURED_LIFECYCLE_CODE_MISMATCH')
    return _configure(base)


def _configure(base):
    base.PROFILE, base.SCHEMA, base.COMMIT = SPEC['profile'], SPEC['schema'], SPEC['commit']
    base.SOURCE_SHA = {module: row['sha256'] for module, row in SPEC['sources'].items()}
    base.POINTS = {module: row['classes'] for module, row in SPEC['sources'].items()}
    # Unreviewed targets from the previous release must not leak into this one.
    base.FUNCTION_POINTS = {}
    base.HANDLER_POINTS = {}
    base.__file__ = __file__
    original = base.Observer
    argument_ids = frozenset(SPEC['void_return_argument_ids'])

    class ConfiguredObserver(original):
        def snapshot(self, point, args, kwargs, result=None, returned=False):
            # These exact reviewed functions return None. Read the bounded IDs
            # from their input and the *current*, post-call native state; never
            # manufacture a return value or consume a one-shot native iterator.
            if returned and point in argument_ids:
                return super().snapshot(point, args, kwargs)
            return super().snapshot(point, args, kwargs, result, returned)

    base.Observer = ConfiguredObserver
    return base


def install(path=None):
    global _BASE
    if _BASE is not None:
        return False
    try:
        base = _private_base()
        if not base.install(path):
            return False
        _BASE = base
        return True
    except Exception:
        return False


class Middleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if _BASE is None:
            return await self.app(scope, receive, send)
        return await _BASE.Middleware(self.app)(scope, receive, send)


def seal(path='/guard-lifecycle/settings.json'):
    return (_BASE if _BASE is not None else _private_base()).seal(path)


def main():
    if sys.argv[1:] != ['--seal']:
        raise SystemExit('NATIVE_SENSOR_SEAL_ARGUMENTS_REQUIRED')
    try:
        seal()
    except Exception:
        raise SystemExit('NATIVE_SENSOR_SEAL_UNAVAILABLE') from None
