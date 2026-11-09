"""Actual V1 gather function with explicit synthetic CPU state; no selected MM model."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.executors import multimodal_gather_cpu as producer


def captured():
    return json.loads((Path(__file__).parent / 'fixtures/multimodal_gather_k3d_20261108.json').read_text())


def test_actual_typed_parser_and_worker_method_cover_masks_windows_and_cache_miss():
    value = captured(); result = producer.project(value)
    assert result['component_scope_complete'] and not result['errors'] and not result['violation_candidates']
    rows = {r['case']: r for r in value['rows']}
    assert rows['masked']['mask'] == [False, True, False, True, False]
    assert rows['partial-window']['embedding_shapes'] == [[2, 8]] and rows['partial-window']['reference_matches']
    assert rows['missing-cache']['outcome'] == 'cache_miss'
    assert rows['hidden-mismatch']['outcome'] == 'parser_rejected'


def test_real_text_default_record_does_not_become_mm_consumption():
    value = captured(); result = producer.project(value)
    row = next(r for r in value['rows'] if r['case'] == 'text-default')
    assert row['embedding_shapes'] == [] and not any(row['mask'])
    assert value['model_binding'] is None and result['selected_processor_worker_complete'] is False
    assert result['admitted_worker_consumption_verified'] is False


@pytest.mark.parametrize('damage', ['mask', 'shape', 'reference', 'hidden-accepted', 'cache-accepted'])
def test_violation_candidates_survive_later_collection_errors(damage):
    value = captured(); row = value['rows'][0]
    if damage == 'mask': row['mask'][0] = True
    elif damage == 'shape': row['embedding_shapes'] = [[2, 7]]
    elif damage == 'reference': row['reference_matches'] = False
    else:
        row = next(r for r in value['rows'] if r['case'] == ('hidden-mismatch' if damage == 'hidden-accepted' else 'missing-cache'))
        row['outcome'] = 'gathered'; row['error_sha256'] = None
    value['source_ended'] = False
    result = producer.project(value)
    assert result['violation_candidates'] and 'MM_GATHER_SOURCE_INCOMPLETE' in result['errors']


@pytest.mark.parametrize('damage', ['execution-error', 'missing-case', 'duplicate', 'wrong-error'])
def test_execution_failures_and_incomplete_rows_are_not_support_completion(damage):
    value = captured()
    if damage == 'execution-error': value['rows'][0]['outcome'] = 'execution_error'
    elif damage == 'missing-case': value['rows'].pop()
    elif damage == 'duplicate': value['rows'][1] = deepcopy(value['rows'][0])
    else:
        row = next(r for r in value['rows'] if r['case'] == 'missing-cache'); row['error_sha256'] = 'f' * 64
    result = producer.project(value)
    assert result['errors'] and not result['component_scope_complete']


@pytest.mark.parametrize('damage', ['model-binding', 'source', 'producer', 'grade', 'extra'])
def test_no_model_can_be_invented_or_example_promoted(damage):
    value = captured()
    if damage == 'model-binding': value['model_binding'] = {'model': 'opt-text-model'}
    elif damage == 'source': value['sources_before']['vllm.multimodal.parse'] = 'f' * 64
    elif damage == 'producer': value['producer']['source_sha256'] = 'f' * 64
    elif damage == 'grade': value['data_origin'] = 'MEASURED'
    else: value['selected_processor_worker_complete'] = True
    with pytest.raises(ValueError, match='MM_GATHER_ENVELOPE_INVALID'):
        producer.project(value)
