"""Explicit EXAMPLE original source bodies/class with library gate substitute; no real worker/C/GPU."""
from copy import deepcopy
import json,threading
import pytest
from guard.collectors import native_worker_model_files as consumer
from guard.adapters import vllm_safe_open_mapping_sensor as mapping
from tests.test_a1_worker_startup_sensor import make_world,prepared_for,recording
from tests.test_a1_weight_iteration_sensor import PRIVATE
from tests.test_a1_file_read_sensor import MODEL_BYTES


def fixture(tmp_path,patch,mode='normal'):
    world=make_world(tmp_path,patch,mode='late-callback' if mode=='late-callback' else 'normal')
    if mode!='library-unavailable':patch.setattr(mapping,'library_identity',lambda *a:True)
    if mode=='callback-unavailable':patch.setattr(world.observer.startup_callback_module.WorkerReadinessObserver,'snapshot',lambda *a,**k:None)
    if mode=='cross-thread':
        def load(**kwargs):
            thread=threading.Thread(target=world.run_loader);thread.start();thread.join()
        world.worker.model_runner.load_model=load
    if mode=='late-callback':
        with pytest.raises(OSError,match=PRIVATE):world.start()
    else:world.start()
    world.close();return recording(world),prepared_for(world),world


def test_exact_startup_root_payload_link_to_scoped_context_preserves_all_source_anchors(tmp_path,monkeypatch):
    data,prepared,world=fixture(tmp_path,monkeypatch);before=deepcopy((data,prepared));result=consumer.analyze(data,prepared)
    assert (data,prepared)==before;candidate,=result['candidates']
    assert candidate['same_startup_root_file_context_candidate'] and candidate['tensor_boundary_return_observed'] and candidate['context_exit_return_observed']
    assert candidate['startup_call_event'][1]<candidate['root_event'][1]<candidate['root_link_event'][1]<=candidate['first_mapping_event'][1]<=candidate['last_mapping_event'][1]<candidate['startup_terminal_event'][1]
    assert candidate['startup_return_observed'] and candidate['recording_closed']
    assert all(row[k] is False for row in (result,candidate) for k in consumer.TRUTH)
    assert result['security']=='UNKNOWN'
    text=json.dumps(result);assert PRIVATE not in text and str(world.path) not in text and MODEL_BYTES.decode() not in text


@pytest.mark.parametrize('mode',['late-callback','callback-unavailable'])
def test_later_callback_error_preserves_root_context_but_never_becomes_readiness(tmp_path,monkeypatch,mode):
    data,prepared,_=fixture(tmp_path,monkeypatch,mode);result=consumer.analyze(data,prepared);candidate,=result['candidates']
    assert candidate['tensor_boundary_return_observed']
    if mode=='late-callback':assert not candidate['startup_return_observed'] and not candidate['root_exhausted_observed']
    else:assert not candidate['startup_callback_method_pair_verified'] and 'worker_startup_callback_unavailable' in result['missing']
    assert candidate['actual_worker_observed'] is False and candidate['ready_send_verified'] is False


@pytest.mark.parametrize('mode',['library-unavailable','cross-thread'])
def test_missing_library_or_same_pid_other_thread_is_unjoined(tmp_path,monkeypatch,mode):
    data,prepared,_=fixture(tmp_path,monkeypatch,mode);result=consumer.analyze(data,prepared)
    assert result['candidates']==[] and result['unjoined_mapping_event_count']>0


@pytest.mark.parametrize('damage',['binding','missing-startup','missing-root-link','mapping-model','mapping-loader','missing-step'])
def test_missing_attribution_never_guesses_from_process_or_time(tmp_path,monkeypatch,damage):
    data,prepared,_=fixture(tmp_path,monkeypatch);cap=data['recordings'][0]['capture'];rows=cap['records']
    if damage=='binding':cap['process_context_verified']=False
    elif damage in {'missing-startup','missing-root-link','missing-step'}:
        event={'missing-startup':'WORKER_STARTUP','missing-root-link':'WORKER_STARTUP_LINK','missing-step':'WEIGHT_ITERATION'}[damage]
        if damage=='missing-step':cap['records']=[r for r in rows if not (r['event']==event and r['payload']['phase']!='FACTORY')]
        else:cap['records']=[r for r in rows if r['event']!=event]
    else:
        for row in rows:
            if row['event']=='SAFE_OPEN_MAPPING':row['payload']['model_object_sha256' if damage=='mapping-model' else 'loader_call_sha256']='9'*64
    result=consumer.analyze(data,prepared);assert not result['candidates'] and result['unjoined_mapping_event_count']>0


def test_partial_recording_and_missing_other_capture_preserve_indices_and_earlier_context(tmp_path,monkeypatch):
    data,prepared,_=fixture(tmp_path,monkeypatch);cap=data['recordings'][0]['capture'];cap['summary']['recording_closed']=False
    cap['records']=[r for r in cap['records'] if not (r['event']=='SAFE_OPEN_MAPPING' and r['payload']['phase']=='EXIT_RETURN')]
    data['recordings'].insert(0,{'capture':None,'session_owner_match':False,'listed_file_identity_match':False})
    result=consumer.analyze(data,prepared);candidate,=result['candidates']
    assert candidate['recording']==1 and candidate['root_event'][0]==candidate['last_mapping_event'][0]==1
    assert candidate['tensor_boundary_return_observed'] and not candidate['context_exit_return_observed'] and not candidate['recording_closed']
    assert 'startup_file_recording_capture_unavailable' in result['missing']


def test_locator_bound_keeps_exact_upper_anchor_for_product_forward_comparison(tmp_path,monkeypatch):
    data,prepared,_=fixture(tmp_path,monkeypatch);monkeypatch.setattr(consumer,'MAX_LOCATORS',1)
    result=consumer.analyze(data,prepared);candidate,=result['candidates']
    assert not candidate['mapping_source_event_locators_complete'] and len(candidate['mapping_source_events'])==1
    assert candidate['last_mapping_event'][1]>candidate['mapping_source_events'][-1][1] and candidate['mapping_source_event_count']>1
    assert 'startup_file_event_locator_output_bound' in result['missing']


def test_multiple_startup_calls_with_same_model_remain_separate_and_output_bound_is_explicit(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch);monkeypatch.setattr(mapping,'library_identity',lambda *a:True)
    world.start();world.start();world.close();data=recording(world);prepared=prepared_for(world)
    result=consumer.analyze(data,prepared)
    assert len(result['candidates'])==result['candidate_context_count']==2
    assert len({c['startup_call_sha256'] for c in result['candidates']})==2
    assert len({c['root_load_sha256'] for c in result['candidates']})==2
    monkeypatch.setattr(consumer,'MAX_CANDIDATES',1);bounded=consumer.analyze(data,prepared)
    assert len(bounded['candidates'])==1 and bounded['candidate_context_count']==2 and not bounded['candidate_context_locators_complete']
    assert 'startup_file_candidate_output_bound' in bounded['missing']


def test_cross_recording_mapping_does_not_attach_to_another_recording_root(tmp_path,monkeypatch):
    data,prepared,_=fixture(tmp_path,monkeypatch);original=data['recordings'][0];other=deepcopy(original)
    other['capture']['records']=[r for r in other['capture']['records'] if r['event'] in {'START','SAFE_OPEN_MAPPING'}]
    original['capture']['records']=[r for r in original['capture']['records'] if r['event']!='SAFE_OPEN_MAPPING'];data['recordings'].append(other)
    result=consumer.analyze(data,prepared);assert not result['candidates'] and result['unjoined_mapping_event_count']>0
    assert all(row['event'][0]==1 for row in result['unjoined_mapping_events'])


@pytest.mark.parametrize('damage',['profile','source','truth','session','root-payload','raw','duplicate','unavailable-bound','process-start'])
def test_closed_contract_and_opaque_root_reference_tampering_are_sanitized(tmp_path,monkeypatch,damage):
    data,prepared,_=fixture(tmp_path,monkeypatch);rows=data['recordings'][0]['capture']['records']
    if damage=='profile':prepared['profile']='vllm-native-lifecycle-metadata/21'
    elif damage=='session':prepared['session']='a'*32
    elif damage=='process-start':rows[0]['payload']['context']['start_ticks']+=1
    elif damage=='duplicate':rows.append(deepcopy(rows[-1]))
    elif damage=='unavailable-bound':data['recordings'].append({'capture':None,'session_owner_match':True,'listed_file_identity_match':False})
    else:
        row=next(r['payload'] for r in rows if r['event']=='WORKER_STARTUP_LINK')
        if damage=='root-payload':row['weight_payload_sha256']='0'*64
        elif damage=='truth':row['actual_worker_observed']=True
        elif damage=='source':
            row=next(r['payload'] for r in rows if r['event']=='WORKER_STARTUP')
            row['source_files'][next(iter(row['source_files']))]='0'*64
        else:row['raw_path']=PRIVATE
    with pytest.raises(ValueError,match='^NATIVE_WORKER_FILE_INPUT_INVALID$') as caught:consumer.analyze(data,prepared)
    assert PRIVATE not in str(caught.value)


def test_literal_common_source_gate_is_not_runtime_auto_approval(tmp_path,monkeypatch):
    data,prepared,_=fixture(tmp_path,monkeypatch);monkeypatch.setitem(consumer.SOURCE_PINS,'vllm_worker_startup_metadata.py','0'*64)
    with pytest.raises(ValueError,match='^NATIVE_WORKER_FILE_COMMON_SOURCE_MISMATCH$'):consumer.analyze(data,prepared)
