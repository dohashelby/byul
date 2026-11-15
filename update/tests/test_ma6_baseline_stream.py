"""Approved 0.34 wheel source into the original stream producer and consumer; EXAMPLE only."""
import asyncio
import copy
import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from types import SimpleNamespace
import types

import pytest

from guard.adapters import vllm_baseline_stream_composition as baseline
from guard.adapters import vllm_stream_buffer_metadata as metadata
from guard.adapters import vllm_stream_buffer_sensor as stream
from guard.adapters import vllm_lifecycle_sensor as base8
from guard.adapters import vllm_lifecycle_catalog as catalog
from guard.adapters import vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_observer_abi as abi
from guard.collectors import lifecycle
from guard.executors.registry import resolve_executor
from guard.executors.lifecycle_capture import parse_recording
from tests.test_lifecycle_capture import case, revised  # noqa: F401
from tests import test_a1_stream_buffer_sensor as original
from tests.test_lifecycle_sensor import CONTEXT


FIXTURES = Path(__file__).parent / 'fixtures/uvicorn-034'
SECRET = b'EXAMPLE-baseline-stream-private-key'
REPO = Path(__file__).resolve().parents[2]


def composed_world(tmp_path, monkeypatch):
    prepared = lifecycle.prepare_assets(tmp_path / 'assets', REPO, profile=baseline.PROFILE)
    key = bytes.fromhex(json.loads((tmp_path / 'assets/request-binding-key.json').read_bytes())['key_hex'])
    source = tmp_path / 'assets/guard_lifecycle_sensor.py'
    module = types.ModuleType('_baseline_stream_composed_fixture')
    module.__file__ = str(source)
    exec(compile(source.read_bytes(), str(source), 'exec', dont_inherit=True, optimize=0), module.__dict__)
    monkeypatch.setattr(module, '_dependency', lambda: Path(base8.__file__).read_bytes())
    monkeypatch.setattr(module, '_binding_key', lambda spec: (prepared['session'], key))
    monkeypatch.setattr(module, '_stream_dependency', lambda: Path(stream.__file__).read_bytes())
    base = module._private_base()
    ancestor = next(cls for cls in base.Observer.__mro__ if cls.__name__ == 'Observer')
    def record(self, event, payload):
        self.recorded.append({'event': event, 'payload': copy.deepcopy(payload), 'monotonic_ns': time.monotonic_ns()})
    monkeypatch.setattr(ancestor, 'emit', record)
    class Observer(base.Observer):
        def __init__(self):
            super().__init__({'session': prepared['session']}, 'b' * 64)
            self.recorded = []
        def _writer(self):
            return SimpleNamespace(context={**CONTEXT, 'pid': os.getpid()})
    observer = Observer()
    base._OBSERVER = observer
    monkeypatch.setattr(original, 'REFERENCES', FIXTURES)
    monkeypatch.setattr(stream, 'SOURCE_ADDITIONS', dict(baseline.SOURCE_ROWS))
    modules = {}
    for name in baseline.SOURCE_ROWS:
        target = original.module_from_reference(name)
        observer.instrument(target)
        observer.modules[name] = {'source_sha256': baseline.SOURCE_ROWS[name], 'code_sha256': 'c' * 64}
        observer.emit('MODULE', {'module': name, 'verified': True, **observer.modules[name]})
        modules[name] = target
    return prepared, SimpleNamespace(base=base, observer=observer, modules=modules, key=key)


def test_exact_wheel_rows_keep_old_extension_bytes_and_distinct_abi():
    assert hashlib.sha256(Path(stream.__file__).read_bytes()).hexdigest() == baseline.EXTENSION_SHA256
    assert set(baseline.SOURCE_ROWS) == set(stream.SOURCE_ADDITIONS)
    assert baseline.SOURCE_ROWS['uvicorn.protocols.http.flow_control'] == stream.SOURCE_ADDITIONS[
        'uvicorn.protocols.http.flow_control']
    assert baseline.SOURCE_ROWS['uvicorn.protocols.http.h11_impl'] != stream.SOURCE_ADDITIONS[
        'uvicorn.protocols.http.h11_impl']
    for module, digest in baseline.SOURCE_ROWS.items():
        assert hashlib.sha256((FIXTURES / (module.rsplit('.', 1)[-1] + '.py')).read_bytes()).hexdigest() == digest
    assert baseline.STREAM_CODE_3128 == '9ce758b73185aedbab0f9d46ea379f12af3f189d3e9fc9460cab57bc9ef12c50'
    assert baseline.RUNTIME_CODES == {'cpython-3.12.3': stream.RUNTIME_CODE_CONTRACTS['cpython-3.12.3'],
        'cpython-3.12.8': baseline.STREAM_CODE_3128}
    assert abi.read_catalog()['runtimes']['cpython-3.12.8']['contracts']['stream_buffer'] == baseline.STREAM_CODE_3128
    assert 'cpython-3.12.8' not in stream.RUNTIME_CODE_CONTRACTS  # original /12 policy stays fixed
    assert catalog.stream_observation(baseline.PROFILE)
    assert profiles.source_hashes(baseline.PROFILE) == {**profiles.source_hashes(baseline.BASE_PROFILE),
        **baseline.SOURCE_ROWS}
    assert resolve_executor('stream_buffer', 'kubernetes', engine='vllm', version='0.7.0') is None


def test_baseline_stream_prepares_exact_private_extension_and_refuses_tampering(tmp_path, monkeypatch):
    prepared = lifecycle.prepare_assets(tmp_path / 'assets', REPO, profile=baseline.PROFILE)
    assert lifecycle.load_prepared(tmp_path / 'assets/manifest.json') == prepared
    assert set(prepared['assets']) == catalog.asset_names(baseline.PROFILE)
    assert prepared['source_sha256'] == profiles.source_hashes(baseline.PROFILE)
    assert (tmp_path / 'assets/guard_stream_buffer_extension.py').read_bytes() == Path(stream.__file__).read_bytes()
    assert (tmp_path / 'assets/guard_lifecycle_sensor.py').read_bytes() == catalog.render_source(
        baseline.PROFILE, request_key_sha256=prepared['assets']['request-binding-key.json']['sha256'])
    module = types.ModuleType('_baseline_stream_prepared_fixture')
    module.__file__ = str(tmp_path / 'assets/guard_lifecycle_sensor.py')
    exec(compile(Path(module.__file__).read_bytes(), module.__file__, 'exec',
                 dont_inherit=True, optimize=0), module.__dict__)
    monkeypatch.setattr(module, '_dependency', lambda: Path(base8.__file__).read_bytes())
    monkeypatch.setattr(module, '_binding_key', lambda spec: (prepared['session'], SECRET))
    def exact_extension():
        raw = (tmp_path / 'assets/guard_stream_buffer_extension.py').read_bytes()
        if hashlib.sha256(raw).hexdigest() != baseline.EXTENSION_SHA256:
            raise ValueError('changed stream extension')
        return raw
    monkeypatch.setattr(module, '_stream_dependency', exact_extension)
    composed = module._private_base()
    assert composed.PROFILE == baseline.PROFILE and composed.SCHEMA == baseline.SCHEMA
    assert composed.SOURCE_SHA == prepared['source_sha256']
    header = 'byul-lif04-' + 'd' * 32 + '-normal'
    assert lifecycle.request_header_fingerprint(tmp_path / 'assets/manifest.json', prepared, header) != hashlib.sha256(
        header.encode()).hexdigest()
    changed = tmp_path / 'assets/guard_stream_buffer_extension.py'
    changed.write_bytes(changed.read_bytes() + b'# changed')
    with pytest.raises(ValueError):
        lifecycle.load_prepared(tmp_path / 'assets/manifest.json')
    with pytest.raises(ValueError):
        module._private_base()
    key = tmp_path / 'assets/request-binding-key.json'
    key.chmod(0o644)
    with pytest.raises(ValueError):
        lifecycle.request_header_fingerprint(tmp_path / 'assets/manifest.json', prepared, header)


def test_header_uses_private_hmac_only_for_exact_registered_stream_requests():
    raw = b'byul-lif04-' + b'a' * 32 + b'-slow'
    scope = {'type': 'http', 'path': '/v1/completions', 'headers': [(b'x-request-id', raw)]}
    digest = baseline._stream_header(scope, SECRET)
    assert digest == hmac.new(SECRET, b'guard-native-request-binding/1\0asgi-header\0' + raw,
                               hashlib.sha256).hexdigest()
    assert digest != hashlib.sha256(raw).hexdigest()
    assert baseline._stream_header({**scope, 'headers': scope['headers'] * 2}, SECRET) is None
    assert baseline._stream_header({**scope, 'path': '/start_profile'}, SECRET) is None
    assert baseline._stream_header({**scope, 'headers': []}, SECRET) is None


def test_composition_requires_key_before_extension_and_checks_original_code_contract():
    source = Path(baseline.__file__).read_bytes()
    spec = {'profile': baseline.PROFILE, 'schema': baseline.SCHEMA,
        'request_key_sha256': 'a' * 64, 'commit': '5204ff5c3feeb96e8a6eea65dfcb78395f90d4d8',
        'sources': {module: {'sha256': digest} for module, digest in baseline.SOURCE_ROWS.items()}}
    def composition(reader):
        namespace = {'__file__': str(baseline.__file__), 'SPEC': spec,
            '_configure': lambda value: value, '_binding_key': reader}
        exec(compile(source, str(baseline.__file__), 'exec', dont_inherit=True, optimize=0), namespace)
        return namespace
    denied = composition(lambda spec: (_ for _ in ()).throw(ValueError('invalid private key')))
    denied['_stream_dependency'] = lambda: (_ for _ in ()).throw(AssertionError('read before key'))
    fresh = lambda: SimpleNamespace(SOURCE_SHA={}, Observer=type('Observer', (), {}), code_digest=base8.code_digest)
    with pytest.raises(ValueError, match='invalid private key'):
        denied['_configure'](fresh())
    allowed = composition(lambda spec: ('a' * 32, SECRET))
    allowed['_stream_dependency'] = lambda: Path(stream.__file__).read_bytes()
    composed = allowed['_configure'](fresh())
    assert composed.PROFILE == baseline.PROFILE and composed.SCHEMA == baseline.SCHEMA
    assert composed.SOURCE_SHA == baseline.SOURCE_ROWS
    scope = {'type': 'http', 'path': '/v1/completions', 'headers': [
        (b'x-request-id', b'byul-lif04-' + b'a' * 32 + b'-normal')]}
    digest = composed.Observer.stream_wrap.__globals__['request_hash'](scope)
    assert digest == baseline._stream_header(scope, SECRET)
    assert digest != hashlib.sha256(scope['headers'][0][1]).hexdigest()


def test_exact_h11_producer_reaches_original_stream_projection_without_raw_id(tmp_path, monkeypatch):
    prepared, world = composed_world(tmp_path, monkeypatch)
    raw = 'byul-lif04-' + 'a' * 32 + '-slow'
    rows = asyncio.run(original.invoke(world, raw))
    samples = [row['payload'] for row in rows if row['event'] == 'STREAM_BUFFER']
    assert {'REQUEST_ENTER', 'SEND_ENTER', 'SEND_RETURN', 'DRAIN_ENTER', 'DRAIN_RETURN',
            'RUN_RETURN', 'CONNECTION_LOST_RETURN'} <= {row['phase'] for row in samples}
    digest = baseline._stream_header({'type': 'http', 'path': '/v1/completions',
        'headers': [(b'x-request-id', raw.encode())]}, world.key)
    assert all(metadata.validate_sample(row, sources=baseline.SOURCE_ROWS,
        runtime_codes=baseline.RUNTIME_CODES) is None
        for row in samples)
    assert all(digest in row['associated_requests'] for row in samples)
    assert all(row['request_sha256'] in (digest, None) for row in samples)
    assert all(row['asgi_context_live'] and row['asgi_invocation_sha256'] for row in samples
        if row['phase'].startswith(('SEND', 'DRAIN')))
    assert hashlib.sha256(raw.encode()).hexdigest() not in json.dumps(rows)
    records = [{'event': 'START', 'payload': {'context': CONTEXT}, 'seq': 0, 'monotonic_ns': 0}]
    records.extend({**row, 'seq': number + 1} for number, row in enumerate(rows))
    packet = {'recordings': [{'capture': {'records': records, 'process_context_verified': True,
        'image_digest_verified': False}, 'session_owner_match': True, 'listed_file_identity_match': True}]}
    trials = [{'case_id': 'lif04-slow', 'attempted': True, 'request_id_sha256': digest}]
    limits = {'buffer_release_deadline_ms': 100, 'buffer_max_write_bytes': 1000,
        'buffer_max_process_rss_bytes': 10**10, 'buffer_max_send_wait_ms': 1000}
    projected = metadata.project(packet, trials, limits, sources=baseline.SOURCE_ROWS,
        runtime_codes=baseline.RUNTIME_CODES,
        require_live_context=True)
    assert projected['samples'] and projected['cases'][0]['unique_connection_observed'] is True
    assert 'live_asgi_request_context_unavailable' not in projected['cases'][0]['missing']
    assert projected['cases'][0]['producer_authenticated'] is False
    assert projected['cases'][0]['release_verified'] is False
    assert projected['cases'][0]['sampled_write_buffer_peak_bytes'] is None  # EXAMPLE transport
    stale = copy.deepcopy(packet)
    for row in stale['recordings'][0]['capture']['records']:
        if row['event'] == 'STREAM_BUFFER' and row['payload']['request_sha256'] is not None:
            row['payload']['asgi_context_live'] = False
            row['payload']['asgi_invocation_sha256'] = None
    stale_projection = metadata.project(stale, trials, limits, sources=baseline.SOURCE_ROWS,
        runtime_codes=baseline.RUNTIME_CODES,
        require_live_context=True)
    assert 'live_asgi_request_context_unavailable' in stale_projection['cases'][0]['missing']
    assert stale_projection['cases'][0]['unique_connection_observed'] is False
    assert stale_projection['cases'][0]['sampled_write_buffer_peak_bytes'] is None
    assert stale_projection['cases'][0]['drain_intervals_ns'] == []


def test_exact_stream_rows_parse_under_prepared_baseline_profile(case, monkeypatch):
    prepared, world = composed_world(case.root, monkeypatch)
    asyncio.run(original.invoke(world, 'byul-lif04-' + 'c' * 32 + '-normal'))
    produced = [row for row in world.observer.recorded if row['event'] in {'MODULE', 'STREAM_BUFFER', 'ASGI'}]
    def insert(rows):
        start, end = rows[0], rows[-1]
        start['payload'].update(profile=prepared['profile'], schema=baseline.SCHEMA,
            commit=profiles.public_commit(prepared['profile']), session=prepared['session'],
            sensor_sha256=prepared['assets']['guard_lifecycle_sensor.py']['sha256'],
            settings_sha256=prepared['assets']['settings.json']['sha256'])
        start['payload']['context']['pid'] = os.getpid()
        end['monotonic_ns'] = produced[-1]['monotonic_ns'] + 1
        rows[:] = [start, *produced, end]
    rows, summary = parse_recording(revised(case.raw, insert), prepared)
    assert summary['recording_closed'] is True and summary['stream_event_count'] > 0
    assert summary['stream_observer_code_sha256'] in baseline.RUNTIME_CODES.values()
    assert summary['fresh_request_binding']['question_scope_complete'] is False
    assert all(row['payload']['request_sha256'] != hashlib.sha256(
        b'byul-lif04-' + b'c' * 32 + b'-normal').hexdigest() for row in rows if row['event'] == 'STREAM_BUFFER')
    for foreign in ('cpython-3.12.14', 'cpython-3.12.15'):
        changed = copy.deepcopy(rows)
        sample = next(row['payload'] for row in changed if row['event'] == 'STREAM_BUFFER')
        sample['observer_runtime'] = foreign
        sample['observer_code_sha256'] = stream.RUNTIME_CODE_CONTRACTS[foreign]
        with pytest.raises(ValueError, match='STREAM_BUFFER_SAMPLE_INVALID'):
            metadata.validate_sample(sample, sources=baseline.SOURCE_ROWS, runtime_codes=baseline.RUNTIME_CODES)
        def replace(target):
            target[:] = changed
        with pytest.raises(ValueError):
            parse_recording(revised(case.raw, replace), prepared)


def test_exact_httptools_send_and_close_produce_keyed_rows(tmp_path, monkeypatch):
    _, world = composed_world(tmp_path, monkeypatch)
    module = world.modules['uvicorn.protocols.http.httptools_impl']
    module.STATUS_LINE = {200: b'HTTP/1.1 200 OK\r\n'}
    transport = original.Transport()
    flow = world.modules['uvicorn.protocols.http.flow_control'].FlowControl(transport)
    raw = b'byul-lif04-' + b'b' * 32 + b'-normal'
    scope = {'type': 'http', 'path': '/v1/completions', 'method': 'POST',
        'headers': [(b'x-request-id', raw)], 'http_version': '1.1'}
    cycle = module.RequestResponseCycle(scope, transport, flow, logging.getLogger('owned-example'),
        logging.getLogger('owned-example'), False, [], asyncio.Event(), False, True, lambda: None)
    async def app(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'EXAMPLE-body', 'more_body': False})
    async def run():
        await cycle.run_asgi(world.base.Middleware(app))
        protocol = object.__new__(module.HttpToolsProtocol)
        protocol.__dict__.update(connections=set(), logger=logging.getLogger('owned-example'),
            cycle=cycle, flow=flow, transport=transport, timeout_keep_alive_task=None,
            client=('127.0.0.1', 1))
        protocol.connection_lost(None)
    asyncio.run(run())
    samples = [row['payload'] for row in world.observer.recorded if row['event'] == 'STREAM_BUFFER'
        and row['source_module'] == 'uvicorn.protocols.http.httptools_impl']
    assert {'SEND_ENTER', 'SEND_RETURN', 'RUN_RETURN', 'CONNECTION_LOST_RETURN'} <= {
        row['phase'] for row in samples}
    digest = baseline._stream_header(scope, world.key)
    assert all(metadata.validate_sample(row, sources=baseline.SOURCE_ROWS,
        runtime_codes=baseline.RUNTIME_CODES) is None
        for row in samples)
    assert all(row['request_sha256'] in (digest, None) and digest in row['associated_requests'] for row in samples)
    assert all(row['asgi_context_live'] and row['asgi_invocation_sha256'] for row in samples
        if row['phase'].startswith('SEND'))
    assert hashlib.sha256(raw).hexdigest() not in json.dumps(samples)


def test_changed_wheel_source_is_not_installed_or_projected(monkeypatch, tmp_path):
    monkeypatch.setattr(original, 'REFERENCES', FIXTURES)
    monkeypatch.setattr(stream, 'SOURCE_ADDITIONS', dict(baseline.SOURCE_ROWS))
    monkeypatch.setattr(stream, 'RUNTIME_CODE_CONTRACTS', {
        **stream.RUNTIME_CODE_CONTRACTS, 'cpython-3.12.8': baseline.STREAM_CODE_3128})
    world = original.make_observer()
    module = original.module_from_reference('uvicorn.protocols.http.h11_impl')
    original_run = module.RequestResponseCycle.run_asgi
    changed = tmp_path / 'h11_impl.py'
    changed.write_bytes((FIXTURES / 'h11_impl.py').read_bytes() + b'\n# altered\n')
    module.__file__ = str(changed)
    with pytest.raises(ValueError):
        world.observer.instrument(module)
    assert module.RequestResponseCycle.run_asgi is original_run
