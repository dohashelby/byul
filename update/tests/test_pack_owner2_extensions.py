"""Dedicated profile dispatch and independent collection contracts; EXAMPLE I/O."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from guard.adapters.vllm import VllmAdapter
from guard.adapters import vllm_profiler_sensor
from guard.collectors.lifecycle import prepare_assets
from guard.executors import kubernetes_stream_buffer as buffer,kubernetes_initialization as initialization
from guard.executors import kubernetes_profiler_observation as profiler,profiler_lifecycle_cpu as cpu
from guard.executors import kubernetes_observation_composite as composite
from guard.executors.kubernetes_observation_state import KubernetesObservationStateExecutor
from guard.executors.kubernetes_observation_identity import KubernetesObservationIdentityExecutor
from guard.executors.kubernetes_request_termination import KubernetesRequestTerminationExecutor,PROFILE as TERMINATION_PROFILE
from guard.executors.kubernetes_request_attempts import KubernetesRequestAttemptsExecutor,PROFILE as ATTEMPT_PROFILE
from guard.executors.kubernetes_retry_records import KubernetesRetryRecordExecutor
from guard.executors.retry_record_contract import PROFILE as RETRY_PROFILE
from guard.executors.base import EvidenceDraft,DraftArtifact
from guard.models import DataOrigin,ExecStatus,canonical_json,sha256_hex,utcnow
from tests.builders import baseline_pack,environment
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tests.test_a1_stream_buffer_product import inputs as buffer_inputs
from tests.test_a1_initialization_product import inputs as initialization_inputs
from tests.test_a1_observation_state_product import World as StateWorld
from tests.test_profiler_lifecycle_cpu import record

REPO=Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('kind,mode',[(k,m) for k in ('budget','readiness') for m in
    ('normal','engine-replaced','native-partial','http-cleanup')]+[('budget','budget-candidate'),('readiness','partial-failure')])
def test_existing_producers_feed_dedicated_check_profiles(tmp_path,group,native_target,monkeypatch,kind,mode):
    module,inputs,check_id,profile,old_id=(buffer,buffer_inputs,'LIF-03',buffer.BUDGET_PROFILE,'LIF-04') if kind=='budget' else (
        initialization,initialization_inputs,'LIF-07',initialization.READINESS_PROFILE,'LIF-09')
    world,values,pack,_=inputs(tmp_path,group,native_target,mode)
    if kind=='readiness':values['required_readiness_items']=['required-rank','required-model']
    check=baseline_pack().checks[check_id].model_copy(update={'collection_profile':profile,
        'required_observations':pack.checks[old_id].required_observations})
    target=tmp_path/'target.json';target.write_bytes(canonical_json(world.target))
    executor=(buffer.KubernetesStreamBufferExecutor if kind=='budget' else initialization.KubernetesInitializationExecutor)(
        {'target':str(target),'prepared':str(group.manifest_path)},runner=world.runner)
    draft=executor.run(check,group.env,values,VllmAdapter(),tmp_path/'evidence')
    assert draft.data_origin==DataOrigin.EXAMPLE
    assert draft.observations['buffer_scope_complete' if kind=='budget' else 'init_scope_complete'].value is False
    if mode in {'normal','budget-candidate','partial-failure'}:
        assert draft.status==ExecStatus.COMPLETED
        assert draft.observations['buffer_native_samples' if kind=='budget' else 'init_native_samples'].value
    else:assert draft.status==ExecStatus.ABORTED
    assert bool(draft.cleanup_errors)==(mode=='http-cleanup')
    summary=json.loads(draft.artifacts[0].path.read_text()) if len(draft.artifacts[0].path.read_text().splitlines())==1 else json.loads(draft.artifacts[0].path.read_text().splitlines()[-1])
    assert summary['profile']==profile
    if mode=='budget-candidate':
        candidates=draft.observations['buffer_conflict_candidates'].value
        assert candidates and all(c['confirmed_violation'] is False for c in candidates)
    if kind=='readiness':
        assert draft.observations['init_required_readiness_items'].value==['required-rank','required-model']
        assert draft.observations['init_unverified_readiness_items'].value==['required-rank','required-model']
    if mode=='engine-replaced':assert len(world.sent)<=1


@pytest.mark.parametrize('check_id,profile,implementation',[
    ('LIF-10',TERMINATION_PROFILE,KubernetesRequestTerminationExecutor),('LIF-12',RETRY_PROFILE,KubernetesRetryRecordExecutor),
    ('REQ-12',ATTEMPT_PROFILE,KubernetesRequestAttemptsExecutor)])
def test_request_termination_closed_consumer_selection(check_id,profile,implementation):
    executor=KubernetesRequestTerminationExecutor({})
    for candidate in (TERMINATION_PROFILE,RETRY_PROFILE,ATTEMPT_PROFILE,None,'unreviewed'):
        check=SimpleNamespace(id=check_id,collection_profile=candidate)
        if candidate==profile:assert type(executor._consumer(check)) is implementation
        else:
            with pytest.raises(ValueError,match='TERMINATION_PROFILE_UNSUPPORTED'):executor._consumer(check)


@pytest.mark.parametrize('mismatch',[False,True])
def test_profiler_cpu_route_reuses_producer_and_failed_stop_next_start_record(tmp_path,monkeypatch,mismatch):
    sample=record(native_after=mismatch)
    for row in sample['rows']:row['owned_directory_removed']=True
    monkeypatch.setattr(cpu,'capture',lambda **kwargs:sample)
    env=environment(engine={'name':'vllm','version':'reference-'+cpu.SOURCE_COMMIT})
    check=baseline_pack().checks['LIF-14'].model_copy(update={'collection_profile':cpu.PROFILE})
    draft=profiler.KubernetesProfilerObservationExecutor({'source_root':'owned-source','owned_parent':'owned-output'}).run(check,env,{},None,tmp_path)
    assert draft.status==ExecStatus.COMPLETED and draft.data_origin==DataOrigin.EXAMPLE
    assert draft.observations['profiler_cpu_stop_mismatch'].value is mismatch
    assert {r['case_id'] for r in draft.observations['profiler_cpu_trials'].value}==set(cpu.CASE_IDS)
    assert all('next' in r for r in draft.observations['profiler_cpu_trials'].value)
    assert draft.observations['profiler_cpu_deadline_policy'].collected is False
    with pytest.raises(ValueError):profiler.KubernetesProfilerObservationExecutor({}).validate_policy(
        check.model_copy(update={'collection_profile':profiler.PROFILE}),{})



def separate_profiler_target(target,native=False):
    engine=target['kubernetes']['engine']
    engine.update(namespace='profiler-engine',namespace_uid='00000000-0000-0000-0000-000000000003',
        pod_uid='10000000-0000-0000-0000-000000000003',container_id='containerd://'+'3'*64,pod_ip='127.0.0.3')
    if not native:target['engine']['trusted_url']='http://127.0.0.3:8000'

def composite_inputs(tmp_path,group,native_target):
    env=group.env.model_copy(deep=True);env.model.id='EXAMPLE-model';env.model.revision='EXAMPLE-revision';group.env=env.seal()
    target=deepcopy(native_target);target['environment_ref']=group.env.ref().model_dump(mode='json')
    world=StateWorld(group,target,'normal');cfg={};prepared={};targets={}
    for label in ('identity','profiler'):
        targets[label]=deepcopy(world.target)
        if label=='profiler':separate_profiler_target(targets[label])
        path=tmp_path/(label+'-target.json');path.write_bytes(canonical_json(targets[label]));cfg[label+'_target']=str(path)
    for label,profile in [('identity','vllm-native-lifecycle-metadata/8'),('profiler',vllm_profiler_sensor.PROFILE)]:
        folder=group.root/(label+'-assets');prepared[label]=prepare_assets(folder,REPO,profile=profile);cfg[label+'_prepared']=str(folder/'manifest.json')
    now=utcnow();values={'observation_state_environment_sha256':group.env.ref().sha256,
        'identity_target_sha256':sha256_hex(canonical_json(targets['identity'])),
        'profiler_target_sha256':sha256_hex(canonical_json(targets['profiler'])),
        'observation_state_validity_start':(now-timedelta(seconds=30)).isoformat(),
        'observation_state_validity_end':(now+timedelta(seconds=300)).isoformat(),
        'observation_state_required_components':['http_listener'],'identity_record_session':'a'*32,
        'identity_record_source_sha256':'b'*64,'profiler_backend_kind':'torch','profiler_required_ranks':['0']}
    values.update({k+'_prepared_sha256':sha256_hex(canonical_json(v)) for k,v in prepared.items()})
    check=baseline_pack().checks['OBS-02'].model_copy(update={'collection_profile':composite.PROFILE})
    return cfg,values,check


@pytest.mark.parametrize('mode',['normal','first-raise','second-raise','cleanup','missing'])
def test_composite_independent_drafts_preserve_followup_evidence_and_cleanup(tmp_path,group,native_target,monkeypatch,mode):
    cfg,values,check=composite_inputs(tmp_path,group,native_target);seen=[]
    def run(self,child,env,selected,adapter,workdir):
        label='identity' if type(self) is KubernetesObservationIdentityExecutor else 'profiler';seen.append(label)
        assert selected==composite.child_policy(values,label,composite.identity if label=='identity' else profiler)
        if mode==label.replace('identity','first').replace('profiler','second')+'-raise':raise OSError('PRIVATE')
        observations={'state_native_events':{'value':[{'local_seq':1}],'collected':True},
            'state_scope_complete':{'value':False,'collected':True},
            ('identity_missing' if label=='identity' else 'profiler_missing'):{'value':['unverified'],'collected':True}}
        if mode=='missing' and label=='profiler':observations={}
        workdir.mkdir(parents=True);artifact=workdir/'probe_log.jsonl';artifact.write_text(label)
        return EvidenceDraft(status=ExecStatus.COMPLETED,data_origin=DataOrigin.EXAMPLE,normal_control={'attempted':True,'succeeded':None},
            observations=observations,artifacts=[DraftArtifact(name=artifact.name,path=artifact,collector=label)],
            cleanup_errors=['PARENT_CLEANUP_UNVERIFIED'] if mode=='cleanup' and label=='profiler' else [])
    monkeypatch.setattr(KubernetesObservationIdentityExecutor,'run',run);monkeypatch.setattr(profiler.KubernetesProfilerObservationExecutor,'run',run)
    executor=KubernetesObservationStateExecutor(cfg)
    draft=executor.run(check,group.env,values,VllmAdapter(),tmp_path/'result')
    assert seen==['identity','profiler']
    sessions=draft.observations['state_collection_sessions'].value
    assert len({s['session_sha256'] for s in sessions})==2
    assert all(s['environment_sha256']==group.env.ref().sha256 and s['whole_scope_complete'] is False for s in sessions)
    assert draft.observations['state_collection_scope_complete'].value is False
    assert draft.status==(ExecStatus.ABORTED if mode in {'first-raise','second-raise','cleanup'} else ExecStatus.COMPLETED)
    if mode=='normal':
        assert len(draft.observations['state_native_events'].value)==2
        assert {r['collection_component'] for r in draft.observations['state_native_events'].value}=={'identity','profiler'}
        assert {a.name for a in draft.artifacts}=={'identity-probe_log.jsonl','profiler-probe_log.jsonl'}
    if mode in {'missing','first-raise','second-raise'}:
        assert draft.observations['state_native_events'].collected
        assert len(draft.observations['state_native_events'].value)==1
        assert draft.observations['state_native_events'].value[0]['collection_component']==('profiler' if mode=='first-raise' else 'identity')
    if mode=='first-raise':assert draft.observations['profiler_missing'].collected
    assert 'PRIVATE' not in canonical_json(draft.model_dump(mode='json')).decode()


@pytest.mark.parametrize('mode',['same-session','wrong-profiler-hash','unsupported-identity','wrong-target','missing-prepared','same-target'])
def test_composite_rejects_all_bindings_before_first_probe(tmp_path,group,native_target,monkeypatch,mode):
    cfg,values,check=composite_inputs(tmp_path,group,native_target)
    if mode=='same-session':
        # Both manifests remain valid individually; a loader seam isolates the shared-session guard.
        original=composite.load_prepared
        def load(path):
            result=original(path);result['session']='c'*32;return result
        monkeypatch.setattr(composite,'load_prepared',load)
        for label in ('identity','profiler'):values[label+'_prepared_sha256']=sha256_hex(canonical_json(load(cfg[label+'_prepared'])))
    elif mode=='wrong-profiler-hash':values['profiler_prepared_sha256']='0'*64
    elif mode=='unsupported-identity':cfg['identity_prepared']=cfg['profiler_prepared'];values['identity_prepared_sha256']=values['profiler_prepared_sha256']
    elif mode=='wrong-target':values['profiler_target_sha256']='0'*64
    elif mode=='same-target':
        cfg['profiler_target']=cfg['identity_target'];values['profiler_target_sha256']=values['identity_target_sha256']
    else:del cfg['profiler_prepared']
    def forbidden(*args,**kwargs):raise AssertionError('child must not run')
    monkeypatch.setattr(KubernetesObservationIdentityExecutor,'run',forbidden)
    with pytest.raises(ValueError,match='OBSERVATION_COMPOSITE_CONFIGURATION_INVALID'):
        KubernetesObservationStateExecutor(cfg).run(check,group.env,values,VllmAdapter(),tmp_path/'result')




@pytest.mark.parametrize('mode',['normal','missing-profiler','backend-stop-failure'])
def test_composite_consumes_real_identity_and_profiler_producer_outputs(tmp_path,group,native_target,monkeypatch,mode):
    from tests.test_a1_observation_identity_product import inputs as identity_inputs
    from tests.test_a1_profiler_product import inputs as profiler_inputs
    identity_group=deepcopy(group);profiler_group=deepcopy(group)
    identity_folder=tmp_path/'identity';identity_folder.mkdir()
    profiler_folder=tmp_path/'profiler';profiler_folder.mkdir()
    identity_world,identity_values,_=identity_inputs(identity_folder,identity_group,native_target,monkeypatch,'normal')
    profiler_native_target=deepcopy(native_target);separate_profiler_target(profiler_native_target,native=True)
    profiler_world,profiler_values,_=profiler_inputs(profiler_folder,profiler_group,profiler_native_target,monkeypatch,mode)
    profiler_world.target['engine']['trusted_url']='http://127.0.0.3:8000'
    profiler_values['observation_state_target_sha256']=sha256_hex(canonical_json(profiler_world.target))
    assert identity_group.env.ref()==profiler_group.env.ref()
    identity_target=tmp_path/'identity-target.json';identity_target.write_bytes(canonical_json(identity_world.target))
    profiler_target=tmp_path/'profiler-target.json';profiler_target.write_bytes(canonical_json(profiler_world.target))
    cfg={'identity_target':str(identity_target),'profiler_target':str(profiler_target),'identity_prepared':str(identity_folder/'prepared/manifest.json'),
        'profiler_prepared':str(profiler_folder/'prepared/manifest.json')}
    values={**profiler_values,**identity_values}
    del values['observation_state_target_sha256']
    values.update(identity_target_sha256=identity_values['observation_state_target_sha256'],profiler_target_sha256=profiler_values['observation_state_target_sha256'])
    check=baseline_pack().checks['OBS-02'].model_copy(update={'collection_profile':composite.PROFILE})
    identity_run=KubernetesObservationIdentityExecutor.run;profiler_run=profiler.KubernetesProfilerObservationExecutor.run
    def run_identity(self,*args):
        self.runner=identity_world.runner
        return identity_run(self,*args)
    def run_profiler(self,*args):
        self.runner=profiler_world.runner
        return profiler_run(self,*args)
    monkeypatch.setattr(KubernetesObservationIdentityExecutor,'run',run_identity)
    monkeypatch.setattr(profiler.KubernetesProfilerObservationExecutor,'run',run_profiler)
    draft=KubernetesObservationStateExecutor(cfg).run(check,identity_group.env,values,VllmAdapter(),tmp_path/'evidence')
    assert len(identity_world.sent)==len(profiler_world.sent)==5
    assert draft.observations['identity_binding_candidates'].value
    assert draft.observations['state_collection_scope_complete'].value is False
    assert draft.data_origin==DataOrigin.EXAMPLE
    if mode=='missing-profiler':
        assert draft.status==ExecStatus.ABORTED and not draft.observations['profiler_native_samples'].value
    else:
        assert draft.status==ExecStatus.COMPLETED
        assert draft.observations['profiler_native_samples'].value
        assert any(c['flag_adapter_conflict_observed'] for c in draft.observations['profiler_conflict_candidates'].value)==(mode=='backend-stop-failure')
        assert all(c['confirmed_full_state_violation'] is False for c in draft.observations['profiler_conflict_candidates'].value)
    assert len(draft.observations['state_http_trials'].value)==10
    assert {r['collection_component'] for r in draft.observations['state_http_trials'].value}=={'identity','profiler'}
    assert {r['collection_component'] for r in draft.observations['state_native_events'].value}=={'profiler'}


@pytest.mark.parametrize('value',[None,1,['rank',1]])
def test_readiness_retains_original_required_policy_without_inference(value):
    check=baseline_pack().checks['LIF-07'].model_copy(update={'collection_profile':initialization.READINESS_PROFILE})
    with pytest.raises(ValueError,match='INITIALIZATION_READINESS_POLICY_INVALID'):
        initialization.normalize_check_policy(check,{'required_readiness_items':value})
