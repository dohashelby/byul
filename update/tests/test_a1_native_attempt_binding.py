"""Explicit10 real middleware/writer/parser; native producer is CPU EXAMPLE."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import types
from types import SimpleNamespace
import uuid
import pytest
from typer.testing import CliRunner
from guard.adapters import vllm_lifecycle_sensor as legacy,vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_attempt_sensor as attempt,vllm_attempt_metadata as metadata
from guard.cli import app
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture
from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader,KubernetesCaptureError
from tests.test_lifecycle_capture import external,revised,case
from tests.test_lifecycle_sensor import FakeOS,SilentThread,CONTEXT,CANARY,writer_factory
from tests.test_lifecycle_session import group
from tests.test_kubernetes_lifecycle import target
from tests.test_request_selection_sensor import native_module,INPUT

REPO=Path(__file__).resolve().parents[2]


@pytest.fixture
def world(external,monkeypatch):
    prepared=lifecycle.prepare_assets(external/'assets',REPO,profile=attempt.PROFILE)
    assert lifecycle.load_prepared(external/'assets/manifest.json')==prepared
    base=types.ModuleType('_attempt_base_fixture');base.__file__=legacy.__file__
    exec(compile(Path(legacy.__file__).read_bytes(),legacy.__file__,'exec',dont_inherit=True,optimize=0),base.__dict__)
    fake=FakeOS();base.os=fake;monkeypatch.setattr(attempt,'os',fake)
    base._context=lambda:dict(CONTEXT);base._open_directory=lambda p:10
    base.threading=SimpleNamespace(RLock=threading.RLock,Thread=SilentThread,Event=threading.Event)
    attempt.configure(base)
    observer=base.Observer(json.loads((external/'assets/settings.json').read_text()),prepared['assets']['settings.json']['sha256'])
    base._OBSERVER=observer;monkeypatch.setattr(attempt,'_BASE',base)
    writer=observer._writer()
    processor=native_module(observer,INPUT,'InputProcessor',('assign_request_id',))
    processor.assign_request_id.__wrapped__.__globals__['random_uuid']=lambda:uuid.uuid4().hex
    writer.write('MODULE',{'module':INPUT,'verified':True,'source_sha256':legacy.SOURCE_SHA[INPUT],'code_sha256':'c'*64})
    return SimpleNamespace(root=external,prepared=prepared,base=base,observer=observer,writer=writer,fake=fake,processor=processor)


def scope(label='EXAMPLE-RID',path='/v1/completions',method='POST'):
    return {'type':'http','path':path,'root_path':'','method':method,'headers':[(b'x-request-id',label.encode())]}


async def invoke(world,*,label='EXAMPLE-RID',sid='EXAMPLE-SID',body=b'EXAMPLE_PRIVATE_BODY',failure=None,send_failure=None,receive_disconnect=False,saved=None):
    value=SimpleNamespace(request_id='cmpl-'+label,external_req_id=None,session_id=sid)
    response=object();seen=[]
    async def receive():return {'type':'http.disconnect' if receive_disconnect else 'http.request','body':b'EXAMPLE_PRIVATE_INPUT'}
    async def send(message):
        seen.append(message)
        if send_failure is not None:raise send_failure
        return response
    async def app(s,r,w):
        world.processor.assign_request_id(value)
        await asyncio.sleep(0)
        if saved is not None:saved.append(w)
        if receive_disconnect:await r()
        if failure is not None:raise failure
        assert await w({'type':'http.response.body','body':body,'more_body':False}) is response
        return value
    returned=await attempt.Middleware(app)(scope(label),receive,send)
    assert returned is value and world.base._ROUTE_REQUEST.get() is None and world.base._ASGI_REQUEST.get() is None
    return returned,seen


def packet(world):
    world.writer.close('SEALED');raw=bytes(world.fake.data);rows,summary=capture.parse_recording(raw,world.prepared)
    assert b'EXAMPLE_PRIVATE_BODY' not in raw and b'EXAMPLE_PRIVATE_INPUT' not in raw and b'EXAMPLE-RID' not in raw and CANARY.encode() not in raw
    return raw,rows,summary


def test_actual_middleware_native_selection_and_send_share_unique_invocation(world):
    value,sent=asyncio.run(invoke(world,receive_disconnect=True));_,rows,summary=packet(world)
    events=[r['payload'] for r in rows if r['event']=='ATTEMPT']
    selected=next(r['payload']['metadata']['request_selection'] for r in rows if r['event']=='SELECTION' and r['payload']['phase']=='RETURN')
    assert [e['phase'] for e in events]==['APP_ENTER','DISCONNECT_RECEIVED','BODY_SEND_RETURN','APP_RETURN']
    assert {e['asgi_invocation_sha256'] for e in events}=={selected['asgi_invocation_sha256']}
    assert {e['asgi_request_sha256'] for e in events}=={hashlib.sha256(b'EXAMPLE-RID').hexdigest()}
    assert selected['selected_request_id_sha256']==hashlib.sha256(value.request_id.encode()).hexdigest()
    assert selected['selected_session_sha256']==hashlib.sha256(b'EXAMPLE-SID').hexdigest()
    body=events[2];assert body['body_bytes']==len(sent[0]['body']) and body['body_sha256']==hashlib.sha256(sent[0]['body']).hexdigest()
    assert summary['attempt_incomplete_invocations']==0 and summary['attempt_event_count']==4
    assert all(e['context_live'] for e in events)


def test_simultaneous_identical_external_ids_keep_distinct_invocations_and_native_ids(world):
    async def run():return await asyncio.gather(invoke(world,sid='EXAMPLE-SID-A'),invoke(world,sid='EXAMPLE-SID-B'))
    returned=asyncio.run(run());_,rows,_=packet(world)
    selected=[r['payload']['metadata']['request_selection'] for r in rows if r['event']=='SELECTION' and r['payload']['phase']=='RETURN']
    assert len({s['asgi_invocation_sha256'] for s in selected})==2 and len({s['selected_request_id_sha256'] for s in selected})==2
    assert len({s['asgi_request_sha256'] for s in selected})==1
    events=[r['payload'] for r in rows if r['event']=='ATTEMPT'];assert len(events)==6
    assert sorted(sum(e['asgi_invocation_sha256']==s['asgi_invocation_sha256'] for e in events) for s in selected)==[3,3]


@pytest.mark.parametrize('where',['app','send'])
@pytest.mark.parametrize('error_type',[RuntimeError,asyncio.CancelledError,KeyboardInterrupt])
def test_same_native_exception_or_cancellation_is_preserved(world,where,error_type):
    error=error_type(CANARY)
    with pytest.raises(error_type) as caught:
        asyncio.run(invoke(world,**({'failure':error} if where=='app' else {'send_failure':error})))
    assert caught.value is error and world.base._ROUTE_REQUEST.get() is None
    _,rows,summary=packet(world);phases=[r['payload']['phase'] for r in rows if r['event']=='ATTEMPT']
    assert phases[-1]=='APP_RAISE' and ('BODY_SEND_RAISE' in phases)==(where=='send')
    assert summary['attempt_incomplete_invocations']==0


def test_extra_observer_failure_does_not_change_native_return(world,monkeypatch):
    original=world.observer.emit
    def failed(event,payload):
        if event=='ATTEMPT' and payload['phase']=='BODY_SEND_RETURN':raise RuntimeError(CANARY)
        original(event,payload)
    monkeypatch.setattr(world.observer,'emit',failed)
    asyncio.run(invoke(world));_,rows,_=packet(world)
    assert [r['payload']['phase'] for r in rows if r['event']=='ATTEMPT']==['APP_ENTER','APP_RETURN']


@pytest.mark.parametrize('kind',['closed-context','forked-context'])
def test_late_or_inherited_send_cannot_claim_live_invocation(world,kind):
    saved=[]
    async def run():
        await invoke(world,saved=saved)
        if kind=='forked-context':
            # A selected context's owner PID differs from the current callback.
            # Keep writer/process simulation unchanged; this is no real OS fork proof.
            def find(fn,seen):
                if id(fn) in seen:return None
                seen.add(id(fn))
                for cell in fn.__closure__ or ():
                    value=cell.cell_contents
                    if type(value) is dict and {'pid','closed','invocation'}<=set(value):return value
                    if type(value) is types.FunctionType:
                        result=find(value,seen)
                        if result is not None:return result
            context=find(saved[0],set());assert context is not None
            context.update(pid=43,closed=False)
        await saved[0]({'type':'http.response.body','body':b'EXAMPLE-late','more_body':False})
    asyncio.run(run());_,rows,summary=packet(world)
    late=[r['payload'] for r in rows if r['event']=='ATTEMPT' and r['payload']['phase']=='BODY_SEND_RETURN'][-1]
    assert late['context_live'] is False and summary['attempt_late_callbacks']==1


@pytest.mark.parametrize('size',[0,65536,65537])
def test_body_hash_is_bounded_without_truncating_serving_payload(world,size):
    body=b'z'*size;_,seen=asyncio.run(invoke(world,body=body));_,rows,summary=packet(world)
    event=next(r['payload'] for r in rows if r['event']=='ATTEMPT' and r['payload']['phase']=='BODY_SEND_RETURN')
    assert seen[0]['body'] is body and event['body_bytes']==size and event['body_hash_complete']==(size<=65536)
    assert event['body_sha256']==(hashlib.sha256(body).hexdigest() if size<=65536 else None)
    assert summary['attempt_unhashed_bodies']==(size>65536)


@pytest.mark.parametrize('damage',['unknown-key','raw-body','hash','runtime','bool-size','large-hash','phase','context','invocation-reuse','mixed-runtime'])
def test_resealed_bad_metadata_or_code_provenance_is_rejected(world,damage):
    asyncio.run(invoke(world));raw,rows,_=packet(world)
    def change(items):
        values=[r['payload'] for r in items if r['event']=='ATTEMPT'];body=next(v for v in values if v['phase']=='BODY_SEND_RETURN')
        if damage=='unknown-key':body['unknown']=True
        elif damage=='raw-body':body['body']=CANARY
        elif damage=='hash':body['observer_code_sha256']='0'*64
        elif damage=='runtime':body['observer_runtime']='cpython-3.12.99'
        elif damage=='bool-size':body['body_bytes']=True
        elif damage=='large-hash':body['body_bytes']=65537
        elif damage=='phase':body['phase']=[]
        elif damage=='context':body['entry_method_sha256']='0'*64
        elif damage=='invocation-reuse':body['asgi_request_sha256']='0'*64
        else:
            other=next(k for k in attempt.RUNTIME_CODE_CONTRACTS if k!=body['observer_runtime'])
            body['observer_runtime']=other;body['observer_code_sha256']=attempt.RUNTIME_CODE_CONTRACTS[other]
    changed=revised(raw,change)
    with pytest.raises(ValueError):capture.parse_recording(changed,world.prepared)


def test_incomplete_or_duplicate_invocation_is_an_explicit_gap(world):
    asyncio.run(invoke(world));raw,_,_=packet(world)
    def change(rows):rows[:]=[r for r in rows if not(r['event']=='ATTEMPT' and r['payload']['phase']=='APP_RETURN')]
    _,summary=capture.parse_recording(revised(raw,change),world.prepared)
    assert summary['attempt_incomplete_invocations']==1


@pytest.mark.parametrize('old_profile',[legacy.PROFILE,'vllm-native-lifecycle-metadata/9'])
def test_old_parser_contract_does_not_admit_attempt_event_namespace(world,old_profile):
    asyncio.run(invoke(world));raw,_,_=packet(world);prepared=deepcopy(world.prepared);prepared['profile']=old_profile
    with pytest.raises(ValueError):capture.parse_recording(raw,prepared)


def test_explicit_prepare_cli_keeps_default8_and_fixed_base_dependency(external):
    runner=CliRunner()
    new=runner.invoke(app,['prepare-lifecycle-sensor','--profile',attempt.PROFILE,'--output',str(external/'new')]);assert new.exit_code==0,new.stdout
    old=runner.invoke(app,['prepare-lifecycle-sensor','--output',str(external/'old')]);assert old.exit_code==0
    bad=runner.invoke(app,['prepare-lifecycle-sensor','--profile','unknown','--output',str(external/'bad')]);assert bad.exit_code!=0
    assert json.loads(new.stdout)['profile']==attempt.PROFILE and json.loads(old.stdout)['profile']==legacy.PROFILE
    assert len(lifecycle.load_prepared(external/'new/manifest.json')['assets'])==4
    assert len(lifecycle.load_prepared(external/'old/manifest.json')['assets'])==3
    assert json.loads(old.stdout)['profile']==legacy.PROFILE
    path=external/'new/guard_lifecycle_base.py';path.write_bytes(path.read_bytes()+b'#changed')
    with pytest.raises(ValueError):lifecycle.load_prepared(external/'new/manifest.json')


def test_selected10_reader_does_not_open_native032_or_nonlab(group,target):
    prepared={**group.prepared,'profile':attempt.PROFILE};calls=[]
    reader=KubernetesLifecycleReader(target,group.env,prepared,runner=lambda *a,**kw:calls.append(a))
    assert reader.file_read_limit==attempt.MAX_BYTES and reader.file_read_profile=='owned-jsonl-2m-v1' and not calls
    changed=group.env.model_copy(deep=True);changed.engine.version='0.32.0';changed=changed.seal()
    selected={**target,'environment_ref':changed.ref().model_dump(mode='json')}
    with pytest.raises(KubernetesCaptureError,match='^KUBERNETES_CAPTURE_BINDING_UNAVAILABLE$'):
        KubernetesLifecycleReader(selected,changed,prepared,runner=lambda *a,**kw:calls.append(a))
    with pytest.raises(ValueError,match='^KUBERNETES_CAPTURE_TARGET_INVALID$') as error:
        KubernetesLifecycleReader({**target,'lab':False},group.env,prepared,runner=lambda *a,**kw:calls.append(a))
    assert type(error.value) is ValueError
    assert not calls
