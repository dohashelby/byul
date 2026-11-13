"""REQ-12 사건 투영은 계약 후보·공백을 보존하고 출력 귀속을 추정하지 않는다."""
from copy import deepcopy

import pytest

from guard.collectors import gpu_common_inputs as contract
from guard.collectors.request_attempt_projection import project
from tests.test_gpu_common_inputs import COMPLETE, PRODUCER, SCOPE, record, row


def test_req12_declaration_and_unregistered_projection_preserve_candidates():
    assert 'REQ-12' in contract.KINDS['request-attempt']['checks']
    raw = record([*COMPLETE, row(4, 'retry', retry_cause='timeout'),
                  row(5, 'stage', attempt=2, consumed_by_attempt=1, stage='decode')], ended=False)
    before = deepcopy(raw)
    result = project(raw, **SCOPE)
    assert result['supported'] is result['complete'] is False
    assert result['producer_authenticated'] is result['whole_question_complete'] is False
    assert 'GPU_INPUT_PRODUCER_UNSUPPORTED' in result['gaps']
    assert [r['reason'] for r in result['violation_candidates']] == [
        'retry_without_new_attempt', 'consumption_by_finished_attempt']
    result['events'].clear()
    assert raw == before


def test_supported_complete_attempt_is_not_complete_output_proof(monkeypatch):
    monkeypatch.setitem(contract.SUPPORTED_PRODUCERS,
        (PRODUCER['name'], PRODUCER['version']), PRODUCER['source_sha256'])
    result = project(record(COMPLETE), **SCOPE)
    assert result['supported'] is result['complete'] is True
    assert result['whole_question_complete'] is False
    assert 'REQ12_OUTPUT_RECIPIENT_UNVERIFIED' in result['gaps']


def test_projection_rejects_record_with_a_different_expected_scope():
    with pytest.raises(ValueError):
        project(record(COMPLETE), **{**SCOPE, 'session': 'f'*32})
