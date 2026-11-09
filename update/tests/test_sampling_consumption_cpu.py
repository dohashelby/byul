"""Actual typed/scheduler/batch/metadata/Sampler output, EXAMPLE CPU component scope."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.executors import sampling_consumption_cpu as producer


def captured():
    return json.loads((Path(__file__).parent / 'fixtures/sampling_consumption_k3d_20261108.json').read_text())


def test_actual_components_preserve_options_and_enforce_the_output_controls():
    value = captured(); result = producer.project(value)
    assert result['component_scope_complete'] and not result['errors'] and not result['violation_candidates']
    successful = [r for r in value['rows'] if r['outcome'] == 'sampled']
    assert len(successful) == 5
    assert all(r['same_scheduler_params'] and r['expected_output_matched'] and r['slot'] == 0 for r in successful)
    assert all(set(r['stages']) == {'typed', 'scheduler', 'batch', 'metadata'} for r in successful)
    assert result['admitted_worker_consumption_verified'] is False


def test_real_slot_reuse_does_not_apply_the_previous_allow_mask():
    rows = {r['case']: r for r in captured()['rows']}
    assert rows['allowed']['stages']['metadata']['allowed_tokens'] == 1
    assert rows['normal-after-allowed']['stages']['metadata']['allowed_tokens'] == 0
    assert rows['normal-after-allowed']['expected_output_matched']


def test_actual_invalid_options_are_rejected_before_scheduler_and_sampler():
    rows = [r for r in captured()['rows'] if r['case'].startswith('invalid-')]
    assert len(rows) == 3 and all(r['outcome'] == 'typed_rejected' for r in rows)
    assert all(not r['stages'] and r['slot'] is None and r['error_stage'] == 'typed' and r['expected_output_matched'] is None for r in rows)


@pytest.mark.parametrize('damage', ['metadata', 'scheduler-identity', 'output'])
def test_actual_row_mutations_remain_candidates_after_source_collection_error(damage):
    value = captured(); row = value['rows'][0]
    if damage == 'metadata': row['stages']['metadata']['top_p'] = .5
    elif damage == 'scheduler-identity': row['same_scheduler_params'] = False
    else: row['expected_output_matched'] = False
    value['source_ended'] = False
    result = producer.project(value)
    assert result['violation_candidates'] and 'SAMPLING_CONSUMPTION_SOURCE_INCOMPLETE' in result['errors']


@pytest.mark.parametrize('damage', ['missing-stage', 'nan', 'request', 'duplicate', 'missing-case', 'execution-error'])
def test_incomplete_or_malformed_stage_records_do_not_complete(damage):
    value = captured(); row = value['rows'][0]
    if damage == 'missing-stage': del row['stages']['metadata']
    elif damage == 'nan': row['stages']['metadata']['top_p'] = float('nan')
    elif damage == 'request': row['request_sha256'] = 'f' * 64
    elif damage == 'duplicate': value['rows'][1] = deepcopy(row)
    elif damage == 'missing-case': value['rows'].pop()
    else: row['outcome'] = 'execution_error'; row['expected_output_matched'] = None
    assert not producer.project(value)['component_scope_complete']


def test_unrelated_execution_error_is_not_a_typed_rejection_or_a_violation():
    value = captured(); row = value['rows'][-1]
    row['outcome'] = 'execution_error'; row['error_sha256'] = 'f' * 64
    result = producer.project(value)
    assert 'SAMPLING_CONSUMPTION_REJECTION_UNVERIFIED' in result['errors'] and result['violation_candidates'] == []


@pytest.mark.parametrize('damage', ['source-before', 'source-after', 'producer', 'grade', 'extra'])
def test_source_bound_example_envelope_cannot_be_promoted(damage):
    value = captured()
    if damage.startswith('source-'): value['sources_' + damage.split('-')[1]]['vllm.sampling_params'] = 'f' * 64
    elif damage == 'producer': value['producer']['source_sha256'] = 'f' * 64
    elif damage == 'grade': value['data_origin'] = 'MEASURED'
    else: value['admitted_worker_consumption_verified'] = True
    with pytest.raises(ValueError, match='SAMPLING_CONSUMPTION_ENVELOPE_INVALID'):
        producer.project(value)
