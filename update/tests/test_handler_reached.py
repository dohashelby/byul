"""handler_reached는 검증된 안전 프로브가 있는 경로에서만 생긴다. 일반 4xx는 FAIL로 취급하지 않는다."""

from guard.adapters.base import ProbeRequest
from guard.executors.http_common import HANDLER_REACHED, classify
from guard.models import Verdict
from tests.builders import evidence, req01_obs
from tests.judging import direct, sec

SAFE = ProbeRequest("POST", "/scale_elastic_ep", {}, expected_handler_reached={"status": 400, "body_contains": "new_data_parallel_size is required"},
                    basis="safe_probe")
PLAIN = ProbeRequest("POST", "/v1/messages", {})
BODY = '{"error":{"message":"new_data_parallel_size is required","code":400}}'


def test_only_safe_probe_with_matching_response_is_handler_reached():
    assert classify({"outcome": 400, "body": BODY}, SAFE) == HANDLER_REACHED
    assert classify({"outcome": 400, "body": "other validation error"}, SAFE) == 400  # 기대 문구 불일치
    assert classify({"outcome": 422, "body": BODY}, SAFE) == 422                       # 기대 코드 불일치
    assert classify({"outcome": 400, "body": BODY}, PLAIN) == 400                      # 안전 프로브가 아닌 경로
    assert classify({"outcome": 400, "body": BODY}, None) == 400


def test_plain_4xx_is_not_fail_but_handler_reached_is():
    obs400 = req01_obs(direct=direct(invocations=400))
    assert sec(evidence(observations=obs400)) == Verdict.UNKNOWN      # 일반 4xx → FAIL 아님
    obs_hr = req01_obs(direct=direct(invocations=HANDLER_REACHED))
    assert sec(evidence(observations=obs_hr)) == Verdict.FAIL         # 안전 프로브 핸들러 도달 → FAIL
