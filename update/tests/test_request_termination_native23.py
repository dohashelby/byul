"""Real fixed vLLM body code with EXAMPLE CPU objects; no CUDA/serving proof."""
import abc,asyncio,copy,gc,json,sys,threading,time,types,weakref
from pathlib import Path
from contextlib import contextmanager
from collections import namedtuple
import pytest
from guard.adapters import vllm_termination_sensor as sensor,vllm_termination_metadata as metadata,vllm_lifecycle_sensor as base8
from tests.test_lifecycle_sensor import CONTEXT

FIXTURES=Path(__file__).parent/'fixtures/request-termination-native'
PROVENANCE={x['module']:x for x in json.loads((FIXTURES/'PROVENANCE.json').read_text())}

def codes(code):
 out={code.co_qualname:code}
 for value in code.co_consts:
  if type(value)is types.CodeType:out.update(codes(value))
 return out

def module(name):
 p=FIXTURES/PROVENANCE[name]['file'];raw=p.read_bytes();assert sensor.sha(raw)==sensor.SOURCE_ADDITIONS[name]
 code=codes(compile(raw,str(p),'exec',dont_inherit=True,optimize=0));m=types.ModuleType(name);m.__file__=str(p)
 for cls,methods in sensor.TARGETS.get(name,{}).items():
  factory=abc.ABCMeta if cls in {'Scheduler','AsyncOutput'}else type
  c=factory(cls,(),{'__module__':name});m.__dict__[cls]=c
  for method in methods:setattr(c,method,types.FunctionType(code[cls+'.'+method],vars(m),method))
 return m

@pytest.fixture
def world(monkeypatch):
 b=types.ModuleType('_native23_cpu_base');b.__file__=base8.__file__;exec(compile(Path(base8.__file__).read_bytes(),base8.__file__,'exec',dont_inherit=True,optimize=0),vars(b));sensor.configure(b)
 class Observer(b.Observer):
  def __init__(self):super().__init__({'session':'a'*32},'b'*64);self.recorded=[]
  def _writer(self):return types.SimpleNamespace(context=CONTEXT)
  def emit(self,event,payload):self.recorded.append({'event':event,'payload':copy.deepcopy(payload)})
 o=Observer();modules={}
 try:
  for name in sensor.TARGETS:
   m=module(name);monkeypatch.setitem(sys.modules,name,m);o.instrument(m);o.modules[name]={'source_sha256':sensor.SOURCE_ADDITIONS[name],'code_sha256':'f'*64};modules[name]=m
  yield o,modules
 finally:o.close()


def samples(observer):return [x['payload']for x in observer.recorded if x['event']=='TERMINATION']

def test_real_remove_and_finish_bodies_match_only_exact_request(world):
 o,modules=world;r=object.__new__(modules[sensor.RUNNER].GPUModelRunner);state=object.__new__(modules[sensor.STATES].RequestState)
 state.req_id_to_index={'native-private-a':3,'native-private-b':4};state.index_to_req_id={3:'native-private-a',4:'native-private-b'};state.free_indices=[]
 removed=[];r.req_states=state;r.model_state=types.SimpleNamespace(remove_request=lambda rid:removed.append(rid));r.lora_state=types.SimpleNamespace(remove_request=lambda index:removed.append(index))
 r.pooling_runner=r.pp_handler=r.encoder_cache=r.prompt_logprobs_worker=None
 s=types.SimpleNamespace(finished_req_ids={'native-private-a'},preempted_req_ids=set())
 assert r.finish_requests(s)is None;assert state.req_id_to_index=={'native-private-b':4};assert removed==['native-private-a','native-private-a']
 rows=samples(o);assert any(x['point']=='GPUModelRunner.finish_requests'and x['phase']=='RETURN'and x['states'][0]['worker_slot_present']is False for x in rows)
 assert all(x['request_sha256']==[sensor.sha(b'native-private-a')]for x in rows)
 for row in rows:metadata.validate_sample(row)
 assert 'native-private-a'not in json.dumps(o.recorded)

def test_real_body_exception_unchanged_and_callback_observed(world):
 o,modules=world;r=object.__new__(modules[sensor.RUNNER].GPUModelRunner);r.model_state=types.SimpleNamespace(remove_request=lambda _:(_ for _ in ()).throw(RuntimeError('EXAMPLE_NATIVE_PRIVATE')))
 with pytest.raises(RuntimeError,match='EXAMPLE_NATIVE_PRIVATE'):r._remove_request('native-a')
 assert any(x['phase']=='RAISE'for x in samples(o));assert 'EXAMPLE_NATIVE_PRIVATE'not in json.dumps(o.recorded)

def test_observer_exception_never_changes_real_body_result(world,monkeypatch):
 o,modules=world;state=object.__new__(modules[sensor.STATES].RequestState);state.req_id_to_index={};state.index_to_req_id={};state.free_indices=[]
 monkeypatch.setattr(o,'term_states',lambda *a:(_ for _ in ()).throw(RuntimeError('EXAMPLE_OBSERVER_ERROR')))
 assert state.remove_request('native-a')is None

def test_member_does_not_call_properties_and_reads_fixed_namedtuple():
 class Hostile:
  @property
  def input_batch(self):raise AssertionError('property executed')
 assert sensor.member(Hostile(),'input_batch')is None
 c=namedtuple('ExecuteModelState','input_batch');c.__module__=sensor.RUNNER
 assert sensor.member(c(17),'input_batch')==17

def test_source_change_rejected_before_local_events(world,tmp_path):
 o,modules=world;m=module(sensor.STATES);p=tmp_path/'changed.py';p.write_bytes(Path(m.__file__).read_bytes()+b'\n');m.__file__=str(p)
 with pytest.raises(ValueError,match='SOURCE_UNAVAILABLE'):o.instrument(m)

def test_competing_monitor_tool_is_not_replaced():
 sys.monitoring.use_tool_id(sensor.TOOL_ID,'other-profiler')
 b=types.ModuleType('_native23_other');b.__file__=base8.__file__;exec(compile(Path(base8.__file__).read_bytes(),base8.__file__,'exec',dont_inherit=True,optimize=0),vars(b))
 try:
  with pytest.raises(ValueError,match='MONITOR_UNAVAILABLE'):sensor.configure(b)
  assert sys.monitoring.get_tool(sensor.TOOL_ID)=='other-profiler'and b.PROFILE==base8.PROFILE
 finally:sys.monitoring.free_tool_id(sensor.TOOL_ID)

class Event:
 def __init__(self,result=False):self.result=result
 def query(self):
  if isinstance(self.result,BaseException):raise self.result
  return self.result

def test_fence_poll_complete_error_timeout_and_bounded_task(monkeypatch):
 started=[]
 class Thread:
  def __init__(self,**kw):assert kw['daemon']is True
  def start(self):started.append(1)
 monkeypatch.setattr(sensor.threading,'Thread',Thread);now=[1];events=[];w=sensor.BoundedFences(lambda phase,facts:events.append((phase,facts)),clock=lambda:now[0])
 assert w.add('one',Event(True),{'id':1});assert w.add('two',Event(RuntimeError('private')),{'id':2});assert w.add('three',Event(False),{'id':3});assert len(started)==1
 w.poll();assert {x[0]for x in events}=={'FENCE_COMPLETE','FENCE_ERROR'}
 now[0]+=sensor.FENCE_BUDGET_NS;w.poll();assert events[-1][0]=='FENCE_TIMEOUT'and not w.pending
 for n in range(sensor.MAX_PENDING):assert w.add(str(n),Event(),{})
 assert not w.add('overflow',Event(),{});w.stop();assert not w.pending and not w.add('closed',Event(),{})

def test_fence_stop_does_not_wait_for_broken_query():
 entered=threading.Event();release=threading.Event();events=[]
 class Broken:
  def query(self):entered.set();release.wait(1);return True
 w=sensor.BoundedFences(lambda *a:events.append(a));assert w.add('one',Broken(),{});assert entered.wait(1)
 began=time.monotonic();w.stop();assert time.monotonic()-began<.2;release.set();time.sleep(.02);assert not events

@pytest.mark.parametrize('path',['/v1/chat/completions','/v1/completions'])
def test_both_asgi_paths_keep_return_exception_and_invocation_binding(world,path):
 o,_=world;request=b'EXAMPLE_PRIVATE_HEADER';seen=[]
 async def app(scope,receive,send):seen.append(await receive());await send({'type':'http.response.body','body':b'EXAMPLE_PRIVATE_BODY'});return 19
 scope={'type':'http','path':path,'method':'POST','headers':[(b'x-request-id',request)]};o.term_base._OBSERVER=o
 async def receive():return {'type':'http.disconnect'}
 async def send(value):return None
 assert asyncio.run(o.term_base.Middleware(app)(scope,receive,send))==19
 rows=[r for r in samples(o)if r['point']=='ASGI.request'];assert {r['phase']for r in rows}=={'ASGI_DISCONNECT_RECEIVED','ASGI_BODY_SEND_RETURN','ASGI_APP_RETURN'}
 assert len({r['asgi_invocation_sha256']for r in rows})==1 and all(r['asgi_request_sha256']==sensor.sha(request)for r in rows)
 for r in rows:metadata.validate_sample(r)
 assert b'EXAMPLE_PRIVATE_BODY'.decode()not in json.dumps(o.recorded)

def test_real_async_copy_wait_output_lifetime_and_no_observer_tensor_reads(world):
 o,modules=world;m=modules[sensor.ASYNC];counts={'wait':0,'tolist':0}
 class Tensor:
  def __init__(self,value):self.value=value
  def tolist(self):counts['tolist']+=1;return copy.deepcopy(self.value)
 class CUDAEvent:
  def __init__(self,**kw):pass
  def record(self,stream):pass
  def synchronize(self):counts['wait']+=1
 @contextmanager
 def stream(*args):yield
 m.stream=stream;m.async_copy_to_np=lambda t:t;m.torch=types.SimpleNamespace(cuda=types.SimpleNamespace(Event=CUDAEvent));m.envs=types.SimpleNamespace(VLLM_RAISE_ON_LOGIT_NANS=False)
 model=types.SimpleNamespace(req_ids=['native-private-c'],prompt_logprobs_dict={},prompt_token_id_logprobs_dict={})
 sampler=types.SimpleNamespace(sampled_token_ids=Tensor([[101,102]]),logprobs_tensors=None,num_nans=None,sampling_mask_tensors=None)
 count=Tensor([1]);main=object();joins=[];copy_stream=types.SimpleNamespace(wait_stream=lambda selected:joins.append(selected));value=m.AsyncOutput(model,sampler,count,main,copy_stream,False,None);ref=weakref.ref(value);assert joins==[main]
 assert value.get_output()is model;assert model.sampled_token_ids==[[101]]and counts=={'wait':1,'tolist':2}
 created=[v for v in samples(o)if v['phase']=='OUTPUT_CREATED'];assert len(created)==1
 waits=[v for v in samples(o)if v['point']=='AsyncOutput.get_output'];assert waits and all(v['output_token']==created[0]['output_token']for v in waits)
 del value;gc.collect();assert ref()is None
 released=[v for v in samples(o)if v['phase']=='OUTPUT_RELEASED'];assert len(released)==1 and released[0]['output_token']==created[0]['output_token']
 for row in samples(o):metadata.validate_sample(row)
 assert 'native-private-c'not in json.dumps(o.recorded)

def test_asgi_observer_failure_preserves_original_exception(world,monkeypatch):
 o,_=world;o.term_base._OBSERVER=o
 async def app(*args):raise LookupError('EXAMPLE_ORIGINAL')
 async def receive():return {'type':'http.disconnect'}
 async def send(message):return None
 monkeypatch.setattr(o,'term_nonce',lambda *a:(_ for _ in ()).throw(RuntimeError('EXAMPLE_OBSERVER')))
 with pytest.raises(LookupError,match='EXAMPLE_ORIGINAL'):
  asyncio.run(o.term_base.Middleware(app)({'type':'http','path':'/v1/chat/completions','method':'POST','headers':[]},receive,send))

def test_fence_final_poll_records_pending_instead_of_inventing_completion(monkeypatch):
 class Silent:
  def __init__(self,**kw):pass
  def start(self):pass
 monkeypatch.setattr(sensor.threading,'Thread',Silent);events=[];w=sensor.BoundedFences(lambda phase,f:events.append(phase))
 assert w.add('pending',Event(False),{});w.poll(final=True);assert events==['FENCE_PENDING']and not w.pending


def test_source_bound_owner_checkpoints_are_weak_and_record_real_cpu_state(world):
 o,modules=world;r=object.__new__(modules[sensor.RUNNER].GPUModelRunner);r.req_states=types.SimpleNamespace(req_id_to_index={'native-a':0})
 module=modules[sensor.RUNNER];codes=sensor.checked_codes(module,o.term_base);binding={'module':sensor.RUNNER,'point':'GPUModelRunner.finish_requests','source':sensor.SOURCE_ADDITIONS[sensor.RUNNER],'body':codes['GPUModelRunner.finish_requests']}
 o.term_watch_owner(r,binding,(sensor.sha(b'native-a'),));assert o.term_checkpoint(final=True)
 rows=[x for x in samples(o)if x['phase']=='STATE_SNAPSHOT'];assert rows and rows[-1]['states'][0]['worker_slot_present']is True
 r.req_states.req_id_to_index.clear();o.term_checkpoint(final=True);assert [x for x in samples(o)if x['phase']=='STATE_SNAPSHOT'][-1]['states'][0]['worker_slot_present']is False
 o.term_fences.stop();thread=o.term_fences.thread
 if thread is not None:thread.join(1)
 ref=weakref.ref(r);del r;gc.collect();assert ref()is None;assert o.term_checkpoint(final=True)is False
 for row in samples(o):metadata.validate_sample(row)


def test_prepared_export_and_closed_payload_parser_preserve_native8_dependency(tmp_path):
 from guard.collectors.lifecycle import prepare_assets,load_prepared
 from guard.executors.lifecycle_capture import parse_recording,canonical_row
 repo=Path(__file__).resolve().parents[2];out=tmp_path/'assets';prepared=prepare_assets(out,repo,profile=sensor.PROFILE)
 assert load_prepared(out/'manifest.json')==prepared
 assert (out/'guard_lifecycle_base.py').read_bytes()==Path(base8.__file__).read_bytes()
 binding={'module':sensor.STATES,'point':'RequestState.remove_request','source':sensor.SOURCE_ADDITIONS[sensor.STATES],'body':'c'*64}
 # A synthetic parsed record tests only wire/source closure; no physical claim.
 payload={'schema':sensor.EVENT_SCHEMA,'point':binding['point'],'phase':'RETURN','source_module':binding['module'],'source_sha256':binding['source'],'body_code_sha256':binding['body'],'observer_runtime':'cpython-'+'.'.join(map(str,sys.version_info[:3])),'observer_code_sha256':sensor.RUNTIME_CODE_CONTRACTS['cpython-'+'.'.join(map(str,sys.version_info[:3]))],'call_sha256':'d'*64,'operation_started_ns':1,'operation_finished_ns':2,'request_sha256':['e'*64],'gpu_work_selected_sha256':[],'states':[{'request_sha256':'e'*64,'worker_slot_present':False,'worker_mapping_complete':True}],'scheduler_before':[],'scheduler_after':[],'stream_kind':None,'stream_sha256':None,'stream_set_complete':False,'caller_stream_bound':False,'output_token':None,'error_kind':None,'asgi_request_sha256':None,'asgi_invocation_sha256':None,'asgi_path_sha256':None}
 raw=[];previous='0'*64
 entries=[('START',{'schema':sensor.SCHEMA,'profile':sensor.PROFILE,'commit':sensor.COMMIT,'session':prepared['session'],'settings_sha256':prepared['assets']['settings.json']['sha256'],'sensor_sha256':prepared['assets']['guard_lifecycle_sensor.py']['sha256'],'context':CONTEXT,'producer_authenticated':False}),('MODULE',{'module':sensor.STATES,'verified':True,'source_sha256':binding['source'],'code_sha256':'f'*64}),('TERMINATION',payload),('END',{'reason':'SEALED'})]
 for seq,(event,value)in enumerate(entries):
  row={'seq':seq,'event':event,'payload':value,'monotonic_ns':10+seq,'previous_sha256':previous};line=canonical_row(row);raw.append(line);previous=sensor.sha(line)
 records,summary=parse_recording(b''.join(raw),prepared);assert records[2]['payload']==payload and summary['recording_closed']
 payload['states'][0]['worker_mapping_complete']='EXAMPLE_SECRET'
 with pytest.raises(ValueError):metadata.validate_sample(payload)

def test_example_sdk_fences_bind_both_streams_and_refuse_caller_stream_change(world,monkeypatch):
 o,modules=world;runner=modules[sensor.RUNNER];r=object.__new__(runner.GPUModelRunner)
 device=types.SimpleNamespace(index=0);main=types.SimpleNamespace(device=device,cuda_stream=11);copy_stream=types.SimpleNamespace(device=device,cuda_stream=12);recorded=[]
 class CUDAEvent:
  def record(self,stream):recorded.append(stream.cuda_stream)
  def query(self):return True
 torch=types.ModuleType('torch');torch.cuda=types.SimpleNamespace(current_stream=lambda device=None:main,Event=CUDAEvent);monkeypatch.setitem(sys.modules,'torch',torch)
 ec=types.ModuleType(sensor.EC);ec.ECConnector=type('ECConnector',(),{});offload=types.ModuleType(sensor.OFFLOAD);offload.NoopOffloader=type('NoopOffloader',(),{});offload._instance=offload.NoopOffloader();monkeypatch.setitem(sys.modules,sensor.EC,ec);monkeypatch.setitem(sys.modules,sensor.OFFLOAD,offload)
 model=type('Qwen2ForCausalLM',(),{'__module__':sensor.QWEN});runner.NO_OP_KV_CONNECTOR=object()
 r.main_stream=main;r.output_copy_stream=copy_stream;r.device=device;r.speculator=r.pp_handler=r.pcp_manager=r.aux_output_connector=r.ubatch_runner=None;r.ec_connector=ec.ECConnector();r.kv_connector=runner.NO_OP_KV_CONNECTOR;r.model=model();r.model_config=types.SimpleNamespace(enforce_eager=True)
 r.vllm_config=types.SimpleNamespace(parallel_config=types.SimpleNamespace(tensor_parallel_size=1,pipeline_parallel_size=1,data_parallel_size=1,enable_eplb=False),speculative_config=None,kv_transfer_config=None,ec_transfer_config=None,lora_config=None)
 for name,pin in sensor.SOURCE_ADDITIONS.items():o.modules[name]={'source_sha256':pin}
 code=sensor.checked_codes(runner,o.term_base)['GPUModelRunner.sample_tokens'];binding={'module':sensor.RUNNER,'point':'GPUModelRunner.sample_tokens','source':sensor.SOURCE_ADDITIONS[sensor.RUNNER],'body':code}
 assert o.term_stream_profile(r)is True
 o.term_record_fences(binding,'f'*64,time.monotonic_ns(),('e'*64,),r,caller_stream=(0,11));assert o.term_fences.finished.wait(1)
 rows=[v for v in samples(o)if v['phase']=='FENCE_COMPLETE'];assert {x['stream_kind']for x in rows}=={'main','copy'}and all(x['stream_set_complete']and x['caller_stream_bound']for x in rows);assert recorded==[11,12]
 before=len(recorded);o.term_record_fences(binding,'d'*64,time.monotonic_ns(),('e'*64,),r,caller_stream=(0,999));assert len(recorded)==before;assert samples(o)[-1]['phase']=='FENCE_ERROR'
 r.ubatch_runner=object();assert o.term_stream_profile(r)is False
 r.ubatch_runner=None;r.aux_output_connector=object();assert o.term_stream_profile(r)is False
 for row in samples(o):metadata.validate_sample(row)


def test_real_scheduler_finish_retains_pre_removal_dispatch_counters(world):
 o,modules=world;m=modules[sensor.SCHED];r=object.__new__(m.Scheduler)
 Request=type('Request',(),{'__module__':sensor.REQUEST,'is_finished':lambda self:self.status==3})
 q=Request();q.request_id='native-queued-private';q.status=0;q.last_sched_seq=0;q.num_computed_tokens=0;q.num_preemptions=0;q.num_in_flight_tokens=0;q.streaming_queue=None
 r.requests={q.request_id:q};r.running=[];r.waiting=r.kv_holding_waiting=types.SimpleNamespace(remove_requests=lambda requests:None);r.deferred_waiting=set();r.finished_recving_kv_req_ids=set();r.failed_recving_kv_req_ids=set()
 m.RequestStatus=types.SimpleNamespace(is_finished=lambda status:status==3,RUNNING=1,WAITING_FOR_STREAMING_REQ=2,WAITING_FOR_REMOTE_KVS=4)
 r._free_request=lambda request,**kw:r.requests.pop(request.request_id)
 assert r.finish_requests(q.request_id,3)==[q]
 rows=[v for v in samples(o)if v['point']=='Scheduler.finish_requests'];assert [v['phase']for v in rows]==['CALL','RETURN']
 before=rows[-1]['scheduler_before'][0];after=rows[-1]['scheduler_after'][0]
 assert before['request_present']and before['source_request_type_verified']and before['num_computed_tokens']==0 and before['num_preemptions']==0 and before['streaming']is False
 assert after['request_present']is False and after['source_request_type_verified']is False and after['num_computed_tokens']is None
 for row in samples(o):metadata.validate_sample(row)
 assert 'native-queued-private'not in json.dumps(o.recorded)


def test_constructor_monitor_registration_failure_releases_its_tool(monkeypatch):
 actual=sys.monitoring
 class Monitor:
  def __getattr__(self,name):return getattr(actual,name)
  def register_callback(self,*a):raise RuntimeError('EXAMPLE_REGISTER_ERROR')
 monkeypatch.setattr(sys,'monitoring',Monitor())
 b=types.ModuleType('_native23_registration_failure');b.__file__=base8.__file__;exec(compile(Path(base8.__file__).read_bytes(),base8.__file__,'exec',dont_inherit=True,optimize=0),vars(b));sensor.configure(b)
 with pytest.raises(RuntimeError,match='EXAMPLE_REGISTER_ERROR'):b.Observer({'session':'a'*32},'b'*64)
 assert actual.get_tool(sensor.TOOL_ID)is None


def test_cumulative_weak_owner_tracking_stays_bounded_and_records_gap(world):
 o,modules=world;r=object.__new__(modules[sensor.RUNNER].GPUModelRunner);r.req_states=types.SimpleNamespace(req_id_to_index={})
 codes=sensor.checked_codes(modules[sensor.RUNNER],o.term_base);binding={'module':sensor.RUNNER,'point':'GPUModelRunner.finish_requests','source':sensor.SOURCE_ADDITIONS[sensor.RUNNER],'body':codes['GPUModelRunner.finish_requests']}
 for n in range(sensor.MAX_IDS+1):o.term_watch_owner(r,binding,(sensor.sha(str(n).encode()),))
 assert len(next(iter(o.term_tracked.values()))['requests'])==sensor.MAX_IDS
 gap=[v for v in samples(o)if v['phase']=='OBSERVER_GAP'];assert len(gap)==1 and gap[0]['error_kind']=='observer-bound'
 metadata.validate_sample(gap[0])


def test_finished_ids_never_substitute_for_actual_gpu_work(world):
 o,modules=world;active=sensor.sha(b'native-active');finished=sensor.sha(b'native-finished');local={'scheduler_output':types.SimpleNamespace(num_scheduled_tokens={'native-active':1},finished_req_ids={'native-finished'})}
 assert set(o.term_selected('GPUModelRunner.execute_model',local))=={active,finished}
 assert o.term_dispatched('GPUModelRunner.execute_model',local)==(active,)
 local['scheduler_output'].num_scheduled_tokens={};assert o.term_dispatched('GPUModelRunner.execute_model',local)==()
 local['scheduler_output'].num_scheduled_tokens={'native-active':0};assert o.term_dispatched('GPUModelRunner.execute_model',local)is None


def test_new_source_operation_rearms_owner_after_bounded_checkpoint_expiry(world,monkeypatch):
 o,modules=world;o.term_fences.stop();now=[1_000_000_000]
 monkeypatch.setattr(sensor.time,'monotonic_ns',lambda:now[0])
 owner=object.__new__(modules[sensor.RUNNER].GPUModelRunner)
 owner.req_states=types.SimpleNamespace(req_id_to_index={})
 binding=next(v for v in o.term_codes.values()if v['point']=='GPUModelRunner.execute_model')
 first=sensor.sha(b'first-bounded-operation');second=sensor.sha(b'next-operation-after-expiry')
 o.term_watch_owner(owner,binding,(first,));old=o.term_owners[owner]
 now[0]+=sensor.FENCE_BUDGET_NS+1;o.term_checkpoint()
 assert old not in o.term_tracked
 # The same long-lived model runner remains usable after an earlier observer
 # budget. This must create a fresh bounded watch, not dereference a stale key.
 o.term_watch_owner(owner,binding,(second,));new=o.term_owners[owner]
 assert new!=old and o.term_tracked[new]['requests']=={second}
 assert o.term_tracked[new]['expires']==now[0]+sensor.FENCE_BUDGET_NS
 now[0]+=1_000_000_000;o.term_checkpoint()
 rows=[p for p in samples(o)if p['phase']=='STATE_SNAPSHOT']
 assert rows[-1]['request_sha256']==[second]and rows[-1]['states'][0]['worker_slot_present']is False
