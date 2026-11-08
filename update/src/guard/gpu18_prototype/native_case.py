"""SUP-16의 EXAMPLE 전용 메모리 생성·회수·소비 모형.

실제 빌드, 바이너리/모델/플러그인 로드, 파일 접근, 프로세스 및 네트워크
호출은 없다. ``authenticated``는 이 모형의 신원·회수 일치만 뜻한다.
실제 native producer 인증이나 기존 AWS 실증을 대신하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CHECK_IDS: tuple[str, ...] = ("SUP-16",)


def _digest(value: bytes | dict) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


_BUILD_IDENTITY = {
    "simulation": True,
    "source": "prototype-native-source-v1",
    "compiler": "simulated-builder-v1",
    "abi": "simulated-abi-v1",
    "format": "in-memory-text-fixture",
}
_COMPLETE_BYTES = b"SIMULATION ONLY: complete native artifact; never executable.\n"
_COMPLETE_SHA = _digest(_COMPLETE_BYTES)
_SOURCE_SHA = _digest(_BUILD_IDENTITY)

POLICY_VALUES: dict = {
    "SUP-16": {
        "supply_environment_sha256": _digest({"simulation": True, "backend": "memory"}),
        "supply_normal_target_sha256": _digest({"simulation": True, "role": "normal"}),
        "supply_challenge_target_sha256": _digest({"simulation": True, "role": "challenge"}),
        "supply_normal_prepared_sha256": _digest({"simulation": True, "prepared": "complete"}),
        "supply_challenge_prepared_sha256": _digest({"simulation": True, "prepared": "interrupted"}),
        "supply_record_source_sha256": _SOURCE_SHA,
        # 모의 시계의 창이며 실제 환경의 승인·현재 유효기간이 아니다.
        "supply_validity_start": "2000-01-01T00:00:00Z",
        "supply_validity_end": "2000-01-02T00:00:00Z",
    }
}

INTEGRATION_REQUIRED: dict[str, str] = {
    "SUP-16": (
        "실제 download/conversion/compile producer의 source·toolchain/ABI·설정 신원, "
        "완료 표식/원자적 공개 계약, 중단 지점의 원 bytes·manifest·정확 SHA와 회수 receipt, "
        "각 normal/challenge worker·boot/session 신원 및 native FILE_READ 소비 원자료가 필요하다. "
        "이를 실제 ENV/target/prepared/POL 유효창과 기존 supply_boot producer→consumer에 결합해야 한다. "
        "이 모형은 메모리 상태만 실행하며 실제 빌드/회수/인증/worker admission을 검증하지 않는다. "
        "기존 GB09/GB11·FAIL/UNKNOWN·GPU 실증 및 비용 장부는 변경하지 않는다."
    )
}


def _event(events: list[dict], action: str, **details: object) -> None:
    events.append({
        "simulation": True,
        "data_origin": "EXAMPLE",
        "policy_status": "DRAFT",
        "official": False,
        "sequence": len(events),
        "action": action,
        **details,
    })


def _produce(role: str, *, interrupted: bool, events: list[dict]) -> dict:
    payload = _COMPLETE_BYTES[: len(_COMPLETE_BYTES) // 2] if interrupted else _COMPLETE_BYTES
    state = {"role": role, "payload": b"", "state": "writing", "complete": False}
    _event(events, "producer_begin", role=role, identity_sha256=_SOURCE_SHA)
    state["payload"] = payload
    state["state"] = "interrupted" if interrupted else "complete"
    state["complete"] = not interrupted
    _event(events, "producer_terminal", role=role, state=state["state"],
           bytes=len(payload), sha256=_digest(payload), complete_marker=state["complete"])
    return state


def _recover(artifact: dict, events: list[dict]) -> tuple[bytes, bool]:
    """조각 전송과 회수 신원 비교만 실행하며 ZIP/실제 경로를 열지 않는다."""
    payload = artifact["payload"]
    chunks = [payload[offset:offset + 11] for offset in range(0, len(payload), 11)]
    recovered = b"".join(chunks)
    identity = dict(_BUILD_IDENTITY)
    verified = len(recovered) == len(payload) and _digest(recovered) == _digest(payload)
    verified = verified and _digest(identity) == _SOURCE_SHA
    _event(events, "transport_recovered", role=artifact["role"], chunks=len(chunks),
           bytes=len(recovered), sha256=_digest(recovered), identity_sha256=_digest(identity),
           verified=verified, authentication_scope="simulation identity/checksum comparison only")
    return recovered, verified


def _consume(artifact: dict, recovered: bytes, *, enforce_completion: bool,
             observed: bool, events: list[dict]) -> dict | None:
    complete = artifact["complete"] and artifact["state"] == "complete"
    digest_matches = _digest(recovered) == _COMPLETE_SHA
    accepted = (complete and digest_matches) or not enforce_completion
    # 실제 코드를 로드하지 않고 소비 길이/해시만 계산한다.
    consumed = recovered if accepted else b""
    row = {
        "simulation": True, "role": artifact["role"],
        "producer_state": artifact["state"], "complete_marker": complete,
        "completion_check_enforced": enforce_completion,
        "expected_sha256": _COMPLETE_SHA, "actual_sha256": _digest(recovered),
        "artifact_bytes": len(recovered), "consumed": accepted,
        "read_bytes": len(consumed), "read_sha256": _digest(consumed) if accepted else None,
        "complete_bytes_match": digest_matches,
    }
    _event(events, "consumer_transition", **row, observation_delivered=observed)
    if not observed:
        _event(events, "observer_dropped_consumption", role=artifact["role"])
        return None
    return row


def simulate(check_id: str, scenario: str, workdir: Path) -> dict:
    """normal/violation/missing을 실제 메모리 상태 전이로 수행한다.

    workdir는 공용 runner 인터페이스이며 이 모듈은 읽거나 생성하지 않는다.
    missing에서도 생산·회수·소비는 실행하되 challenge 소비 관측을 전달하지 않는다.
    판정은 반환하지 않으며, 기존 CheckDef/judge가 관측을 판정한다.
    """
    if check_id not in CHECK_IDS:
        raise ValueError(f"unsupported check_id: {check_id}")
    if scenario not in ("normal", "violation", "missing"):
        raise ValueError(f"unsupported scenario: {scenario}")
    if not isinstance(workdir, Path):
        raise TypeError("workdir must be pathlib.Path")

    events: list[dict] = []
    normal = _produce("normal", interrupted=False, events=events)
    normal_bytes, normal_recovered = _recover(normal, events)
    normal_read = _consume(normal, normal_bytes, enforce_completion=True, observed=True, events=events)
    normal_verified = bool(normal_read and normal_read["consumed"]
                           and normal_read["complete_bytes_match"] and normal_recovered)

    challenge = _produce("challenge", interrupted=True, events=events)
    partial_bytes, challenge_recovered = _recover(challenge, events)
    challenge_read = _consume(challenge, partial_bytes,
                              enforce_completion=scenario != "violation",
                              observed=scenario != "missing", events=events)
    # 거부/오류 이후 완성본을 다시 생성하는 안전한 복구 경로도 모형에서 실행한다.
    recovery = _produce("recovery", interrupted=False, events=events)
    recovery_bytes, recovery_verified = _recover(recovery, events)
    recovery_read = _consume(recovery, recovery_bytes, enforce_completion=True, observed=True, events=events)
    reads = [row for row in (normal_read, challenge_read, recovery_read) if row is not None]
    violations = [
        {"simulation": True, "reason": "partial_artifact_consumed", "role": row["role"],
         "read_bytes": row["read_bytes"], "read_sha256": row["read_sha256"]}
        for row in reads
        if row["consumed"] and (not row["complete_marker"] or not row["complete_bytes_match"])
    ]
    missing = [] if challenge_read is not None else ["challenge_native_consumption_unobserved"]
    change = {"simulation": True, "kind": "partial_artifact_interruption",
              "applied": challenge["state"] == "interrupted" and _digest(partial_bytes) != _COMPLETE_SHA,
              "partial_sha256": _digest(partial_bytes), "complete_sha256": _COMPLETE_SHA}
    sessions = [
        {"simulation": True, "role": item["role"], "state": item["state"],
         "complete_marker": item["complete"], "artifact_sha256": _digest(item["payload"])}
        for item in (normal, challenge, recovery)
    ]
    values = {
        "supply_boot_sessions": sessions,
        "supply_boot_violations": violations,
        "supply_boot_missing": missing,
        "supply_boot_complete": not missing and recovery_verified and bool(recovery_read["consumed"]),
        "supply_boot_normal_verified": normal_verified,
        "supply_boot_change_applied": change["applied"],
        "supply_boot_record_authenticated": normal_recovered and challenge_recovered and recovery_verified,
        "supply_boot_native_consumption": reads,
        "supply_boot_change": change,
    }
    observations = {key: {"collected": True, "value": value} for key, value in values.items()}
    if missing:
        observations["supply_boot_native_consumption"] = {"collected": False, "value": None}
    return {
        "observations": observations,
        "normal_control": {"attempted": True, "succeeded": normal_verified,
                           "note": "EXAMPLE 메모리 완성본의 신원·회수 SHA·완료 표식·소비 일치만 확인; 실제 native 실행/인증 아님."},
        "events": events,
    }
