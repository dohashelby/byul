"""M-A6-V6: /26은 /14 관측기를 그대로 두고 0.30.0 정확 행만 고른다. 판본을 섞지 않는다."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import types

import pytest

from guard.adapters import vllm_initialization_metadata as metadata
from guard.adapters import vllm_initialization_release_sensor as init_release
from guard.adapters import vllm_initialization_sensor as init_sensor
from guard.adapters import vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_lifecycle_release_sensor as release
from guard.adapters import vllm_lifecycle_sensor as legacy
from guard.adapters.vllm_observer_pin_rows import ROWS as PIN_ROWS
from guard.collectors import lifecycle
from tests import test_a1_initialization_sensor as init_world
from tests.test_lifecycle_capture import external  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / 'fixtures/vllm-mp-030'


def private(module, name):
    copy = types.ModuleType(name)
    copy.__file__ = module.__file__
    exec(compile(Path(module.__file__).read_bytes(), module.__file__, 'exec', dont_inherit=True, optimize=0), copy.__dict__)
    return copy


def test_release_row_is_the_image_verified_multiproc_row_and_assets_are_exact():
    upstream = json.loads((FIXTURE / 'UPSTREAM.json').read_text())
    raw = (FIXTURE / 'multiproc_executor.py').read_bytes()
    row = init_release.SOURCE_ADDITIONS[init_release.MP]
    assert hashlib.sha256(raw).hexdigest() == upstream['files']['multiproc_executor.py']['sha256'] == row
    assert PIN_ROWS[init_release.MP]['sha256'] == row and PIN_ROWS[init_release.MP]['pin_status'] == 'image_pin'
    assert row != init_sensor.SOURCE_ADDITIONS[init_sensor.MP]
    for name, module in (('guard_lifecycle_base.py', legacy), ('guard_lifecycle_release.py', release),
                         ('guard_initialization.py', init_sensor)):
        assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() == init_release.ASSETS[name]
    assert profiles.source_hashes(init_release.PROFILE) == {**release.source_rows(legacy.SOURCE_SHA), init_release.MP: row}


def test_observed_functions_are_source_identical_to_the_reviewed_previous_release():
    def bodies(raw):
        found = {}
        def visit(code):
            found[code.co_qualname] = init_sensor.relative_code(code).replace(co_firstlineno=1, co_linetable=b'')
            for value in code.co_consts:
                if isinstance(value, types.CodeType):
                    visit(value)
        visit(compile(raw, 'multiproc_executor.py', 'exec', dont_inherit=True, optimize=0))
        return found
    old = bodies((Path(__file__).parent / 'fixtures/vllm-mp-031/multiproc_executor.py').read_bytes())
    new = bodies((FIXTURE / 'multiproc_executor.py').read_bytes())
    for point in init_sensor.POINTS:
        assert old[point] == new[point], point


def test_code_contract_does_not_depend_on_the_selected_source_row():
    base = private(legacy, '_contract_base')
    observer = private(init_sensor, '_contract_initialization')
    before = observer.code_contract(base)
    observer.SOURCE_ADDITIONS = dict(init_release.SOURCE_ADDITIONS)
    assert observer.code_contract(base) == before


def test_composition_keeps_installed_modules_unchanged():
    base, rows, observer = private(legacy, '_c_base'), private(release, '_c_release'), private(init_sensor, '_c_init')
    init_release.configure(base, rows, observer)  # /14 runtime code contract applies unchanged
    assert base.PROFILE == init_release.PROFILE and base.SCHEMA == init_release.SCHEMA and base.COMMIT == release.COMMIT
    assert base.SOURCE_SHA[init_release.MP] == init_release.SOURCE_ADDITIONS[init_release.MP]
    assert base.SOURCE_SHA['vllm.v1.request'] == release.RELEASE_ROWS['vllm.v1.request']
    assert init_sensor.SOURCE_ADDITIONS[init_sensor.MP] != init_release.SOURCE_ADDITIONS[init_release.MP]
    assert legacy.PROFILE == 'vllm-native-lifecycle-metadata/8'


def test_prepared_assets_round_trip(external):  # noqa: F811
    prepared = lifecycle.prepare_assets(external / 'assets', REPO, profile=init_release.PROFILE)
    assert lifecycle.load_prepared(external / 'assets/manifest.json') == prepared
    assert set(prepared['assets']) == {'guard_lifecycle_sensor.py', 'sitecustomize.py', 'settings.json', *init_release.ASSETS}
    for name, digest in init_release.ASSETS.items():
        assert prepared['assets'][name]['sha256'] == digest


def test_sample_source_row_is_profile_specific():
    world = init_world.make_world()
    init_world.invoke(world)
    sample = deepcopy(next(r['payload'] for r in world.observer.recorded
                           if r['event'] == 'INITIALIZATION' and r['payload']['phase'] == 'RETURN'))
    metadata.validate_sample(sample)
    with pytest.raises(ValueError):
        metadata.validate_sample(sample, source_sha256=init_release.SOURCE_ADDITIONS[init_release.MP])
    sample['source_sha256'] = init_release.SOURCE_ADDITIONS[init_release.MP]
    metadata.validate_sample(sample, source_sha256=init_release.SOURCE_ADDITIONS[init_release.MP])
    with pytest.raises(ValueError):
        metadata.validate_sample(sample)
