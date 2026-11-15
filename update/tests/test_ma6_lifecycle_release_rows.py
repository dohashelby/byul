"""M-A6-V6: /25는 기반 /8을 0.30.0 정확 설치 행으로 설정할 뿐이고, 판본을 섞거나 넓히지 않는다."""
import hashlib
import json
from pathlib import Path
import threading
import types
from types import SimpleNamespace

import pytest

from guard.adapters import engine_profiles
from guard.adapters import vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_lifecycle_release_sensor as release
from guard.adapters import vllm_lifecycle_sensor as legacy
from guard.adapters.vllm_observer_pin_rows import ROWS as PIN_ROWS
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture
from guard.executors.registry import resolve_executor
from tests.test_lifecycle_capture import external  # noqa: F401  (fixture)
from tests.test_lifecycle_sensor import CONTEXT, FakeOS, SilentThread

REPO = Path(__file__).resolve().parents[2]
# 의미 검토에서 같지 않거나 센서가 읽지 않아 이번 판본 행을 두지 않은 모듈.
WITHOUT_ROWS = {'vllm.model_executor.model_loader.default_loader', 'vllm.v1.engine.core_client',
                'vllm.v1.core.kv_cache_coordinator', 'vllm.entrypoints.serve.utils.api_utils',
                'vllm.entrypoints.openai.completion.serving'}


def test_release_rows_replace_only_reviewed_modules_of_the_fixed_base():
    assert hashlib.sha256(Path(legacy.__file__).read_bytes()).hexdigest() == release.BASE_SHA256
    assert set(release.RELEASE_ROWS) < set(legacy.SOURCE_SHA)
    assert not set(release.RELEASE_ROWS) & WITHOUT_ROWS
    for module, digest in release.RELEASE_ROWS.items():
        assert len(digest) == 64 and digest != legacy.SOURCE_SHA[module]
    merged = profiles.source_hashes(release.PROFILE)
    assert merged == {**legacy.SOURCE_SHA, **release.RELEASE_ROWS}
    for module in WITHOUT_ROWS:
        assert merged[module] == legacy.SOURCE_SHA[module]
    # 이미지 대조로 올린 후보 행과 겹치는 모듈은 같은 bytes여야 한다.
    overlap = set(release.RELEASE_ROWS) & set(PIN_ROWS)
    assert overlap and all(PIN_ROWS[m]['sha256'] == release.RELEASE_ROWS[m] for m in overlap)
    assert profiles.points(release.PROFILE) == legacy.POINTS
    assert profiles.point_sources(release.PROFILE) == profiles.point_sources(legacy.PROFILE)
    assert profiles.public_commit(release.PROFILE) == release.COMMIT != legacy.COMMIT
    with pytest.raises(ValueError, match='LIFECYCLE_RELEASE_ROWS_UNAVAILABLE'):
        release.source_rows({k: v for k, v in legacy.SOURCE_SHA.items() if k != 'vllm.v1.request'})


@pytest.fixture
def world(external, monkeypatch):  # noqa: F811
    prepared = lifecycle.prepare_assets(external / 'assets', REPO, profile=release.PROFILE)
    assert lifecycle.load_prepared(external / 'assets/manifest.json') == prepared
    base = types.ModuleType('_release_base_fixture')
    base.__file__ = legacy.__file__
    exec(compile(Path(legacy.__file__).read_bytes(), legacy.__file__, 'exec', dont_inherit=True, optimize=0), base.__dict__)
    fake = FakeOS()
    base.os = fake
    base._context = lambda: dict(CONTEXT)
    base._open_directory = lambda p: 10
    base.threading = SimpleNamespace(RLock=threading.RLock, Thread=SilentThread, Event=threading.Event)
    release.configure(base)
    settings = json.loads((external / 'assets/settings.json').read_text())
    writer = base.Writer(settings, prepared['assets']['settings.json']['sha256'], dict(CONTEXT))
    return SimpleNamespace(prepared=prepared, base=base, writer=writer, fake=fake, root=external)


def test_prepared_release_profile_ships_the_exact_base_and_release_table(world):
    prepared = world.prepared
    assert prepared['profile'] == release.PROFILE
    assert set(prepared['assets']) == {'guard_lifecycle_sensor.py', 'sitecustomize.py', 'settings.json', 'guard_lifecycle_base.py'}
    assert prepared['assets']['guard_lifecycle_base.py']['sha256'] == release.BASE_SHA256
    assert prepared['source_sha256'] == profiles.source_hashes(release.PROFILE)
    assert world.base.PROFILE == release.PROFILE and world.base.SCHEMA == release.SCHEMA
    assert world.base.SOURCE_SHA['vllm.v1.request'] == release.RELEASE_ROWS['vllm.v1.request']
    # 설치된 /8 모듈 자체는 바뀌지 않는다.
    assert legacy.PROFILE == 'vllm-native-lifecycle-metadata/8' and legacy.SOURCE_SHA['vllm.v1.request'] != world.base.SOURCE_SHA['vllm.v1.request']
    damaged = json.loads((world.root / 'assets/manifest.json').read_text())
    damaged['source_sha256'] = dict(legacy.SOURCE_SHA)
    (world.root / 'assets/manifest.json').write_text(json.dumps(damaged, sort_keys=True, indent=2) + '\n')
    with pytest.raises(ValueError):
        lifecycle.load_prepared(world.root / 'assets/manifest.json')


def _recording(world, rows):
    for event, payload in rows:
        world.writer.write(event, payload)
    world.writer.close('SEALED')
    return bytes(world.fake.data)


def test_release_module_rows_are_ready_and_previous_release_bytes_are_rejected(world):
    module = 'vllm.v1.worker.gpu_model_runner'
    raw = _recording(world, [('MODULE', {'module': module, 'verified': True,
                                         'source_sha256': release.RELEASE_ROWS[module], 'code_sha256': 'c' * 64}),
                             ('MODULE', {'module': 'vllm.model_executor.model_loader.default_loader', 'verified': False})])
    rows, summary = capture.parse_recording(raw, world.prepared)
    assert rows[0]['payload']['profile'] == release.PROFILE and summary['source_profile_modules'] == [module]


def test_mixed_release_bytes_cannot_be_admitted(world):
    module = 'vllm.v1.worker.gpu_model_runner'
    raw = _recording(world, [('MODULE', {'module': module, 'verified': True,
                                         'source_sha256': legacy.SOURCE_SHA[module], 'code_sha256': 'c' * 64})])
    with pytest.raises(ValueError, match='LIFECYCLE_RECORDING_MODULE_HASH_INVALID'):
        capture.parse_recording(raw, world.prepared)


def test_checked_loader_uses_only_the_release_row(world, tmp_path):
    """같은 모듈 이름이라도 이번 판본 정확 bytes가 아니면 감싸지 않는다(원 동작 유지)."""
    module = 'vllm.v1.request'
    source = tmp_path / 'request.py'
    source.write_bytes(b'VALUE = 1\n')
    emitted = []
    observer = SimpleNamespace(emit=lambda e, p: emitted.append((e, p)), modules={}, instrument=lambda m: None)

    class Loader:
        def get_filename(self, name): return str(source)
        def get_data(self, path): return Path(path).read_bytes()
        def get_code(self, name): return compile(source.read_bytes(), str(source), 'exec', dont_inherit=True)
        def exec_module(self, target): exec(self.get_code(module), target.__dict__)

    target = types.ModuleType(module)
    world.base.CheckedLoader(module, Loader(), observer).exec_module(target)
    assert target.VALUE == 1 and emitted == [('MODULE', {'module': module, 'verified': False})]
    world.base.SOURCE_SHA = {**world.base.SOURCE_SHA, module: hashlib.sha256(source.read_bytes()).hexdigest()}
    emitted.clear()
    world.base.CheckedLoader(module, Loader(), observer).exec_module(types.ModuleType(module))
    assert emitted[0][1]['verified'] is True and emitted[0][1]['source_sha256'] == world.base.SOURCE_SHA[module]


def test_termination_family_accepts_release_records_like_the_base():
    from guard.executors.kubernetes_request_termination import KubernetesRequestTerminationExecutor as T
    from guard.executors.kubernetes_request_attempts import KubernetesRequestAttemptsExecutor as A
    from guard.executors.kubernetes_observation_state import KubernetesObservationStateExecutor as O
    for executor in (T, A, O):
        assert {legacy.PROFILE, release.PROFILE} <= executor.supported_native_profiles


def test_release_profile_is_admitted_only_for_its_catalogued_release():
    assert engine_profiles.resolve('vllm', '0.30.0', 'lifecycle_release_rows').profile == 'vllm-lifecycle-release-rows/1'
    for version in ('0.7.0', '0.31.0', 'EXAMPLE-future'):
        assert not engine_profiles.supports('vllm', version, 'lifecycle_release_rows')
    for executor in ('model_authorization', 'session_revocation', 'credential_revocation', 'native_route',
                     'initialization'):
        assert resolve_executor(executor, 'kubernetes', engine='vllm', version='0.30.0') is not None
        assert resolve_executor(executor, 'kubernetes', engine='vllm', version='0.7.0') is None
    for executor in ('request_termination', 'observation_state'):
        assert resolve_executor(executor, 'kubernetes', engine='vllm', version='0.30.0') is not None
        assert resolve_executor(executor, 'kubernetes', engine='vllm', version='0.7.0') is not None
    # 행이 없는 모듈에 기대는 엔진 안 관측은 계속 닫혀 있다.
    for executor in ('stream_buffer', 'profiler_observation'):
        assert resolve_executor(executor, 'kubernetes', engine='vllm', version='0.30.0') is None
