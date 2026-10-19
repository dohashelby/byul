"""FakeExecutor: 미리 정한 값을 돌려주는 가짜 실행기. 엔진·네트워크에 접속하지 않는다.

산출물은 항상 data_origin: EXAMPLE이다(INV-02). 공식 판정 근거가 될 수 없다.
REQ-01(Gateway 우회 직통 요청)의 4가지 증거를 만든다:

- pass:          정상 대조 성공, 챌린지 3개 시도, 직통 401 / Gateway 200
- direct_200:    직통이 200 응답 (위반 확인)
- not_attempted: 챌린지를 하나도 보내지 않았는데 관측값만 "거부"처럼 보임 (INV-03)
- missing_obs:   직통 응답 관측을 수집하지 못함 (센서 장애, INV-04·11)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from guard.models import CheckDef, DataOrigin, Environment, ExecStatus, utcnow

from .base import DraftArtifact, EvidenceDraft

_OBS_OK = {"direct_route_status": {"value": 401, "collected": True}, "gateway_route_status": {"value": 200, "collected": True}}

SCENARIOS: dict[str, dict[str, Any]] = {
    "pass": {"attempted": True, "observations": _OBS_OK},
    "direct_200": {
        "attempted": True,
        "observations": {**_OBS_OK, "direct_route_status": {"value": 200, "collected": True}},
    },
    "not_attempted": {"attempted": False, "observations": _OBS_OK},
    "missing_obs": {
        "attempted": True,
        "observations": {**_OBS_OK, "direct_route_status": {"value": None, "collected": False}},
    },
}
SUPPORTED_CHECKS = {"REQ-01"}


class FakeExecutor:
    name = "fake"
    version = "0.1.0"

    def __init__(self, scenario: str = "pass"):
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown fake scenario {scenario!r}; choose from {sorted(SCENARIOS)}")
        self.scenario = scenario

    def run(self, check: CheckDef, env: Environment, policy_values: dict, adapter, workdir: Path) -> EvidenceDraft:
        if check.id not in SUPPORTED_CHECKS:
            raise ValueError(f"FakeExecutor has no scenario for {check.id}")
        spec = SCENARIOS[self.scenario]
        started = utcnow()
        challenges = [
            {
                "step": step,
                "attempted": spec["attempted"],
                "observations": (
                    {"direct_route_status": spec["observations"]["direct_route_status"]["value"]}
                    if spec["attempted"] and i == 1
                    else {}
                ),
            }
            for i, step in enumerate(check.challenge_steps)
        ]
        workdir.mkdir(parents=True, exist_ok=True)
        log = workdir / "request_log.jsonl"
        lines = [
            {"example": True, "scenario": self.scenario, "route": "gateway", "status": spec["observations"]["gateway_route_status"]["value"]},
            {"example": True, "scenario": self.scenario, "route": "direct", "attempted": spec["attempted"],
             "status": spec["observations"]["direct_route_status"]["value"]},
        ]
        log.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
        return EvidenceDraft(
            status=ExecStatus.COMPLETED,
            data_origin=DataOrigin.EXAMPLE,
            normal_control={"attempted": True, "succeeded": True, "detail": "EXAMPLE: allowed key via gateway"},
            challenges=challenges,
            observations=spec["observations"],
            artifacts=[DraftArtifact(name="request_log.jsonl", path=log, collector=f"{self.name}/{self.version}")],
            config={"scenario": self.scenario, "policy_values": policy_values},
            started_at=started,
            finished_at=utcnow(),
        )
