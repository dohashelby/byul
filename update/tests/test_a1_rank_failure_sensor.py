"""Exact fixed method code with EXAMPLE framework/MQ; owned CPU children only."""
import ast,copy,hashlib,json,multiprocessing,os,queue,threading,time,traceback,types,weakref
from collections import deque
from concurrent.futures import Future,InvalidStateError
from contextlib import suppress
from enum import Enum,auto
from pathlib import Path
from types import SimpleNamespace
import pytest
from guard.adapters import vllm_lifecycle_sensor as base8,vllm_rank_failure_sensor as sensor,vllm_rank_failure_metadata as metadata
from tests.test_lifecycle_sensor import CONTEXT

REPO=Path(__file__).resolve().parents[2]
REFERENCES=Path(__file__).parent/'fixtures/vllm-mp-031'
PRIVATE='EXAMPLE_RANK_FAILURE_PRIVATE'


class QuietLogger:
    def __getattr__(self,name):return lambda *a,**kw:None


def codes(root):
    result={root.co_qualname:root}
    for value in root.co_consts:
        if isinstance(value,types.CodeType):result.update(codes(value))
    return result


def cell(value):return (lambda:value).__closure__[0]


def reference_module(name):
    path=REFERENCES/('multiproc_executor.py' if name==sensor.MP else 'core.py');raw=path.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==sensor.SOURCES[name]
    compiled=codes(compile(raw,str(path),'exec',dont_inherit=True,optimize=0));module=types.ModuleType(name);module.__file__=str(path)
    class EngineCoreRequestType(Enum):
        WAKEUP=auto();ADD=auto();ABORT=auto();UTILITY=auto();EXECUTOR_FAILED=auto()
    class ResponseStatus(Enum):SUCCESS=auto();FAILURE=auto()
    class AsyncModelRunnerOutput:pass
    module.__dict__.update(Future=Future,InvalidStateError=InvalidStateError,deque=deque,suppress=suppress,time=time,queue=queue,
        weakref=weakref,multiprocessing=multiprocessing,threading=threading,Thread=threading.Thread,os=os,traceback=traceback,
        envs=SimpleNamespace(VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=.02,VLLM_WORKER_SHUTDOWN_TIMEOUT_SECONDS=.01),
        logger=QuietLogger(),EngineCoreRequestType=EngineCoreRequestType,AsyncModelRunnerOutput=AsyncModelRunnerOutput)
    classes={cls:type(cls,(Future,) if cls=='FutureWrapper' else (),{'__module__':name}) for cls in sensor.TARGETS[name]}
    if name==sensor.CORE:classes['EngineCore']=type('EngineCore',(),{'__module__':name})
    module.__dict__.update(classes)
    defaults={'MultiprocExecutor.execute_model':(False,),
        'MultiprocExecutor.collective_rpc':(None,(),None,False,None,None,None),
        'FutureWrapper.__init__':(lambda value:value,),'FutureWrapper.result':(None,),
        'MultiprocExecutor.start_worker_monitor':(False,)}
    wanted={cls:list(methods) for cls,methods in sensor.TARGETS[name].items()}
    if name==sensor.MP:
        wanted['MultiprocExecutor']+=['start_worker_monitor','register_failure_callback','_ensure_worker_termination']
        classes['WorkerProc'].ResponseStatus=ResponseStatus
    else:wanted['EngineCore']=['abort_requests'];classes['EngineCoreProc'].ENGINE_CORE_DEAD=b'ENGINE_CORE_DEAD'
    for cls,methods in wanted.items():
        for method in methods:
            point=cls+'.'+method;code=compiled[point]
            closure=tuple(cell(classes[cls]) if k=='__class__' else None for k in code.co_freevars)
            assert all(v is not None for v in closure)
            fn=types.FunctionType(code,module.__dict__,method,defaults.get(point),closure if closure else None)
            setattr(classes[cls],method,staticmethod(fn) if method=='_ensure_worker_termination' else fn)
    return module


class ResponseMQ:
    def __init__(self,kind='normal'):self.items=[];self.kind=kind;self.closed=False
    def enqueue(self,value):self.items.append(value)
    def dequeue(self,timeout=None):
        if self.kind in {'delay','communication-stall'}:raise TimeoutError(PRIVATE)
        if not self.items:raise AssertionError('EXAMPLE_RESPONSE_MISSING')
        return self.items.pop(0)
    def shutdown(self):self.closed=True


class BroadcastMQ:
    def __init__(self,workers):self.workers=workers;self.closed=False
    def enqueue(self,value):
        for worker in self.workers:worker._execute_worker_rpc(value)
    def shutdown(self):self.closed=True


def make_world(session='a'*32):
    base=types.ModuleType('_rank_private_base');base.__file__=base8.__file__
    exec(compile(Path(base8.__file__).read_bytes(),base8.__file__,'exec',dont_inherit=True,optimize=0),base.__dict__)
    sensor.configure(base)
    class Observer(base.Observer):
        def __init__(self):super().__init__({'session':session},'b'*64);self.recorded=[]
        def _writer(self):return SimpleNamespace(context={**CONTEXT,'pid':os.getpid()})
        def emit(self,event,payload):self.recorded.append({'event':event,'payload':copy.deepcopy(payload),'monotonic_ns':time.monotonic_ns()})
    observer=Observer();base._OBSERVER=observer;modules={}
    for name in sensor.TARGETS:
        module=reference_module(name);observer.instrument(module);modules[name]=module
        observer.modules[name]={'source_sha256':sensor.SOURCES[name],'code_sha256':'c'*64}
        observer.emit('MODULE',{'module':name,'verified':True,**observer.modules[name]})
    mp=modules[sensor.MP];executor=object.__new__(mp.MultiprocExecutor)
    executor.__dict__.update(world_size=2,output_rank=1,is_failed=False,failure_callback=None,futures_queue=deque(),workers=[],
        kv_output_aggregator=None,ec_output_aggregator=None)
    world=SimpleNamespace(base=base,observer=observer,modules=modules,executor=executor,kind='normal')
    def work(rank):
        def execute(scheduler):
            if world.kind=='exception' and rank==1:raise RuntimeError(PRIVATE)
            return SimpleNamespace(req_ids=list(scheduler.num_scheduled_tokens))
        return execute
    workers=[]
    for rank in range(2):
        worker=object.__new__(mp.WorkerProc);worker.__dict__.update(rank=rank,use_async_scheduling=False,worker=SimpleNamespace(execute_model=work(rank)),worker_response_mq=ResponseMQ())
        workers.append(worker)
    world.workers=workers;executor.rpc_broadcast_mq=BroadcastMQ(workers);executor.response_mqs=[w.worker_response_mq for w in workers]
    return world


def invoke(world,rid,kind='normal',non_block=False):
    world.kind=kind
    for worker in world.workers:worker.worker_response_mq.kind=kind
    scheduler=SimpleNamespace(num_scheduled_tokens={'native-'+rid:1},finished_req_ids=set(),sched_step_seq=1)
    return world.executor.execute_model(scheduler,non_block=non_block)


@pytest.mark.parametrize('kind',['normal','exception','delay','communication-stall'])
def test_exact_selected_rank_methods_preserve_results_and_do_not_infer_four_faults(kind):
    world=make_world()
    if kind=='normal':assert invoke(world,'owned-normal').req_ids==['native-owned-normal']
    else:
        with pytest.raises(TimeoutError if kind!='exception' else RuntimeError):invoke(world,'owned-'+kind,kind)
    rows=[r['payload'] for r in world.observer.recorded if r['event']=='RANK_FAILURE']
    assert rows and all(r['fault_activation_verified'] is False and r['cross_process_group_verified'] is False for r in rows)
    for row in rows:metadata.validate_sample(row)
    assert PRIVATE not in json.dumps(world.observer.recorded)
    assert {r['rank'] for r in rows if r['role']=='worker'}=={0,1}
    assert any(r['point']=='MultiprocExecutor.collective_rpc' and r['selected_reply_rank']==1 for r in rows)
    assert any(r['point']=='WorkerProc.enqueue_output' and r['rank']==1 for r in rows)
    assert not any(r['point']=='WorkerProc.enqueue_output' and r['rank']==0 for r in rows)
    if kind in {'delay','communication-stall'}:assert any(r['exception_kind']=='timeout' for r in rows)
    if kind=='exception':assert any(r['worker_output_is_exception'] is True for r in rows)


def test_deferred_future_is_distinct_from_its_eventual_result_and_exception():
    world=make_world();future=invoke(world,'owned-deferred',non_block=True)
    assert isinstance(future,Future) and not future.done()
    assert any(r['event']=='RANK_FAILURE' and r['payload']['point']=='MultiprocExecutor.execute_model' and r['payload']['returned_kind']=='future' for r in world.observer.recorded)
    assert future.result().req_ids==['native-owned-deferred']
    world=make_world();future=invoke(world,'owned-deferred-error','exception',non_block=True)
    with pytest.raises(RuntimeError):future.result()
    assert any(r['event']=='RANK_FAILURE' and r['payload']['future_has_exception'] is True for r in world.observer.recorded)


@pytest.mark.parametrize('damage',['raw','runtime','bool','source','phase','verified-fault','wrong-role','clock','exit-sign'])
def test_closed_rank_samples_reject_raw_or_fabricated_claims(damage):
    world=make_world();invoke(world,'owned-normal');row=copy.deepcopy(next(r['payload'] for r in world.observer.recorded if r['event']=='RANK_FAILURE' and r['payload']['phase']=='RETURN'))
    if damage=='raw':row['traceback']=PRIVATE
    elif damage=='runtime':row['observer_code_sha256']='0'*64
    elif damage=='bool':row['rank']=True
    elif damage=='source':row['source_sha256']=PRIVATE
    elif damage=='phase':row['phase']='unreviewed'
    elif damage=='verified-fault':row['fault_activation_verified']=True
    elif damage=='wrong-role':row['role']='core'
    elif damage=='clock':row['operation_finished_ns']=row['operation_started_ns']-1
    else:row['monitor_exit_code_sign']='negative'
    with pytest.raises(ValueError,match='RANK_FAILURE_SAMPLE_INVALID'):metadata.validate_sample(row)


def _owned_child(connection,kind):
    try:
        connection.send(('ready',os.getpid()))
        if kind=='death':os._exit(7)
        if kind=='exception':
            try:raise RuntimeError(PRIVATE)
            except RuntimeError:connection.send(('failure',));return
        if kind=='delay':time.sleep(.15);connection.send(('returned',));return
        connection.recv();connection.send(('released',))
    finally:connection.close()


@pytest.mark.parametrize('kind',['exception','death','delay','communication-stall'])
def test_owned_cpu_children_keep_distinct_fault_setup_and_cleanup(kind):
    # This is an owned CPU harness, not a vLLM/TP worker or trusted fault producer.
    context=multiprocessing.get_context('spawn');parent,child=context.Pipe();process=context.Process(target=_owned_child,args=(child,kind));process.start();child.close()
    try:
        assert parent.poll(5);ready=parent.recv();assert ready==('ready',process.pid)
        if kind=='death':process.join(5);assert process.exitcode==7
        elif kind=='exception':assert parent.poll(5) and parent.recv()==('failure',)
        elif kind=='delay':
            assert not parent.poll(.02) and process.is_alive();assert parent.poll(5) and parent.recv()==('returned',)
        else:
            assert not parent.poll(.02) and process.is_alive();parent.send('release');assert parent.poll(5) and parent.recv()==('released',)
    finally:
        parent.close();process.join(5)
        if process.is_alive():process.terminate();process.join(5)
        assert not process.is_alive();process.close()


def test_owned_child_death_monitor_and_core_handoff_are_distinct_from_client_receipt():
    world=make_world();invoke(world,'owned-before-death');core_module=world.modules[sensor.CORE]
    core=object.__new__(core_module.EngineCoreProc)
    core.__dict__.update(model_executor=world.executor,input_queue=queue.Queue(),output_queue=queue.Queue(),
        output_thread=SimpleNamespace(join=lambda timeout:None,is_alive=lambda:False))
    marker=core_module.EngineCoreRequestType.EXECUTOR_FAILED
    world.executor.register_failure_callback(lambda:core.input_queue.put_nowait((marker,b'')))
    context=multiprocessing.get_context('spawn');parent,child=context.Pipe();process=context.Process(target=_owned_child,args=(child,'death'));process.start();child.close()
    try:
        assert parent.poll(5) and parent.recv()==('ready',process.pid)
        process.join(5);assert process.exitcode==7
        world.executor.workers=[SimpleNamespace(proc=process,rank=1,death_writer=None,worker_response_mq=None)]
        world.executor.start_worker_monitor(inline=True)
        assert world.executor.is_failed and world.executor.shutting_down
        args=core.input_queue.get_nowait()
        with pytest.raises(RuntimeError,match='Executor failed'):core._handle_client_request(*args)
        core._send_engine_dead();assert core.output_queue.get_nowait()==core_module.EngineCoreProc.ENGINE_CORE_DEAD
        rows=[r['payload'] for r in world.observer.recorded if r['event']=='RANK_FAILURE']
        monitor=next(r for r in rows if r['monitor_caller_verified'] and r['phase']=='CALL')
        assert monitor['monitor_dead_pid']==process.pid and monitor['monitor_dead_rank']==1 and monitor['monitor_exit_code_abs']==7
        assert any(r['executor_failed_marker_observed'] for r in rows)
        assert all(r['upper_client_receipt_verified'] is False and r['fault_activation_verified'] is False for r in rows)
        for row in rows:metadata.validate_sample(row)
        assert PRIVATE not in json.dumps(rows)
    finally:
        parent.close();process.join(5)
        if process.is_alive():process.terminate();process.join(5)
        assert not process.is_alive();process.close()


@pytest.mark.parametrize('damage',['runtime','optimize','target-code','source-file'])
def test_unknown_runtime_source_or_target_code_never_installs_new_hooks(monkeypatch,damage):
    if damage in {'runtime','optimize'}:
        if damage=='runtime':monkeypatch.setattr(sensor,'RUNTIME_CODE_CONTRACTS',{})
        else:monkeypatch.setattr(sensor,'sys',SimpleNamespace(version_info=sensor.sys.version_info,implementation=sensor.sys.implementation,flags=SimpleNamespace(optimize=1)))
        with pytest.raises(ValueError):make_world()
        return
    world=make_world();module=reference_module(sensor.MP);original=module.MultiprocExecutor.execute_model
    if damage=='target-code':module.WorkerProc.enqueue_output=lambda *a:PRIVATE
    else:module.__file__=str(REFERENCES/'core.py')
    with pytest.raises(ValueError):world.observer.instrument(module)
    assert module.MultiprocExecutor.execute_model is original


def test_late_target_mutation_and_observer_failure_preserve_existing_native_evidence():
    world=make_world();invoke(world,'owned-before-mutation');wrapped=world.modules[sensor.MP].MultiprocExecutor.execute_model
    original=wrapped.__wrapped__
    def changed(*a,**kw):return 71
    original.__code__=changed.__code__
    assert world.executor.execute_model(None)==71
    assert world.observer.recorded[-1]['payload']=={'module':sensor.MP,'verified':False}
    assert any(r['event']=='RANK_FAILURE' for r in world.observer.recorded)
    world=make_world()
    def broken(*a):raise KeyboardInterrupt(PRIVATE)
    world.observer.emit=broken
    assert invoke(world,'owned-observer-failure').req_ids==['native-owned-observer-failure']
    assert world.observer.disabled_pid==os.getpid()


@pytest.mark.parametrize('damage',['none','unverified-process','unverified-source','broken-parent','declared-rank-mismatch'])
def test_projection_preserves_missing_faults_and_does_not_accept_unbound_failure_claims(damage):
    world=make_world()
    with pytest.raises(RuntimeError):invoke(world,'owned-projection','exception')
    rows=[{'seq':0,'event':'START','monotonic_ns':0,'payload':{'context':CONTEXT}}]
    rows.extend({**r,'seq':i+1} for i,r in enumerate(world.observer.recorded))
    if damage in {'unverified-source','broken-parent'}:
        for row in rows:
            if row['event']!='RANK_FAILURE':continue
            if damage=='unverified-source':row['payload']['source_verified']=False
            elif row['payload']['parent_call_sha256'] is not None:row['payload']['parent_call_sha256']='0'*64
    recording={'capture':{'records':rows,'process_context_verified':damage!='unverified-process','image_digest_verified':False},
        'session_owner_match':True,'listed_file_identity_match':True}
    limits={'rank_expected_ranks':['0'] if damage=='declared-rank-mismatch' else ['0','1'],'rank_output_rank':0 if damage=='declared-rank-mismatch' else 1}
    for kind in metadata.FAULT_KINDS:
        for step in ('detection','notification'):limits['rank_'+kind.replace('-','_')+'_'+step+'_ms']=20
    result=metadata.project({'recordings':[recording]},limits)
    assert result['samples'] and result['calls'] and len(result['fault_cases'])==4
    assert not result['producer_authenticated'] and not result['all_required_ranks_completed'] and not result['upper_client_receipt_verified']
    assert bool(result['source_failure_candidates'])==(damage not in {'unverified-process','unverified-source'})
    assert all(not c['four_fault_kind_inferred'] and not c['confirmed_violation'] for c in result['source_failure_candidates'])
    assert all(not c['whole_kind_complete'] and not c['fault_activation_verified'] for c in result['fault_cases'])
    if damage=='broken-parent':assert not any(c['parent_interval_observed'] for c in result['calls'])
    if damage=='declared-rank-mismatch':assert any('declared' in c['kind'] or 'declaration' in c['kind'] for c in result['source_failure_candidates'])
