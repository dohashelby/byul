"""EXAMPLE exact upstream bodies + real B callback/writer/parser; no serving/GPU."""
import ast
import os
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import types
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.adapters import vllm_lifecycle_sensor as legacy, vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_online_sensor as online, vllm_online_input as callback
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture, owned_capture
from tests.test_lifecycle_capture import external, revised, case, Docker, IMAGE
from tests.test_lifecycle_sensor import FakeOS, SilentThread, CONTEXT, CANARY, writer_factory
from tests.test_lifecycle_session import group
from tests.test_kubernetes_lifecycle import target
from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader, KubernetesCaptureError

REPOSITORY=Path(__file__).resolve().parents[2]
FIXTURES=Path(__file__).parent/'fixtures/online-input-native-methods.json'


def find(code, name):
    if code.co_qualname==name:return code
    for value in code.co_consts:
        if type(value) is types.CodeType:
            result=find(value,name)
            if result is not None:return result


def native(point, namespace=None):
    row=json.loads(FIXTURES.read_text(encoding='utf-8'))[point]
    contract=online.POINT_CONTRACTS[point]
    assert row['full_source_sha256']==online.MODULE_CONTRACTS[row['module']]['source_sha256']
    assert row['license']=='Apache-2.0'
    if row['owner_class']:
        source='\n'*(row['first_line']-2)+'class '+row['owner_class']+':\n'+row['method']
        qualname=point
    else:
        source='\n'*(row['first_line']-1)+row['method'];qualname=point.split('.',1)[1]
    # CPython uses module import bindings to select LOAD_GLOBAL/LOAD_ATTR call
    # forms. Preserve this compile context, without executing any import.
    lines=source.splitlines(keepends=True)
    for entry in row['module_imports']:
        pieces=entry['source'].splitlines(keepends=True)
        assert entry['line']+len(pieces)-1 < row['first_line']-1
        lines[entry['line']-1:entry['line']-1+len(pieces)]=pieces
    source=''.join(lines)
    code=compile(source,row['filename'],'exec',flags=row['compiler_flags'],dont_inherit=True,optimize=0)
    body=find(code,qualname)
    assert legacy.code_digest(body)==online.runtime_contract()['function_codes'][point]
    fn=types.FunctionType(body,{'__name__':row['module'],**(namespace or {})})
    node=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==qualname.split('.')[-1])
    fn.__defaults__=tuple(ast.literal_eval(n) for n in node.args.defaults) or None
    fn.__kwdefaults__={arg.arg:ast.literal_eval(value) for arg,value in zip(node.args.kwonlyargs,node.args.kw_defaults) if value is not None}
    return fn


@pytest.fixture
def world(external,monkeypatch):
    prepared=lifecycle.prepare_assets(external/'assets',REPOSITORY,profile=online.PROFILE)
    assert lifecycle.load_prepared(external/'assets/manifest.json')==prepared
    base=types.ModuleType('_test_private_base');base.__file__=legacy.__file__
    exec(compile(Path(legacy.__file__).read_bytes(),legacy.__file__,'exec',dont_inherit=True,optimize=0),base.__dict__)
    cb=types.ModuleType('_test_private_callback')
    code=compile(Path(callback.__file__).read_bytes(),'guard/adapters/vllm_online_input.py','exec',dont_inherit=True,optimize=0)
    assert legacy.code_digest(code)==online.runtime_contract()['callback_code_sha256']
    exec(code,cb.__dict__)
    fake=FakeOS();
    class HostRuntimeOS:
        name=os.name
        if hasattr(os,'uname'):uname=staticmethod(os.uname)
        def __getattr__(self,key):return getattr(fake,key)
    monkeypatch.setattr(online,'os',HostRuntimeOS())
    base.os=fake;base._open_directory=lambda p:10;base._context=lambda:dict(CONTEXT)
    base.threading=SimpleNamespace(RLock=threading.RLock,Thread=SilentThread,Event=threading.Event)
    online.configure(base,cb)
    config=json.loads((external/'assets/settings.json').read_text())
    observer=base.Observer(config,prepared['assets']['settings.json']['sha256'])
    base._OBSERVER=observer;monkeypatch.setattr(online,'_BASE',base)
    writer=observer._writer()
    # Module records are EXAMPLE fixtures; copied function bodies have independent literal code checks.
    for module,contract in online.MODULE_CONTRACTS.items():
        writer.write('MODULE',{'module':module,'verified':True,'source_sha256':contract['source_sha256'],
            'code_sha256':'c'*64,'online_code_sha256':online.runtime_contract()['module_codes'][module],
            'online_runtime':online.runtime_name()})
    return SimpleNamespace(root=external,prepared=prepared,base=base,observer=observer,writer=writer,fake=fake,callback=cb)


def wrapped(world,point,namespace=None):
    return world.observer.wrap(online.POINT_CONTRACTS[point]['module'],point,native(point,namespace))


def run_chat(world,call,*,path='/v1/chat/completions',headers=None):
    async def application(scope,receive,send):
        result=call()
        if hasattr(result,'__await__'):result=await result
        await send({'type':'http.response.body','body':b'EXAMPLE','more_body':False})
        return result
    async def receive():return {'type':'http.request','body':b'EXAMPLE'}
    sent=[]
    async def send(value):sent.append(value)
    result=asyncio.run(online.Middleware(application)({'type':'http','path':path,'root_path':'','method':'POST',
        'headers':[(b'x-request-id',b'EXAMPLE-RID')] if headers is None else headers},receive,send))
    return result,sent


def packet(world):
    world.writer.close('SEALED');raw=bytes(world.fake.data)
    rows,summary=capture.parse_recording(raw,world.prepared)
    assert CANARY.encode() not in raw and b'EXAMPLE-RID' not in raw
    return raw,rows,summary


@pytest.mark.parametrize('point',list(online.POINT_CONTRACTS))
def test_all_eight_exact_original_function_codes_are_accepted(world,point):
    assert callable(wrapped(world,point))


def test_actual_reuse_pop_is_observed_before_and_after_without_content(world):
    request=SimpleNamespace(kv_transfer_params={'prompt_token_ids':[-1,32]})
    reuse=wrapped(world,'online_renderer._reused_prompt_token_ids')
    result,sent=run_chat(world,lambda:reuse(request))
    assert result==[-1,32] and request.kv_transfer_params=={} and len(sent)==1
    raw,rows,summary=packet(world)
    snapshots=[r['payload']['metadata']['online_input'] for r in rows if r['event']=='NATIVE']
    assert [s['phase'] for s in snapshots]==['CALL','RETURN']
    assert snapshots[0]['binding']==snapshots[1]['binding']
    assert snapshots[0]['metadata']['parsed_fields']['kv_transfer_params']['canonical_sha256']!=snapshots[1]['metadata']['parsed_fields']['kv_transfer_params']['canonical_sha256']
    assert snapshots[1]['metadata']['reused']['min']==-1 and summary['online_incomplete_calls']==0
    assert summary['online_callback_provenance']['bytes_sha256']==online.CALLBACK_SHA256


def test_actual_processor_invocation_passes_native_id_to_nested_validation(world):
    check=wrapped(world,'InputProcessor._validate_prompt_len',{'VLLMValidationError':RuntimeError})
    error=RuntimeError(CANARY)
    owner=SimpleNamespace(skip_prompt_length_check=False,model_config=SimpleNamespace(max_model_len=16,runner_type='generate'),supports_mm_inputs=False)
    def validate(*args):
        check(owner,1,'decoder')
        raise error
    owner._validate_params=validate
    process=wrapped(world,'InputProcessor.process_inputs')
    with pytest.raises(RuntimeError) as caught:
        run_chat(world,lambda:process(owner,'EXAMPLE-NATIVE-RID',{'prompt_token_ids':[1]},None,()))
    assert caught.value is error
    _,rows,_=packet(world)
    snapshots=[r['payload']['metadata']['online_input'] for r in rows if r['event']=='NATIVE']
    assert len(snapshots)==4 and {s['binding']['native_request_id_sha256'] for s in snapshots}=={legacy.id_sha('EXAMPLE-NATIVE-RID')}
    assert snapshots[0]['binding']['call_id_sha256']==snapshots[-1]['binding']['call_id_sha256']
    assert snapshots[1]['binding']['call_id_sha256']==snapshots[2]['binding']['call_id_sha256']
    assert snapshots[0]['binding']['call_id_sha256']!=snapshots[1]['binding']['call_id_sha256']


def test_actual_async_render_preserves_same_cancellation(world):
    error=asyncio.CancelledError(CANARY)
    def fail(tokenizer):raise error
    render=wrapped(world,'OnlineRenderer.render_chat',{'is_mistral_tokenizer':fail})
    owner=SimpleNamespace(renderer=SimpleNamespace(tokenizer=None),parser=None)
    with pytest.raises(asyncio.CancelledError) as caught:run_chat(world,lambda:render(owner,SimpleNamespace()))
    assert caught.value is error
    _,rows,_=packet(world)
    snapshots=[r['payload']['metadata']['online_input'] for r in rows if r['event']=='NATIVE']
    assert [s['phase'] for s in snapshots]==['CALL','RAISE'] and snapshots[-1]['metadata']['exception_type']=='Exception'


@pytest.mark.parametrize('count,runner_type,raises',[(15,'generate',False),(16,'generate',True),(16,'pooling',False),(17,'pooling',True)])
def test_exact_length_body_preserves_return_and_same_exception(world,count,runner_type,raises):
    class VLLMValidationError(Exception):pass
    check=wrapped(world,'InputProcessor._validate_prompt_len',{'VLLMValidationError':VLLMValidationError})
    owner=SimpleNamespace(skip_prompt_length_check=False,model_config=SimpleNamespace(max_model_len=16,runner_type=runner_type),supports_mm_inputs=False)
    error=None
    try:run_chat(world,lambda:check(owner,count,'decoder'))
    except VLLMValidationError as exc:error=exc
    assert (error is not None)==raises
    _,rows,_=packet(world)
    snaps=[r['payload']['metadata']['online_input'] for r in rows if r['event']=='NATIVE']
    assert snaps[-1]['phase']==('RAISE' if raises else 'RETURN')
    assert snaps[-1]['metadata']['prompt_type']=='decoder' and snaps[-1]['metadata']['effective_config']['runner_type']==runner_type
    assert snaps[0]['binding']==snaps[-1]['binding']


@pytest.mark.parametrize('path,headers',[('/v1/completions',None),('/v1/chat/completions',[]),('/v1/chat/completions',[(b'x-request-id',b'a'),(b'x-request-id',b'b')])])
def test_unbound_or_duplicate_request_does_not_invent_online_observation(world,path,headers):
    reuse=wrapped(world,'online_renderer._reused_prompt_token_ids')
    result,_=run_chat(world,lambda:reuse(SimpleNamespace(kv_transfer_params={'prompt_token_ids':[3]})),path=path,headers=headers)
    assert result==[3]
    _,rows,_=packet(world)
    assert not any(r['event']=='NATIVE' for r in rows)


def test_closed_asgi_context_cannot_observe_late_work(world):
    reuse=wrapped(world,'online_renderer._reused_prompt_token_ids');contexts=[]
    import contextvars
    run_chat(world,lambda:contexts.append(contextvars.copy_context()))
    assert contexts[0].run(reuse,SimpleNamespace(kv_transfer_params={'prompt_token_ids':[4]}))==[4]
    _,rows,_=packet(world);assert not any(r['event']=='NATIVE' for r in rows)


def test_callback_failure_does_not_replace_actual_native_return(world,monkeypatch):
    # Captured callback uses this private module's helper, not a test-only alternate callback.
    monkeypatch.setattr(world.callback,'_binding',lambda _:(_ for _ in ()).throw(KeyboardInterrupt(CANARY)))
    reuse=wrapped(world,'online_renderer._reused_prompt_token_ids')
    result,_=run_chat(world,lambda:reuse(SimpleNamespace(kv_transfer_params={'prompt_token_ids':[4]})))
    assert result==[4]
    _,rows,_=packet(world);assert not any(r['event']=='NATIVE' for r in rows)


@pytest.mark.parametrize('mutation',['raw','binding','callback-code','module-code','duplicate-call','wrong-call','wrong-phase','negative-count','extra-field','domain-raw'])
def test_resealed_unsafe_or_cross_generation_online_rows_fail_closed(world,mutation):
    reuse=wrapped(world,'online_renderer._reused_prompt_token_ids')
    run_chat(world,lambda:reuse(SimpleNamespace(kv_transfer_params={'prompt_token_ids':[0,1]})))
    raw,_,_=packet(world)
    def change(rows):
        events=[r for r in rows if r['event']=='NATIVE'];first=events[0]['payload']['metadata']['online_input'];last=events[-1]['payload']['metadata']['online_input']
        if mutation=='raw':last['metadata']['body']=CANARY
        elif mutation=='binding':last['binding']['process_start_sha256']='d'*64
        elif mutation=='callback-code':next(r for r in rows if r['event']=='MODULE' and r['payload']['module']==online.CALLBACK_MODULE)['payload']['code_sha256']='d'*64
        elif mutation=='module-code':next(r for r in rows if r['event']=='MODULE' and 'online_code_sha256' in r['payload'])['payload']['online_code_sha256']='d'*64
        elif mutation=='duplicate-call':events[-1]['payload']['phase']='CALL';last['phase']='CALL';last['metadata']=deepcopy(first['metadata'])
        elif mutation=='wrong-call':last['binding']['call_id_sha256']=first['binding']['call_id_sha256'];last['binding']['asgi_request_id_sha256']='d'*64
        elif mutation=='wrong-phase':last['phase']='CALL'
        elif mutation=='negative-count':last['metadata']['reused']['count']=-1
        elif mutation=='extra-field':last['metadata']['parsed_fields']['tools']={'status':'unavailable'}
        elif mutation=='domain-raw':last['metadata']['token_id_domain']={'status':'collected','tokens':[CANARY]}
    with pytest.raises(ValueError):capture.parse_recording(revised(raw,change),world.prepared)


def test_native_recording_under_old_profile_never_accepts_new_namespace(world):
    reuse=wrapped(world,'online_renderer._reused_prompt_token_ids')
    run_chat(world,lambda:reuse(SimpleNamespace(kv_transfer_params={'prompt_token_ids':[0]})))
    raw,_,_=packet(world);old=deepcopy(world.prepared);old['profile']=legacy.PROFILE
    with pytest.raises(ValueError):capture.parse_recording(raw,old)
    assert profiles.asset_supported(legacy.PROFILE,Path(legacy.__file__).read_bytes())
    assert hashlib.sha256(Path(legacy.__file__).read_bytes()).hexdigest()==online.BASE_SHA256


@pytest.mark.parametrize('asset',['guard_lifecycle_base.py','guard_online_input_callback.py','guard_lifecycle_sensor.py'])
def test_dependency_bytes_and_asset_hashes_are_not_runtime_approval(world,asset):
    path=world.root/'assets'/asset;path.write_bytes(path.read_bytes()+b'\n')
    manifest=world.root/'assets/manifest.json';value=json.loads(manifest.read_text());raw=path.read_bytes()
    value['assets'][asset]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)};manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError):lifecycle.load_prepared(manifest)


def test_cli_explicit_profile_preserves_old_default_and_rejects_unknown(external):
    runner=CliRunner()
    new=runner.invoke(app,['prepare-lifecycle-sensor','--output',str(external/'online'),'--profile',online.PROFILE])
    old=runner.invoke(app,['prepare-lifecycle-sensor','--output',str(external/'legacy')])
    bad=runner.invoke(app,['prepare-lifecycle-sensor','--output',str(external/'bad'),'--profile','other'])
    assert new.exit_code==old.exit_code==0 and bad.exit_code==2
    assert json.loads(new.stdout)['profile']==online.PROFILE and json.loads(old.stdout)['profile']==legacy.PROFILE
    assert len(lifecycle.load_prepared(external/'online/manifest.json')['assets'])==5
    assert len(lifecycle.load_prepared(external/'legacy/manifest.json')['assets'])==3


def test_large_read_is_explicit_and_old_read_contract_stays_closed():
    owned_capture._read_budget(online.MAX_BYTES,owned_capture.EXTENDED_RECORDING_PROFILE)
    for profile in (None,'unknown'):
        with pytest.raises(ValueError):owned_capture._read_budget(online.MAX_BYTES,profile)
    with pytest.raises(ValueError):owned_capture._read_budget(online.MAX_BYTES+1,owned_capture.EXTENDED_RECORDING_PROFILE)


def test_large_recording_uses_actual_owned_file_protocol_and_rechecks(world):
    for _ in range(1500):world.writer.write('ASGI',{'request_sha256':'d'*64,'phase':'APP_RETURN','more_body':None})
    raw,_,_=packet(world);assert len(raw)>262144
    selected={'container':'EXAMPLE-engine','image':IMAGE,'path':'/guard-lifecycle/data/'+world.prepared['session']+'/pid-42-99.jsonl'}
    target={'run_id':'EXAMPLE-lifecycle','engine':{'container':'EXAMPLE-engine'}}
    fake_case=SimpleNamespace(raw=raw);client=Docker(fake_case)
    result=capture.capture(client,target,source=selected,prepared=world.prepared)
    assert result['process_context_verified'] and result['summary']['recording_closed']
    assert client.read_budgets==[(online.MAX_BYTES,2*online.MAX_BYTES)]*2
    assert result['online_callback_provenance']['bytes_sha256']==online.CALLBACK_SHA256
    assert result['security']=='UNKNOWN' and result['producer_authenticated'] is False


def test_standalone_seal_loads_only_fixed_base_without_installing_hooks(monkeypatch):
    calls=[]
    monkeypatch.setattr(online,'_BASE',None)
    monkeypatch.setattr(online,'_private_base',lambda:SimpleNamespace(seal=lambda path:calls.append(path)))
    online.seal()
    assert calls==['/guard-lifecycle/settings.json'] and online._BASE is None


def test_unreviewed_runtime_cannot_install_or_read_source_assets(monkeypatch):
    calls=[];monkeypatch.setattr(online,'_BASE',None)
    monkeypatch.setattr(online,'runtime_name',lambda:'cpython-3.12.9')
    monkeypatch.setattr(online,'_asset',lambda *a:calls.append(a))
    assert online.install() is False and not calls
    with pytest.raises(ValueError):online.runtime_contract()


def test_recording_cannot_mix_two_approved_compilers_in_one_process(world):
    raw,_,_=packet(world)
    other=next(name for name in online.RUNTIME_CONTRACTS if name!=online.runtime_name())
    def change(rows):
        row=next(r['payload'] for r in rows if r['event']=='MODULE' and r['payload']['module'] in online.MODULE_CONTRACTS)
        row['online_runtime']=other;row['online_code_sha256']=online.runtime_contract(other)['module_codes'][row['module']]
    with pytest.raises(ValueError,match='ONLINE_CODE_RUNTIME_MISMATCH'):
        capture.parse_recording(revised(raw,change),world.prepared)


def test_rejudge_accepts_other_fixed_compiler_contract_without_runtime_hash_approval(world):
    reuse=wrapped(world,'online_renderer._reused_prompt_token_ids')
    run_chat(world,lambda:reuse(SimpleNamespace(kv_transfer_params={'prompt_token_ids':[2]})))
    raw,_,_=packet(world)
    other=next(name for name in online.RUNTIME_CONTRACTS if name!=online.runtime_name());codes=online.runtime_contract(other)
    def change(rows):
        for row in rows:
            payload=row['payload']
            if row['event']=='MODULE' and payload['module']==online.CALLBACK_MODULE:
                payload['online_runtime']=other;payload['code_sha256']=codes['callback_code_sha256']
            elif row['event']=='MODULE' and payload['module'] in online.MODULE_CONTRACTS:
                payload['online_runtime']=other;payload['online_code_sha256']=codes['module_codes'][payload['module']]
    _,summary=capture.parse_recording(revised(raw,change),world.prepared)
    assert summary['online_runtime']==other and summary['online_callback_provenance']['code_sha256']==codes['callback_code_sha256']
    assert not summary['whole_run_coverage_verified']


def test_kubernetes_large_recording_budget_is_selected_profile_only(group,target):
    prepared={**group.prepared,'profile':online.PROFILE}
    selected=KubernetesLifecycleReader(target,group.env,prepared)
    old=KubernetesLifecycleReader(target,group.env,group.prepared)
    command=['python3','-c',owned_capture._FILE_READER,'/guard-lifecycle/data/'+prepared['session']+'/pid-42-99.jsonl',str(online.MAX_BYTES)]
    assert selected._program('engine',command,2*online.MAX_BYTES)=='file'
    with pytest.raises(KubernetesCaptureError):old._program('engine',command,2*online.MAX_BYTES)
    command[3]=command[3].replace(prepared['session'],'f'*32)
    with pytest.raises(KubernetesCaptureError):selected._program('engine',command,2*online.MAX_BYTES)
    assert selected.image_check({}, {}) is False and not selected.rows and not old.rows


def test_online_032_and_nonlab_stay_closed_before_kubernetes_io(group,target):
    called=[];prepared={**group.prepared,'profile':online.PROFILE}
    nonlab={**target,'lab':False}
    with pytest.raises(ValueError):KubernetesLifecycleReader(nonlab,group.env,prepared,runner=lambda *a,**k:called.append(1))
    environment=group.env.model_copy(deep=True);environment.engine.version='0.32.0';environment=environment.seal()
    selected={**target,'environment_ref':environment.ref().model_dump(mode='json')}
    with pytest.raises(ValueError):KubernetesLifecycleReader(selected,environment,prepared,runner=lambda *a,**k:called.append(1))
    assert not called
