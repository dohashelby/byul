"""Owned stdlib Futures: request binding, original exceptions and weak lifetime."""
from concurrent.futures import Future
from copy import deepcopy
from hashlib import sha256
import gc
import json
import sys
import types
import time
import weakref

import pytest

from guard.adapters import vllm_rank_ipc_hooks as ipc
from guard.adapters import vllm_rank_ipc_sources as sources
from guard.adapters import vllm_rank_rpc_hooks as rpc
from guard.adapters import vllm_rank_rpc_input as projection

RAW=b'''calls=[]
class Scheduled:
    pass
class NativeFuture(Future):
    def result(self, timeout=None):
        calls.append('result')
        return super().result(timeout)
class Executor:
    def execute(self, scheduler_output):
        calls.append('execute')
        return self.future
'''


@pytest.fixture
def deferred(tmp_path,monkeypatch):
    name='owned_future_fixture';path=tmp_path/'fixture.py';path.write_bytes(RAW)
    module=types.ModuleType(name);module.__file__=str(path);module.Future=Future
    exec(compile(RAW,str(path),'exec',dont_inherit=True,optimize=0),vars(module));monkeypatch.setitem(sys.modules,name,module)
    common={'phase':'around-original','role':'EXAMPLE','source_modules':[name]}
    hooks={'Executor.execute':{**common,'kind':'rpc','requests':{'root':'scheduler_output','layout':'scheduled'},
            'deferred_result':{'class':'NativeFuture','point':'NativeFuture.result'}},
        'NativeFuture.result':{**common,'kind':'deferred-result'}}
    codes=sources._codes(compile(RAW,'fixture.py','exec',dont_inherit=True,optimize=0))
    row={'path':'fixture.py','bytes':len(RAW),'sha256':sha256(RAW).hexdigest(),'functions':list(hooks),'hooks':hooks,
        'body_code_sha256':{point:sources.code_digest(codes[point])for point in hooks}}
    profile={'id':'EXAMPLE-deferred','image':'fixture-image','runtime':sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3])),
        'sources':{},'rpc_sources':{name:row},'request_layouts':{'scheduled':{'kind':'positive-dict-keys',
            'type':{'module':name,'name':'Scheduled'},'field':'num_scheduled_tokens','item_types':[]}}}
    catalog=tmp_path/'profiles.json';catalog.write_text(json.dumps({'schema':'guard-rank-ipc-source-candidates/1','profiles':[profile]}))
    monkeypatch.setattr(sources,'PROFILES',catalog)
    rows=[];identity=ipc.IPCObserver(image='fixture-image',key=b'K'*32,emit=rows.append)
    observer=rpc.RPCObserver(image='fixture-image',key=b'K'*32,emit=rows.append,identity_observer=identity)
    observer.attach(module)
    owner=module.Executor();owner.future=module.NativeFuture()
    scheduled=module.Scheduled();scheduled.num_scheduled_tokens={'private-request':1}
    return module,observer,rows,owner,scheduled


def test_deferred_original_result_has_same_request_and_actual_future_without_reading_output(deferred):
    module,observer,rows,owner,scheduled=deferred
    class Opaque:
        def __repr__(self):raise AssertionError('output repr forbidden')
        def __iter__(self):raise AssertionError('output iteration forbidden')
        @property
        def __dict__(self):raise AssertionError('output access forbidden')
    result=Opaque();owner.future.set_result(result)
    future=owner.execute(scheduled);origin=rows[-1]
    assert future is owner.future and future.result()is result
    before,after=rows[-2:]
    assert [row['event']for row in rows]==['CALL','RETURN','CALL','RETURN']
    assert before['request_hmac_sha256']==origin['request_hmac_sha256']==after['request_hmac_sha256']
    binding=after['deferred_rpc'];assert binding==before['deferred_rpc']
    assert binding['origin_call_hmac_sha256']==origin['call_hmac_sha256']and binding['origin_object_hmac_sha256']==origin['object_hmac_sha256']
    assert binding['future_object_hmac_sha256']==after['object_hmac_sha256']and binding['origin_returned_ns']<=before['operation_started_ns']
    assert after['exception_kind']is None and module.calls==['execute','result']
    assert b'private-request'not in json.dumps(rows).encode()
    assert all(r['producer_registered']is r['engine_group_verified']is r['source_authenticated']is False for r in rows)


@pytest.mark.parametrize('error,kind',[(TimeoutError('PRIVATE_ERROR'),'timeout'),(RuntimeError('PRIVATE_ERROR'),'runtime-error'),(ValueError('PRIVATE_ERROR'),'other')])
def test_exception_object_and_original_call_count_are_preserved(deferred,error,kind):
    module,observer,rows,owner,scheduled=deferred;owner.future.set_exception(error)
    future=owner.execute(scheduled)
    with pytest.raises(type(error))as raised:future.result()
    assert raised.value is error and module.calls==['execute','result']
    assert rows[-1]['event']=='RAISE'and rows[-1]['exception_kind']==kind and rows[-1]['original_invoked']is True
    assert b'PRIVATE_ERROR'not in json.dumps(rows).encode()


def test_observer_failure_never_retries_or_suppresses_original_exception(deferred):
    module,observer,rows,owner,scheduled=deferred;error=RuntimeError('PRIVATE_ERROR');owner.future.set_exception(error)
    future=owner.execute(scheduled)
    def broken(value):raise OSError('observer failure')
    observer.emit=broken
    with pytest.raises(RuntimeError)as raised:future.result()
    assert raised.value is error and module.calls==['execute','result']


def test_unbound_or_foreign_future_cannot_inherit_request_metadata(deferred):
    module,observer,rows,owner,scheduled=deferred;unbound=module.NativeFuture();unbound.set_result(3)
    assert unbound.result()==3 and rows==[]
    owner.future.set_result(5);owner.execute(scheduled)
    assert unbound.result()==3 and len(rows)==2


def test_no_engine_or_future_strong_reference_is_retained(deferred):
    module,observer,rows,owner,scheduled=deferred;owner.future.set_result(7);future=owner.execute(scheduled)
    reference=weakref.ref(future);del owner.future;del future;gc.collect()
    assert reference()is None
    temporary=module.Executor();temporary.future=module.NativeFuture();temporary.future.set_result(8);temporary.execute(scheduled)
    origin=weakref.ref(temporary);del temporary;gc.collect();assert origin()is None


def test_parent_executor_disappearance_refuses_causal_binding_but_preserves_result(deferred):
    module,observer,rows,owner,scheduled=deferred
    temporary=module.Executor();temporary.future=module.NativeFuture();temporary.future.set_result(7);future=temporary.execute(scheduled)
    del temporary;gc.collect();assert future.result()==7
    assert len([r for r in rows if r.get('point')=='NativeFuture.result'and not r.get('gaps')])==0
    assert rows[-1]['diagnostic_code']=='RANK_RPC_DEFERRED_ORIGIN_UNAVAILABLE'


def test_process_generation_change_clears_inherited_future_bindings(deferred):
    module,observer,rows,owner,scheduled=deferred;owner.future.set_result(7);future=owner.execute(scheduled)
    observer.pid-=1
    assert future.result()==7 and len(rows)==2 and observer.deferred=={}


def test_wrong_super_class_cell_is_refused_before_installation(deferred):
    module,observer,rows,owner,scheduled=deferred
    original=observer.functions[module.__name__]['NativeFuture.result']
    class Unrelated:pass
    original.__closure__[0].cell_contents=Unrelated
    identity=ipc.IPCObserver(image='fixture-image',key=b'K'*32,emit=rows.append)
    candidate=rpc.RPCObserver(image='fixture-image',key=b'K'*32,emit=rows.append,identity_observer=identity)
    # Restore the original function so qualification reaches the class-cell gate.
    module.NativeFuture.result=original
    module.Executor.execute=observer.functions[module.__name__]['Executor.execute']
    with pytest.raises(ValueError,match='RANK_RPC_CLASS_CELL_UNVERIFIED'):candidate.attach(module)


def test_projection_keeps_source_exception_separate_from_detected_client_and_recovery_events(deferred):
    module,observer,rows,owner,scheduled=deferred;owner.future.set_exception(TimeoutError('PRIVATE_ERROR'))
    future=owner.execute(scheduled);applied=time.monotonic_ns()
    with pytest.raises(TimeoutError):future.result()
    request=rows[0]['request_hmac_sha256'][0]
    result,=projection.deferred_results(rows,observer.profile,request,applied,rows[0]['context'])
    assert result['event']=='original-result-raised'and result['exception_kind']=='timeout'
    assert all(result[k]is False for k in('fault_causality_verified','upper_delivery_verified','recovery_verified'))
    assert b'PRIVATE_ERROR'not in json.dumps(result).encode()
    assert projection.deferred_results(rows,observer.profile,'0'*64,applied,rows[0]['context'])==[]
    assert projection.deferred_results(rows,observer.profile,request,rows[-1]['observation_ns']+1,rows[0]['context'])==[]


@pytest.mark.parametrize('damage',['wrong-request','wrong-origin-object','wrong-future-object','different-context','missing-origin','duplicate-origin','origin-time'])
def test_projection_refuses_unpaired_deferred_request_or_generation(deferred,damage):
    module,observer,rows,owner,scheduled=deferred;owner.future.set_result(7);future=owner.execute(scheduled);future.result()
    data=deepcopy(rows);request=data[0]['request_hmac_sha256'][0]
    if damage=='wrong-request':data[1]['request_hmac_sha256']=['0'*64]
    elif damage=='wrong-origin-object':data[0]['object_hmac_sha256']='0'*64
    elif damage=='wrong-future-object':data[-1]['object_hmac_sha256']='0'*64
    elif damage=='different-context':data[1]['context']['start_ticks']+=1
    elif damage=='missing-origin':del data[1]
    elif damage=='duplicate-origin':data.append(deepcopy(data[1]))
    else:data[1]['observation_ns']+=1
    with pytest.raises(ValueError):projection.deferred_results(data,observer.profile,request,0,rows[0]['context'])
