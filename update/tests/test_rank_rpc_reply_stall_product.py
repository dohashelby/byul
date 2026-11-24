"""An isolated owned Python child runs the real reply controller, without engine/GPU."""
from copy import deepcopy
from hashlib import sha256
import hmac
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from guard.adapters import vllm_rank_rpc_control as control
from guard.adapters import vllm_rank_rpc_input as adapter
from guard.collectors import rank_rpc_fault_producer as producer
from tests.test_rank_ipc_runtime import _prepared
from tests.test_rank_rpc_product_binding import example
from tests.test_rank_rpc_reply_stall import RAW, spec

PROGRAM = '''import sys,os,json,time,signal,importlib.util,importlib
from pathlib import Path
from multiprocessing import Pipe
from multiprocessing.shared_memory import SharedMemory
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
package_spec=importlib.util.spec_from_file_location('_byul_rank_ipc',root/'guard/__init__.py',submodule_search_locations=[str(root/'guard')])
package=importlib.util.module_from_spec(package_spec);sys.modules['_byul_rank_ipc']=package;package_spec.loader.exec_module(package)
runtime=importlib.import_module('_byul_rank_ipc.adapters.vllm_rank_ipc_runtime');runtime.ROOT=root
assert runtime.install()
actor=runtime.rpc_actor;code=runtime.rpc_control
import rank_reply_fixture as fixture
worker,queue,scheduled=fixture.Worker(),fixture.Queue(),fixture.Scheduled()
shared=SharedMemory(create=True,size=1)
worker.rank,worker.tp,worker.use_async_scheduling,worker.queue,worker.output=0,2,False,queue,object()
queue.shared,queue.error=shared,None
controller=runtime._INSTALLED.observer.rpc.control
binding=json.loads((root/'EXAMPLE-binding.json').read_bytes())
os.environ.update(GUARD_POD_UID=binding['pod_uid'],GUARD_CONTAINER_ID=binding['container_id'],GUARD_RESTART_COUNT=str(binding['restart_count']))
controller.write('control-product-binding.json',binding)
parent,end=Pipe();pid=os.fork()
if pid==0:
 parent.close()
 try:
  while True:
   header=end.recv();scheduled.num_scheduled_tokens={header:1}
   assert worker.execute(scheduled)is worker.output
   end.send(200)
 except (EOFError,OSError):pass
 finally:end.close();shared.close();os._exit(0)
end.close()
def client(model,header,key):
 started=time.monotonic_ns();fixture.External.header(header);parent.send(header)
 try:status=parent.recv()
 except (EOFError,OSError):status=None
 return dict(context=code.base._context(),header_hmac_sha256=actor._id(header,key),request_started_ns=started,
  response_finished_ns=time.monotonic_ns(),status=status,complete=status is not None,
  response_sha256=code.digest('EXAMPLE-response')if status is not None else None,response_id_hmac_sha256=None)
actor.post=client
def forbid_signal(*args,**kwargs):raise AssertionError('communication-stall must not send an OS signal')
actor.signal.pidfd_send_signal=forbid_signal
try:
 assert client('EXAMPLE-model','EXAMPLE-normal',controller.key)['status']==200
 records=controller._records()
 proof,=[p for p,_,_ in records if p['point']=='Queue.enqueue' and p['event']=='CALL' and p['context']['pid']==pid]
 header,=[p for p,_,_ in records if p['point']=='External.header' and p['event']=='RETURN']
 calibration=dict(proof=proof,header_context=header['context'],assignment_context=None,
  initial_capture_sha256=code.digest(sorted(raw for _,raw,_ in records)),
  normal_rpc_returns=dict(all_declared_member_returns_observed=True,scope='EXAMPLE fixture precondition only'))
 owner=dict(pod_uid=binding['pod_uid'],model_receipt_sha256='9'*64)
 result=actor.exercise(controller,calibration,'EXAMPLE-model','communication-stall',owner,hold_ms=10,product_binding=binding)
 assert result['arm']['schema']=='guard-rank-rpc-arm/3' and result['target']['schema']=='guard-rank-rpc-lab-target/3'
 assert result['physical_action']['signal']is None and result['client']['status']==200
 assert result['scope_complete']is result['producer_registered']is result['engine_group_verified']is False
finally:
 parent.close()
 try:os.kill(pid,signal.SIGKILL)
 except ProcessLookupError:pass
 os.waitpid(pid,0);shared.close();shared.unlink()
'''


def build(parent):
    prepared,settings,manifest=_prepared(parent)
    (prepared/'rank_reply_fixture.py').write_bytes(RAW)
    catalog={'schema':'guard-rank-ipc-source-candidates/1','profiles':[spec(RAW)]}
    name='guard/adapters/assets/vllm_rank_ipc_profiles.json'
    raw=control.canonical(catalog);(prepared/name).write_bytes(raw)
    settings['assets'][name]={'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
    (prepared/'settings.json').write_bytes(control.canonical(settings))
    manifest.update(assets=settings['assets'],settings_sha256=sha256(control.canonical(settings)).hexdigest())
    (prepared/'manifest.json').write_bytes(control.canonical(manifest))
    binding=example(settings['session']);binding['prepared_sha256']=sha256(control.canonical(manifest)).hexdigest()
    (prepared/'EXAMPLE-binding.json').write_bytes(control.canonical(binding))
    result=subprocess.run([sys.executable,'-I','-S','-c',PROGRAM,str(prepared)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    capture=parent/'capture';(capture/'snapshots').mkdir(parents=True);(capture/'controls').mkdir()
    for path in (prepared/'data'/settings['session']).iterdir():
        shutil.copy2(path,capture/('snapshots'if path.name.startswith('pid-')else'controls')/path.name)
    return prepared,capture,binding


@pytest.fixture(scope='module')
def original(tmp_path_factory):return build(tmp_path_factory.mktemp('EXAMPLE-reply-stall'))


def project(data):return adapter.project(*data)


def test_isolated_reply_stall_and_release_reach_partial_common_input(original):
    projection=project(original)
    assert [row['event']for row in projection['events']]==['fault_applied','fault_released']
    assert all(row['ack']and row['fault_kind']=='communication-stall'for row in projection['events'])
    assert projection['events'][0]['at_ns']<projection['events'][1]['at_ns']
    prepared,capture,binding=original
    value,validation,_=producer.record(prepared,capture,pod={k:binding[k]for k in('pod_uid','container_id','restart_count')},
        **{k:binding[k]for k in('environment_ref','target_sha256','prepared_sha256','session')})
    assert [row['event']for row in value['events']]==['fault_applied','fault_released']
    assert validation['supported']is validation['complete']is validation['producer_authenticated']is False
    assert projection['scope_complete']is projection['engine_group_verified']is projection['producer_registered']is False
    assert set(projection['diagnostic_codes'])>={'RANK_RPC_DETECTION_UNOBSERVED','RANK_RPC_RECOVERY_UNOBSERVED','RANK_RPC_UPPER_DELIVERY_UNVERIFIED'}
    assert projection['client_observation']=={'status':200,'complete':True,'upper_delivery_verified':False}


@pytest.mark.parametrize('damage',['missing-stall','missing-release','barrier-only','wrong-queue','wrong-action','renamed-delay'])
def test_client_success_or_actor_files_cannot_replace_exact_stall_source_ack(original,tmp_path,damage):
    prepared,capture,binding=original
    copied=tmp_path/'capture';shutil.copytree(capture,copied)
    key=bytes.fromhex(json.loads((prepared/'settings.json').read_bytes())['key'])
    target_event='CONTROL_RELEASE'if damage=='missing-release'else'CONTROL_STALL'
    for path in (copied/'snapshots').glob('pid-*.json'):
        document=json.loads(path.read_bytes());payload=document['snapshot']['payload']
        if payload.get('event')!=target_event:continue
        if damage in {'missing-stall','missing-release'}:path.unlink()
        else:
            if damage=='barrier-only':payload['control']['barrier_only']=True
            elif damage=='wrong-queue':payload['endpoints']['response']['identity_hmac_sha256']='f'*64
            elif damage=='wrong-action':payload['control']['action_sha256']='f'*64
            else:payload['control']['fault_kind']='delay'
            document['hmac_sha256']=hmac.new(key,control.canonical(document['snapshot']),sha256).hexdigest()
            path.write_bytes(control.canonical(document))
        break
    else:raise AssertionError('the actual fixture source acknowledgment is required')
    with pytest.raises(ValueError):adapter.project(prepared,copied,binding)
