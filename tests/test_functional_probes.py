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


@pytest.mark.parametrize("bad_body", [None, "{}", '{"error":"not an inference"}', "<html>login</html>"])
def test_inference_http_200_without_generation_is_not_a_passing_control(bad_body):
    from guard.executors.http_common import functional_results
    probe = REQ01.functional_probes[0]
    functional, _, normal = functional_results({probe.id: probe}, {probe.id: {"outcome": 200, "body": bad_body}}, "model")
    assert functional[probe.id] == "no_response"
    assert normal["succeeded"] is False


def test_non_inference_metrics_and_health_keep_their_status_expectation():
    from guard.executors.http_common import functional_results
    from guard.models.checkdef import FunctionalProbe
    probes = [FunctionalProbe(method="GET", path="/health", expect=[200]),
              FunctionalProbe(method="GET", path="/metrics", expect=[200])]
    functional, _, normal = functional_results({p.id: p for p in probes}, {p.id: {"outcome": 200} for p in probes})
    assert set(functional.values()) == {"pass"} and normal["succeeded"] is True


@pytest.mark.parametrize("exposed, expected", [(False, "UNKNOWN"), (True, "FAIL")])
def test_direct_security_pass_withheld_but_independent_violation_kept_if_inference_is_invalid(tmp_path, exposed, expected):
    from tests.test_direct_route import World, _run
    world = World(exposed=exposed)
    original = world.handle
    def handle(network, request):
        result = original(network, request)
        if request.get("id", "").startswith("func|"):
            result.update(outcome=200, body='{"error":"generation failed"}')
        return result
    world.handle = handle
    _, evd, res = _run(tmp_path, world)
    assert evd.normal_control.succeeded is False
    assert res.verdicts.security.value == expected
    assert res.verdicts.functional.value == "UNKNOWN"
