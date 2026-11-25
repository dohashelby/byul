"""입력 파일/결합의 형식 확인을 실환경 자격·실행 결과와 구분한다."""
from copy import deepcopy
from hashlib import sha256
import json
import socket
import subprocess
from pathlib import Path

import pytest
import yaml

from guard.models import load_pack
from tests.test_code_selection_observation import IMAGE
from tests.test_mvp_sup08_inputs import source
from tools import mvp_a_input_preflight as tool, mvp_sup08_inputs


@pytest.fixture
def sources(tmp_path, monkeypatch):
    fixture, envs, targets = source(tmp_path, monkeypatch)
    folder = tmp_path / 'assembled'
    mvp_sup08_inputs.prepare(fixture, envs, targets, folder, control_image=IMAGE)
    rows = []
    for side in tool.SIDES:
        row = {'check_id': 'SUP-08', 'side': side}
        for role, name in [('environment', 'environment.json'), ('policy', 'policy.yaml'),
                           ('configuration', 'guard.toml')]:
            p = folder / side / name
            row[role] = {'path': str(p), 'sha256': sha256(p.read_bytes()).hexdigest()}
        rows.append(row)
    return {'schema': tool.SCHEMA, 'pack_ref': load_pack(tool.ROOT / 'packs/PACK-BASELINE').ref().model_dump(mode='json'),
            'inputs': rows}


def run(tmp_path, data):
    p = tmp_path / 'index.json'
    p.write_text(json.dumps(data))
    return tool.audit(p)


def test_files_and_binding_are_checked_without_network_or_live_success(tmp_path, monkeypatch, sources):
    def forbidden(*args, **kwargs):
        raise AssertionError('파일 사전 점검의 외부 IO 금지')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    report = run(tmp_path, sources)
    assert len(report['rows']) == 54 and report['file_binding_valid_sides'] == 2
    bound = [r for r in report['rows'] if r['file_binding_valid']]
    assert all(r['environment_origin'] == 'EXAMPLE' for r in bound)
    assert all('EXAMPLE_ENV_NOT_ACTUAL' in r['blocking_codes'] for r in bound)
    assert all(r['ready_for_execution'] is r['input_preflight_passed'] is r['live_target_verified'] is False
               and r['actual_results'] is None for r in report['rows'])
    assert sum(r['blocking_codes'] == ['ACTUAL_INPUT_FILES_MISSING'] for r in report['rows']) == 52
    assert report['engine_executed'] is report['ready_for_execution'] is False


def test_preflight_parses_verified_environment_and_policy_bytes(tmp_path, monkeypatch, sources):
    verified = {Path(row[role]['path']) for row in sources['inputs'] for role in ('environment', 'policy')}
    open_file = Path.open

    def reject_second_reader(path, *args, **kwargs):
        if path in verified:
            pytest.fail('preflight must parse the original bounded read')
        return open_file(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', reject_second_reader)
    report = run(tmp_path, sources)
    assert report['file_binding_valid_sides'] == 2
    assert not report['engine_executed']


@pytest.mark.parametrize('change', ['hash', 'side', 'target-env', 'symlink', 'missing', 'policy-scope', 'policy-type'])
def test_invalid_side_keeps_other_input_and_missing_scope_visible(tmp_path, sources, change):
    data = deepcopy(sources); entry = data['inputs'][0]
    if change == 'hash':
        entry['environment']['sha256'] = '0' * 64
    elif change == 'side':
        entry['environment'] = data['inputs'][1]['environment']
    elif change == 'target-env':
        from pathlib import Path
        cfg = Path(entry['configuration']['path'])
        target = cfg.parent / 'target.json'
        value = json.loads(target.read_bytes()); value['environment_ref']['sha256'] = '0' * 64
        target.write_text(json.dumps(value))
    elif change == 'symlink':
        from pathlib import Path
        p = Path(entry['policy']['path']); link = tmp_path / 'policy-link.yaml'; link.symlink_to(p)
        entry['policy']['path'] = str(link)
    elif change == 'missing':
        entry['configuration']['path'] += '.missing'
    else:
        from pathlib import Path
        p = Path(entry['policy']['path']); value = yaml.safe_load(p.read_bytes())
        if change == 'policy-scope': value['scope']['exclude'] = ['SUP-*']
        else: value['values']['SUP-08']['code_selection_allowed'] = 'EXAMPLE-private-invalid-bool'
        p.write_text(yaml.safe_dump(value)); entry['policy']['sha256'] = sha256(p.read_bytes()).hexdigest()
    report = run(tmp_path, data)
    bad = next(r for r in report['rows'] if r['check_id'] == 'SUP-08' and r['side'] == 'baseline')
    assert bad['file_binding_valid'] is False and bad['blocking_codes']
    assert report['file_binding_valid_sides'] == 1
    assert 'EXAMPLE-private-invalid-bool' not in json.dumps(report)


@pytest.mark.parametrize('change', ['duplicate', 'pack', 'unknown-check', 'unknown-side', 'extra'])
def test_index_mismatch_is_rejected(tmp_path, sources, change):
    data = deepcopy(sources)
    if change == 'duplicate': data['inputs'].append(data['inputs'][0])
    elif change == 'pack': data['pack_ref']['revision'] -= 1
    elif change == 'unknown-check': data['inputs'][0]['check_id'] = 'REQ-99'
    elif change == 'unknown-side': data['inputs'][0]['side'] = 'other'
    else: data['inputs'][0]['ready_for_execution'] = True
    with pytest.raises(ValueError, match='MA7_SOURCE_'):
        run(tmp_path, data)


def test_policy_wildcard_scope_keeps_existing_selection_semantics(tmp_path, sources):
    from pathlib import Path
    data = deepcopy(sources); entry = data['inputs'][0]; p = Path(entry['policy']['path'])
    value = yaml.safe_load(p.read_bytes()); value['scope']['include'] = ['SUP-*']
    p.write_text(yaml.safe_dump(value)); entry['policy']['sha256'] = sha256(p.read_bytes()).hexdigest()
    assert run(tmp_path, data)['file_binding_valid_sides'] == 2


@pytest.mark.parametrize('placement',['root','side','side-precedence'])
@pytest.mark.parametrize('damage',['missing','foreign-env','valid-override'])
def test_check_target_override_is_the_file_actually_preflighted(tmp_path,sources,placement,damage):
    data=deepcopy(sources);entry=data['inputs'][0]
    cfg_path=Path(entry['configuration']['path']);shared=cfg_path.parent/'target.json'
    override=tmp_path/'check-target.json'
    target=json.loads(shared.read_bytes())
    if damage=='foreign-env':target['environment_ref']['sha256']='0'*64
    if damage!='missing':override.write_text(json.dumps(target))
    text=cfg_path.read_text()
    if damage=='valid-override':
        # A bad shared target must not hide a valid per-check replacement.
        shared.unlink()
    prefix='check_config' if placement=='root' else 'sides.baseline.check_config'
    if placement=='side-precedence':
        text+='\n[check_config.SUP-08]\ntarget='+json.dumps(str(tmp_path/'missing-root-target.json'))+'\n'
    cfg_path.write_text(text+f'\n[{prefix}.SUP-08]\ntarget='+json.dumps(str(override))+'\n')
    entry['configuration']['sha256']=sha256(cfg_path.read_bytes()).hexdigest()
    result=run(tmp_path,data)
    row=next(r for r in result['rows'] if (r['check_id'],r['side'])==('SUP-08','baseline'))
    assert row['file_binding_valid'] is (damage=='valid-override')
    assert result['file_binding_valid_sides']==(2 if damage=='valid-override' else 1)
    if damage=='foreign-env':assert row['blocking_codes']==['MA7_TARGET_ENVIRONMENT_MISMATCH']
    assert not row['ready_for_execution'] and row['actual_results'] is None


def test_malformed_per_check_settings_are_reported_as_blocked(tmp_path,sources):
    data=deepcopy(sources);entry=data['inputs'][0];path=Path(entry['configuration']['path'])
    path.write_text('check_config = []\n'+path.read_text())
    entry['configuration']['sha256']=sha256(path.read_bytes()).hexdigest()
    result=run(tmp_path,data)
    row=next(r for r in result['rows'] if (r['check_id'],r['side'])==('SUP-08','baseline'))
    assert not row['file_binding_valid'] and row['blocking_codes']==['CHECK_CONFIG_INVALID']


@pytest.mark.parametrize('record_session', ['5' * 32, None])
def test_stage2_side_table_is_used_and_missing_session_stays_blocked(tmp_path, sources, record_session):
    from pathlib import Path
    from guard.executors import run_binding
    from tests.test_a1_credential_copy_contract import example
    data = deepcopy(sources); entry = data['inputs'][0]; entry['check_id'] = 'PLT-12'
    pack = load_pack(tool.ROOT / 'packs/PACK-BASELINE'); check = pack.checks['PLT-12']
    inventory, values, _, _ = example()
    from guard.executors.credential_copy_contract import treatments
    values['copy_treatments'] = treatments(inventory)
    values = {k: v for k, v in values.items() if k not in run_binding.owned_keys(check)}
    p = Path(entry['policy']['path']); value = yaml.safe_load(p.read_bytes())
    value['scope']['include'], value['values'] = ['PLT-12'], {'PLT-12': values}
    p.write_text(yaml.safe_dump(value)); entry['policy']['sha256'] = sha256(p.read_bytes()).hexdigest()
    p = Path(entry['configuration']['path']); target = p.parent / 'target.json'
    producer = p.parent / 'copy-producer.py'
    producer.write_bytes(b'# EXAMPLE credential-copy record producer\n')
    inventory_path = p.parent / 'copy-inventory.json'
    inventory_path.write_text(json.dumps(inventory))
    config = "[executors]\nrequired_event_record='kubernetes'\n[required_event_record]\ntarget=" + json.dumps(str(target)) + '\n'
    config += 'copy_inventory=' + json.dumps(str(inventory_path)) + '\n'
    if record_session:
        config += "[sides.baseline.run_binding.PLT-12]\nrecord_session=" + json.dumps(record_session) + '\n'
        config += 'record_producer=' + json.dumps(str(producer)) + '\n'
    p.write_text(config); entry['configuration']['sha256'] = sha256(p.read_bytes()).hexdigest()
    result = run(tmp_path, data)
    row = next(r for r in result['rows'] if r['check_id'] == 'PLT-12' and r['side'] == 'baseline')
    if record_session:
        assert row['file_binding_valid'] and row['binding_schema'] == 'guard-run-binding/2'
        assert row['binding_files']['copy_target_sha256']
    else:
        assert not row['file_binding_valid'] and row['blocking_codes'] == ['RUN_BINDING_CONFIG_INVALID']


@pytest.mark.parametrize('file_key', ['target', 'retry_scope', 'copy_inventory', 'subjects', 'selection',
                                     'record_producer', 'gateway_producer', 'engine_producer', 'issuer_event'])
def test_target_change_during_binding_is_rejected(tmp_path, monkeypatch, sources, file_key):
    from pathlib import Path
    import tomllib
    from tools.j02_run_config import write_config
    original = tool.run_binding.effective_values
    baseline_target = Path(sources['inputs'][0]['configuration']['path']).parent / 'target.json'
    if file_key != 'target':
        configuration = sources['inputs'][0]['configuration']
        config_path = Path(configuration['path'])
        config = tomllib.loads(config_path.read_text())
        baseline_target = config_path.parent / (file_key + '.json')
        baseline_target.write_bytes(b'{"example":true}\n')
        config['input_validation'][file_key] = str(baseline_target)
        write_config(config, config_path)
        configuration['sha256'] = sha256(config_path.read_bytes()).hexdigest()
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs['side'] == 'baseline':
            baseline_target.write_bytes(baseline_target.read_bytes() + b' ')
        return result
    monkeypatch.setattr(tool.run_binding, 'effective_values', changed)
    report = run(tmp_path, sources)
    row = next(r for r in report['rows'] if r['check_id'] == 'SUP-08' and r['side'] == 'baseline')
    assert row['blocking_codes'] == ['MA7_INPUT_FILE_CHANGED'] and not row['file_binding_valid']
    assert report['file_binding_valid_sides'] == 1
