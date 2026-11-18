"""EXAMPLE kernel/containerd fixtures; no GPU qualification or registration."""
from copy import deepcopy
import json,time
from types import SimpleNamespace

import pytest

from guard.executors import kubernetes_image_approval as consumer
from guard.executors import node_image_proof as proof_module
from guard.models import DataOrigin
from tests.builders import baseline_pack,environment
from tests.test_kubernetes_running_image_approval import inputs,snapshot
from tests.test_node_image_proof import World,KERNEL,NODE


class Transport:
    def __init__(self,target,*,damage=None):
        self.target=target;self.binding=target['kubernetes'];self.damage=damage
        self.clock=time.monotonic;self.deadline=self.clock()+120
        self.injected=True;self.runner=lambda *a,**k:None
        self.process_observation_origin='EXAMPLE';self.reads=0
    def snapshot(self):
        self.reads+=1;value=snapshot(self.target)
        if self.damage=='replaced' and self.reads==2:value['pod']['status']['containerStatuses'][0]['restartCount']+=1
        return value
    def _call(self,args):
        assert args==['get','node','node-one','-o','json']
        value=deepcopy(NODE)
        if self.damage=='node-reboot':value['status']['nodeInfo']['bootID']='other-boot'
        return value
    def report(self):return {'profile':'EXAMPLE-exact-owned-transport'}


def setup(monkeypatch,*,approved=True,damage=None):
    monkeypatch.setattr(proof_module,'verify_grpcurl_binary',lambda:None)
    world=World();_,target=inputs();engine=target['kubernetes']['engine']
    image=world.engine['image'];engine.update(image=image,image_id=world.config,node='node-one',container_id=world.engine['container_id'])
    target['engine']['image_digest']=image
    env=environment(engine={'name':'vllm','version':'0.31.0','image_digest':image},model={'id':'m'})
    target['environment_ref']=env.ref().model_dump(mode='json')
    world.engine=deepcopy(engine)
    original_runner=world.run
    def runner(argv,**kwargs):
        result=original_runner(argv,**kwargs)
        if 'containers' in argv:
            row=json.loads(result['stdout_bytes']);row['Labels'].update({
                'io.kubernetes.pod.uid':engine['pod_uid'],'io.kubernetes.pod.name':engine['pod'],
                'io.kubernetes.pod.namespace':engine['namespace'],'io.kubernetes.container.name':engine['container']})
            result['stdout_bytes']=json.dumps(row).encode()
        return result
    world.run=runner
    collected=proof_module.collect_local_node_image_proof
    def factory(binding,node,**kwargs):
        assert binding['engine']==engine and 0<kwargs['timeout']<=30
        if damage=='fake-proof':return {'image_chain_verified':True,'data_origin':'MEASURED'}
        if damage=='missing-proof':raise PermissionError('PRIVATE_MUST_NOT_PERSIST')
        proof=collected(binding,node,runner=runner,kernel=KERNEL,proc_reader=world.process)
        if damage=='foreign-proof':proof._scope['container_id']='containerd://'+'f'*64
        return proof
    monkeypatch.setattr(proof_module,'collect_local_node_image_proof',factory)
    monkeypatch.setattr(proof_module,'kernel_identity',lambda:KERNEL)
    calls=[]
    def control(ctx):
        calls.append(True)
        return {'functional_probe_results':{'value':{'normal':'pass'},'collected':True}}, {'attempted':True,'succeeded':True,'detail':'EXAMPLE control'}
    monkeypatch.setattr(consumer,'kubernetes_process_control',control)
    ctx=SimpleNamespace(env=env,target=target,check=baseline_pack().checks['SUP-04'],
        policy_values={'approved_image_digests':[image] if approved else []},
        cprobe=Transport(target,damage=damage))
    return ctx,calls


@pytest.mark.parametrize('approved',[True,False])
def test_live_chain_binds_actual_image_and_runs_normal_control_without_claiming_full_supply(monkeypatch,approved):
    ctx,calls=setup(monkeypatch,approved=approved)
    observations,normal,challenges,summary=consumer.collect(ctx)
    assert observations['running_image_identity_verified']=={'value':True,'collected':True}
    assert observations['running_image_approved']=={'value':approved,'collected':True}
    assert observations['running_image_unapproved']=={'value':not approved,'collected':True}
    assert normal['attempted'] and normal['succeeded'] and len(calls)==1
    assert observations['supply_execution_scope_complete']=={'value':False,'collected':True}
    assert [x['attempted'] for x in challenges]==[True,False,False]
    assert summary['kubernetes_image']['runtime_image_relation_verified']
    assert summary['node_image_proof']['data_origin']=='EXAMPLE'
    assert ctx._kubernetes_image_data_origin==DataOrigin.EXAMPLE
    assert not summary['dispatch']['whole_run_coverage_verified']
    assert 'EXAMPLE_PRIVATE' not in json.dumps(summary)


@pytest.mark.parametrize('damage',['missing-proof','fake-proof','foreign-proof','node-reboot','replaced'])
def test_unproven_or_changed_generation_never_runs_control_or_borrows_an_approval(monkeypatch,damage):
    ctx,calls=setup(monkeypatch,damage=damage)
    observations,normal,challenges,summary=consumer.collect(ctx)
    assert observations['running_image_identity_verified']=={'value':False,'collected':True}
    assert observations['running_image_approved']=={'value':None,'collected':False}
    assert not normal['attempted'] and not calls and not any(x['attempted'] for x in challenges)
    assert not summary['dispatch']['runtime_image_approval_connected']
    assert summary['collection_errors']
    assert 'PRIVATE_MUST_NOT_PERSIST' not in json.dumps(summary)
