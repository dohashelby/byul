"""Admitted native17 packet correlation; local synthetic reads are EXAMPLE only."""
from copy import deepcopy
import json,os
import pytest
from guard.adapters import vllm_file_read_sensor as sensor
from guard.collectors.model_read_scope import deploy_scope
from guard.collectors.native_model_file_reads import analyze,TRUTH
from guard.models import canonical_json
from tests.test_a1_file_read_sensor import make_world,prepared_for,scope_value,MODEL_BYTES
from tests.test_a1_weight_iteration_sensor import packet,PRIVATE


def fixture(tmp_path,patch,mode='normal',*,scope_change=None):
    path=tmp_path/'public-example.safetensors';path.write_bytes(MODEL_BYTES)
    scope=scope_value(path)
    if scope_change:scope_change(scope)
    scope_path=tmp_path/'scope.json';scope_path.write_bytes(deploy_scope(scope,canonical_json(scope),'b'*32))
    world=make_world(tmp_path,patch,path=path,scope_path=scope_path,mode=mode)
    if mode=='open-failure':
        path.unlink()
        with pytest.raises(FileNotFoundError):world.run_loader()
    elif mode in {'decoder-error','late-callback'}:
        with pytest.raises(OSError,match=PRIVATE):world.run_loader()
    elif mode=='unbound-load':list(world.load())
    else:world.run_loader()
    data=packet(world);data['recordings'][0]['capture']['records'][0]['payload']['profile']=sensor.PROFILE
    return data,prepared_for(world),world


def test_same_loader_model_read_joins_without_authority_or_tensor_truth(tmp_path,monkeypatch):
    data,prepared,world=fixture(tmp_path,monkeypatch);before=deepcopy((data,prepared))
    result=analyze(data,prepared);assert (data,prepared)==before
    candidate,=result['candidates'];group,=result['groups']
    assert candidate['full_fd_read_return_observed'] and candidate['declared_reference_bytes_match_observed']
    assert candidate['read_return_bytes']==len(MODEL_BYTES) and candidate['close_observed']
    assert candidate['path_fd_metadata_match_observed']==(os.name!='nt')
    assert group['paths_with_read_candidate_count']==group['declared_path_count']==1
    assert all(candidate[k] is False and result[k] is False and group[k] is False for k in TRUTH)
    assert result['security']=='UNKNOWN'
    text=json.dumps(result);assert str(world.path) not in text and PRIVATE not in text and MODEL_BYTES.decode() not in text


@pytest.mark.parametrize('mode',['decoder-error','late-callback'])
def test_read_survives_later_decoder_or_parameter_callback_error(tmp_path,monkeypatch,mode):
    data,prepared,_=fixture(tmp_path,monkeypatch,mode)
    result=analyze(data,prepared);candidate,=result['candidates']
    assert candidate['declared_reference_bytes_match_observed'] and not candidate['root_exhausted_observed']
    assert result['tensor_storage_applied_verified'] is False and result['decoder_completion_verified'] is False


@pytest.mark.parametrize('hashing,cap,expected',[(False,sensor.MAX_HASH_BYTES,'disabled'),(True,1,'too-large')])
def test_disabled_or_bounded_hash_is_not_declared_reference_mismatch(tmp_path,monkeypatch,hashing,cap,expected):
    data,prepared,_=fixture(tmp_path,monkeypatch,scope_change=lambda s:s.update(hash_model_bytes=hashing,max_hash_bytes=cap))
    candidate,=analyze(data,prepared)['candidates']
    assert candidate['hash_status']==expected and candidate['read_return_bytes_sha256'] is None
    assert not candidate['declared_reference_comparison_observed'] and not candidate['declared_reference_mismatch_observed']


def test_declared_reference_mismatch_is_a_fact_not_security_or_approval(tmp_path,monkeypatch):
    def change(scope):scope['files'][0]['reference_sha256']='0'*64
    data,prepared,_=fixture(tmp_path,monkeypatch,scope_change=change)
    result=analyze(data,prepared);candidate,=result['candidates']
    assert candidate['declared_reference_comparison_observed'] and candidate['declared_reference_mismatch_observed']
    assert not candidate['declared_reference_bytes_match_observed'] and result['security']=='UNKNOWN'
    assert all(result[k] is False for k in TRUTH)


def test_replacement_preserves_read_but_unstable_fd_is_not_complete_reference_comparison(tmp_path,monkeypatch):
    data,prepared,_=fixture(tmp_path,monkeypatch,'replace');candidate,=analyze(data,prepared)['candidates']
    assert candidate['read_return_bytes']==len(MODEL_BYTES)
    assert not candidate['fd_metadata_stable_observed'] and not candidate['full_fd_read_return_observed']
    assert not candidate['declared_reference_mismatch_observed'] and not candidate['path_fd_metadata_match_observed']


@pytest.mark.parametrize('damage',['binding','loader','model','root-source','cross-recording','missing-step','unbound-load'])
def test_unjoined_read_locators_remain_when_attribution_is_missing(tmp_path,monkeypatch,damage):
    data,prepared,_=fixture(tmp_path,monkeypatch,'unbound-load' if damage=='unbound-load' else 'normal')
    rows=data['recordings'][0]['capture']['records']
    if damage=='binding':data['recordings'][0]['capture']['process_context_verified']=False
    elif damage in {'loader','model'}:
        for row in rows:
            if row['event']=='FILE_READ':row['payload']['loader_call_sha256' if damage=='loader' else 'model_object_sha256']='9'*64
    elif damage=='root-source':
        for row in rows:
            if row['event']=='WEIGHT_ITERATION' and row['payload']['point']=='AutoWeightsLoader._load_module' and row['payload']['phase']=='FACTORY':row['payload']['source_verified']=False
    elif damage=='cross-recording':
        other=deepcopy(data['recordings'][0]);other['capture']['records']=[r for r in other['capture']['records'] if r['event'] in {'START','FILE_READ'}]
        data['recordings'][0]['capture']['records']=[r for r in rows if r['event']!='FILE_READ'];data['recordings'].append(other)
    elif damage=='missing-step':data['recordings'][0]['capture']['records']=[r for r in rows if r['event']!='WEIGHT_ITERATION']
    result=analyze(data,prepared)
    assert result['candidates']==[] and result['groups']==[] and result['unjoined_read_count']==1
    assert result['unjoined_reads'][0]['phase']=='READ_RETURN' and result['security']=='UNKNOWN'


def test_partial_close_and_recording_keep_known_read_and_coverage_separate(tmp_path,monkeypatch):
    data,prepared,_=fixture(tmp_path,monkeypatch);capture=data['recordings'][0]['capture']
    capture['records']=[r for r in capture['records'] if not (r['event']=='FILE_READ' and r['payload']['phase']=='CLOSE_RETURN')]
    capture['summary']['recording_closed']=False
    candidate,=analyze(data,prepared)['candidates']
    assert candidate['declared_reference_bytes_match_observed'] and not candidate['close_observed'] and not candidate['recording_closed']
    assert len(candidate['source_events'])==2 and candidate['scope_complete'] is False


def test_failed_other_recording_preserves_original_source_indices(tmp_path,monkeypatch):
    data,prepared,_=fixture(tmp_path,monkeypatch)
    data['recordings'].insert(0,{'capture':None,'session_owner_match':False,'listed_file_identity_match':False})
    result=analyze(data,prepared);candidate,=result['candidates']
    assert candidate['recording']==1 and candidate['root_event'][0]==1 and all(event[0]==1 for event in candidate['source_events'])
    assert 'model_file_recording_capture_unavailable' in result['missing']


def test_additional_declared_path_is_an_unknown_candidate_gap(tmp_path,monkeypatch):
    def change(scope):scope['files'].append({'normalized_path_sha256':'1'*64,'reference_sha256':'2'*64,'bytes':3})
    data,prepared,_=fixture(tmp_path,monkeypatch,scope_change=change);group,=analyze(data,prepared)['groups']
    assert group['declared_path_count']==2 and group['paths_with_read_candidate_count']==1 and group['declared_paths_without_candidate_sha256']==['1'*64]
    assert group['full_inventory_verified'] is False and group['scope_complete'] is False


@pytest.mark.parametrize('bounded',[False,True])
def test_same_model_reloaded_keeps_distinct_roots_handles_and_explicit_ambiguity(tmp_path,monkeypatch,bounded):
    from guard.collectors import native_model_file_reads as consumer
    _,prepared,world=fixture(tmp_path,monkeypatch);world.run_loader()
    data=packet(world);data['recordings'][0]['capture']['records'][0]['payload']['profile']=sensor.PROFILE
    if bounded:monkeypatch.setattr(consumer,'MAX_CANDIDATES',1)
    result=analyze(data,prepared)
    assert len(result['candidates'])==len(result['groups'])==(1 if bounded else 2)
    assert all(c['observed_loader_root_count']==2 and c['multiple_loader_roots_observed'] for c in result['candidates'])
    assert all(g['paths_with_read_candidate_count']==1 and not g['actual_model_generation_verified'] for g in result['groups'])
    if bounded:assert 'model_file_candidate_output_bound' in result['missing']
    else:
        assert len({c['handle_sha256'] for c in result['candidates']})==2
        assert len({c['root_load_sha256'] for c in result['candidates']})==2
        assert len({c['loader_call_sha256'] for c in result['candidates']})==2


@pytest.mark.parametrize('mode',['open-failure','normal-C'])
def test_open_error_or_unobserved_C_backend_cannot_create_read_candidate(tmp_path,monkeypatch,mode):
    data,prepared,_=fixture(tmp_path,monkeypatch,mode);result=analyze(data,prepared)
    assert not result['candidates'] and result['security']=='UNKNOWN'
    assert result['open_failure_count']==(mode=='open-failure')
    assert 'ordinary_C_safe_open_read_and_mmap_unobserved' in result['missing']


@pytest.mark.parametrize('damage',['profile','scope','source','raw','duplicate','unavailable-bound','hash-consent'])
def test_invalid_or_promoted_input_is_sanitized(tmp_path,monkeypatch,damage):
    data,prepared,_=fixture(tmp_path,monkeypatch);rows=data['recordings'][0]['capture']['records']
    if damage=='profile':rows[0]['payload']['profile']='vllm-native-lifecycle-metadata/16'
    elif damage=='scope':next(r for r in rows if r['event']=='FILE_READ')['payload']['scope_sha256']='0'*64
    elif damage=='source':next(r for r in rows if r['event']=='FILE_READ')['payload']['source_sha256']='0'*64
    elif damage=='raw':next(r for r in rows if r['event']=='FILE_READ')['payload']['raw_bytes']=PRIVATE
    elif damage=='duplicate':rows.append(deepcopy(rows[-1]))
    elif damage=='unavailable-bound':data['recordings'].append({'capture':None,'session_owner_match':True,'listed_file_identity_match':False})
    else:prepared['model_read_scope']['nonsecret_model_weights']=False
    with pytest.raises(ValueError,match='NATIVE_MODEL_FILE_READ_INPUT_INVALID') as error:analyze(data,prepared)
    assert PRIVATE not in str(error.value)


def test_common_source_pin_error_is_sanitized(tmp_path,monkeypatch):
    from guard.collectors import native_model_file_reads as consumer
    data,prepared,_=fixture(tmp_path,monkeypatch)
    monkeypatch.setitem(consumer.SOURCE_PINS,'vllm_file_read_metadata.py','0'*64)
    with pytest.raises(ValueError,match='NATIVE_MODEL_FILE_READ_COMMON_SOURCE_MISMATCH'):analyze(data,prepared)
