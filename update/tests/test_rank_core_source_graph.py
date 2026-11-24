"""Exact installed core functions with stubs, without importing a vLLM engine."""
from concurrent.futures import Future
from pathlib import Path
import json
import sys
import types

import pytest

from guard.adapters import vllm_rank_ipc_hooks as ipc,vllm_rank_ipc_sources as sources,vllm_rank_rpc_hooks as rpc

ROOT=Path(__file__).parent/'fixtures/vllm-0.30.0'
MP='vllm.v1.executor.multiproc_executor'
SCHEDULE='vllm.v1.core.sched.output'


@pytest.fixture
def core(monkeypatch):
    profile,=[p for p in json.loads(sources.PROFILES.read_bytes())['profiles']if p.get('ipc_topology',{}).get('kind')=='broadcast-workers']
    modules={}
    for name in(MP,SCHEDULE):
        row=profile['rpc_sources'][name];path=ROOT/Path(row['path']+'.txt');module=types.ModuleType(name);module.__file__=str(path)
        codes=sources._codes(compile(path.read_bytes(),str(path),'exec',dont_inherit=True,optimize=0));classes={};cells={}
        for point in row['functions']:
            owner,member=point.split('.');code=codes[point]
            assert code.co_freevars in((),('__class__',))
            closure=None
            if code.co_freevars:cells.setdefault(owner,types.CellType());closure=(cells[owner],)
            classes.setdefault(owner,{})[member]=types.FunctionType(code,vars(module),member,closure=closure)
        for owner,methods in classes.items():
            setattr(module,owner,type(owner,(Future,)if owner=='FutureWrapper'else(),methods))
        for owner,cell in cells.items():cell.cell_contents=vars(module)[owner]
        if name==SCHEDULE:module.SchedulerOutput=type('SchedulerOutput',(),{'__module__':name})
        modules[name]=module;monkeypatch.setitem(sys.modules,name,module)
    rows=[];identity=ipc.IPCObserver(image=profile['image'],key=b'K'*32,emit=rows.append)
    observer=rpc.RPCObserver(image=profile['image'],key=b'K'*32,emit=rows.append,identity_observer=identity)
    abi=sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3]))
    if abi!=profile['runtime']:
        with pytest.raises(ValueError,match='RANK_IPC_SOURCE_UNAVAILABLE'):observer.attach(modules[SCHEDULE])
        assert not observer.installed
        return None
    observer.attach(modules[SCHEDULE]);observer.attach(modules[MP])
    module=modules[MP];module.envs=types.SimpleNamespace(VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=2)
    owner=module.MultiprocExecutor();owner.output_rank=0;owner.kv_output_aggregator=None;owner.ec_output_aggregator=None
    owner.parallel_config=type('Config',(),{})();owner.parallel_config.tensor_parallel_size=2
    future=module.FutureWrapper();output=object();future.set_result(output)
    calls=[]
    def collective(*args,**kwargs):calls.append((args,kwargs));return future
    owner.collective_rpc=collective
    scheduled=modules[SCHEDULE].SchedulerOutput();scheduled.num_scheduled_tokens={'EXAMPLE-private':1};scheduled.total_num_scheduled_tokens=1
    return module,observer,rows,owner,future,scheduled,calls,output


def test_exact_core_source_can_observe_deferred_return_without_gpu_worker_modules(core):
    if core is None:return  # Unsupported ABI was explicitly rejected in the fixture.
    module,observer,rows,owner,future,scheduled,calls,output=core
    assert set(observer.modules)=={MP,SCHEDULE}
    assert owner.execute_model(scheduled,non_block=True)is future
    assert future.result(timeout=None)is output and len(calls)==1
    assert [r['event']for r in rows]==['CALL','RETURN','CALL','RETURN']
    assert rows[-1]['deferred_rpc']['point']=='MultiprocExecutor.execute_model'
    assert all(r['gaps']==[]and r['engine_group_verified']is r['producer_registered']is r['source_authenticated']is False for r in rows)
    assert set(rows[0]['source_graph'])=={MP,SCHEDULE}and set(rows[-1]['source_graph'])=={MP}


def test_missing_required_core_data_module_still_refuses_observation_and_preserves_original(core):
    if core is None:return
    module,observer,rows,owner,future,scheduled,calls,output=core
    del observer.modules[SCHEDULE]
    assert owner.execute_model(scheduled,non_block=True)is future and len(calls)==1
    assert rows[-1]['diagnostic_code']=='RANK_RPC_SOURCE_GRAPH_INCOMPLETE'
    assert not observer.deferred and not any(r.get('event')=='RETURN'for r in rows)


def test_worker_dependencies_and_fault_eligibility_are_not_reduced_by_core_graph_fix():
    profile,=[p for p in json.loads(sources.PROFILES.read_bytes())['profiles']if p.get('ipc_topology',{}).get('kind')=='broadcast-workers']
    worker=profile['rpc_sources']['vllm.v1.worker.worker_base']['hooks']['WorkerWrapperBase.execute_model']
    assert set(worker['source_modules'])=={MP,SCHEDULE,'vllm.v1.worker.worker_base','vllm.v1.worker.gpu_worker'}
    assert worker['fault_eligible']is True and worker['parent']['point']=='WorkerProc._execute_worker_rpc'
    for point in('MultiprocExecutor.execute_model','FutureWrapper.result'):
        assert profile['rpc_sources'][MP]['hooks'][point].get('fault_eligible')is not True
