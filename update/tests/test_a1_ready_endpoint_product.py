"""Existing public LIF09/exact Ref contract consumes explicit EXAMPLE native22.

These fixtures execute fixed source bodies with substituted dependencies and
an inline Process.start double. They do not observe an actual serving worker.
"""
from copy import deepcopy
from pathlib import Path
import json,sys
import pytest
from guard.adapters import vllm_ready_endpoint_sensor as sensor,vllm_ready_endpoint_metadata as metadata
from guard.adapters import vllm_worker_generation_sensor as generation
from guard.collectors.lifecycle import prepare_assets,load_prepared
from guard.models import canonical_json,sha256_hex
from guard.store import Store
from tests import test_a1_initialization_product as original
from tests.test_a1_ready_endpoint_sensor import make_world,records,rebase_rows
from tests.test_a1_file_read_sensor import scope_value,MODEL_BYTES
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_sensor import writer_factory
from tests.test_lifecycle_capture import revised

REPO=Path(__file__).resolve().parents[2];ORIGINAL_INPUTS=original.inputs
PRIVATE='EXAMPLE_READY_ENDPOINT_PRODUCT_PRIVATE'


class World(original.World):
    def add_native(self):
        if self.native_added:return
        self.native_added=True
        def insert(rows):
            values=deepcopy([r for r in self.endpoint_rows if r['event'] not in {'START','END'}])
            offset=rows[-1]['monotonic_ns']+1-values[0]['monotonic_ns']
            for row in values:
                row['monotonic_ns']+=offset;value=row['payload']
                for key in ('operation_started_ns','operation_finished_ns','call_started_ns','observation_started_ns','observation_finished_ns'):
                    if value.get(key) is not None:value[key]+=offset
            if self.endpoint_mode=='missing-send':
                values=[r for r in values if not (r['event']=='READY_ENDPOINT' and r['payload']['operation']=='SEND' and r['payload']['phase']=='RETURN')]
            if self.endpoint_mode=='duplicate-creator':
                duplicate=deepcopy(next(r for r in values if r['event']=='READY_ENDPOINT' and r['payload']['operation']=='CREATE' and r['payload']['phase']=='CALL'))
                duplicate['seq']=max(r['seq'] for r in values)+1;duplicate['monotonic_ns']=max(r['monotonic_ns'] for r in values)+1
                values.append(duplicate)
            values=rebase_rows(values,rows[0]['payload']['context'],start_index=len(rows)-1)
            rows[-1]['monotonic_ns']=max(rows[-1]['monotonic_ns'],max(r['monotonic_ns'] for r in values)+1)
            rows[-1:-1]=values
        self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],insert)


def inputs(tmp_path,group,target,mode,patch,endpoint_mode):
    old,values,pack,receipt=ORIGINAL_INPUTS(tmp_path,group,target,mode)
    native={'schema':'guard-kubernetes-lifecycle-target/1','lab':old.target['lab'],'run_id':old.target['kubernetes']['run_id'],
        'environment_ref':group.env.ref().model_dump(mode='json'),'kubernetes':{k:old.target['kubernetes'][k] for k in ('context','engine')}}
    path=tmp_path/'endpoint-public.safetensors';path.write_bytes(MODEL_BYTES)
    scope=tmp_path/'endpoint-scope.json';scope.write_bytes(canonical_json(scope_value(path,env=group.env,target=native)))
    folder=group.root/'endpoint-initialization-prepared';prepare_assets(folder,REPO,profile=sensor.PROFILE,model_read_scope=scope)
    group.prepared=load_prepared(folder/'manifest.json');group.manifest_path=folder/'manifest.json'
    def header(rows):
        rows[0]['payload'].update(profile=sensor.PROFILE,schema=sensor.SCHEMA,session=group.prepared['session'],
            settings_sha256=group.prepared['assets']['settings.json']['sha256'],sensor_sha256=group.prepared['assets']['guard_lifecycle_sensor.py']['sha256'])
    for pid in group.raw_by_pid:group.raw_by_pid[pid]=revised(group.raw_by_pid[pid],header)
    values['init_prepared_sha256']=sha256_hex(canonical_json(group.prepared))
    producer=make_world(tmp_path,patch,session=group.prepared['session'],mode=endpoint_mode if endpoint_mode in {'eof','constructor-failure','send-failure'} else 'normal',
        path=path,scope_path=folder/'model-read-scope.json')
    try:
        try:producer.invoke()
        except Exception:
            assert sys.platform!='linux' or endpoint_mode in {'eof','constructor-failure','send-failure'}
    finally:producer.close()
    world=World.__new__(World);world.__dict__.update(old.__dict__)
    world.endpoint_rows=records(producer);world.endpoint_mode=endpoint_mode;world.native_added=False
    return world,values,pack,receipt


@pytest.mark.parametrize('mode',['normal','eof','constructor-failure','send-failure','after-timeout','seal-failure','attachment','missing-send','duplicate-creator','projection-error'])
def test_public_LIF09_native22_original_pipeline_and_exact_refs(tmp_path,group,native_target,writer_factory,monkeypatch,mode):
    selected=mode if mode in {'after-timeout','seal-failure','attachment'} else 'normal'
    monkeypatch.setattr(original,'inputs',lambda p,g,t,m:inputs(p,g,t,m,monkeypatch,mode))
    if mode=='projection-error':
        def fail(*args,**kwargs):raise ValueError(PRIVATE)
        monkeypatch.setattr(metadata,'project',fail)
    original.test_registered_cli_preparation_and_aggregate_claims_exact_refs(tmp_path,group,native_target,monkeypatch,selected)
    e,=Store.read_only(tmp_path/'store').iter_kind('EVD')
    assert e.observations['init_native_samples'].value
    endpoint_events=e.observations['init_ready_endpoint_events'].value
    if mode=='projection-error':assert not endpoint_events and e.execution.status.value=='ABORTED'
    else:
        assert endpoint_events
        assert all(row[key] is False for row in endpoint_events for key in sensor.TRUTH)
        # Inline source-body roles share one declared recording here. Actual
        # worker lineage must never follow from these PID claims.
        assert not any(row['same_held_fifo_candidate'] for row in e.observations['init_ready_endpoint_candidates'].value)
        if mode not in {'eof','constructor-failure'}:assert e.observations['init_worker_generation_events'].value


@pytest.mark.parametrize('profile',['vllm-native-lifecycle-metadata/14','vllm-native-lifecycle-metadata/20','vllm-native-lifecycle-metadata/21'])
def test_native22_requires_its_own_recording_header(profile):
    old={'profile':profile,'session':'a'*32}
    with pytest.raises(ValueError):metadata.project({'recordings':[{'capture':{'records':[{'payload':{'profile':profile,'schema':generation.SCHEMA,'session':old['session'],'context':{}}}]}}]},old)


def test_old_check_without_endpoint_declarations_rejected_before_target_io(tmp_path,group,native_target,monkeypatch):
    from guard.executors.kubernetes_initialization import KubernetesInitializationExecutor
    world,values,pack,_=inputs(tmp_path,group,native_target,'normal',monkeypatch,'normal')
    check=pack.checks['LIF-09'];old=check.model_copy(update={'required_observations':[o for o in check.required_observations if o.key not in {'init_ready_endpoint_events','init_ready_endpoint_candidates'}]})
    target=tmp_path/'target.json';target.write_bytes(canonical_json(world.target))
    executor=KubernetesInitializationExecutor({'target':str(target),'prepared':str(group.manifest_path)},runner=lambda *a,**kw:pytest.fail('target IO before declarations'))
    with pytest.raises(ValueError,match='INITIALIZATION_CONFIGURATION_INVALID'):
        executor.run(old,group.env,values,NSAdapter(),tmp_path/'work')


class NSAdapter:
    pass
