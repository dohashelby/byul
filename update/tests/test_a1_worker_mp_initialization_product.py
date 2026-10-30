"""Explicitly rebased EXAMPLE recordings through original LIF09 public CLI."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.collectors.lifecycle import prepare_assets,load_prepared
from guard.adapters import vllm_worker_initialization_sensor as sensor,vllm_worker_initialization_metadata as metadata
from guard.models import canonical_json,sha256_hex
from guard.store import Store
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_a1_worker_startup_sensor import scope_value,MODEL_BYTES,PRIVATE
from tests.test_a1_worker_mp_initialization_sensor import make_world
from tests import test_a1_initialization_product as original
from tests import test_a1_worker_startup_admission as admission

REPO=Path(__file__).resolve().parents[2]
ORIGINAL_INPUTS=original.inputs


class World(original.World):
    def add_native(self):
        super().add_native()
        if self.worker_added:return
        self.worker_added=True
        def insert(rows):
            values=[deepcopy(r) for r in self.worker_events if r['event']!='END']
            # These namespace/time/seq changes are solely an EXAMPLE fixture.
            # No actual worker or measured process lineage is claimed.
            context=rows[0]['payload']['context'];index=len(rows)-1
            offset=rows[-1]['monotonic_ns']+1-values[0]['monotonic_ns']
            old={i+1:r for i,r in enumerate(values)}
            for row in values:
                row['monotonic_ns']+=offset;value=row['payload']
                for key in ('operation_started_ns','operation_finished_ns','call_started_ns','observation_started_ns','observation_finished_ns'):
                    if key in value and value[key] is not None:value[key]+=offset
                if row['event']=='WORKER_STARTUP' and value['callback']:
                    value['callback']['binding']['pid']=context['pid']
                    from guard.adapters.vllm_worker_startup_sensor import sha,canonical
                    value['callback']['binding']['process_start_sha256']=sha(canonical(context))
            positions={id(r):index+i for i,r in enumerate(values)}
            for row in values:
                if row['event']=='WORKER_STARTUP_LINK':
                    target=old[row['payload']['weight_event_seq']]
                    row['payload']['weight_event_seq']=positions[id(target)]
                    row['payload']['weight_payload_sha256']=sha256_hex(canonical_json(target['payload']))
            if self.worker_mode=='missing':
                # Keep source/other model facts, while explicit Worker events are absent.
                values=[r for r in values if r['event'] not in {'WORKER_STARTUP','WORKER_STARTUP_LINK'}]
            if self.worker_mode=='duplicate':
                values.append(deepcopy(next(r for r in values if r['event']=='WORKER_STARTUP' and r['payload']['phase']=='CALL')))
            rows[-1]['monotonic_ns']=max(rows[-1]['monotonic_ns'],max(r['monotonic_ns'] for r in values)+1)
            rows[index:index]=values
        self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],insert)


def inputs(tmp_path,group,target,mode,patch,worker_mode):
    old,values,pack,receipt=ORIGINAL_INPUTS(tmp_path,group,target,mode)
    native={'schema':'guard-kubernetes-lifecycle-target/1','lab':old.target['lab'],'run_id':old.target['kubernetes']['run_id'],
        'environment_ref':group.env.ref().model_dump(mode='json'),
        'kubernetes':{k:old.target['kubernetes'][k] for k in ('context','engine')}}
    path=tmp_path/'example-public.safetensors';path.write_bytes(MODEL_BYTES)
    scope=tmp_path/'joint-model-scope.json';scope.write_bytes(canonical_json(scope_value(path,env=group.env,target=native)))
    folder=group.root/'joint-initialization-prepared'
    prepare_assets(folder,REPO,profile=sensor.PROFILE,model_read_scope=scope)
    group.prepared=load_prepared(folder/'manifest.json');group.manifest_path=folder/'manifest.json'
    def header(rows):
        rows[0]['payload'].update(profile=sensor.PROFILE,schema=sensor.SCHEMA,session=group.prepared['session'],
            settings_sha256=group.prepared['assets']['settings.json']['sha256'],sensor_sha256=group.prepared['assets']['guard_lifecycle_sensor.py']['sha256'])
    for pid in group.raw_by_pid:group.raw_by_pid[pid]=revised(group.raw_by_pid[pid],header)
    values['init_prepared_sha256']=sha256_hex(canonical_json(group.prepared))
    producer=make_world(tmp_path,patch,session=group.prepared['session'],path=path,scope_path=folder/'model-read-scope.json')
    assert producer.start() is None;producer.close()
    world=World.__new__(World);world.__dict__.update(old.__dict__)
    world.worker_events=producer.observer.recorded;world.worker_added=False;world.worker_mode=worker_mode
    return world,values,pack,receipt


@pytest.mark.parametrize('mode',['normal','partial-failure','after-timeout','seal-failure','attachment','missing','duplicate','consumer-error'])
def test_existing_LIF09_CLI_worker_facts_and_exact_refs(tmp_path,group,native_target,writer_factory,monkeypatch,mode):
    selected=mode if mode not in {'missing','duplicate','consumer-error'} else 'normal'
    monkeypatch.setattr(original,'inputs',lambda p,g,t,m:inputs(p,g,t,m,monkeypatch,mode))
    if mode=='consumer-error':
        def unavailable(*args,**kwargs):raise ValueError(PRIVATE)
        monkeypatch.setattr(metadata,'project',unavailable)
    # Original product assertions, criteria, redaction and saved-pack Ref round trip stay intact.
    original.test_registered_cli_preparation_and_aggregate_claims_exact_refs(tmp_path,group,native_target,monkeypatch,selected)
    e,=Store.read_only(tmp_path/'store').iter_kind('EVD')
    events=e.observations['init_worker_startup_events'].value
    candidates=e.observations['init_worker_startup_candidates'].value
    missing=e.observations['init_worker_startup_missing'].value
    assert e.observations['init_native_samples'].value
    if mode=='consumer-error':
        assert not events and 'worker_startup_projection_unavailable' in missing
        assert e.execution.status.value=='ABORTED'
    elif mode=='missing':assert not events and 'worker_startup_method_observation_unavailable' in missing
    else:
        assert events and candidates
        assert all(not c['actual_worker_observed'] for c in candidates)
        if mode=='duplicate':assert 'worker_startup_unique_call_pair_unavailable' in missing
        else:assert any(c['same_startup_model_root_candidate'] for c in candidates)
    assert all(getattr(next(iter(Store.read_only(tmp_path/'store').iter_kind('RES'))).verdicts,key).value=='UNKNOWN' for key in ('security','functional','performance'))


def test_public_prepare_thirteen_original_assets(tmp_path,group,native_target):
    from tests.test_a1_model_read_scope import reference
    env,target,manifest=reference(tmp_path,group,native_target)
    for name,value in [('environment',env.to_dict()),('target',target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    runner=CliRunner();scope=tmp_path/'scope.json'
    result=runner.invoke(app,['prepare-model-read-scope','--env',str(tmp_path/'environment.json'),'--target',str(tmp_path/'target.json'),
        '--model-manifest',str(manifest),'--model-root','/models/UNMOUNTED','--nonsecret-model-weights','--output',str(scope)])
    assert result.exit_code==0,(result.output,result.exception)
    destination=tmp_path/'prepared20'
    result=runner.invoke(app,['prepare-lifecycle-sensor','--profile',sensor.PROFILE,'--model-read-scope',str(scope),'--output',str(destination)])
    assert result.exit_code==0,(result.output,result.exception)
    value=load_prepared(destination/'manifest.json')
    assert len(value['assets'])==13 and not value['engine_started']
    assert sha256_hex((destination/'guard_worker_startup.py').read_bytes())==sensor.STARTUP19_SHA256
    assert sha256_hex((destination/'guard_initialization.py').read_bytes())==sensor.INITIALIZATION14_SHA256
    (destination/'guard_initialization.py').write_bytes((destination/'guard_initialization.py').read_bytes()+b'\n')
    with pytest.raises(ValueError,match='LIFECYCLE_ASSET_BYTES_INVALID'):load_prepared(destination/'manifest.json')


@pytest.mark.parametrize('change',['env','target','model','revision','inventory','version'])
def test_profile20_retains_exact_scope_and_native032_gate(tmp_path,group,native_target,monkeypatch,change):
    monkeypatch.setattr(admission,'sensor',sensor)
    admission.test_new_profile_preserves_exact_target_model_and_native032_rejection_before_probe(tmp_path,group,native_target,change)
