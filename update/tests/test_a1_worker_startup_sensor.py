"""Profile19 EXAMPLE source bodies/OTel wrappers and original B callback.

No actual vLLM/Torch/OTel package, Pod, worker or GPU is imported or executed.
"""
from contextlib import contextmanager,nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import functools,hashlib,inspect,json,os,sys,threading,time,types
import pytest
from guard.adapters import vllm_lifecycle_sensor as base8,vllm_weight_iteration_sensor as iteration
from guard.adapters import vllm_file_read_sensor as scope17,vllm_safe_open_mapping_sensor as mapping
from guard.adapters import vllm_worker_startup_contract as contract,vllm_worker_startup_sensor as sensor,vllm_worker_startup_metadata as metadata
from guard.collectors.model_read_scope import deploy_scope,scope_summary
from guard.models import canonical_json,sha256_hex
from tests.test_a1_rank_failure_sensor import codes,QuietLogger
from tests.test_a1_weight_iteration_sensor import make_world as iteration_world,packet,PRIVATE
from tests.test_a1_file_read_sensor import scope_value,MODEL_BYTES

FIXTURES=Path(__file__).parent/'fixtures'


def source_world(*,otel_available):
    modules={}
    paths={contract.WORKER:FIXTURES/'profiler-native/gpu_worker.py.txt',
        **{name:FIXTURES/'worker-startup-native'/filename for name,filename in contract.SOURCE_FILENAMES.items() if name in contract.SOURCE_SHA256 and name!=contract.WORKER}}
    compiled={}
    for name,path in paths.items():
        raw=path.read_bytes();assert sha256_hex(raw)==contract.SOURCE_SHA256[name]
        compiled[name]=codes(compile(raw,str(path),'exec',dont_inherit=True,optimize=0))
        module=types.ModuleType(name);module.__file__=str(path);modules[name]=module
    otel=modules[contract.OTEL];tracing=modules[contract.TRACING];worker=modules[contract.WORKER]
    for name in contract.BACKEND_NAMES:
        value=types.FunctionType(compiled[contract.OTEL][name],vars(otel),name);setattr(otel,name,value);setattr(tracing,name,value)
    otel._IS_OTEL_AVAILABLE=otel_available;otel.functools=functools;otel.inspect=inspect;otel.original_spans=[]
    def span(*args,**kwargs):otel.original_spans.append(True);return nullcontext()
    otel.trace=NS(get_tracer=lambda *a:NS(start_as_current_span=span),get_current_span=lambda:NS(get_span_context=lambda:NS(is_valid=True)))
    otel.LoadingSpanAttributes=NS(CODE_FUNCTION='code.function',CODE_NAMESPACE='code.namespace',CODE_FILEPATH='code.filepath',CODE_LINENO='code.lineno')
    otel.TRACE_HEADERS=();otel.os=os;otel.inject=lambda target:None
    otel._get_smart_context=types.FunctionType(compiled[contract.OTEL]['_get_smart_context'],vars(otel))
    otel.propagate_trace_to_env=contextmanager(types.FunctionType(compiled[contract.OTEL]['propagate_trace_to_env'],vars(otel)))
    tracing.functools=functools;tracing._REGISTERED_TRACING_BACKENDS={'otel':tuple(vars(otel)[name] for name in contract.BACKEND_NAMES)}
    tracing.instrument=types.FunctionType(compiled[contract.TRACING]['instrument'],vars(tracing),'instrument');tracing.instrument.__defaults__=(None,)
    tracing.instrument.__kwdefaults__={'span_name':'','attributes':None,'record_exception':True}
    worker.instrument=tracing.instrument
    cls=type('Worker',(),{'__module__':contract.WORKER});worker.Worker=cls
    for point,label in contract.SPAN_NAMES.items():
        method=point.split('.')[1];body=types.FunctionType(compiled[contract.WORKER][point],vars(worker),method)
        if method=='load_model':body.__kwdefaults__={'load_dummy_weights':False}
        setattr(cls,method,tracing.instrument(body,span_name=label) if label else body)
    return NS(modules=modules,worker=worker,otel=otel,compiled=compiled)


class Recorder:
    def __init__(self,world,context):
        self.world=world;self.context=context;self.seq=len(world.observer.recorded)+1;self.lock=threading.RLock();self.closed=False;self.fd=None
    def write(self,event,payload):
        if self.closed:return
        self.world.observer.recorded.append({'event':event,'payload':deepcopy(payload),'monotonic_ns':time.monotonic_ns()});self.seq+=1
    def close(self,reason):
        if not self.closed:self.write('END',{'reason':reason});self.closed=True


def make_world(tmp_path,patch,*,otel_available=False,mode='normal',session='b'*32,path=None,scope_path=None):
    path=path or tmp_path/'public-example.safetensors'
    if not path.exists():path.write_bytes(MODEL_BYTES)
    if scope_path is None:
        value=scope_value(path);scope_path=tmp_path/'scope.json';scope_path.write_bytes(deploy_scope(value,canonical_json(value),session))
    private=types.ModuleType('_startup_test_iteration');private.__file__=iteration.__file__
    raw=Path(iteration.__file__).read_bytes();assert sha256_hex(raw)==sensor.ITERATION16_SHA256
    exec(compile(raw,private.__file__,'exec',dont_inherit=True,optimize=0),vars(private))
    callback,support=contract.load_callback(str(Path(sensor.__file__).with_name('vllm_worker_readiness.py')),str(Path(sensor.__file__).with_name('vllm_slot_mask.py')))
    with patch.context() as local:
        local.setattr(iteration,'configure',lambda base:sensor.configure(base,private,scope17,mapping,contract,callback,support,scope_path=str(scope_path)))
        world=iteration_world('late-callback' if mode=='late-callback' else 'normal',session=session)
    cls=type(world.observer);context=world.observer._writer().context
    cls.emit=sensor.StartupObservation.emit;cls._writer=sensor.StartupObservation._writer
    world.observer.writer=Recorder(world,context)
    source=source_world(otel_available=otel_available)
    for name,module in source.modules.items():patch.setitem(sys.modules,name,module)
    world.observer.instrument(source.worker)
    for name,module in source.modules.items():
        info={'source_sha256':contract.SOURCE_SHA256[name],'code_sha256':base8.code_digest(source.compiled[name]['<module>'])}
        world.observer.modules[name]=info;world.observer.emit('MODULE',{'module':name,'verified':True,**info})
    weights=world.modules[iteration.WEIGHTS]
    def iterator():return weights.safetensors_weights_iterator([str(path)],False,None,None,safetensors_prefetch_num_threads=1,safetensors_prefetch_block_size=1)
    world.loader._get_weights_iterator=lambda source:iterator()
    native=source.worker;native.set_current_vllm_config=lambda *a:nullcontext();native.has_ec_transfer=lambda:False;native.set_torch_threads_for_runtime=lambda:None
    owner=native.Worker();owner.rank=owner.local_rank=0;owner.model_config=NS(enforce_eager=False)
    owner.vllm_config=NS(weight_transfer_config=None,compilation_config=NS(cudagraph_mode=NS(_name_='FULL')))
    owner.device_config=NS(device_type='EXAMPLE-unsupported-device',device=NS(type='EXAMPLE-unsupported-device'))
    owner._maybe_get_memory_pool_context=lambda **kw:nullcontext();owner._scoped_allocator_max_split=lambda **kw:nullcontext()
    owner.model_runner=NS(load_model=lambda **kw:world.run_loader())
    world.worker=owner;world.worker_source=source;world.path=path;world.scope_path=scope_path
    world.start=lambda:owner.load_model(load_dummy_weights=False)
    world.close=lambda:world.observer._writer().close('SEALED')
    return world


def prepared_for(world):
    raw=world.scope_path.read_bytes();value=json.loads(raw)
    return {'profile':sensor.PROFILE,'session':value['session'],'model_read_scope':scope_summary(value),
        'assets':{'model-read-scope.json':{'sha256':sha256_hex(raw),'bytes':len(raw)}}}


def recording(world,*,bound=True):
    value=packet(world,closed=world.observer.writer.closed,bound=bound)
    value['recordings'][0]['capture']['records'][0]['payload']['profile']=sensor.PROFILE
    value['recordings'][0]['capture']['records'][0]['payload']['context']=deepcopy(world.observer.writer.context)
    return value


@pytest.mark.parametrize('available',[False,True])
def test_source_gate_distinguishes_original_body_and_preserved_otel_wrapper(available):
    world=source_world(otel_available=available)
    selected=contract.qualify(world.worker,base8,modules=world.modules)
    assert set(selected)==set(contract.SPAN_NAMES)
    for point,row in selected.items():
        assert row['original'] is vars(world.worker.Worker)[point.split('.')[1]]
        assert row['entry_kind']==('otel-sync-wrapper' if available and contract.SPAN_NAMES[point] else 'direct-body')
        assert row['inner_body_entry_observed'] is False
    assert world.otel.original_spans==[]


@pytest.mark.parametrize('changed',['module-alias','wrapper-body','closure','body-code','backend'])
def test_source_gate_rejects_substitution_before_any_worker_method_changes(changed):
    world=source_world(otel_available=True);cls=world.worker.Worker;kept=cls.load_model
    if changed=='module-alias':world.worker.instrument=lambda *a:None
    elif changed=='wrapper-body':cls.init_device=lambda *a:None
    elif changed=='closure':cls.init_device.__wrapped__=kept
    elif changed=='body-code':cls.load_model=lambda *a:None
    else:world.modules[contract.TRACING]._REGISTERED_TRACING_BACKENDS={'otel':(lambda:True,)*5}
    with pytest.raises(ValueError,match='WORKER_STARTUP_'):contract.qualify(world.worker,base8,modules=world.modules)
    if changed!='body-code':assert cls.load_model is kept
    assert world.otel.original_spans==[]


@pytest.mark.parametrize('available',[False,True])
@pytest.mark.parametrize('mode',['normal','late-callback'])
def test_actual_fixed_worker_load_body_uses_original_model_call_and_explicit_root_link(tmp_path,monkeypatch,available,mode):
    world=make_world(tmp_path,monkeypatch,otel_available=available,mode=mode)
    if mode=='late-callback':
        with pytest.raises(OSError,match=PRIVATE):world.start()
    else:assert world.start() is None
    world.close();packet_value=recording(world);projected=metadata.project(packet_value,prepared_for(world))
    assert len(projected['spans'])==1 and projected['spans'][0]['outer_call_pair_verified']
    assert projected['spans'][0]['callback_method_pair_verified'] and projected['spans'][0]['returned']==(mode=='normal')
    assert projected['model_candidates'] and any(row['same_startup_model_root_candidate'] for row in projected['model_candidates'])
    assert projected['close_events'][0]['result']['complete'] and world.observer.startup_callback_closed
    assert all(row[key] is False for row in projected['events'] for key in sensor.TRUTH)
    assert PRIVATE not in json.dumps(projected) and str(world.path) not in json.dumps(projected)
    assert not world.worker_source.otel.original_spans  # load_model is undecorated.
    assert world.path.read_bytes()==MODEL_BYTES


@pytest.mark.parametrize('available',[False,True])
def test_original_decorated_init_error_and_tracing_context_are_not_stripped(tmp_path,monkeypatch,available):
    world=make_world(tmp_path,monkeypatch,otel_available=available)
    with pytest.raises((RuntimeError,ValueError),match='device|Device|support'):world.worker.init_device()
    world.close();projected=metadata.project(recording(world),prepared_for(world))
    assert projected['events'][-1]['phase']=='RAISE' and projected['spans'][0]['returned'] is False
    assert len(world.worker_source.otel.original_spans)==int(available)
    assert all(row['inner_body_entry_observed'] is False for row in projected['events'])


def test_missing_source_or_process_binding_never_becomes_worker_or_approval_truth(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch);world.observer.modules.pop(contract.UTILS)
    world.start();world.close();projected=metadata.project(recording(world,bound=False),prepared_for(world))
    assert projected['events'] and all(not row['source_verified'] and not row['process_binding_verified'] for row in projected['events'])
    assert not any(row['same_startup_model_root_candidate'] for row in projected['model_candidates'])
    assert all(not projected[key] for key in sensor.TRUTH)


def test_callback_code_change_retains_original_return_and_prior_root_facts(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch);callback=world.observer.startup_callback_module
    monkeypatch.setattr(callback.WorkerReadinessObserver,'snapshot',lambda *a,**kw:None)
    assert world.start() is None;world.close()
    projected=metadata.project(recording(world),prepared_for(world))
    assert projected['events'] and all(row['callback_error']=='CALLBACK_UNAVAILABLE' for row in projected['events'])
    assert all(row['callback_code_verified'] is False for row in projected['events'])
    assert any(row['same_startup_model_root_candidate'] for row in projected['model_candidates'])
    assert 'worker_startup_callback_unavailable' in projected['missing'] and 'worker_startup_observer_close_incomplete' in projected['missing']


@pytest.mark.parametrize('changed',['truth','source','runtime','callback-code','binding','root-link','duplicate'])
def test_closed_parser_rejects_substitution_or_retains_duplicate_ambiguity(tmp_path,monkeypatch,changed):
    world=make_world(tmp_path,monkeypatch);world.start();world.close();value=recording(world)
    rows=value['recordings'][0]['capture']['records'];first=next(row for row in rows if row['event']=='WORKER_STARTUP')
    if changed=='truth':first['payload']['actual_worker_observed']=True
    elif changed=='source':first['payload']['source_files'][contract.OTEL]='0'*64
    elif changed=='runtime':first['payload']['observer_runtime']='cpython-3.12.0'
    elif changed=='callback-code':first['payload']['callback_code_sha256']='0'*64
    elif changed=='binding':first['payload']['callback']['binding']['pid']+=1
    elif changed=='root-link':next(row for row in rows if row['event']=='WORKER_STARTUP_LINK')['payload']['weight_payload_sha256']='0'*64
    else:
        rows.append(deepcopy(first));rows[-1]['seq']=len(rows)-1
        projected=metadata.project(value,prepared_for(world))
        assert 'worker_startup_unique_call_pair_unavailable' in projected['missing']
        assert not any(row['same_startup_model_root_candidate'] for row in projected['model_candidates']);return
    with pytest.raises(ValueError,match='WORKER_STARTUP_'):metadata.project(value,prepared_for(world))



def test_same_pid_overlapping_thread_is_not_linked_by_time_alone(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch)
    failures=[]
    def load():
        try:world.run_loader()
        except BaseException as error:failures.append(type(error).__name__)
    def threaded(**kwargs):
        thread=threading.Thread(target=load);thread.start();thread.join()
    world.worker.model_runner.load_model=threaded
    assert world.start() is None and failures==[]
    world.close();projected=metadata.project(recording(world),prepared_for(world))
    assert projected['spans'][0]['outer_call_pair_verified'] and projected['model_candidates']==[]
    assert 'worker_startup_model_root_link_unavailable' in projected['missing']
    assert metadata.project_iterations(recording(world))['model_candidates']


def test_callback_busy_retains_independent_outer_root_and_unknown_metadata(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch);lock=world.observer.startup_callback._lock;lock.acquire()
    try:assert world.start() is None
    finally:lock.release()
    world.close();projected=metadata.project(recording(world),prepared_for(world))
    assert any(row['same_startup_model_root_candidate'] for row in projected['model_candidates'])
    assert all(row['callback']['status']=='unavailable' and row['callback_code_verified'] for row in projected['events'])
    assert 'worker_startup_callback_metadata_unavailable' in projected['missing']


def test_observer_limit_records_gap_once_and_delegates_without_holding_lock(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch);world.observer.startup_calls=sensor.MAX_CALLS
    original=world.worker.model_runner.load_model
    def unlocked(**kwargs):
        assert world.observer.startup_lock.acquire(blocking=False)
        world.observer.startup_lock.release()
        return original(**kwargs)
    world.worker.model_runner.load_model=unlocked
    assert world.start() is None and world.start() is None
    world.close();value=recording(world);rows=value['recordings'][0]['capture']['records']
    assert sum(row['event']=='WORKER_STARTUP_LIMIT' for row in rows)==1
    projected=metadata.project(value,prepared_for(world))
    assert projected['events']==[] and 'worker_startup_observer_call_bound' in projected['missing']
    assert len(world.applied)==4


def test_changed_pid_cannot_emit_or_link_old_observer_generation(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch);world.observer.startup_pid=-1
    assert world.start() is None;world.close()
    projected=metadata.project(recording(world),prepared_for(world))
    assert projected['events']==projected['model_candidates']==[]
    assert 'worker_startup_method_observation_unavailable' in projected['missing']
    assert all(projected[key] is False for key in sensor.TRUTH)


def test_missing_close_retains_valid_calls_but_cannot_claim_observer_cleanup(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch);world.start()
    projected=metadata.project(recording(world),prepared_for(world))
    assert any(row['same_startup_model_root_candidate'] for row in projected['model_candidates'])
    assert projected['close_events']==[] and 'worker_startup_observer_close_unavailable' in projected['missing']
    world.close()


