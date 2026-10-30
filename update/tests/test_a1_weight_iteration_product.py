"""Native16 -> existing OBS02 registry/CLI/pack; all external I/O is EXAMPLE."""
from copy import deepcopy
from pathlib import Path
import json,shutil,socket,subprocess
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.collectors.lifecycle import prepare_assets,load_prepared
from guard.models import canonical_json,sha256_hex
from guard.runtime import kubernetes_http as http
from guard.store import Store
from guard.adapters import vllm_weight_iteration_sensor as sensor
from guard.executors import observation_identity_contract as contract
from tests.builders import policy,T0
from tests.test_lifecycle_sensor import writer_factory
from tests.test_lifecycle_capture import case,external,revised
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_a1_observation_identity_product import World as IdentityWorld,inputs as identity_inputs
from tests.test_a1_weight_iteration_sensor import make_world,PRIVATE

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
            values=deepcopy(self.weight_events)
            start=rows[index-1]['monotonic_ns']+1;offset=start-values[0]['monotonic_ns']
            for row in values:
                row['monotonic_ns']+=offset
                if row['event']=='WEIGHT_ITERATION':
                    for key in ('operation_started_ns','operation_finished_ns'):
                        if row['payload'][key] is not None:row['payload'][key]+=offset
                    if self.weight_mode=='wrong-source':row['payload']['source_sha256']='0'*64
                if row['event'] in {'NATIVE','SELECTION'}:
                    for key in ('observation_started_ns','observation_finished_ns'):row['payload'][key]+=offset
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
    destination=group.root/'weight-assets';prepare_assets(destination,REPO,profile=sensor.PROFILE)
    group.prepared=load_prepared(destination/'manifest.json');group.manifest_path=destination/'manifest.json'
    def header(rows):
        rows[0]['payload'].update(profile=sensor.PROFILE,schema=sensor.SCHEMA,session=group.prepared['session'],
            sensor_sha256=group.prepared['assets']['guard_lifecycle_sensor.py']['sha256'],settings_sha256=group.prepared['assets']['settings.json']['sha256'])
    for pid in group.raw_by_pid:group.raw_by_pid[pid]=revised(group.raw_by_pid[pid],header)
    base_mode='late-record-error' if mode=='later-record-error' else 'normal'
    world,values,pack=identity_inputs(tmp_path,group,target,patch,base_mode)
    producer=make_world('late-callback' if mode=='late-callback' else 'normal',session=group.prepared['session'])
    if mode=='no-iteration':producer.load().close()
    elif mode=='partial':
        iterator=producer.load();next(iterator);iterator.close()
    elif mode=='late-callback':
        iterator=producer.load()
        assert next(iterator)=='weight'
        with pytest.raises(OSError,match=PRIVATE):next(iterator)
    else:assert producer.run_loader() is None
    result=World.__new__(World);result.__dict__.update(world.__dict__)
    result.weight_mode=mode;result.weight_events=producer.observer.recorded;result.weight_model=producer.observer.model_reference(producer.model);result.weight_inserted=False
    return result,values,pack


@pytest.mark.parametrize('mode',['normal','no-iteration','partial','late-callback','wrong-source','wrong-model','later-record-error','attachment'])
def test_existing_identity_product_native16_consumption_and_exact_refs(tmp_path,group,native_target,writer_factory,monkeypatch,mode):
    world,values,pack=inputs(tmp_path,group,native_target,monkeypatch,mode)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected external runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*a,**kw):
            if path.name=='probe_log.jsonl' and a and a[0]=='w':raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['OBS-02']},values={'OBS-02':values})
    for name,value in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nobservation_identity="kubernetes"\n[adapter]\nname="vllm"\n[observation_identity]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(tmp_path/'prepared/manifest.json'))+'\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(argv):
        result=runner.invoke(app,list(map(str,argv)));assert result.exit_code==0,(result.output,result.exception)
        assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    cli(['run','--plan',plan.id,'--pack',pack.path,*common]);cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common])
    cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');evd,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert evd.data_origin.value=='EXAMPLE' and evd.normal_control.succeeded is None
    assert all(getattr(res.verdicts,key).value=='UNKNOWN' for key in ('security','functional','performance'))
    assert all(evd.observations[key].value is False for key in contract.TRUTH_FIELDS)
    candidates=evd.observations['identity_native_models'].value
    weights=[weight for row in candidates for model in row['configured_model_candidates'] for weight in model.get('weight_iteration_candidates',[])]
    if mode in {'normal','partial','late-callback','later-record-error','attachment'}:
        assert any(row['same_process_iteration_observed'] and row['parameter_callback_returns_observed']>0 for row in weights)
    elif mode=='no-iteration':assert not any(row['same_process_iteration_observed'] for row in weights)
    else:assert weights==[]
    assert all(not row['tensor_storage_applied_verified'] and not row['worker_assignment_verified'] for row in weights)
    refs=sorted((obj.kind.value,obj.id,obj.revision,obj.content_sha256) for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for obj in store.iter_kind(kind));assert len(refs)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'evidence.zip'])
    cli(['evidence','restore','--bundle',tmp_path/'evidence.zip','--destination',tmp_path/'restored'])
    restored_common=['--store',tmp_path/'restored','--config',cfg]
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*restored_common]);cli(['verify',*restored_common])
    restored=Store.read_only(tmp_path/'restored')
    assert refs==sorted((obj.kind.value,obj.id,obj.revision,obj.content_sha256) for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for obj in restored.iter_kind(kind))
