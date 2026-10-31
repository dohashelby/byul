"""Real common journals over exact native bodies and declared EXAMPLE namespaces."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS
import pytest
from guard.adapters import vllm_generation_file_metadata as common
from guard.adapters import vllm_safe_open_mapping_sensor as mapping
from guard.adapters import vllm_worker_startup_metadata as legacy
from tests import test_a1_worker_mp_initialization_sensor as native20
from tests import test_a1_worker_generation_sensor as native21
from tests import test_a1_ready_endpoint_sensor as native22
from tests.test_a1_worker_startup_sensor import prepared_for

FACTORIES={20:native20,21:native21,22:native22}


def run_world(tmp_path,patch,version=21,*,many=False,partial=False):
    source=FACTORIES[version];world=source.make_world(tmp_path,patch)
    if many:
        parameter=type(world.model.parameters['weight']);tensor=type(world.tensor_values['weight'])
        for index in range(20):
            name='EXAMPLE_'+str(index);world.model.parameters[name]=parameter(name,world);world.tensor_values[name]=tensor()
    if partial:
        def early_close(values):
            iterator=world.auto._load_module('',world.model,values);assert next(iterator)=='weight';iterator.close();return {'weight'}
        world.model.load_weights=early_close
    # This namespace-only substitution occurs after real observer code admission.
    # It is not a native C/worker/library trust measurement.
    with patch.context() as local:
        local.setattr(mapping,'library_identity',lambda original,cache:True)
        assert world.start() is None;world.close()
    packet=source.packet(world);packet['recordings'][0]['capture']['image_digest_verified']=True
    prepared=prepared_for(world);prepared['profile']=world.base.PROFILE
    return world,packet,prepared


@pytest.mark.parametrize('version',[20,21,22])
def test_exact_profiles_keep_journals_immutable_and_all_truth_limits(tmp_path,monkeypatch,version):
    world,packet,prepared=run_world(tmp_path,monkeypatch,version)
    before=json.dumps(packet,sort_keys=True);value=common.project_file_contexts(packet,prepared)
    assert json.dumps(packet,sort_keys=True)==before
    assert value['schema']==common.SCHEMA and value['recording_profile']==world.base.PROFILE
    assert value['startup_generation'] and value['model_candidates'] and value['weight_iteration']['model_candidates']
    assert value['safe_open_mappings']['samples'] and not value['diagnostics']
    assert all(value[key] is False for key in common.TRUTH)
    assert all(candidate['process_binding_verified'] for candidate in value['model_candidates'])
    assert value['scope_binding']['environment_ref']==prepared['model_read_scope']['environment_ref']
    for context in value['safe_open_mappings']['contexts']:
        samples=[row for row in value['safe_open_mappings']['samples'] if row['context_sha256']==context['context_sha256']]
        assert context['first_mapping_event']==[0,samples[0]['event_seq']]
        assert context['last_mapping_event']==[0,samples[-1]['event_seq']]


@pytest.mark.parametrize('damage',['missing-first','missing-last','profile','schema','session','duplicate','restart','mapping-extra','weight-code'])
def test_later_or_other_capture_errors_keep_original_indices_and_valid_facts(tmp_path,monkeypatch,damage):
    _,packet,prepared=run_world(tmp_path,monkeypatch)
    good=packet['recordings'][0];other=deepcopy(good)
    if damage.startswith('missing'):
        absent={'capture':None,'session_owner_match':False,'listed_file_identity_match':False}
        packet['recordings']=[absent,good] if damage=='missing-first' else [good,absent]
    elif damage=='duplicate':packet['recordings']=[good,other]
    elif damage=='restart':
        other['capture']['process_context_verified']=False;other['capture']['records'][0]['payload']['context']['start_ticks']+=1
        packet['recordings']=[good,other]
    else:
        header=other['capture']['records'][0]['payload']
        if damage in {'profile','schema','session'}:header[damage]='unsupported'
        elif damage=='mapping-extra':
            row=next(r for r in other['capture']['records'] if r['event']=='SAFE_OPEN_MAPPING');row['payload']['raw']='PRIVATE_MUST_NOT_ESCAPE'
        else:
            row=next(r for r in other['capture']['records'] if r['event']=='WEIGHT_ITERATION');row['payload']['observer_code_sha256']='0'*64
        packet['recordings']=[good,other]
    value=common.project_file_contexts(packet,prepared);index=1 if damage=='missing-first' else 0
    assert any(row['recording']==index for row in value['weight_iteration']['samples'])
    assert any(row['recording']==index for row in value['model_candidates'])
    assert 'PRIVATE_MUST_NOT_ESCAPE' not in json.dumps(value)
    if damage=='duplicate':
        assert all(row['duplicate_process_generation'] and not row['process_binding_verified'] for row in value['recordings'])
        assert all(not row['process_binding_verified'] for row in value['weight_iteration']['model_candidates'])
    else:assert value['missing']
    if damage=='mapping-extra':
        assert any(row['recording']==1 for row in value['weight_iteration']['samples'])
        assert not any(row['recording']==1 for row in value['safe_open_mappings']['samples'])


@pytest.mark.parametrize('bound_key',['image_digest_verified','process_context_verified'])
def test_unbound_recording_preserves_facts_without_joining_a_worker(tmp_path,monkeypatch,bound_key):
    _,packet,prepared=run_world(tmp_path,monkeypatch)
    packet['recordings'][0]['capture'][bound_key]=False
    value=common.project_file_contexts(packet,prepared)
    assert value['weight_iteration']['samples'] and value['safe_open_mappings']['samples']
    assert all(not row['process_binding_verified'] for row in value['model_candidates'])


def test_partial_root_and_full_upper_anchor_are_not_a_truncated_forward_proof(tmp_path,monkeypatch):
    world,packet,prepared=run_world(tmp_path,monkeypatch,many=True)
    value=common.project_file_contexts(packet,prepared);root,=value['weight_iteration']['model_candidates']
    assert len(root['source_events'])==64 and not root['source_event_locators_complete']
    assert root['last_source_event'][1]>root['source_events'][-1][1]
    candidate={'session_model_object_sha256':root['model_object_sha256'],'native_locator':{
        'context':world.observer.writer.context,'recording':0,'forward_seq':len(packet['recordings'][0]['capture']['records'])+1}}
    trials=[{'configured_model_candidates':[candidate]}];common.attach_configured_models(trials,value)
    assert candidate['generation_file_context_candidates']
    candidate['native_locator']['forward_seq']=root['source_events'][-1][1]+1
    common.attach_configured_models(trials,value);assert candidate['generation_file_context_candidates']==[]


def test_early_close_is_partial_and_never_complete_tensor_consumption(tmp_path,monkeypatch):
    _,packet,prepared=run_world(tmp_path,monkeypatch,partial=True)
    value=common.project_file_contexts(packet,prepared)
    assert value['weight_iteration']['model_candidates']
    assert any(not row['root_exhausted_observed'] for row in value['weight_iteration']['model_candidates'])
    assert all(value[key] is False for key in common.TRUTH)


def test_old_public19_bridge_does_not_silently_accept_a_new_profile(tmp_path,monkeypatch):
    _,packet,prepared=run_world(tmp_path,monkeypatch)
    assert legacy.project_iterations(packet)['samples']==[]
    assert legacy.project_mappings(packet,prepared)['samples']==[]
    for profile in ('vllm-native-lifecycle-metadata/19','vllm-native-lifecycle-metadata/23'):
        with pytest.raises(ValueError,match='GENERATION_FILE_CONTEXT_PROFILE_UNSUPPORTED'):
            common.project_file_contexts(packet,{**prepared,'profile':profile})
    with pytest.raises(ValueError,match='GENERATION_FILE_CONTEXT_INPUT_INVALID'):
        common.project_file_contexts({'recordings':packet['recordings']*33},prepared)


@pytest.mark.parametrize('damage',['missing','model-hash','secret-scope','environment-ref'])
def test_malformed_scope_is_rejected_without_returning_raw_fields(tmp_path,monkeypatch,damage):
    _,packet,prepared=run_world(tmp_path,monkeypatch)
    if damage=='missing':prepared.pop('model_read_scope')
    elif damage=='model-hash':prepared['model_read_scope']['model_sha256']='PRIVATE_SCOPE'
    elif damage=='secret-scope':prepared['model_read_scope']['nonsecret_model_weights']=False
    else:prepared['model_read_scope']['environment_ref']['sha256']='PRIVATE_SCOPE'
    with pytest.raises(ValueError,match='^GENERATION_FILE_CONTEXT_SCOPE_INVALID$'):
        common.project_file_contexts(packet,prepared)
