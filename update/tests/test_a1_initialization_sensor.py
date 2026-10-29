"""Exact pinned preparation code; MQ/config doubles are explicitly EXAMPLE."""
from copy import deepcopy
from collections import deque
from contextlib import nullcontext
import hashlib,json,multiprocessing,os,threading,time,types,weakref
from pathlib import Path
from types import SimpleNamespace as NS
from multiprocessing.connection import Connection
import pytest
from guard.adapters import vllm_lifecycle_sensor as base8,vllm_initialization_sensor as sensor,vllm_initialization_metadata as metadata
from tests.test_lifecycle_sensor import CONTEXT
from tests.test_a1_rank_failure_sensor import codes,QuietLogger

REPO=Path(__file__).resolve().parents[2]
REFERENCE=Path(__file__).parent/'fixtures/vllm-mp-031/multiproc_executor.py'
PRIVATE='EXAMPLE_INITIALIZATION_PRIVATE'


class MQ:
    def __init__(self,*a,**kw):pass
    def export_handle(self):return None
    def wait_until_ready(self):pass
    @staticmethod
    def create_from_handle(*a):return MQ()


def make_world(session='a'*32,mode='normal'):
    raw=REFERENCE.read_bytes();assert hashlib.sha256(raw).hexdigest()==sensor.SOURCE_ADDITIONS[sensor.MP]
    compiled=codes(compile(raw,str(REFERENCE),'exec',dont_inherit=True,optimize=0))
    module=types.ModuleType(sensor.MP);module.__file__=str(REFERENCE)
    executor_cls=type('MultiprocExecutor',(),{'__module__':sensor.MP});worker_cls=type('WorkerProc',(),{'__module__':sensor.MP})
    class OMP:
        def __init__(self,config):pass
        def configure_omp_envs(self,**kwargs):return nullcontext()
    module.__dict__.update(weakref=weakref,deque=deque,FutureWrapper=object,WorkerProc=worker_cls,MessageQueue=MQ,
        MultiprocExecutor=executor_cls,Connection=multiprocessing.connection.Connection,multiprocessing=multiprocessing,
        cast=lambda cls,value:value,WorkerProcHandle=NS,OMPProcessManager=OMP,logger=QuietLogger(),
        set_multiprocessing_worker_envs=lambda n:None,aiter_requires_tcp_store=lambda:False,
        get_file_store_init_method=lambda:None,get_ip=lambda:None,set_torch_threads_for_runtime=lambda:None,
        get_mp_context=lambda:NS(Lock=threading.Lock,get_start_method=lambda:'spawn'),envs=NS(VLLM_MQ_MAX_CHUNK_BYTES_MB=1))
    for cls_name,methods in sensor.TARGETS.items():
        cls=vars(module)[cls_name]
        for method in methods:
            point=cls_name+'.'+method;fn=types.FunctionType(compiled[point],module.__dict__,method)
            setattr(cls,method,staticmethod(fn) if point in sensor.STATIC_POINTS else fn)
    module.WorkerProcHandle=type('WorkerProcHandle',(),{})
    module.WorkerProcHandle.from_unready_handle=staticmethod(lambda h,mq,peer_worker_response_mqs:NS(
        rank=h.rank,proc=h.proc,worker_response_mq=mq,peer_worker_response_mqs=peer_worker_response_mqs))
    base=types.ModuleType('_initialization_private_base');base.__file__=base8.__file__
    exec(compile(Path(base8.__file__).read_bytes(),base8.__file__,'exec',dont_inherit=True,optimize=0),base.__dict__)
    sensor.configure(base)
    class Observer(base.Observer):
        def __init__(self):super().__init__({'session':session},'b'*64);self.recorded=[]
        def _writer(self):return NS(context={**CONTEXT,'pid':os.getpid()})
        def emit(self,event,payload):self.recorded.append({'event':event,'payload':deepcopy(payload),'monotonic_ns':time.monotonic_ns()})
    observer=Observer();observer.instrument(module);observer.modules[sensor.MP]={'source_sha256':sensor.SOURCE_ADDITIONS[sensor.MP],'code_sha256':'c'*64}
    observer.emit('MODULE',{'module':sensor.MP,'verified':True,**observer.modules[sensor.MP]})
    world=NS(observer=observer,module=module,base=base,created=[],terminated=[],mode=mode)
    def make_worker(**kw):
        rank=kw['rank'];reader,writer=multiprocessing.Pipe(duplex=False);death_reader,death_writer=multiprocessing.Pipe(duplex=False)
        handle=NS(rank=rank,ready_pipe=reader,death_writer=death_writer,proc=NS(_popen=NS(pid=os.getpid())))
        world.created.append((handle,death_reader))
        if not (world.mode=='eof' and rank==1):
            writer.send({'status':'NOT_READY' if world.mode=='partial-failure' and rank==1 else 'READY',
                'handle':NS(local_reader_ranks=[0]),'peer_response_handles':[]})
        writer.close();return handle
    worker_cls.make_worker_process=staticmethod(make_worker)
    executor=executor_cls();executor.__dict__.update(world_size=2,local_world_size=2,monitor_workers=False,vllm_config=NS(),
        parallel_config=NS(data_parallel_size_local=1,node_rank_within_dp=0,node_rank=0,master_addr=None),
        shutdown=lambda:None,_get_parallel_sizes=lambda:(2,1,1),_is_driver_worker=lambda rank:rank==0,
        _post_init_executor=lambda:None,_get_output_rank=lambda:0,_ensure_worker_termination=lambda procs:world.terminated.extend(procs))
    world.executor=executor;return world


def invoke(world):
    try:return world.executor._init_executor()
    finally:
        finalizer=world.executor.__dict__.get('_finalizer')
        if finalizer:finalizer.detach()
        for handle,reader in world.created:
            handle.ready_pipe.close();reader.close()
            if handle.death_writer:handle.death_writer.close()


def packet(world,bound=True):
    rows=[{'event':'START','payload':{'context':CONTEXT}},*world.observer.recorded]
    rows=[{**row,'seq':index} for index,row in enumerate(rows)]
    return {'recordings':[{'session_owner_match':True,'listed_file_identity_match':True,
        'capture':{'records':rows,'process_context_verified':bound,'image_digest_verified':False}}]}


@pytest.mark.parametrize('mode',['normal','partial-failure','eof','recovered-normal'])
def test_exact_startup_static_descriptors_aggregate_and_cleanup(mode):
    world=make_world(mode=mode)
    assert type(vars(world.module.WorkerProc)['wait_for_ready']) is staticmethod
    if mode in {'partial-failure','eof'}:
        with pytest.raises(Exception,match='WorkerProc initialization failed'):invoke(world)
        assert len(world.terminated)==2
        assert all(h.death_writer is None for h,r in world.created)
    else:
        assert invoke(world) is None and len(world.executor.workers)==2 and world.executor.output_rank==0
        assert not world.terminated
    rows=[r['payload'] for r in world.observer.recorded if r['event']=='INITIALIZATION']
    assert rows and PRIVATE not in json.dumps(rows)
    for row in rows:metadata.validate_sample(row)
    projection=metadata.project(packet(world),{'init_expected_ranks':['0','1']})
    correspondence,=projection['correspondence'];assert correspondence['same_process_interval_observed']
    assert correspondence['ready_failure_propagation_observed']==(mode in {'partial-failure','eof'})
    assert correspondence['required_rank_ready_return_observed']==(mode not in {'partial-failure','eof'})
    assert not correspondence['confirmed_violation'] and not projection['whole_scope_complete']
    for flag in ('producer_authenticated','failure_activation_verified','cross_phase_lineage_verified','service_gate_verified'):
        assert projection[flag] is False


def test_dead_process_claims_stay_unverified_and_changed_rank_declaration_is_not_normal():
    world=make_world();invoke(world)
    unbound=metadata.project(packet(world,False),{'init_expected_ranks':['0','1']})
    assert unbound['samples'] and all(r['safe_file_claim_retained'] and not r['process_binding_verified'] for r in unbound['samples'])
    assert all(not r['same_process_interval_observed'] for r in unbound['correspondence'])
    mismatch=metadata.project(packet(world),{'init_expected_ranks':['0']})
    assert not mismatch['correspondence'][0]['required_rank_ready_return_observed']
    assert not mismatch['correspondence'][0]['declared_world_rank_workers_match']


def test_descriptor_or_late_code_replacement_fails_closed_without_changing_original_call():
    world=make_world();fn=vars(world.module.WorkerProc)['wait_for_ready'].__func__.__wrapped__
    fn.__code__=(lambda *args,**kw:[]).__code__
    assert world.module.WorkerProc.wait_for_ready([])==[]
    assert sensor.MP not in world.observer.modules
    assert not [r for r in world.observer.recorded if r['event']=='INITIALIZATION']


@pytest.mark.parametrize('key,value',[('failure_activation_verified',True),('service_gate_verified',True),('response_status',PRIVATE),
    ('world_size',True),('returned_handles',[{'rank':0,'pid':0}]),('source_sha256','0'*64)])
def test_closed_metadata_rejects_unsafe_or_overclaimed_values(key,value):
    world=make_world();invoke(world);sample=deepcopy(next(r['payload'] for r in world.observer.recorded if r['event']=='INITIALIZATION' and r['payload']['phase']=='RETURN'))
    sample[key]=value
    with pytest.raises(ValueError,match='INITIALIZATION_SAMPLE_INVALID'):metadata.validate_sample(sample)
