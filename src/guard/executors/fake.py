"""FakeExecutor: 미리 정한 값을 돌려주는 가짜 실행기. 엔진·네트워크에 접속하지 않는다.

산출물은 항상 data_origin: EXAMPLE이다(INV-02). 공식 판정 근거가 될 수 없다.
REQ-01(Gateway 우회 직통 요청)의 경로별 관측 시나리오:

- pass:                              직통 무키·오키 401, Gateway 허용 키 200, 등록 경로 전부 시험
- direct_200:                        /v1/completions 직통이 200 (위반 확인)
- not_attempted:                     챌린지를 하나도 보내지 않았는데 관측값만 "거부"처럼 보임 (INV-03)
- missing_obs:                       직통 결과를 수집하지 못함 (센서 장애, INV-04·11)
- refused_with_positive_control:     직통 전부 연결 거부 + 신뢰 출발점에서는 도달 → 차단 근거 인정
- refused_without_positive_control:  직통 전부 연결 거부, 양성 대조 실패 → 근거 불충분
- partial_routes:                    등록 경로 중 일부만 시험
- direct_200_on_invocations:         /v1은 거부되지만 /invocations 직통이 200
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from guard.models import CheckDef, DataOrigin, Environment, ExecStatus, utcnow

from .base import DraftArtifact, EvidenceDraft

ROUTES = ["/v1/models", "/v1/completions", "/invocations"]
FUNCTIONAL_PROBE = "POST /v1/completions"  # 가짜 증거의 기능 표본 이름(REQ-01 override와 같은 모양)


def _obs(value: Any) -> dict:
    return {"value": value, "collected": True}


def _scenario(direct: dict, trusted: bool = True, tested: list[str] | None = None, attempted: bool = True,
              missing: tuple[str, ...] = ()) -> dict:
    tested = ROUTES if tested is None else tested
    obs = {
        "routes_registered": _obs(ROUTES),
        "routes_tested": _obs(tested),
        "direct_outcomes": _obs({r: direct[r] for r in tested}),
        "gateway_outcomes": _obs({r: 200 for r in tested}),
        "trusted_origin_reachable": _obs(trusted),
        "functional_probes_planned": _obs([FUNCTIONAL_PROBE]),
        "functional_probe_results": _obs({FUNCTIONAL_PROBE: "pass"}),
    }
    for key in missing:
        obs[key] = {"value": None, "collected": False}
    return {"attempted": attempted, "observations": obs}


_401 = {r: 401 for r in ROUTES}
_REFUSED = {r: "refused" for r in ROUTES}

SCENARIOS: dict[str, dict[str, Any]] = {
    "pass": _scenario(_401),
    "direct_200": _scenario({**_401, "/v1/completions": 200}),
    "not_attempted": _scenario(_401, attempted=False),
    "missing_obs": _scenario(_401, missing=("direct_outcomes",)),
    "refused_with_positive_control": _scenario(_REFUSED, trusted=True),
    "refused_without_positive_control": _scenario(_REFUSED, trusted=False),
    "partial_routes": _scenario(_401, tested=["/v1/completions"]),
    "direct_200_on_invocations": _scenario({**_REFUSED, "/invocations": 200}, trusted=True),
}
SUPPORTED_CHECKS = {"REQ-01"}


class FakeExecutor:
    name = "fake"
    version = "0.2.0"

    def __init__(self, scenario: str = "pass"):
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown fake scenario {scenario!r}; choose from {sorted(SCENARIOS)}")
        self.scenario = scenario

    def run(self, check: CheckDef, env: Environment, policy_values: dict, adapter, workdir: Path) -> EvidenceDraft:
        if check.id not in SUPPORTED_CHECKS:
            raise ValueError(f"FakeExecutor has no scenario for {check.id}")
        spec = SCENARIOS[self.scenario]
        started = utcnow()
        challenges = [{"step": step, "attempted": spec["attempted"]} for step in check.challenge_steps]
        workdir.mkdir(parents=True, exist_ok=True)
        log = workdir / "probe_log.jsonl"
        lines = [{"example": True, "scenario": self.scenario, "observation": k, **v} for k, v in spec["observations"].items()]
        lines.append({"example": True, "scenario": self.scenario, "direct_allowed_outcomes": {r: 401 for r in ROUTES}})
        log.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
        return EvidenceDraft(
            status=ExecStatus.COMPLETED,
            data_origin=DataOrigin.EXAMPLE,
            normal_control={"attempted": True, "succeeded": True, "detail": "EXAMPLE: allowed key via gateway → 200"},
            challenges=challenges,
            observations=spec["observations"],
            artifacts=[DraftArtifact(name="probe_log.jsonl", path=log, collector=f"{self.name}/{self.version}")],
            config={"scenario": self.scenario, "policy_values": policy_values},
            started_at=started,
            finished_at=utcnow(),
        )
