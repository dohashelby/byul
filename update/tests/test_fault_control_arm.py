"""Closed opt-in LAB ARM/actions/publication; no native fault or signal."""
import json,os,time
import pytest
from guard.adapters import vllm_fault_control as c

NONCE='a'*32;SESSION='b'*32;TARGET='c'*64

def arm():return dict(schema=c.CONTROL_SCHEMA,nonce=NONCE,fault_kind='death',rank=0,external_request_sha256='d'*64,session=SESSION,target_sha256=TARGET,expires_unix_ns=time.time_ns()+10_000_000_000,hold_timeout_ms=1000)
def write(root,kind,value):
 root.chmod(0o700);p=root/(NONCE+'.'+kind+'.json');p.write_text(json.dumps(value));p.chmod(0o600);return p

def test_owned_arm_and_exact_actions(tmp_path):
 write(tmp_path,'arm',arm());assert c.load_arm(str(tmp_path),NONCE,session=SESSION,target_sha256=TARGET)['receipt']['rank']==0
 for action in ('activate','release'):
  write(tmp_path,action,dict(schema=c.ACTION_SCHEMA,nonce=NONCE,session=SESSION,target_sha256=TARGET,action=action))
  assert c.load_action(str(tmp_path),NONCE,session=SESSION,target_sha256=TARGET,action=action)['receipt']['action']==action

@pytest.mark.parametrize('key,value',[('rank',False),('rank',64),('expires_unix_ns',1),('expires_unix_ns',2**64),('expires_unix_ns',False),('hold_timeout_ms',0),('hold_timeout_ms',30001),('hold_timeout_ms',True),('fault_kind','unknown'),('target_sha256','f'*64),('nonce','f'*32),('external_request_sha256','private-token')])
def test_invalid_arm_refused(tmp_path,key,value):
 a=arm();a[key]=value;write(tmp_path,'arm',a)
 with pytest.raises(ValueError):c.load_arm(str(tmp_path),NONCE,session=SESSION,target_sha256=TARGET)


def test_no_overwrite_no_symlink_and_exact_owned_writer(tmp_path):
 tmp_path.chmod(0o700);value={'schema':c.ASSIGNED_SCHEMA,'EXAMPLE':'only'}
 receipt=c.publish_control_receipt(str(tmp_path),NONCE,'assigned',value);p=tmp_path/(NONCE+'.assigned.json')
 assert receipt['bytes']==p.stat().st_size and p.stat().st_mode&0o777==0o600
 with pytest.raises(FileExistsError):c.publish_control_receipt(str(tmp_path),NONCE,'assigned',value)
 p.unlink();p.symlink_to(tmp_path/'elsewhere')
 with pytest.raises(FileExistsError):c.publish_control_receipt(str(tmp_path),NONCE,'assigned',value)
 assert not(tmp_path/'elsewhere').exists()


def test_actions_unknown_keys_scope_and_partial_artifacts_refused(tmp_path):
 value=dict(schema=c.ACTION_SCHEMA,nonce=NONCE,session=SESSION,target_sha256=TARGET,action='activate',private='EXAMPLE')
 p=write(tmp_path,'activate',value)
 with pytest.raises(ValueError):c.load_action(str(tmp_path),NONCE,session=SESSION,target_sha256=TARGET,action='activate')
 p.write_bytes(b'{"schema":')
 with pytest.raises(ValueError):c.load_action(str(tmp_path),NONCE,session=SESSION,target_sha256=TARGET,action='activate')
 with pytest.raises(ValueError):c.publish_control_receipt(str(tmp_path),NONCE,'arm',{})
 p.chmod(0o644)
 with pytest.raises(ValueError):c._read_owned(str(tmp_path),p.name)
