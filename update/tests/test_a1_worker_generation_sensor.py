"""Native21 exact source bodies over EXAMPLE namespaces and owned CPU pipes.

No vLLM/Torch package, real worker, model device or GPU is executed. Source
functions run with explicitly replaced dependency namespaces. Real stdlib
pipe I/O proves only this fixture's selected delegate call behavior.
"""
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import builtins,hashlib,json,multiprocessing,os,sys,threading,time,types
import pytest
from guard.adapters import vllm_lifecycle_sensor as base8,vllm_weight_iteration_sensor as iteration
from guard.adapters import vllm_file_read_sensor as scope17,vllm_safe_open_mapping_sensor as mapping
from guard.adapters import vllm_worker_startup_sensor as startup,vllm_worker_startup_contract as prior
from guard.adapters import vllm_initialization_sensor as initialization,vllm_worker_initialization_sensor as joint
from guard.adapters import vllm_worker_generation_sensor as sensor,vllm_worker_generation_contract as contract,vllm_worker_generation_metadata as metadata
from guard.collectors.model_read_scope import deploy_scope
from guard.models import canonical_json,sha256_hex
from tests.test_a1_worker_startup_sensor import source_world,Recorder
from tests.test_a1_weight_iteration_sensor import make_world as weight_world
from tests.test_a1_file_read_sensor import scope_value,MODEL_BYTES
from tests.test_a1_rank_failure_sensor import codes,QuietLogger

FIXTURES=Path(__file__).parent/'fixtures';PRIVATE='EXAMPLE_WORKER_GENERATION_PRIVATE'


def private(name,path):
    value=types.ModuleType(name);value.__file__=str(path)
    exec(compile(path.read_bytes(),str(path),'exec',dont_inherit=True,optimize=0),vars(value));return value


def make_world(tmp_path,patch,*,session='b'*32,mode='normal',path=None,scope_path=None,instrument=True):
    path=path or tmp_path/'example-public.safetensors'
    if not path.exists():path.write_bytes(MODEL_BYTES)
    if scope_path is None:
        scope_path=tmp_path/'scope.json';value=scope_value(path);scope_path.write_bytes(deploy_scope(value,canonical_json(value),session))
    inherited=private('_generation_original_iteration',Path(iteration.__file__))
    previous=private('_generation_original_startup19',Path(startup.__file__))
    callback,support=prior.load_callback(str(Path(startup.__file__).with_name('vllm_worker_readiness.py')),str(Path(startup.__file__).with_name('vllm_slot_mask.py')))
    def compose(base):
        previous.configure(base,inherited,scope17,mapping,prior,callback,support,scope_path=str(scope_path))
        joint.configure(base,initialization,previous)
        return sensor.configure(base,joint,contract)
    with patch.context() as local:
        local.setattr(iteration,'configure',compose)
        world=weight_world(session=session)
    cls=type(world.observer);context=world.observer._writer().context
    cls.emit=sensor.GenerationObservation.emit;cls._writer=sensor.GenerationObservation._writer
    world.observer.writer=Recorder(world,context)
    source=source_world(otel_available=True);world.source=source;world.mode=mode;world.cleanup=[];world.busy=[]
    wrapper=types.ModuleType(contract.WRAPPER);wrapper.__file__=str(FIXTURES/'worker-generation-native/worker_base.py.txt')
    mp=types.ModuleType(contract.MP);mp.__file__=str(FIXTURES/'vllm-mp-031/multiproc_executor.py')
    compiled={}
    for name,module in ((contract.WRAPPER,wrapper),(contract.MP,mp)):
        raw=Path(module.__file__).read_bytes();assert sha256_hex(raw)==contract.SOURCE_SHA256[name]
        compiled[name]=codes(compile(raw,module.__file__,'exec',dont_inherit=True,optimize=0))
        module.instrument=source.modules[prior.TRACING].instrument;module.logger=QuietLogger()
    worker_base=type('WorkerBase',(),{'__module__':contract.WRAPPER});wrapper.WorkerBase=worker_base
    gpu=source.worker;gpu.Worker=type('Worker',(worker_base,),{'__module__':prior.WORKER})
    platform=NS(logical_device_id_to_visible_device_id=lambda n:n,check_if_supports_dtype=lambda t:None,
        dist_backend='EXAMPLE',update_block_size_for_backend=lambda c:None,register_triton_kernel_overrides=lambda:None)
    def gpu_import(name,globals=None,locals=None,fromlist=(),level=0):
        if name=='vllm.v1.worker.gpu_model_runner':return NS(GPUModelRunner=lambda *a:NS(load_model=lambda **kw:world.run_loader()))
        return builtins.__import__(name,globals,locals,fromlist,level)
    gpu.__dict__['__builtins__']={**vars(builtins),'__import__':gpu_import}
    gpu.__dict__.update(os=NS(environ={}),torch=NS(device=lambda n:NS(type='EXAMPLE-cuda',index=0),
        accelerator=NS(device_count=lambda:1,set_device_index=lambda d:None,empty_cache=lambda:None)),
        current_platform=platform,init_worker_distributed_environment=lambda *a:None,set_random_seed=lambda v:None,
        gc=NS(collect=lambda:None),MemorySnapshot=lambda **kw:NS(EXAMPLE=True),request_memory=lambda *a:1,
        format_gib=lambda n:n,init_workspace_manager=lambda *a:None,_num_workspace_lanes=lambda *a:1,
        report_usage_stats=lambda c:None,set_current_vllm_config=lambda *a:nullcontext(),has_ec_transfer=lambda:False,
        set_torch_threads_for_runtime=lambda:None,logger=QuietLogger())
    for point,label in prior.SPAN_NAMES.items():
        name=point.split('.')[1];body=types.FunctionType(source.compiled[prior.WORKER][point],vars(gpu),name)
        if name=='load_model':body.__kwdefaults__={'load_dummy_weights':False}
        setattr(gpu.Worker,name,gpu.instrument(body,span_name=label) if label else body)
    parallel=NS(world_size=1,worker_cls='vllm.v1.worker.gpu_worker.Worker',worker_extension_cls=None,
        assigned_physical_gpu_ids=None,numa_bind=False,enable_expert_parallel=False,distributed_executor_backend='mp',
        data_parallel_backend='mp',nnodes_within_dp=1,data_parallel_rank_local=0,data_parallel_index=0,
        pipeline_parallel_size=1,prefill_context_parallel_size=1,tensor_parallel_size=1,local_world_size=1,enable_dbo=False)
    config=NS(parallel_config=parallel,enable_trace_function_call_for_thread=lambda:None,
        scheduler_config=NS(async_scheduling=False),weight_transfer_config=None,
        compilation_config=NS(cudagraph_mode=NS(_name_='FULL')))
    def worker_init(self,**kwargs):
        world.worker=self
        self.__dict__.update(rank=kwargs['rank'],local_rank=kwargs['local_rank'],vllm_config=config,parallel_config=parallel,
            device_config=NS(device_type='EXAMPLE-unsupported' if mode=='constructor-failure' else 'cuda',device=NS(type='EXAMPLE')),
            model_config=NS(dtype='EXAMPLE',seed=0,enforce_eager=False),cache_config=NS(),use_v2_model_runner=False,
            distributed_init_method='EXAMPLE',_maybe_get_memory_pool_context=lambda **kw:nullcontext(),_scoped_allocator_max_split=lambda **kw:nullcontext())
    gpu.Worker.__init__=worker_init;gpu.Worker.shutdown=lambda self:world.cleanup.append('worker-shutdown')
    def wrapper_import(name,globals=None,locals=None,fromlist=(),level=0):
        if name=='vllm.plugins':return NS(load_general_plugins=lambda:None)
        if name=='vllm.platforms':return NS(current_platform=platform)
        return builtins.__import__(name,globals,locals,fromlist,level)
    wrapper.__dict__.update(__builtins__={**vars(builtins),'__import__':wrapper_import},
        resolve_obj_by_qualname=lambda value:gpu.Worker,worker_receiver_cache_from_config=lambda *a:None,
        set_current_vllm_config=lambda *a:nullcontext())
    wrapper.WorkerWrapperBase=type('WorkerWrapperBase',(),{'__module__':contract.WRAPPER})
    for name in ('__init__','init_worker','init_device','initialize_from_config','__getattr__','shutdown'):
        body=types.FunctionType(compiled[contract.WRAPPER]['WorkerWrapperBase.'+name],vars(wrapper),name)
        if name=='__init__':body.__defaults__=(0,None)
        setattr(wrapper.WorkerWrapperBase,name,wrapper.instrument(body,span_name='Worker init') if name=='init_worker' else body)
    mp.WorkerProc=type('WorkerProc',(),{'__module__':contract.MP,'READY_STR':'READY'})
    mp.MultiprocExecutor=type('MultiprocExecutor',(),{'__module__':contract.MP})
    for point in ('WorkerProc.__init__','WorkerProc.worker_main','WorkerProc.wait_for_ready','WorkerProc.wait_for_response_handle_ready','MultiprocExecutor._init_executor'):
        cls_name,name=point.split('.');body=types.FunctionType(compiled[contract.MP][point],vars(mp),name)
        if name=='__init__':body=mp.instrument(body,span_name='Worker init')
        setattr(vars(mp)[cls_name],name,staticmethod(body) if name in ('worker_main','wait_for_ready','wait_for_response_handle_ready') else body)
    def queues(owner,*args):
        owner.worker_response_mq=NS(export_handle=lambda:None,wait_until_ready=lambda:None)
        owner.peer_response_handles=[];owner.rpc_broadcast_mq=None
    def busy(owner):
        world.busy.append('entered')
        if mode=='busy-failure':raise RuntimeError(PRIVATE)
    mp.WorkerProc._init_message_queues=queues;mp.WorkerProc.setup_proc_title_and_log_prefix=lambda *a,**kw:None
    mp.WorkerProc.monitor_death_pipe=lambda *a:None;mp.WorkerProc.worker_busy_loop=busy
    mp.WorkerProc.shutdown=lambda owner:owner.worker.shutdown()
    mp.__dict__.update(WorkerWrapperBase=wrapper.WorkerWrapperBase,signal=NS(SIGTERM=15,SIGINT=2,signal=lambda *a:None),
        threading=threading,set_worker_net_device=lambda *a:None,maybe_init_worker_tracer=lambda **kw:None,
        envs=NS(VLLM_ELASTIC_EP_SCALE_UP_LAUNCH=False),current_platform=platform,enable_envs_cache=lambda:None)
    world.config=config;world.mp=mp;world.wrapper=wrapper;world.path=path;world.scope_path=scope_path
    for name,module in {**source.modules,contract.WRAPPER:wrapper,contract.MP:mp}.items():patch.setitem(sys.modules,name,module)
    if instrument:
        for module in (wrapper,mp,gpu):world.observer.instrument(module)
    for name,module in {**source.modules,contract.WRAPPER:wrapper,contract.MP:mp}.items():
        expected=contract.SOURCE_SHA256.get(name,prior.SOURCE_SHA256.get(name));info={'source_sha256':expected,'code_sha256':'c'*64}
        world.observer.modules[name]=info;world.observer.emit('MODULE',{'module':name,'verified':True,**info})
    weights=world.modules[iteration.WEIGHTS]
    world.loader._get_weights_iterator=lambda source:weights.safetensors_weights_iterator([str(path)],False,None,None,
        safetensors_prefetch_num_threads=1,safetensors_prefetch_block_size=1)
    def start():
        reader,writer=multiprocessing.Pipe(duplex=False);world.pipe_reader=reader;world.pipe_writer=writer
        try:
            if mode=='send-failure':reader.close()
            if mode=='close-failure':
                original_close=writer._close
                def close_with_later_error():
                    original_close();raise OSError(PRIVATE)
                writer._close=close_with_later_error
            return mp.WorkerProc.worker_main(vllm_config=config,local_rank=0,rank=0,distributed_init_method='EXAMPLE',
                input_shm_handle=None,shared_worker_lock=None,is_driver_worker=True,ready_pipe=writer,death_pipe=None,inherited_fds=[])
        finally:reader.close();writer.close()
    world.start=start;world.close=lambda:world.observer._writer().close('SEALED')
    world.construct=lambda:mp.WorkerProc(vllm_config=config,local_rank=0,rank=0,distributed_init_method='EXAMPLE',
        input_shm_handle=None,shared_worker_lock=None,is_driver_worker=True)
    return world


def packet(world):
    context=world.observer.writer.context
    rows=[{'event':'START','payload':{'profile':sensor.PROFILE,'schema':sensor.SCHEMA,'session':world.observer.config['session'],'context':context}},*world.observer.recorded]
    return {'recordings':[{'session_owner_match':True,'listed_file_identity_match':True,
        'capture':{'records':[{**row,'seq':index} for index,row in enumerate(rows)],'process_context_verified':True,'image_digest_verified':False}}]}


@pytest.mark.parametrize('mode',['normal','constructor-failure','send-failure','busy-failure','close-failure'])
def test_source_generation_selected_pipe_and_original_cleanup(tmp_path,monkeypatch,mode):
    world=make_world(tmp_path,monkeypatch,mode=mode);assert world.start() is None;world.close()
    result=metadata.project(packet(world),{'profile':sensor.PROFILE,'session':world.observer.config['session']})
    assert result['generation_events'] and all(result[k] is False for k in sensor.TRUTH)
    assert world.pipe_reader.closed and world.pipe_writer.closed
    assert PRIVATE not in json.dumps(world.observer.recorded)
    if mode=='constructor-failure':
        assert not result['model_candidates'] and not any(r['original_send_return_observed'] for r in result['ready_sends'])
        assert not world.busy
    else:
        assert result['model_candidates'] and any(r['local_worker_generation_model_candidate'] for r in result['model_candidates'])
        assert world.cleanup==['worker-shutdown']
        assert any(r['original_send_return_observed'] for r in result['ready_sends'])==(mode!='send-failure')
        assert bool(world.busy)==(mode not in {'send-failure','close-failure'})
        if mode=='close-failure':
            assert any(r['original_close_raise_observed'] for r in result['ready_sends'])
            assert any(r['original_close_return_observed'] for r in result['ready_sends'])
    assert len(world.observer.generation_workers)==0 and world.observer.generation_closed


@pytest.mark.parametrize('change',['prepare-error','emit-error','binding-error'])
def test_observer_failures_do_not_repeat_or_replace_original_behavior(tmp_path,monkeypatch,change):
    world=make_world(tmp_path,monkeypatch)
    def unavailable(*args,**kwargs):raise RuntimeError(PRIVATE)
    monkeypatch.setattr(world.observer,{'prepare-error':'generation_prepare','emit-error':'generation_emit','binding-error':'generation_bind_worker'}[change],unavailable)
    assert world.start() is None
    assert world.busy==['entered'] and world.cleanup==['worker-shutdown']
    assert world.pipe_reader.closed and world.pipe_writer.closed
    world.close()


@pytest.mark.parametrize('change',['session','process','link','duplicate','missing'])
def test_recording_context_exact_links_and_missing_are_not_promoted(tmp_path,monkeypatch,change):
    world=make_world(tmp_path,monkeypatch);world.start();world.close();data=packet(world)
    rows=data['recordings'][0]['capture']['records'];prepared={'profile':sensor.PROFILE,'session':world.observer.config['session']}
    if change in {'session','process'}:
        row=next(r for r in rows if r['event']=='WORKER_GENERATION');row['payload'][change+'_sha256']='0'*64
        with pytest.raises(ValueError,match='WORKER_GENERATION_CONTEXT_MISMATCH'):metadata.project(data,prepared)
    elif change=='link':
        next(r for r in rows if r['event']=='WORKER_GENERATION_LINK')['payload']['worker_payload_sha256']='0'*64
        with pytest.raises(ValueError,match='WORKER_GENERATION_LINK_SUBSTITUTED'):metadata.project(data,prepared)
    else:
        if change=='duplicate':
            row=deepcopy(next(r for r in rows if r['event']=='WORKER_GENERATION' and r['payload']['point']=='WorkerProc.__init__' and r['payload']['phase']=='CALL'))
            row['seq']=len(rows);row['monotonic_ns']=max(r['monotonic_ns'] for r in rows if 'monotonic_ns' in r)+1;rows.append(row)
        else:rows[:]=[r for r in rows if r['event']!='WORKER_READY_SEND']
        value=metadata.project(data,prepared)
        assert value['model_candidates'] and not value['cross_process_lineage_verified']
        assert 'worker_generation_unique_call_pair_unavailable' in value['missing'] if change=='duplicate' else 'worker_selected_ready_send_unavailable' in value['missing']


@pytest.mark.parametrize('change',['descriptor','decorator','alias','source'])
def test_unqualified_source_is_rejected_before_original_methods_are_changed(tmp_path,monkeypatch,change):
    world=make_world(tmp_path,monkeypatch,instrument=False);cls=world.mp.WorkerProc;kept=vars(cls)['__init__']
    if change=='descriptor':cls.worker_main=vars(cls)['worker_main'].__func__
    elif change=='decorator':kept.__wrapped__=lambda *a:None
    elif change=='alias':world.mp.instrument=lambda *a:None
    else:
        p=tmp_path/'changed-mp.py';p.write_bytes(Path(world.mp.__file__).read_bytes()+b'\n');world.mp.__file__=str(p)
    with pytest.raises(ValueError):world.observer.instrument(world.mp)
    assert vars(cls)['__init__'] is kept
    assert not any(r['event']=='WORKER_GENERATION' for r in world.observer.recorded)
    world.close()


def test_other_thread_closed_generation_and_new_constructor_do_not_reuse_old_links(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch);owner=world.construct();assert owner.worker.worker is world.worker
    old_worker=world.worker
    before=sum(r['event']=='WORKER_GENERATION_LINK' for r in world.observer.recorded)
    errors=[]
    def other_thread():
        try:old_worker.load_model(load_dummy_weights=False)
        except BaseException as e:errors.append(type(e).__name__)
    thread=threading.Thread(target=other_thread);thread.start();thread.join()
    assert not errors and sum(r['event']=='WORKER_GENERATION_LINK' for r in world.observer.recorded)==before
    assert world.start() is None
    binding=world.observer.generation_workers.get(world.worker)
    assert binding is not None and binding['valid'] is False
    before=sum(r['event']=='WORKER_GENERATION_LINK' for r in world.observer.recorded)
    world.worker.load_model(load_dummy_weights=False)
    assert sum(r['event']=='WORKER_GENERATION_LINK' for r in world.observer.recorded)==before
    world.close();assert not world.observer.generation_workers


def test_seal_only_clears_selected_observer_and_pipe_classes_are_closed(tmp_path,monkeypatch):
    first=tmp_path/'one';second=tmp_path/'two';first.mkdir();second.mkdir()
    a=make_world(first,monkeypatch,session='a'*32);a.construct()
    b=make_world(second,monkeypatch,session='c'*32);b.construct()
    assert a.observer.generation_workers and b.observer.generation_workers
    a.close();assert not a.observer.generation_workers and b.observer.generation_workers
    assert contract.pipe_methods(NS(send=lambda v:None,close=lambda:None),b.base,b.observer.generation_runtime) is None
    b.close()


def test_ready_send_can_be_observed_while_original_worker_main_is_still_running(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch);entered=threading.Event();release=threading.Event();errors=[]
    def busy(owner):entered.set();release.wait()
    world.mp.WorkerProc.worker_busy_loop=busy
    def run():
        try:world.start()
        except BaseException as error:errors.append(type(error).__name__)
    thread=threading.Thread(target=run);thread.start()
    try:
        while thread.is_alive() and not entered.is_set():entered.wait(.01)
        assert entered.is_set() and not errors
        value=metadata.project(packet(world),{'profile':sensor.PROFILE,'session':world.observer.config['session']})
        assert any(r['original_send_return_observed'] for r in value['ready_sends'])
        assert 'worker_main_terminal_not_observed' in value['missing'] and value['ready_delivery_verified'] is False
    finally:
        release.set();thread.join();world.close()
