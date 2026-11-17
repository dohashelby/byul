"""EXAMPLE 입력만 쓰는 사전 결합·사후 귀속 거부 시험. GPU/클라우드 실행은 없다."""
from copy import deepcopy
from hashlib import sha256
import hmac
import json
import os
import socket
import subprocess

import pytest

from guard.adapters import vllm_rank_rpc_actor as actor
from guard.adapters import vllm_rank_rpc_control as control
from guard.models import Environment, canonical_json
from tests.test_mvp_sup08_inputs import source
from tests.test_rank_rpc_control import configured, arm_request, begin, ready
from tests.test_rank_rpc_hooks import setup as rpc_setup
from tools import rank_ipc_candidates as ipc
from tools import rank_rpc_product_binding as tool
from tools.mvp_versions import versions


def example(session='a' * 32):
    return {'schema': 'guard-rank-rpc-product-binding/1', 'session': session,
        'environment_ref': {'kind': 'ENV', 'id': 'ENV-EXAMPLE', 'revision': 1, 'sha256': '1' * 64},
        'target_sha256': '2' * 64, 'prepared_sha256': '3' * 64,
        'pod_uid': 'EXAMPLE-pod', 'container_id': '4' * 64, 'restart_count': 0}


def supply(control_object, monkeypatch, value=None):
    value = example(control_object.settings['session']) if value is None else value
    monkeypatch.setenv('GUARD_POD_UID', value['pod_uid'])
    monkeypatch.setenv('GUARD_CONTAINER_ID', value['container_id'])
    monkeypatch.setenv('GUARD_RESTART_COUNT', str(value['restart_count']))
    control_object.write('control-product-binding.json', value)
    return value


@pytest.fixture
def files(tmp_path, monkeypatch):
    _, environments, targets = source(tmp_path, monkeypatch)
    for side in environments:
        env = Environment.model_validate(json.loads(environments[side].read_bytes()))
        env.runtime.tp_size, env.runtime.gpu_count = 2, 2
        env.runtime.pp_size, env.runtime.dp_size = 1, 1
        env = env.seal()
        environments[side].write_bytes(canonical_json(env.to_dict()))
        target = json.loads(targets[side].read_bytes())
        target['environment_ref'] = env.ref().model_dump(mode='json')
        targets[side].write_bytes(canonical_json(target))
    prepared = tmp_path / 'rpc-prepared'
    ipc.prepare(prepared, image=versions()['baseline']['image'])
    return prepared, environments['baseline'], targets['baseline']


def test_file_binding_is_signed_before_arm_and_never_claims_execution(files, tmp_path, monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('준비 중 네트워크/프로세스 실행 금지')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    output = tmp_path / 'bound'
    result = tool.prepare(*files, output)
    raw = (output / 'control-product-binding.json').read_bytes()
    payload = json.loads(raw)
    settings = json.loads((files[0] / 'settings.json').read_bytes())
    assert payload['hmac_sha256'] == hmac.new(bytes.fromhex(settings['key']),
        control.DOMAIN + control.canonical(payload['payload']), sha256).hexdigest()
    assert result['binding'] == payload['payload']
    assert result['file_sha256'] == sha256(raw).hexdigest()
    assert result['binding']['prepared_sha256'] == sha256((files[0] / 'manifest.json').read_bytes()).hexdigest()
    assert result['binding']['target_sha256'] == sha256(canonical_json(json.loads(files[2].read_bytes()))).hexdigest()
    assert result['data_origin'] == 'EXAMPLE'
    assert all(result[key] is False for key in ('live_target_verified', 'input_preflight_passed',
        'engine_executed', 'scope_complete', 'producer_registered'))
    assert settings['key'] not in json.dumps(result) and settings['key'] not in raw.decode()
    assert (output / 'control-product-binding.json').stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        tool.prepare(*files, output)


def test_host_binding_explicitly_matches_target_owner_and_never_uses_ambient_root(files,tmp_path,monkeypatch):
    uid=(files[0]/'settings.json').stat().st_uid
    monkeypatch.setattr(tool.os,'geteuid',lambda:uid+1)
    with pytest.raises(ValueError,match='RANK_RPC_BINDING_PRIVATE_SETTINGS'):
        tool.prepare(*files,tmp_path/'wrong-default')
    result=tool.prepare(*files,tmp_path/'explicit-owner',expected_uid=uid)
    assert result['engine_executed']is result['scope_complete']is result['producer_registered']is False
    assert result['binding']['schema']=='guard-rank-rpc-product-binding/1'
    with pytest.raises(ValueError,match='RANK_RPC_BINDING_PRIVATE_SETTINGS'):
        tool.prepare(*files,tmp_path/'wrong-owner',expected_uid=uid+1)


@pytest.mark.parametrize('uid',[True,-1,2**31,'1000'])
def test_invalid_target_owner_cannot_prepare_product_binding(files,tmp_path,uid):
    with pytest.raises(ValueError,match='RANK_RPC_BINDING_PRIVATE_SETTINGS'):
        tool.prepare(*files,tmp_path/'invalid-owner',expected_uid=uid)


def test_fault_input_conversion_requires_the_explicit_original_target_owner(files,tmp_path,monkeypatch):
    from tools import rank_rpc_fault_inputs as inputs
    uid=(files[0]/'settings.json').stat().st_uid
    monkeypatch.setattr(tool.os,'geteuid',lambda:uid+1)
    reached=[]
    def source_required(*args,**kwargs):
        reached.append(kwargs)
        raise ValueError('EXAMPLE_ORIGINAL_CAPTURE_STILL_REQUIRED')
    monkeypatch.setattr(inputs.producer,'record',source_required)
    for owner in (None,uid+1,True,-1,2**31):
        with pytest.raises(ValueError,match='RANK_RPC_BINDING_PRIVATE_SETTINGS'):
            inputs.prepare(files[0],tmp_path/'absent-capture',*files[1:],tmp_path/'denied',expected_uid=owner)
        assert not reached and not (tmp_path/'denied').exists()
    with pytest.raises(ValueError,match='EXAMPLE_ORIGINAL_CAPTURE_STILL_REQUIRED'):
        inputs.prepare(files[0],tmp_path/'absent-capture',*files[1:],tmp_path/'selected',expected_uid=uid)
    assert len(reached)==1 and not (tmp_path/'selected').exists()
    assert reached[0]['pod']['pod_uid']==json.loads(files[2].read_bytes())['kubernetes']['engine']['pod_uid']


@pytest.mark.parametrize('damage', ['env_hash', 'tp1', 'gpu1', 'dp2', 'pp2', 'env_image', 'target_ref',
    'target_image', 'container', 'restart_bool', 'old_asset', 'settings_hash', 'symlink'])
def test_bad_inputs_are_rejected_before_output(files, tmp_path, damage):
    prepared, environment, target = files
    env = Environment.model_validate(json.loads(environment.read_bytes()))
    document = json.loads(target.read_bytes())
    if damage == 'env_hash':
        env.content_sha256 = '0' * 64
    elif damage in {'tp1', 'gpu1', 'dp2', 'pp2', 'env_image'}:
        if damage == 'tp1': env.runtime.tp_size = 1
        elif damage == 'gpu1': env.runtime.gpu_count = 1
        elif damage == 'dp2': env.runtime.dp_size = 2
        elif damage == 'pp2': env.runtime.pp_size = 2
        else: env.engine.image_digest = versions()['candidate']['image']
        env = env.seal()
        document['environment_ref'] = env.ref().model_dump(mode='json')
    elif damage == 'target_ref': document['environment_ref']['sha256'] = '0' * 64
    elif damage == 'target_image': document['kubernetes']['engine']['image_id'] = 'unqualified-image'
    elif damage == 'container': document['kubernetes']['engine']['container_id'] = 'wrong'
    elif damage == 'restart_bool': document['kubernetes']['engine']['restart_count'] = False
    elif damage == 'old_asset':
        (prepared / 'guard/adapters/vllm_rank_rpc_actor.py').write_text('# 이전 bytes\n')
    elif damage == 'settings_hash':
        value = json.loads((prepared / 'settings.json').read_bytes())
        value['session'] = '0' * 32
        (prepared / 'settings.json').write_bytes(control.canonical(value))
    environment.write_bytes(canonical_json(env.to_dict()))
    target.write_bytes(canonical_json(document))
    if damage == 'symlink':
        link = tmp_path / 'link.json'; link.symlink_to(target); target = link
    output = tmp_path / 'denied'
    with pytest.raises((ValueError, OSError)):
        tool.prepare(prepared, environment, target, output)
    assert not output.exists()


def test_bound_arm_requires_external_signed_binding_and_keeps_barrier_semantics(configured, monkeypatch):
    _, _, rows, inner, _, _, instance, _ = configured
    binding = supply(instance, monkeypatch)
    arm, arm_sha = arm_request(configured,
        change=lambda arm: arm.update(schema='guard-rank-rpc-arm/2', product_binding=binding))
    thread, outcome = begin(configured)
    receipt, receipt_sha = ready(instance)
    assert thread.is_alive() and receipt['arm_sha256'] == arm_sha
    instance.write('control-action.json', {'schema': 'guard-rank-rpc-action/1', 'nonce': arm['nonce'],
        'arm_sha256': arm_sha, 'ready_sha256': receipt_sha, 'action': 'release', 'receipt_sha256': '4' * 64})
    thread.join(3)
    assert outcome == [('returned', inner.result)] and not thread.is_alive()
    assert [p['event'] for p in rows if p.get('event', '').startswith('CONTROL_')] == ['CONTROL_READY', 'CONTROL_RELEASE']


@pytest.mark.parametrize('damage', ['missing', 'hmac', 'session', 'pod', 'container', 'restart',
    'env_ref', 'target', 'prepared', 'boolean', 'extra'])
def test_unbound_arm_never_blocks_original_or_creates_ready(configured, tmp_path, monkeypatch, damage):
    _, _, _, inner, _, _, instance, _ = configured
    binding = supply(instance, monkeypatch)
    changed = deepcopy(binding)
    if damage == 'missing': (tmp_path / 'data/control-product-binding.json').unlink()
    elif damage == 'hmac':
        p = tmp_path / 'data/control-product-binding.json'
        envelope = json.loads(p.read_bytes()); envelope['hmac_sha256'] = '0' * 64
        p.write_bytes(control.canonical(envelope))
    elif damage in {'pod', 'container', 'restart'}:
        key = {'pod': 'GUARD_POD_UID', 'container': 'GUARD_CONTAINER_ID', 'restart': 'GUARD_RESTART_COUNT'}[damage]
        monkeypatch.setenv(key, 'wrong')
    elif damage == 'session': changed['session'] = '0' * 32
    elif damage == 'env_ref': changed['environment_ref']['sha256'] = '0' * 64
    elif damage == 'target': changed['target_sha256'] = '0' * 64
    elif damage == 'prepared': changed['prepared_sha256'] = '0' * 64
    elif damage == 'boolean': changed['restart_count'] = False
    elif damage == 'extra': changed['source_authenticated'] = True
    arm_request(configured, change=lambda arm: arm.update(schema='guard-rank-rpc-arm/2', product_binding=changed))
    thread, outcome = begin(configured); thread.join(3)
    assert not thread.is_alive() and outcome == [('returned', inner.result)]
    with pytest.raises(FileNotFoundError): instance.read('control-ready.json')


def test_actor_refuses_binding_mismatch_before_pidfd_http_or_arm(configured, monkeypatch):
    *_, instance, _ = configured
    binding = supply(instance, monkeypatch)
    def forbidden(*a, **kw):
        raise AssertionError('잘못된 결합은 신호/요청/ARM 전에 거부해야 한다')
    monkeypatch.setattr(actor, 'SignalTarget', forbidden)
    monkeypatch.setattr(actor, 'post', forbidden)
    wrong = deepcopy(binding); wrong['target_sha256'] = '0' * 64
    calibration = {'proof': configured[-1], 'initial_capture_sha256': '9' * 64}
    with pytest.raises(ValueError, match='PRODUCT_BINDING_MISMATCH'):
        actor.exercise(instance, calibration, 'EXAMPLE-model', 'delay', {'pod_uid': binding['pod_uid']}, product_binding=wrong)
    with pytest.raises(FileNotFoundError): instance.read('control-arm.json')


def test_legacy_case_cannot_be_given_posthoc_environment_or_target():
    binding = example()
    case = {'arm': {'schema': 'guard-rank-rpc-arm/1'}, 'target': {'schema': 'guard-rank-rpc-lab-target/1'}}
    with pytest.raises(ValueError, match='PRODUCT_BINDING_REQUIRED'):
        tool.require_case_binding(case, binding)
    target = {'schema': 'guard-rank-rpc-lab-target/2', 'product_binding': binding,
        'owner': {'pod_uid': binding['pod_uid']}}
    case = {'target': target, 'arm': {'schema': 'guard-rank-rpc-arm/2', 'session': binding['session'],
        'product_binding': deepcopy(binding), 'target_sha256': control.digest(target)}}
    assert tool.require_case_binding(case, binding) is None
    changed = deepcopy(binding); changed['prepared_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='PRODUCT_BINDING_MISMATCH'):
        tool.require_case_binding(case, changed)
    case['target']['owner']['pod_uid'] = 'changed-after-arm'
    with pytest.raises(ValueError, match='PRODUCT_BINDING_MISMATCH'):
        tool.require_case_binding(case, binding)
