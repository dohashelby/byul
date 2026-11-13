"""Actual fixed RPC/runner bodies with CPU helpers; no process signals or CUDA."""
import os,time,types,weakref
from copy import deepcopy
from types import SimpleNamespace
import pytest
from guard.adapters import vllm_fault_sensor as sensor,vllm_fault_runtime as mechanics,vllm_fault_control as control,vllm_lifecycle_sensor as base8,vllm_termination_sensor as term
from tests.test_a1_rank_failure_sensor import reference_module,ResponseMQ
from tests.test_request_termination_native23 import module as runner_module
from tests.test_fault_runtime24 import arm,action,NONCE,SESSION,TARGET,EXTERNAL,NATIVE,INVOCATION


class ExampleObserver(sensor.FaultObservation):
 def __init__(self,path):
  self.config={'session':SESSION};self.fault_base=base8;self.fault_control=control;self.fault_mechanics=mechanics
  self.fault_abi='cpython-3.12.3';self.fault_code='c'*64;self.fault_pid=os.getpid();self.fault_serial=0
  self.fault_outputs=weakref.WeakKeyDictionary();self.fault_body={};self.rank_codes={};self.recorded=[];self.term='not-attached'
  self.fault_runtime=mechanics.SourceControl(control,directory=str(path),session=SESSION,target_sha256=TARGET,
   emit=self.fault_emit,context=self.fault_context,observer_runtime=self.fault_abi,observer_code_sha256=self.fault_code)
 def active(self):return True
 def _writer(self):return SimpleNamespace(context={'pid':os.getpid(),'start_ticks':100,'pid_namespace':200,
  'boot_sha256':'a'*64,'time_namespace':7,'time_offsets_sha256':'b'*64})
 def emit(self,event,payload):self.recorded.append((event,deepcopy(payload)))


@pytest.fixture
def world(tmp_path):
 tmp_path.chmod(0o700);o=ExampleObserver(tmp_path);mp=reference_module(sensor.MP);runner=runner_module(sensor.RUNNER)
 # The complete fixed body is used; only its dependency helpers are examples.
 raw=runner.GPUModelRunner.execute_model;raw.__defaults__=(None,False,False,False,0,False,False)
 for name in ('_execute_worker_rpc','handle_output','enqueue_output'):
  fn=getattr(mp.WorkerProc,name);point='WorkerProc.'+name;o.fault_body[(sensor.MP,point)]=base8.code_digest(fn.__code__)
  setattr(mp.WorkerProc,name,o.fault_wrap(sensor.MP,point,fn))
 o.fault_body[(sensor.RUNNER,'GPUModelRunner.execute_model')]=base8.code_digest(raw.__code__)
 runner.GPUModelRunner.execute_model=o.fault_wrap(sensor.RUNNER,'GPUModelRunner.execute_model',raw)
 model=object.__new__(runner.GPUModelRunner)
 class Output:pass
 output=Output()
 for key in ('update_pp_decode_requests','finish_requests','free_states','add_requests','update_requests'):
  setattr(model,key,lambda *a:None)
 model.block_tables=SimpleNamespace(apply_staged_writes=lambda:None);model.aux_output_connector=None
 model.kv_connector=SimpleNamespace(no_forward=lambda s:output);model._merge_ec_connector_no_forward=lambda *a:output
 model.vllm_config=SimpleNamespace(parallel_config=SimpleNamespace(tensor_parallel_size=1,pipeline_parallel_size=1,data_parallel_size=1))
 Worker=type('Worker',(),{'__module__':'vllm.v1.worker.gpu_worker'})
 Wrapper=type('WorkerWrapperBase',(),{'__module__':'vllm.v1.worker.worker_base'})
 worker=Worker();worker.model_runner=model;wrapper=Wrapper();wrapper.worker=worker;wrapper.execute_model=lambda s:model.execute_model(s)
 proc=object.__new__(mp.WorkerProc);proc.rank=0;proc.worker=wrapper;proc.worker_response_mq=ResponseMQ();proc.use_async_scheduling=False
 native='example-native';native_sha=sensor.sha(native.encode());scheduler=SimpleNamespace(num_scheduled_tokens={native:1},total_num_scheduled_tokens=0)
 return SimpleNamespace(path=tmp_path,observer=o,mp=mp,runner=runner,model=model,proc=proc,scheduler=scheduler,native=native_sha,output=output)


def assigned(world,kind):
 arm(world.path,kind);world.observer.fault_runtime.assign(EXTERNAL,world.native,INVOCATION)


def invoke(world):return world.proc._execute_worker_rpc(('execute_model',(world.scheduler,),{},0))


def test_real_rpc_catches_intentional_exception_raised_inside_its_try(world):
 assigned(world,'exception');action(world.path,'activate');assert invoke(world)is None
 status,error,=world.proc.worker_response_mq.items[0]
 assert status is world.mp.WorkerProc.ResponseStatus.FAILURE and 'BYUL_LAB_WORKER_RPC_EXCEPTION'in error
 phases=[p['phase']for e,p in world.observer.recorded if e=='FAULT_CONTROL']
 assert phases==['ASSIGNED','READY','ACTIVATE','EXCEPTION_INTENT','EXCEPTION_CAUGHT']
 ready=world.observer.recorded[1][1];assert ready['rpc_active']and ready['runner_bound']
 assert ready['native_request_sha256']==world.native and ready['source_module']==sensor.MP
 assert sensor._CURRENT.get()is None


@pytest.mark.parametrize('kind',['death','delay'])
def test_released_source_barrier_preserves_real_rpc_output_without_signal(world,kind):
 # 출력 보존 대조는 제어기의 주입 시계로 잰다. READY 파일 fsync의 벽시계
 # 지연을 30ms 만료로 오인하지 않는다. wait가 호출되면 같은 30ms 한도까지
 # 진행하므로 release를 무시하는 결함은 여전히 EXPIRED로 검출된다.
 now=[None]
 def clock():
  if now[0]is None:now[0]=time.monotonic_ns()
  return now[0]
 world.observer.fault_runtime.clock=clock
 world.observer.fault_runtime.wait=lambda seconds:now.__setitem__(0,now[0]+int(seconds*1_000_000_000))
 assigned(world,kind);action(world.path,'release');assert invoke(world)is None
 status,value=world.proc.worker_response_mq.items[0]
 assert status is world.mp.WorkerProc.ResponseStatus.SUCCESS and value is world.output
 assert [p['phase']for e,p in world.observer.recorded if e=='FAULT_CONTROL']==['ASSIGNED','READY','RELEASE']
 assert not world.observer.fault_outputs and sensor._CURRENT.get()is None


def test_observer_control_failure_leaves_original_rpc_result_unchanged(world,monkeypatch):
 assigned(world,'exception')
 def failed(*a,**kw):raise ValueError('EXAMPLE_PRIVATE_CONTROL_ERROR')
 monkeypatch.setattr(world.observer.fault_runtime,'select',failed)
 assert invoke(world)is None
 assert world.proc.worker_response_mq.items[0][1]is world.output
 assert 'EXAMPLE_PRIVATE'not in repr(world.observer.recorded)


def test_changed_body_or_wrong_physical_worker_selection_cannot_arm(world):
 assigned(world,'exception');action(world.path,'activate')
 # The selected source body must still match immediately before actuation.
 world.observer.fault_body[(sensor.RUNNER,'GPUModelRunner.execute_model')]='0'*64
 assert invoke(world)is None and world.proc.worker_response_mq.items[0][1]is world.output
 assert not(world.path/(NONCE+'.ready.json')).exists()
 world.proc.rank=False
 assert world.observer.fault_rpc(world.proc,(world.proc,('execute_model',(world.scheduler,),{},0)),{})is None


def test_ipc_control_does_not_hold_a_synchronous_worker_or_raise(world):
 assigned(world,'communication-stall');action(world.path,'activate')
 assert invoke(world)is None and world.proc.worker_response_mq.items[0][1]is world.output
 assert not(world.path/(NONCE+'.ready.json')).exists()


def test_composed_installer_uses_actual_checked_rank_and_runner_bodies(monkeypatch):
 import inspect,sys
 from pathlib import Path
 from guard.adapters import vllm_rank_failure_sensor as rank
 b=types.ModuleType('_fault24_example_base');b.__file__=base8.__file__
 exec(compile(Path(base8.__file__).read_bytes(),base8.__file__,'exec',dont_inherit=True,optimize=0),vars(b))
 term.configure(b);sensor.configure(b,term,rank,control,mechanics)
 class Observer(b.Observer):
  def __init__(self):super().__init__({'session':SESSION},'b'*64);self.recorded=[]
  def _writer(self):return SimpleNamespace(context={'pid':os.getpid(),'start_ticks':100,'uid':os.geteuid(),
   'pid_namespace':200,'boot_sha256':'a'*64,'time_namespace':7,'time_offsets_sha256':'b'*64})
  def emit(self,event,payload):self.recorded.append((event,deepcopy(payload)))
 o=Observer()
 try:
  mp=reference_module(sensor.MP);gpu=runner_module(sensor.RUNNER)
  monkeypatch.setitem(sys.modules,sensor.MP,mp);monkeypatch.setitem(sys.modules,sensor.RUNNER,gpu)
  o.instrument(mp);o.instrument(gpu)
  assert b.PROFILE==sensor.PROFILE and sensor.MP in b.SOURCE_SHA
  assert o.fault_body[(sensor.MP,'WorkerProc._execute_worker_rpc')]==base8.code_digest(inspect.unwrap(mp.WorkerProc._execute_worker_rpc).__code__)
  assert o.fault_body[(sensor.RUNNER,'GPUModelRunner.execute_model')]==base8.code_digest(inspect.unwrap(gpu.GPUModelRunner.execute_model).__code__)
  assert sensor.MP in o.rank_codes and o.term_codes
  # The observer's extra wrapper keeps the complete native unwrap chain.
  assert inspect.unwrap(mp.WorkerProc.enqueue_output).__globals__ is vars(mp)
 finally:o.close()


def test_ipc_ready_requires_actual_fixed_async_queue_body_and_preserves_output(world,monkeypatch):
 import sys
 from pathlib import Path
 from tests.test_a1_rank_failure_sensor import codes
 assigned(world,'communication-stall');action(world.path,'activate')
 world.proc.use_async_scheduling=True
 class EndExampleLoop(BaseException):pass
 class Queue:
  def __init__(self):self.items=[]
  def put(self,value):self.items.append(value)
  def get(self):
   if self.items:return self.items.pop(0)
   raise EndExampleLoop()
 world.proc.async_output_queue=Queue()
 assert invoke(world)is None and not(world.path/(NONCE+'.ready.json')).exists()
 compiled=codes(compile(Path(world.mp.__file__).read_bytes(),world.mp.__file__,'exec',dont_inherit=True,optimize=0))
 code=compiled['WorkerProc.async_output_busy_loop']
 world.observer.rank_codes[sensor.MP]={'WorkerProc.async_output_busy_loop':base8.code_digest(code),'WorkerProc.worker_busy_loop':base8.code_digest(compiled['WorkerProc.worker_busy_loop'])}
 world.mp.WorkerProc.async_output_busy_loop=types.FunctionType(code,vars(world.mp),'async_output_busy_loop')
 package=types.ModuleType('vllm');package.__path__=[];platform=types.ModuleType('vllm.platforms');platform.current_platform=SimpleNamespace()
 monkeypatch.setitem(sys.modules,'vllm',package);monkeypatch.setitem(sys.modules,'vllm.platforms',platform)
 import threading
 entered=threading.Event();stop=threading.Event()
 class RequestQueue:
  def dequeue(self,**kw):entered.set();stop.wait(2);raise EndExampleLoop()
 world.proc.rpc_broadcast_mq=RequestQueue()
 world.mp.WorkerProc.worker_busy_loop=types.FunctionType(compiled['WorkerProc.worker_busy_loop'],vars(world.mp),'worker_busy_loop')
 def worker_loop():
  try:world.proc.worker_busy_loop()
  except EndExampleLoop:pass
 thread=threading.Thread(target=worker_loop);thread.start();assert entered.wait(1)
 world.observer.fault_runtime.wait=lambda _:action(world.path,'release')
 try:
  with pytest.raises(EndExampleLoop):world.proc.async_output_busy_loop()
 finally:stop.set();thread.join(1)
 assert not thread.is_alive()
 phases=[p['phase']for e,p in world.observer.recorded if e=='FAULT_CONTROL']
 assert phases==['ASSIGNED','READY','ACTIVATE','RELEASE']
 ready=next(p for e,p in world.observer.recorded if p['phase']=='READY')
 assert ready['async_output_bound']and ready['worker_loop_observed']and not ready['rpc_active']
 assert world.proc.worker_response_mq.items[0][1]is world.output and not world.observer.fault_outputs
