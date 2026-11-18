"""MVP 입력 조립의 EXAMPLE 계약 시험. 모델·클러스터·엔진 실행은 없다."""
import json
from pathlib import Path
import tomllib

import pytest
import yaml

from guard.models import canonical_json, sha256_hex
from guard.runtime.kubernetes_http import KubernetesBindingError
from tests import test_gpu_a_run_inputs as fixtures
from tests.test_code_selection_observation import IMAGE
from tools import gpu_a_run_inputs as inputs, mvp_sup08_inputs as tool
from tools.mvp_versions import versions
from tools.prepare_sup08_phi3v_fixture import SOURCE_MODEL, SOURCE_REVISION, CODE_REPOSITORY


def source(tmp_path, monkeypatch):
    inventory = {'EXAMPLE-not-model-bytes': {'bytes': 1, 'sha256': 'a' * 64}}
    digest = sha256_hex(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode())
    fixture = {'schema': 'byul-sup08-phi3v-fixture/1', 'source_model': SOURCE_MODEL,
        'source_revision': SOURCE_REVISION, 'model_id': 'local-byul/sup08-phi3v-derived',
        'model_snapshot_id': '7ae557604adf67be50417f59c2c2f167def9a775',
        'model_inventory': inventory, 'model_inventory_sha256': digest,
        'code_revisions': {'approved': 'b' * 40, 'unapproved': 'c' * 40},
        'code_repository': CODE_REPOSITORY, 'code_marker_root': '/cache/hf/byul-code-markers/fixture-source'}
    fixture_file = tmp_path / 'fixture.json'; fixture_file.write_bytes(canonical_json(fixture))
    historical = {'model_inventory_sha256': digest, 'security_comparison': 'REGRESSED'}
    contract = tmp_path / 'fixture-contract.json'
    contract.write_bytes(canonical_json({'fixture': {key: fixture[key] for key in (
        'schema', 'source_model', 'source_revision', 'model_id', 'model_snapshot_id',
        'model_inventory_sha256', 'code_repository', 'code_revisions')}}))
    monkeypatch.setattr(tool, 'FIXTURE_CONTRACT', contract)
    envs, targets = {}, {}
    for side, row in versions().items():
        folder = tmp_path / side; folder.mkdir()
        monkeypatch.setattr(fixtures, 'IMAGE', row['image'])
        pods, render = fixtures.deployed(folder, 'v2-code-selection-trust', engine_version=row['version'],
            model_id=fixture['model_id'], code_revision='b' * 40)
        env = inputs.environment(render, observed_engine_version=row['version'], inventory=digest,
            gpu_model='NVIDIA L4', driver='EXAMPLE-driver', data_origin='EXAMPLE')
        target = inputs.target(env, render, json.loads((folder / 'namespace.json').read_bytes()),
            pods['engine'], pods['probe'], context='EXAMPLE')
        envs[side] = folder / 'environment.json'; envs[side].write_bytes(canonical_json(env.to_dict()))
        targets[side] = folder / 'target.json'; targets[side].write_bytes(canonical_json(target))
        historical[side] = {'image_digest': row['image'], 'challenge_state': 'ignored' if side == 'baseline' else 'consumed'}
    poc = tmp_path / 'historical.json'; poc.write_bytes(canonical_json({'product_update': historical}))
    monkeypatch.setattr(tool, 'POC', poc)
    return fixture_file, envs, targets


def test_pair_is_current_canonical_inputs_without_fabricated_results(tmp_path, monkeypatch):
    fixture, envs, targets = source(tmp_path, monkeypatch)
    out = tmp_path / 'prepared'
    manifest = tool.prepare(fixture, envs, targets, out, control_image=IMAGE)
    assert manifest['pack_ref']['revision'] == 39 and manifest['expected_comparison'] == 'REGRESSED'
    assert manifest['actual_comparison'] is None and manifest['official'] is False
    assert manifest['data_origin'] == 'DECLARED'
    assert manifest['preflight'] == {'input_contracts_valid': True, 'comparison_conditions_equal': True,
        'cluster_observed': False, 'engine_executed': False, 'model_bytes_rechecked': False}
    for side, state in [('baseline', 'ignored'), ('candidate', 'consumed')]:
        assert manifest['sides'][side]['actual_result'] is None
        assert manifest['sides'][side]['expected_state'] == state
        folder = out / side
        config = tomllib.loads((folder / 'guard.toml').read_text())
        target = json.loads((folder / 'target.json').read_bytes())
        policy = yaml.safe_load((folder / 'policy.yaml').read_bytes())
        assert config['input_validation']['code_marker_root'] == target['code_marker_root']
        assert config['input_validation']['code_marker_root'] != json.loads(fixture.read_bytes())['code_marker_root']
        assert policy['scope']['include'] == ['SUP-08'] and policy['confirmed_by'] is None
        assert policy['values']['SUP-08']['approved_code_revisions'] == ['b' * 40]
        assert 'prepared' not in config['input_validation']
    for name, digest in manifest['files'].items():
        assert sha256_hex((out / name).read_bytes()) == digest
    assert not any(key in canonical_json(manifest).decode() for key in ['EVD-', 'RES-', 'RPT-', 'MEASURED'])
    with pytest.raises(ValueError, match='새 입력'):
        tool.prepare(fixture, envs, targets, out, control_image=IMAGE)


@pytest.mark.parametrize('damage', ['inventory', 'code', 'image', 'model', 'gpu', 'trust'])
def test_mismatched_fixture_or_pair_rejected_before_output(tmp_path, monkeypatch, damage):
    fixture, envs, targets = source(tmp_path, monkeypatch)
    file = fixture if damage in {'inventory', 'code'} else targets['candidate'] if damage == 'trust' else envs['candidate']
    value = json.loads(file.read_bytes())
    if damage == 'inventory': value['model_inventory_sha256'] = 'd' * 64
    elif damage == 'code': value['code_revisions']['unapproved'] = value['code_revisions']['approved']
    elif damage == 'trust': value['comparison_config']['trust_remote_code'] = False
    else:
        from guard.models import Environment
        env = Environment.model_validate(value)
        if damage == 'image': env.engine.image_digest = 'docker.io/vllm/vllm-openai@sha256:' + 'e' * 64
        if damage == 'model': env.model.inventory_sha256 = 'e' * 64
        if damage == 'gpu': env.runtime.gpu_model = 'EXAMPLE-other-GPU'
        env = env.seal(); value = env.to_dict()
        # ENV 신원 변경에 맞춰 target도 다시 결합한다. 의미가 다른 쌍 자체를 거부해야 한다.
        target = json.loads(targets['candidate'].read_bytes())
        target['environment_ref'] = env.ref().model_dump(mode='json')
        targets['candidate'].write_bytes(canonical_json(target))
    file.write_bytes(canonical_json(value))
    out = tmp_path / 'rejected'
    with pytest.raises(KubernetesBindingError if damage == 'gpu' else ValueError):
        tool.prepare(fixture, envs, targets, out, control_image=IMAGE)
    assert not out.exists()


def test_changed_pair_does_not_inherit_historical_expectations(tmp_path, monkeypatch):
    fixture, envs, targets = source(tmp_path, monkeypatch)
    historical = json.loads(tool.POC.read_bytes())
    historical['product_update']['candidate']['image_digest'] = 'other'
    tool.POC.write_bytes(canonical_json(historical))
    result = tool.prepare(fixture, envs, targets, tmp_path / 'new', control_image=IMAGE)
    assert result['expected_comparison'] is None
    assert all(row['expected_state'] is None for row in result['sides'].values())


@pytest.mark.parametrize('damage', ['approved', 'unapproved', 'code_repository', 'model_id', 'model_snapshot_id'])
def test_valid_shaped_but_different_poc_identity_rejected(tmp_path, monkeypatch, damage):
    fixture, envs, targets = source(tmp_path, monkeypatch)
    value = json.loads(fixture.read_bytes())
    if damage in {'approved', 'unapproved'}:
        value['code_revisions'][damage] = 'd' * 40
    elif damage == 'model_snapshot_id':
        value[damage] = 'e' * 40
    else:
        value[damage] += '-different'
    fixture.write_bytes(canonical_json(value))
    out = tmp_path / 'rejected-identity'
    with pytest.raises(ValueError, match='PoC와 같은'):
        tool.prepare(fixture, envs, targets, out, control_image=IMAGE)
    assert not out.exists()


def test_fixed_fixture_identity_has_unchanged_poc_source_document():
    contract = json.loads(tool.FIXTURE_CONTRACT.read_bytes())
    source_document = tool.ROOT / contract['provenance']['source_document']
    assert sha256_hex(source_document.read_bytes()) == contract['provenance']['source_document_sha256']
    assert contract['fixture']['source_model'] == SOURCE_MODEL
    assert contract['fixture']['source_revision'] == SOURCE_REVISION
    assert contract['fixture']['code_repository'] == CODE_REPOSITORY


def test_original_pack38_remains_supported_by_its_exact_published_ref(tmp_path,monkeypatch):
    import shutil
    from guard.models import load_pack
    fixture,envs,targets=source(tmp_path,monkeypatch)
    original=tmp_path/'pack38';shutil.copytree(tool.ROOT/'packs/PACK-BASELINE',original)
    overlay=tool.ROOT/'tests/fixtures/input-matrix-pack38'
    for path in overlay.rglob('*.yaml'):shutil.copyfile(path,original/path.relative_to(overlay))
    pack=load_pack(original)
    assert pack.ref().sha256=='22dee30a0df2e9e79defd6d28010f3d16e202512d67269e831d3c5fa95113d80'
    result=tool.prepare(fixture,envs,targets,tmp_path/'old-prepared',control_image=IMAGE,pack=original)
    assert result['pack_ref']==pack.ref().model_dump(mode='json')and result['preflight']['engine_executed']is False


def test_same_revision_with_changed_definition_cannot_borrow_the_supported_pack_ref(tmp_path,monkeypatch):
    import shutil
    from guard.models import load_pack
    fixture,envs,targets=source(tmp_path,monkeypatch)
    changed=tmp_path/'changed-pack';shutil.copytree(tool.ROOT/'packs/PACK-BASELINE',changed)
    file=changed/'checks/SUP-08.yaml';doc=yaml.safe_load(file.read_bytes());doc['limitations'].append('EXAMPLE altered definition')
    file.write_text(yaml.safe_dump(doc,allow_unicode=True,sort_keys=False));manifest=yaml.safe_load((changed/'pack.yaml').read_bytes())
    for row in manifest['files_sha256']:
        if row['path']=='checks/SUP-08.yaml':row['sha256']=sha256_hex(file.read_bytes())
    (changed/'pack.yaml').write_text(yaml.safe_dump(manifest,allow_unicode=True,sort_keys=False))
    assert load_pack(changed).manifest.revision==39
    out=tmp_path/'refused'
    with pytest.raises(ValueError,match='정본 팩과 SUP-08'):
        tool.prepare(fixture,envs,targets,out,control_image=IMAGE,pack=changed)
    assert not out.exists()
