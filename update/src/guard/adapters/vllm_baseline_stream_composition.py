"""Append to the reviewed /28 composition for baseline V1 stream observation.

The original /12 extension is loaded from exact bytes after the /28 private
request-key boundary qualifies. This source installs no hook on import. The
three Uvicorn rows are wheel source candidates, not installed-image evidence.
"""
import hashlib
import hmac
import os
import re
import stat
import sys
import types

PROFILE = 'vllm-native-lifecycle-metadata/33'
SCHEMA = 'guard-native-lifecycle-events/33'
BASE_PROFILE = 'vllm-native-lifecycle-metadata/28'
EXTENSION_SHA256 = 'c224517c891bf73b2eb1be20d55edfc95416d7938175932ba2bb9fa1ec31b93c'
SOURCE_ROWS = {
    'uvicorn.protocols.http.h11_impl': 'e1bf8ab302b9ec505a44f7875e52bfa32f2928ceaf1b585a00b0887911fed5b0',
    'uvicorn.protocols.http.httptools_impl': 'b6e4010a20faadfe4341ec90c0755ce39af11fda2ea233299178bd286e8d4419',
    'uvicorn.protocols.http.flow_control': 'd39d16560df512f3ce907c329c2a0c3f5cd7165fef3b753876eae5739bf2a785',
}
# Stream targets belong to the original extension's module-qualified TARGETS.
# They are source pins here, never duplicated generic NATIVE request points.
SOURCE_LAYOUT = {
    'uvicorn.protocols.http.h11_impl': {'sha256': SOURCE_ROWS['uvicorn.protocols.http.h11_impl'],
        'bytes': 20694, 'classes': {}},
    'uvicorn.protocols.http.httptools_impl': {'sha256': SOURCE_ROWS['uvicorn.protocols.http.httptools_impl'],
        'bytes': 21805, 'classes': {}},
    'uvicorn.protocols.http.flow_control': {'sha256': SOURCE_ROWS['uvicorn.protocols.http.flow_control'],
        'bytes': 1701, 'classes': {}},
}
STREAM_CODE_3128 = '9ce758b73185aedbab0f9d46ea379f12af3f189d3e9fc9460cab57bc9ef12c50'
RUNTIME_CODES = {
    'cpython-3.12.3': '8cef15ad93903b20197b5f5bbec66504b9017b438a4e50082b9c104832eb5a4e',
    'cpython-3.12.8': STREAM_CODE_3128,
}


def _stream_dependency():
    directory = os.open('/guard-lifecycle', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        before_path = os.stat('guard_stream_buffer_extension.py', dir_fd=directory, follow_symlinks=False)
        fd = os.open('guard_stream_buffer_extension.py', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 262144:
            raise ValueError('BASELINE_STREAM_EXTENSION_UNAVAILABLE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(262145)
        after = os.fstat(fd)
        after_path = os.stat('guard_stream_buffer_extension.py', dir_fd=directory, follow_symlinks=False)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if (len(raw) != before.st_size or len({identity(s) for s in (before_path, before, after, after_path)}) != 1
                or hashlib.sha256(raw).hexdigest() != EXTENSION_SHA256):
            raise ValueError('BASELINE_STREAM_EXTENSION_UNAVAILABLE')
        return raw
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def _stream_header(scope, key):
    if type(scope) is not dict or scope.get('type') != 'http' or scope.get('path') != '/v1/completions':
        return None
    headers = scope.get('headers')
    if type(headers) not in (tuple, list) or len(headers) > 128:
        return None
    found = [pair[1] for pair in headers if type(pair) in (tuple, list) and len(pair) == 2
             and type(pair[0]) is bytes and type(pair[1]) is bytes and pair[0].lower() == b'x-request-id']
    if (len(found) != 1 or re.fullmatch(
            rb'byul-lif04-[0-9a-f]{32}-(normal-before|normal|slow|slow-close|normal-after)', found[0]) is None):
        return None
    return hmac.new(key, b'guard-native-request-binding/1\0asgi-header\0' + found[0], hashlib.sha256).hexdigest()


if '_configure' in globals():
    _baseline_stream_previous = _configure
    def _configure(base):
        if (SPEC['profile'] != PROFILE or SPEC['schema'] != SCHEMA
                or any(SPEC['sources'].get(name, {}).get('sha256') != digest
                       for name, digest in SOURCE_ROWS.items())):
            raise ValueError('BASELINE_STREAM_BASE_UNAVAILABLE')
        # /28 validates the exact private key and establishes HMAC-only native
        # emission before the stream wrapper can be installed.
        base = _baseline_stream_previous(base)
        session, key = _binding_key(SPEC)
        if SPEC['request_key_sha256'] is None or not key:
            raise ValueError('BASELINE_STREAM_KEY_UNAVAILABLE')
        raw = _stream_dependency()
        extension = types.ModuleType('_guard_baseline_stream_extension')
        extension.__file__ = '/guard-lifecycle/guard_stream_buffer_extension.py'
        exec(compile(raw, extension.__file__, 'exec', dont_inherit=True, optimize=0), extension.__dict__)
        if (set(extension.SOURCE_ADDITIONS) != set(SOURCE_ROWS)
                or extension.STREAM_SCHEMA != 'guard-native-stream-buffer/1'
                or extension.CODE_FILENAME != 'guard/adapters/vllm_stream_buffer_sensor.py'):
            raise ValueError('BASELINE_STREAM_EXTENSION_UNAVAILABLE')
        extension.SOURCE_ADDITIONS = dict(SOURCE_ROWS)
        if extension.RUNTIME_CODE_CONTRACTS.get('cpython-3.12.3') != RUNTIME_CODES['cpython-3.12.3']:
            raise ValueError('BASELINE_STREAM_EXTENSION_UNAVAILABLE')
        extension.RUNTIME_CODE_CONTRACTS = dict(RUNTIME_CODES)
        base = extension.configure(base)  # checks exact original code fingerprint
        extension.request_hash = lambda scope: _stream_header(scope, key)
        base.PROFILE, base.SCHEMA = PROFILE, SCHEMA
        base.COMMIT = SPEC['commit']
        base.__file__ = __file__
        return base
