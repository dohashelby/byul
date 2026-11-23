"""Linux CPython fixtures for the exact tagged eager read and loader frames."""

from copy import deepcopy
from pathlib import Path
from types import FunctionType, ModuleType, SimpleNamespace
from typing import Iterable
import builtins
import json
import os
import sys
import time

import pytest

from guard.adapters import vllm_approved_file_read_composition as composition
from guard.adapters import vllm_lifecycle_catalog as catalog
from guard.collectors.approved_model_file_reads import analyze, journal
from guard.collectors.lifecycle import load_prepared, prepare_assets
from guard.executors.lifecycle_capture import parse_recording
from guard.executors import run_binding
from guard.models import canonical_json, load_pack, sha256_hex, utcnow
from tests.builders import environment
from tests.test_a1_file_read_sensor import scope_value
from tests.test_a1_rank_failure_sensor import QuietLogger, codes
from tests.test_lifecycle_capture import external


FIXTURES = Path(__file__).parent / 'fixtures'
pytestmark = pytest.mark.skipif(
    sys.platform != 'linux' or os.uname().machine != 'x86_64'
    or sys.version_info[:3] != (3, 12, 3) or sys.flags.optimize != 0,
    reason='the selected GPU observer ABI is Linux x86_64 CPython 3.12.3',
)


def _example_weights():
    header = b'{"weight":{"dtype":"U8","shape":[4],"data_offsets":[0,4]}}'
    header += b' ' * (-len(header) % 8)
    return len(header).to_bytes(8, 'little') + header + b'BYUL'


def _source_fixture(root, release, name):
    relative = name.replace('.', '/') + '.py'
    fixture = (FIXTURES / 'vllm-0.30.0' / (relative + '.txt') if release == '0.30.0'
               else FIXTURES / 'weight-iteration-native' / (Path(relative).name + '.txt'))
    installed = root / relative
    installed.parent.mkdir(parents=True, exist_ok=True)
    raw = fixture.read_bytes()
    installed.write_bytes(raw)
    module = ModuleType(name)
    module.__file__ = str(installed)
    module.logger = QuietLogger()
    full_code = compile(raw, module.__file__, 'exec', dont_inherit=True, optimize=0)
    return module, codes(full_code)


def produce_normal_file_read_fixture(tmp_path, monkeypatch, release, *, model_path, prepared_path,
                                     matched_model=True, observed=True, missing_file=False):
    """Use installed-source-shaped EXAMPLE files with the real writer and parser."""
    monkeypatch.setenv('VLLM_USE_V2_MODEL_RUNNER', '0')
    prepared_path = Path(prepared_path)
    prepared = load_prepared(prepared_path)
    assert prepared['profile'] == composition.PROFILES[release]
    assets = prepared_path.parent
    base_path = assets / 'guard_lifecycle_base.py'
    sensor_path = assets / 'guard_lifecycle_sensor.py'
    base = ModuleType('_approved_file_read_fixture_base')
    base.__file__ = str(base_path)
    base_code = compile(base_path.read_bytes(), base.__file__, 'exec', dont_inherit=True, optimize=0)
    exec(base_code, base.__dict__)
    producer = ModuleType('_approved_file_read_fixture')
    producer.__file__ = str(sensor_path)
    exec(compile(sensor_path.read_bytes(), producer.__file__, 'exec', dont_inherit=True, optimize=0), producer.__dict__)
    assert base.code_digest(producer._relative(base_code)) == producer.SPEC['runtime_code'][composition.RUNTIME]['posix']
    base = producer._approved_file_read_previous(base)
    installed_root = tmp_path / 'installed'
    producer.configure_approved_file_reads(base, producer.SPEC, asset_root=str(assets),
        install_root=str(installed_root), model_root=str(model_path.parent))
    sink = tmp_path / 'recording'
    sink.mkdir(mode=0o700, parents=True)
    config = {'session': prepared['session'], 'output_dir': str(sink), 'control_path': str(sink / 'SEALED')}
    observer = base.Observer(config, prepared['assets']['settings.json']['sha256'])
    weight, weight_codes = _source_fixture(installed_root, release, composition.WEIGHTS)
    loader, loader_codes = _source_fixture(installed_root, release, composition.LOADER)
    original = model_path.read_bytes()
    decoded = []

    def decode(value):
        decoded.append(value)
        assert value == original
        return {'weight': 42}

    weight.__dict__.update(tqdm=lambda values, **kw: values, enable_tqdm=lambda value: False, _BAR_FORMAT='',
        _natural_sort_key=lambda value: value, _get_fs_type=lambda paths: 'ext4',
        _get_checkpoints_size_bytes=lambda paths: len(original), _get_available_ram_bytes=lambda: 1024,
        should_skip_weight=lambda *args: False, load=decode)
    weight.safetensors_weights_iterator = FunctionType(weight_codes[composition.EAGER_POINT], weight.__dict__)
    loader.__dict__.update(time=time, cast=lambda kind, value: value, Iterable=Iterable)
    cls = type('DefaultModelLoader', (), {'__module__': composition.LOADER})
    loader.DefaultModelLoader = cls
    for point in (composition.LOAD_POINT, composition.ITERATOR_POINT):
        setattr(cls, point.split('.')[1], FunctionType(loader_codes[point], loader.__dict__))
    cls.Source = lambda model, revision, **kw: SimpleNamespace(model_or_path=model, revision=revision, **kw)
    cls._init_ep_weight_filter = lambda self, config: None
    cls._init_mm_encoder_only_weight_filter = lambda self, model, config: None
    cls.counter_before_loading_weights = 0.0
    cls.enable_weights_track = False
    for module, compiled in ((weight, weight_codes), (loader, loader_codes)):
        observer.instrument(module)
        identity = {'source_sha256': sha256_hex(Path(module.__file__).read_bytes()),
                    'code_sha256': base.code_digest(compiled['<module>'])}
        observer.modules[module.__name__] = identity
        observer.emit('MODULE', {'module': module.__name__, 'verified': True, **identity})

    def eager_weights():
        return weight.safetensors_weights_iterator([str(model_path)], False, 'eager', None,
            safetensors_prefetch_num_threads=1, safetensors_prefetch_block_size=1)

    instance = cls()
    instance._get_weights_iterator = lambda selected: eager_weights()
    applied = []

    class Model:
        def load_weights(self, weights):
            applied.extend(weights)
            return {name for name, _ in applied}

    model = Model()
    model_config = SimpleNamespace(model=str(model_path.parent) if matched_model else '/different-model',
                                   revision=None, quantization=None)
    original_open = builtins.open
    try:
        if missing_file:
            # The private EXAMPLE file becomes unavailable before the ordinary eager open.
            model_path.unlink()
            with pytest.raises(FileNotFoundError):
                instance.load_weights(model, model_config)
            assert not decoded and not applied
        elif observed:
            assert instance.load_weights(model, model_config) is None
        else:
            applied.extend(eager_weights())
        if not missing_file:
            assert decoded == [original] and applied == [('weight', 42)]
        assert builtins.open is original_open
    finally:
        observer.writer.close('PROCESS_EXIT')
    recording, = sink.glob('pid-*.jsonl')
    raw = recording.read_bytes()
    rows, summary = parse_recording(raw, prepared)
    capture = {'process_context_verified': True, 'summary': summary, 'records': rows,
               'provenance': {'capture_sha256': sha256_hex(raw), 'owner_binding_verified': True}}
    return {'session': prepared['session'], 'recordings': [
        {'capture': capture, 'session_owner_match': True, 'listed_file_identity_match': True}]}, raw


def _prepared(tmp_path, release):
    model_path = tmp_path / 'approved.safetensors'
    model_path.write_bytes(_example_weights())
    scope = scope_value(model_path)
    scope['files'][0].update(reference_sha256=sha256_hex(model_path.read_bytes()), bytes=model_path.stat().st_size)
    scope_path = tmp_path / 'scope.json'
    scope_path.write_bytes(canonical_json(scope))
    asset_root = tmp_path / 'prepared'
    prepared = prepare_assets(asset_root, Path(__file__).parents[2], profile=composition.PROFILES[release],
                              model_read_scope=scope_path)
    return model_path, asset_root / 'manifest.json', prepared


@pytest.mark.parametrize('release', ('0.30.0', '0.31.0'))
def test_pinned_loader_eager_read_reaches_owner_bound_projection(external, tmp_path, monkeypatch, release):
    path, prepared_path, prepared = _prepared(external, release)
    packet, raw = produce_normal_file_read_fixture(tmp_path, monkeypatch, release,
        model_path=path, prepared_path=prepared_path)
    result = analyze(packet, prepared)
    candidate, = result['candidates']
    assert candidate['declared_reference_bytes_match_observed'] and candidate['path_fd_metadata_match_observed']
    assert candidate['read_return_bytes_sha256'] == sha256_hex(path.read_bytes())
    assert candidate['loader_source_sha256'] == catalog.row(prepared['profile'])['sources'][composition.LOADER]['sha256']
    assert candidate['recording_closed'] and candidate['root_event'] == candidate['source_events'][0]
    assert result['scope_complete'] is False and result['producer_authenticated'] is False
    assert str(path).encode() not in raw and b'BYUL' not in raw
    if release == '0.31.0':
        # This method digest was already independently qualified by the /17 ABI.
        assert candidate['source_method_code_sha256'] == '604f0b08bf48e4eda5d2951377d1cf41e23d35d0fcce8d6bfcd362f7d8e9623a'
    mutated = deepcopy(packet['recordings'][0]['capture']['records'])
    selected = [row for row in mutated if row['event'] == composition.EVENT]
    selected[1]['payload']['loader_call_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='APPROVED_FILE_READ_ORDER_INVALID'):
        journal(selected, prepared)


@pytest.mark.parametrize('matched,observed', ((False, True), (True, False)))
def test_eager_native_read_remains_unobserved_without_the_same_active_loader(external, tmp_path, monkeypatch, matched, observed):
    path, prepared_path, prepared = _prepared(external, '0.30.0')
    packet, _ = produce_normal_file_read_fixture(tmp_path, monkeypatch, '0.30.0',
        model_path=path, prepared_path=prepared_path, matched_model=matched, observed=observed)
    assert analyze(packet, prepared)['candidates'] == []


def test_source_preparation_refuses_changed_approved_scope(external):
    _, prepared_path, prepared = _prepared(external, '0.30.0')
    scope_file = prepared_path.parent / 'model-read-scope.json'
    scope = json.loads(scope_file.read_bytes())
    scope['files'][0]['reference_sha256'] = '0' * 64
    changed = canonical_json(scope)
    scope_file.write_bytes(changed)
    prepared['model_read_scope']['files'][0]['reference_sha256'] = '0' * 64
    prepared['assets']['model-read-scope.json'] = {'bytes': len(changed), 'sha256': sha256_hex(changed)}
    prepared_path.write_bytes(canonical_json(prepared))
    with pytest.raises(ValueError, match='LIFECYCLE_ASSET_VERSION_UNAVAILABLE'):
        load_prepared(prepared_path)


def test_normal_run_binding_refuses_another_valid_prepared_session_at_the_committed_path(external):
    _, prepared_path, prepared = _prepared(external, '0.30.0')
    replacement_path = external / 'replacement'
    replacement = prepare_assets(replacement_path, Path(__file__).parents[2], profile=prepared['profile'],
                                 model_read_scope=external / 'scope.json')
    assert replacement['session'] != prepared['session']
    target = external / 'target.json'
    target.write_bytes(canonical_json({'run_id': 'EXAMPLE'}))
    reference = external / 'reference.json'
    reference.write_bytes(canonical_json({'reference': 'EXAMPLE'}))
    settings = {'normal_file_reads_only': True, 'normal':
        {'target': str(target), 'prepared': str(prepared_path), 'reference_manifest': str(reference)}}
    check = load_pack(Path(__file__).parents[1] / 'packs/PACK-BASELINE').checks['SUP-05']
    env = environment()
    committed = {'prepared': sha256_hex(prepared_path.read_bytes()),
                 'reference_manifest': sha256_hex(reference.read_bytes())}
    kwargs = {'side': 'baseline', 'attempt_id': None, 'issued_at': utcnow(),
              'expected_normal_file_sha256': committed, 'expected_target_sha256': sha256_hex(target.read_bytes())}
    _, values = run_binding.compute(check, settings, env, **kwargs)
    assert values['supply_normal_prepared_sha256'] == sha256_hex(canonical_json(prepared))
    assert values['supply_normal_prepared_sha256'] != committed['prepared']
    old = prepared_path.parent
    old.rename(external / 'retained')
    replacement_path.rename(old)
    assert load_prepared(prepared_path) == replacement
    with pytest.raises(run_binding.RunBindingUnavailable, match='RUN_BINDING_SUP05_NORMAL_PREPARED_CHANGED'):
        run_binding.compute(check, settings, env, **kwargs)
