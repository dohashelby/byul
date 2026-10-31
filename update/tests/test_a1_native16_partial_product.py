"""Original A2 f8 partial capture fix through existing OBS02, EXAMPLE only."""
import json
from pathlib import Path
import pytest
from guard.collectors import native_weight_iteration as dedicated
from guard.models import canonical_json,sha256_hex
from guard.store import Store
from tests import test_a1_weight_iteration_product as product
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_sensor import writer_factory
from tests.test_lifecycle_capture import case,external
from tests.test_a1_weight_iteration_sensor import PRIVATE


@pytest.mark.parametrize('mode',['normal','other-capture-missing','partial','later-record-error','attachment','other-missing-attachment','dedicated-error'])
def test_original_f8_partial_capture_existing_product_and_exact_refs(tmp_path,group,native_target,writer_factory,monkeypatch,mode):
    assert dedicated.VERSION=='0.1.1'
    original_inputs=product.identity_inputs;analyze=dedicated.analyze;calls=[]
    missing_mode=mode in {'other-capture-missing','other-missing-attachment'}
    if missing_mode:
        def inputs(path,grouping,target,patch,base_mode):
            return original_inputs(path,grouping,target,patch,'native-one-missing')
        monkeypatch.setattr(product,'identity_inputs',inputs)
    def observed(packet):
        if mode=='dedicated-error':raise ValueError(PRIVATE)
        result=analyze(packet)
        unavailable=[index for index,row in enumerate(packet['recordings']) if row['capture'] is None]
        available=[index for index,row in enumerate(packet['recordings']) if row['capture'] is not None]
        calls.append({'unavailable_recordings':unavailable,'available_recordings':available,'missing':result['missing'],
            'candidate_roots':[candidate['root_event'] for candidate in result['candidates']]})
        if missing_mode:
            assert unavailable and available and result['candidates']
            assert 'weight_recording_capture_unavailable' in result['missing']
            assert all(packet['recordings'][index]['session_owner_match'] is False
                and packet['recordings'][index]['listed_file_identity_match'] is False for index in unavailable)
            assert all(candidate['root_event'][0] in available and candidate['selection_event'][0] in available
                and all(event[0] in available for event in candidate['source_events']) for candidate in result['candidates'])
        return result
    monkeypatch.setattr(dedicated,'analyze',observed)
    old_mode='attachment' if mode=='other-missing-attachment' else 'normal' if mode in {'other-capture-missing','dedicated-error'} else mode
    product.test_existing_identity_product_native16_consumption_and_exact_refs(tmp_path,group,native_target,writer_factory,monkeypatch,old_mode)
    store=Store.read_only(tmp_path/'store');evd,=store.iter_kind('EVD')
    models=[model for trial in evd.observations['identity_native_models'].value for model in trial['configured_model_candidates']]
    weights=[candidate for model in models for candidate in model.get('weight_iteration_candidates',[])]
    selections=[candidate for model in models for candidate in model.get('weight_selection_iteration_candidates',[])]
    assert weights
    if mode=='dedicated-error':assert selections==[] and evd.execution.status.value=='ABORTED'
    else:
        assert calls and selections and all(candidate['same_loader_call_candidate'] for candidate in selections)
        assert all(candidate['per_file_consumption_verified'] is False and candidate['scope_complete'] is False for candidate in selections)
    if missing_mode:assert evd.execution.status.value=='ABORTED'
    if mode in {'attachment','other-missing-attachment','later-record-error'}:assert evd.execution.status.value=='ABORTED'
    assert PRIVATE not in canonical_json(evd.to_dict()).decode()
    for artifact in evd.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/artifact.path).read_bytes()
    objects=[obj for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for obj in store.iter_kind(kind)]
    restored=Store.read_only(tmp_path/'restored');assert len(objects)==6 and restored.verify()==[]
    assert all(restored.resolve(obj.ref()).to_dict()==obj.to_dict() for obj in objects)
    receipt={'case':mode,'version':dedicated.VERSION,'schema':dedicated.SCHEMA,'capture_origin':'EXAMPLE',
        'A2_original_source_sha256':sha256_hex(Path(dedicated.__file__).read_bytes()),'source_gate_or_parser_substituted':False,
        'recording_coverage':calls,'dedicated_candidates':len(selections),'common_candidates':len(weights),
        'execution':evd.execution.status.value,'actual_worker_approval_tensor_consumption':False,
        'exact_refs':[obj.ref().model_dump(mode='json') for obj in objects],
        'original_export_sha256':sha256_hex((tmp_path/'evidence.zip').read_bytes()),'saved_pack_rejudge_equal':True}
    (tmp_path/'a1-native16-partial-product-receipt.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n',encoding='utf-8')
