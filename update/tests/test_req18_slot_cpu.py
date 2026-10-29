"""EXAMPLE capture failure preservation and strict unopened component gate."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import pytest
from tools import b_req18_slot_cpu as collector
from guard.adapters import vllm_slot_mask as native


def row(case):
    return {'case_id':case,'collected':True,'current_b_unrestricted':True,'slot_reused':case!='standalone-b',
        'constraint_sha256':native.digest(None),'logits_sha256':'a'*64,'host_mask_status':'absent' if case=='standalone-b' else 'collected',
        'metadata_mask_status':'absent' if case=='standalone-b' else 'collected','metadata_mask_present':case!='standalone-b',
        'host_mask_sha256':None if case=='standalone-b' else 'b'*64,'host_masked_count':None if case=='standalone-b' else 0,
        'metadata_mask_sha256':None if case=='standalone-b' else 'b'*64,'metadata_masked_count':None if case=='standalone-b' else 0,
        'sampling_metadata_token':'c'*64,'sampling_mask_token':None if case=='standalone-b' else 'd'*64,
        'sampled_count':1,'sampled_ids_sha256':'e'*64,'standalone_output_sha256':'e'*64,'standalone_mask_sha256':None,
        'effective_unrestricted':True,'effective_constraint_matches_standalone':True,'mask_matches_standalone':True,
        'mask_comparison_basis':'effective_unrestricted_branches','physical_mask_hash_matches_standalone':None,
        'output_matches_standalone':True,'sampler_returned':True,'metadata_argument_matches_refresh':True,
        'restricted_request_preserved':True,'source_invariant_mismatch':False,
        'source_validation':{'pre':'verified','post':{'status':'verified'},'boundary':'source_component'}}


@pytest.fixture
def example_boundary(monkeypatch):
    from guard.executors import structured_tools_cpu
    monkeypatch.setattr(collector,'verify_component',lambda:None)
    monkeypatch.setattr(structured_tools_cpu,'_verify_source',lambda *a,**kw:None)
    monkeypatch.setattr(structured_tools_cpu,'_validate_target031_dependencies',lambda:None)
    monkeypatch.setattr(collector,'_post',lambda *a:{'status':'verified'})
    def run(root,record):
        record['data_origin']='EXAMPLE'
        record['results'].extend(row(case) for case in ('standalone-b','condense-reuse','swap'))
        record['followup']={'collected':True,'matches_standalone':True,'sampled_ids_sha256':'e'*64}
    monkeypatch.setattr(collector,'_run_actual',run)


def test_component_pin_is_explicit_and_unconfigured_never_discovers_head(monkeypatch):
    monkeypatch.setattr(collector,'PRODUCT_COMPONENT_COMMIT',None)
    with pytest.raises(ValueError,match='COMPONENT_PIN_UNCONFIGURED'):collector.verify_component()


def test_capture_preserves_absence_and_collection_identity(tmp_path,example_boundary):
    record=collector.capture_slot_cpu(tmp_path/'source',tmp_path/'work')
    assert record['capture_status']=='completed' and record['data_origin']=='EXAMPLE'
    assert len(record['results'])==3 and record['results'][0]['host_mask_sha256'] is None
    assert record['results'][0]['metadata_mask_present'] is False
    assert record['results'][1]['host_masked_count']==0
    assert record['fixture_sha256']==native.digest(collector.FIXTURE)
    assert datetime.fromisoformat(record['capture_started_at'])<=datetime.fromisoformat(record['capture_finished_at'])
    assert record['gpu_consumption_verified'] is False and record['kubernetes_verified'] is False


def test_later_trial_failure_does_not_discard_collected_independent_mismatch(tmp_path,example_boundary,monkeypatch):
    def broken(root,record):
        record['data_origin']='EXAMPLE';value=row('condense-reuse');value.update(source_invariant_mismatch=True,output_matches_standalone=False)
        record['results'].append(value)
        raise OSError('PRIVATE_LATER_TRIAL_ERROR')
    monkeypatch.setattr(collector,'_run_actual',broken)
    record=collector.capture_slot_cpu(tmp_path/'source',tmp_path/'work')
    assert record['capture_status']=='incomplete' and record['results'][0]['source_invariant_mismatch'] is True
    assert record['error_type']=='OSError' and 'PRIVATE_' not in json.dumps(record)


def test_final_source_failure_keeps_verified_trial_facts(tmp_path,example_boundary,monkeypatch):
    monkeypatch.setattr(collector,'_post',lambda *a:{'status':'unavailable','error_type':'OSError'})
    record=collector.capture_slot_cpu(tmp_path/'source',tmp_path/'work')
    assert record['capture_status']=='incomplete' and len(record['results'])==3
    assert record['source_validation']['post']['status']=='unavailable'


def test_auxiliary_write_failure_keeps_all_trial_observations(tmp_path,example_boundary,monkeypatch):
    original=Path.open
    def failed(path,*a,**kw):
        if path==tmp_path/'work/result.json':raise OSError('PRIVATE_AUX_ERROR')
        return original(path,*a,**kw)
    monkeypatch.setattr(Path,'open',failed)
    record=collector.capture_slot_cpu(tmp_path/'source',tmp_path/'work')
    assert len(record['results'])==3 and record['capture_status']=='incomplete'
    assert record['auxiliary_write']=={'status':'failed','error_type':'OSError'}
    assert 'PRIVATE_' not in json.dumps(record)


def test_wrong_source_profile_never_reuses_reference_defaults(tmp_path):
    with pytest.raises(ValueError,match='PROFILE_INVALID'):
        collector.capture_slot_cpu(tmp_path,tmp_path/'work',source_profile='reference5d')
    assert not (tmp_path/'work').exists()



def test_trial_source_unknown_remains_incomplete_even_if_final_check_recovers(tmp_path,example_boundary,monkeypatch):
    def run(root,record):
        record['data_origin']='EXAMPLE';record['results'].extend(row(c) for c in ('standalone-b','condense-reuse','swap'))
        record['results'][1]['source_validation']['post']={'status':'unavailable','error_type':'OSError'}
        record['results'][1]['source_invariant_mismatch']=True
        record['followup']={'matches_standalone':True}
    monkeypatch.setattr(collector,'_run_actual',run)
    record=collector.capture_slot_cpu(tmp_path/'source',tmp_path/'work')
    assert record['source_validation']['post']=={'status':'verified'}
    assert record['capture_status']=='incomplete' and record['results'][1]['source_invariant_mismatch'] is True


@pytest.mark.parametrize('collected',[False,None])
def test_followup_same_output_without_collected_mask_is_incomplete_and_keeps_mismatch(tmp_path,example_boundary,monkeypatch,collected):
    def run(root,record):
        record['data_origin']='EXAMPLE';record['results'].extend(row(c) for c in ('standalone-b','condense-reuse','swap'))
        record['results'][1].update(source_invariant_mismatch=True,output_matches_standalone=False)
        record['followup']={'matches_standalone':True,'sampled_ids_sha256':'e'*64,
            'host_mask_status':'unavailable','metadata_mask_status':'unavailable'}
        if collected is not None:record['followup']['collected']=collected
    monkeypatch.setattr(collector,'_run_actual',run)
    record=collector.capture_slot_cpu(tmp_path/'source',tmp_path/'work')
    assert record['capture_status']=='incomplete'
    assert record['results'][1]['source_invariant_mismatch'] is True
    assert record['followup']['sampled_ids_sha256']==record['results'][0]['sampled_ids_sha256']
