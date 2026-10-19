"""기능 축: REQ-01 override의 functional_probes 목록으로 판정. 목록이 늘어도 판정표는 같다."""

import json
from pathlib import Path

import pytest

from guard.models import CheckDef, Verdict
from tests.builders import evidence, req01_obs
from tests.judging import REQ01, judge

SRC = Path(__file__).resolve().parents[1] / "src/guard"


def func(**kw):
    return judge(evidence(observations=req01_obs(**kw))).verdicts.functional


def test_override_defines_probe_list_not_rules():
    probes = REQ01.functional_probes
    assert [(p.method, p.path, p.expect) for p in probes] == [("POST", "/v1/completions", [200])]
    assert probes[0].body["max_tokens"] == 1
    # 판정표에는 경로가 없다(목록이 늘어도 규칙은 그대로)
    assert "/v1/" not in json.dumps(REQ01.judge.params)


def test_no_hardcoded_probe_route_in_code():
    for f in [SRC / "executors/direct_route.py", SRC / "core/judge.py", SRC / "core/report.py", SRC / "judges/rule_table.py"]:
        assert "/v1/completions" not in f.read_text(encoding="utf-8"), f


@pytest.mark.parametrize(
    "results, planned, expected",
    [
        ({"POST /v1/completions": "pass"}, None, Verdict.PASS),
        ({"POST /v1/completions": "fail"}, None, Verdict.FAIL),
        ({"POST /v1/completions": "no_response"}, None, Verdict.UNKNOWN),
        ({"POST /v1/completions": "pass"}, ["POST /v1/completions", "GET /v1/models"], Verdict.UNKNOWN),  # 계획한 항목 누락
        ({}, [], Verdict.UNKNOWN),  # 빈 목록은 PASS가 아니다
        ({"a": "pass", "b": "pass", "c": "pass"}, None, Verdict.PASS),  # 목록이 늘어도 같은 판정표
        ({"a": "pass", "b": "fail", "c": "pass"}, None, Verdict.FAIL),
    ],
)
def test_functional_verdict(results, planned, expected):
    assert func(functional=results, planned=planned) == expected


def test_functional_independent_of_other_gateway_routes():
    # 다른 경로의 Gateway 허용 키 400/404는 기능 판정에 쓰지 않는다
    gw = {"/v1/models": 404, "/v1/completions": 200, "/invocations": 400}
    assert func(gateway=gw) == Verdict.PASS


def test_checkdef_probe_shape_validated():
    raw = REQ01.model_dump(mode="json")
    raw["functional_probes"] = [{"method": "POST", "path": "/x", "expect": []}]
    with pytest.raises(ValueError):
        CheckDef.model_validate(raw)
