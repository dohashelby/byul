"""Existing public LIF09 product consumes EXAMPLE native21 facts and exact Refs."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.collectors.lifecycle import prepare_assets,load_prepared
from guard.adapters import vllm_worker_generation_sensor as sensor,vllm_worker_generation_metadata as metadata
from guard.adapters import vllm_worker_startup_sensor as startup
from guard.models import canonical_json,sha256_hex
from guard.store import Store
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_a1_worker_generation_sensor import make_world,scope_value,MODEL_BYTES,PRIVATE
from tests import test_a1_initialization_product as original
from tests import test_a1_worker_startup_admission as admission

REPO=Path(__file__).resolve().parents[2];ORIGINAL_INPUTS=original.inputs


class World(original.World):
    def add_native(self):
        super().add_native()
        if self.worker_added:return
        self.worker_added=True
        def insert(rows):
            values=[deepcopy(r) for r in self.worker_events if r['event']!='END']
            context=rows[0]['payload']['context'];index=len(rows)-1;offset=rows[-1]['monotonic_ns']+1-values[0]['monotonic_ns']
            # Only EXAMPLE namespace/time/seq rebasing. No actual worker claim.
            old={i+1:r for i,r in enumerate(values)}
            for row in values:
                row['monotonic_ns']+=offset;value=row['payload']
                for key in ('operation_started_ns','operation_finished_ns','call_started_ns','observation_started_ns','observation_finished_ns'):
                    if key in value and value[key] is not None:value[key]+=offset
                if row['event']=='WORKER_STARTUP' and value['callback']:
                    value['callback']['binding']['pid']=context['pid']
                    value['callback']['binding']['process_start_sha256']=startup.sha(startup.canonical(context))
                if row['event'] in metadata.VALIDATORS:value['process_sha256']=sensor.sha(sensor.canonical(context))
            positions={id(r):index+i for i,r in enumerate(values)}
            for row in values:
                value=row['payload']
                if row['event']=='WORKER_STARTUP_LINK':
                    target=old[value['weight_event_seq']];value['weight_event_seq']=positions[id(target)]
                    value['weight_payload_sha256']=startup.sha(startup.canonical(target['payload']))
                if row['event']=='WORKER_GENERATION_LINK':
                    target=old[value['worker_event_seq']]
                    for key in ('worker_event_seq','constructor_event_seq','wrapper_event_seq'):value[key]=positions[id(old[value[key]])]
                    value['worker_payload_sha256']=sensor.sha(sensor.canonical(target['payload']))
            if self.worker_mode=='missing-ready':values=[r for r in values if r['event']!='WORKER_READY_SEND']
            if self.worker_mode=='duplicate':
                duplicate=deepcopy(next(r for r in values if r['event']=='WORKER_GENERATION' and r['payload']['point']=='WorkerProc.__init__' and r['payload']['phase']=='CALL'))
                duplicate['monotonic_ns']=max(r['monotonic_ns'] for r in values)+1;values.append(duplicate)
            rows[-1]['monotonic_ns']=max(rows[-1]['monotonic_ns'],max(r['monotonic_ns'] for r in values)+1)
            rows[index:index]=values
        self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],insert)


def inputs(tmp_path,group,target,mode,patch,worker_mode):
    old,values,pack,receipt=ORIGINAL_INPUTS(tmp_path,group,target,mode)
    native={'schema':'guard-kubernetes-lifecycle-target/1','lab':old.target['lab'],'run_id':old.target['kubernetes']['run_id'],
        'environment_ref':group.env.ref().model_dump(mode='json'),'kubernetes':{k:old.target['kubernetes'][k] for k in ('context','engine')}}
    path=tmp_path/'example-public.safetensors';path.write_bytes(MODEL_BYTES)
    scope=tmp_path/'generation-model-scope.json';scope.write_bytes(canonical_json(scope_value(path,env=group.env,target=native)))
    folder=group.root/'generation-initialization-prepared';prepare_assets(folder,REPO,profile=sensor.PROFILE,model_read_scope=scope)
    group.prepared=load_prepared(folder/'manifest.json');group.manifest_path=folder/'manifest.json'
    def header(rows):
        rows[0]['payload'].update(profile=sensor.PROFILE,schema=sensor.SCHEMA,session=group.prepared['session'],
            settings_sha256=group.prepared['assets']['settings.json']['sha256'],sensor_sha256=group.prepared['assets']['guard_lifecycle_sensor.py']['sha256'])
    for pid in group.raw_by_pid:group.raw_by_pid[pid]=revised(group.raw_by_pid[pid],header)
    values['init_prepared_sha256']=sha256_hex(canonical_json(group.prepared))
    producer=make_world(tmp_path,patch,session=group.prepared['session'],mode=worker_mode if worker_mode in {'constructor-failure','send-failure'} else 'normal',
        path=path,scope_path=folder/'model-read-scope.json')
    assert producer.start() is None;producer.close()
    world=World.__new__(World);world.__dict__.update(old.__dict__);world.worker_events=producer.observer.recorded
    world.worker_added=False;world.worker_mode=worker_mode
    return world,values,pack,receipt


@pytest.mark.parametrize('mode',['normal','constructor-failure','send-failure','after-timeout','seal-failure','attachment','missing-ready','duplicate','projection-error'])
def test_existing_LIF09_CLI_native21_separate_facts_and_exact_refs(tmp_path,group,native_target,writer_factory,monkeypatch,mode):
    selected=mode if mode in {'after-timeout','seal-failure','attachment'} else 'normal'
    monkeypatch.setattr(original,'inputs',lambda p,g,t,m:inputs(p,g,t,m,monkeypatch,mode))
    if mode=='projection-error':
        def unavailable(*args,**kwargs):raise ValueError(PRIVATE)
        monkeypatch.setattr(metadata,'project',unavailable)
    original.test_registered_cli_preparation_and_aggregate_claims_exact_refs(tmp_path,group,native_target,monkeypatch,selected)
    e,=Store.read_only(tmp_path/'store').iter_kind('EVD')
    assert e.observations['init_native_samples'].value
    events=e.observations['init_worker_generation_events'].value
    candidates=e.observations['init_worker_generation_candidates'].value
    ready=e.observations['init_ready_send_observations'].value
    if mode=='projection-error':
        assert not events and e.execution.status.value=='ABORTED'
    else:
        assert events
        assert all(r['cross_process_lineage_verified'] is False and r['actual_worker_verified'] is False for r in candidates)
        if mode=='constructor-failure':assert not e.observations['init_worker_startup_candidates'].value
        else:assert e.observations['init_worker_startup_candidates'].value and candidates
        if mode=='send-failure':assert any(r['original_send_raise_observed'] for r in ready) and not any(r['original_send_return_observed'] for r in ready)
        elif mode=='missing-ready':assert not ready
        elif mode!='constructor-failure':assert any(r['original_send_return_observed'] for r in ready)
        if mode=='duplicate':assert not any(r['local_object_generation_candidate'] for r in candidates)


def test_existing_prepare_CLI_fifteen_assets_and_fixed_dependencies(tmp_path,group,native_target):
    from tests.test_a1_model_read_scope import reference
    env,target,manifest=reference(tmp_path,group,native_target)
    for name,value in [('environment',env.to_dict()),('target',target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    runner=CliRunner();scope=tmp_path/'scope.json'
    result=runner.invoke(app,['prepare-model-read-scope','--env',str(tmp_path/'environment.json'),'--target',str(tmp_path/'target.json'),
        '--model-manifest',str(manifest),'--model-root','/models/UNMOUNTED','--nonsecret-model-weights','--output',str(scope)])
    assert result.exit_code==0,(result.output,result.exception)
    out=tmp_path/'prepared21'
    result=runner.invoke(app,['prepare-lifecycle-sensor','--profile',sensor.PROFILE,'--model-read-scope',str(scope),'--output',str(out)])
    assert result.exit_code==0,(result.output,result.exception)
    prepared=load_prepared(out/'manifest.json');assert len(prepared['assets'])==15 and not prepared['engine_started']
    assert sha256_hex((out/'guard_worker_initialization.py').read_bytes())==sensor.JOINT20_SHA256
    assert sha256_hex((out/'guard_worker_generation_contract.py').read_bytes())==sensor.CONTRACT_SHA256
    p=out/'guard_worker_initialization.py';p.write_bytes(p.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='LIFECYCLE_ASSET_BYTES_INVALID'):load_prepared(out/'manifest.json')


@pytest.mark.parametrize('change',['env','target','model','revision','inventory','version'])
def test_profile21_retains_exact_scope_and_native032_rejection(tmp_path,group,native_target,monkeypatch,change):
    monkeypatch.setattr(admission,'sensor',sensor)
    admission.test_new_profile_preserves_exact_target_model_and_native032_rejection_before_probe(tmp_path,group,native_target,change)


@pytest.mark.parametrize('profile',['vllm-native-lifecycle-metadata/14','vllm-native-lifecycle-metadata/19','vllm-native-lifecycle-metadata/20'])
def test_generation_cannot_be_consumed_by_old_profile_relabel(tmp_path,group,native_target,writer_factory,monkeypatch,profile):
    from guard.executors.lifecycle_capture import parse_recording
    world,values,pack,receipt=inputs(tmp_path,group,native_target,'normal',monkeypatch,'normal');world.add_native()
    records,summary=parse_recording(group.raw_by_pid[42],group.prepared);assert summary['worker_generation_events']>0
    prepared=deepcopy(group.prepared);prepared['profile']=profile
    def header(rows):rows[0]['payload'].update(profile=profile,schema='guard-native-lifecycle-events/'+profile.rsplit('/',1)[1])
    with pytest.raises(ValueError,match='LIFECYCLE_RECORDING_ROW_INVALID'):parse_recording(revised(group.raw_by_pid[42],header),prepared)


def test_old_check_without_generation_declarations_cannot_run_profile21(tmp_path,group,native_target,writer_factory,monkeypatch):
    from guard.executors.kubernetes_initialization import KubernetesInitializationExecutor
    world,values,pack,receipt=inputs(tmp_path,group,native_target,'normal',monkeypatch,'normal')
    keys={'init_worker_generation_events','init_worker_generation_candidates','init_ready_send_observations'}
    check=pack.checks['LIF-09'].model_copy(update={'required_observations':[o for o in pack.checks['LIF-09'].required_observations if o.key not in keys]})
    path=tmp_path/'target.json';path.write_bytes(canonical_json(world.target))
    def forbidden(*args,**kwargs):pytest.fail('old check must be rejected before target IO')
    executor=KubernetesInitializationExecutor({'target':str(path),'prepared':str(group.manifest_path)},runner=forbidden)
    with pytest.raises(ValueError,match='INITIALIZATION_CONFIGURATION_INVALID'):
        executor.run(check,group.env,values,object(),tmp_path/'unsupported')
    assert world.sent==[]
