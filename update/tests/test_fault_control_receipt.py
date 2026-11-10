"""Owned metadata selection boundary; no real worker, signal or GPU."""
import json,os,time
import pytest
from guard.adapters import vllm_fault_control as c

def ready():
 return dict(schema=c.READY_SCHEMA,nonce='a'*32,fault_kind='death',rank=0,external_request_sha256='b'*64,
 native_request_sha256='c'*64,session='d'*32,target_sha256='e'*64,pid=42,start_ticks=123,pid_namespace=5,
 call_sha256='f'*64,point='WorkerProc._execute_worker_rpc',clock_context=dict(boot_sha256='1'*64,time_namespace=7,time_offsets_sha256='2'*64),
 operation_started_ns=10,ready_ns=20,barrier_deadline_ns=30_000_000_020,active=True,bounded_barrier=True,expires_unix_ns=time.time_ns()+10_000_000_000,
 observer_runtime='cpython-3.12.3',observer_code_sha256='3'*64,source_module=c.MP,source_sha256=c.MP_SHA256)

def load(path):return c.load_control_receipt(str(path),'a'*32,session='d'*32,target_sha256='e'*64,expected_external_sha='b'*64,rank=0,point='WorkerProc._execute_worker_rpc')
def write(path,value):
 path.chmod(0o700);p=path/('a'*32+'.ready.json');p.write_text(json.dumps(value));p.chmod(0o600);return p

def test_exact_owned_receipt_is_selection_only(tmp_path):
 p=write(tmp_path,ready());v=load(tmp_path);assert v['selection_only']and not v['native_fault_verified'];assert v['bytes']==p.stat().st_size
@pytest.mark.parametrize('key,value',[('nonce','0'*32),('session','0'*32),('target_sha256','0'*64),('external_request_sha256','0'*64),('rank',1),('rank',False),('pid',1),('active',False),('bounded_barrier',False),('source_sha256','0'*64),('expires_unix_ns',1),('point','WorkerProc.enqueue_output')])
def test_bad_scope_or_expired_receipt_refused(tmp_path,key,value):
 v=ready();v[key]=value;write(tmp_path,v)
 with pytest.raises(ValueError):load(tmp_path)
def test_world_readable_or_symlink_refused(tmp_path):
 p=write(tmp_path,ready());p.chmod(0o644)
 with pytest.raises(ValueError):load(tmp_path)
 p.chmod(0o600);q=tmp_path/'source';p.rename(q);p.symlink_to(q)
 with pytest.raises(OSError):load(tmp_path)
def test_duplicate_and_nonfinite_refused(tmp_path):
 p=write(tmp_path,ready());p.write_text('{"x":1,"x":2}')
 with pytest.raises(ValueError):load(tmp_path)
 with pytest.raises(ValueError):c.strict_json(b'{"x":NaN}')
def test_other_kind_in_ipc_boundary_refused(tmp_path):
 v=ready();v.update(fault_kind='communication-stall');write(tmp_path,v)
 with pytest.raises(ValueError):load(tmp_path)

@pytest.mark.parametrize('deadline',[20,30_000_000_021,False])
def test_barrier_deadline_same_clock_and_hard_bound(tmp_path,deadline):
 v=ready();v['barrier_deadline_ns']=deadline;write(tmp_path,v)
 with pytest.raises(ValueError):load(tmp_path)
