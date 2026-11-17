"""A missing release ack preserves only separately verified, incomplete facts."""
from copy import deepcopy
from hashlib import sha256
import hmac
import json
import shutil

import pytest

from guard.adapters import vllm_rank_rpc_control as control
from guard.adapters import vllm_rank_rpc_input as adapter
from guard.collectors import gpu_common_inputs as common, rank_rpc_fault_producer as producer
from tests.test_rank_rpc_reply_stall_product import original
from tests.test_rank_rpc_fault_producer import examples
from tools import rank_rpc_fault_inputs as tool


@pytest.fixture
def missing_release(original,tmp_path):
    prepared,capture,binding=original
    copied=tmp_path/'capture';shutil.copytree(capture,copied)
    released=[p for p in(copied/'snapshots').glob('pid-*.json')
        if json.loads(p.read_bytes())['snapshot']['payload'].get('event')=='CONTROL_RELEASE']
    assert len(released)==1
    released[0].unlink()
    return prepared,copied,deepcopy(binding)


def resign(data,name,mutate):
    prepared,capture,_=data;path=capture/'controls'/name
    value=json.loads(path.read_bytes());mutate(value['payload'])
    key=bytes.fromhex(json.loads((prepared/'settings.json').read_bytes())['key'])
    value['hmac_sha256']=hmac.new(key,control.DOMAIN+control.canonical(value['payload']),sha256).hexdigest()
    path.write_bytes(control.canonical(value))


def test_strict_projection_still_rejects_release_gap_and_opt_in_keeps_only_applied(missing_release):
    with pytest.raises(ValueError):adapter.project(*missing_release)
    value=adapter.project_stall_with_release_gap(*missing_release)
    assert [row['event']for row in value['events']]==['fault_applied']
    assert value['events'][0]['ack']is True
    assert 'RANK_RPC_RELEASE_UNOBSERVED'in value['diagnostic_codes']
    assert all(value[key]is False for key in('scope_complete','source_authenticated','engine_group_verified','producer_registered'))
    assert value['client_observation']['upper_delivery_verified']is False
    assert not any(row['event']in{'fault_released','detected','client_delivered','recovered'}for row in value['events'])


def test_opt_in_still_requires_actual_ack_before_representing_a_release(original):
    value=adapter.project_stall_with_release_gap(*original)
    assert [row['event']for row in value['events']]==['fault_applied','fault_released']
    assert 'RANK_RPC_RELEASE_UNOBSERVED'not in value['diagnostic_codes']
    assert value['scope_complete']is value['engine_group_verified']is value['producer_registered']is False


@pytest.mark.parametrize('damage',['wrong-action','wrong-queue','barrier-only','duplicate'])
def test_present_invalid_release_ack_is_not_treated_as_an_unobserved_gap(original,tmp_path,damage):
    prepared,capture,binding=original
    copied=tmp_path/'capture';shutil.copytree(capture,copied)
    path,=[path for path in(copied/'snapshots').glob('pid-*.json')
        if json.loads(path.read_bytes())['snapshot']['payload'].get('event')=='CONTROL_RELEASE']
    value=json.loads(path.read_bytes());payload=value['snapshot']['payload']
    if damage=='duplicate':(path.parent/'pid-1-1-999999.json').write_bytes(path.read_bytes())
    else:
        if damage=='wrong-action':payload['control']['action_sha256']='f'*64
        elif damage=='wrong-queue':payload['endpoints']['response']['identity_hmac_sha256']='f'*64
        else:payload['control']['barrier_only']=False
        key=bytes.fromhex(json.loads((prepared/'settings.json').read_bytes())['key'])
        value['hmac_sha256']=hmac.new(key,control.canonical(value['snapshot']),sha256).hexdigest()
        path.write_bytes(control.canonical(value))
    with pytest.raises(ValueError):adapter.project_stall_with_release_gap(prepared,copied,binding)


@pytest.mark.parametrize('damage',['missing-stall','bad-stall-hmac','wrong-queue','barrier-only',
    'release-hmac','release-nonce','release-expired','client-namespace','product-binding'])
def test_partial_mode_does_not_accept_missing_application_or_invalid_signed_metadata(missing_release,damage):
    prepared,capture,binding=missing_release
    if damage in{'missing-stall','bad-stall-hmac','wrong-queue','barrier-only'}:
        paths=[p for p in(capture/'snapshots').glob('pid-*.json')
            if json.loads(p.read_bytes())['snapshot']['payload'].get('event')=='CONTROL_STALL']
        assert len(paths)==1
        path,=paths
        if damage=='missing-stall':path.unlink()
        else:
            value=json.loads(path.read_bytes());payload=value['snapshot']['payload']
            key=bytes.fromhex(json.loads((prepared/'settings.json').read_bytes())['key'])
            if damage=='wrong-queue':payload['endpoints']['response']['identity_hmac_sha256']='f'*64
            elif damage=='barrier-only':payload['control']['barrier_only']=True
            value['hmac_sha256']=('0'*64 if damage=='bad-stall-hmac'else
                hmac.new(key,control.canonical(value['snapshot']),sha256).hexdigest())
            path.write_bytes(control.canonical(value))
    elif damage=='release-hmac':
        path=capture/'controls/control-stall-release.json';value=json.loads(path.read_bytes())
        value['hmac_sha256']='0'*64;path.write_bytes(control.canonical(value))
    elif damage=='release-nonce':resign(missing_release,'control-stall-release.json',lambda row:row.update(nonce='0'*32))
    elif damage=='release-expired':
        arm=json.loads((capture/'controls/control-arm.json').read_bytes())['payload']
        resign(missing_release,'control-physical-resume.json',lambda row:row.update(published_ns=arm['expires_ns']+1))
        resume=json.loads((capture/'controls/control-physical-resume.json').read_bytes())
        resign(missing_release,'control-case-result.json',lambda row:row.update(physical_resume=resume['payload']))
        resign(missing_release,'control-stall-release.json',lambda row:row.update(
            receipt_sha256=sha256(control.canonical(resume)).hexdigest()))
    elif damage=='client-namespace':
        resign(missing_release,'control-case-result.json',lambda row:row['client']['context'].update(pid_namespace=0))
    else:binding['target_sha256']='0'*64
    with pytest.raises(ValueError):adapter.project_stall_with_release_gap(prepared,capture,binding)


@pytest.mark.parametrize('wire',['guard-gpu-common-input/1','guard-gpu-common-input/4'])
def test_partial_stall_enters_common_input_as_unregistered_and_incomplete(missing_release,wire):
    prepared,capture,binding=missing_release
    kwargs={key:binding[key]for key in('environment_ref','target_sha256','prepared_sha256','session')}
    kwargs['pod']={key:binding[key]for key in('pod_uid','container_id','restart_count')}
    with pytest.raises(ValueError):producer.record(prepared,capture,**kwargs,wire_schema=wire)
    value,validation,projection=producer.record(prepared,capture,**kwargs,wire_schema=wire,allow_missing_stall_release=True)
    assert [row['event']for row in value['events']]==['fault_applied']
    assert value['completeness']['source_ended']is False
    assert 'RANK_RPC_RELEASE_UNOBSERVED'in value['completeness']['diagnostic_codes']
    assert validation['supported']is validation['complete']is validation['producer_authenticated']is False
    assert projection['engine_group_verified']is False
    assert (producer.NAME,producer.VERSION)not in common.SUPPORTED_PRODUCERS


@pytest.mark.parametrize('kind',['delay','death','exception'])
def test_missing_stall_release_option_cannot_expand_other_fault_scopes(examples,kind):
    with pytest.raises(ValueError,match='RANK_RPC_STALL_PARTIAL_SCOPE_REQUIRED'):
        adapter.project_stall_with_release_gap(*examples[kind])


def test_input_tool_requires_explicit_mode_and_keeps_source_gap(missing_release,tmp_path,monkeypatch):
    prepared,capture,binding=missing_release
    monkeypatch.setattr(tool,'binding_for',lambda *args:(binding,{},'EXAMPLE'))
    output=tmp_path/'strict'
    with pytest.raises(ValueError):tool.prepare(prepared,capture,'EXAMPLE-env','EXAMPLE-target',output)
    assert not output.exists()
    output=tmp_path/'partial'
    result=tool.prepare(prepared,capture,'EXAMPLE-env','EXAMPLE-target',output,allow_missing_stall_release=True)
    assert result['data_origin']=='EXAMPLE'
    assert result['validation']['supported']is result['validation']['complete']is False
    assert result['ready_for_execution']is result['engine_executed']is result['live_target_verified']is False
    assert result['actual_results']is None
    assert result['common_input_sha256']==sha256((output/'common-input.json').read_bytes()).hexdigest()
    assert 'RANK_RPC_RELEASE_UNOBSERVED'in result['source_projection']['diagnostic_codes']


def test_rank_projection_changes_do_not_invalidate_the_unchanged_qualified_input_sdk():
    from guard.collectors.native_input_qualification import status
    result=status('vllm','0.30.0','docker.io/vllm/vllm-openai@sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40')
    assert result['registered']is True
    assert result['producer']['source_sha256']=='5c9e6a5c956b8f2ec0873e71941776eb7027b16b4fd185c4606dd2125938b5d6'
    assert result['full_check_scope_qualified']is result['whole69_complete']is False
