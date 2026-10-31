"""Immutable682 owner delivery: real19 source rejection, common facts retained."""
from copy import deepcopy
from pathlib import Path
import hashlib,json
import pytest
from guard.collectors import native_worker_model_files as dedicated
from guard.adapters import vllm_worker_startup_metadata as common
from guard.adapters import vllm_safe_open_mapping_sensor as mapping
from guard.store import Store
from tests.test_native_worker_model_files import fixture
from tests import test_a1_worker_startup_product as product
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_sensor import writer_factory
from tests.test_lifecycle_capture import case,external


def test_stale_native19_owner_literal_rejects_valid_packet_without_promoting_current_source(tmp_path,monkeypatch):
    packet,prepared,_=fixture(tmp_path,monkeypatch);before=deepcopy((packet,prepared))
    assert dedicated.SOURCE_PINS['vllm_worker_startup_metadata.py']=='d122b34e03da52f78f425bca84660b302cac062a889dfecccf8c5393091c25cc'
    assert hashlib.sha256(Path(common.__file__).read_bytes()).hexdigest()=='b5d0be2de94fe9453a5f68b58e019b62a2f454bd59718fb43512a321755e651a'
    with pytest.raises(ValueError,match='^NATIVE_WORKER_FILE_COMMON_SOURCE_MISMATCH$'):dedicated.analyze(packet,prepared)
    assert (packet,prepared)==before
    facts=common.project(packet,prepared)
    assert facts['model_candidates'] and facts['spans']
    assert all(facts[key] is False for key in common.sensor.TRUTH)


@pytest.mark.parametrize('mode',['normal','later-record-error','attachment'])
def test_existing_OBS02_native19_keeps_common_facts_and_exact_refs_on_stale_owner_pin(tmp_path,group,native_target,writer_factory,monkeypatch,mode):
    make=product.make_world;analyze=dedicated.analyze;rejected=[]
    def example_world(*args,**kwargs):
        world=make(*args,**kwargs)
        monkeypatch.setattr(mapping,'library_identity',lambda original,cache:True)
        return world
    def observed(packet,prepared):
        try:return analyze(packet,prepared)
        except ValueError as error:
            assert str(error)=='NATIVE_WORKER_FILE_COMMON_SOURCE_MISMATCH'
            rejected.append('NATIVE_WORKER_FILE_COMMON_SOURCE_MISMATCH');raise
    monkeypatch.setattr(product,'make_world',example_world);monkeypatch.setattr(dedicated,'analyze',observed)
    product.test_existing_identity_cli_profile19_facts_and_exact_ref_roundtrip(tmp_path,group,native_target,writer_factory,monkeypatch,mode)
    assert rejected
    store=Store.read_only(tmp_path/'store');evd,=store.iter_kind('EVD')
    models=[model for trial in evd.observations['identity_native_models'].value for model in trial['configured_model_candidates']]
    assert any(model.get('worker_startup_candidates') for model in models)
    assert any(model.get('weight_iteration_candidates') for model in models)
    assert any(model.get('safe_open_mapping_candidates') for model in models)
    assert all(model.get('scoped_startup_file_context_candidates',[])==[] for model in models)
    assert evd.execution.status.value=='ABORTED'
    objects=[obj for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for obj in store.iter_kind(kind)]
    assert len(objects)==6
    (tmp_path/'a1-ABI-stale19-product-receipt.json').write_text(json.dumps({'case':mode,'source_pin_rejection':rejected,
        'capture_origin':'EXAMPLE','EXAMPLE_library_identity_substitute':True,'source_gate_or_parser_substituted':False,
        'actual_worker_GPU_authority_tensor':False,'exact_refs':[obj.ref().model_dump(mode='json') for obj in objects],
        'original_export_sha256':hashlib.sha256((tmp_path/'evidence.zip').read_bytes()).hexdigest(),'saved_pack_rejudge_exact':True},indent=2)+'\n')
