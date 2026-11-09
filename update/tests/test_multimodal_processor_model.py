"""Real image processor + real random Qwen2-VL model calls; no pretrained deployment claim."""
import json
from pathlib import Path

import pytest

from guard.collectors import multimodal_processor_model as consumer


def captured():
    return json.loads((Path(__file__).parent / 'fixtures/multimodal_processor_model_cpu_20261108.json').read_text())


def test_actual_processor_vision_language_and_normal_after_rejection_are_connected():
    result = consumer.project(captured())
    assert result['processor_model_component_complete'] and result['errors'] == [] and result['violation_candidates'] == []
    assert result['selected_pretrained_model_verified'] is False and result['vllm_worker_verified'] is False and result['GPU_verified'] is False
    rows = {r['case']: r for r in captured()['rows']}
    assert rows['image-rgb']['embedding_values_match'] and rows['image-grayscale']['embedding_values_match']
    assert rows['token-mismatch']['outcome'] == 'token_feature_mismatch' and rows['token-mismatch']['language_shape'] == []
    assert rows['recovery']['recovery_logits_match'] is True


@pytest.mark.parametrize('damage', ['embedding', 'accepted-mismatch', 'recovery'])
def test_independent_candidates_remain_after_later_source_incompleteness(damage):
    value = captured()
    if damage == 'embedding': value['rows'][0]['embedding_values_match'] = False
    elif damage == 'accepted-mismatch': value['rows'][2]['outcome'] = 'returned'; value['rows'][2]['language_shape'] = [1, 6, 16]
    else: value['rows'][3]['recovery_logits_match'] = False
    value['source_ended'] = False
    result = consumer.project(value)
    assert result['violation_candidates'] and 'MM_PROCESSOR_MODEL_SOURCE_INCOMPLETE' in result['errors']


@pytest.mark.parametrize('damage', ['processor-failure', 'model-error', 'different-shape', 'wrong-rejection'])
def test_incomplete_real_components_are_not_complete(damage):
    value = captured()
    if damage == 'processor-failure': value['rows'][0]['processor_returned'] = False
    elif damage == 'model-error': value['rows'][0]['outcome'] = 'execution_error'
    elif damage == 'different-shape': value['rows'][0]['vision_shape'] = [4, 8]
    else: value['rows'][2]['error_sha256'] = 'f' * 64
    assert not consumer.project(value)['processor_model_component_complete']


@pytest.mark.parametrize('damage', ['pretrained', 'target', 'grade', 'source', 'configuration'])
def test_random_micro_model_cannot_be_promoted_to_the_approved_pretrained_target(damage):
    value = captured()
    if damage == 'pretrained': value['pretrained_weights_loaded'] = True
    elif damage == 'target': value['target_model'] = 'Qwen2-VL-2B-Instruct'
    elif damage == 'grade': value['data_origin'] = 'MEASURED'
    elif damage == 'source': value['sources_after']['transformers.models.qwen2_vl.modeling_qwen2_vl'] = 'f' * 64
    else: value['config_identity'] = 'f' * 64
    with pytest.raises(ValueError, match='MM_PROCESSOR_MODEL_ENVELOPE_INVALID'):
        consumer.project(value)
