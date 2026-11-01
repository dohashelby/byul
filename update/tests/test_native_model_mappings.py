"""Exact18 candidate joins. C gate is explicitly EXAMPLE; no native C/worker execution."""
from copy import deepcopy
import json
import pytest
from guard.adapters import vllm_safe_open_mapping_sensor as sensor
from guard.collectors import native_model_mappings as consumer
from tests.test_a1_safe_open_mapping_sensor import make_world,prepared_for
from tests.test_a1_weight_iteration_sensor import packet,PRIVATE
from tests.test_a1_file_read_sensor import MODEL_BYTES


def fixture(tmp_path,patch,mode='normal'):
    world=make_world(tmp_path,patch,mode=mode)
    try:
        if mode in {'enter-error','tensor-error','exit-error','late-callback'}:
            with pytest.raises(OSError,match=PRIVATE):world.run_loader()
        elif mode=='unbound':list(world.load())
        else:world.run_loader()
        data=packet(world);data['recordings'][0]['capture']['records'][0]['payload']['profile']=sensor.PROFILE
        return data,prepared_for(world),world
    finally:world.cleanup()


def test_context_file_root_candidates_retain_locators_and_do_not_mutate_inputs(tmp_path,monkeypatch):
    data,prepared,world=fixture(tmp_path,monkeypatch);before=deepcopy((data,prepared))
    result=consumer.analyze(data,prepared);assert (data,prepared)==before
    candidate,=result['candidates'];group,=result['groups']
    assert candidate['same_process_safe_open_context_observed'] and candidate['constructor_return_observed']
    assert candidate['enter_return_observed'] and candidate['tensor_boundary_return_observed'] and candidate['context_exit_return_observed']
    assert candidate['first_source_event']==candidate['source_events'][0] and candidate['last_source_event']==candidate['source_events'][-1]
    assert candidate['root_event'][1]<candidate['first_source_event'][1]
    assert candidate['source_event_count']==len(candidate['source_events']) and candidate['recording_closed']
    assert group['paths_with_safe_open_context_candidate_count']==group['declared_path_count']==1
    assert all(row[k] is False for row in (result,candidate,group) for k in consumer.TRUTH)
    assert result['security']=='UNKNOWN'
    text=json.dumps(result);assert PRIVATE not in text and str(world.path) not in text and MODEL_BYTES.decode() not in text


@pytest.mark.parametrize('mode',['enter-error','tensor-error','exit-error','late-callback'])
def test_later_original_errors_keep_prior_context_facts_without_read_or_release_claim(tmp_path,monkeypatch,mode):
    data,prepared,_=fixture(tmp_path,monkeypatch,mode);candidate,=consumer.analyze(data,prepared)['candidates']
    assert candidate['constructor_return_observed']
    if mode!='late-callback':assert candidate['context_exception_observed']
    assert candidate['enter_return_observed']==(mode!='enter-error')
    assert candidate['tensor_boundary_return_observed']==(mode in {'exit-error','late-callback'})
    assert candidate['mapping_release_verified'] is False and candidate['tensor_storage_applied_verified'] is False


def test_library_unavailable_keeps_locator_but_cannot_create_joined_context(tmp_path,monkeypatch):
    data,prepared,_=fixture(tmp_path,monkeypatch,'library-unavailable');result=consumer.analyze(data,prepared)
    assert result['candidates']==[] and result['groups']==[] and result['unjoined_event_count']==1
    assert result['unjoined_events'][0]['phase']=='LIBRARY_UNAVAILABLE'


@pytest.mark.parametrize('damage',['binding','loader','model','root-source','cross-recording','missing-step','unbound'])
def test_attribution_gaps_keep_unjoined_locators_instead_of_cross_generation_join(tmp_path,monkeypatch,damage):
    data,prepared,_=fixture(tmp_path,monkeypatch,'unbound' if damage=='unbound' else 'normal')
    rows=data['recordings'][0]['capture']['records']
    if damage=='binding':data['recordings'][0]['capture']['process_context_verified']=False
    elif damage in {'loader','model'}:
        for row in rows:
            if row['event']=='SAFE_OPEN_MAPPING':row['payload']['loader_call_sha256' if damage=='loader' else 'model_object_sha256']='9'*64
    elif damage=='root-source':
        for row in rows:
            if row['event']=='WEIGHT_ITERATION' and row['payload']['phase']=='FACTORY' and row['payload']['point']=='AutoWeightsLoader._load_module':row['payload']['source_verified']=False
    elif damage=='cross-recording':
        other=deepcopy(data['recordings'][0]);other['capture']['records']=[r for r in rows if r['event'] in {'START','SAFE_OPEN_MAPPING'}]
        data['recordings'][0]['capture']['records']=[r for r in rows if r['event']!='SAFE_OPEN_MAPPING'];data['recordings'].append(other)
    elif damage=='missing-step':data['recordings'][0]['capture']['records']=[r for r in rows if r['event']!='WEIGHT_ITERATION']
    result=consumer.analyze(data,prepared)
    assert result['candidates']==[] and result['unjoined_event_count']>0 and result['unjoined_events']
    assert all(row[k] is False for row in result['groups'] for k in consumer.TRUTH)


def test_missing_other_recording_does_not_renumber_or_erase_valid_observations(tmp_path,monkeypatch):
    data,prepared,_=fixture(tmp_path,monkeypatch)
    data['recordings'].insert(0,{'capture':None,'session_owner_match':False,'listed_file_identity_match':False})
    result=consumer.analyze(data,prepared);candidate,=result['candidates']
    assert candidate['recording']==candidate['root_event'][0]==candidate['last_source_event'][0]==1
    assert all(event[0]==1 for event in candidate['source_events'])
    assert 'mapping_recording_capture_unavailable' in result['missing']


def test_partial_capture_and_missing_exit_keep_valid_tensor_boundary(tmp_path,monkeypatch):
    data,prepared,_=fixture(tmp_path,monkeypatch);capture=data['recordings'][0]['capture']
    capture['records']=[r for r in capture['records'] if not (r['event']=='SAFE_OPEN_MAPPING' and r['payload']['phase']=='EXIT_RETURN')]
    capture['summary']['recording_closed']=False
    result=consumer.analyze(data,prepared);candidate,=result['candidates']
    assert candidate['tensor_boundary_return_observed'] and not candidate['context_exit_return_observed'] and not candidate['recording_closed']
    assert 'safe_open_exit_unavailable' in result['missing']


@pytest.mark.parametrize('bounded',[False,True])
def test_reloaded_same_model_never_merges_contexts_roots_or_loader_calls(tmp_path,monkeypatch,bounded):
    data,prepared,world=fixture(tmp_path,monkeypatch);world.run_loader();world.cleanup()
    data=packet(world);data['recordings'][0]['capture']['records'][0]['payload']['profile']=sensor.PROFILE
    if bounded:monkeypatch.setattr(consumer,'MAX_CANDIDATES',1)
    result=consumer.analyze(data,prepared);candidates=result['candidates']
    assert len(candidates)==len(result['groups'])==(1 if bounded else 2)
    assert all(c['observed_loader_root_count']==2 for c in candidates)
    assert result['candidate_context_count']==2 and result['candidate_context_locators_complete'] is (not bounded)
    if bounded:assert 'mapping_candidate_output_bound' in result['missing']
    else:
        for key in ('context_sha256','root_load_sha256','loader_call_sha256'):assert len({c[key] for c in candidates})==2


def test_same_opaque_tokens_in_different_recording_process_generations_are_separate_candidates(tmp_path,monkeypatch):
    data,prepared,_=fixture(tmp_path,monkeypatch);other=deepcopy(data['recordings'][0])
    other['capture']['records'][0]['payload']['context'].update(pid=43,start_ticks=100)
    data['recordings'].append(other);result=consumer.analyze(data,prepared)
    assert len(result['candidates'])==len(result['groups'])==2
    assert {(c['recording'],c['pid'],c['start_ticks']) for c in result['candidates']}=={(0,42,99),(1,43,100)}
    assert all(c['root_event'][0]==c['last_source_event'][0]==c['recording'] for c in result['candidates'])


def test_mapping_coordinates_are_bounded_nonatomic_hints_even_after_context_exit(tmp_path,monkeypatch):
    data,prepared,_=fixture(tmp_path,monkeypatch)
    sample={'region_sha256':'1'*64,'device_sha256':'2'*64,'inode_sha256':'3'*64,'file_offset':0,'mapped_bytes':4096,'permissions':'r--p','deleted':True}
    for row in data['recordings'][0]['capture']['records']:
        if row['event']=='SAFE_OPEN_MAPPING':
            for side in ('before','after'):row['payload'][side]={'status':'bounded','complete':False,'non_atomic':True,'matches':[deepcopy(sample)]}
            row['payload']['path_identity_unchanged_observed']=False
    monkeypatch.setattr(consumer,'MAX_COORDINATES',1);monkeypatch.setattr(consumer,'MAX_EVENTS',1)
    result=consumer.analyze(data,prepared);candidate,=result['candidates']
    assert candidate['mapping_coordinates_observed'] and candidate['context_exit_return_observed']
    assert candidate['mapping_coordinate_observation_count']>1 and len(candidate['mapping_coordinates'])==1
    assert not candidate['mapping_coordinate_locators_complete'] and not candidate['source_event_locators_complete']
    assert candidate['last_source_event'][1]>candidate['source_events'][-1][1]
    assert not candidate['snapshots_complete_observed'] and not candidate['all_path_identity_checks_unchanged_observed']
    assert all(candidate[k] is False for k in consumer.TRUTH)
    assert {'mapping_coordinate_output_bound','mapping_context_event_output_bound','mapping_path_identity_change_observed'}<=set(result['missing'])


def test_additional_declared_path_is_a_context_coverage_gap_not_missing_consumption_proof(tmp_path,monkeypatch):
    data,prepared,_=fixture(tmp_path,monkeypatch);prepared['model_read_scope']['files'].append({'normalized_path_sha256':'1'*64,'reference_sha256':'2'*64,'bytes':3})
    result=consumer.analyze(data,prepared);group,=result['groups']
    assert group['declared_path_count']==2 and group['declared_paths_without_context_candidate_count']==1
    assert group['declared_paths_without_context_candidate_sha256']==['1'*64]
    assert group['scope_complete'] is False and group['per_file_consumption_verified'] is False


@pytest.mark.parametrize('damage',['profile','source','raw-address','duplicate','unavailable-bound','scope','summary','promotion'])
def test_invalid_or_promoted_inputs_have_only_sanitized_error(tmp_path,monkeypatch,damage):
    data,prepared,_=fixture(tmp_path,monkeypatch);rows=data['recordings'][0]['capture']['records']
    if damage=='profile':prepared['profile']='vllm-native-lifecycle-metadata/19'
    elif damage=='duplicate':rows.append(deepcopy(rows[-1]))
    elif damage=='unavailable-bound':data['recordings'].append({'capture':None,'session_owner_match':True,'listed_file_identity_match':False})
    elif damage=='scope':prepared['model_read_scope']['nonsecret_model_weights']=False
    elif damage=='summary':data['recordings'][0]['capture']['summary']=PRIVATE
    else:
        row=next(r['payload'] for r in rows if r['event']=='SAFE_OPEN_MAPPING')
        if damage=='source':row['source_sha256']='0'*64
        elif damage=='raw-address':row['raw_address']=PRIVATE
        else:row['model_bytes_read_verified']=True
    with pytest.raises(ValueError,match='^NATIVE_MODEL_MAPPING_INPUT_INVALID$') as caught:consumer.analyze(data,prepared)
    assert PRIVATE not in str(caught.value)


def test_common_source_gate_uses_reviewed_literal_not_observed_hash(tmp_path,monkeypatch):
    data,prepared,_=fixture(tmp_path,monkeypatch)
    monkeypatch.setitem(consumer.SOURCE_PINS,'vllm_safe_open_mapping_metadata.py','0'*64)
    with pytest.raises(ValueError,match='^NATIVE_MODEL_MAPPING_COMMON_SOURCE_MISMATCH$'):consumer.analyze(data,prepared)
