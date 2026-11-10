"""Existing executor integration with EXAMPLE native23 evidence and transport."""
from copy import deepcopy
from datetime import timedelta
import json,types
import pytest
from guard.executors import kubernetes_request_termination as executor
from guard.models import utcnow,sha256_hex
from tests.test_request_termination_projection23 import packet,EXT
from tests.test_kubernetes_request_binding import env


def replace(value,before,after):
 if type(value)is str:return after if value==before else value
 if type(value)is list:return [replace(v,before,after)for v in value]
 if type(value)is dict:return {k:replace(v,before,after)for k,v in value.items()}
 return value


@pytest.mark.parametrize('damage',['none','client-close','seal','normal-after','slot-retained'])
def test_native23_seals_after_idle_window_before_normal_control_and_emits_original_activation_contract(monkeypatch,damage):
 events=[];native=[None];engine={'container':'engine','image':'repo@sha256:'+'f'*64}
 class Reader:
  def __init__(self,target,environment,prepared,**kwargs):
   self.binding={'engine':engine};self.owner_identity=self.read_file=self.image_check=None
  def snapshot(self):events.append('snapshot')
  def seal_session(self):events.append('seal');return {'sealed':damage!='seal'}
  def report(self):return {}
 class Transport:
  binding={'run_id':'EXAMPLE-run','context':'EXAMPLE-context','engine':engine}
  def __call__(self,source,requests,timeout):
   request,=requests;slot=request['id'].removeprefix('lif10-');events.append(slot)
   if slot=='disconnect':
    p,_=packet();native[0]=replace(p,EXT,sha256_hex(request['headers']['X-Request-Id'].encode()))
    if damage=='slot-retained':
     for r in native[0]['recordings'][0]['capture']['records']:
      if r['payload'].get('point')=='GPUModelRunner.finish_requests':r['payload']['states'][0]['worker_slot_present']=True
    return [{'outcome':200,'capture_complete':False}]
   return [{'outcome':200,'capture_complete':True,'eof':True,'utf8_valid':True,'body':json.dumps({'model':'EXAMPLE-model','choices':[{'text':'ok'}]})if not(slot=='after'and damage=='normal-after')else'{}'}]
  def report(self):return {'requests':[{'id':'lif10-disconnect','expected_client_close':damage!='client-close'}],'collection_gap':False,'cleanup_errors':[]}
 def capture(reader,target,**kwargs):
  events.append('capture');p=deepcopy(native[0]);p['recordings'][0].update(pid=42,start_ticks=100);p['recordings'][0]['capture']['provenance']['capture_sha256']='0'*64;return p
 monkeypatch.setattr(executor,'KubernetesLifecycleReader',Reader);monkeypatch.setattr(executor,'capture_session',capture);monkeypatch.setattr(executor.time,'sleep',lambda seconds:events.append(('wait',seconds)))
 now=utcnow();values=dict(termination_environment_sha256='1'*64,termination_target_sha256='2'*64,termination_validity_start=(now-timedelta(minutes=1)).isoformat(),termination_validity_end=(now+timedelta(minutes=1)).isoformat(),termination_max_delay_ms=1,termination_required_scopes=sorted(executor.SCOPES))
 ctx=types.SimpleNamespace(policy_values=values,target={'lab':True},cprobe=Transport(),env=env(),api_model='EXAMPLE-model',engine_url='http://engine:8000',timeout=10,_termination_prepared={'profile':'vllm-native-lifecycle-metadata/23','session':'a'*32})
 e=executor.KubernetesRequestTerminationExecutor({});observations,normal,_,summary=e.collect(ctx)
 assert events==['snapshot','before','disconnect',('wait',.001),'seal','after','capture']
 assert normal['succeeded']and summary['post_disconnect_observation_wait_ms']==1
 assert {'activation_steps','activation_violations','activation_missing','activation_scope_complete'}<=observations.keys()
 expected=damage=='none';assert observations['activation_scope_complete']['value']is expected and observations['termination_scope_complete']['value']is expected
 if not expected:assert observations['activation_missing']['value']
 assert not observations['activation_violations']['value']


def test_new_profile_dispatch_uses_closed_projector_and_legacy_packet_stays_historical():
 from guard.adapters.vllm_request_termination import project_native
 p,_=packet();r=project_native(p,EXT,1,sorted(executor.SCOPES));assert r['whole_scope_complete']
 p['recordings'][0]['capture']['records'][0]['payload']['profile']='vllm-native-lifecycle-metadata/8'
 r=project_native(p,EXT,1,sorted(executor.SCOPES));assert not r['whole_scope_complete']and not r['worker_gpu_release_verified']
