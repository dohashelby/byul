"""Explicit OBS01 public transport; old check IDs/profiles are not aliases."""
from copy import deepcopy
import pytest
from guard.runtime.kubernetes_http import KubernetesHTTP,KubernetesBindingError,REQUIRED_EVENT_PROFILE,MODEL_AUTHORIZATION_PROFILE,REQUEST_BINDING_PROFILE
from tests.test_a1_required_event_product import inputs,ALLOWED,DENIED


def request(slot,credential=ALLOWED):
    return {'id':'obs01-'+slot,'mode':'json','method':'POST','url':'http://10.0.0.1:8000/v1/chat/completions',
        'headers':{'X-Request-Id':'byul-obs01-'+'a1'*16+'-'+slot,'Authorization':'Bearer '+credential},
        'body':{'model':'fixture-model','messages':[{'role':'user','content':'hello'}],'max_tokens':1,'stream':False}}


def prepared(tmp_path,profile=REQUIRED_EVENT_PROFILE):
    environment,world,values,_,_=inputs(tmp_path)
    transport=KubernetesHTTP(world.target,environment,duration=30,runner=world.runner,profile=profile)
    base=world.target['engine']['trusted_url']
    return transport,world,base


@pytest.mark.parametrize('damage',['foreign-id','foreign-header','method','path','query','fragment','body','model','stream','tokens','extra-body',
    'extra-header','missing-header','no-auth','large-auth','duplicate-batch','late-first','before-denied','different-token','same-credential','after-credential','duplicate','timeout'])
def test_closed_request_and_sequence_reject_before_unapproved_dispatch(tmp_path,damage):
    transport,world,base=prepared(tmp_path);r=request('before');r['url']=base+'/v1/chat/completions';sent_before=0
    if damage in {'different-token','same-credential','after-credential','duplicate'}:
        transport('kubernetes-probe',[deepcopy(r)],10);sent_before=1
        if damage=='duplicate':pass
        else:
            r=request('after' if damage=='after-credential' else 'denied',DENIED);r['url']=base+'/v1/chat/completions'
            if damage=='different-token':r['headers']['X-Request-Id']=r['headers']['X-Request-Id'].replace('a1'*16,'b2'*16)
            if damage=='same-credential':r['headers']['Authorization']='Bearer '+ALLOWED
    elif damage=='foreign-id':r['id']='req05-model-before'
    elif damage=='foreign-header':r['headers']['X-Request-Id']='byul-req05-model-'+'a1'*16+'-before'
    elif damage=='method':r['method']='GET'
    elif damage=='path':r['url']=base+'/v1/responses'
    elif damage=='query':r['url']+='?store=true'
    elif damage=='fragment':r['url']+='#private'
    elif damage=='body':r['body']['messages'][0]['content']='private'
    elif damage=='model':r['body']['model']='other'
    elif damage=='stream':r['body']['stream']=True
    elif damage=='tokens':r['body']['max_tokens']=2
    elif damage=='extra-body':r['body']['store']=True
    elif damage=='extra-header':r['headers']['X-Forwarded-User']='admin'
    elif damage=='missing-header':r['headers'].pop('X-Request-Id')
    elif damage=='no-auth':r['headers']['Authorization']=''
    elif damage=='large-auth':r['headers']['Authorization']='Bearer '+'x'*4097
    elif damage in {'late-first','before-denied'}:
        r=request('after' if damage=='late-first' else 'denied',DENIED);r['url']=base+'/v1/chat/completions'
    with pytest.raises(KubernetesBindingError):transport('kubernetes-probe',[r,r] if damage=='duplicate-batch' else [r],11 if damage=='timeout' else 10)
    assert len(world.sent)==sent_before
    assert ALLOWED not in str(transport.report()) and DENIED not in str(transport.report())


@pytest.mark.parametrize('profile',[MODEL_AUTHORIZATION_PROFILE,REQUEST_BINDING_PROFILE])
def test_previous_profiles_do_not_consume_obs01_ids(tmp_path,profile):
    transport,world,base=prepared(tmp_path,profile);r=request('before');r['url']=base+'/v1/chat/completions'
    with pytest.raises(KubernetesBindingError):transport('kubernetes-probe',[r],10)
    assert not world.sent


def test_public_three_case_contract_uses_same_model_and_distinct_credential(tmp_path):
    transport,world,base=prepared(tmp_path)
    for slot,key in (('before',ALLOWED),('denied',DENIED),('after',ALLOWED)):
        r=request(slot,key);r['url']=base+'/v1/chat/completions';row,=transport('kubernetes-probe',[r],10)
        assert row['outcome']==(403 if slot=='denied' else 200)
    assert world.sent==['obs01-before','obs01-denied','obs01-after'] and not transport.report()['collection_gap']
