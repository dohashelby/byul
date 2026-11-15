"""Exact baseline V1 snapshots; source fixtures and synthetic native state only."""
import ast
from copy import deepcopy
import hashlib
import importlib.machinery
import json
from pathlib import Path
import threading
import types
from types import SimpleNamespace

import pytest

from guard.adapters import engine_profiles
from guard.adapters import vllm_lifecycle_catalog as catalog
from guard.adapters import vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_lifecycle_sensor as legacy
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture
from guard.executors.registry import resolve_executor
from tests.builders import environment
from tests.test_lifecycle_capture import external  # noqa: F401
from tests.test_lifecycle_sensor import CONTEXT, CANARY, FakeOS, SilentThread

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
PROFILE = 'vllm-native-lifecycle-metadata/27'
FIXTURE = ROOT / 'tests/fixtures/vllm-0.7.0'


def asset(profile=PROFILE):
    module = types.ModuleType('_configured_lifecycle_fixture')
    module.__file__ = '/guard-lifecycle/guard_lifecycle_sensor.py'
    exec(compile(catalog.render_source(profile), module.__file__, 'exec', dont_inherit=True), module.__dict__)
    module._dependency = lambda: Path(legacy.__file__).read_bytes()
    return module


@pytest.fixture
def world(external):  # noqa: F811
    prepared = lifecycle.prepare_assets(external / 'assets', REPO, profile=PROFILE)
    assert lifecycle.load_prepared(external / 'assets/manifest.json') == prepared
    module = asset()
    module.__file__ = str(external / 'assets/guard_lifecycle_sensor.py')
    base = module._private_base()  # Exact compiled source + interpreter gate, not a fabricated module.
    fake = FakeOS()
    base.os = fake
    base._context = lambda: dict(CONTEXT)
    base._open_directory = lambda p: 10
    base.threading = SimpleNamespace(RLock=threading.RLock, Thread=SilentThread, Event=threading.Event)
    settings = json.loads((external / 'assets/settings.json').read_bytes())
    observer = base.Observer(settings, prepared['assets']['settings.json']['sha256'])
    # EXAMPLE provenance rows for synthetic native calls, never a runtime
    # qualification or producer registration.
    for name, digest in base.SOURCE_SHA.items():
        observer.emit('MODULE', {'module': name, 'verified': True, 'source_sha256': digest,
                                 'code_sha256': 'c' * 64})
    return SimpleNamespace(prepared=prepared, module=module, base=base, fake=fake, observer=observer, root=external)


def native_class(module, cls, methods, **namespace):
    """Execute only reviewed method ASTs, with test-owned collaborators; never import vLLM."""
    path = FIXTURE / (module.replace('.', '/') + '.py.txt')
    tree = ast.parse(path.read_bytes())
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == cls)
    nodes = [node for node in original.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in methods]
    assert {node.name for node in nodes} == set(methods)
    source = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                             ast.ClassDef(name=cls, bases=[], keywords=[], body=nodes, decorator_list=[])], type_ignores=[])
    exec(compile(ast.fix_missing_locations(source), str(path), 'exec', dont_inherit=True), namespace)
    return namespace[cls]


def test_source_rows_and_actual_functions_match_the_pinned_tag_and_installation_review():
    row = catalog.row(PROFILE)
    provenance = json.loads((FIXTURE / 'PROVENANCE.json').read_bytes())
    installation = json.loads((ROOT / row['installation_review']).read_bytes())
    assert row['commit'] == provenance['tag_commit']
    assert installation['sides']['base']['git_blob_mismatch_count'] == 0
    assert row['images'][0].endswith(installation['sides']['base']['image_manifest_sha256'])
    assert len(row['sources']) == 8 and len(profiles.point_sources(PROFILE)) == 14
    for module, source in row['sources'].items():
        name = module.replace('.', '/') + '.py.txt'
        raw = (FIXTURE / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source['sha256'] == provenance['files'][name]['sha256']
        assert len(raw) == source['bytes']
        for cls, methods in source['classes'].items():
            tree = ast.parse(raw)
            target = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
            available = {n.name for n in target.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert set(methods) <= available
    # Similar names never supply absent source paths, functions or observation meaning.
    points = profiles.point_sources(PROFILE)
    assert points['Scheduler._free_request'] == 'vllm.v1.core.scheduler'
    assert points['Processor.process_inputs'] == 'vllm.v1.engine.processor'
    assert not {'InputProcessor.assign_request_id', 'Scheduler._free_request_blocks',
                'GPUModelRunner.sample_tokens', 'GPUModelRunner._model_forward'} & points.keys()


def test_prepared_bytes_bind_only_the_selected_row_and_preserve_immutable_sensors(world):
    assert world.base.PROFILE == PROFILE
    assert world.base.SOURCE_SHA == profiles.source_hashes(PROFILE)
    assert world.base.POINTS == profiles.points(PROFILE)
    assert world.base.FUNCTION_POINTS == world.base.HANDLER_POINTS == {}
    assert set(world.prepared['assets']) == {'guard_lifecycle_sensor.py', 'guard_lifecycle_base.py',
                                             'sitecustomize.py', 'settings.json'}
    assert world.prepared['assets']['guard_lifecycle_base.py']['sha256'] == catalog.runtime_spec(PROFILE)['base_sha256']
    assert not world.prepared['engine_started'] and not world.prepared['image_contents_verified']
    assert not world.prepared['product_connected']
    assert legacy.PROFILE == 'vllm-native-lifecycle-metadata/8'
    assert legacy.SOURCE_SHA != world.base.SOURCE_SHA and legacy.FUNCTION_POINTS


@pytest.mark.parametrize('damage', [None, 'mode', 'image', 'version'])
def test_session_binding_checks_selected_catalog_image_and_declared_mode(world, damage):
    row = catalog.row(PROFILE)
    image = row['images'][0] if damage != 'image' else 'example.invalid/engine@sha256:' + 'd' * 64
    version = row['version'] if damage != 'version' else '0.30.0'
    env = environment(engine={'name': row['engine'], 'version': version, 'image_digest': image},
                      runtime={'platform': 'engine_requested=V0' if damage == 'mode' else 'engine_requested=V1'})
    target = {'run_id': 'EXAMPLE-configured', 'environment_ref': env.ref().model_dump(mode='json'),
              'engine': {'container': 'EXAMPLE-engine'}}
    source = {'container': 'EXAMPLE-engine', 'image': image,
              'path': '/guard-lifecycle/data/' + world.prepared['session'] + '/pid-42-99.jsonl'}
    if damage:
        with pytest.raises(ValueError, match='LIFECYCLE_CONFIGURED_ENGINE_UNSUPPORTED'):
            lifecycle.validate_binding(env, target, source, world.prepared)
    else:
        assert lifecycle.validate_binding(env, target, source, world.prepared) is None


@pytest.mark.parametrize('damage', [None, 'bytes', 'symlink'])
def test_standalone_dependency_reader_requires_exact_regular_base(tmp_path, damage):
    path = tmp_path / 'guard_lifecycle_base.py'
    if damage == 'symlink':
        path.symlink_to(legacy.__file__)
    else:
        path.write_bytes(Path(legacy.__file__).read_bytes() + (b'\n' if damage else b''))
    native_open = catalog.template.os.open

    def opened(path, flags, *args, **kwargs):
        return native_open(str(tmp_path) if path == '/guard-lifecycle' else path, flags, *args, **kwargs)

    namespace = types.ModuleType('_configured_dependency_fixture')
    namespace.__file__ = str(tmp_path / 'sensor.py')
    exec(compile(catalog.render_source(PROFILE), str(tmp_path / 'sensor.py'), 'exec', dont_inherit=True), namespace.__dict__)
    namespace.os = SimpleNamespace(**(vars(namespace.os) | {'open': opened}))
    if damage:
        with pytest.raises((ValueError, OSError)):
            namespace._private_base()
    else:
        assert namespace._private_base().PROFILE == PROFILE


def test_data_only_future_row_uses_the_same_sensor_without_mutating_an_existing_asset(monkeypatch):
    original = catalog.render_source(PROFILE)
    future = deepcopy(catalog.row(PROFILE))
    future.update(profile='EXAMPLE-future/1', schema='EXAMPLE-events/1', engine='example', version='future',
                  images=['example.invalid@sha256:' + 'd' * 64], engine_mode='EXAMPLE')
    future['sources'] = {'example.scheduler': future['sources']['vllm.v1.core.scheduler']}
    future['void_return_argument_ids'] = ['Scheduler.finish_requests']
    monkeypatch.setitem(catalog.ROWS, future['profile'], future)
    base = asset(future['profile'])._private_base()
    assert base.PROFILE == future['profile'] and set(base.SOURCE_SHA) == {'example.scheduler'}
    assert profiles.point_sources(future['profile'])['Scheduler.finish_requests'] == 'example.scheduler'
    assert catalog.render_source(PROFILE) == original
    assert catalog.matches(future['profile'], 'example', 'future', future['images'][0], 'engine_requested=EXAMPLE')


@pytest.mark.parametrize('point', ['OutputProcessor.abort_requests', 'Scheduler.finish_requests'])
def test_void_native_returns_keep_input_ids_and_observe_actual_post_call_state(world, point):
    if point.startswith('OutputProcessor'):
        cls = native_class('vllm.v1.engine.output_processor', 'OutputProcessor', ['abort_requests'])
        owner = cls.__new__(cls)
        owner.request_states = {CANARY: object()}
        arguments = ([CANARY],)
        field = 'output_state_present'
    else:
        status = SimpleNamespace(is_finished=lambda v: True, RUNNING=0)
        cls = native_class('vllm.v1.core.scheduler', 'Scheduler',
                           ['finish_requests', '_free_request'], RequestStatus=status)
        owner = cls.__new__(cls)
        request = SimpleNamespace(request_id=CANARY, status=0, is_finished=lambda: True)
        owner.requests = {CANARY: request}
        owner.running, owner.waiting = [request], []
        owner.kv_cache_manager = SimpleNamespace(free=lambda request: None)
        owner.running_reqs_data, owner.finished_req_ids = {}, set()
        arguments = ([CANARY], 2)
        field = 'request_present'
    wrapped = world.observer.wrap(profiles.point_sources(PROFILE)[point], point, getattr(cls, point.split('.')[1]))
    assert wrapped(owner, *arguments) is None
    records, summary = recording(world)
    native = [r['payload'] for r in records if r['event'] == 'NATIVE' and r['payload']['point'] == point]
    assert [r['phase'] for r in native] == ['CALL', 'RETURN']
    assert [r['requests'][0][field] for r in native] == [True, False]
    assert all(r['requests'][0]['request_sha256'] == world.base.id_sha(CANARY) for r in native)
    assert summary['source_profile_modules']


def recording(world):
    writer = world.observer._writer()
    writer.close('SEALED')
    raw = bytes(world.fake.data)
    assert CANARY.encode() not in raw
    return capture.parse_recording(raw, world.prepared)


def test_void_return_observation_does_not_consume_a_native_iterator(world):
    cls = native_class('vllm.v1.engine.output_processor', 'OutputProcessor', ['abort_requests'])
    owner = cls.__new__(cls)
    owner.request_states = {CANARY: object()}
    consumed = []

    def values():
        consumed.append('native')
        yield CANARY

    wrapped = world.observer.wrap('vllm.v1.engine.output_processor', 'OutputProcessor.abort_requests', cls.abort_requests)
    assert wrapped(owner, values()) is None
    assert consumed == ['native'] and owner.request_states == {}
    rows, _ = recording(world)
    assert all(r['payload']['requests'] == [] for r in rows if r['event'] == 'NATIVE')


@pytest.mark.parametrize('failure', [RuntimeError, KeyboardInterrupt])
def test_void_return_adaptation_preserves_the_identical_native_exception(world, failure):
    error = failure(CANARY)

    def native(owner, request_ids):
        raise error

    wrapped = world.observer.wrap('vllm.v1.engine.output_processor', 'OutputProcessor.abort_requests', native)
    with pytest.raises(failure) as caught:
        wrapped(SimpleNamespace(request_states={CANARY: object()}), [CANARY])
    assert caught.value is error and world.base._CHAIN.get() == ()
    rows, _ = recording(world)
    assert [r['payload']['phase'] for r in rows if r['event'] == 'NATIVE'] == ['CALL', 'RAISE']


def test_processor_input_snapshot_cannot_claim_a_fresh_asgi_assignment(world):
    # A long-lived task can inherit context. A matching string or return ID
    # alone is not a live request-selection callback.
    token = world.base._ASGI_REQUEST.set((42, 'a' * 64))

    class Private:
        @property
        def prompt(self):
            raise AssertionError('prompt must remain unread')

        @property
        def request_id(self):
            raise AssertionError('property must remain unread')

    try:
        for returned in (False, True):
            value = world.observer.snapshot('Processor.process_inputs',
                (object(), CANARY, Private(), Private()), {}, Private(), returned)
            assert value['requests'][0]['request_sha256'] == world.base.id_sha(CANARY)
            assert value['metadata'] == {}
    finally:
        world.base._ASGI_REQUEST.reset(token)


@pytest.mark.parametrize('kind', ['source', 'code'])
def test_wrong_release_source_or_loader_code_leaves_native_behavior_unwrapped(world, tmp_path, kind):
    name = 'vllm.v1.engine.processor'
    source = tmp_path / 'processor.py'
    source.write_text('VALUE = 7\n')
    loader = importlib.machinery.SourceFileLoader(name, str(source))
    if kind == 'code':
        world.base.SOURCE_SHA[name] = hashlib.sha256(source.read_bytes()).hexdigest()
        loader.get_code = lambda name: compile('VALUE = 9\n', str(source), 'exec', dont_inherit=True)
    emitted = []
    observer = SimpleNamespace(emit=lambda e, p: emitted.append((e, p)), modules={},
                               instrument=lambda module: pytest.fail('unqualified native hooks'))
    module = types.ModuleType(name)
    world.base.CheckedLoader(name, loader, observer).exec_module(module)
    assert module.VALUE == (9 if kind == 'code' else 7)
    assert emitted == [('MODULE', {'module': name, 'verified': False})]


@pytest.mark.parametrize('kind', ['source', 'base'])
def test_self_rehashed_prepared_assets_cannot_replace_the_reviewed_composition(world, kind):
    name = 'guard_lifecycle_sensor.py' if kind == 'source' else 'guard_lifecycle_base.py'
    path = world.root / 'assets' / name
    raw = path.read_bytes() + b'\n# EXAMPLE modified asset\n'
    path.write_bytes(raw)
    manifest_path = world.root / 'assets/manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['assets'][name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        lifecycle.load_prepared(manifest_path)


@pytest.mark.parametrize('digest', ['0' * 64, legacy.SOURCE_SHA['vllm.v1.worker.gpu_input_batch']])
def test_recording_cannot_substitute_a_same_named_module_from_another_release(world, digest):
    world.observer.emit('MODULE', {'module': 'vllm.v1.worker.gpu_input_batch', 'verified': True,
                                 'source_sha256': digest, 'code_sha256': 'c' * 64})
    with pytest.raises(ValueError, match='LIFECYCLE_RECORDING_MODULE_HASH_INVALID'):
        recording(world)


@pytest.mark.parametrize('kind', ['runtime', 'os', 'fingerprint', 'optimize'])
def test_unqualified_interpreter_cannot_install_hooks(monkeypatch, kind):
    module = asset()
    spec = deepcopy(module.SPEC)
    monkeypatch.setattr(module, 'SPEC', spec)
    if kind in {'runtime', 'os'}:
        spec['runtime_code'] = {} if kind == 'runtime' else {key: {'EXAMPLE_OS': value['posix']}
                                                          for key, value in spec['runtime_code'].items()}
    elif kind == 'fingerprint':
        spec['runtime_code'] = {key: {'posix': '0' * 64} for key in spec['runtime_code']}
    else:
        monkeypatch.setattr(module, 'sys', SimpleNamespace(implementation=module.sys.implementation,
                            version_info=module.sys.version_info, flags=SimpleNamespace(optimize=1)))
    with pytest.raises(ValueError, match='CONFIGURED_LIFECYCLE_(RUNTIME_UNAVAILABLE|CODE_MISMATCH)'):
        module._private_base()
    assert module.install() is False and module._BASE is None


@pytest.mark.parametrize('image,mode', [('wrong', 'engine_requested=V1'), ('correct', 'engine_requested=V0'),
                                     ('correct', ''), ('correct', 'engine_requested=V1; engine_requested=V1')])
def test_release_and_engine_selection_are_not_inferred_from_a_similar_name(image, mode):
    row = catalog.row(PROFILE)
    assert not catalog.matches(PROFILE, row['engine'], row['version'],
                               row['images'][0] if image == 'correct' else 'example:latest', mode)
    assert catalog.matches(PROFILE, row['engine'], row['version'], row['images'][0], 'engine_requested=V1')
    assert not catalog.matches(PROFILE, row['engine'], 'EXAMPLE-future', row['images'][0], 'engine_requested=V1')


def test_snapshot_availability_does_not_open_unreviewed_executors_or_register_gpu_results():
    assert engine_profiles.supports('vllm', '0.7.0', 'configured_lifecycle_rows')
    for executor in ('engine_sensor', 'request_termination', 'observation_state', 'initialization',
                     'model_authorization', 'profiler_observation'):
        assert resolve_executor(executor, 'kubernetes', engine='vllm', version='0.7.0') is None
