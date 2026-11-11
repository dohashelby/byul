"""EXAMPLE packet/source-claim validation; no actual engine or GPU execution."""
from copy import deepcopy
import hashlib
import pytest
from serving_runtime.mvp.update_engine_link import project_termination
from tests.test_request_termination_projection23 import packet,EXT

RID='f'*32;UPSTREAM='byul-cb-'+RID

def replace(value,old,new):
 if type(value)is dict:return {k:replace(v,old,new)for k,v in value.items()}
 if type(value)is list:return [replace(v,old,new)for v in value]
 return new if value==old else value

def inputs():
 p,_=packet();p=replace(p,EXT,hashlib.sha256(UPSTREAM.encode()).hexdigest())
 image='sha256:'+'1'*64
 owner={'namespace_uid':'namespace-one','pod_uid':'pod-one','container_id':'containerd://'+'2'*64,'reported_image_id':image,'restart_count':0}
 p['before']['owner']=deepcopy(owner);p['after']['owner']=deepcopy(owner);p['recordings'][0]['capture']['provenance']['owner']=deepcopy(owner)
 target={'pod_uid':owner['pod_uid'],'container_id':owner['container_id'],'image_digest':image,'restart_count':0,'instance_id':'instance-one'}
 context={'request_id':RID,'upstream_request_id':UPSTREAM,'observation_session_id':'runtime-session','run_id':'run-one','environment':'lab','backend_kind':'vllm','target_id':'target-one','target':target,'policy':{'sha256':'3'*64},'policy_bundle':{'sha256':'4'*64},'route':'POST /v1/chat/completions'}
 records=[{**deepcopy(context),'phase':phase,'recorded_at':'2026-11-07T00:00:00+00:00','final_outcome':'client_disconnected'}for phase in ('started','final')]
 binding={'context':'cluster-one','run_id':'run-one','engine':{'namespace_uid':owner['namespace_uid'],'pod_uid':owner['pod_uid'],'container_id':owner['container_id'],'image_id':image,'restart_count':0}}
 return records,p,binding


def result(records,p,binding,origin='EXAMPLE'):
 return project_termination(records,RID,p,1,['asgi_transport'],source_binding=binding,source_data_origin=origin)


def test_supported_chat_uses_actual_projector_but_example_origin_never_claims_engine_execution():
 records,p,binding=inputs();r=result(records,p,binding)
 assert r['support']=='source_qualified_native_lifecycle'and r['native_projection']['whole_scope_complete']
 assert r['native_termination_verified']and r['worker_gpu_release_verified']and not r['actual_engine_execution_verified']
 assert r['source_data_origin']=='EXAMPLE'and r['new_security_verdict']is None
 r=result(records,p,binding,None);assert not r['native_termination_verified']and 'RUNTIME_NATIVE_SOURCE_ORIGIN_UNQUALIFIED'in r['runtime_native_binding']['gaps']


@pytest.mark.parametrize('damage',['missing-binding','wrong-pod','wrong-image','wrong-run','wrong-restart','bool-restart','other-owner','missing-policy','mock'])
def test_modern_source_claim_keeps_all_runtime_generation_boundaries(damage):
 records,p,binding=inputs()
 if damage=='missing-binding':binding=None
 elif damage=='wrong-pod':binding['engine']['pod_uid']='other'
 elif damage=='wrong-image':binding['engine']['image_id']='unparseable'
 elif damage=='wrong-run':binding['run_id']='other'
 elif damage=='wrong-restart':binding['engine']['restart_count']=1
 elif damage=='bool-restart':binding['engine']['restart_count']=False
 elif damage=='other-owner':p['before']['owner']['container_id']='other'
 elif damage=='missing-policy':
  for r in records:r['policy']['sha256']=None
 elif damage=='mock':
  for r in records:r['backend_kind']='mock'
 r=result(records,p,binding,'DECLARED');assert not r['native_termination_verified']and not r['worker_gpu_release_verified']and r['new_security_verdict']is None


def test_declared_source_claim_is_joined_without_issuing_a_new_security_or_hardware_verdict():
 records,p,binding=inputs();r=result(records,p,binding,'DECLARED')
 assert r['runtime_native_binding']['state']=='bound'and r['native_termination_verified']and r['worker_gpu_release_verified']
 assert not r['actual_engine_execution_verified']and r['new_security_verdict']is None
 # This is an EXAMPLE assertion about the DECLARED input mode, not an actual source capture.
