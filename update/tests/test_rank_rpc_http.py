"""EXAMPLE asynchronous send calls; never network or GPU delivery evidence."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
import sys
import time
import types

import pytest

from guard.adapters import vllm_rank_ipc_hooks as ipc
from guard.adapters import vllm_rank_ipc_sources as sources
from guard.adapters import vllm_rank_rpc_hooks as rpc
from guard.adapters import vllm_rank_rpc_input as project

RAW=b'''calls=[]
class Cycle:
    async def send(self, message):
        calls.append(message['type'])
        if self.change:
            message['status']=201
        if self.error is not None:
            raise self.error
        return None
'''


@pytest.fixture
def setup(tmp_path,monkeypatch):
    name='owned_http_fixture';path=tmp_path/'fixture.py';path.write_bytes(RAW)
    module=types.ModuleType(name);module.__file__=str(path)
    exec(compile(RAW,str(path),'exec',dont_inherit=True,optimize=0),vars(module));monkeypatch.setitem(sys.modules,name,module)
    point='Cycle.send';code=sources._codes(compile(RAW,'fixture.py','exec',dont_inherit=True,optimize=0))[point]
    row={'path':'fixture.py','bytes':len(RAW),'sha256':sha256(RAW).hexdigest(),'functions':[point],
        'body_code_sha256':{point:sources.code_digest(code)},'hooks':{point:{'kind':'asgi-send','phase':'around-original',
            'role':'EXAMPLE','source_modules':[name],'paths':['/v1/completions']}}}
    profile={'id':'EXAMPLE-http','image':'fixture-image','runtime':sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3])),
        'sources':{},'rpc_sources':{name:row}}
    catalog=tmp_path/'profiles.json';catalog.write_text(json.dumps({'schema':'guard-rank-ipc-source-candidates/1','profiles':[profile]}))
    monkeypatch.setattr(sources,'PROFILES',catalog)
    rows=[];identity=ipc.IPCObserver(image='fixture-image',key=b'K'*32,emit=rows.append)
    observer=rpc.RPCObserver(image='fixture-image',key=b'K'*32,emit=rows.append,identity_observer=identity);observer.attach(module)
    owner=module.Cycle();owner.scope={'type':'http','method':'POST','path':'/v1/completions','headers':[(b'x-request-id',b'EXAMPLE-id')]}
    owner.error=None;owner.change=False
    return module,observer,rows,owner


def invoke(owner,message):return asyncio.run(owner.send(message))


def test_actual_await_preserves_body_and_only_reads_bounded_status_metadata(setup):
    module,observer,rows,owner=setup
    class Secret:
        def __repr__(self):raise AssertionError('body repr forbidden')
        def __len__(self):raise AssertionError('body len forbidden')
        def __iter__(self):raise AssertionError('body iteration forbidden')
    body=Secret();invoke(owner,{'type':'http.response.start','status':500,'headers':[(b'secret',body)]})
    invoke(owner,{'type':'http.response.body','body':body,'more_body':False})
    assert module.calls==['http.response.start','http.response.body']
    assert [r['event']for r in rows]==['CALL','RETURN','CALL','RETURN']
    assert rows[0]['object_hmac_sha256']==rows[-1]['object_hmac_sha256']
    assert rows[1]['http_send']['status']==500 and rows[-1]['http_send']['more_body']is False
    assert all(r['producer_registered']is r['engine_group_verified']is r['source_authenticated']is False for r in rows)
    assert b'EXAMPLE-id'not in json.dumps(rows).encode()


def test_original_exception_object_is_not_replaced_or_retried(setup):
    module,observer,rows,owner=setup;owner.error=RuntimeError('PRIVATE_ERROR')
    with pytest.raises(RuntimeError)as caught:invoke(owner,{'type':'http.response.start','status':500})
    assert caught.value is owner.error and module.calls==['http.response.start']
    assert [r['event']for r in rows]==['CALL','RAISE']and 'PRIVATE_ERROR'not in json.dumps(rows)


def test_changed_message_refuses_success_record_after_original_send(setup):
    module,observer,rows,owner=setup;owner.change=True;message={'type':'http.response.start','status':500}
    invoke(owner,message)
    assert message['status']==201 and module.calls==['http.response.start']
    assert rows[0]['event']=='CALL'and rows[-1]['diagnostic_code']=='RANK_HTTP_MESSAGE_CHANGED'
    assert not any(r.get('event')=='RETURN'for r in rows)


@pytest.mark.parametrize('damage',['duplicate-header','non-ascii','bool-status','bool-more-body','scope-property'])
def test_invalid_observation_fails_open_to_one_original_call(setup,damage):
    module,observer,rows,owner=setup;message={'type':'http.response.start','status':500}
    if damage=='duplicate-header':owner.scope['headers']*=2
    elif damage=='non-ascii':owner.scope['headers']=[(b'x-request-id',b'\xff')]
    elif damage=='bool-status':message['status']=True
    elif damage=='bool-more-body':message={'type':'http.response.body','more_body':1}
    else:
        class Trap(module.Cycle):
            @property
            def scope(self):raise AssertionError('scope property forbidden')
        owner=Trap();owner.error=None;owner.change=False
    invoke(owner,message)
    assert len(module.calls)==1 and rows[-1]['gaps']and not any(r.get('event')=='RETURN'for r in rows)


def delivery(setup,status=500):
    module,observer,rows,owner=setup;context=ipc.base._context();started=time.monotonic_ns()
    header={'context':context,'observation_ns':time.monotonic_ns()};applied=time.monotonic_ns()
    invoke(owner,{'type':'http.response.start','status':status});invoke(owner,{'type':'http.response.body'})
    client={'complete':True,'status':status,'header_hmac_sha256':rows[0]['request_hmac_sha256'][0],
        'context':context,'request_started_ns':started,'response_finished_ns':time.monotonic_ns()}
    return observer,rows,header,client,applied


@pytest.mark.parametrize('status',[200,500])
def test_native_send_and_client_eof_stay_separate_from_fault_cause_and_recovery(setup,status):
    observer,rows,header,client,applied=delivery(setup,status)
    result=project.http_delivery(rows,observer.profile,header,client,applied)
    assert result['source_start_written']and result['source_final_body_written']and result['client_eof_correlated']
    assert result['error_response_delivered_observed']==(status==500)and result['status']==status
    assert len(result['native_source_refs'])==2
    assert all(result[k]is False for k in('fault_causality_verified','recovery_verified','whole_group_verified'))


@pytest.mark.parametrize('damage',['partial-client','missing-final','wrong-request','status','generation','cycle','call-metadata','duplicate-start','before-applied'])
def test_client_status_or_unpaired_native_sends_cannot_be_promoted(setup,damage):
    observer,rows,header,client,applied=delivery(setup)
    if damage=='partial-client':client['complete']=False
    elif damage=='missing-final':rows[:]=rows[:2]
    elif damage=='wrong-request':client['header_hmac_sha256']='0'*64
    elif damage=='status':client['status']=200
    elif damage=='generation':header['context']={**header['context'],'start_ticks':header['context']['start_ticks']+1}
    elif damage=='cycle':rows[-1]['object_hmac_sha256']='0'*64
    elif damage=='call-metadata':rows[-2]['http_send']={**rows[-2]['http_send'],'more_body':True}
    elif damage=='duplicate-start':rows.append(deepcopy(rows[1]))
    else:applied=client['response_finished_ns']
    if damage in{'partial-client','missing-final','wrong-request'}:
        result=project.http_delivery(rows,observer.profile,header,client,applied)
        assert not result['client_eof_correlated']and not result['error_response_delivered_observed']
        assert result['source_start_written']==(damage!='wrong-request')
        assert result['source_final_body_written']==(damage=='partial-client')
    else:
        with pytest.raises(ValueError):project.http_delivery(rows,observer.profile,header,client,applied)
