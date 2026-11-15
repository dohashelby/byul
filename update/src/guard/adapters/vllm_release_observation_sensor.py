"""Exact 0.30.0 /25 base plus unchanged stream or profiler observation code.

The variant is fixed by the rendered source. All three private dependencies are
verified before execution; neither the original /12 nor /15 sensor is edited.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
import sys
import types

STREAM_PROFILE = 'vllm-native-lifecycle-metadata/29'
PROFILER_PROFILE = 'vllm-native-lifecycle-metadata/30'
PROFILES = {'stream': (STREAM_PROFILE, 'guard-native-lifecycle-events/29'),
            'profiler': (PROFILER_PROFILE, 'guard-native-lifecycle-events/30')}
BASE_SHA256 = 'e537d1d1b19813f171aa30b9344c1dbb49139eed3dea60bfa6dc71744d0d4f39'
RELEASE_SHA256 = '0b8a824f5347b37c8c78f1064f1ab1722c931539a4fa1a96ad1404b077f98a46'
STREAM_SHA256 = 'c224517c891bf73b2eb1be20d55edfc95416d7938175932ba2bb9fa1ec31b93c'
PROFILER_SHA256 = '97f920da0ca9ab9ed41b814ab0e08b3af99833cb3a680686c9ffc07d552e1291'
BINDING_SHA256 = '0d490e63317f0344d26f1bd237a63dab7dc26cdf7768fedbe86742cd8d149846'
KEY_SHA256 = None  # Rendered source replaces this assignment with a literal.
PROFILER_ROWS = {
    'vllm.profiler.wrapper': 'b1d3fff5035031d86ead1925d541cd07f084b6711875b592401b5528e3f730f4',
    'vllm.v1.worker.gpu_worker': '6994436e4547c0996ab555b3e814e8ccea26b650dfc1b378b97d640745309347',
    'vllm.entrypoints.serve.profile.api_router': 'b943c3a2201134e2d0b38bfa86d00e8b5685f5be6a54f6c7178dee784c1b78e5',
}
VARIANT = None  # Rendered source replaces this assignment with a literal.
_BASE = None


def render_source(variant, *, request_key_sha256):
    if variant not in PROFILES or type(request_key_sha256) is not str or re.fullmatch('[0-9a-f]{64}', request_key_sha256) is None:
        raise ValueError('LIFECYCLE_OBSERVATION_VARIANT_UNAVAILABLE')
    raw = Path(__file__).read_bytes()
    return raw + ('\nVARIANT = ' + repr(variant) + '\nKEY_SHA256 = ' + repr(request_key_sha256) + '\n'
        "if __name__ == '__main__':\n"
        "    if sys.argv[1:] != ['--seal']:\n"
        "        raise SystemExit('LIFECYCLE_OBSERVATION_SEAL_ARGUMENTS_REQUIRED')\n"
        "    try: seal()\n"
        "    except Exception: raise SystemExit('LIFECYCLE_OBSERVATION_SEAL_UNAVAILABLE') from None\n").encode()


def _read(name, digest):
    directory = os.open('/guard-lifecycle', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 262144:
            raise ValueError('LIFECYCLE_OBSERVATION_DEPENDENCY_UNAVAILABLE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(262145)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if (len(raw) != before.st_size or identity(before) != identity(after)
                or hashlib.sha256(raw).hexdigest() != digest):
            raise ValueError('LIFECYCLE_OBSERVATION_DEPENDENCY_UNAVAILABLE')
        return raw
    finally:
        os.close(fd)


def _module(name, filename, raw):
    module = types.ModuleType(name)
    module.__file__ = '/guard-lifecycle/' + filename
    exec(compile(raw, module.__file__, 'exec', dont_inherit=True, optimize=0), module.__dict__)
    return module


def _request_key(binding):
    # Reuse the /28 asset reader, including no-follow, mode, owner, session and
    # before/after identity validation. The exact reader source is pinned below.
    binding.os, binding.stat, binding.hashlib = os, stat, hashlib
    return binding._binding_key({'request_key_sha256': KEY_SHA256})


def _private_base():
    if VARIANT not in PROFILES:
        raise ValueError('LIFECYCLE_OBSERVATION_VARIANT_UNAVAILABLE')
    base = _module('_guard_observation_base', 'guard_lifecycle_base.py',
                   _read('guard_lifecycle_base.py', BASE_SHA256))
    release = _module('_guard_observation_release', 'guard_lifecycle_release.py',
                      _read('guard_lifecycle_release.py', RELEASE_SHA256))
    dependency = STREAM_SHA256 if VARIANT == 'stream' else PROFILER_SHA256
    extension = _module('_guard_observation_extension', 'guard_lifecycle_extension.py',
                        _read('guard_lifecycle_extension.py', dependency))
    binding = _module('_guard_observation_key_reader', 'guard_request_binding.py',
                      _read('guard_request_binding.py', BINDING_SHA256))
    session, key = _request_key(binding)
    release.configure(base)
    if VARIANT == 'profiler':
        if set(extension.SOURCE_ADDITIONS) != set(PROFILER_ROWS):
            raise ValueError('PROFILER_RELEASE_ROWS_UNAVAILABLE')
        extension.SOURCE_ADDITIONS = dict(PROFILER_ROWS)
    extension.configure(base)
    parent = base.Observer
    def digest(domain, value):
        if type(value) is not str or not 0 < len(value.encode('utf-8', 'surrogatepass')) <= 512:
            return None
        return hmac.new(key, b'guard-native-request-binding/1\0' + domain.encode() + b'\0'
            + value.encode('utf-8', 'surrogatepass'), hashlib.sha256).hexdigest()
    def header(scope):
        if type(scope) is not dict or scope.get('type') != 'http' or scope.get('path') != '/v1/completions':
            return None
        headers = scope.get('headers', ())
        values = [pair[1] for pair in headers if type(pair) in (tuple, list) and len(pair) == 2
                  and type(pair[0]) is bytes and pair[0].lower() == b'x-request-id'] if (
                      type(headers) in (tuple, list) and len(headers) <= 64) else []
        if len(values) != 1 or type(values[0]) is not bytes or not 0 < len(values[0]) <= 512:
            return None
        return hmac.new(key, b'guard-native-request-binding/1\0asgi-header\0' + values[0], hashlib.sha256).hexdigest()
    request_fields = frozenset({'request_sha256', 'external_request_sha256', 'selected_request_id_sha256'})
    request_lists = frozenset({'observed_request_sha256', 'selected_scheduled_sha256', 'selected_finished_sha256'})
    def relay(value, context):
        # These are the base observer's request-ID fields only. Model, source,
        # file, object and invocation hashes retain their original meaning.
        def legacy(digest):
            if digest is None:
                return None
            if type(digest) is not str or re.fullmatch('[0-9a-f]{64}', digest) is None:
                raise ValueError('LIFECYCLE_OBSERVATION_REQUEST_ID_INVALID')
            return hmac.new(key, b'guard-native-request-binding/1\0legacy-request-sha\0'
                + bytes.fromhex(digest), hashlib.sha256).hexdigest()
        if type(value) is list:
            return [relay(item, context) for item in value]
        if type(value) is not dict:
            return value
        result = {}
        for name, item in value.items():
            if name == 'asgi_request_sha256':
                result[name] = context.get('request') if context else None
            elif name in request_fields:
                result[name] = legacy(item)
            elif name in request_lists:
                result[name] = [legacy(entry) for entry in item] if type(item) is list else None
            elif type(item) in (dict, list):
                result[name] = relay(item, context)
            else:
                result[name] = item
        return result
    class PrivateObserver(parent):
        def __init__(self, config, config_sha):
            if config['session'] != session:
                raise ValueError('REQUEST_BINDING_KEY_SESSION_MISMATCH')
            super().__init__(config, config_sha)
        def route_scope(self, scope):
            context = super().route_scope(scope)
            if context is not None:
                context['request'] = header(scope)
                context['invocation'] = digest('asgi-invocation', context['invocation'])
            return context
        def emit(self, event, payload):
            if event == 'ASGI':
                context = self.selection_context()
                payload = {**payload, 'request_sha256': context.get('request') if context else None}
            elif event in {'NATIVE', 'SELECTION'}:
                try: payload = relay(payload, self.selection_context())
                except (TypeError, ValueError):
                    self.disabled_pid = os.getpid()
                    return
            return super().emit(event, payload)
    base.Observer = PrivateObserver
    if VARIANT == 'stream':
        def private_stream_request(scope):
            # Keep the exact /12 eligibility grammar without computing raw SHA.
            if type(scope) is not dict or scope.get('type') != 'http' or scope.get('path') != '/v1/completions':
                return None
            headers = scope.get('headers')
            if type(headers) not in (tuple, list) or len(headers) > 128:
                return None
            values = [pair[1] for pair in headers if type(pair) in (tuple, list) and len(pair) == 2
                and type(pair[0]) is bytes and type(pair[1]) is bytes and pair[0].lower() == b'x-request-id']
            if (len(values) != 1 or re.fullmatch(
                    rb'byul-lif04-[0-9a-f]{32}-(normal-before|normal|slow|slow-close|normal-after)',
                    values[0]) is None):
                return None
            return header(scope)
        extension.request_hash = private_stream_request
    base.PROFILE, base.SCHEMA = PROFILES[VARIANT]
    base.COMMIT = release.COMMIT
    base.__file__ = __file__
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

