"""All active sides stay visible; offline and synthetic inputs never become live proof."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess

import pytest

from guard.models import load_pack
from tools import mvp_live_readiness as tool
from tests.test_mvp_a_input_preflight import sources  # noqa: F401


def run(tmp_path, value):
    index = tmp_path / 'index.json'
    index.write_text(json.dumps(value), encoding='utf-8')
    return tool.audit(index)


def blank():
    return tool.template(load_pack(tool.ROOT / 'packs/PACK-BASELINE'))


def test_all_69_checks_and_138_sides_remain_visible_without_network_or_execution(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('offline readiness must not access a target or execute commands')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    report = run(tmp_path, blank())
    assert report['active_checks'] == 69 and len(report['rows']) == report['sides'] == 138
    assert len({(r['check_id'], r['side']) for r in report['rows']}) == 138
    assert all('ACTUAL_INPUT_FILES_MISSING' in r['blocking_codes'] for r in report['rows'])
    assert report['blocking_counts']['SECURITY_RULES_EMPTY'] == 2
    assert {r['check_id'] for r in report['rows'] if not r['security_rules_present']} == {'SUP-03'}
    assert all(r['engine_executed'] is r['ready_for_execution'] is r['live_target_verified'] is False
               and r['actual_results'] is None for r in report['rows'])
    assert report['engine_executed'] is report['ready_for_execution'] is False


@pytest.mark.parametrize('damage', ['pack', 'retired', 'side', 'duplicate', 'extra'])
def test_invalid_or_ambiguous_sources_are_rejected(tmp_path, damage):
    value = blank()
    item = dict(check_id='SUP-08', side='baseline', environment=None, policy=None, configuration=None)
    value['inputs'] = [item]
    if damage == 'pack': value['pack_ref']['revision'] -= 1
    if damage == 'retired': item['check_id'] = 'SUP-01'
    if damage == 'side': item['side'] = 'single'
    if damage == 'duplicate': value['inputs'].append(deepcopy(item))
    if damage == 'extra': item['ready_for_execution'] = True
    with pytest.raises(ValueError, match='MVP_SOURCE_'):
        run(tmp_path, value)


def test_supplied_file_hash_mismatch_is_recorded_without_hiding_other_137_sides(tmp_path):
    value = blank();path = tmp_path / 'env.json';path.write_text('{}')
    pointer = {'path': str(path), 'sha256': '0' * 64}
    value['inputs'] = [dict(check_id='SUP-08', side='baseline', environment=pointer,
                            policy=pointer, configuration=pointer)]
    report = run(tmp_path, value)
    damaged = next(r for r in report['rows'] if (r['check_id'], r['side']) == ('SUP-08', 'baseline'))
    assert damaged['blocking_codes'] == ['MVP_INPUT_HASH_MISMATCH']
    assert report['blocking_counts']['ACTUAL_INPUT_FILES_MISSING'] == 137


def test_example_file_binding_does_not_issue_live_readiness(tmp_path, sources):  # noqa: F811
    value = blank();value['inputs'] = sources['inputs']
    report = run(tmp_path, value)
    bound = [r for r in report['rows'] if r['file_binding_valid']]
    assert report['file_binding_valid_sides'] == len(bound) == 2
    assert all('EXAMPLE_ENV_NOT_ACTUAL' in r['blocking_codes'] for r in bound)
    assert all('LIVE_EXECUTOR_PREFLIGHT_REQUIRED' in r['blocking_codes'] for r in bound)
    assert all(not r['ready_for_execution'] and r['actual_results'] is None for r in bound)
    assert report['blocking_counts']['ACTUAL_INPUT_FILES_MISSING'] == 136


def test_fake_executor_cannot_be_an_active_execution_input(tmp_path, sources):  # noqa: F811
    value = blank();value['inputs'] = sources['inputs'][:1]
    pointer = value['inputs'][0]['configuration'];path = Path(pointer['path'])
    path.write_bytes(path.read_bytes().replace(b'kubernetes', b'fake'))
    pointer['sha256'] = sha256(path.read_bytes()).hexdigest()
    report = run(tmp_path, value)
    row = next(r for r in report['rows'] if (r['check_id'], r['side']) == ('SUP-08', 'baseline'))
    assert row['file_binding_valid'] is False
    assert row['blocking_codes'] == ['MVP_ACTIVE_EXECUTOR_REQUIRED']


def test_typed_but_unsupported_policy_is_refused_by_the_actual_executor_before_network(tmp_path,sources,monkeypatch):
    import yaml
    def forbidden(*args,**kwargs):raise AssertionError('offline admission must not execute or contact a target')
    monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
    value=blank();value['inputs']=sources['inputs'][:1]
    entry=value['inputs'][0];path=Path(entry['policy']['path']);policy=yaml.safe_load(path.read_bytes())
    policy['values']['SUP-08']['code_selection_fields']=['invalid..field']
    path.write_text(yaml.safe_dump(policy));entry['policy']['sha256']=sha256(path.read_bytes()).hexdigest()
    report=run(tmp_path,value)
    row=next(r for r in report['rows']if(r['check_id'],r['side'])==('SUP-08','baseline'))
    assert row['file_binding_valid']is False and row['blocking_codes']==['MVP_EXECUTOR_POLICY_INVALID']
    assert len(report['rows'])==138 and report['blocking_counts']['ACTUAL_INPUT_FILES_MISSING']==137
    assert not report['engine_executed']and not report['ready_for_execution']


@pytest.mark.parametrize('capture_present',[False,True])
def test_current_matrix_admission_checks_effective_check_config_without_live_success(tmp_path,monkeypatch,capture_present):
    import yaml
    from guard.models import canonical_json
    from tests.builders import policy
    from tests.test_input_matrix import values
    from tests.test_model_preparation_integration import full_inputs
    def forbidden(*args,**kwargs):raise AssertionError('no target or engine during file admission')
    monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(subprocess,'Popen',forbidden)
    pack=load_pack(tool.ROOT/'packs/PACK-BASELINE');expected=tool.versions()['candidate'];env,target=full_inputs()
    env=env.model_copy(update={'engine':env.engine.model_copy(update={'name':expected['engine'],'version':expected['version'],'image_digest':expected['image']}),
        'runtime':env.runtime.model_copy(update={'gpu_count':2,'gpu_model':'EXAMPLE-GPU','driver':'EXAMPLE-driver'})}).seal()
    target['environment_ref']=env.ref().model_dump(mode='json')
    pol=policy(pack.ref(),scope={'include':['REQ-09']},values={'REQ-09':values()})
    files={}
    for role,obj in [('environment',env.to_dict()),('policy',pol.to_dict()),('target',target)]:
        file=tmp_path/(role+'.json');file.write_bytes(canonical_json(obj));files[role]=file
    cfg=tmp_path/'guard.toml'
    text='[executors]\ninput_validation="kubernetes"\n[adapter]\nname="vllm"\n[input_validation]\ntarget='+json.dumps(str(files['target']))+'\nvocab_size=100\nvocab_source="EXAMPLE"\n'
    if capture_present:text+='[check_config.REQ-09.native_input_capture]\nprepared_dir="EXAMPLE-not-installed"\nprepared_sha256="'+'a'*64+'"\n'
    cfg.write_text(text);files['configuration']=cfg
    entry={'check_id':'REQ-09','side':'candidate',**{role:{'path':str(files[role]),'sha256':sha256(files[role].read_bytes()).hexdigest()}
        for role in('environment','policy','configuration')}}
    index=tmp_path/'index.json';index.write_text(json.dumps({**blank(),'inputs':[entry]}))
    result=tool.audit(index,side='candidate');row=next(r for r in result['rows']if r['check_id']=='REQ-09')
    assert row['file_binding_valid']is capture_present and len(result['rows'])==69
    if capture_present:
        assert row['executor_policy_valid']is True and 'EXAMPLE_ENV_NOT_ACTUAL'in row['blocking_codes']
        assert 'LIVE_EXECUTOR_PREFLIGHT_REQUIRED'in row['blocking_codes']
    else:assert row['blocking_codes']==['MVP_EXECUTOR_POLICY_INVALID']
    assert not row['ready_for_execution']and not row['engine_executed']and row['actual_results']is None
