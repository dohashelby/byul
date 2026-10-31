"""Original A2 analysis through OBS02; EXAMPLE transport, no serving claims."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from guard.adapters import vllm_safe_open_mapping_metadata as common
from guard.collectors import native_model_mappings as dedicated
from guard.executors.kubernetes_observation_identity import _attach_scoped_mapping_contexts
from guard.models import canonical_json,sha256_hex
from guard.store import Store
from tests.test_native_model_mappings import fixture as mapping_fixture
from tests.test_a1_safe_open_mapping_product import test_existing_identity_product_native18_consumption_and_exact_refs as run_existing_product
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_sensor import writer_factory
from tests.test_a1_weight_iteration_sensor import PRIVATE


def attachment_case(tmp_path,monkeypatch):
    packet,prepared,_=mapping_fixture(tmp_path,monkeypatch)
    projection=dedicated.analyze(packet,prepared);candidate,=projection['candidates']
    roots=common.project_iterations(packet)['model_candidates']
    model={'native_locator':{'recording':candidate['recording'],
        'context':{'pid':candidate['pid'],'start_ticks':candidate['start_ticks']},
        'forward_seq':max(candidate['root_last_seq'],candidate['last_source_event'][1])+1},
        'session_model_object_sha256':candidate['observed_model_object_sha256'],
        'same_process_loader_object_linked':True,'source_process_context_verified':True,
        'weight_iteration_candidates':roots}
    return [{'configured_model_candidates':[model]}],projection,model,candidate


def test_same_root_context_attaches_without_mutating_original_analysis(tmp_path,monkeypatch):
    trials,projection,model,candidate=attachment_case(tmp_path,monkeypatch);before=deepcopy(projection)
    _attach_scoped_mapping_contexts(trials,projection)
    assert model['scoped_mapping_context_candidates']==[candidate] and projection==before
    assert all(candidate[key] is False for key in dedicated.TRUTH)


@pytest.mark.parametrize('damage',['recording','pid','start','model','loader','root','root-event','root-last',
    'late-last-truncated','last-other-recording','first-other-recording','retained-event-after-forward','context-unverified','root-unverified'])
def test_context_never_crosses_generation_root_or_untruncated_last_event(tmp_path,monkeypatch,damage):
    trials,projection,model,candidate=attachment_case(tmp_path,monkeypatch)
    if damage=='recording':candidate['recording']+=1
    elif damage=='pid':candidate['pid']+=1
    elif damage=='start':candidate['start_ticks']+=1
    elif damage=='model':candidate['observed_model_object_sha256']='0'*64
    elif damage=='loader':candidate['loader_call_sha256']='0'*64
    elif damage=='root':candidate['root_load_sha256']='0'*64
    elif damage=='root-event':candidate['root_event'][1]+=1
    elif damage=='root-last':candidate['root_last_seq']=model['native_locator']['forward_seq']
    elif damage=='late-last-truncated':
        candidate['source_events']=candidate['source_events'][:1]
        candidate['source_event_locators_complete']=False
        candidate['last_source_event']=[candidate['recording'],model['native_locator']['forward_seq']]
        assert all(event[1]<model['native_locator']['forward_seq'] for event in candidate['source_events'])
    elif damage=='last-other-recording':candidate['last_source_event'][0]+=1
    elif damage=='first-other-recording':candidate['first_source_event'][0]+=1
    elif damage=='retained-event-after-forward':candidate['source_events'].append([candidate['recording'],model['native_locator']['forward_seq']])
    elif damage=='context-unverified':model['source_process_context_verified']=False
    else:model['weight_iteration_candidates'][0]['source_verified']=False
    _attach_scoped_mapping_contexts(trials,projection)
    assert model['scoped_mapping_context_candidates']==[]


def test_attachment_failure_does_not_leave_partial_dedicated_models(tmp_path,monkeypatch):
    trials,projection,model,_=attachment_case(tmp_path,monkeypatch)
    trials[0]['configured_model_candidates'].append({'native_locator':{}})
    before=deepcopy(trials)
    with pytest.raises(KeyError):_attach_scoped_mapping_contexts(trials,projection)
    assert trials==before and 'scoped_mapping_context_candidates' not in model


@pytest.mark.parametrize('mode',['normal','partial','later-record-error','attachment','dedicated-error',
    'no-iteration','wrong-model','wrong-source','unbound-partial'])
def test_original_dedicated_consumer_existing_CLI_saved_pack_exact_refs(tmp_path,group,native_target,writer_factory,monkeypatch,mode):
    if mode=='dedicated-error':
        def unavailable(*args,**kwargs):raise ValueError(PRIVATE)
        monkeypatch.setattr(dedicated,'analyze',unavailable)
    run_existing_product(tmp_path,group,native_target,writer_factory,monkeypatch,'normal' if mode=='dedicated-error' else mode)
    store=Store.read_only(tmp_path/'store');evd,=store.iter_kind('EVD')
    models=[model for trial in evd.observations['identity_native_models'].value for model in trial['configured_model_candidates']]
    contexts=[candidate for model in models for candidate in model.get('scoped_mapping_context_candidates',[])]
    weights=[weight for model in models for weight in model.get('weight_iteration_candidates',[])]
    mappings=[sample for model in models for sample in model.get('safe_open_mapping_candidates',[])]
    if mode in {'normal','partial','later-record-error','attachment'}:
        assert contexts and all(candidate['tensor_boundary_return_observed'] for candidate in contexts)
        for model in models:
            locator=model['native_locator']
            for candidate in model.get('scoped_mapping_context_candidates',[]):
                assert candidate['last_source_event'][0]==locator['recording']
                assert candidate['last_source_event'][1]<locator['forward_seq']
                assert candidate['pid']==locator['context']['pid'] and candidate['start_ticks']==locator['context']['start_ticks']
                assert candidate['observed_model_object_sha256']==model['session_model_object_sha256']
    else:assert contexts==[]
    if mode=='dedicated-error':assert evd.execution.status.value=='ABORTED' and weights and mappings
    assert all(candidate[key] is False for candidate in contexts for key in dedicated.TRUTH)
    assert PRIVATE not in canonical_json(evd.to_dict()).decode()
    for artifact in evd.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/artifact.path).read_bytes()
    objects=[obj for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for obj in store.iter_kind(kind)]
    restored=Store.read_only(tmp_path/'restored');assert len(objects)==6 and restored.verify()==[]
    assert all(restored.resolve(obj.ref()).to_dict()==obj.to_dict() for obj in objects)
    receipt={'case':mode,'schema':dedicated.SCHEMA,'version':dedicated.VERSION,
        'dedicated_source_sha256':sha256_hex(Path(dedicated.__file__).read_bytes()),
        'actual_worker_approval_tensor_consumption':False,'capture_origin':'EXAMPLE',
        'context_candidate_count':len(contexts),'common_mapping_count':len(mappings),
        'execution':evd.execution.status.value,'exact_refs':[obj.ref().model_dump(mode='json') for obj in objects],
        'original_export_sha256':sha256_hex((tmp_path/'evidence.zip').read_bytes()),'saved_pack_rejudge_equal':True}
    (tmp_path/'a1-native18-product-receipt.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n',encoding='utf-8')
