"""정규화 공통 packet의 A2 join 단위 시험. capture admission/실제제품은 별도다."""
from copy import deepcopy
from types import SimpleNamespace
import json
import pytest
from guard.collectors.native_weight_iteration import analyze
from guard.adapters import vllm_weight_iteration_metadata as common
from guard.adapters import vllm_lifecycle_profiles as profiles
from tests.test_a1_weight_iteration_sensor import make_world, packet, PRIVATE


def fixture(mode='normal',*,bound=True,closed=True):
    world=make_world('late-callback' if mode=='late-callback' else 'normal')
    if mode=='multiple':world.model.secondary_weights=[SimpleNamespace(prefix='')]
    if mode=='factory':world.load().close()
    elif mode=='late-callback':
        with pytest.raises(OSError):world.run_loader()
    elif mode in {'partial','unclosed'}:
        def partial(weights):
            world.held=world.auto._load_module('',world.model,weights)
            next(world.held)
            if mode=='partial':world.held.close()
            return {'weight'}
        world.model.load_weights=partial
        world.run_loader()
    else:world.run_loader()
    result=packet(world,bound=bound,closed=closed)
    if mode=='unclosed':world.held.close()
    return result


def selection(result,*,factory=False,call=None,model=None,paths=None):
    # Reuse the selection already emitted by the actual pinned observer.
    # Individual mutations test join logic after common normalization, not raw
    # recording admission (covered by the unchanged A1 parser tests).
    rows=result['recordings'][0]['capture']['records']
    row=next(r for r in rows if profiles.returned_weight_selection(r) is not None)
    value=row['payload']['metadata']['weight_selection']
    value['factory_returned_generator']=factory
    if paths is not None:value.update(normalized_path_sha256=paths,path_count=len(paths),paths_complete=True)
    if call is not None:value['weight_call_sha256']=call
    if model is not None:value['weight_model_sha256']=model
    if call is not None or model is not None:
        parent=next(f for f in reversed(row['payload']['call_chain']) if f['point']=='DefaultModelLoader.load_weights')
        parent.update(weight_call_sha256=value['weight_call_sha256'],weight_model_sha256=value['weight_model_sha256'])
    return result


@pytest.mark.parametrize('factory',[False,True,None])
def test_same_call_candidate_preserves_legacy_flag_without_file_or_tensor_truth(factory):
    source=selection(fixture(),factory=factory);before=deepcopy(source)
    result=analyze(source)
    assert result['candidates'] and source==before
    assert all(r['same_loader_call_candidate'] and r['legacy_factory_returned_generator'] is factory for r in result['candidates'])
    assert all(not r[k] for r in result['candidates'] for k in (*common.TRUTH,'scope_complete','per_file_consumption_verified'))
    assert result['security']=='UNKNOWN' and not result['per_file_consumption_verified']
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize('damage',['call','model','recording','after','binding'])
def test_cross_context_or_later_selection_does_not_join(damage):
    source=selection(fixture(),call='e'*64 if damage=='call' else None,model='f'*64 if damage=='model' else None)
    if damage=='recording':
        source['recordings'].append(deepcopy(source['recordings'][0]))
        source['recordings'][0]['capture']['records']=[r for r in source['recordings'][0]['capture']['records'] if r['event']!='NATIVE']
        source['recordings'][1]['capture']['records']=[r for r in source['recordings'][1]['capture']['records'] if r['event'] in {'START','NATIVE'}]
    if damage=='after':
        rows=source['recordings'][0]['capture']['records'];row=next(r for r in rows if profiles.returned_weight_selection(r) is not None)
        row['seq']=max(r['seq'] for r in rows)+1
    if damage=='binding':source['recordings'][0]['session_owner_match']=False
    assert analyze(source)['candidates']==[]


@pytest.mark.parametrize('mode',['partial','late-callback','unclosed'])
def test_known_earlier_pairs_remain_when_iteration_or_collection_is_incomplete(mode):
    source=selection(fixture(mode,closed=mode!='unclosed'))
    result=analyze(source)
    assert result['candidates'] and any(r['parameter_callback_returns_observed'] for r in result['candidates'])
    assert not any(r['root_exhausted_observed'] for r in result['candidates'])
    if mode=='unclosed':assert not any(r['recording_closed'] for r in result['candidates'])
    assert not result['tensor_storage_applied_verified']


def test_multiple_selections_are_ambiguous_not_merged_as_actual_consumption():
    source=fixture('multiple')
    result=analyze(source)
    assert result['candidates'] and all(r['selection_ambiguous'] for r in result['candidates'])


def test_factory_without_iteration_and_no_selection_have_no_join():
    assert analyze(fixture('factory'))['candidates']==[]
    source=fixture()
    for row in source['recordings'][0]['capture']['records']:
        if profiles.returned_weight_selection(row) is not None:row['payload']['metadata']['weight_selection']=None
    assert analyze(source)['candidates']==[]


def test_output_path_bound_keeps_known_candidate_and_reports_incomplete():
    result=analyze(selection(fixture(),paths=[f'{i:064x}' for i in range(40)]))
    assert result['candidates'] and all(len(r['selected_path_sha256'])==32 and not r['selection_paths_complete'] for r in result['candidates'])
    assert 'weight_selection_paths_incomplete' in result['missing']


def failed_recording():
    # Exact failure row shape emitted by capture_session after one read fails.
    return {'pid':43,'start_ticks':99,'capture':None,'session_owner_match':False,
        'listed_file_identity_match':False,'diagnostic_codes':['LIFECYCLE_PROCESS_RECORDING_UNAVAILABLE']}


@pytest.mark.parametrize('position',[0,1])
def test_one_failed_recording_preserves_other_admitted_pairs_and_locators(position):
    source=selection(fixture());before=analyze(source)
    source['recordings'].insert(position,failed_recording());saved=deepcopy(source)
    result=analyze(source);assert source==saved
    assert len(result['candidates'])==len(before['candidates']) and result['candidates']
    expected_recording=1 if position==0 else 0
    for candidate in result['candidates']:
        assert candidate['selection_event'][0]==candidate['root_event'][0]==expected_recording
        assert all(event[0]==expected_recording for event in candidate['source_events'])
        assert not candidate['per_file_consumption_verified'] and not candidate['scope_complete']
    assert 'weight_recording_capture_unavailable' in result['missing'] and result['security']=='UNKNOWN'


def test_all_recordings_unavailable_is_unknown_without_candidates():
    result=analyze({'recordings':[failed_recording()]})
    assert not result['candidates'] and result['security']=='UNKNOWN'
    assert 'weight_recording_capture_unavailable' in result['missing']


def test_unavailable_capture_cannot_claim_process_binding():
    source=selection(fixture());failed=failed_recording();failed['session_owner_match']=True
    source['recordings'].append(failed)
    with pytest.raises(ValueError,match='NATIVE_WEIGHT_ITERATION_INPUT_INVALID'):analyze(source)


def test_unverified_root_does_not_join():
    source=selection(fixture())
    for row in source['recordings'][0]['capture']['records']:
        if row['event']=='WEIGHT_ITERATION' and row['payload']['point']=='AutoWeightsLoader._load_module' and row['payload']['phase']=='FACTORY':
            row['payload']['source_verified']=False
    assert analyze(source)['candidates']==[]


@pytest.mark.parametrize('field',['loader_call_sha256','loader_model_sha256'])
def test_different_child_loader_does_not_supply_callback_facts(field):
    source=selection(fixture())
    for row in source['recordings'][0]['capture']['records']:
        if row['event']=='WEIGHT_ITERATION' and row['payload']['point']=='AutoWeightsLoader._load_param':
            row['payload'][field]='d'*64
    result=analyze(source)
    assert result['candidates'] and not any(r['parameter_callback_returns_observed'] for r in result['candidates'])


@pytest.mark.parametrize('damage',['pin','missing'])
def test_common_source_gate_has_sanitized_error(monkeypatch,damage):
    from guard.collectors import native_weight_iteration as consumer
    source=fixture()
    if damage=='pin':monkeypatch.setitem(consumer.SOURCE_PINS,'vllm_weight_iteration_metadata.py','0'*64)
    else:
        def unreadable(*args,**kwargs):raise OSError(PRIVATE)
        monkeypatch.setattr(consumer.Path,'read_bytes',unreadable)
    with pytest.raises(ValueError) as error:analyze(source)
    assert str(error.value)=='NATIVE_WEIGHT_ITERATION_COMMON_SOURCE_MISMATCH'
    assert PRIVATE not in str(error.value)


@pytest.mark.parametrize('damage',['profile','source','runtime','raw','duplicate'])
def test_unsupported_or_invalid_common_fields_are_rejected(damage):
    source=selection(fixture());rows=source['recordings'][0]['capture']['records']
    sample=next(r for r in rows if r['event']=='WEIGHT_ITERATION')
    if damage=='profile':rows[0]['payload']['profile']='vllm-native-lifecycle-metadata/9'
    elif damage=='source':sample['payload']['source_sha256']='0'*64
    elif damage=='runtime':sample['payload']['observer_runtime']='cpython-3.13.0'
    elif damage=='raw':sample['payload']['tensor_value']=PRIVATE
    else:rows.append(deepcopy(rows[-1]))
    with pytest.raises(ValueError,match='NATIVE_WEIGHT_ITERATION_INPUT_INVALID'):analyze(source)
