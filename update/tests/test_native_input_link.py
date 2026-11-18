"""Owned files and exact CodeTypes; injected transport is never GPU evidence."""
from copy import deepcopy
from hashlib import sha256
import json
import importlib
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import types

import pytest

from guard.collectors import native_input as collector
from guard.adapters import vllm_native_input_runtime as runtime
from guard.executors import native_input_link as link
from guard.executors.base import EvidenceDraft
from guard.models import DataOrigin,ExecStatus
from guard.runtime.kubernetes_native_input import KubernetesNativeInputHTTP,READER
from guard.runtime.kubernetes_http import INPUT_VALIDATION_PROFILE,KubernetesBindingError
from tests.test_native_input_capture import captured
from tests.test_kubernetes_input_validation import transport,request,TOKENS


def test_native_transport_requires_explicit_single_use_header_and_keeps_original_body_gate(tmp_path):
    base=transport(tmp_path)
    native=object.__new__(KubernetesNativeInputHTTP);native.__dict__.update(base.__dict__)
    native._native_ids=set();native._native_pending=set()
    native.declare_input_contract({'path':'/v1/completions','control':TOKENS,
        'prompt_tokens':{'max_length':128,'max_value':151936},'fields':{}})
    identifier='byul-input-'+'1'*32+'-before'
    with pytest.raises(KubernetesBindingError):native._input_validation_request(request(TOKENS,headers={'X-Request-Id':identifier}))
    native.declare_request_id(identifier)
    with pytest.raises(KubernetesBindingError):native._input_validation_request(request({**TOKENS,'prompt':'FORBIDDEN'},headers={'X-Request-Id':identifier}))
    native._input_validation_request(request(TOKENS,headers={'X-Request-Id':identifier}))
    with pytest.raises(KubernetesBindingError):native._input_validation_request(request(TOKENS,headers={'X-Request-Id':identifier}))
    with pytest.raises(KubernetesBindingError):native.declare_request_id(identifier)
    with pytest.raises(KubernetesBindingError):base._input_validation_request(request(TOKENS,headers={'X-Request-Id':identifier}))


@pytest.fixture
def connected(captured,monkeypatch,tmp_path):
    if captured is None:return None
    prepared,output,cfg,writer,invoke,modules=captured
    manifest,_,_=collector.load_prepared(prepared)
    # The fixed read program runs against owned files through an owned path
    # substitution only. It imports no engine and its original bytes are parsed.
    reader=types.ModuleType('EXAMPLE_owned_native_reader');exec(compile(READER.read_bytes(),str(READER),'exec'),vars(reader))
    original_open=os.open
    def owned_open(path,*args,**kwargs):
        return original_open(str(prepared)if path=='/guard-native-input'else path,*args,**kwargs)
    monkeypatch.setattr(os,'open',owned_open)
    folder=prepared/'data'/cfg['session']
    for name in folder.iterdir():assert False,'fresh capture directory is expected'
    def records(_manifest):
        for path in output.glob('pid-*.json'):
            target=folder/path.name
            if not target.exists():os.link(path,target)
        return reader.run({k:manifest[k]for k in('session','settings_sha256','assets')})
    native=object.__new__(KubernetesNativeInputHTTP);native.binding={'EXAMPLE_owned_generation':'a'}
    native._native_ids=set();native._native_pending=set();native.injected=True
    monkeypatch.setattr(native,'native_input_records',records)
    ctx=NS(check=NS(id='REQ-09'),target={'lab':True},env=NS(engine=NS(image_digest=manifest['image'])),
        cprobe=native,workdir=tmp_path/'work')
    spec={'prepared_dir':str(prepared),'prepared_sha256':sha256(runtime.canonical(manifest)).hexdigest()}
    capture=link.Capture(ctx,spec)
    return capture,ctx,invoke,manifest,records,output


def test_current_dispatch_uses_fresh_id_and_original_request_remains_unchanged(connected):
    if connected is None:return
    capture,ctx,invoke,manifest,records,output=connected
    request={'method':'POST','headers':{},'body':{'prompt':[0]}}
    original=deepcopy(request);calls=[]
    def dispatch(transmitted):
        calls.append(transmitted)
        assert invoke(8,transmitted['headers']['X-Request-Id'].encode())==[0]*8
        return {'outcome':200}
    assert capture.call('before',request,dispatch)=={'outcome':200}
    assert len(calls)==1 and request==original
    result=capture.finish()
    assert result['capture_origin']=='EXAMPLE'and result['target_generation_stable']is True
    assert len(result['source_capture']['requests']['before'])==4 and not result['source_capture']['gaps']
    assert all(result[k]is False for k in('runtime_qualified','producer_registered','scope_complete'))
    assert capture.requests['before']not in json.dumps(result)
    assert len(list((ctx.workdir/'native-input-originals').glob('pid-*.json')))==8


@pytest.mark.parametrize('damage',['generation','request-window','baseline','clock','invalid-private-row'])
def test_bad_current_read_does_not_get_persisted_as_current_proof(connected,monkeypatch,damage):
    if connected is None:return
    capture,ctx,invoke,manifest,records,output=connected
    def dispatch(transmitted):
        invoke(8,transmitted['headers']['X-Request-Id'].encode());return {'outcome':200}
    capture.call('before',{'headers':{},'body':{}},dispatch)
    if damage=='generation':capture.transport.binding={'EXAMPLE_owned_generation':'b'}
    elif damage=='request-window':capture.before['finished_ns']=2**62
    elif damage=='baseline':capture.before['files'][0]['sha256']='f'*64
    elif damage=='clock':capture.before['clock_wall_finished_ns']+=1_000_000_000
    else:
        original=records
        def malicious(_manifest):
            value=original(_manifest);record=value['files'][-1]
            document=json.loads(bytes.fromhex(record['raw_hex']));document['snapshot']['payload']['private_text']='FORBIDDEN'
            raw=runtime.canonical(document);record['raw_hex']=raw.hex();record['sha256']=sha256(raw).hexdigest()
            return value
        monkeypatch.setattr(capture.transport,'native_input_records',malicious)
    with pytest.raises(ValueError,match='NATIVE_INPUT_'):capture.finish()
    assert not(ctx.workdir/'native-input-originals').exists()


def test_runtime_freezes_source_and_rejects_early_loaded_engine(captured,monkeypatch):
    if captured is None:return
    prepared,output,cfg,writer,invoke,modules=captured
    monkeypatch.setattr(runtime,'ROOT',prepared)
    runtime.freeze(cfg)
    monkeypatch.setattr(runtime,'_INSTALLED',None)
    before=list(sys.meta_path)
    with pytest.raises(ValueError,match='NATIVE_INPUT_SOURCE_ALREADY_LOADED'):runtime.install()
    assert sys.meta_path==before


def test_runtime_refuses_replaced_observer_function_before_install(captured,monkeypatch):
    if captured is None:return
    prepared,output,cfg,writer,invoke,modules=captured
    monkeypatch.setattr(runtime,'ROOT',prepared)
    monkeypatch.setattr(runtime.sensor.NativeInputObserver,'attach',lambda self,module:None)
    with pytest.raises(ValueError):runtime.freeze(cfg)


def test_failed_capture_keeps_previous_observations_and_cannot_remain_measured(tmp_path):
    class Failed:
        cases=[{'case':'before','dispatched':True}]
        def finish(self):raise ValueError('PRIVATE_ERROR_MUST_NOT_BE_SAVED')
    ctx=NS(_native_input_capture=Failed(),workdir=tmp_path,secrets=[])
    draft=EvidenceDraft(status=ExecStatus.COMPLETED,data_origin=DataOrigin.MEASURED,
        normal_control={'attempted':True,'succeeded':True},
        observations={'previous_violation':{'value':True,'collected':True}})
    result=link.finish(ctx,draft)
    assert result.status==ExecStatus.ABORTED and result.data_origin==DataOrigin.DECLARED
    assert result.observations['previous_violation'].value is True
    assert 'NATIVE_INPUT_CAPTURE_INCOMPLETE'in result.cleanup_errors and len(result.artifacts)==1
    assert 'PRIVATE_ERROR_MUST_NOT_BE_SAVED'not in result.artifacts[0].path.read_text()


def test_unconfigured_capture_preserves_original_dispatch_and_draft():
    request={'headers':{},'body':object()};result=object();calls=[]
    ctx=NS()
    assert link.case_call(ctx,'before',request,lambda actual:calls.append(actual)or result)is result
    assert calls==[request]and calls[0]is request
    draft=object();assert link.finish(ctx,draft)is draft


def test_prepared_private_package_uses_only_its_pinned_helper_modules(captured,monkeypatch):
    if captured is None:return
    prepared,output,cfg,writer,invoke,modules=captured
    name='_EXAMPLE_native_input_private_sdk'
    spec=importlib.util.spec_from_file_location(name,prepared/'guard/__init__.py',
        submodule_search_locations=[str(prepared/'guard')])
    package=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,name,package)
    spec.loader.exec_module(package)
    existing=set(sys.modules)
    try:
        private=importlib.import_module(name+'.adapters.vllm_native_input_runtime')
        assert private.sensor.ipc is private.shared.hooks and private.sensor.source is private.shared.sources
        assert private.sensor.ipc is not runtime.shared.hooks
        assert private.sensor.NativeInputObserver.__module__.startswith(name+'.')
        private.ROOT=prepared;private.freeze(cfg)
        assert not any(module.startswith('vllm.')for module in set(sys.modules)-existing)
    finally:
        for module in set(sys.modules)-existing:
            if module.startswith(name+'.'):sys.modules.pop(module,None)
