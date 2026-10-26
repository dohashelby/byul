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

ROUTES = ["GET /v1/models", "POST /v1/completions", "POST /invocations"]  # "METHOD path" 키
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
    "direct_200": _scenario({**_401, "POST /v1/completions": 200}),
    "not_attempted": _scenario(_401, attempted=False),
    "missing_obs": _scenario(_401, missing=("direct_outcomes",)),
    "refused_with_positive_control": _scenario(_REFUSED, trusted=True),
    "refused_without_positive_control": _scenario(_REFUSED, trusted=False),
    "partial_routes": _scenario(_401, tested=["POST /v1/completions"]),
    "direct_200_on_invocations": _scenario({**_REFUSED, "POST /invocations": 200}, trusted=True),
}
# REQ-03(역할별 관리·진단 경로): 관리·진단 등록 경로, 비관리 역할 대표 결과
REQ03_ROUTES = ["POST /is_scaling_elastic_ep", "GET /kv_event_sources", "GET /load", "GET /metrics", "POST /scale_elastic_ep"]
REQ03_FUNCTIONAL = {"POST /v1/completions": "pass", "GET /metrics": "pass"}


def _req03(nonadmin: dict, tested: list[str] | None = None) -> dict:
    tested = REQ03_ROUTES if tested is None else tested
    return {"attempted": True, "observations": {
        "routes_registered": _obs(REQ03_ROUTES),
        "routes_tested": _obs(tested),
        "nonadmin_outcomes": _obs({r: nonadmin[r] for r in tested}),
        "functional_probes_planned": _obs(list(REQ03_FUNCTIONAL)),
        "functional_probe_results": _obs(dict(REQ03_FUNCTIONAL)),
    }}


_403 = {r: 403 for r in REQ03_ROUTES}
SCENARIOS_BY_CHECK: dict[str, dict[str, dict[str, Any]]] = {
    "REQ-01": SCENARIOS,
    "REQ-03": {
        "pass": _req03(_403),                                                   # Gateway가 비관리 역할을 전부 거부
        "role_open": _req03({**_403, "GET /metrics": 200, "GET /load": 200}),           # 일반 키로 진단 작업 실행 → FAIL
        "scale_handler_reached": _req03({**_403, "POST /scale_elastic_ep": "handler_reached"}),  # 안전 프로브가 핸들러 도달 → FAIL
        "mutating_untested": _req03(_403, tested=[r for r in REQ03_ROUTES if r != "POST /scale_elastic_ep"]),  # 미시험 → UNKNOWN
    },
}
# PLT-01(컨테이너 실행 권한). 실험실 정책 기준: pass·tmp_exec 외에는 위반
DEFAULT_CAPS = ["AUDIT_WRITE", "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "MKNOD", "NET_BIND_SERVICE",
                "NET_RAW", "SETFCAP", "SETGID", "SETPCAP", "SETUID", "SYS_CHROOT"]


def _plt01(caps=(), mounts=("/models:ro",), root=False, sentinel=None, exec_writable=("/guard-cache",)) -> dict:
    sentinel = {"/models": "denied", "/guard-sentinel": "absent"} if sentinel is None else sentinel
    return {"attempted": True, "observations": {
        "permission_scan_complete": _obs(True),
        "process_scan_complete": _obs(True),
        "capabilities_effective": _obs(sorted(caps)), "mounts": _obs(sorted(mounts)), "devices": _obs([]),
        "privileged": _obs(False), "host_namespaces": _obs([]), "root_uid_present": _obs(root),
        "sentinel_results": _obs(sentinel),
        "exec_writable_mounts": _obs(sorted(exec_writable)),
        "functional_probes_planned": _obs(["POST /v1/completions"]),
        "functional_probe_results": _obs({"POST /v1/completions": "pass"}),
    }}


SCENARIOS_BY_CHECK["PLT-01"] = {
    "pass": _plt01(),                                                     # hardened
    "overprivileged": _plt01(caps=DEFAULT_CAPS + ["SYS_ADMIN"], mounts=("/models:ro", "/guard-sentinel:rw"), root=True,
                             sentinel={"/models": "denied", "/guard-sentinel": "succeeded"},
                             exec_writable=("/", "/dev", "/guard-sentinel")),
    "default_caps": _plt01(caps=DEFAULT_CAPS, root=True, exec_writable=("/", "/dev")),
    "root_user": _plt01(root=True),
    "tmp_exec": _plt01(exec_writable=("/tmp",)),                          # /tmp 자체에 exec 허용(허용 목록 밖)
}
SUPPORTED_CHECKS = set(SCENARIOS_BY_CHECK)


class FakeExecutor:
    name = "fake"
    version = "0.2.1"

    def __init__(self, scenario: str = "pass", scenarios: dict[str, str] | None = None):
        """scenario: 기본 시나리오, scenarios: 점검별 시나리오(없으면 기본)."""
        if scenario not in SCENARIOS and not any(scenario in t for t in SCENARIOS_BY_CHECK.values()):
            raise ValueError(f"unknown fake scenario {scenario!r}")
        self.scenario = scenario
        self.scenarios = dict(scenarios or {})

    def run(self, check: CheckDef, env: Environment, policy_values: dict, adapter, workdir: Path) -> EvidenceDraft:
        if check.id not in SUPPORTED_CHECKS:
            raise ValueError(f"FakeExecutor has no scenario for {check.id}")
        name = self.scenarios.get(check.id, self.scenario)
        table = SCENARIOS_BY_CHECK[check.id]
        if name not in table:
            raise ValueError(f"FakeExecutor: no scenario {name!r} for {check.id}; choose from {sorted(table)}")
        spec = table[name]
        started = utcnow()
        challenges = [{"step": step, "attempted": spec["attempted"]} for step in check.challenge_steps]
        workdir.mkdir(parents=True, exist_ok=True)
        log = workdir / "probe_log.jsonl"
        lines = [{"example": True, "scenario": name, "observation": k, **v} for k, v in spec["observations"].items()]
        if check.id == "REQ-01":
            lines.append({"example": True, "scenario": name, "direct_allowed_outcomes": {r: 401 for r in ROUTES}})
        log.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
        return EvidenceDraft(
            status=ExecStatus.COMPLETED,
            data_origin=DataOrigin.EXAMPLE,
            normal_control={"attempted": True, "succeeded": True, "detail": "EXAMPLE: allowed key via gateway → 200"},
            challenges=challenges,
            observations=spec["observations"],
            artifacts=[DraftArtifact(name="probe_log.jsonl", path=log, collector=f"{self.name}/{self.version}")],
            config={"scenario": name, "policy_values": policy_values},
            started_at=started,
            finished_at=utcnow(),
        )
