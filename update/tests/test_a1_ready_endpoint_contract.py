"""Closed source/owned FIFO capabilities; no serving package or GPU execution."""
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace as NS
import json,multiprocessing,sys,types
import pytest
from guard.adapters import vllm_ready_endpoint_contract as contract,vllm_ready_endpoint_sensor as sensor
from guard.adapters import vllm_lifecycle_sensor as base,vllm_worker_startup_contract as prior
from guard.collectors.lifecycle import prepare_assets,load_prepared
from tests.test_a1_rank_failure_sensor import codes
from tests.test_a1_file_read_sensor import scope_value,MODEL_BYTES
from tests.test_lifecycle_sensor import CONTEXT

FIXTURES=Path(__file__).parent/'fixtures';REPO=Path(__file__).resolve().parents[2]
RUNTIME='cpython-'+'.'.join(map(str,sys.version_info[:3]))


def source_world(patch):
    mp=types.ModuleType(contract.MP);mp.__file__=str(FIXTURES/'vllm-mp-031/multiproc_executor.py')
    utilities=types.ModuleType(contract.UTILS);utilities.__file__=str(FIXTURES/'ready-endpoint-native/system_utils.py.txt')
    patch.setitem(sys.modules,contract.MP,mp);patch.setitem(sys.modules,contract.UTILS,utilities)
    for module in (mp,utilities):
        raw=Path(module.__file__).read_bytes();assert contract.sha(raw)==contract.SOURCE_SHA256[module.__name__]
        values=codes(compile(raw,module.__file__,'exec',dont_inherit=True,optimize=0))
        if module is utilities:
            for name in contract.HELPERS:setattr(module,name,types.FunctionType(values[name],vars(module),name))
        else:
            module.WorkerProc=type('WorkerProc',(),{'__module__':contract.MP})
            module.Connection=multiprocessing.connection.Connection
            module.UnreadyWorkerProcHandle=dataclass(type('UnreadyWorkerProcHandle',(),{
                '__module__':contract.MP,'__annotations__':{'proc':object,'rank':int,'ready_pipe':object,'death_writer':object},'death_writer':None}))
            for point in contract.POINTS:
                name=point.split('.')[1];function=types.FunctionType(values[point],vars(module),name)
                if name=='make_worker_process':function.__defaults__=(None,)
                setattr(module.WorkerProc,name,staticmethod(function))
    mp.get_mp_context=utilities.get_mp_context
    return NS(mp=mp,utilities=utilities)


def test_fixed_parent_source_qualification_does_not_call_context_getter(monkeypatch):
    world=source_world(monkeypatch)
    # Missing globals would fail if this helper were executed; qualification only compiles.
    selected=contract.qualify(world.mp,base,prior,RUNTIME)
    assert set(selected['points'])==contract.POINTS
    assert selected['get_mp_context'] is world.utilities.get_mp_context


@pytest.mark.parametrize('change',['descriptor','default','body','context-alias','context-helper','handle-getter'])
def test_parent_namespace_descriptor_body_default_or_getter_change_rejected(monkeypatch,change):
    world=source_world(monkeypatch);worker=world.mp.WorkerProc
    if change=='descriptor':worker.make_worker_process=vars(worker)['make_worker_process'].__func__
    elif change=='default':vars(worker)['make_worker_process'].__func__.__defaults__=('changed',)
    elif change=='body':worker.make_worker_process=staticmethod(lambda **kwargs:None)
    elif change=='context-alias':world.mp.get_mp_context=lambda:None
    elif change=='context-helper':world.utilities._maybe_force_spawn=lambda:None
    else:world.mp.UnreadyWorkerProcHandle.__getattribute__=lambda self,name:object.__getattribute__(self,name)
    with pytest.raises(ValueError,match='READY_ENDPOINT_'):contract.qualify(world.mp,base,prior,RUNTIME)


def test_live_owned_endpoints_distinct_domains_and_closed_endpoints():
    reader,writer=multiprocessing.Pipe(duplex=False);other_reader,other_writer=multiprocessing.Pipe(duplex=False)
    try:
        a,reason=contract.endpoint(reader,CONTEXT,'a'*32,base,RUNTIME)
        if sys.platform!='linux':
            assert a is None and reason=='unsupported-platform'
            assert contract.delegate(reader,'fileno',base,RUNTIME) is None
        else:
            b,reason_b=contract.endpoint(writer,CONTEXT,'a'*32,base,RUNTIME)
            c,reason_c=contract.endpoint(other_reader,CONTEXT,'a'*32,base,RUNTIME)
            d,_=contract.endpoint(reader,CONTEXT,'b'*32,base,RUNTIME)
            assert reason is reason_b is reason_c is None
            assert a==b and a!=c and a!=d and len(a)==64
            assert contract.endpoint(reader,{**CONTEXT,'time_namespace':None},'a'*32,base,RUNTIME)==(None,'endpoint-domain-unavailable')
            # A runtime instance override cannot become an approved descriptor.
            reader.fileno=lambda:writer.fileno()
            assert contract.endpoint(reader,CONTEXT,'a'*32,base,RUNTIME)==(None,'endpoint-delegate-unqualified')
            del vars(reader)['fileno'];reader.close()
            assert contract.endpoint(reader,CONTEXT,'a'*32,base,RUNTIME)==(None,'endpoint-unavailable')
    finally:
        for value in (reader,writer,other_reader,other_writer):value.close()


def test_public_prepare_native22_seventeen_closed_assets_preserve_original_bytes(tmp_path):
    model=tmp_path/'public.safetensors';model.write_bytes(MODEL_BYTES)
    scope=tmp_path/'scope.json';scope.write_text(json.dumps(scope_value(model)),encoding='utf-8')
    prepared=prepare_assets(tmp_path/'prepared',REPO,profile=sensor.PROFILE,model_read_scope=scope)
    loaded=load_prepared(tmp_path/'prepared/manifest.json');assert loaded['profile']==sensor.PROFILE
    assert len(loaded['assets'])==17
    protected={'guard_worker_generation.py':'vllm_worker_generation_sensor.py',
               'guard_worker_initialization.py':'vllm_worker_initialization_sensor.py',
               'guard_initialization.py':'vllm_initialization_sensor.py',
               'guard_worker_startup.py':'vllm_worker_startup_sensor.py',
               'guard_worker_readiness_callback.py':'vllm_worker_readiness.py'}
    for name,original in protected.items():
        assert (tmp_path/'prepared'/name).read_bytes()==Path(sensor.__file__).with_name(original).read_bytes()
    assert loaded['product_connected'] is False and prepared['engine_started'] is False
    path=tmp_path/'prepared/guard_ready_endpoint_contract.py';path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError):load_prepared(tmp_path/'prepared/manifest.json')
