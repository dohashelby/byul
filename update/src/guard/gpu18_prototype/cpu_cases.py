"""REQ-08/SUP-05의 EXAMPLE 전용 메모리 상태 모형.

실제 엔진, 파일 읽기 센서, 승인 권한 또는 기업 정책을 구현하지 않는다.
판정값은 반환하지 않는다. 기존 CheckDef/judge가 계산된 모의 관측을 판정한다.
workdir는 공유 인터페이스 인자이며 이 모듈은 파일·네트워크·프로세스를 사용하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CHECK_IDS: tuple[str, ...] = ("REQ-08", "SUP-05")
SCENARIOS = ("normal", "violation", "missing")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _example_identity(label: str) -> str:
    # 임시 상태 모형 식별자다. 실제 환경/target/prepared/소스 해시가 아니다.
    return _digest({"simulation": True, "prototype": "cpu_cases/1", "label": label})


POLICY_VALUES: dict = {
    "REQ-08": {
        "reused_token_field": "prompt_token_ids",
        "reused_token_allowed": False,
        "rejection_codes": ["400", "422"],
    },
    "SUP-05": {
        "supply_environment_sha256": _example_identity("environment"),
        "supply_normal_target_sha256": _example_identity("normal-target"),
        "supply_challenge_target_sha256": _example_identity("challenge-target"),
        "supply_normal_prepared_sha256": _example_identity("normal-prepared"),
        "supply_challenge_prepared_sha256": _example_identity("challenge-prepared"),
        "supply_record_source_sha256": _example_identity("mock-record-producer"),
        "supply_validity_start": "2026-11-08T00:00:00+00:00",
        "supply_validity_end": "2026-11-08T00:10:00+00:00",
    },
}

INTEGRATION_REQUIRED: dict[str, str] = {
    "REQ-08": (
        "선정 endpoint/필드/허용 주체·문맥과 권한 있는 실제 정책, 고정 ENV/source/image/model/argv, "
        "요청·attempt→renderer/InputProcessor→worker/model 최종 소비값 또는 사전 제거·거부의 실제 기록이 필요하다. "
        "허용 재사용 입력의 타입·vocab·길이·신원 검사는 별도 실제 연동 대상이다. 기존 CPU registry 진단은 "
        "재사용하되 과거 timeout 원인 해결이나 제품 소비 증거로 소급하지 않는다."
    ),
    "SUP-05": (
        "실제 eager FD read 이전에 활성·기록되는 loader/model causal-root 공통 producer/profile, "
        "고정 source/method/runtime/manifest 및 strict consumer 계약이 필요하다. 기존 seq29 읽기와 "
        "후속 seq125 root를 소급 결합하지 않는다. 공식 owned-Docker scope 지원 또는 실제 Kubernetes "
        "ENV/target·UID/GID·mount/subPath·PID/start/FD 귀속, 권한 있는 승인 inventory/정책, "
        "worker/rank 배정·tensor/decoder 소비와 정상/변경/정리 원자료를 각각 반환해야 한다. "
        "이 모형은 파일 race/symlink/mount/실제 센서 인증·제품 factory를 실행하거나 증명하지 않는다."
    ),
}


def _event(events: list[dict], check_id: str, scenario: str, stage: str, **facts) -> None:
    events.append({"sequence": len(events), "check_id": check_id, "scenario": scenario,
                   "stage": stage, "facts": facts, "simulation": True,
                   "data_origin": "EXAMPLE", "policy_status": "DRAFT", "official": False,
                   "backend": "in_memory_state_model"})


def _observation(value, *, collected: bool = True) -> dict:
    return {"collected": collected, "value": value if collected else None}


def _validate_tokens(tokens: object, vocab_size: int = 32) -> list[int]:
    if type(tokens) is not list or not 1 <= len(tokens) <= 16:
        raise ValueError("EXAMPLE_TOKEN_LENGTH_OR_CONTAINER_INVALID")
    if any(type(token) is not int or not 0 <= token < vocab_size for token in tokens):
        raise ValueError("EXAMPLE_TOKEN_TYPE_OR_VOCAB_INVALID")
    return list(tokens)


class _TokenEndpoint:
    """텍스트 renderer와 token consumer를 가진 안전한 모의 endpoint."""

    def __init__(self, events: list[dict], scenario: str, enforce_field_policy: bool):
        self.events = events
        self.scenario = scenario
        self.enforce_field_policy = enforce_field_policy
        self.calls = 0

    def request(self, request_id: str, text: str, reused: list[int] | None = None,
                *, receipt_available: bool = True) -> dict:
        field = POLICY_VALUES["REQ-08"]["reused_token_field"]
        allowed = POLICY_VALUES["REQ-08"]["reused_token_allowed"]
        _event(self.events, "REQ-08", self.scenario, "request_produced",
               request_id=request_id, message=text, reused_field=field,
               submitted_tokens=None if reused is None else list(reused))
        removed = reused is not None and not allowed and self.enforce_field_policy
        selected = None if removed else reused
        _event(self.events, "REQ-08", self.scenario, "field_policy_applied",
               request_id=request_id, field_removed=removed, allowed=allowed,
               control_enabled=self.enforce_field_policy)
        # 변환과 검사는 실제 Python 연산이며 모델/플러그인 코드를 실행하지 않는다.
        rendered = [byte % 32 for byte in text.encode("utf-8")] if selected is None else selected
        tokens = _validate_tokens(rendered)
        _event(self.events, "REQ-08", self.scenario, "input_checked",
               request_id=request_id, source="text" if selected is None else "reused_tokens",
               exact_integer_types=all(type(token) is int for token in tokens),
               vocab_size=32, length=len(tokens))
        self.calls += 1
        # 최종 소비 상태는 입력 token을 복사해 계산한다. 추론 모델 출력이 아니다.
        receipt = {"request_id": request_id, "consumed_tokens": list(tokens),
                   "checksum": sum(tokens), "simulation": True}
        if receipt_available:
            _event(self.events, "REQ-08", self.scenario, "mock_consumer_returned", **receipt)
        else:
            _event(self.events, "REQ-08", self.scenario, "consumer_receipt_missing",
                   request_id=request_id, consumer_called=True)
        return {"status": 200, "receipt": receipt if receipt_available else None}


def _req08(scenario: str) -> dict:
    events: list[dict] = []
    endpoint = _TokenEndpoint(events, scenario, enforce_field_policy=scenario != "violation")
    text, fixture = "hello", [11, 12, 13]
    before = endpoint.request("before", text)
    challenge = endpoint.request("challenge", text, fixture, receipt_available=scenario != "missing")
    after = endpoint.request("after", text)
    controls = {name: "pass" if reply["status"] == 200 and reply["receipt"] is not None else "fail"
                for name, reply in (("before", before), ("after", after))}
    receipt = challenge["receipt"]
    if receipt is None:
        state = "accepted_unverified"
    elif receipt["consumed_tokens"] == fixture:
        state = "consumed"
    elif receipt["consumed_tokens"] == before["receipt"]["consumed_tokens"]:
        state = "ignored"
    else:
        state = "accepted_unverified"
    forbidden = not POLICY_VALUES["REQ-08"]["reused_token_allowed"]
    violations = ([POLICY_VALUES["REQ-08"]["reused_token_field"]]
                  if forbidden and state == "consumed" else [])
    trial = {"field": POLICY_VALUES["REQ-08"]["reused_token_field"], "outcome": str(challenge["status"]),
             "state": state, "simulation": True, "request_fixture_sha256": _digest(fixture)}
    obs = {"reused_input_trials": _observation([trial]),
           "reused_input_state": _observation(state, collected=receipt is not None),
           "reused_input_forbidden": _observation(forbidden),
           "reused_input_violations": _observation(violations, collected=receipt is not None),
           "functional_probes_planned": _observation(list(controls)),
           "functional_probe_results": _observation(controls)}
    return {"observations": obs, "events": events,
            "normal_control": {"attempted": endpoint.calls >= 2,
                               "succeeded": all(value == "pass" for value in controls.values()),
                               "note": "EXAMPLE 메모리 endpoint의 변경 전후 정상 대조; 실제 API/모델 미실행"}}


class _SupplyModel:
    """메모리 binding만 바꾼다. 실제 파일·symlink·mount·권한은 없다."""

    def __init__(self, events: list[dict], scenario: str, enforce_integrity: bool):
        self.events = events
        self.scenario = scenario
        self.enforce_integrity = enforce_integrity
        self.objects = {"approved": b"EXAMPLE approved supply A", "replacement": b"EXAMPLE replacement supply B"}
        self.binding = "approved"
        self.approved_sha = hashlib.sha256(self.objects[self.binding]).hexdigest()
        self.calls = 0
        _event(events, "SUP-05", scenario, "mock_supply_approved", approved_sha256=self.approved_sha)

    def bind(self, key: str) -> bool:
        previous, self.binding = self.binding, key
        changed = self.binding != previous
        _event(self.events, "SUP-05", self.scenario, "memory_binding_changed",
               previous=previous, selected=key, changed=changed, actual_file_operation=False)
        return changed

    def consume(self, role: str, *, receipt_available: bool = True) -> dict | None:
        data = self.objects[self.binding]
        observed_sha = hashlib.sha256(data).hexdigest()
        matches = observed_sha == self.approved_sha
        blocked = self.enforce_integrity and not matches
        self.calls += 1
        receipt = {"role": role, "simulation": True, "source": "memory_binding",
                   "approved_sha256": self.approved_sha, "read_sha256": observed_sha,
                   "read_bytes": len(data), "consumed": not blocked, "blocked": blocked,
                   "matches_approval": matches, "native_sensor": False,
                   "producer_authenticated": False, "source_fd_verified": False}
        if receipt_available:
            _event(self.events, "SUP-05", self.scenario, "mock_supply_consumer_returned", **receipt)
        else:
            _event(self.events, "SUP-05", self.scenario, "consumer_receipt_missing",
                   role=role, consumer_called=True)
        return receipt if receipt_available else None


def _sup05(scenario: str) -> dict:
    events: list[dict] = []
    model = _SupplyModel(events, scenario, enforce_integrity=scenario != "violation")
    before = model.consume("normal")
    changed = model.bind("replacement")
    challenge = model.consume("challenge", receipt_available=scenario != "missing")
    model.bind("approved")
    after = model.consume("recovery-control")
    normal_ok = all(row is not None and row["consumed"] and row["matches_approval"]
                    for row in (before, after))
    violations = []
    if challenge is not None and challenge["consumed"] and not challenge["matches_approval"]:
        violations.append({"reason": "unapproved_simulated_supply_consumed", "simulation": True,
                           "approved_sha256": challenge["approved_sha256"],
                           "consumed_sha256": challenge["read_sha256"]})
    missing = ["simulation_challenge_consumer_receipt_unavailable"] if challenge is None else []
    complete = normal_ok and changed and challenge is not None and not missing
    sessions = [{"role": role, "simulation": True, "receipt": row}
                for role, row in (("normal", before), ("challenge", challenge), ("recovery-control", after))]
    change = {"kind": "in_memory_binding_substitution", "simulation": True,
              "applied": changed, "actor": "EXAMPLE:test-writer", "actual_file_operation": False}
    obs = {"supply_boot_sessions": _observation(sessions),
           "supply_boot_violations": _observation(violations),
           "supply_boot_missing": _observation(missing),
           "supply_boot_complete": _observation(complete),
           "supply_boot_normal_verified": _observation(normal_ok),
           "supply_boot_change_applied": _observation(changed),
           "supply_boot_record_authenticated": _observation(False),
           "supply_boot_native_consumption": _observation(
               [row for row in (before, challenge, after) if row is not None], collected=challenge is not None),
           "supply_boot_change": _observation(change)}
    return {"observations": obs, "events": events,
            "normal_control": {"attempted": model.calls >= 2, "succeeded": normal_ok,
                               "note": "EXAMPLE 승인 메모리 자료의 변경 전후 소비 대조; native/file/model 미실행"}}


def simulate(check_id: str, scenario: str, workdir: Path) -> dict:
    """격리된 상태 전이를 실행해 관측을 반환한다. workdir는 접근하지 않는다."""
    if check_id not in CHECK_IDS:
        raise ValueError(f"unsupported simulation check: {check_id!r}")
    if scenario not in SCENARIOS:
        raise ValueError(f"unsupported simulation scenario: {scenario!r}")
    if not isinstance(workdir, Path):
        raise TypeError("workdir must be pathlib.Path")
    return _req08(scenario) if check_id == "REQ-08" else _sup05(scenario)
