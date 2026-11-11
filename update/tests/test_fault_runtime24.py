"""EXAMPLE mechanical source controls; native attachment and signals are absent."""
import json,os,time
from copy import deepcopy
import pytest
from guard.adapters import vllm_fault_control as control
from guard.adapters import vllm_fault_runtime as runtime

NONCE='1'*32;SESSION='2'*32;TARGET='3'*64;EXTERNAL='4'*64;NATIVE='5'*64;INVOCATION='6'*64
CLOCK={'boot_sha256':'a'*64,'time_namespace':7,'time_offsets_sha256':'b'*64}

def context():return {'pid':os.getpid(),'start_ticks':100,'pid_namespace':200,'clock_context':dict(CLOCK)}

def write(directory,name,value):
 p=directory/name;p.write_text(json.dumps(value));p.chmod(0o600)

def arm(directory,kind='death'):
 value={'schema':control.CONTROL_SCHEMA,'nonce':NONCE,'session':SESSION,'target_sha256':TARGET,
  'fault_kind':kind,'rank':0,'external_request_sha256':EXTERNAL,
  'expires_unix_ns':time.time_ns()+10_000_000_000,'hold_timeout_ms':30}
 write(directory,NONCE+'.arm.json',value);return value

def action(directory,name):
 write(directory,NONCE+'.'+name+'.json',{'schema':control.ACTION_SCHEMA,'nonce':NONCE,
  'session':SESSION,'target_sha256':TARGET,'action':name})

@pytest.fixture
def world(tmp_path):
 tmp_path.chmod(0o700);rows=[];now=[1_000_000_000]
 def wait(_):now[0]+=10_000_000
 def emit(phase,value,checksum):rows.append((phase,deepcopy(value),checksum))
 value=runtime.SourceControl(control,directory=str(tmp_path),session=SESSION,target_sha256=TARGET,
  emit=emit,context=context,observer_runtime='cpython-3.12.3',observer_code_sha256='c'*64,
  clock=lambda:now[0],wait=wait)
 return tmp_path,value,rows,now


def test_exact_source_assignment_is_owned_immutable_and_cannot_repeat(world):
 path,c,rows,_=world;arm(path)
 assert c.assign('0'*64,NATIVE,INVOCATION)is None
 value=c.assign(EXTERNAL,NATIVE,INVOCATION)
 assert value['native_request_sha256']==NATIVE and rows[0][0]=='ASSIGNED'
 assert (path/(NONCE+'.assigned.json')).stat().st_mode&0o777==0o600
 with pytest.raises(ValueError):c.assign(EXTERNAL,NATIVE,INVOCATION)
 assert c.select([NATIVE],1,'WorkerProc._execute_worker_rpc')is None
 assert c.select(['d'*64],0,'WorkerProc._execute_worker_rpc')is None
 assert c.select([NATIVE],0,'WorkerProc.enqueue_output')is None


@pytest.mark.parametrize('kind',['death','delay'])
def test_signal_cases_hold_until_bounded_timeout_without_actuation_or_raise(world,kind):
 path,c,rows,now=world;arm(path,kind);c.assign(EXTERNAL,NATIVE,INVOCATION)
 selected=c.select([NATIVE],0,'WorkerProc._execute_worker_rpc')
 result=c.hold(selected,point='WorkerProc._execute_worker_rpc',call_sha256='d'*64,operation_started_ns=now[0])
 assert result=={'activated':False,'timed_out':True,'kind':kind}
 ready=rows[1][1];assert rows[-1][0]=='EXPIRED' and now[0]==ready['barrier_deadline_ns']
 loaded=control.load_control_receipt(str(path),NONCE,session=SESSION,target_sha256=TARGET,
  expected_external_sha=EXTERNAL,rank=0,point='WorkerProc._execute_worker_rpc')
 assert loaded['selection_only']and not loaded['native_fault_verified']
 assert c.select([NATIVE],0,'WorkerProc._execute_worker_rpc')is None


def test_release_before_activate_cannot_inject_an_exception(world):
 path,c,rows,now=world;arm(path,'exception');c.assign(EXTERNAL,NATIVE,INVOCATION);action(path,'release');action(path,'activate')
 selected=c.select([NATIVE],0,'WorkerProc._execute_worker_rpc')
 result=c.hold(selected,point='WorkerProc._execute_worker_rpc',call_sha256='d'*64,operation_started_ns=now[0])
 assert not result['activated']and not result['timed_out']and [r[0]for r in rows]==['ASSIGNED','READY','RELEASE']


def test_exception_activation_returns_explicit_intent_for_caller_inside_rpc_try(world):
 path,c,rows,now=world;arm(path,'exception');c.assign(EXTERNAL,NATIVE,INVOCATION);action(path,'activate')
 result=c.hold(c.select([NATIVE],0,'WorkerProc._execute_worker_rpc'),point='WorkerProc._execute_worker_rpc',call_sha256='d'*64,operation_started_ns=now[0])
 assert result=={'activated':True,'timed_out':False,'kind':'exception'}
 assert [r[0]for r in rows]==['ASSIGNED','READY','ACTIVATE']


def test_ipc_uses_only_enqueue_boundary_and_waits_release_or_timeout(world):
 path,c,rows,now=world;arm(path,'communication-stall');c.assign(EXTERNAL,NATIVE,INVOCATION);action(path,'activate')
 assert c.select([NATIVE],0,'WorkerProc._execute_worker_rpc')is None
 selected=c.select([NATIVE],0,'WorkerProc.enqueue_output')
 result=c.hold(selected,point='WorkerProc.enqueue_output',call_sha256='d'*64,operation_started_ns=now[0])
 assert result['activated']and result['timed_out']and [r[0]for r in rows][-2:]==['ACTIVATE','EXPIRED']


@pytest.mark.parametrize('damage',['wrong-native','wrong-source','wrong-clock','extra-private','bad-rank'])
def test_untrusted_assignment_never_selects_source_operation(world,damage):
 path,c,_,_=world;arm(path);value=c.assign(EXTERNAL,NATIVE,INVOCATION)
 if damage=='wrong-native':value['native_request_sha256']='not-a-hash'
 if damage=='wrong-source':value['source_sha256']='d'*64
 if damage=='wrong-clock':value['frontend_context']['clock_context']['time_namespace']=False
 if damage=='extra-private':value['private_debug']='EXAMPLE_PRIVATE'
 if damage=='bad-rank':value['rank']=False
 write(path,NONCE+'.assigned.json',value)
 with pytest.raises(ValueError,match='FAULT_SOURCE_ASSIGNMENT_INVALID'):c.select([NATIVE],0,'WorkerProc._execute_worker_rpc')


def test_ambiguous_arm_directory_bound_and_replaced_arm_refuse(world):
 path,c,_,_=world;v=arm(path);v=dict(v,nonce='7'*32);write(path,v['nonce']+'.arm.json',v)
 with pytest.raises(ValueError,match='AMBIGUOUS'):c.assign(EXTERNAL,NATIVE,INVOCATION)
 (path/(v['nonce']+'.arm.json')).unlink();c.assign(EXTERNAL,NATIVE,INVOCATION)
 v=arm(path,'delay')
 with pytest.raises(ValueError,match='ARM_CHANGED'):c.select([NATIVE],0,'WorkerProc._execute_worker_rpc')
 for n in range(33):write(path,'extra'+str(n),{})
 with pytest.raises(ValueError,match='DIRECTORY_BOUND'):c.entries('.arm.json')


def test_fork_discards_prior_selection_and_live_barrier_state(world,monkeypatch):
 _,c,_,_=world;c.assigned.add(NONCE);c.consumed.add(NONCE);c.barrier_lock.acquire()
 monkeypatch.setattr(runtime.os,'getpid',lambda:c.pid+1);c.reset()
 assert not c.assigned and not c.consumed and c.barrier_lock.acquire(blocking=False)
 c.barrier_lock.release()


def test_delayed_owned_activation_read_cannot_activate_after_same_clock_barrier(world,monkeypatch):
 path,c,rows,now=world;arm(path,'exception');c.assign(EXTERNAL,NATIVE,INVOCATION);action(path,'activate')
 original=c.action
 def late(nonce,kind):
  result=original(nonce,kind)
  if kind=='activate':now[0]+=40_000_000
  return result
 monkeypatch.setattr(c,'action',late)
 result=c.hold(c.select([NATIVE],0,'WorkerProc._execute_worker_rpc'),point='WorkerProc._execute_worker_rpc',call_sha256='d'*64,operation_started_ns=now[0])
 assert result=={'activated':False,'timed_out':True,'kind':'exception'}
 assert [v[0]for v in rows]==['ASSIGNED','READY','EXPIRED']
