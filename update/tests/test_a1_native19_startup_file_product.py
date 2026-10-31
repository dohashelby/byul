"""Original A2 exact19 -> OBS02; fixture bindings remain EXAMPLE."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from guard.adapters import vllm_worker_startup_metadata as common,vllm_safe_open_mapping_sensor as mapping
from guard.collectors import native_worker_model_files as dedicated
from guard.executors.kubernetes_observation_identity import _attach_startup_file_contexts
from guard.models import canonical_json,sha256_hex
from guard.store import Store
from tests.test_native_worker_model_files import fixture as startup_fixture
from tests.test_a1_worker_startup_product import test_existing_identity_cli_profile19_facts_and_exact_ref_roundtrip as run_existing_product
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_sensor import writer_factory
from tests.test_lifecycle_capture import case,external
from tests.test_a1_weight_iteration_sensor import PRIVATE


def attachment_case(tmp_path,monkeypatch):
    packet,prepared,_=startup_fixture(tmp_path,monkeypatch)
    projection=dedicated.analyze(packet,prepared);candidate,=projection['candidates']
    roots=common.project_iterations(packet)['model_candidates'];starts=common.project(packet,prepared)['model_candidates']
    last=max([candidate['startup_terminal_event'][1],candidate['last_mapping_event'][1],*[root['last_seq'] for root in roots]])
    model={'native_locator':{'recording':candidate['recording'],'context':{'pid':candidate['pid'],'start_ticks':candidate['start_ticks']},'forward_seq':last+1},
        'session_model_object_sha256':candidate['observed_model_object_sha256'],'same_process_loader_object_linked':True,
        'source_process_context_verified':True,'weight_iteration_candidates':roots,'worker_startup_candidates':starts}
    return [{'configured_model_candidates':[model]}],projection,model,candidate


def test_exact_startup_root_and_loader_attaches_without_changing_analysis(tmp_path,monkeypatch):
    trials,projection,model,candidate=attachment_case(tmp_path,monkeypatch);before=deepcopy(projection)
    _attach_startup_file_contexts(trials,projection)
    assert model['scoped_startup_file_context_candidates']==[candidate] and projection==before
    assert all(candidate[key] is False for key in dedicated.TRUTH)


@pytest.mark.parametrize('damage',['recording','pid','start','model','loader','root','startup-call','startup-point','root-link',
    'late-startup-terminal','late-last-mapping-truncated','last-other-recording','retained-event-after-forward',
    'context-unverified','root-unverified','startup-unverified','root-terminal-after-forward'])
def test_startup_and_mapping_terminal_locators_cannot_cross_selected_forward_or_generation(tmp_path,monkeypatch,damage):
    trials,projection,model,candidate=attachment_case(tmp_path,monkeypatch)
    if damage=='recording':candidate['recording']+=1
    elif damage=='pid':candidate['pid']+=1
    elif damage=='start':candidate['start_ticks']+=1
    elif damage=='model':candidate['observed_model_object_sha256']='0'*64
    elif damage=='loader':candidate['loader_call_sha256']='0'*64
    elif damage=='root':candidate['root_load_sha256']='0'*64
    elif damage=='startup-call':candidate['startup_call_sha256']='0'*64
    elif damage=='startup-point':candidate['startup_point']='EXAMPLE-different-call'
    elif damage=='root-link':candidate['root_link_event'][1]+=1
    elif damage=='late-startup-terminal':candidate['startup_terminal_event'][1]=model['native_locator']['forward_seq']
    elif damage=='late-last-mapping-truncated':
        candidate['mapping_source_events']=candidate['mapping_source_events'][:1];candidate['mapping_source_event_locators_complete']=False
        candidate['last_mapping_event']=[candidate['recording'],model['native_locator']['forward_seq']]
        assert all(event[1]<model['native_locator']['forward_seq'] for event in candidate['mapping_source_events'])
    elif damage=='last-other-recording':candidate['last_mapping_event'][0]+=1
    elif damage=='retained-event-after-forward':candidate['mapping_source_events'].append([candidate['recording'],model['native_locator']['forward_seq']])
    elif damage=='context-unverified':model['source_process_context_verified']=False
    elif damage=='root-unverified':model['weight_iteration_candidates'][0]['source_verified']=False
    elif damage=='startup-unverified':model['worker_startup_candidates'][0]['process_binding_verified']=False
    else:model['weight_iteration_candidates'][0]['last_seq']=model['native_locator']['forward_seq']
    _attach_startup_file_contexts(trials,projection)
    assert model['scoped_startup_file_context_candidates']==[]


def test_startup_context_attachment_is_transactional_on_later_bad_model(tmp_path,monkeypatch):
    trials,projection,model,_=attachment_case(tmp_path,monkeypatch)
    trials[0]['configured_model_candidates'].append({'native_locator':{}});before=deepcopy(trials)
    with pytest.raises(KeyError):_attach_startup_file_contexts(trials,projection)
    assert trials==before and 'scoped_startup_file_context_candidates' not in model


@pytest.mark.parametrize('mode',['normal','late-callback','later-record-error','attachment','dedicated-error','common-error','wrong-model','wrong-link','no-worker-call'])
def test_original_native19_analysis_existing_CLI_saved_pack_and_exact_refs(tmp_path,group,native_target,writer_factory,monkeypatch,mode):
    # The original source-body fixture uses an EXAMPLE safe_open class. This
    # explicit library test double is not an actual C/worker observation;
    # A2's eight immutable source pins and packet/profile parser stay real.
    monkeypatch.setattr(mapping,'library_identity',lambda original,cache:True)
    calls=[]
    if mode in {'dedicated-error','common-error'}:
        def unavailable(*args,**kwargs):
            calls.append(True)
            if mode=='common-error':raise AssertionError('dedicated consumer retried after common failure')
            raise ValueError(PRIVATE)
        monkeypatch.setattr(dedicated,'analyze',unavailable)
    old_mode='consumer-error' if mode=='common-error' else 'normal' if mode=='dedicated-error' else mode
    run_existing_product(tmp_path,group,native_target,writer_factory,monkeypatch,old_mode)
    store=Store.read_only(tmp_path/'store');evd,=store.iter_kind('EVD')
    models=[model for trial in evd.observations['identity_native_models'].value for model in trial['configured_model_candidates']]
    contexts=[candidate for model in models for candidate in model.get('scoped_startup_file_context_candidates',[])]
    weights=[row for model in models for row in model.get('weight_iteration_candidates',[])]
    starts=[row for model in models for row in model.get('worker_startup_candidates',[])]
    mappings=[row for model in models for row in model.get('safe_open_mapping_candidates',[])]
    if mode in {'normal','late-callback','later-record-error','attachment'}:
        assert contexts and all(candidate['tensor_boundary_return_observed'] for candidate in contexts)
        for model in models:
            locator=model['native_locator']
            for candidate in model.get('scoped_startup_file_context_candidates',[]):
                assert candidate['startup_terminal_event'][1]<locator['forward_seq'] and candidate['last_mapping_event'][1]<locator['forward_seq']
                assert candidate['pid']==locator['context']['pid'] and candidate['start_ticks']==locator['context']['start_ticks']
                assert candidate['observed_model_object_sha256']==model['session_model_object_sha256']
    else:assert contexts==[]
    if mode=='dedicated-error':assert calls and weights and starts and mappings and evd.execution.status.value=='ABORTED'
    if mode=='common-error':assert calls==[] and weights and mappings and evd.execution.status.value=='ABORTED'
    assert all(candidate[key] is False for candidate in contexts for key in dedicated.TRUTH)
    assert PRIVATE not in canonical_json(evd.to_dict()).decode()
    for artifact in evd.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/artifact.path).read_bytes()
    objects=[obj for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for obj in store.iter_kind(kind)]
    restored=Store.read_only(tmp_path/'restored');assert len(objects)==6 and restored.verify()==[]
    assert all(restored.resolve(obj.ref()).to_dict()==obj.to_dict() for obj in objects)
    receipt={'case':mode,'schema':dedicated.SCHEMA,'version':dedicated.VERSION,'dedicated_source_sha256':sha256_hex(Path(dedicated.__file__).read_bytes()),
        'capture_origin':'EXAMPLE','EXAMPLE_library_identity_substitute':True,'A2_eight_source_pins_and_packet_parser_substituted':False,
        'context_candidates':len(contexts),'common_startup_candidates':len(starts),'execution':evd.execution.status.value,
        'actual_serving_worker_GPU_approval_tensor_release':False,'exact_refs':[obj.ref().model_dump(mode='json') for obj in objects],
        'original_export_sha256':sha256_hex((tmp_path/'evidence.zip').read_bytes()),'saved_pack_rejudge_equal':True}
    (tmp_path/'a1-native19-product-receipt.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n',encoding='utf-8')
