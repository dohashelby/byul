"""Initialization observations (/14) over the release rows (/25) of the native /8 base.

User decision M-A6-V6 (2026-11-15). The initialization observer and the base stay
byte-identical; this profile only selects the reviewed exact rows of one engine
release for the base modules and for the initialization source module. It adds
no observation code, so the /14 runtime code contract still applies unchanged.
No engine imports or installation occur on module import.
"""
from __future__ import annotations
import hashlib
import os
import stat
import sys
import types

PROFILE = "vllm-native-lifecycle-metadata/26"
SCHEMA = "guard-native-lifecycle-events/26"
BASE_SHA256 = "e537d1d1b19813f171aa30b9344c1dbb49139eed3dea60bfa6dc71744d0d4f39"
RELEASE25_SHA256 = "0b8a824f5347b37c8c78f1064f1ab1722c931539a4fa1a96ad1404b077f98a46"
INITIALIZATION14_SHA256 = "654b22da19bd6ff8cd0e72edb29fa705afa6c01e6fb3616cef9a091e17e9977f"
MP = "vllm.v1.executor.multiproc_executor"
# Exact installed multiproc_executor bytes of the release (image-verified row,
# assets/vllm_observer_pin_rows.json). The three observed functions are
# source-identical to the previous release (docs/dev-1/ma6-version-applicability.md 23).
SOURCE_ADDITIONS = {MP: "3ed0149a8a8e1550c4f062af40e645d1b734e70f99981bc5bf15f0360507648a"}
ASSETS = {'guard_lifecycle_base.py': BASE_SHA256, 'guard_lifecycle_release.py': RELEASE25_SHA256,
          'guard_initialization.py': INITIALIZATION14_SHA256}
_BASE = None


def _read(name):
    directory = os.open('/guard-lifecycle', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 262144:
            raise ValueError('INITIALIZATION_RELEASE_DEPENDENCY_UNAVAILABLE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(262145)
        after = os.fstat(fd)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if (any(getattr(before, k) != getattr(after, k) for k in fields) or len(raw) != before.st_size
                or hashlib.sha256(raw).hexdigest() != ASSETS[name]):
            raise ValueError('INITIALIZATION_RELEASE_DEPENDENCY_UNAVAILABLE')
        return raw
    finally:
        os.close(fd)


def _module(name, filename, raw):
    module = types.ModuleType(name)
    module.__file__ = '/guard-lifecycle/' + filename
    exec(compile(raw, module.__file__, 'exec', dont_inherit=True, optimize=0), module.__dict__)
    return module


def configure(base, release, initialization):
    """Compose exact private copies: /8 base, /25 release rows, then /14 with this release's MP row."""
    release.configure(base)
    if set(SOURCE_ADDITIONS) != set(initialization.SOURCE_ADDITIONS):
        raise ValueError('INITIALIZATION_RELEASE_ROWS_UNAVAILABLE')
    initialization.SOURCE_ADDITIONS = dict(SOURCE_ADDITIONS)
    initialization.configure(base)
    base.PROFILE, base.SCHEMA, base.COMMIT = PROFILE, SCHEMA, release.COMMIT
    base.__file__ = __file__
    return base


def install(path=None):
    global _BASE
    if _BASE is not None:
        return False
    try:
        base = _module('_guard_initialization_release_base', 'guard_lifecycle_base.py', _read('guard_lifecycle_base.py'))
        release = _module('_guard_initialization_release_rows', 'guard_lifecycle_release.py', _read('guard_lifecycle_release.py'))
        initialization = _module('_guard_initialization_release_observer', 'guard_initialization.py', _read('guard_initialization.py'))
        configure(base, release, initialization)
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
    if _BASE is not None:
        return _BASE.seal(path)
    return _module('_guard_initialization_release_base', 'guard_lifecycle_base.py', _read('guard_lifecycle_base.py')).seal(path)


if __name__ == '__main__':
    if sys.argv[1:] != ['--seal']:
        raise SystemExit('NATIVE_SENSOR_SEAL_ARGUMENTS_REQUIRED')
    try:
        seal()
    except Exception:
        raise SystemExit('NATIVE_SENSOR_SEAL_UNAVAILABLE') from None
