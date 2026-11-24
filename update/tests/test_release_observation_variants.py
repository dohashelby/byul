"""Exact 0.30.0 variants compose unchanged observation dependencies."""
import hashlib
import hmac
import asyncio
import os
import time
from copy import deepcopy
from pathlib import Path
import sys
import types
import json
from types import SimpleNamespace as NS

import pytest

from guard.adapters import vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_lifecycle_release_sensor as release
from guard.adapters import vllm_lifecycle_sensor as base
from guard.adapters import vllm_profiler_sensor as profiler
from guard.adapters import vllm_profiler_metadata as profiler_metadata
from guard.adapters import vllm_stream_buffer_sensor as stream
from guard.adapters import vllm_rank_failure_sensor as rank
from guard.adapters import vllm_rank_failure_metadata as rank_metadata
from guard.adapters import vllm_release_observation_sensor as variant
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture_module
from guard.executors.kubernetes_profiler_control import project_captures
from guard.executors.registry import resolve_executor
from tests.test_lifecycle_capture import external, case, revised  # noqa: F401
from tests.test_a1_profiler_sensor import make_world, invoke, Router, Response, PRIVATE
from tests.test_a1_rank_failure_sensor import codes, QuietLogger
from tests.test_lifecycle_sensor import CONTEXT, writer_factory  # noqa: F401

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('kind,profile,extension,sha,added', [
    ('stream', variant.STREAM_PROFILE, stream, variant.STREAM_SHA256, stream.SOURCE_ADDITIONS),
    ('profiler', variant.PROFILER_PROFILE, profiler, variant.PROFILER_SHA256, variant.PROFILER_ROWS),
    ('rank', variant.RANK_PROFILE, rank, variant.RANK_SHA256, variant.RANK_ROWS),
])
def test_candidate_variant_prepares_exact_private_dependencies_and_rejects_changes(
        external, kind, profile, extension, sha, added):
    assert hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest() == variant.BASE_SHA256
    assert hashlib.sha256(Path(release.__file__).read_bytes()).hexdigest() == variant.RELEASE_SHA256
    assert hashlib.sha256(Path(extension.__file__).read_bytes()).hexdigest() == sha
    assets = external / 'assets'
    manifest_path = assets / 'manifest.json'
    prepared = lifecycle.prepare_assets(assets, REPO, profile=profile)
    assert lifecycle.load_prepared(manifest_path) == prepared
    assert prepared['profile'] == profile and prepared['public_commit'] == release.COMMIT
    assert prepared['source_sha256'] == {**release.source_rows(base.SOURCE_SHA), **added}
    assert set(prepared['assets']) == {'guard_lifecycle_sensor.py', 'guard_lifecycle_base.py',
        'guard_lifecycle_release.py', 'guard_lifecycle_extension.py', 'guard_request_binding.py',
        'request-binding-key.json', 'sitecustomize.py', 'settings.json'}
    assert (assets / 'guard_lifecycle_sensor.py').read_bytes() == variant.render_source(kind,
        request_key_sha256=prepared['assets']['request-binding-key.json']['sha256'])
    assert profiles.point_sources(profile) == profiles.point_sources(release.PROFILE)
    assert resolve_executor({'stream':'stream_buffer','profiler':'profiler_observation','rank':'rank_failure'}[kind],
        'kubernetes', engine='vllm', version='0.30.0') is not None
    missing_asset = deepcopy(prepared)
    del missing_asset['assets']['guard_lifecycle_extension.py']
    extra_asset = deepcopy(prepared)
    extra_asset['assets']['unexpected.py'] = prepared['assets']['guard_lifecycle_extension.py']
    for manifest in (missing_asset, extra_asset):
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with pytest.raises(ValueError, match='^LIFECYCLE_ASSETS_INVALID$'):
            lifecycle.load_prepared(manifest_path)

    changed = assets / 'guard_lifecycle_extension.py'
    changed_bytes = changed.read_bytes() + b'# modified\n'
    changed.write_bytes(changed_bytes)
    manifest_path.write_text(json.dumps(prepared), encoding='utf-8')
    with pytest.raises(ValueError, match='^LIFECYCLE_ASSET_BYTES_INVALID$'):
        lifecycle.load_prepared(manifest_path)

    recomputed = deepcopy(prepared)
    recomputed['assets'][changed.name] = {
        'sha256': hashlib.sha256(changed_bytes).hexdigest(), 'bytes': len(changed_bytes),
    }
    manifest_path.write_text(json.dumps(recomputed), encoding='utf-8')
    with pytest.raises(ValueError, match='^LIFECYCLE_OBSERVATION_DEPENDENCY_UNAVAILABLE$'):
        lifecycle.load_prepared(manifest_path)


@pytest.mark.parametrize('pin,reason', [
    ('STREAM_SHA256', 'LIFECYCLE_OBSERVATION_DEPENDENCY_UNAVAILABLE'),
    ('BINDING_SHA256', 'LIFECYCLE_OBSERVATION_KEY_READER_UNAVAILABLE'),
])
def test_candidate_preparation_preserves_dependency_errors(external, monkeypatch, pin, reason):
    monkeypatch.setattr(variant, pin, '0' * 64)
    with pytest.raises(ValueError, match=f'^{reason}$'):
        lifecycle.prepare_assets(external / 'assets', REPO, profile=variant.STREAM_PROFILE)


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
    ('rank', variant.RANK_PROFILE, rank),
])
def test_private_composition_selects_candidate_profile_and_exact_rows(
        external, monkeypatch, kind, profile, extension):
    runtime = 'cpython-' + '.'.join(map(str, sys.version_info[:3]))
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
    contracts=variant.RANK_RUNTIME_CODE_CONTRACTS if kind=='rank' else extension.RUNTIME_CODE_CONTRACTS
    if (runtime not in contracts or
            runtime == 'cpython-3.12.15' and (sys.platform != 'linux' or os.name != 'posix'
                or os.uname().machine != 'x86_64')):
        with pytest.raises(ValueError):
            module._private_base()
        assert module._BASE is None
        return
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
    if kind=='rank':
        observer.emit('RANK_FAILURE',{'native_request_sha256':[raw],'owner_sha256':raw})
        assert captured[-1][1]['native_request_sha256']==[native['requests'][0]['request_sha256']]
        assert captured[-1][1]['owner_sha256']==raw
    observer.emit('ASGI', {'request_sha256': raw, 'phase': 'APP_RETURN', 'more_body': None})
    assert captured[-1][1]['request_sha256'] == 'a' * 64
    observer.emit('SELECTION', {'requests': [], 'metadata': {'request_selection': {
        'selected_request_id_sha256': raw, 'asgi_request_sha256': raw}}})
    assert captured[-1][1]['metadata']['request_selection']['selected_request_id_sha256'] == native['requests'][0]['request_sha256']
    assert captured[-1][1]['metadata']['request_selection']['asgi_request_sha256'] == 'a' * 64
    token = composed._CHAIN.set(({'point': 'InputProcessor.assign_request_id',
        'request_sha256': [raw], 'weight_model_sha256': raw, '_weight_pid': os.getpid()},))
    try: chain = composed.Observer.chain_rows()
    finally: composed._CHAIN.reset(token)
    observer.emit('NATIVE', {'requests': [], 'metadata': {}, 'call_chain': chain})
    assert captured[-1][1]['call_chain'][0]['request_sha256'] == [native['requests'][0]['request_sha256']]
    assert captured[-1][1]['call_chain'][0]['weight_model_sha256'] == raw
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


def test_profiler_control_route_hmac_reaches_original_producer_and_validator(external, monkeypatch):
    runtime = 'cpython-' + '.'.join(map(str, sys.version_info[:3]))
    prepared = lifecycle.prepare_assets(external / 'assets', REPO, profile=variant.PROFILER_PROFILE)
    module = types.ModuleType('_candidate_profiler_route_fixture')
    module.__file__ = str(external / 'assets/guard_lifecycle_sensor.py')
    exec(compile((external / 'assets/guard_lifecycle_sensor.py').read_bytes(), module.__file__, 'exec',
                 dont_inherit=True, optimize=0), module.__dict__)
    monkeypatch.setattr(module, '_read', lambda name, digest: (external / 'assets' / name).read_bytes())
    monkeypatch.setattr(module, '_request_key', lambda binding: (prepared['session'], b'k' * 32))
    if (runtime not in profiler.RUNTIME_CODE_CONTRACTS or
            runtime == 'cpython-3.12.15' and (sys.platform != 'linux' or os.name != 'posix'
                or os.uname().machine != 'x86_64')):
        with pytest.raises(ValueError):
            module._private_base()
        assert module._BASE is None
        return
    composed = module._private_base()
    ancestor = next(cls for cls in composed.Observer.__mro__ if cls.__name__ == 'Observer')
    monkeypatch.setattr(ancestor, 'route_scope', lambda self, scope: {
        'pid': os.getpid(), 'closed': False, 'request': 'f' * 64, 'invocation': 'e' * 64})
    route_observer = composed.Observer.__new__(composed.Observer)
    request = b'byul-lif14-' + b'a' * 32 + b'-start'
    def scope(path, headers):
        return {'type': 'http', 'path': path, 'headers': headers}
    context = route_observer.route_scope(scope('/start_profile', [(b'x-request-id', request)]))
    assert context['request'] == hmac.new(b'k' * 32,
        b'guard-native-request-binding/1\0asgi-header\0' + request, hashlib.sha256).hexdigest()
    assert context['request'] != hashlib.sha256(request).hexdigest()
    assert context['invocation'] is not None
    missing = route_observer.route_scope(scope('/stop_profile', []))
    assert missing['request'] is None and missing['invocation'] is None
    world = make_world(patch=monkeypatch)
    monkeypatch.setattr(world.observer, 'route_context', lambda: context)
    loop = asyncio.new_event_loop()
    try: invoke(world, loop)
    finally: loop.close()
    samples = [row['payload'] for row in world.observer.recorded if row['event'] == 'PROFILER']
    assert samples and any(row['point'] == 'ProfilerAPI.start_profile' for row in samples)
    assert all(profiler_metadata.validate_sample(row) is None for row in samples)
    assert all(row['asgi_request_sha256'] == context['request']
        and row['asgi_invocation_sha256'] == context['invocation'] for row in samples)


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


def test_composed_profiler_api_producer_parses_and_joins_exact_hmac(case, monkeypatch):
    root = case.root
    prepared = lifecycle.prepare_assets(root / 'candidate-assets', REPO, profile=variant.PROFILER_PROFILE)
    module = types.ModuleType('_candidate_profiler_producer_fixture')
    module.__file__ = str(root / 'candidate-assets/guard_lifecycle_sensor.py')
    exec(compile((root / 'candidate-assets/guard_lifecycle_sensor.py').read_bytes(), module.__file__, 'exec',
                 dont_inherit=True, optimize=0), module.__dict__)
    monkeypatch.setattr(module, '_read', lambda name, digest: (root / 'candidate-assets' / name).read_bytes())
    monkeypatch.setattr(module, '_request_key', lambda binding: (prepared['session'], b'k' * 32))
    runtime = 'cpython-' + '.'.join(map(str, sys.version_info[:3]))
    if (runtime not in profiler.RUNTIME_CODE_CONTRACTS or
            runtime == 'cpython-3.12.15' and (sys.platform != 'linux' or os.name != 'posix'
                or os.uname().machine != 'x86_64')):
        with pytest.raises(ValueError): module._private_base()
        assert module._BASE is None
        return
    composed = module._private_base()
    source = Path(__file__).parent / 'fixtures/profiler-native/api_router.py.txt'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == variant.PROFILER_ROWS[profiler.ROUTER]
    compiled = codes(compile(source.read_bytes(), str(source), 'exec', dont_inherit=True, optimize=0))
    router = types.ModuleType(profiler.ROUTER)
    router.__file__ = str(source)
    router.logger, router.Response = QuietLogger(), Response
    router.start_profile = types.FunctionType(compiled['start_profile'], router.__dict__)
    router.stop_profile = types.FunctionType(compiled['stop_profile'], router.__dict__)
    router.router = Router(router.start_profile, router.stop_profile)
    class Client:
        async def start_profile(self): return None
        async def stop_profile(self): return None
    router.engine_client = lambda request: Client()
    events = []
    class Sink:
        context = {**CONTEXT, 'pid': os.getpid()}
        def write(self, event, payload):
            events.append({'event': event, 'payload': deepcopy(payload),
                'monotonic_ns': max(time.monotonic_ns(), events[-1]['monotonic_ns'] + 1 if events else 0)})
    sink = Sink()
    class Observer(composed.Observer):
        def _writer(self): return sink
    observer = Observer({'session': prepared['session']}, 'b' * 64)
    observer.instrument(router)
    info = {'module': profiler.ROUTER, 'verified': True,
        'source_sha256': variant.PROFILER_ROWS[profiler.ROUTER],
        'code_sha256': composed.code_digest(compiled['<module>'])}
    observer.modules[profiler.ROUTER] = dict(info)
    observer.emit('MODULE', info)
    requests = {}
    loop = asyncio.new_event_loop()
    try:
        for slot in ('start', 'stop'):
            rid = ('byul-lif14-' + 'a' * 32 + '-' + slot).encode()
            context = observer.route_scope({'type': 'http', 'path': '/' + slot + '_profile',
                'headers': [(b'x-request-id', rid)]})
            requests[slot] = context['request']
            token = composed._ROUTE_REQUEST.set(context)
            try:
                assert loop.run_until_complete(router.router.dispatch('/' + slot + '_profile', object())).status_code == 200
            finally:
                context['closed'] = True
                composed._ROUTE_REQUEST.reset(token)
    finally: loop.close()
    assert not observer.disabled_pid and len([row for row in events if row['event'] == 'PROFILER']) == 4
    def insert(rows):
        start, end = rows[0], rows[-1]
        start['payload'].update(profile=prepared['profile'], schema=profiles.SCHEMAS[prepared['profile']],
            commit=release.COMMIT, session=prepared['session'],
            sensor_sha256=prepared['assets']['guard_lifecycle_sensor.py']['sha256'],
            settings_sha256=prepared['assets']['settings.json']['sha256'])
        end['monotonic_ns'] = events[-1]['monotonic_ns'] + 1
        rows[:] = [start, *events, end]
    rows, summary = capture_module.parse_recording(revised(case.raw, insert), prepared)
    assert summary['recording_closed'] is True
    assert len([row for row in rows if row['event'] == 'PROFILER']) == 4
    packet = {'recordings': [{'capture': {'records': rows, 'summary': summary,
        'process_context_verified': True, 'image_digest_verified': False},
        'session_owner_match': True, 'listed_file_identity_match': True}]}
    projection = profiler_metadata.project(packet, ['0'])
    trials = [{'case_id': slot, 'request_id_sha256': requests[slot], 'attempted': True,
        'binding_verified_before_after': True, 'capture_complete': True, 'outcome': 200}
        for slot in ('start', 'stop')]
    joined = project_captures([{'phase': 'final', 'session': prepared['session'],
        'projection': projection}], trials, sealed=True)
    assert {row['case_id'] for row in joined['api_links']} == {'start', 'stop'}
    assert all(row['http_status_matches'] for row in joined['api_links'])
    assert projection['backend_collection_stopped_verified'] is False
    assert projection['trace_output_completed_verified'] is False
    assert projection['cross_process_rpc_verified'] is False


@pytest.mark.parametrize('mode', ['normal', 'backend-stop-failure',
                                 'post-stop-output-failure', 'start-failure'])
def test_exact_candidate_worker_and_wrapper_reuse_backend_without_certifying_it(
        external, monkeypatch, mode):
    prepared = lifecycle.prepare_assets(external / 'assets', REPO, profile=variant.PROFILER_PROFILE)
    module = types.ModuleType('_candidate_profiler_worker_fixture')
    module.__file__ = str(external / 'assets/guard_lifecycle_sensor.py')
    exec(compile((external / 'assets/guard_lifecycle_sensor.py').read_bytes(), module.__file__,
                 'exec', dont_inherit=True, optimize=0), module.__dict__)
    monkeypatch.setattr(module, '_read', lambda name, digest: (external / 'assets' / name).read_bytes())
    monkeypatch.setattr(module, '_request_key', lambda binding: (prepared['session'], b'k' * 32))
    runtime = 'cpython-' + '.'.join(map(str, sys.version_info[:3]))
    if (runtime not in profiler.RUNTIME_CODE_CONTRACTS or
            runtime == 'cpython-3.12.15' and (sys.platform != 'linux' or os.name != 'posix'
                or os.uname().machine != 'x86_64')):
        with pytest.raises(ValueError): module._private_base()
        assert module._BASE is None
        return
    composed = module._private_base()
    world = make_world(session=prepared['session'], mode=mode, patch=monkeypatch,
        fixtures=Path(__file__).parent / 'fixtures/profiler-native-030',
        source_rows=variant.PROFILER_ROWS, private_base=composed)
    # Candidate Worker.profile retains its Torch wrapper and the wrapper retains
    # its backend. Construction is an EXAMPLE precondition; calls below execute
    # the complete, pinned candidate start/stop bodies through real observers.
    world.actor.profiler = world.profile
    world.profile.profiler = world.backend
    world.profile.profiler_config = NS(torch_profiler_dump_cuda_time_total=False)
    loop = asyncio.new_event_loop()
    try:
        invoke(world, loop)
        invoke(world, loop)
    finally:
        loop.close()
    assert world.actor.profiler is world.profile and world.profile.profiler is world.backend
    assert world.backend.starts == 2
    assert world.backend.stops == (0 if mode == 'start-failure' else 2)
    assert world.backend.running == (mode == 'backend-stop-failure')
    events = [row['payload'] for row in world.observer.recorded if row['event'] == 'PROFILER']
    assert events and all(profiler_metadata.validate_sample(row,
        sources=variant.PROFILER_ROWS) is None for row in events)
    # The EXAMPLE backend cannot satisfy the independent Torch C probe.
    assert all(row['backend_generation_sha256'] is None and not row['backend_probe_qualified']
               for row in events)
    assert all(not row['backend_collection_stopped_verified']
        and not row['trace_output_completed_verified'] and not row['cross_process_rpc_verified']
        for row in events)
    packet = {'recordings': [{'session_owner_match': True, 'listed_file_identity_match': True,
        'capture': {'records': [{'seq': 0, 'event': 'START',
            'payload': {'context': dict(CONTEXT), 'profile': variant.PROFILER_PROFILE}}]
            + [{'seq': index + 1, **row} for index, row in enumerate(world.observer.recorded)],
            'summary': {'recording_closed': True}, 'process_context_verified': True,
            'image_digest_verified': False}}]}
    projected = profiler_metadata.project(packet, ['0'])
    assert projected['complete_calls'] > 0 and len(projected['worker_calls']) == 4
    assert projected['call_binding_failures'] == {}
    expected_conflicts = 2 if mode in {'backend-stop-failure', 'post-stop-output-failure'} else 0
    assert len(projected['conflict_candidates']) == (0 if mode == 'start-failure' else 2)
    assert sum(row['flag_adapter_conflict_observed'] for row in projected['conflict_candidates']) == expected_conflicts
    assert not projected['scope_complete'] and not projected['producer_authenticated']
    assert PRIVATE not in json.dumps(world.observer.recorded)
