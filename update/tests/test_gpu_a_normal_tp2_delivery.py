"""Verify a sealed delivery pair offline with harmless prepared fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import stat

import pytest
import yaml

from guard.models import canonical_json, sha256_hex
from tests.test_gpu_a_aws_lab import HANDOFF, REVISION
from tools import gpu_a_aws_lab as lab
from tools import gpu_a_normal_tp2_callbacks as passive
from tools.mvp_versions import versions


def _write_json(path, document):
    raw = canonical_json(document) + b'\n'
    path.write_bytes(raw)
    path.chmod(0o600)
    return sha256_hex(raw)


@pytest.fixture
def delivery(tmp_path):
    directory = tmp_path / 'prepared'
    directory.mkdir()
    rows = []
    for side, release in versions().items():
        root = directory / side
        record = lab.prepare_normal_tp2_render(HANDOFF, root,
            callback_mode='worker-shards-and-scoped-runner-consumption', generation='v2-template',
            profile='dev', run_id='example-' + side, engine_image=release['image'],
            engine_version=release['version'], model_revision=REVISION, host_root='/srv/byul-a',
            release_launch=True, normal_tp2=True)
        asset_dir = root / 'engine-callbacks'
        if side == 'candidate':
            # The retained copy can remain private without being installed.
            asset_dir.chmod(0o700)
            for item in asset_dir.rglob('*'):
                item.chmod(0o700 if item.is_dir() else 0o600)
        manifest = json.loads((asset_dir / 'manifest.json').read_bytes())
        rows.append({'side': side, 'version': release['version'], 'image': release['image'],
            'profile': record['generation'], 'model_inventory_sha256': 'b' * 64,
            'same_engine_arguments_as_held_tp2': True, 'shipped_asset_count': len(manifest['assets']),
            'normal_tp2_callbacks': record[passive.KEY], 'render_sha256': sha256_hex((root / 'render.json').read_bytes()),
            'engine_yaml_sha256': record['outputs_sha256']['engine.yaml'],
            'probe_yaml_sha256': record['outputs_sha256']['probe.yaml'],
            'staged_template_sha256': lab.CHAT_TEMPLATE_SHA256})
    pair = {'schema': passive.PAIR_PREPARATION_SCHEMA, 'tested_source_tree': 'a' * 40,
            'held_parameters_sha256': 'a' * 64, 'sides': rows, 'local_render_only': True,
            'provider_contacted': False, 'engine_started': False, 'live_scope_verified': False, 'new_actual_pairs': 0}
    path = directory / 'paired-normal-tp2-preparation.json'
    return pair, path, _write_json(path, pair)


def test_delivery_verifies_paired_bytes_private_local_modes_and_host_expectation(tmp_path, delivery):
    _, source, pin = delivery
    output = tmp_path / 'delivery.json'
    argv = ['--verify-pair', str(source), '--pair-sha256', pin, '--out', str(output)]
    passive.main(argv)
    raw = output.read_bytes()
    receipt = json.loads(raw)
    assert receipt['schema'] == passive.DELIVERY_CHECK_SCHEMA and receipt['pair_sha256'] == pin
    assert receipt['local_input_verified'] is True
    assert receipt['host_publication_verified'] is receipt['ready_for_execution'] is False
    left, right = receipt['sides']
    assert [row['side'] for row in (left, right)] == ['baseline', 'candidate']
    assert left['launch_args_sha256'] == right['launch_args_sha256']
    assert left['plan_sha256'] != right['plan_sha256']
    assert left['source']['code_directory_modes'] == ['0500']
    assert right['source']['code_directory_modes'] == ['0700']
    assert [row['source']['settings_mode'] for row in (left, right)] == ['0400', '0600']
    for row in (left, right):
        host = row['expected_host']
        assert row['declared_runner_principal_verified'] is True and row['host_publication_verified'] is False
        assert (host['uid'], host['gid'], host['settings_mode'], host['journal_directory_mode']) == (2000, 0, '0400', '0700')
        assert row['source']['journal_empty'] is True
        assert set(row[passive.KEY]) == {'schema', 'mode', 'manifest_sha256', 'settings_sha256',
                                         'mounts_sha256', 'assets_sha256', 'session_sha256'}
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert sha256_hex(source.read_bytes()) == pin
    with pytest.raises(FileExistsError):
        passive.main(argv)
    assert output.read_bytes() == raw
    with pytest.raises(ValueError, match=passive.DELIVERY_ERROR):
        passive.main([*argv[:-1], str(source.parent / 'receipt.json')])


@pytest.mark.parametrize('damage', ('pair-pin', 'changed-render-bytes', 'exposed-settings',
    'linked-settings', 'journal-nonempty', 'missing-template', 'different-owner'))
def test_delivery_rejects_mismatched_sources_before_emitting_receipt(tmp_path, delivery, damage):
    pair, source, pin = delivery
    root = source.parent / 'candidate'
    if damage == 'pair-pin':
        pin = 'c' * 64
    elif damage == 'changed-render-bytes':
        render = root / 'render.json'
        render.write_bytes(render.read_bytes() + b'\n')
    elif damage == 'exposed-settings':
        (root / 'engine-callbacks' / 'settings.json').chmod(0o640)
    elif damage == 'linked-settings':
        settings = root / 'engine-callbacks' / 'settings.json'
        copy = tmp_path / 'inert-copy'
        copy.write_bytes(settings.read_bytes())
        copy.chmod(0o600)
        settings.unlink()
        settings.symlink_to(copy)
    elif damage == 'journal-nonempty':
        (root / 'lifecycle-data' / 'EXAMPLE').write_bytes(b'old')
    elif damage == 'missing-template':
        (root / Path(lab.CHAT_TEMPLATE_PATH).name).unlink()
    else:
        # Re-pin coherent saved bytes to reach the declaration check.
        engine_path = root / 'engine.yaml'
        deployment, service = yaml.safe_load_all(engine_path.read_bytes())
        deployment['spec']['template']['spec']['securityContext']['runAsUser'] = 2001
        raw = yaml.safe_dump_all([deployment, service]).encode()
        engine_path.write_bytes(raw)
        render_path = root / 'render.json'
        render = json.loads(render_path.read_bytes())
        render['outputs_sha256']['engine.yaml'] = sha256_hex(raw)
        pair = deepcopy(pair)
        pair['sides'][1]['engine_yaml_sha256'] = sha256_hex(raw)
        pair['sides'][1]['render_sha256'] = _write_json(render_path, render)
        pin = _write_json(source, pair)
    output = tmp_path / 'never-created.json'
    with pytest.raises(ValueError, match=passive.DELIVERY_ERROR):
        passive.main(['--verify-pair', str(source), '--pair-sha256', pin, '--out', str(output)])
    assert not output.exists()
