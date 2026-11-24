"""Real source CodeTypes and owned capture files; no engine/network/GPU calls."""
import asyncio
from hashlib import sha256
import hmac
import json
from pathlib import Path
import sys
import types

import pytest

from guard.adapters import vllm_native_input_profiles as profiles
from guard.adapters import vllm_native_input_runtime as runtime
from guard.adapters import vllm_native_input_observer as sensor
from guard.collectors import native_input as collector
from tools import verify_req10_consumption as reviewed

ROOT=Path(__file__).parent/'fixtures/vllm-0.30.0'


@pytest.fixture
def captured(tmp_path,monkeypatch):
    profile,=json.loads(profiles.PROFILES.read_bytes())['profiles']
    if sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3]))!=profile['runtime']:
        with pytest.raises(ValueError,match='NATIVE_INPUT_RUNTIME_UNAVAILABLE'):
            sensor.NativeInputObserver(profile,b'K'*32,lambda row:None)
        return None
    monkeypatch.setattr(reviewed,'verify',lambda path:{'reviewed_common_commit':'a'*64})
    prepared=tmp_path/'prepared'
    manifest=collector.prepare(prepared,image=profile['image'],repository=tmp_path/'EXAMPLE_repository')
    loaded,cfg,actual=collector.load_prepared(prepared)
    assert loaded==manifest and actual==profile
    output=tmp_path/'capture';output.mkdir(mode=0o700)
    writer=runtime.SnapshotWriter({**cfg,'output_dir':str(output)},manifest['settings_sha256'])
    observer=sensor.NativeInputObserver(profile,bytes.fromhex(cfg['key']),writer)
    modules={}
    for name,pin in profile['sources'].items():
        path=ROOT/(pin['path']+'.txt');raw=path.read_bytes()
        assert len(raw)==pin['bytes']and sha256(raw).hexdigest()==pin['sha256']
        module=types.ModuleType(name);module.__file__=str(path)
        codes=runtime.shared.sources._codes(compile(raw,str(path),'exec',dont_inherit=True,optimize=0))
        if name==profile['validation_exception']['module']:
            exec(compile(raw,str(path),'exec',dont_inherit=True,optimize=0),vars(module))
        else:
            classes={};cells={}
            for point in pin['functions']:
                code=codes[point];parts=point.split('.')
                assert code.co_freevars in((),('__class__',))
                closure=None
                if code.co_freevars:cells.setdefault(parts[0],types.CellType());closure=(cells[parts[0]],)
                function=types.FunctionType(code,vars(module),parts[-1],closure=closure)
                if len(parts)==1:setattr(module,point,function)
                else:classes.setdefault(parts[0],{})[parts[1]]=function
            for owner,methods in classes.items():setattr(module,owner,type(owner,(),methods))
            for owner,cell in cells.items():cell.cell_contents=vars(module)[owner]
        modules[name]=module;monkeypatch.setitem(sys.modules,name,module)
    for module in modules.values():observer.attach(module)
    params_module=modules['vllm.renderers.params'];params=params_module.TokenizeParams()
    params_module.VLLMValidationError=modules['vllm.exceptions'].VLLMValidationError
    params.max_input_tokens=8;params.max_output_tokens=1;params.max_total_tokens=9
    api=modules['vllm.entrypoints.openai.completion.serving'].OpenAIServingCompletion()
    async def create(request,raw_request):return params._token_len_check(None,request)
    async def cleanup(coroutine,request,raw_request):return await coroutine
    api._create_completion=create;api._with_kv_transfer_rejection_cleanup=cleanup
    def invoke(count,identifier=b'EXAMPLE-owned-input'):
        class Raw:pass
        raw=Raw();raw.scope={'type':'http','method':'POST','path':'/v1/completions',
            'headers':[(b'x-request-id',identifier)]}
        return asyncio.run(api.create_completion([0]*count,raw))
    assert invoke(8)==[0]*8
    return prepared,output,cfg,writer,invoke,modules


def test_source_profile_and_current_snapshot_capture_are_candidate_only(captured):
    if captured is None:return
    prepared,output,cfg,writer,invoke,modules=captured
    result=collector.verify_capture(prepared,output,requests={'normal':'EXAMPLE-owned-input'})
    assert result['snapshots']==4 and len(result['requests']['normal'])==4 and not result['gaps']
    assert [row['event']for row in result['rows']]==['CALL','CALL','RETURN','RETURN']
    assert all(result[key]is False for key in('scope_complete','runtime_qualified','producer_registered','source_authenticated','gpu_executed'))
    assert cfg['key']not in json.dumps(result)and 'EXAMPLE-owned-input'not in json.dumps(result)


def test_actual_native_rejection_site_is_preserved_with_no_exception_message(captured):
    if captured is None:return
    prepared,output,cfg,writer,invoke,modules=captured
    with pytest.raises(modules['vllm.exceptions'].VLLMValidationError):invoke(9)
    result=collector.verify_capture(prepared,output)
    rejected=[row for row in result['rows']if row['exception_kind']=='native-validation-error']
    assert len(rejected)==1 and rejected[0]['exception_site']['direct_raise']is True
    assert not result['gaps']and result['scope_complete']is False


@pytest.mark.parametrize('damage',['hmac','schema','source','binding','context','true-claim','extra','terminal','request','root','site'])
def test_resigned_invalid_source_and_causality_are_refused(captured,damage):
    if captured is None:return
    prepared,output,cfg,writer,invoke,modules=captured
    paths=sorted(output.glob('pid-*.json'));path=paths[-1]
    record=json.loads(path.read_bytes());value=record['snapshot'];row=value['payload']
    if damage=='schema':row['schema']='guard-native-input-hook-candidate/999'
    elif damage=='source':row['source_sha256']='f'*64
    elif damage=='binding':value['settings_sha256']='f'*64
    elif damage=='context':row['context']={**row['context'],'pid':row['context']['pid']+1}
    elif damage=='true-claim':row['producer_registered']=True
    elif damage=='extra':row['private_content']='FORBIDDEN'
    elif damage=='terminal':row['operation_started_ns']+=1
    elif damage=='request':row['request_hmac_sha256']='f'*64
    elif damage=='root':row['root_call_hmac_sha256']='f'*64
    elif damage=='site':row['exception_site']={'code_offset':0,'source_line':0,'direct_raise':True}
    record['hmac_sha256']=('0'*64 if damage=='hmac'else hmac.new(bytes.fromhex(cfg['key']),runtime.canonical(value),sha256).hexdigest())
    path.write_bytes(runtime.canonical(record))
    with pytest.raises(ValueError,match='NATIVE_INPUT_'):collector.verify_capture(prepared,output)


def test_missing_terminal_and_expected_request_are_explicit_gaps(captured):
    if captured is None:return
    prepared,output,cfg,writer,invoke,modules=captured
    path=sorted(output.glob('pid-*.json'))[-1];path.unlink()
    result=collector.verify_capture(prepared,output,requests={'new':'EXAMPLE-unobserved'})
    assert 'NATIVE_INPUT_CALL_NOT_CLOSED'in result['gaps']
    assert 'NATIVE_INPUT_EXPECTED_REQUEST_UNAVAILABLE:new'in result['gaps']and result['scope_complete']is False


def test_atomic_publication_is_complete_and_never_overwrites_existing_snapshot(captured):
    if captured is None:return
    prepared,output,cfg,writer,invoke,modules=captured
    path=sorted(output.glob('pid-*.json'))[0];raw=path.read_bytes();identity=path.stat()
    backing=[p for p in output.glob('.sealed-native-input-*.bin')if p.stat().st_ino==identity.st_ino]
    assert len(backing)==1 and backing[0].read_bytes()==raw
    writer.serial=0
    with pytest.raises(FileExistsError):writer({'schema':'EXAMPLE'})
    assert path.read_bytes()==raw and path.stat().st_ctime_ns==identity.st_ctime_ns


def test_changed_observer_asset_cannot_inherit_prepared_identity(captured):
    if captured is None:return
    prepared,output,cfg,writer,invoke,modules=captured
    path=prepared/'guard/adapters/vllm_native_input_observer.py';path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError):collector.verify_capture(prepared,output)


def test_unsupported_image_and_interpreter_are_not_qualified():
    with pytest.raises(ValueError,match='NATIVE_INPUT_PROFILE_UNAVAILABLE'):profiles.select('EXAMPLE-unpinned-image')
    profile,=json.loads(profiles.PROFILES.read_bytes())['profiles']
    with pytest.raises(ValueError,match='NATIVE_INPUT_PROFILE_UNAVAILABLE'):profiles.select(profile['image'],runtime='cpython-3.12.8')


def test_host_verification_requires_explicit_target_owner_while_sdk_requires_own_euid(captured,monkeypatch):
    if captured is None:return
    prepared,output,cfg,writer,invoke,modules=captured
    uid=(prepared/'settings.json').stat().st_uid
    monkeypatch.setattr(collector.os,'geteuid',lambda:uid+1)
    with pytest.raises(ValueError,match='NATIVE_INPUT_PREPARED_OWNER_INVALID'):collector.load_prepared(prepared)
    with pytest.raises(ValueError,match='NATIVE_INPUT_SETTINGS_UNAVAILABLE'):runtime.load_settings(prepared/'settings.json')
    manifest,_,_=collector.load_prepared(prepared,expected_uid=uid)
    result=collector.verify_capture(prepared,output,expected_uid=uid)
    assert result['snapshots']==4 and not result['gaps']and result['scope_complete']is False
    assert manifest['producer_registered']is False


@pytest.mark.parametrize('owner',[True,-1,2**31,'1000'])
def test_invalid_expected_target_owner_is_refused(captured,owner):
    if captured is None:return
    prepared,output,cfg,writer,invoke,modules=captured
    with pytest.raises(ValueError,match='NATIVE_INPUT_PREPARED_OWNER_INVALID'):
        collector.load_prepared(prepared,expected_uid=owner)
