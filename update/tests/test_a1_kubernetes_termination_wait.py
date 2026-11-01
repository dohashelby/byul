"""Normal deletion lag and replacement remain distinct in the real API gate."""
import json
import pytest

from guard.runtime.kubernetes_binding import verify_owned_pod
from guard.runtime.kubernetes_transition import KubernetesTransition
from tests.test_kubernetes_transition import Pair, Clock


def stopping(pair,argv,kwargs,*,terminal=False):
    result=pair.runner(argv,**kwargs);value=json.loads(result['stdout_bytes'])
    value['metadata']['deletionTimestamp']='2026-10-26T00:00:30Z'
    value['status']['conditions']=[{'type':'Ready','status':'False'}]
    state=value['status']['containerStatuses'][0];state['ready']=False
    if terminal:
        started=state['state']['running']['startedAt']
        state['state']={'terminated':{'startedAt':started,'finishedAt':'2026-10-26T00:01:00Z','exitCode':0}}
    return result,value


def test_gate_waits_same_deleting_running_and_final_container_until_api_absence(tmp_path):
    pair=Pair(stop=False);clock=Clock();seen=[]
    def runner(argv,**kwargs):
        if '--ignore-not-found' not in argv:return pair.runner(argv,**kwargs)
        seen.append(len(seen))
        if len(seen)==3:
            result=pair.runner(argv,**kwargs);result['stdout_bytes']=b'';return result
        result,value=stopping(pair,argv,kwargs,terminal=len(seen)==2)
        result['stdout_bytes']=json.dumps(value).encode();return result
    gate=KubernetesTransition(pair.envs,pair.configs(tmp_path),runner=runner,clock=clock,sleep=clock.sleep,timeout=1,poll_interval=.01)
    gate.prepare_baseline();gate.candidate_ready()
    assert len(seen)==3 and clock.value>=.02
    assert gate.report()['events']==['BASELINE_READY_OBSERVED','WAITING_BASELINE_API_TERMINATION','BASELINE_API_TERMINATION_OBSERVED','CANDIDATE_READY_OBSERVED']
    assert not gate.report()['gpu_release_verified'] and not gate.report()['workloads_mutated']
    assert all('get' in call and 'exec' not in call for call in pair.calls)


@pytest.mark.parametrize('damage',['uid','container','restart','image','start','invalid-deletion','naive-deletion','waiting','finished'])
def test_deleting_generation_tamper_fails_before_candidate_selection(tmp_path,damage):
    pair=Pair(stop=False);clock=Clock()
    def runner(argv,**kwargs):
        if '--ignore-not-found' not in argv:return pair.runner(argv,**kwargs)
        result,value=stopping(pair,argv,kwargs,terminal=damage=='finished')
        state=value['status']['containerStatuses'][0]
        if damage=='uid':value['metadata']['uid']='replacement'
        elif damage=='container':state['containerID']='containerd://'+'f'*64
        elif damage=='restart':state['restartCount']+=1
        elif damage=='image':state['imageID']='sha256:'+'f'*64
        elif damage=='start':state['state']['running']['startedAt']='2026-10-26T00:00:01Z'
        elif damage=='invalid-deletion':value['metadata']['deletionTimestamp']='invalid'
        elif damage=='naive-deletion':value['metadata']['deletionTimestamp']='2026-10-26T00:00:30'
        elif damage=='waiting':state['state']={'waiting':{'reason':'ContainerCreating'}}
        else:state['state']['terminated']['finishedAt']='2020-01-01T00:00:00Z'
        result['stdout_bytes']=json.dumps(value).encode();return result
    gate=KubernetesTransition(pair.envs,pair.configs(tmp_path),runner=runner,clock=clock,sleep=clock.sleep,timeout=1,poll_interval=.01)
    gate.prepare_baseline()
    with pytest.raises(ValueError,match='KUBERNETES_TRANSITION_UNVERIFIED'):gate.candidate_ready()
    assert pair.candidate_polls==0 and gate.report()['events'][-1]=='RECOVERY_REQUIRED'


def test_stopping_permission_cannot_admit_ready_or_normal_http_dispatch():
    pair=Pair(stop=False);expected=pair.worlds[0].target['kubernetes']['engine']
    args=['kubectl','--context='+pair.worlds[0].target['kubernetes']['context'],'--request-timeout=1s',
          '--namespace='+expected['namespace'],'get','pod',expected['pod'],'-o','json']
    _,value=stopping(pair,args,{'timeout':1})
    namespace={'uid':expected['namespace_uid']};run=pair.worlds[0].target['kubernetes']['run_id']
    for options in ({},{'require_ready':False},{'allow_terminating':True},
                    {'require_ready':False,'terminated':True,'allow_terminating':True},
                    {'require_ready':False,'allow_terminating':'yes'}):
        with pytest.raises(ValueError):verify_owned_pod(value,namespace,expected,run,**options)
    verify_owned_pod(value,namespace,expected,run,require_ready=False,allow_terminating=True)
