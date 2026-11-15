"""Candidate /29 and /30 compose reviewed 0.30.0 rows without changing /12 or /15."""
import hashlib
import hmac
import asyncio
import os
from pathlib import Path
import sys
import types

import pytest

from guard.adapters import vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_lifecycle_release_sensor as release
from guard.adapters import vllm_lifecycle_sensor as base
from guard.adapters import vllm_profiler_sensor as profiler
from guard.adapters import vllm_stream_buffer_sensor as stream
from guard.adapters import vllm_release_observation_sensor as variant
from guard.collectors import lifecycle
from guard.executors.registry import resolve_executor
from tests.test_lifecycle_capture import external  # noqa: F401

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('kind,profile,extension,sha,added', [
    ('stream', variant.STREAM_PROFILE, stream, variant.STREAM_SHA256, stream.SOURCE_ADDITIONS),
    ('profiler', variant.PROFILER_PROFILE, profiler, variant.PROFILER_SHA256, variant.PROFILER_ROWS),
])
def test_candidate_variant_prepares_exact_private_dependencies_and_rejects_changes(
        external, kind, profile, extension, sha, added):
    assert hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest() == variant.BASE_SHA256
    assert hashlib.sha256(Path(release.__file__).read_bytes()).hexdigest() == variant.RELEASE_SHA256
    assert hashlib.sha256(Path(extension.__file__).read_bytes()).hexdigest() == sha
    prepared = lifecycle.prepare_assets(external / 'assets', REPO, profile=profile)
    assert lifecycle.load_prepared(external / 'assets/manifest.json') == prepared
    assert prepared['profile'] == profile and prepared['public_commit'] == release.COMMIT
    assert prepared['source_sha256'] == {**release.source_rows(base.SOURCE_SHA), **added}
    assert set(prepared['assets']) == {'guard_lifecycle_sensor.py', 'guard_lifecycle_base.py',
        'guard_lifecycle_release.py', 'guard_lifecycle_extension.py', 'guard_request_binding.py',
        'request-binding-key.json', 'sitecustomize.py', 'settings.json'}
    assert (external / 'assets/guard_lifecycle_sensor.py').read_bytes() == variant.render_source(kind,
        request_key_sha256=prepared['assets']['request-binding-key.json']['sha256'])
    assert profiles.point_sources(profile) == profiles.point_sources(release.PROFILE)
    assert resolve_executor('stream_buffer' if kind == 'stream' else 'profiler_observation',
        'kubernetes', engine='vllm', version='0.30.0') is not None
    changed = external / 'assets/guard_lifecycle_extension.py'
    changed.write_bytes(changed.read_bytes() + b'# modified\n')
    with pytest.raises(ValueError):
        lifecycle.load_prepared(external / 'assets/manifest.json')


def test_profiler_candidate_sources_and_generation_are_separate_from_old_profile():
    assert variant.PROFILER_ROWS[profiler.WRAPPER] != profiler.SOURCE_ADDITIONS[profiler.WRAPPER]
    assert variant.PROFILER_ROWS[profiler.WORKER] != profiler.SOURCE_ADDITIONS[profiler.WORKER]
    assert variant.PROFILER_ROWS[profiler.ROUTER] == profiler.SOURCE_ADDITIONS[profiler.ROUTER]
    assert profiles.source_hashes(variant.PROFILER_PROFILE)[profiler.WRAPPER] == variant.PROFILER_ROWS[profiler.WRAPPER]
    assert profiles.source_hashes(profiler.PROFILE)[profiler.WRAPPER] == profiler.SOURCE_ADDITIONS[profiler.WRAPPER]
    assert b"self.profiler_generations[key]={'generation':self.profiler_nonce()" in Path(profiler.__file__).read_bytes()


@pytest.mark.parametrize('kind,profile,extension', [
    ('stream', variant.STREAM_PROFILE, stream),
    ('profiler', variant.PROFILER_PROFILE, profiler),
])
def test_private_composition_selects_candidate_profile_and_exact_rows(
        external, monkeypatch, kind, profile, extension):
    runtime = 'cpython-' + '.'.join(map(str, sys.version_info[:3]))
    if (runtime not in extension.RUNTIME_CODE_CONTRACTS or
            runtime == 'cpython-3.12.15' and (sys.platform != 'linux' or os.name != 'posix'
                or os.uname().machine != 'x86_64')):
        pytest.skip('Unregistered interpreter patch; the observer itself rejects this runtime')
    prepared = lifecycle.prepare_assets(external / 'assets', REPO, profile=profile)
    module = types.ModuleType('_candidate_observation_fixture')
    module.__file__ = str(external / 'assets/guard_lifecycle_sensor.py')
    exec(compile((external / 'assets/guard_lifecycle_sensor.py').read_bytes(), module.__file__, 'exec',
                 dont_inherit=True, optimize=0), module.__dict__)
    def exact_dependency(name, digest):
        raw = (external / 'assets' / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest
        return raw
    monkeypatch.setattr(module, '_read', exact_dependency)
    monkeypatch.setattr(module, '_request_key', lambda binding: (prepared['session'], b'k' * 32))
    composed = module._private_base()
    assert composed.PROFILE == prepared['profile'] and composed.SCHEMA == profiles.SCHEMAS[profile]
    assert composed.COMMIT == release.COMMIT and composed.SOURCE_SHA == prepared['source_sha256']
    assert composed.MAX_ROWS == extension.MAX_ROWS and composed.MAX_BYTES == extension.MAX_BYTES
    assert composed.id_sha('native-request-id') == hashlib.sha256(b'native-request-id').hexdigest()
    captured = []
    ancestor = next(cls for cls in composed.Observer.__mro__ if cls.__name__ == 'Observer')
    monkeypatch.setattr(ancestor, 'emit', lambda self, event, payload: captured.append((event, payload)))
    monkeypatch.setattr(composed.Observer, 'selection_context', lambda self: {'request': 'a' * 64})
    observer = composed.Observer.__new__(composed.Observer)
    raw = hashlib.sha256(b'native-request-id').hexdigest()
    observer.emit('NATIVE', {'requests': [{'request_sha256': raw}], 'metadata': {
        'external_request_sha256': raw, 'asgi_request_sha256': raw,
        'selected_scheduled_sha256': [raw], 'configured_model_sha256': raw}})
    native = captured[-1][1]
    assert native['requests'][0]['request_sha256'] != raw
    assert native['metadata']['external_request_sha256'] == native['requests'][0]['request_sha256']
    assert native['metadata']['selected_scheduled_sha256'] == [native['requests'][0]['request_sha256']]
    assert native['metadata']['asgi_request_sha256'] == 'a' * 64
    assert native['metadata']['configured_model_sha256'] == raw
    observer.emit('ASGI', {'request_sha256': raw, 'phase': 'APP_RETURN', 'more_body': None})
    assert captured[-1][1]['request_sha256'] == 'a' * 64
    if kind == 'stream':
        request = b'byul-lif04-' + b'a' * 32 + b'-normal'
        scope = {'type': 'http', 'path': '/v1/completions', 'headers': [(b'x-request-id', request)]}
        digest = composed.Observer.stream_wrap.__globals__['request_hash'](scope)
        assert digest == hmac.new(b'k' * 32,
            b'guard-native-request-binding/1\0asgi-header\0' + request, hashlib.sha256).hexdigest()
        assert digest != hashlib.sha256(request).hexdigest()
        assert composed.Observer.stream_wrap.__globals__['request_hash'](
            {'type': 'http', 'path': '/v1/completions', 'headers': []}) is None


def test_candidate_key_failure_leaves_no_observer_installed(external, monkeypatch):
    prepared = lifecycle.prepare_assets(external / 'assets', REPO, profile=variant.STREAM_PROFILE)
    module = types.ModuleType('_candidate_key_failure_fixture')
    module.__file__ = str(external / 'assets/guard_lifecycle_sensor.py')
    exec(compile((external / 'assets/guard_lifecycle_sensor.py').read_bytes(), module.__file__, 'exec',
                 dont_inherit=True, optimize=0), module.__dict__)
    monkeypatch.setattr(module, '_read', lambda name, digest: (external / 'assets' / name).read_bytes())
    monkeypatch.setattr(module, '_request_key', lambda binding: (_ for _ in ()).throw(ValueError('bad key')))
    assert module.install() is False and module._BASE is None
    called = []
    async def app(scope, receive, send):
        called.append(scope)
    asyncio.run(module.Middleware(app)({'type': 'http', 'path': '/v1/completions'}, None, None))
    assert len(called) == 1
    assert prepared['assets']['request-binding-key.json']['bytes'] > 0


def test_candidate_key_permissions_and_header_fingerprint(external):
    prepared = lifecycle.prepare_assets(external / 'assets', REPO, profile=variant.STREAM_PROFILE)
    header = 'byul-lif04-' + 'b' * 32 + '-normal'
    digest = lifecycle.request_header_fingerprint(external / 'assets/manifest.json', prepared, header)
    assert digest != hashlib.sha256(header.encode()).hexdigest()
    key = external / 'assets/request-binding-key.json'
    key.chmod(0o644)
    with pytest.raises(ValueError):
        lifecycle.load_prepared(external / 'assets/manifest.json')
    with pytest.raises(ValueError):
        lifecycle.request_header_fingerprint(external / 'assets/manifest.json', prepared, header)
