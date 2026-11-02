"""Direct process RSS source, session selection and historical branch contracts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from guard.executors import kubernetes_process_rss as rss
from guard.executors.kubernetes_lifecycle_capture import supervised_source,KubernetesCaptureError
from guard.executors.lifecycle_capture import CONTEXT_READER
from guard.executors.lifecycle_session import capture_session
from guard.executors.kubernetes_stream_buffer import KubernetesStreamBufferExecutor,BUDGET_PROFILE
from guard.adapters.vllm import VllmAdapter
from guard.models import canonical_json,sha256_hex,ExecStatus
from tests.builders import baseline_pack
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tests.test_a1_stream_buffer_product import inputs


def example_rss_runner(original,mode='normal',calls=None):
    def runner(argv,**kwargs):
        if 'exec' in argv:
            command=argv[argv.index('--')+1:]
            if len(command)>5 and command[5]==supervised_source(rss.RSS_READER):
                if calls is not None:calls.append(argv)
                payload={'schema':'guard-direct-process-rss/1','rss_bytes':4096,'process_context_verified':True,'diagnostic_codes':[]}
                if mode=='missing':payload.update(rss_bytes=None,process_context_verified=False,diagnostic_codes=['BUDGET_RSS_PROCESS_UNAVAILABLE'])
                if mode=='pid-reused':payload.update(rss_bytes=None,process_context_verified=False,diagnostic_codes=['BUDGET_RSS_CONTEXT_CHANGED'])
                pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
                if mode=='cleanup':pipe.update(pipes_closed=False,read_errors=['child_reap'])
                return {'returncode':0,'stdout_bytes':canonical_json(payload),'stderr_bytes':b'PRIVATE','capture':pipe}
        return original(argv,**kwargs)
    return runner


@pytest.mark.parametrize('mode',['normal','missing','pid-reused','cleanup','engine-replaced'])
def test_budget_profile_preserves_direct_rss_facts_and_later_failures(tmp_path,group,native_target,mode):
    world,values,pack,_=inputs(tmp_path,group,native_target,mode if mode=='engine-replaced' else 'normal')
    values['buffer_max_process_rss_bytes']=1
    path=tmp_path/'target.json';path.write_bytes(canonical_json(world.target));calls=[]
    check=baseline_pack().checks['LIF-03'].model_copy(update={'collection_profile':BUDGET_PROFILE,
        'required_observations':pack.checks['LIF-04'].required_observations})
    draft=KubernetesStreamBufferExecutor({'target':str(path),'prepared':str(group.manifest_path)},
        runner=example_rss_runner(world.runner,mode,calls)).run(check,group.env,values,VllmAdapter(),tmp_path/'evidence')
    samples=draft.observations['budget_process_rss_samples'].value;exceeded=draft.observations['budget_process_rss_exceeded']
    assert calls and samples and all(row['query_source_sha256']==rss.RSS_SOURCE_SHA256 for row in samples)
    if mode in {'missing','pid-reused'}:
        assert not exceeded.collected and exceeded.value is None and draft.status==ExecStatus.ABORTED
    else:
        assert exceeded.collected and exceeded.value is True
        verified=[row for row in samples if row['process_context_verified'] and row['owner_binding_verified']]
        assert verified
        assert all(row['selected_owner_sha256']==row['query_owner_sha256'] for row in verified)
        assert all(len(row[key])==64 for row in verified for key in ('session_sha256','process_context_sha256',
            'recording_capture_sha256','recording_file_identity_sha256','selected_owner_sha256','query_owner_sha256'))
    if mode in {'cleanup','engine-replaced'}:
        assert draft.status==ExecStatus.ABORTED
        before=[row for row in samples if row['stage']=='before' and row['owner_binding_verified']]
        assert before and all(row['recording_capture_sha256'] for row in before)
    assert bool(draft.cleanup_errors)==(mode=='cleanup')
    assert all(not c['confirmed_violation'] for c in draft.observations['buffer_conflict_candidates'].value)
    assert 'PRIVATE' not in canonical_json(draft.model_dump(mode='json')).decode()


def test_only_capture_session_verified_processes_can_select_rss(tmp_path,group,native_target):
    world,values,_,_=inputs(tmp_path,group,native_target,'normal');calls=[]
    # The ENV ref changed when product inputs supplied the explicit model binding.
    target={**native_target,'environment_ref':group.env.ref().model_dump(mode='json')}
    reader=rss.KubernetesProcessRSSReader(target,group.env,group.prepared,runner=example_rss_runner(world.runner,calls=calls))
    role=reader.binding['engine']
    packet=capture_session(reader,target,container=role['container'],image=role['image'],prepared=group.prepared,
        owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
    selected=reader.sample_packet(packet,'before')
    assert calls and any(row['process_context_verified'] for row in selected)
    calls.clear()
    owner_calls=[]
    def changed_owner(timeout):
        owner_calls.append(True);return {'generation':len(owner_calls)}
    reader._owner=changed_owner
    changed=reader.sample_packet(packet,'after')
    assert calls and all(row['owner_binding_verified'] is False and row['rss_bytes'] is None for row in changed)
    assert all(row['diagnostic_codes']==['BUDGET_RSS_OWNER_CHANGED'] for row in changed)
    calls.clear()
    for recording in packet['recordings']:recording['listed_file_identity_match']=False
    missing=reader.sample_packet(packet,'after')
    assert not calls and missing[0]['diagnostic_codes']==['BUDGET_RSS_BOUND_PROCESS_UNAVAILABLE']
    assert rss.project_samples(missing,1)['budget_process_rss_exceeded']=={'value':None,'collected':False}
    with pytest.raises(KubernetesCaptureError):reader._program(role['container'],['python3','-c',rss.RSS_READER,'42'],4096)


def test_historical_lif04_never_runs_direct_rss_program(tmp_path,group,native_target):
    world,values,pack,_=inputs(tmp_path,group,native_target,'normal');path=tmp_path/'target.json';path.write_bytes(canonical_json(world.target));calls=[]
    draft=KubernetesStreamBufferExecutor({'target':str(path),'prepared':str(group.manifest_path)},
        runner=example_rss_runner(world.runner,calls=calls)).run(pack.checks['LIF-04'],group.env,values,VllmAdapter(),tmp_path/'evidence')
    assert draft.status==ExecStatus.COMPLETED and not calls
    assert 'budget_process_rss_samples' not in draft.observations and 'budget_process_rss_exceeded' not in draft.observations


def test_fixed_program_measures_real_linux_process_and_rejects_changed_identity(tmp_path):
    # WSL is the already-available Linux host on this Windows workstation; no serving engine is started.
    harness='''import hashlib,json,os,subprocess,sys
allocation=bytearray(8*1024*1024)
for i in range(0,len(allocation),4096):allocation[i]=1
context=json.loads(subprocess.check_output([sys.executable,'-I','-S','-B','-c',CONTEXT_READER,str(os.getpid())]))['context']
def sample(expected):
 return json.loads(subprocess.check_output([sys.executable,'-I','-S','-B','-c',RSS_READER,json.dumps(expected,sort_keys=True,separators=(',',':'))]))
normal=sample(context)
changed=dict(context,start_ticks=context['start_ticks']+1)
missing=dict(context,pid=9999999999)
print(json.dumps({'source_sha256':hashlib.sha256(RSS_READER.encode()).hexdigest(),'normal':normal,'changed':sample(changed),'missing':sample(missing)},sort_keys=True))
'''
    script='CONTEXT_READER='+repr(CONTEXT_READER)+'\nRSS_READER='+repr(rss.RSS_READER)+'\n'+harness
    argv=['wsl','-d','Ubuntu','--exec','python3','-c',script] if os.name=='nt' else [sys.executable,'-c',script]
    result=subprocess.run(argv,capture_output=True,text=True,check=True)
    record=json.loads(result.stdout)
    assert record['source_sha256']==rss.RSS_SOURCE_SHA256
    assert record['normal']['process_context_verified'] is True and record['normal']['rss_bytes']>=8*1024*1024
    assert record['changed']['rss_bytes'] is None and record['changed']['diagnostic_codes']==['BUDGET_RSS_CONTEXT_CHANGED']
    assert record['missing']['rss_bytes'] is None and record['missing']['diagnostic_codes']==['BUDGET_RSS_PROCESS_UNAVAILABLE']
    sample={'stage':'before','rss_bytes':record['normal']['rss_bytes'],'process_context_verified':True,'owner_binding_verified':True,
        'query_source_sha256':record['source_sha256'],'diagnostic_codes':[]}
    assert rss.project_samples([sample],1)['budget_process_rss_exceeded']=={'value':True,'collected':True}
    assert rss.project_samples([sample],2**62)['budget_process_rss_exceeded']=={'value':False,'collected':True}
    # This artifact records direct Linux process facts only; Kubernetes owner transport remains a separate EXAMPLE contract.
    artifact=Path(__file__).parents[1]/'out/pack-owner2-extensions/direct-linux-process-rss.json'
    artifact.parent.mkdir(parents=True,exist_ok=True);artifact.write_bytes(canonical_json({'scope':'owned_linux_process_stdlib_only','record':record}))
