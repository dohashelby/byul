"""Existing OBS02 CLI with explicitly EXAMPLE-rebased profile19 input/Refs."""
from copy import deepcopy
from pathlib import Path
import json,socket,subprocess
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.collectors.lifecycle import prepare_assets,load_prepared
from guard.adapters import vllm_worker_startup_sensor as sensor,vllm_worker_startup_metadata as metadata
from guard.runtime import kubernetes_http as http
from guard.executors import observation_identity_contract as identity_contract
from guard.models import canonical_json,sha256_hex
from guard.store import Store
from tests.builders import policy,T0
from tests.test_lifecycle_sensor import writer_factory
from tests.test_lifecycle_capture import case,external,revised
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_a1_observation_identity_product import World as IdentityWorld,inputs as identity_inputs
from tests.test_a1_worker_startup_sensor import make_world,scope_value,MODEL_BYTES,PRIVATE
REPO=Path(__file__).resolve().parents[2]


class World(IdentityWorld):
    def add_native(self,rid):
        super().add_native(rid)
        if self.weight_inserted:
            if self.weight_mode!='wrong-model':
                def bind(rows):
                    for row in rows:
                        if row['event']=='NATIVE' and row['payload']['point']=='GPUModelRunner._model_forward':row['payload']['metadata']['model_selection']['owner_model_sha256']=self.weight_model
                self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],bind)
            return
        self.weight_inserted=True
        def insert(rows):
            index=next(i for i,row in enumerate(rows) if row['event']=='NATIVE' and row['payload']['point']=='BaseModelLoader.load_model' and row['payload']['phase']=='RETURN')
            values=deepcopy(self.weight_events);start=rows[index-1]['monotonic_ns']+1;offset=start-values[0]['monotonic_ns']
            context=rows[0]['payload']['context']
            # These PID/start/time/seq changes construct an EXAMPLE packet.
            # They are not actual producer/worker binding or remeasured evidence.
            old_sequences={i+1:row for i,row in enumerate(values)}
            values=[row for row in values if row['event']!='END']
            for row in values:
                row['monotonic_ns']+=offset;payload=row['payload']
                if row['event'] in {'WEIGHT_ITERATION','SAFE_OPEN_MAPPING','WORKER_STARTUP'}:
                    for key in ('operation_started_ns','operation_finished_ns','call_started_ns'):
                        if key in payload and payload[key] is not None:payload[key]+=offset
                if row['event']=='WORKER_STARTUP' and payload['callback']:
                    payload['callback']['binding']['pid']=context['pid']
                    payload['callback']['binding']['process_start_sha256']=sensor.sha(sensor.canonical(context))
                if row['event'] in {'NATIVE','SELECTION'}:
                    for key in ('observation_started_ns','observation_finished_ns'):payload[key]+=offset
            indexes={id(row):index+i for i,row in enumerate(values)}
            for row in values:
                if row['event']=='WORKER_STARTUP_LINK':
                    target=old_sequences[row['payload']['weight_event_seq']]
                    row['payload']['weight_event_seq']=indexes[id(target)]
                    row['payload']['weight_payload_sha256']=sensor.sha(sensor.canonical(target['payload']))
                    if self.weight_mode=='wrong-link':row['payload']['weight_payload_sha256']='0'*64
            extra=values[-1]['monotonic_ns']-start+1
            for row in rows[index:]:row['monotonic_ns']+=extra
            if self.weight_mode!='wrong-model':
                for row in rows[index:]:
                    if row['event']=='NATIVE' and row['payload']['point']=='BaseModelLoader.load_model':row['payload']['metadata']['model_selection']['returned_model_sha256']=self.weight_model
            rows[index:index]=values
        self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],insert)
        if self.weight_mode!='wrong-model':
            def forwards(rows):
                for row in rows:
                    if row['event']=='NATIVE' and row['payload']['point']=='GPUModelRunner._model_forward':row['payload']['metadata']['model_selection']['owner_model_sha256']=self.weight_model
            self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],forwards)


def inputs(tmp_path,group,target,patch,mode):
    env=group.env.model_copy(deep=True);env.model.id='EXAMPLE-model';env.model.revision='EXAMPLE-revision'
    env.model.inventory_sha256=sha256_hex(canonical_json([{'path':'example-public.safetensors','sha256':sha256_hex(MODEL_BYTES),'bytes':len(MODEL_BYTES)}]))
    group.env=env.seal();target=deepcopy(target);target['environment_ref']=group.env.ref().model_dump(mode='json')
    path=tmp_path/'example-public.safetensors';path.write_bytes(MODEL_BYTES)
    scope=tmp_path/'declared-read-scope.json';scope.write_bytes(canonical_json(scope_value(path,env=group.env,target=target)))
    destination=group.root/'worker-assets';prepare_assets(destination,REPO,profile=sensor.PROFILE,model_read_scope=scope)
    group.prepared=load_prepared(destination/'manifest.json');group.manifest_path=destination/'manifest.json'
    def header(rows):
        rows[0]['payload'].update(profile=sensor.PROFILE,schema=sensor.SCHEMA,session=group.prepared['session'],
            sensor_sha256=group.prepared['assets']['guard_lifecycle_sensor.py']['sha256'],settings_sha256=group.prepared['assets']['settings.json']['sha256'])
    for pid in group.raw_by_pid:group.raw_by_pid[pid]=revised(group.raw_by_pid[pid],header)
    world,values,pack=identity_inputs(tmp_path,group,target,patch,'late-record-error' if mode=='later-record-error' else 'normal')
    producer=make_world(tmp_path,patch,mode='late-callback' if mode=='late-callback' else 'normal',
        session=group.prepared['session'],path=path,scope_path=destination/'model-read-scope.json',otel_available=True)
    if mode=='late-callback':
        with pytest.raises(OSError,match=PRIVATE):producer.start()
    elif mode=='no-worker-call':assert producer.run_loader() is None
    else:assert producer.start() is None
    producer.close()
    result=World.__new__(World);result.__dict__.update(world.__dict__);result.weight_mode=mode
    result.weight_events=producer.observer.recorded;result.weight_model=producer.observer.model_reference(producer.model);result.weight_inserted=False
    return result,values,pack


@pytest.mark.parametrize('mode',['normal','late-callback','wrong-model','wrong-link','no-worker-call','later-record-error','attachment','consumer-error'])
def test_existing_identity_cli_profile19_facts_and_exact_ref_roundtrip(tmp_path,group,native_target,writer_factory,monkeypatch,mode):
    world,values,pack=inputs(tmp_path,group,native_target,monkeypatch,mode)
    if mode=='consumer-error':
        def unavailable(*a,**kw):raise ValueError(PRIVATE)
        monkeypatch.setattr(metadata,'project',unavailable)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected external runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def opened(path,*a,**kw):
            if path.name=='probe_log.jsonl' and a and a[0]=='w':raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',opened)
    pol=policy(pack.ref(),scope={'include':['OBS-02']},values={'OBS-02':values})
    for name,value in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target)]:(tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    config=tmp_path/'guard.toml';config.write_text('[executors]\nobservation_identity="kubernetes"\n[adapter]\nname="vllm"\n[observation_identity]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(tmp_path/'prepared/manifest.json'))+'\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',config]
    def cli(args):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==0,(result.output,result.exception);assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    cli(['run','--plan',plan.id,'--pack',pack.path,*common]);cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common])
    cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');evd,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert evd.data_origin.value=='EXAMPLE' and evd.normal_control.succeeded is None
    assert all(getattr(res.verdicts,key).value=='UNKNOWN' for key in ('security','functional','performance'))
    assert all(evd.observations[key].value is False for key in identity_contract.TRUTH_FIELDS)
    models=evd.observations['identity_native_models'].value
    weights=[row for trial in models for model in trial['configured_model_candidates'] for row in model.get('weight_iteration_candidates',[])]
    startups=[row for trial in models for model in trial['configured_model_candidates'] for row in model.get('worker_startup_candidates',[])]
    if mode in {'normal','late-callback','later-record-error','attachment'}:
        assert startups and any(row['same_startup_model_root_candidate'] for row in startups)
    else:assert startups==[]
    if mode not in {'wrong-model','wrong-link'}:assert any(row['same_process_iteration_observed'] for row in weights)
    assert all(row[key] is False for row in startups for key in sensor.TRUTH)
    if mode=='consumer-error':assert evd.execution.status.value=='ABORTED' and weights
    assert PRIVATE not in json.dumps(evd.to_dict())
    refs=sorted((o.kind.value,o.id,o.revision,o.content_sha256) for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind));assert len(refs)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'evidence.zip'])
    cli(['evidence','restore','--bundle',tmp_path/'evidence.zip','--destination',tmp_path/'restored'])
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',config])
    cli(['verify','--store',tmp_path/'restored','--config',config])
    restored=Store.read_only(tmp_path/'restored')
    assert refs==sorted((o.kind.value,o.id,o.revision,o.content_sha256) for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in restored.iter_kind(kind))



def test_unknown_module_stays_rejected_in_new_recording_profile(tmp_path,group,native_target,writer_factory,monkeypatch):
    from guard.executors.lifecycle_capture import parse_recording
    world,values,pack=inputs(tmp_path,group,native_target,monkeypatch,'normal')
    world.add_native('EXAMPLE-public-canary')
    def changed(rows):
        row=next(row for row in rows if row['event']=='MODULE')
        row['payload']['module']='EXAMPLE_UNREVIEWED_MODULE'
    raw=revised(world.group.raw_by_pid[42],changed)
    with pytest.raises(ValueError,match='LIFECYCLE_RECORDING_ROW_INVALID'):parse_recording(raw,world.group.prepared)
