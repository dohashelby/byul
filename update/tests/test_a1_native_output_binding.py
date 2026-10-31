"""Real reviewed native method bodies; dependencies and serving are CPU EXAMPLE."""
import os
import ast
import asyncio
from collections import defaultdict
from copy import deepcopy
import gc
import hashlib
import json
from pathlib import Path
import threading
import types
from types import SimpleNamespace
import uuid
import weakref
import pytest
from typer.testing import CliRunner
from guard.adapters import vllm_lifecycle_sensor as legacy,vllm_attempt_sensor as invocation
from guard.adapters import vllm_request_output_sensor as output,vllm_request_output_metadata as metadata
from guard.adapters.vllm_request_attempts import project_attempts
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture
from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader,KubernetesCaptureError
from guard.cli import app
from tests.test_lifecycle_capture import external,revised,case
from tests.test_lifecycle_sensor import FakeOS,SilentThread,CONTEXT,CANARY,writer_factory
from tests.test_lifecycle_session import group
from tests.test_kubernetes_lifecycle import target
from tests.test_request_selection_sensor import native_module,INPUT
from tests.test_a1_native_attempt_binding import scope

REPO=Path(__file__).resolve().parents[2]
FIXTURE=Path(__file__).parent/'fixtures/a1-output-native-methods.json'


class Box:
    def __init__(self,**values):self.__dict__.update(values)


def native_output_module(observer):
    module=types.ModuleType(output.MODULE)
    class RequestOutput:
        def __init__(self,rid,outputs):self.request_id=rid;self.outputs=outputs
        def add(self,value,aggregate):
            if aggregate:self.outputs[0].text+=value.outputs[0].text
            else:self.outputs=value.outputs
    class PoolingRequestOutput:pass
    class RequestState:
        @classmethod
        def from_new_request(cls,request,queue,**kwargs):
            return cls(request_id=request.request_id,external_req_id=request.external_req_id,queue=queue,
                output_kind='DELTA',stream_interval=1,parent_req=None,pending_text='EXAMPLE_PRIVATE_OUTPUT')
        def __init__(self,**data):self.__dict__.update(data)
        def _new_completion_output(self,*args):return Box(text=self.pending_text)
        def _new_request_output(self,rid,outputs,*args):return RequestOutput(rid,outputs)
    class OutputProcessor:
        def __init__(self):
            self.request_states={};self.external_req_ids=defaultdict(list);self.parent_requests={}
            self.tokenizer=None;self.log_stats=False;self.stream_interval=1
        def _update_admission_stats(self):pass
        def _update_streaming_request_state(self,*args):pass
        def abort_requests(self,*args):pass
        def _finish_request(self,*args):pass
    class RequestOutputCollector:pass
    module.__dict__.update(locals());module.__dict__.update(asyncio=asyncio,RequestOutputKind=Box(DELTA='DELTA',FINAL_ONLY='FINAL_ONLY'))
    rows=json.loads(FIXTURE.read_text())
    for point,row in rows.items():
        assert row['full_source_sha256']==output.SOURCE_SHA256 and row['public_commit']==output.COMMIT and row['license']=='Apache-2.0'
        owner,name=point.split('.');node=ast.parse(row['method']).body[0]
        tree=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])
        exec(compile(ast.fix_missing_locations(tree),'reviewed-output-processor-body','exec'),module.__dict__)
        setattr(module.__dict__[owner],name,module.__dict__[name])
    observer.instrument(module)
    return module


@pytest.fixture
def world(external,monkeypatch):
    prepared=lifecycle.prepare_assets(external/'assets',REPO,profile=output.PROFILE)
    assert lifecycle.load_prepared(external/'assets/manifest.json')==prepared
    base=types.ModuleType('_output_private_base');base.__file__=legacy.__file__
    exec(compile(Path(legacy.__file__).read_bytes(),legacy.__file__,'exec',dont_inherit=True,optimize=0),base.__dict__)
    fake=FakeOS();base.os=fake
    class HostRuntimeOS:
        name=os.name
        if hasattr(os,'uname'):uname=staticmethod(os.uname)
        def __getattr__(self,key):return getattr(fake,key)
    monkeypatch.setattr(invocation,'os',HostRuntimeOS())
    base._context=lambda:dict(CONTEXT);base._open_directory=lambda p:10
    base.threading=SimpleNamespace(RLock=threading.RLock,Thread=SilentThread,Event=threading.Event)
    output.configure(base,invocation)
    observer=base.Observer(json.loads((external/'assets/settings.json').read_text()),prepared['assets']['settings.json']['sha256'])
    base._OBSERVER=observer;monkeypatch.setattr(output,'_BASE',base);writer=observer._writer()
    processor=native_module(observer,INPUT,'InputProcessor',('assign_request_id',))
    processor.assign_request_id.__wrapped__.__globals__['random_uuid']=lambda:uuid.uuid4().hex
    native=native_output_module(observer)
    for module in (INPUT,output.MODULE):writer.write('MODULE',{'module':module,'verified':True,'source_sha256':legacy.SOURCE_SHA[module],'code_sha256':'c'*64})
    return SimpleNamespace(root=external,prepared=prepared,base=base,observer=observer,writer=writer,fake=fake,
        processor=processor,native=native,output_processor=native.OutputProcessor())


async def invoke(world,*,label='EXAMPLE-RID',sid='EXAMPLE-SID',body=b'EXAMPLE_PRIVATE_BODY',failure=None,send_failure=None,receive_disconnect=False,saved=None,merge=False,output_error=None):
    value=Box(request_id='cmpl-'+label,external_req_id=None,session_id=sid);seen=[];response=object()
    async def receive():return {'type':'http.disconnect' if receive_disconnect else 'http.request','body':b'EXAMPLE_PRIVATE_INPUT'}
    async def send(message):
        seen.append(message)
        if send_failure is not None:raise send_failure
        return response
    async def serving(s,r,w):
        world.processor.assign_request_id(value)
        queue=world.native.RequestOutputCollector('DELTA',value.external_req_id)
        world.output_processor.add_request(value,None,queue=queue)
        state=world.output_processor.request_states[value.request_id]
        first=state.make_request_output([1],None,None,None)
        queue.put(first)
        if merge:
            state.pending_text='EXAMPLE_PRIVATE_SECOND';second=state.make_request_output([2],None,None,None);queue.put(second)
        if output_error is not None:queue.put(output_error)
        result=await queue.get();assert result is first
        assert queue.get_nowait() is None
        queue.close()
        await asyncio.sleep(0)
        if saved is not None:saved.append(w)
        if receive_disconnect:await r()
        if failure is not None:raise failure
        assert await w({'type':'http.response.body','body':body,'more_body':False}) is response
        return value
    returned=await output.Middleware(serving)(scope(label),receive,send)
    assert returned is value and world.base._ROUTE_REQUEST.get() is None
    return value,seen


def packet(world):
    world.writer.close('SEALED');raw=bytes(world.fake.data);rows,summary=capture.parse_recording(raw,world.prepared)
    for secret in (b'EXAMPLE_PRIVATE_OUTPUT',b'EXAMPLE_PRIVATE_BODY',b'EXAMPLE-RID',CANARY.encode()):assert secret not in raw
    return raw,rows,summary


def test_native_external_id_and_collector_put_get_objects_remain_distinct(world):
    request,_=asyncio.run(invoke(world));raw,rows,summary=packet(world)
    events=[r['payload'] for r in rows if r['event']=='OUTPUT']
    add=next(p for p in events if p['point']=='OutputProcessor.add_request' and p['phase']=='RETURN')
    make=next(p for p in events if p['point']=='RequestState.make_request_output' and p['phase']=='RETURN')
    put=next(p for p in events if p['point']=='RequestOutputCollector.put' and p['phase']=='RETURN')
    get=next(p for p in events if p['point']=='RequestOutputCollector.get' and p['phase']=='RETURN')
    assert add['native_request_sha256']==make['native_request_sha256']==hashlib.sha256(request.request_id.encode()).hexdigest()
    assert add['native_request_sha256']!=get['external_request_sha256'] and get['native_request_sha256'] is None
    assert add['collector_sha256']==make['collector_sha256']==put['collector_sha256']==get['collector_sha256']
    assert make['output_object_sha256']==put['provided_output_object_sha256']==put['stored_output_object_sha256']==get['output_object_sha256']
    assert get['returned_text']['outputs_complete'] and get['asgi_context_live'] and summary['output_incomplete_calls']==0
    assert summary['output_event_count']==12


def test_real_collector_aggregation_is_observed_without_equating_provided_and_stored(world):
    asyncio.run(invoke(world,merge=True));_,rows,_=packet(world)
    puts=[r['payload'] for r in rows if r['event']=='OUTPUT' and r['payload']['point']=='RequestOutputCollector.put' and r['payload']['phase']=='RETURN']
    assert len(puts)==2 and puts[1]['provided_output_object_sha256']!=puts[1]['stored_output_object_sha256']
    assert puts[1]['provided_text']!=puts[1]['stored_text']


@pytest.mark.parametrize('error_type',[RuntimeError])
def test_real_collector_raises_the_exact_original_exception(world,error_type):
    error=error_type(CANARY)
    with pytest.raises(error_type) as caught:asyncio.run(invoke(world,output_error=error))
    assert caught.value is error
    _,rows,_=packet(world)
    assert any(r['event']=='OUTPUT' and r['payload']['phase']=='RAISE' and r['payload']['point']=='RequestOutputCollector.get' for r in rows)


@pytest.mark.parametrize('error_type',[asyncio.CancelledError,KeyboardInterrupt])
def test_real_occupied_collector_keeps_unsupported_baseexception_behavior(world,error_type):
    # Native put only replaces a pending output for Exception, not BaseException.
    # Observe that exact behavior rather than inventing a new supported input.
    asyncio.run(invoke(world,output_error=error_type(CANARY)));_,rows,_=packet(world)
    assert not any(r['event']=='OUTPUT' and r['payload']['phase']=='RAISE' for r in rows)


@pytest.mark.parametrize('error_type',[RuntimeError,asyncio.CancelledError,KeyboardInterrupt])
def test_thin_wrapper_preserves_original_baseexception_instance(world,error_type):
    error=error_type(CANARY)
    def native_method(owner):raise error
    wrapped=world.observer.output_wrap('RequestOutputCollector.close',native_method)
    with pytest.raises(error_type) as caught:wrapped(Box())
    assert caught.value is error


def test_cancel_suspended_get_keeps_native_cancellation_and_does_not_consume(world):
    async def run():
        queue=world.native.RequestOutputCollector('DELTA','EXAMPLE-EXTERNAL')
        task=asyncio.create_task(queue.get());await asyncio.sleep(0);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert queue.output is None
    asyncio.run(run());packet(world)


def test_observer_failure_does_not_change_return_or_original_error(world,monkeypatch):
    original=world.observer.emit
    def emit(event,payload):
        if event=='OUTPUT':raise KeyboardInterrupt(CANARY)
        return original(event,payload)
    monkeypatch.setattr(world.observer,'emit',emit)
    asyncio.run(invoke(world));_,rows,_=packet(world);assert not any(r['event']=='OUTPUT' for r in rows)


def test_weak_identity_expiry_bound_reuse_and_process_generation(monkeypatch):
    serial=0;pid=[42]
    def nonce(kind):
        nonlocal serial
        serial+=1;return hashlib.sha256(str(serial).encode()).hexdigest()
    registry=output.WeakIdentities(nonce,pid=lambda:pid[0]);value=Box();first=registry.identify(value,'output')
    assert registry.identify(value,'output')==first and registry.identify([], 'output') is None
    ref=weakref.ref(value);del value;gc.collect();assert ref() is None and not registry.entries
    value=Box();second=registry.identify(value,'output');assert second!=first
    # Simulate a stale entry at a reused address: lookup must check referent identity.
    other=Box();registry.entries[('output',id(other))]=(weakref.ref(value),second)
    assert registry.identify(other,'output')!=second
    monkeypatch.setattr(output,'MAX_IDENTITIES',len(registry.entries));assert registry.identify(Box(),'output') is None
    pid[0]=43;assert registry.identify(value,'output')!=second and registry.generation==1


@pytest.mark.parametrize('text',['', 'x'*65536,'x'*65537,'한'*30000,None],ids=['empty','at-limit','over-limit','utf8-over-limit','unsupported'])
def test_text_hash_bound_and_unsupported_outputs(text):
    value=Box(outputs=[Box(text=text)]);sample=output.text_samples(value);metadata.validate_text(sample)
    expected=type(text) is str and len(text.encode())<=65536
    assert sample['outputs_complete'] is expected
    assert sample['items'][0]['text_sha256']==(hashlib.sha256(text.encode()).hexdigest() if expected else None)
    assert output.text_samples(Box())['outputs_complete'] is False


def test_native_slots_are_read_without_executing_properties():
    class Slotted:
        __slots__=('request_id','external_req_id')
        @property
        def queue(self):raise AssertionError('property must not execute')
    value=Slotted();value.request_id='EXAMPLE-NATIVE';value.external_req_id='EXAMPLE-EXTERNAL'
    assert output.attributes(value)=={'request_id':'EXAMPLE-NATIVE','external_req_id':'EXAMPLE-EXTERNAL'}


@pytest.mark.parametrize('damage',['raw','hash','runtime','bool-count','point','phase','native-from-collector','missing-call','double-call'])
def test_resealed_bad_metadata_rejected_or_incomplete_calls_preserved(world,damage):
    asyncio.run(invoke(world));raw,_,_=packet(world)
    def change(rows):
        row=next(r for r in rows if r['event']=='OUTPUT' and r['payload']['point']=='RequestOutputCollector.get' and r['payload']['phase']=='RETURN');p=row['payload']
        if damage=='raw':p['body']=CANARY
        elif damage=='hash':p['observer_code_sha256']='0'*64
        elif damage=='runtime':p['observer_runtime']='cpython-3.12.99'
        elif damage=='bool-count':p['returned_text']['output_count']=True
        elif damage=='point':p['point']='secret'
        elif damage=='phase':p['phase']=[]
        elif damage=='native-from-collector':p['native_request_sha256']='a'*64
        else:
            call=next(r for r in rows if r['event']=='OUTPUT' and r['payload']['call_sha256']==p['call_sha256'] and r['payload']['phase']=='CALL')
            if damage=='missing-call':rows.remove(call)
            else:rows.insert(rows.index(call),deepcopy(call))
    mutated=revised(raw,change)
    if damage in {'missing-call','double-call'}:
        _,summary=capture.parse_recording(mutated,world.prepared);assert summary['output_incomplete_calls']==1
    else:
        with pytest.raises(ValueError):capture.parse_recording(mutated,world.prepared)


def test_fixed_prepare_dependencies_and_default_remain_closed(external):
    runner=CliRunner();result=runner.invoke(app,['prepare-lifecycle-sensor','--profile',output.PROFILE,'--output',str(external/'new')])
    assert result.exit_code==0,result.output
    prepared=lifecycle.load_prepared(external/'new/manifest.json');assert len(prepared['assets'])==5
    assert prepared['source_sha256'][output.MODULE]==output.SOURCE_SHA256
    for name in ('guard_lifecycle_base.py','guard_attempt_invocation.py'):
        p=external/'new'/name;raw=p.read_bytes();p.write_bytes(raw+b'#mutation')
        with pytest.raises(ValueError):lifecycle.load_prepared(external/'new/manifest.json')
        p.write_bytes(raw)
    old=lifecycle.prepare_assets(external/'old',REPO);assert old['profile']==legacy.PROFILE and len(old['assets'])==3


@pytest.mark.parametrize('old',[legacy.PROFILE,invocation.PROFILE,'vllm-native-lifecycle-metadata/9'])
def test_old_profiles_refuse_output_namespace(world,old):
    asyncio.run(invoke(world));raw,_,_=packet(world)
    with pytest.raises(ValueError):capture.parse_recording(raw,{**world.prepared,'profile':old})


def test_reader11_keeps_source_image_lab_and_engine_boundaries(group,target):
    prepared={**group.prepared,'profile':output.PROFILE};calls=[]
    reader=KubernetesLifecycleReader(target,group.env,prepared,runner=lambda *a,**kw:calls.append(a));assert reader.file_read_limit==output.MAX_BYTES
    changed=group.env.model_copy(deep=True);changed.engine.version='0.32.0';changed=changed.seal()
    with pytest.raises(KubernetesCaptureError):KubernetesLifecycleReader({**target,'environment_ref':changed.ref().model_dump(mode='json')},changed,prepared,runner=lambda *a,**kw:calls.append(a))
    with pytest.raises(ValueError):KubernetesLifecycleReader({**target,'lab':False},group.env,prepared,runner=lambda *a,**kw:calls.append(a))
    assert not calls


@pytest.mark.parametrize('mode',['normal','merge','missing-get','cross-invocation','source-failure','partial'])
def test_projection_requires_exact_native_collector_object_and_caller_without_client_claim(world,mode):
    async def run():
        await invoke(world,label='EXAMPLE-SHARED',sid='EXAMPLE-A',merge=mode=='merge')
        await invoke(world,label='EXAMPLE-SHARED',sid='EXAMPLE-B')
    asyncio.run(run());raw,_,_=packet(world)
    trials=[{'attempted':True,'case_id':'req12-'+case,'external_request_sha256':hashlib.sha256(b'EXAMPLE-SHARED').hexdigest(),
        'auxiliary_session_sha256':hashlib.sha256(sid.encode()).hexdigest()} for case,sid in [('disconnect','EXAMPLE-A'),('replacement','EXAMPLE-B')]]
    def mutate(rows):
        gets=[r for r in rows if r['event']=='OUTPUT' and r['payload']['point']=='RequestOutputCollector.get']
        if mode=='missing-get':rows.remove(gets[0])
        if mode=='cross-invocation':
            other=gets[-1]['payload']['asgi_invocation_sha256']
            for r in gets[:2]:r['payload']['asgi_invocation_sha256']=other
        if mode=='source-failure':
            first=next(r for r in rows if r['event']=='OUTPUT')
            rows.insert(rows.index(first),{'event':'MODULE','monotonic_ns':first['monotonic_ns'],
                'payload':{'module':output.MODULE,'verified':False}})
    if mode=='source-failure':
        with pytest.raises(ValueError,match='OUTPUT_SOURCE_MODULE_UNAVAILABLE'):capture.parse_recording(revised(raw,mutate),world.prepared)
        return
    rows,summary=capture.parse_recording(revised(raw,mutate),world.prepared)
    result=project_attempts({'recordings':[{'capture':{'records':rows,'summary':summary,'process_context_verified':mode!='partial',
        'image_digest_verified':False},'session_owner_match':True,'listed_file_identity_match':True}]},trials)
    assert result['native_output_collector_binding_observed'] is (mode in {'normal','cross-invocation'})
    assert all(not v['client_receipt_verified'] and not v['confirmed_violation'] for v in result['output_links'])
    assert not result['late_delivery_verified'] and not result['automatic_retry_verified'] and not result['whole_scope_complete']
    if mode=='cross-invocation':assert any(v['cross_invocation_candidate'] and v['origin_case_id']=='req12-disconnect' for v in result['output_links'])
    if mode=='merge':assert any(v['mutation_or_aggregation_observed'] and not v['origin_binding_observed'] for v in result['output_links'])
