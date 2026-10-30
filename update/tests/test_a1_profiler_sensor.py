"""Exact pinned native bodies; backend, trace sink and routes are EXAMPLE."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import asyncio,hashlib,json,os,sys,time,types
import pytest
from guard.adapters import vllm_lifecycle_sensor as base8,vllm_profiler_sensor as sensor,vllm_profiler_metadata as metadata
from tests.test_lifecycle_sensor import CONTEXT
from tests.test_a1_rank_failure_sensor import codes,QuietLogger

FIXTURES=Path(__file__).parent/'fixtures/profiler-native'
PRIVATE='EXAMPLE_PROFILER_PRIVATE_PREFIX_PATH_AND_EXCEPTION'


class Route:
    def __init__(self,path,endpoint):self.path=path;self.methods={'POST'};self.endpoint=endpoint;self.dependant=NS(call=endpoint)
class Router:
    def __init__(self,start,stop):self.routes=[Route('/start_profile',start),Route('/stop_profile',stop)]
    async def dispatch(self,path,request):return await next(r for r in self.routes if r.path==path).dependant.call(request)
class Response:
    def __init__(self,*,status_code):self.status_code=status_code


class Backend:
    def __init__(self,mode):self.mode=mode;self.running=False;self.starts=0;self.stops=0
    def start(self):
        self.starts+=1
        if self.mode=='start-failure':raise RuntimeError(PRIVATE)
        self.running=True
    def stop(self):
        self.stops+=1
        if self.mode=='backend-stop-failure':raise RuntimeError(PRIVATE)
        self.running=False


def make_world(session='a'*32,mode='normal',patch=None):
    loaded={}
    for name,filename in ((sensor.WRAPPER,'wrapper.py.txt'),(sensor.WORKER,'gpu_worker.py.txt'),(sensor.ROUTER,'api_router.py.txt')):
        path=FIXTURES/filename;raw=path.read_bytes();assert hashlib.sha256(raw).hexdigest()==sensor.SOURCE_ADDITIONS[name]
        loaded[name]=codes(compile(raw,str(path),'exec',dont_inherit=True,optimize=0))
    wrapper=types.ModuleType(sensor.WRAPPER);wrapper.__file__=str(FIXTURES/'wrapper.py.txt');wrapper.logger=QuietLogger()
    worker=types.ModuleType(sensor.WORKER);worker.__file__=str(FIXTURES/'gpu_worker.py.txt');worker.logger=QuietLogger()
    router=types.ModuleType(sensor.ROUTER);router.__file__=str(FIXTURES/'api_router.py.txt');router.logger=QuietLogger();router.Response=Response
    base_cls=type('WorkerProfiler',(),{'__module__':sensor.WRAPPER});torch_cls=type('TorchProfilerWrapper',(base_cls,),{'__module__':sensor.WRAPPER})
    worker_cls=type('Worker',(),{'__module__':sensor.WORKER});worker.Worker=worker_cls
    wrapper.WorkerProfiler=base_cls;wrapper.TorchProfilerWrapper=torch_cls
    for name,classes in sensor.TARGETS.items():
        module=wrapper if name==sensor.WRAPPER else worker
        for cls_name,methods in classes.items():
            for method in methods:setattr(vars(module)[cls_name],method,types.FunctionType(loaded[name][cls_name+'.'+method],module.__dict__,method))
    for point,name in sensor.API_POINTS.items():setattr(router,name,types.FunctionType(loaded[sensor.ROUTER][name],router.__dict__,name))
    router.router=Router(router.start_profile,router.stop_profile)
    base=types.ModuleType('_profiler_private_base');base.__file__=base8.__file__
    exec(compile(Path(base8.__file__).read_bytes(),base8.__file__,'exec',dont_inherit=True,optimize=0),base.__dict__);sensor.configure(base)
    class Observer(base.Observer):
        def __init__(self):super().__init__({'session':session},'b'*64);self.recorded=[]
        def _writer(self):return NS(context={**CONTEXT,'pid':os.getpid()})
        def emit(self,event,payload):self.recorded.append({'event':event,'payload':deepcopy(payload),'monotonic_ns':time.monotonic_ns()})
    observer=Observer()
    for module in (wrapper,worker,router):
        observer.instrument(module)
        # instrument() checks/wraps points; CheckedLoader separately emits this
        # MODULE journal in a real import. The EXAMPLE module executes selected
        # exact bodies, so supply that explicit fixture precondition from the
        # already pinned full-source code, never from an unchecked live module.
        info={'source_sha256':sensor.SOURCE_ADDITIONS[module.__name__],
            'code_sha256':base.code_digest(loaded[module.__name__]['<module>'])}
        observer.modules[module.__name__]=info
        observer.emit('MODULE',{'module':module.__name__,'verified':True,**info})
        assert observer.modules[module.__name__]['source_sha256']==sensor.SOURCE_ADDITIONS[module.__name__]
    backend=Backend(mode);profile=torch_cls();profile.__dict__.update(_active=False,_running=False,_delay_iters=1 if mode=='delayed' else 0,
        _max_iters=1 if mode=='automatic-stop' else 0,_active_iteration_count=0,_profiling_for_iters=0,
        _profiler_kwargs={},_initial_warmup_steps_remaining=0,local_rank=0,dump_device_time_total=False,dump_cpu_time_total=mode=='post-stop-output-failure',_uses_schedule=False)
    profile._maybe_add_version_metadata=lambda:None
    profile._build_profiler_table=lambda **kw:PRIVATE
    def output(*a):raise OSError(PRIVATE)
    profile._write_profiler_table=output
    wrapper.torch=NS(profiler=NS(profile=lambda **kw:backend))
    worker.create_worker_profiler=lambda *a,**kw:profile
    actor=worker_cls();actor.__dict__.update(profiler=None,profiler_config=NS(profiler='torch'),rank=0,local_rank=0)
    class Client:
        async def start_profile(self):actor.profile(True,profile_prefix=PRIVATE)
        async def stop_profile(self):actor.profile(False,profile_prefix=PRIVATE)
    router.engine_client=lambda request:Client()
    if patch is not None:
        module=types.ModuleType('vllm.distributed.utils');module.get_worker_rank_suffix=lambda **kw:'EXAMPLE-rank0'
        for name in ('vllm','vllm.distributed'):patch.setitem(sys.modules,name,types.ModuleType(name))
        patch.setitem(sys.modules,'vllm.distributed.utils',module)
    return NS(observer=observer,base=base,wrapper=wrapper,worker=worker,router=router,profile=profile,actor=actor,backend=backend)


def invoke(world,loop):
    assert loop.run_until_complete(world.router.router.dispatch('/start_profile',NS())).status_code==200
    if world.backend.mode=='delayed':world.profile.step()
    if world.backend.mode=='automatic-stop':world.profile.step();world.profile.step()
    assert loop.run_until_complete(world.router.router.dispatch('/stop_profile',NS())).status_code==200


@pytest.mark.parametrize('mode',['normal','backend-stop-failure','post-stop-output-failure','start-failure','delayed','automatic-stop'])
def test_exact_native_flags_do_not_certify_backend_stop_or_trace_completion(monkeypatch,mode):
    world=make_world(mode=mode,patch=monkeypatch);loop=asyncio.new_event_loop()
    try:invoke(world,loop)
    finally:loop.close()
    events=[r['payload'] for r in world.observer.recorded if r['event']=='PROFILER']
    assert events and all(metadata.validate_sample(p) is None for p in events)
    assert any(p['point']=='ProfilerAPI.stop_profile' and p['phase']=='RETURN' and p['api_status_code']==200 and p['registered_handler_observed'] for p in events)
    assert world.profile._running is False
    assert world.backend.running==(mode=='backend-stop-failure')
    assert all(not p['backend_collection_stopped_verified'] and not p['trace_output_completed_verified'] and not p['cross_process_rpc_verified'] for p in events)
    assert PRIVATE not in json.dumps(world.observer.recorded)
    failed=[p for p in events if p['point']=='TorchProfilerWrapper._stop' and p['phase']=='RAISE']
    assert bool(failed)==(mode in {'backend-stop-failure','post-stop-output-failure'})
    if failed:
        parent=next(p for p in events if p['call_sha256']==failed[0]['parent_call_sha256'] and p['phase']=='RETURN')
        assert parent['point']=='WorkerProfiler._call_stop' and parent['state_after']['running'] is False
    assert world.backend.starts==1 and world.backend.stops==(0 if mode=='start-failure' else 1)
    rows=[{'seq':0,'event':'START','payload':{'context':dict(CONTEXT)}}]+[
        {'seq':index+1,**row} for index,row in enumerate(world.observer.recorded)]
    capture={'records':rows,'summary':{'recording_closed':True},'process_context_verified':True,'image_digest_verified':False}
    packet={'recordings':[{'capture':capture,'session_owner_match':True,'listed_file_identity_match':True}]}
    projected=metadata.project(packet,['0'])
    assert projected['complete_calls'] and all(row['recording_closed'] for row in projected['samples'])
    assert any(row['flag_adapter_conflict_observed'] for row in projected['conflict_candidates'])==bool(failed)
    assert not projected['backend_collection_stopped_verified'] and not projected['trace_output_completed_verified']
    capture['summary']['recording_closed']=False
    unclosed=metadata.project(packet,['0'])
    assert unclosed['complete_calls']==0 and unclosed['conflict_candidates']==[]


def test_registered_callback_code_mismatch_rejects_atomically(monkeypatch):
    world=make_world(patch=monkeypatch);module=world.router
    # A separately constructed module with the same bytes but a foreign route
    # pointer must be rejected before any module/endpoint/dependant mutation.
    fresh=types.ModuleType(sensor.ROUTER);fresh.__file__=module.__file__;fresh.__dict__.update(Response=Response,logger=QuietLogger(),engine_client=lambda r:None)
    compiled=codes(compile(Path(fresh.__file__).read_bytes(),fresh.__file__,'exec',dont_inherit=True,optimize=0))
    fresh.start_profile=types.FunctionType(compiled['start_profile'],fresh.__dict__);fresh.stop_profile=types.FunctionType(compiled['stop_profile'],fresh.__dict__)
    fresh.router=Router(fresh.start_profile,fresh.stop_profile);foreign=lambda:None;fresh.router.routes[1].dependant.call=foreign
    original=fresh.start_profile
    with pytest.raises(ValueError,match='PROFILER_REGISTERED_ROUTE_UNAVAILABLE'):world.observer.instrument(fresh)
    assert fresh.start_profile is original and fresh.router.routes[0].endpoint is original and fresh.router.routes[0].dependant.call is original
    assert fresh.router.routes[1].dependant.call is foreign


def test_native_profile_uses_original_base8_bytes_and_explicit_runtime_contract():
    assert hashlib.sha256(Path(base8.__file__).read_bytes()).hexdigest()==sensor.BASE8_SHA256
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]))
    assert sensor.code_contract(base8)==sensor.RUNTIME_CODE_CONTRACTS[runtime]
