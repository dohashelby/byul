"""Offline preparation only. Small EXAMPLE assets are not runtime producers."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.models import canonical_json
from tools import gpu_b_sup16_prepare as prep


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def fixture_args(tmp_path, monkeypatch):
    # Public pair/helper/sensor retain their actual production SHA pins. Only
    # external metadata inputs and the Git/source boundary are explicit fixtures.
    files = {}
    for name in ('stage.py', 'prepare_pair.py', 'source_owner.py', 'original-procedure.json'):
        raw = b'{"EXAMPLE":"not executed"}' if name.endswith('.json') else b'# EXAMPLE static fixture; never executed\n'
        files[name] = put(tmp_path / 'inputs' / name, raw)
        monkeypatch.setitem(prep.PINS, name, hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(prep, 'source_context', lambda *a: {'commit': 'a' * 40, 'manifest_sha256': 'b' * 64,
        'verification': 'EXAMPLE Git boundary; not source admission'})
    return SimpleNamespace(source_root=prep.ROOT, source_commit='a' * 40, manifest_sha256='b' * 64,
        stage=files['stage.py'], prepare_pair=files['prepare_pair.py'], source_owner=files['source_owner.py'],
        procedure=files['original-procedure.json'], normal_model=None, challenge_model=None,
        output=tmp_path / 'prepared')


def test_actual_public_helper_to_pair_asset_contract_and_missing_runtime(tmp_path, monkeypatch):
    args = fixture_args(tmp_path, monkeypatch)
    result = prep.prepare(args)
    assert result['static_assets_prepared'] is True
    assert result['both_model_copies_verified'] is False
    assert result['ready_for_execution'] is False
    assert result['missing_runtime_inputs'] == prep.RUNTIME_MISSING + ['normal staged model directory', 'challenge staged model directory']
    assert not result['new_ENV_target_prepared_session_generated']
    assert not result['engine_GPU_AWS_or_writer_started'] and not result['official']
    assert result['same_record_producer_for_both_roles'] == prep.PINS['pair.py']
    for name, entry in result['assets'].items():
        raw = (args.output / 'assets' / name).read_bytes()
        assert entry == {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    commands = json.loads((args.output / 'commands.json').read_bytes())
    assert hashlib.sha256((args.output / 'commands.json').read_bytes()).hexdigest() == result['commands_sha256']
    assert commands['deadlines_seconds'] == {'asset_wait': 90, 'collection': 120, 'Pod': 600}
    for role in ('normal', 'challenge'):
        argv = commands['writers'][role]
        assert argv[argv.index('--tool-sha256') + 1] == prep.PINS['pair.py']
        assert argv[argv.index('--ordering-helper') + 1] == '/guard-prepare/ordering.py'
        assert '--native-recording' in argv and '<actual-' in argv[-1]
    assert commands['prepare_pair'][commands['prepare_pair'].index('--record-producer-sha256') + 1] == prep.PINS['pair.py']
    # Actual generated asset bytes are directly consumed by the existing fixed
    # pair module, not a hand-built substitute writer or a registry wrapper.
    pair = prep.module((args.output / 'assets/pair.py').read_bytes(), args.output / 'assets/pair.py')
    assert pair['ORDERING_HELPER_SHA256'] == result['assets']['ordering.py']['sha256']


@pytest.mark.parametrize('asset', ['stage.py', 'prepare_pair.py', 'source_owner.py', 'original-procedure.json'])
def test_changed_asset_fails_before_output_reservation(tmp_path, monkeypatch, asset):
    args = fixture_args(tmp_path, monkeypatch)
    attr = {'stage.py': 'stage', 'prepare_pair.py': 'prepare_pair', 'source_owner.py': 'source_owner', 'original-procedure.json': 'procedure'}[asset]
    Path(getattr(args, attr)).write_bytes(b'CHANGED')
    with pytest.raises(ValueError, match='FILE_READ_DEPENDENCY_UNAVAILABLE'):
        prep.prepare(args)
    assert not args.output.exists()


def test_missing_asset_does_not_make_an_empty_substitute(tmp_path, monkeypatch):
    args = fixture_args(tmp_path, monkeypatch)
    args.stage = tmp_path / 'absent.py'
    with pytest.raises(FileNotFoundError):
        prep.prepare(args)
    assert not args.output.exists()


def test_existing_output_is_not_modified(tmp_path, monkeypatch):
    args = fixture_args(tmp_path, monkeypatch)
    args.output.mkdir()
    original = put(args.output / 'old-record', b'ORIGINAL')
    with pytest.raises(ValueError, match='existing evidence is never overwritten'):
        prep.prepare(args)
    assert original.read_bytes() == b'ORIGINAL' and len(list(args.output.iterdir())) == 1


def test_source_gate_error_is_not_replaced_by_partial_preparation(tmp_path, monkeypatch):
    args = fixture_args(tmp_path, monkeypatch)
    def reject(*a):
        raise ValueError('ORIGINAL_SOURCE_FAILURE')
    monkeypatch.setattr(prep, 'source_context', reject)
    with pytest.raises(ValueError, match='ORIGINAL_SOURCE_FAILURE'):
        prep.prepare(args)
    assert not args.output.exists()


def model(tmp_path, role):
    # Tiny bytes for direct metadata/error contracts, never a model loader.
    full, partial = b'EXAMPLE-full-reference', b'EXAMPLE-part'
    sha = lambda raw: hashlib.sha256(raw).hexdigest()
    root = tmp_path / role
    rows = []
    for name, raw in [('config.json', b'{}'), ('model.safetensors', partial if role == 'challenge' else full)]:
        put(root / name, raw)
        rows.append({'path': name, 'bytes': len(raw), 'sha256': sha(raw)})
    refs = deepcopy(rows)
    refs[1].update(bytes=len(full), sha256=sha(full))
    manifest = {'schema': 'guard.model-manifest/1', 'scope': 'all', 'model_id': 'EXAMPLE-no-model-load',
                'files': refs, 'inventory_sha256': sha(canonical_json(refs))}
    raw = canonical_json(manifest)
    put(root / 'reference-manifest.json', raw)
    receipt = {'schema': 'byul-sup16-staged-artifact/1', 'role': role, 'producer_sha256': prep.PINS['stage.py'],
        'actual_partial_promoted_as_LAB_fault': role == 'challenge',
        'partial_source_sha256': sha(partial) if role == 'challenge' else None,
        'reference_manifest_sha256': sha(raw), 'actual_files': rows}
    put(root / 'staging-receipt.json', canonical_json(receipt))
    pair = {'MODEL_FILES': {'config.json', 'model.safetensors'}, 'REFERENCE_SHA': sha(full), 'REFERENCE_BYTES': len(full),
        'PARTIAL_SHA': sha(partial), 'PARTIAL_BYTES': len(partial), 'digest': lambda p: sha(Path(p).read_bytes())}
    return root, pair


@pytest.mark.parametrize('role', ['normal', 'challenge'])
def test_actual_file_bytes_match_the_staged_role_and_reference(tmp_path, role):
    root, pair = model(tmp_path, role)
    result = prep.check_model(root, role, pair)
    assert result['role'] == role and result['worker_consumption_verified'] is False


def test_role_mix_is_rejected(tmp_path):
    root, pair = model(tmp_path, 'normal')
    with pytest.raises(ValueError, match='ROLE_RECEIPT_MISMATCH'):
        prep.check_model(root, 'challenge', pair)


@pytest.mark.parametrize('damage', ['bytes', 'duplicate', 'reference', 'receipt'])
def test_model_error_boundaries(tmp_path, damage):
    root, pair = model(tmp_path, 'challenge')
    receipt = json.loads((root / 'staging-receipt.json').read_bytes())
    if damage == 'bytes':
        (root / 'model.safetensors').write_bytes(b'altered')
    elif damage == 'duplicate':
        receipt['actual_files'] = [receipt['actual_files'][0]] * 2
    elif damage == 'reference':
        receipt['reference_manifest_sha256'] = '0' * 64
    else:
        receipt['producer_sha256'] = '0' * 64
    (root / 'staging-receipt.json').write_bytes(canonical_json(receipt))
    with pytest.raises(ValueError, match='SUP16_PREPARATION_'):
        prep.check_model(root, 'challenge', pair)


def test_packet_commands_keep_each_role_and_do_not_make_future_identity(tmp_path, monkeypatch):
    args = fixture_args(tmp_path, monkeypatch)
    args.normal_model = tmp_path / 'normal'
    args.challenge_model = tmp_path / 'challenge'
    metadata = {role: {'root': str(getattr(args, role + '_model')), 'role': role, 'reference_manifest_sha256': 'c' * 64,
        'model_id': 'EXAMPLE', 'inventory_sha256': 'd' * 64, 'staging_receipt_sha256': str(i) * 64} for i, role in enumerate(('normal', 'challenge'))}
    monkeypatch.setattr(prep, 'check_model', lambda root, role, pair: metadata[role])
    result = prep.prepare(args)
    assert result['both_model_copies_verified'] is True and not result['ready_for_execution']
    commands = json.loads((args.output / 'commands.json').read_bytes())
    for i, role in enumerate(('normal', 'challenge')):
        argv = commands['writers'][role]
        assert argv[argv.index('--staging-receipt-sha256') + 1] == str(i) * 64
    assert not list(args.output.glob('*binding*')) and not list(args.output.glob('*ENV*'))


def test_two_different_model_references_are_not_a_pair(tmp_path, monkeypatch):
    args = fixture_args(tmp_path, monkeypatch)
    args.normal_model = tmp_path / 'normal'
    args.challenge_model = tmp_path / 'challenge'
    monkeypatch.setattr(prep, 'check_model', lambda root, role, pair: {'root': str(root),
        'model_id': role, 'inventory_sha256': 'c' * 64, 'reference_manifest_sha256': 'd' * 64})
    with pytest.raises(ValueError, match='SAME_REFERENCE_REQUIRED'):
        prep.prepare(args)
    assert not args.output.exists()
