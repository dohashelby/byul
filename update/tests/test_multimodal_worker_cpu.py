"""Captured raw images -> real vLLM MM worker -> actual embedding/model/sample calls."""
import json
from pathlib import Path

import pytest

from guard.collectors import multimodal_worker_cpu as consumer


def captured():
    return json.loads((Path(__file__).parent / 'fixtures/multimodal_worker_k3d_20261108.json').read_text())


def native(value, point, phase='RETURN'):
    return [r for r in value['rows'] if r.get('schema') == consumer.callback.SCHEMA and r.get('point') == point and r.get('phase') == phase]


def test_actual_raw_media_worker_gather_preprocess_model_and_sampler_are_connected():
    result = consumer.project(captured())
    assert result['scenario_complete'] and result['selected_request_chains_complete'] and result['selected_multimodal_links_complete']
    assert len(result['requests']) == 4 and sum(len(r['steps']) for r in result['requests']) == 8
    for request in result['requests']:
        if request['case'] == 'text-recovery':
            assert request['multimodal_embedding_links'] == []
        else:
            assert len(request['multimodal_embedding_links']) == 1
            assert request['multimodal_embedding_links'][0]['mask_tokens'] == 4
    assert result['violation_candidates'] == []
    assert result['capture_complete'] is False and result['source_ended'] is True
    assert result['selected_pretrained_model_verified'] is False and result['pretrained_weights_loaded'] is False
    assert result['load_format'] == 'dummy' and result['GPU_verified'] is False


@pytest.mark.parametrize('point,field', [
    ('GPUModelRunner._model_forward', 'selected_embeddings_sha256'),
    ('GPUModelRunner._preprocess', 'selected_embeddings_sha256'),
])
def test_changed_gathered_embedding_is_a_candidate_after_later_capture_failure(point, field):
    value = captured()
    row = next(r for r in native(value, point) if r['fields'].get(field))
    row['fields'][field] = 'f' * 64
    value['source_ended'] = False
    result = consumer.project(value)
    assert not result['selected_multimodal_links_complete'] and not result['source_ended']
    assert any(v['reason'] == 'gathered_embeddings_changed_before_actual_model' for v in result['violation_candidates'])


@pytest.mark.parametrize('damage', ['feature', 'embedding-object', 'parent', 'mask', 'missing-gather'])
def test_mm_identity_gaps_cannot_be_replaced_by_matching_shape_or_output(damage):
    value = captured()
    if damage == 'feature':
        row = native(value, 'InputProcessor.process_inputs')[0]
        row['fields']['mm_features_sha256'] = 'f' * 64
    elif damage == 'embedding-object':
        row = next(r for r in native(value, 'GPUModelRunner._preprocess') if r['fields'].get('selected_embeddings_sha256'))
        row['fields']['inputs_embeds_object_sha256'] = 'f' * 64
    else:
        row = next(r for r in native(value, 'GPUModelRunner._gather_mm_embeddings') if r['fields'].get('gathered_embeddings_sha256'))
        if damage == 'parent': row['fields']['execution_parent_verified'] = False
        elif damage == 'mask': row['fields']['mask_tokens'] = 0
        else: value['rows'].remove(row)
    result = consumer.project(value)
    assert not result['selected_multimodal_links_complete']
    assert any(r['missing'] for r in result['requests'])


def test_same_raw_media_can_repeat_without_persistent_generation_or_embedding_claims():
    result = consumer.project(captured())
    requests = {r['case']: r for r in result['requests']}
    first = requests['image-rgb']['multimodal_embedding_links'][0]
    repeat = requests['image-repeat']['multimodal_embedding_links'][0]
    assert first['mm_features_sha256'] == repeat['mm_features_sha256']
    assert first['embeddings_sha256'] == repeat['embeddings_sha256']
    assert requests['image-rgb']['internal_sha256'] != requests['image-repeat']['internal_sha256']


def test_synthetic_mm_producer_and_text_engine_capture_are_not_interchangeable():
    from guard.collectors import consumption_links
    with pytest.raises(ValueError, match='CONSUMPTION_LINK_ENVELOPE_INVALID'):
        consumption_links.project(captured())
    value = captured(); value['target_model'] = 'Qwen2-VL-2B-Instruct'
    with pytest.raises(ValueError, match='CONSUMPTION_LINK_ENVELOPE_INVALID'):
        consumer.project(value)
