"""REQ-12에 반환하는 공통 시도 관측. 출력 수신·정책 위반은 추정하지 않는다."""
from copy import deepcopy

from .gpu_common_inputs import validate


def project(record, *, environment_ref, target_sha256, prepared_sha256, session):
    """외부 기대 범위로 검증한 사건과 후보를 분리해 돌려준다."""
    checked = validate(record, kind='request-attempt', environment_ref=environment_ref,
        target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)
    return {
        'check_id': 'REQ-12',
        'supported': checked['supported'],
        'complete': checked['complete'],
        'events': deepcopy(record['events']),
        'violation_candidates': deepcopy(checked['violation_candidates']),
        'gaps': sorted(set(checked['gaps']) | {
            'REQ12_OUTPUT_RECIPIENT_UNVERIFIED', 'REQ12_CANCEL_POLICY_UNVERIFIED',
            'REQ12_AUTOMATIC_RETRY_UNVERIFIED'}),
        'producer_authenticated': False,
        'whole_question_complete': False,
    }
