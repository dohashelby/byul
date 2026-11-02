"""Dedicated joins over the fixed public20/21/22 projection; EXAMPLE only."""
from copy import deepcopy
import json
import pytest
from guard.collectors import native_generation_model_files as consumer
from guard.adapters import vllm_generation_file_metadata as common
from tests.test_a1_generation_file_metadata import run_world

PRIVATE='PRIVATE_MUST_NOT_ESCAPE'


def fixture(tmp_path,patch,version=21,**options):
    return run_world(tmp_path,patch,version,**options)


@pytest.mark.parametrize('version',[20,21,22])
def test_exact_public_profile_joins_local_file_context_without_trust(tmp_path,monkeypatch,version):
    world,data,prepared=fixture(tmp_path,monkeypatch,version)
    before=deepcopy((data,prepared));value=consumer.analyze(data,prepared)
    assert (data,prepared)==before and value['profile']==world.base.PROFILE
    assert value['schema']==consumer.SCHEMA and value['security']=='UNKNOWN'
    candidate,=value['candidates']
    assert candidate['same_startup_root_file_context_candidate']
    assert candidate['root_event'][1]<candidate['root_link_event'][1]<=candidate['first_mapping_event'][1]
    assert candidate['last_mapping_event'][1]<candidate['startup_terminal_event'][1]
    assert candidate['last_weight_source_event'][0]==candidate['recording']
    assert all(row[key] is False for row in (value,candidate) for key in consumer.TRUTH)
    assert candidate['startup_pair_facts_available']==(version==20)
    if version!=20:
        assert not candidate['startup_return_observed'] and not candidate['startup_callback_method_pair_verified']
        assert 'generation_file_startup_pair_facts_unavailable' in value['missing']
    text=json.dumps(value);assert PRIVATE not in text and str(tmp_path) not in text


@pytest.mark.parametrize('position',[0,1])
def test_missing_other_capture_retains_original_recording_and_earlier_candidate(tmp_path,monkeypatch,position):
    _,data,prepared=fixture(tmp_path,monkeypatch)
    data['recordings'].insert(position,{'capture':None,'session_owner_match':False,'listed_file_identity_match':False})
    value=consumer.analyze(data,prepared);candidate,=value['candidates']
    index=1-position
    assert candidate['recording']==index and candidate['root_event'][0]==candidate['last_mapping_event'][0]==index
    assert len(value['recording_status'])==2 and value['diagnostics']
    assert 'generation_file_capture_missing' in value['missing']


@pytest.mark.parametrize('damage',['bad-later-header','bad-mapping-stage','bad-weight-code'])
def test_other_or_later_error_keeps_independent_stage_facts_and_closed_diagnostics(tmp_path,monkeypatch,damage):
    _,data,prepared=fixture(tmp_path,monkeypatch)
    if damage=='bad-later-header':
        other=deepcopy(data['recordings'][0]);other['capture']['records'][0]['payload']['session']=PRIVATE
        data['recordings'].append(other)
    else:
        event='SAFE_OPEN_MAPPING' if damage=='bad-mapping-stage' else 'WEIGHT_ITERATION'
        row=next(row for row in data['recordings'][0]['capture']['records'] if row['event']==event)
        if damage=='bad-mapping-stage':row['payload']['raw']=PRIVATE
        else:row['payload']['observer_code_sha256']='0'*64
    value=consumer.analyze(data,prepared)
    assert value['diagnostics'] and PRIVATE not in json.dumps(value)
    if damage=='bad-later-header':assert value['candidates'] and value['candidates'][0]['recording']==0
    elif damage=='bad-mapping-stage':assert value['observed_weight_fact_count']>0 and value['candidates']==[]
    else:assert value['candidates']==[]
    assert not value['scope_complete']


@pytest.mark.parametrize('axis',['image_digest_verified','process_context_verified','duplicate-process','restart'])
def test_binding_axes_and_replacement_do_not_become_worker_identity(tmp_path,monkeypatch,axis):
    _,data,prepared=fixture(tmp_path,monkeypatch)
    recording=data['recordings'][0]
    if axis=='duplicate-process':data['recordings'].append(deepcopy(recording))
    elif axis=='restart':recording['capture']['records'][0]['payload']['context']['start_ticks']+=1
    else:recording['capture'][axis]=False
    value=consumer.analyze(data,prepared)
    if axis=='image_digest_verified':
        candidate,=value['candidates'];assert candidate['process_binding_verified'] and not candidate['image_binding_verified']
    else:assert value['candidates']==[]
    assert value['missing'] and not value['actual_worker_observed']


def test_partial_progress_and_full_upper_anchor_are_preserved(tmp_path,monkeypatch):
    _,data,prepared=fixture(tmp_path,monkeypatch,partial=True)
    value=consumer.analyze(data,prepared)
    assert value['candidates'] and any(not row['root_exhausted_observed'] for row in value['candidates'])
    assert all(not row['tensor_storage_applied_verified'] for row in value['candidates'])


@pytest.mark.parametrize('damage',['old-profile','future-profile','session','secret-scope','missing-scope','file-reference'])
def test_unadmitted_profile_or_invalid_prepared_contract_is_sanitized(tmp_path,monkeypatch,damage):
    _,data,prepared=fixture(tmp_path,monkeypatch)
    if damage.endswith('profile'):prepared['profile']='vllm-native-lifecycle-metadata/'+('19' if damage=='old-profile' else '23')
    elif damage=='session':prepared['session']=PRIVATE
    elif damage=='secret-scope':prepared['model_read_scope']['nonsecret_model_weights']=False
    elif damage=='missing-scope':prepared.pop('model_read_scope')
    else:prepared['model_read_scope']['files'][0]['reference_sha256']=PRIVATE
    with pytest.raises(ValueError,match='^NATIVE_GENERATION_FILE_INPUT_INVALID$') as caught:consumer.analyze(data,prepared)
    assert caught.value.__cause__ is None and PRIVATE not in str(caught.value)


@pytest.mark.parametrize('dependency',['vllm_generation_file_metadata.py','vllm_worker_generation_metadata.py','vllm_ready_endpoint_metadata.py'])
def test_changed_selected_version_dependency_is_rejected_before_projection(monkeypatch,dependency):
    called=[]
    monkeypatch.setitem(consumer.SOURCE_PINS,dependency,'0'*64)
    monkeypatch.setattr(common,'project_file_contexts',lambda *a:called.append(True))
    with pytest.raises(ValueError,match='^NATIVE_GENERATION_FILE_COMMON_SOURCE_MISMATCH$'):consumer.analyze({}, {})
    assert called==[]


def test_followup_projection_exception_never_echoes_raw_exception(monkeypatch):
    def broken(*a):raise RuntimeError(PRIVATE)
    monkeypatch.setattr(common,'project_file_contexts',broken)
    with pytest.raises(ValueError,match='^NATIVE_GENERATION_FILE_INPUT_INVALID$') as caught:
        consumer.analyze({}, {'profile':'vllm-native-lifecycle-metadata/21'})
    assert PRIVATE not in str(caught.value) and caught.value.__cause__ is None


def model_for(world,data,projection):
    root,=projection['weight_iteration']['model_candidates']
    return {'session_model_object_sha256':root['model_object_sha256'],'native_locator':{
        'context':dict(world.observer.writer.context),'recording':0,
        'forward_seq':len(data['recordings'][0]['capture']['records'])+100}}


@pytest.mark.parametrize('version',[20,21,22])
def test_attach_requires_existing_common_model_root_and_full_forward_bounds(tmp_path,monkeypatch,version):
    world,data,prepared=fixture(tmp_path,monkeypatch,version)
    projection=common.project_file_contexts(data,prepared);value=consumer.analyze(data,prepared)
    model=model_for(world,data,projection);trials=[{'configured_model_candidates':[model]}]
    common.attach_configured_models(trials,projection);consumer.attach_configured_models(trials,value)
    assert model['generation_scoped_file_context_candidates'] and not model['actual_tensor_consumption_verified']
    for damage in ['common-join','model','process','mapping-upper','weight-upper']:
        changed=deepcopy(model)
        if damage=='common-join':changed['generation_file_context_candidates']=[]
        elif damage=='model':changed['session_model_object_sha256']='0'*64
        elif damage=='process':changed['native_locator']['context']['start_ticks']+=1
        else:
            row=value['candidates'][0]
            changed['native_locator']['forward_seq']=row['last_mapping_event'][1] if damage=='mapping-upper' else row['last_weight_source_event'][1]
        consumer.attach_configured_models([{'configured_model_candidates':[changed]}],value)
        assert changed['generation_scoped_file_context_candidates']==[]


def test_truncated_prefix_never_supplies_full_upper_forward_proof(tmp_path,monkeypatch):
    world,data,prepared=fixture(tmp_path,monkeypatch,many=True)
    projection=common.project_file_contexts(data,prepared);value=consumer.analyze(data,prepared)
    model=model_for(world,data,projection);trials=[{'configured_model_candidates':[model]}]
    common.attach_configured_models(trials,projection);consumer.attach_configured_models(trials,value)
    assert model['generation_scoped_file_context_candidates']
    root,=projection['weight_iteration']['model_candidates']
    assert not root['source_event_locators_complete'] and root['last_source_event'][1]>root['source_events'][-1][1]
    model['native_locator']['forward_seq']=root['source_events'][-1][1]+1
    consumer.attach_configured_models(trials,value)
    assert model['generation_scoped_file_context_candidates']==[]

