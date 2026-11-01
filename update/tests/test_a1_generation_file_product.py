"""Exact20/21/22 -> existing OBS02 select/run/judge/report/pack Ref replay."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from guard.adapters import vllm_worker_startup_sensor as startup
from guard.adapters import vllm_safe_open_mapping_sensor as mapping
from guard.models import sha256_hex
from guard.store import Store
from tests import test_a1_worker_startup_product as old
from tests.test_a1_observation_identity_product import World as IdentityWorld
from tests.test_a1_ready_endpoint_sensor import rebase_rows
from tests.test_a1_generation_file_metadata import FACTORIES
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_sensor import writer_factory
from tests.test_lifecycle_capture import case,external,revised

ORIGINAL_INPUTS=old.inputs


class World(IdentityWorld):
    def add_native(self,rid):
        super().add_native(rid)
        if self.weight_inserted:
            if self.weight_mode!='wrong-model':self.bind_forward()
            return
        self.weight_inserted=True
        def insert(rows):
            index=next(i for i,row in enumerate(rows) if row['event']=='NATIVE' and row['payload']['point']=='BaseModelLoader.load_model' and row['payload']['phase']=='RETURN')
            values=[{**deepcopy(row),'seq':number+1} for number,row in enumerate(self.weight_events) if row['event']!='END']
            start=rows[index-1]['monotonic_ns']+1;offset=start-values[0]['monotonic_ns']
            # Explicit synthetic PID/time/seq binding only; never an actual worker measurement.
            for row in values:
                row['monotonic_ns']+=offset;payload=row['payload']
                for key in ('operation_started_ns','operation_finished_ns','call_started_ns','observation_started_ns','observation_finished_ns'):
                    if key in payload and payload[key] is not None:payload[key]+=offset
            values=rebase_rows(values,rows[0]['payload']['context'],start_index=index)
            extra=values[-1]['monotonic_ns']-start+1
            for row in rows[index:]:
                row['monotonic_ns']+=extra
                if self.weight_mode!='wrong-model' and row['event']=='NATIVE' and row['payload']['point']=='BaseModelLoader.load_model':
                    row['payload']['metadata']['model_selection']['returned_model_sha256']=self.weight_model
            rows[index:index]=values
        self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],insert)
        if self.weight_mode!='wrong-model':self.bind_forward()

    def bind_forward(self):
        def bind(rows):
            for row in rows:
                if row['event']=='NATIVE' and row['payload']['point']=='GPUModelRunner._model_forward':
                    row['payload']['metadata']['model_selection']['owner_model_sha256']=self.weight_model
        self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],bind)


def inputs(tmp_path,group,target,patch,mode,version):
    factory=FACTORIES[version];actual=factory.sensor
    class Profile:
        PROFILE=actual.PROFILE;SCHEMA=actual.SCHEMA
        def __getattr__(self,key):return getattr(startup,key)
    def make(*args,**kwargs):
        kwargs.pop('otel_available',None)
        world=factory.make_world(*args,**kwargs)
        if mode=='partial':
            def early_close(values):
                iterator=world.auto._load_module('',world.model,values);assert next(iterator)=='weight';iterator.close();return {'weight'}
            world.model.load_weights=early_close
        # Keep actual code admission before the declared EXAMPLE library facade.
        patch.setattr(mapping,'library_identity',lambda original,cache:True)
        return world
    with patch.context() as local:
        local.setattr(old,'sensor',Profile());local.setattr(old,'make_world',make);local.setattr(old,'World',World)
        return ORIGINAL_INPUTS(tmp_path,group,target,local,'normal' if mode=='partial' else mode)


@pytest.mark.parametrize('version',[20,21,22])
@pytest.mark.parametrize('mode',['normal','partial','later-record-error','attachment','wrong-model'])
def test_registered_OBS02_exact_profile_generation_files_and_Refs(tmp_path,group,native_target,writer_factory,monkeypatch,version,mode):
    def selected(path,grouping,target,patch,unused):return inputs(path,grouping,target,patch,mode,version)
    monkeypatch.setattr(old,'inputs',selected)
    old.test_existing_identity_cli_profile19_facts_and_exact_ref_roundtrip(tmp_path,group,native_target,writer_factory,monkeypatch,'normal' if mode=='partial' else mode)
    store=Store.read_only(tmp_path/'store');evd,=store.iter_kind('EVD')
    models=[model for trial in evd.observations['identity_native_models'].value for model in trial['configured_model_candidates']]
    joined=[row for model in models for row in model.get('generation_file_context_candidates',[])]
    if mode=='wrong-model':assert joined==[]
    else:
        assert joined and all(row['recording_profile'].endswith('/'+str(version)) for row in joined)
        assert all(row['weight_root']['last_source_event'][1]<model['native_locator']['forward_seq']
            for model in models for row in model.get('generation_file_context_candidates',[]))
    if mode=='partial':assert any(not row['weight_root']['root_exhausted_observed'] for row in joined)
    assert all(not model['actual_tensor_consumption_verified'] for model in models)
    objects=[obj for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for obj in store.iter_kind(kind)]
    assert len(objects)==6
    receipt={'case':mode,'native_profile':version,'capture_origin':'EXAMPLE','EXAMPLE_library_identity_substitute':True,
        'source_code_parser_or_gate_substituted':False,'generation_file_candidates':len(joined),
        'actual_serving_worker_GPU_authority_tensor':False,'exact_refs':[obj.ref().model_dump(mode='json') for obj in objects],
        'original_export_sha256':sha256_hex((tmp_path/'evidence.zip').read_bytes()),'saved_pack_rejudge_equal':True}
    (tmp_path/'a1-generation-file-product-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
