"""작은 EXAMPLE 파일로 로컬 확인과 실제 입력/실행의 경계를 검증한다."""
import base64
import json
from pathlib import Path
import shutil

import pytest
import yaml

from guard.models import Environment, canonical_json, load_pack, sha256_hex
from tests import test_mvp_sup08_inputs as old
from tests.test_code_selection_observation import IMAGE
from tools import gpu_a_aws_lab, mvp_sup08_inputs, mvp_sup08_local_audit as tool
from tools.mvp_versions import versions


def local_assets(tmp_path, monkeypatch):
    fixture_file, envs, targets = old.source(tmp_path, monkeypatch)
    fixture = json.loads(fixture_file.read_bytes())
    model = tmp_path / 'EXAMPLE-model'
    model.mkdir()
    raw = b'EXAMPLE-small-model-not-weights'
    (model / 'EXAMPLE-model.bin').write_bytes(raw)
    fixture['model_path'] = str(model)
    fixture['model_inventory'] = {'EXAMPLE-model.bin': {'bytes': len(raw), 'sha256': sha256_hex(raw)}}
    fixture['model_inventory_sha256'] = sha256_hex(json.dumps(
        fixture['model_inventory'], sort_keys=True, separators=(',', ':')).encode())
    fixture['hf_home'] = str(tmp_path / 'hf')
    cache = Path(fixture['hf_home']) / 'hub' / ('models--' + fixture['code_repository'].replace('/', '--'))
    fixture['code_processor_sha256'] = {}
    image_processor = b'EXAMPLE-image-processor-not-executed'
    monkeypatch.setattr(tool, 'IMAGE_PROCESSOR_SHA256', sha256_hex(image_processor))
    for label, revision in fixture['code_revisions'].items():
        folder = cache / 'snapshots' / revision
        folder.mkdir(parents=True)
        raw = ('EXAMPLE-processor-' + label).encode()
        (folder / 'processing_phi3_v.py').write_bytes(raw)
        (folder / 'image_processing_phi3_v.py').write_bytes(image_processor)
        fixture['code_processor_sha256'][label] = sha256_hex(raw)
    (cache / 'refs').mkdir()
    (cache / 'refs/main').write_text(fixture['code_revisions']['approved'])
    fixture_file.write_bytes(canonical_json(fixture))
    trusted = tmp_path / 'trusted-EXAMPLE-fixture.json'
    trusted.write_bytes(canonical_json(fixture))
    monkeypatch.setattr(tool, 'LOCAL_FIXTURE', trusted)
    contract = json.loads(mvp_sup08_inputs.FIXTURE_CONTRACT.read_bytes())
    contract['fixture']['model_inventory_sha256'] = fixture['model_inventory_sha256']
    mvp_sup08_inputs.FIXTURE_CONTRACT.write_bytes(canonical_json(contract))
    historical = json.loads(mvp_sup08_inputs.POC.read_bytes())
    historical['product_update']['model_inventory_sha256'] = fixture['model_inventory_sha256']
    mvp_sup08_inputs.POC.write_bytes(canonical_json(historical))
    for side in tool.SIDES:
        env = Environment.model_validate(json.loads(envs[side].read_bytes()))
        env.model.inventory_sha256 = fixture['model_inventory_sha256']
        env = env.seal()
        envs[side].write_bytes(canonical_json(env.to_dict()))
        folder = envs[side].parent
        target = old.inputs.target(env, json.loads((folder / 'render.json').read_bytes()),
            json.loads((folder / 'namespace.json').read_bytes()),
            json.loads((folder / 'engine-pod.json').read_bytes()),
            json.loads((folder / 'probe-pod.json').read_bytes()), context='EXAMPLE')
        targets[side].write_bytes(canonical_json(target))
    assembled = tmp_path / 'EXAMPLE-contract-inputs'
    mvp_sup08_inputs.prepare(fixture_file, envs, targets, assembled, control_image=IMAGE)
    launch = tmp_path / 'launch'
    launch.mkdir()
    sides = {}
    for side, expected in versions().items():
        folder = launch / side
        folder.mkdir()
        engine, probe, render = gpu_a_aws_lab.render(old.fixtures.HANDOFF,
            generation='v2-code-selection-trust', profile='dev', run_id='a-run-1',
            engine_image=expected['image'], engine_version=expected['version'],
            model_revision=fixture['model_snapshot_id'], model_id=fixture['model_id'],
            code_revision=fixture['code_revisions']['approved'], host_root='/srv/byul-a', release_launch=True)
        (folder / 'engine.yaml').write_bytes(engine)
        (folder / 'probe.yaml').write_bytes(probe)
        (folder / 'render.json').write_bytes(canonical_json(render))
        shutil.copyfile(assembled / side / 'policy.yaml', folder / 'policy.yaml')
        shutil.copyfile(assembled / side / 'guard.toml', folder / 'guard.pending-target.toml')
        sides[side] = {'code_marker_root': render['code_marker_root']}
    (launch / 'control.png').write_bytes(base64.b64decode(IMAGE.partition(',')[2]))
    (launch / 'control-image.txt').write_text(IMAGE + '\n')
    manifest = {'schema': 'guard-sup08-local-launch-inputs/1', 'fixture_manifest_sha256': sha256_hex(fixture_file.read_bytes()),
        'pack_ref': load_pack(tool.ROOT / 'packs/PACK-BASELINE').ref().model_dump(mode='json'), 'sides': sides,
        'files_sha256': {name: sha256_hex((launch / name).read_bytes()) for name in sorted(tool.LAUNCH_FILES)}}
    manifest_file = launch / 'manifest.json'
    manifest_file.write_bytes(canonical_json(manifest))
    return fixture_file, manifest_file, envs, targets


def test_verified_local_files_do_not_create_actual_inputs_or_results(tmp_path, monkeypatch):
    fixture, launch, _, _ = local_assets(tmp_path, monkeypatch)
    result = tool.audit(fixture, launch)
    assert result['local_assets_valid'] and result['model_bytes_rechecked']
    assert result['code_cache_valid'] and result['launch_plan_valid']
    assert len(result['missing_inputs']) == 4 and result['final_input_contracts_valid'] is None
    assert not result['ready_for_final_assembly'] and not result['input_preflight_passed']
    assert result['actual_comparison'] is None and not result['official']
    assert not result['cluster_observed'] and not result['engine_executed']


def test_example_environment_contracts_are_not_actual_environment_proof(tmp_path, monkeypatch):
    fixture, launch, envs, targets = local_assets(tmp_path, monkeypatch)
    result = tool.audit(fixture, launch, environments=envs, targets=targets)
    assert result['final_input_contracts_valid'] is True
    assert result['missing_inputs'] == ['baseline:EXAMPLE_ENV_NOT_ACTUAL', 'candidate:EXAMPLE_ENV_NOT_ACTUAL']
    assert not result['ready_for_final_assembly'] and not result['input_preflight_passed']


@pytest.mark.parametrize('damage', ['model', 'missing_model', 'processor', 'ref', 'launch', 'image_with_new_file_hash'])
def test_changed_actual_files_or_self_rehashed_wrong_launch_are_not_valid(tmp_path, monkeypatch, damage):
    fixture, launch, _, _ = local_assets(tmp_path, monkeypatch)
    value = json.loads(fixture.read_bytes())
    if damage in {'model', 'missing_model'}:
        path = Path(value['model_path']) / 'EXAMPLE-model.bin'
        if damage == 'model': path.write_bytes(b'x' * path.stat().st_size)
        else: path.unlink()
    elif damage in {'processor', 'ref'}:
        cache = Path(value['hf_home']) / 'hub' / ('models--' + value['code_repository'].replace('/', '--'))
        path = cache / ('refs/main' if damage == 'ref' else 'snapshots/' + value['code_revisions']['approved'] + '/processing_phi3_v.py')
        path.write_bytes(b'EXAMPLE-changed')
    else:
        path = launch.parent / 'candidate/engine.yaml'
        if damage == 'launch': path.write_bytes(path.read_bytes() + b'\n')
        else:
            deployment, service = list(yaml.safe_load_all(path.read_bytes()))
            deployment['spec']['template']['spec']['containers'][0]['image'] = 'EXAMPLE-unreviewed-image'
            path.write_text(yaml.safe_dump_all([deployment, service]))
            manifest = json.loads(launch.read_bytes())
            manifest['files_sha256']['candidate/engine.yaml'] = sha256_hex(path.read_bytes())
            launch.write_bytes(canonical_json(manifest))
    result = tool.audit(fixture, launch)
    assert not result['local_assets_valid'] and not result['ready_for_final_assembly']
    assert not result['input_preflight_passed'] and result['actual_comparison'] is None


def test_cli_writes_blocked_report_and_keeps_existing_report(tmp_path, monkeypatch):
    fixture, launch, _, _ = local_assets(tmp_path, monkeypatch)
    out = tmp_path / 'report.json'
    args = ['--fixture', str(fixture), '--launch-manifest', str(launch), '--output', str(out)]
    assert tool.main(args) == 2
    before = out.read_bytes()
    assert json.loads(before)['model_bytes_rechecked']
    with pytest.raises(ValueError, match='덮어쓰지'):
        tool.main(args)
    assert out.read_bytes() == before


def test_reader_refuses_symlink_without_following_it(tmp_path, monkeypatch):
    source = tmp_path / 'EXAMPLE-input'
    source.write_bytes(b'EXAMPLE')
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda p: p == source or original(p))
    assert tool.verify_file(source, sha256_hex(b'EXAMPLE')) == {
        'path': str(source), 'expected_sha256': sha256_hex(b'EXAMPLE'), 'verified': False, 'error': 'SYMLINK'}


def test_both_launch_commands_use_current_provider_data(tmp_path, monkeypatch):
    from guard.adapters.engine_provider import VllmProvider
    _, launch, _, _ = local_assets(tmp_path, monkeypatch)
    for side, expected in versions().items():
        deployment, _ = list(yaml.safe_load_all((launch.parent / side / 'engine.yaml').read_bytes()))
        engine, = deployment['spec']['template']['spec']['containers']
        assert engine['command'] == VllmProvider().release_profile(expected['version'])['launch']['command']
        assert engine['command'] != ['/usr/local/bin/vllm-nonroot-entrypoint.sh']
