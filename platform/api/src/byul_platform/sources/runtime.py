"""runtime 조회 API 읽기 전용 어댑터.

- runtime에는 GET만 보낸다. 추론 요청(POST /v1/chat/completions)은 보내지 않는다.
- runtime 내부 형식은 이 파일에서만 다룬다. 밖으로는 계약 형식(runtime-event,
  runtime-status, revalidation-request)과 그 native 원본에서 꺼낸 값만 내보낸다.
- 변환 규칙은 contracts/README.md의 "변환 규칙" 표를 그대로 따른다.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from ..contracts import canonical_sha256

EVENT_SCHEMA = "byul-runtime-event/0.2"
STATUS_SCHEMA = "byul-runtime-status/0.2"
REVALIDATION_SCHEMA = "byul-revalidation-request/0.2"
LAB_SCHEMA = "byul-runtime-lab-report/0.1"
DETAIL_SCHEMA = "byul-runtime-request-detail/0.1"

_LOCAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PHASES = {"loading", "compiling", "inferring", "maintenance", "unknown"}

# contracts README "runtime data_origin → 등급"
_MEASURED = {"real", "external_backend", "kubernetes_api", "local_file"}
_DECLARED = {"declared"}
_EXAMPLE = {"mock", "synthetic", "actual_test", "fake_backend_test"}

# runtime /api/summary 결과 키 → 계약 outcome
SUMMARY_RESULT_TO_OUTCOME = {
    "ALLOWED": "allowed",
    "DENIED": "denied",
    "INVALID": "invalid",
    "ENGINE_REJECTED": "engine_rejected",
    "ERROR": "error",
}


class RuntimeUnavailable(RuntimeError):
    """runtime에 연결하지 못했거나 runtime이 오류를 돌려줌."""


class RuntimeUnauthorized(RuntimeError):
    """조회 토큰이 없거나 거부됨."""


class RuntimeNotFound(LookupError):
    pass


# --- 등급·참조 --------------------------------------------------------------


def grade(origin: object) -> str:
    """runtime data_origin → 계약 등급. 모르는 값은 공식 근거가 될 수 없으므로 EXAMPLE로 둔다."""
    if origin in _MEASURED:
        return "MEASURED"
    if origin in _DECLARED:
        return "DECLARED"
    if origin in _EXAMPLE:
        return "EXAMPLE"
    return "EXAMPLE"


def _local_id(value: object) -> str | None:
    return value if isinstance(value, str) and _LOCAL_ID.fullmatch(value) else None


def _policy_ref(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    pid, version, sha = value.get("policy_id"), value.get("policy_version"), value.get("sha256")
    if isinstance(pid, str) and isinstance(version, str) and isinstance(sha, str) and _HEX64.fullmatch(sha):
        return {"policy_id": pid, "policy_version": version, "sha256": sha}
    return None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


# --- Gateway 판정 → runtime-event ----------------------------------------------


def gateway_outcome(record: dict[str, Any]) -> str:
    """contracts README 'runtime Gateway 판정 → outcome' 표."""
    if record.get("phase") != "final":
        return "unknown"
    decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
    delivery = record.get("delivery") if isinstance(record.get("delivery"), dict) else {}
    result = decision.get("result")
    if result == "ALLOWED":
        return "allowed"
    if result == "DENIED":
        return "denied" if delivery.get("attempted") is False else "error"
    if result == "INVALID":
        return "invalid"
    if result == "ENGINE_REJECTED":
        return "engine_rejected"
    if result == "ERROR":
        return "error"
    return "unknown"


def pick_gateway_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """요청 하나의 기록 중 final이 있으면 마지막 final, 없으면 마지막 started."""
    finals = [r for r in records if isinstance(r, dict) and r.get("phase") == "final"]
    if finals:
        return finals[-1]
    started = [r for r in records if isinstance(r, dict) and r.get("phase") == "started"]
    return started[-1] if started else None


def gateway_event(record: dict[str, Any]) -> dict[str, Any]:
    decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
    target = record.get("target") if isinstance(record.get("target"), dict) else {}
    outcome = gateway_outcome(record)
    request_id = record.get("request_id")
    rule_ref = _str_or_none(decision.get("rule_id"))
    if rule_ref is None and outcome not in ("allowed", "unknown"):
        rule_ref = _str_or_none(decision.get("reason_code"))
    origin = record.get("data_origin")
    return {
        "schema_version": EVENT_SCHEMA,
        "event_id": f"req:{request_id}",
        "occurred_at": record.get("recorded_at"),
        "recorded_at": record.get("recorded_at"),
        "source": "gateway",
        "category": "request",
        "outcome": outcome,
        "enforcer": "gateway",
        "rule_ref": rule_ref,
        "phase": None,
        "target_ref": _local_id(record.get("target_id")),
        "target_instance_id": _str_or_none(target.get("instance_id")),
        "policy_ref": _policy_ref(record.get("policy")),
        "scope": {
            "environment": _str_or_none(record.get("environment")),
            "run_id": _str_or_none(record.get("run_id")),
            "observation_session_id": _str_or_none(record.get("observation_session_id")),
            "request_id": request_id if isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id) else None,
        },
        "related_checks": [],
        "data_origin": grade(origin),
        "origin_detail": _str_or_none(origin),
        "evidence_refs": [],
        "observation": None,
        "native": {"schema_version": _str_or_none(record.get("schema_version")), "record": record},
    }


# --- 관측 → runtime-event ---------------------------------------------------------

_TETRAGON_OBSERVED = {"allowed_in_phase", "exception"}
_TETRAGON_DETECTED = {"outside_allowlist", "suspect_binary", "exec_from_cache"}
_TETRAGON_UNKNOWN = {"phase_unknown", "phase_signal_unverified"}


def observation_outcome(record: dict[str, Any]) -> str:
    """contracts README 'runtime 관측 → outcome' 표. 표에 없는 경우는 unknown(미확인)."""
    source = record.get("source")
    if source == "hubble":
        return "observed"  # dropped 포함. Hubble은 거부 확인이 아니다.
    if source == "tetragon":
        if _action_mode(record) == "enforce":
            return "enforce_attempted"
        if record.get("kind") == "file_access":
            if _action_mode(record) != "monitor":
                return "unknown"
            rule_id = record.get("rule_id")
            if rule_id in {"TG-ASSET-01", "TG-ASSET-02", "TG-SECRET-01"}:
                return "detected"
            if rule_id == "TG-CACHE-01":
                generation = record.get("cache_generation")
                if generation == "current":
                    return "observed"
                if generation in {"other", "outside"}:
                    return "detected"
            return "unknown"
        verdict = record.get("exec_verdict")
        if verdict in _TETRAGON_OBSERVED:
            return "observed"
        if verdict in _TETRAGON_UNKNOWN:
            return "unknown"
        if verdict in _TETRAGON_DETECTED and _action_mode(record) == "monitor":
            return "detected"
    return "unknown"


def _action_mode(record: dict[str, Any]) -> str | None:
    """runtime 화면과 같은 규칙: action_mode가 없으면 원본 action 값으로 판단한다."""
    mode = record.get("action_mode")
    if mode in ("monitor", "enforce"):
        return mode
    raw = str(record.get("action_raw") or record.get("action") or "").upper()
    if raw == "KPROBE_ACTION_OVERRIDE":
        return "enforce"
    if raw in ("KPROBE_ACTION_POST", "OBSERVED"):
        return "monitor"
    return "unknown" if raw else None


def _observation_category(record: dict[str, Any]) -> str:
    if record.get("source") == "hubble":
        return "network"
    kind = str(record.get("kind") or "")
    return "process" if "exec" in kind else "file"


def _phases(record: dict[str, Any]) -> list[str]:
    phase = record.get("phase")
    if isinstance(phase, list):
        return [p for p in phase if isinstance(p, str)]
    return [phase] if isinstance(phase, str) else []


def _pod_parts(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, dict):
        return _str_or_none(value.get("namespace")), _str_or_none(value.get("pod"))
    return None, _str_or_none(value)


def observation_detail(record: dict[str, Any]) -> dict[str, Any]:
    """runtime-event.observation: 화면 표에 쓰는 관측 값(native.record에서 옮김)."""
    src = record.get("src") if isinstance(record.get("src"), dict) else {}
    dst = record.get("dst") if isinstance(record.get("dst"), dict) else {}
    pod_ns, pod_name = _pod_parts(record.get("pod"))
    port = dst.get("port", record.get("dst_port"))
    allowed = record.get("allowed_binary")
    return {
        "kind": str(record.get("kind") or ""),
        "verdict": _str_or_none(record.get("verdict")),
        "reason": _str_or_none(record.get("reason")) or _str_or_none(record.get("drop_reason")),
        "direction": _str_or_none(record.get("traffic_direction")),
        "src_namespace": _str_or_none(src.get("namespace")),
        "src_pod": _str_or_none(src.get("pod")) or _str_or_none(record.get("src_pod")),
        "dst_namespace": _str_or_none(dst.get("namespace")),
        "dst_pod": _str_or_none(dst.get("pod")),
        "dst_ip": _str_or_none(dst.get("ip")) or _str_or_none(record.get("dst_ip")),
        "dst_port": port if isinstance(port, int) and not isinstance(port, bool) else None,
        "protocol": _str_or_none(dst.get("protocol")) or _str_or_none(record.get("protocol")),
        "binary": _str_or_none(record.get("binary")),
        "path": _str_or_none(record.get("path")),
        "access": _str_or_none(record.get("access")),
        "pod_namespace": pod_ns,
        "pod_name": pod_name,
        "pod_attribution": _str_or_none(record.get("pod_attribution")),
        "process_attribution": _str_or_none(record.get("process_attribution")),
        "rule_id": _str_or_none(record.get("rule_id")),
        "action_raw": _str_or_none(record.get("action_raw")) or _str_or_none(record.get("action")),
        "action_mode": _action_mode(record) if record.get("source") == "tetragon" else None,
        "phases": _phases(record),
        "exec_verdict": _str_or_none(record.get("exec_verdict")),
        "exception_id": _str_or_none(record.get("exception_id")),
        "cache_generation": _str_or_none(record.get("cache_generation")),
        "allowed_binary": allowed if isinstance(allowed, bool) else None,
    }


def observation_event(record: dict[str, Any]) -> dict[str, Any]:
    source = record.get("source")
    event_key = record.get("event_key")
    if isinstance(event_key, str) and _HEX64.fullmatch(event_key):
        event_id = f"feed:{event_key}"
    else:
        event_id = f"sha:{canonical_sha256(record)}"
    rule_refs = record.get("rule_refs")
    rule_ref = None
    if source == "tetragon":
        if isinstance(rule_refs, list) and rule_refs and isinstance(rule_refs[0], str):
            rule_ref = rule_refs[0]
        else:
            rule_ref = _str_or_none(record.get("rule_id")) or _str_or_none(record.get("policy"))
    phases = _phases(record)
    # 사건의 단계는 하나일 때만 둔다. 여러 단계가 겹치면 null(목록은 observation.phases).
    phase = phases[0] if len(phases) == 1 and phases[0] in _PHASES else None
    origin = record.get("data_origin")
    return {
        "schema_version": EVENT_SCHEMA,
        "event_id": event_id,
        "occurred_at": record.get("observed_at"),
        # 관측 기록에는 따로 기록 시각이 없다. 관측 시각을 쓰고, 없으면 검증에서 형식 오류로 드러난다.
        "recorded_at": record.get("observed_at"),
        "source": source,
        "category": _observation_category(record),
        "outcome": observation_outcome(record),
        "enforcer": "tetragon" if source == "tetragon" else None,
        "rule_ref": rule_ref,
        "phase": phase,
        "target_ref": _local_id(record.get("target_id")),
        "target_instance_id": None,
        "policy_ref": None,
        "scope": {
            "environment": _str_or_none(record.get("environment")),
            "run_id": _str_or_none(record.get("run_id")),
            "observation_session_id": None,
            "request_id": None,
        },
        "related_checks": [],
        "data_origin": grade(origin),
        "origin_detail": _str_or_none(origin),
        "evidence_refs": [],
        "observation": observation_detail(record),
        "native": {"schema_version": _str_or_none(record.get("schema_version") or record.get("schema")), "record": record},
    }


# --- 요청 표 한 행 ----------------------------------------------------------------


def request_row(event: dict[str, Any]) -> dict[str, Any]:
    """계약 runtime-event(source=gateway)의 native.record에서 표에 쓸 값을 꺼낸다."""
    record = event.get("native", {}).get("record", {})
    decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
    request = record.get("request") if isinstance(record.get("request"), dict) else {}
    delivery = record.get("delivery") if isinstance(record.get("delivery"), dict) else {}
    scope = event.get("scope", {})

    def _int(v: object) -> int | None:
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    def _bool(v: object) -> bool | None:
        return v if isinstance(v, bool) else None

    return {
        "event_id": event["event_id"],
        "request_id": scope.get("request_id") or event["event_id"].removeprefix("req:"),
        "recorded_at": event.get("recorded_at"),
        "outcome": event["outcome"],
        "decision_result": _str_or_none(decision.get("result")),
        "model": _str_or_none(request.get("model")),
        "max_tokens": _int(request.get("max_tokens")),
        "reason_code": _str_or_none(decision.get("reason_code")),
        "rule_id": _str_or_none(decision.get("rule_id")),
        "http_status": _int(decision.get("http_status")),
        "engine_http_status": _int(decision.get("engine_http_status")),
        "stream": _bool(record.get("stream")),
        "delivery_attempted": _bool(delivery.get("attempted")),
        "backend_kind": _str_or_none(record.get("backend_kind")),
        "environment": scope.get("environment"),
        "run_id": scope.get("run_id"),
        "target_ref": event.get("target_ref"),
        "observation_session_id": scope.get("observation_session_id"),
        "data_origin": event["data_origin"],
        "origin_detail": event.get("origin_detail"),
    }


# --- 상태 → runtime-status -------------------------------------------------------

_APPROVAL_STATES = {"not_configured", "verified", "last_verified", "unverified", "rejected", "error"}
_COLLECTOR_STATES = {"disconnected", "never", "ok", "partial", "stale", "error"}


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _num(value: Any) -> float | int | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _nonneg(value: Any) -> int:
    v = _int(value)
    return v if v is not None and v >= 0 else 0


def _store_state(value: Any) -> dict[str, Any]:
    v = _d(value)
    state = v.get("state")
    if state is None and isinstance(v.get("ok"), bool):
        state = "ok" if v["ok"] else "error"
    return {"state": _str_or_none(state), "last_error": _str_or_none(v.get("last_error"))}


def _line_issues(items: Any, source: str) -> list[dict[str, Any]]:
    return [
        {"source": source, "line": _int(i.get("line")), "reason": str(i.get("reason"))}
        for i in _l(items) if isinstance(i, dict)
    ]


def _collector_detail(item: dict[str, Any]) -> dict[str, Any] | None:
    source = str(item.get("source") or "")
    value = item.get("last_value")
    if not isinstance(value, dict):
        return None
    if source.startswith("observation_feed_"):
        writer = _d(value.get("writer"))
        gap = writer.get("last_gap")
        return {
            "kind": "observation_feed",
            "processed": _nonneg(value.get("processed")),
            "accepted": _nonneg(value.get("accepted")),
            "duplicates": _nonneg(value.get("duplicates")),
            "rejected": _nonneg(value.get("rejected")),
            "rejected_details": _line_issues(value.get("rejected_details"), source),
            "corrupt_lines_total": _nonneg(value.get("corrupt_lines_total")),
            "out_of_window": _nonneg(value.get("out_of_window")),
            "out_of_window_total": _nonneg(value.get("out_of_window_total")),
            "idle": value.get("idle") is True,
            "lost_events_total": _int(value.get("lost_events_total")),
            "lost_events_by_source": (
                {
                    str(name): _nonneg(count)
                    for name, count in value["lost_events_by_source"].items()
                }
                if isinstance(value.get("lost_events_by_source"), dict)
                else None
            ),
            "last_lost_at": _str_or_none(value.get("last_lost_at")),
            "loss_detected": (
                value.get("loss_detected")
                if isinstance(value.get("loss_detected"), bool)
                else None
            ),
            "writer": {
                "configured": writer.get("configured") is True,
                "state": _str_or_none(writer.get("state")),
                "updated_at": _str_or_none(writer.get("updated_at")),
                "reconnect_count": _int(writer.get("reconnect_count")),
                "last_gap": {"opened_at": _str_or_none(gap.get("opened_at")), "closed_at": _str_or_none(gap.get("closed_at"))}
                if isinstance(gap, dict) else None,
            },
        }
    if source == "record_retention":
        files = _d(value.get("files"))
        limits = _d(value.get("limits"))
        holds = _d(value.get("holds"))
        last = value.get("last_deletion")
        return {
            "kind": "record_retention",
            "mode": str(value.get("mode") or ""),
            "max_age_days": _num(limits.get("max_age_days")),
            "max_bytes": _int(limits.get("max_bytes")),
            "total_bytes": _int(value.get("total_bytes")),
            "over_size_limit": value.get("over_size_limit") is True,
            "removable_lines": sum(_nonneg(_d(f).get("removable_lines")) for f in files.values()),
            "holds_state": _str_or_none(holds.get("state")),
            "holds_error": _str_or_none(holds.get("error")),
            "errors": [str(e) for e in _l(value.get("errors"))],
            "last_deletion": {
                "ran_at": _str_or_none(last.get("ran_at")),
                "files": [
                    {
                        "name": str(name),
                        "removed_lines": _nonneg(_d(f).get("removed_lines")),
                        "from": _str_or_none(_d(_d(f).get("removable_range")).get("from")),
                        "to": _str_or_none(_d(_d(f).get("removable_range")).get("to")),
                        "durability": _str_or_none(_d(f).get("durability")),
                    }
                    for name, f in _d(last.get("files")).items()
                ],
            } if isinstance(last, dict) else None,
        }
    if source == "metrics":
        fields = []
        for name, field in value.items():
            if not isinstance(field, dict) or not field.get("state"):
                continue
            reset = _d(field.get("counter_reset"))
            last_reset = _d(reset.get("last_detected"))
            fields.append({
                "name": str(name),
                "state": str(field["state"]),
                "declared_type": _str_or_none(field.get("declared_type")),
                "counter_reset_now": reset.get("detected_this_poll") is True,
                "last_counter_reset_at": _str_or_none(last_reset.get("detected_at")),
            })
        return {"kind": "metrics", "fields": fields}
    if source == "asset_integrity":
        counts = _d(value.get("counts"))
        return {
            "kind": "asset_integrity",
            "intact": _bool(value.get("intact")),
            "total": _int(value.get("total")),
            **{k: _nonneg(counts.get(k)) for k in ("match", "changed", "missing", "unreadable", "symlink")},
            "extra_files": _int(value.get("extra_file_count")),
            "extra_symlinks": _int(value.get("extra_symlink_count")),
            "walk_errors": _int(value.get("walk_error_count")),
            "limit": _str_or_none(value.get("limit")),
        }
    if source == "cilium_policy":
        policies = []
        for pol in _l(value.get("policies")):
            if not isinstance(pol, dict):
                continue
            nodes = _d(pol.get("node_status"))
            policies.append({
                "name": str(pol.get("name")),
                "status_present": pol.get("status_present") is True,
                "node_total": _nonneg(nodes.get("total")),
                "node_enforcing": _nonneg(nodes.get("enforcing")),
                "node_errors": _nonneg(nodes.get("errors")),
                "last_updated": _str_or_none(nodes.get("last_updated")),
                "conditions": [
                    {"type": _str_or_none(c.get("type")), "status": _str_or_none(c.get("status")), "reason": _str_or_none(c.get("reason"))}
                    for c in _l(pol.get("conditions")) if isinstance(c, dict)
                ],
            })
        return {"kind": "cilium_policy", "object_count": _nonneg(value.get("object_count")), "policies": policies}
    return None


def runtime_status(
    status: dict[str, Any],
    policy: dict[str, Any],
    collectors: dict[str, Any],
    target: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    revalidation: dict[str, Any] | None = None,
    observations: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    st_target = _d(status.get("target"))
    instance = _d(st_target.get("instance"))
    current = _d(instance.get("current"))
    target = _d(target)
    history = _l(target.get("history"))
    last_hist = _d(history[0]) if history else None
    st_policy = _d(status.get("policy"))
    approval = _d(st_policy.get("approval"))
    approval_state = approval.get("state")
    current_policy = _d(policy.get("current"))
    summary = _d(summary)
    record_store = _d(summary.get("record_store")) or _d(status.get("record_store"))
    corrupt = _d(summary.get("corrupt_lines"))
    corrupt_details = _d(summary.get("corrupt_details"))
    backend = _d(status.get("mock_engine")) or _d(status.get("backend"))
    auth = _d(status.get("auth"))
    revalidation = _d(revalidation)
    observations = _d(observations)
    obs_counts = _d(observations.get("counts"))
    hold = st_target.get("gateway_hold")
    extra = [_d(x) for x in _l(st_policy.get("extra_fields"))]
    counts = _d(st_target.get("counts"))
    collector_items = []
    for item in _l(collectors.get("collectors")):
        if not isinstance(item, dict):
            continue
        state = item.get("state")
        origin = item.get("data_origin")
        collector_items.append({
            "source": str(item.get("source")),
            "state": state if state in _COLLECTOR_STATES else "error",
            "last_success_at": item.get("last_success_at"),
            "last_attempt_at": item.get("last_attempt_at"),
            "last_error": _str_or_none(item.get("last_error")),
            "interval_s": _num(item.get("interval_s")),
            "stale_after_s": _num(item.get("stale_after_s")),
            "data_origin": grade(origin) if origin is not None else None,
            "origin_detail": _str_or_none(origin),
            "detail": _collector_detail(item),
        })
    store_state = record_store.get("state")
    return {
        "schema_version": STATUS_SCHEMA,
        "generated_at": collectors.get("generated_at") or policy.get("evaluated_at"),
        "observation_session_id": _str_or_none(status.get("observation_session_id")),
        "environment": _str_or_none(status.get("environment")),
        "run_id": _str_or_none(status.get("run_id")),
        "overall": "ok" if status.get("status") == "ok" else "degraded",
        "target": {
            "target_ref": _local_id(st_target.get("target_ref")) or _local_id(st_target.get("target_id")),
            "target_id": _str_or_none(st_target.get("target_id")),
            "registration": str(st_target.get("registration") or "unknown"),
            "error_code": _str_or_none(st_target.get("error_code")),
            "instance_id": _str_or_none(current.get("instance_id")),
            "instance_state": str(instance.get("state") or "unknown"),
            "instance_observed_at": _str_or_none(instance.get("observed_at")),
            "instance_pod": _str_or_none(current.get("pod")),
            "instance_restart_count": _int(current.get("restart_count")),
            "gateway_hold": {"reason_code": str(hold.get("reason_code")), "message": _str_or_none(hold.get("message"))}
            if isinstance(hold, dict) else None,
            "comparison_counts": {k: _nonneg(counts.get(k)) for k in ("match", "mismatch", "unknown", "not_declared")},
            "last_history": {
                "event": str(last_hist.get("event")),
                "reason": _str_or_none(last_hist.get("reason")),
                "recorded_at": _str_or_none(last_hist.get("recorded_at")),
            } if last_hist else None,
        },
        "policy": {
            "policy_ref": _policy_ref(st_policy),
            # runtime은 번들 ID를 아직 기록하지 않는다(정책 번들 배포 미구현).
            "bundle_id": None,
            "loaded": st_policy.get("loaded") is True,
            "gateway_active": st_policy.get("gateway_active") is True,
            "approval_state": approval_state if approval_state in _APPROVAL_STATES else "error",
            "valid_until": current_policy.get("valid_until"),
            "schema": _str_or_none(st_policy.get("schema_version")),
            "profile": _str_or_none(st_policy.get("profile")),
            "test_only": st_policy.get("test_only") is True,
            "max_n": _int(st_policy.get("max_n")),
            "passthrough_fields": [str(x.get("name")) for x in extra if x.get("mode") == "passthrough"],
            "validate_count": _int(st_policy.get("validate_count")),
            "loaded_at": _str_or_none(st_policy.get("loaded_at")),
            "load_valid": st_policy.get("valid") is True,
            "error_code": _str_or_none(st_policy.get("error_code")),
            "error_message": _str_or_none(st_policy.get("error_message")),
            "external_enforcement": str(st_policy.get("external_enforcement") or "unknown"),
            "approval_checked_at": _str_or_none(approval.get("checked_at")),
            "approval_reason_code": _str_or_none(approval.get("reason_code")),
            "validity_items": [
                {"item": str(v.get("item")), "state": _str_or_none(v.get("state")), "result": _str_or_none(v.get("result"))}
                for v in _l(policy.get("validity")) if isinstance(v, dict)
            ],
            "history": [
                {
                    "at": _str_or_none(h.get("at")),
                    "event": str(h.get("event")),
                    "policy_id": _str_or_none(h.get("policy_id")),
                    "policy_version": _str_or_none(h.get("policy_version")),
                    "sha256": h.get("sha256") if isinstance(h.get("sha256"), str) and _HEX64.fullmatch(h["sha256"]) else None,
                    "previous_version": _str_or_none(_d(h.get("previous")).get("policy_version")),
                    "same_version_content_changed": h.get("same_version_content_changed") is True,
                }
                for h in _l(policy.get("history")) if isinstance(h, dict)
            ],
            "history_store": _store_state(policy.get("history_storage")),
        },
        "backend": {
            "kind": str(status.get("backend_kind") or "unknown"),
            "mode": str(status.get("mode") or "unknown"),
            "url": _str_or_none(backend.get("url")),
            "healthy": _bool(backend.get("healthy")),
            "reachable": _bool(backend.get("reachable")),
            "inference_verified": _bool(backend.get("inference_verified")),
            "checked_at": _str_or_none(backend.get("checked_at")),
            "error": _str_or_none(backend.get("error")),
            "credential": _str_or_none(backend.get("credential")),
            "metrics_credential": _str_or_none(backend.get("metrics_credential")),
            "receipts_store": _store_state(backend.get("receipts_store")),
        },
        "concurrency": {k: _int(_d(status.get("concurrency")).get(k)) for k in ("in_flight", "limit")},
        "connections": {
            "active": _int(_d(status.get("connections")).get("active")),
            "limit": _int(_d(status.get("connections")).get("limit")),
            "rejected": _int(_d(status.get("connections")).get("rejected")),
            "request_header_timeout_s": _num(_d(status.get("connections")).get("request_header_timeout_s")),
            "request_body_timeout_s": _num(_d(status.get("connections")).get("request_body_timeout_s")),
        },
        "auth": {
            "state": str(auth.get("state") or "unknown"),
            "method": _str_or_none(auth.get("method")),
            "principal_count": _int(auth.get("principal_count")),
            "protected_routes": [str(r) for r in _l(auth.get("protected_routes"))],
            "error_code": _str_or_none(auth.get("error_code")),
        },
        "collectors": collector_items,
        "store": {
            "state": store_state if store_state in ("ok", "error") else "unknown",
            "data_dir": _str_or_none(record_store.get("data_dir")),
            "last_error": _str_or_none(record_store.get("last_error")),
            "last_error_at": _str_or_none(record_store.get("last_error_at")),
            "corrupt_lines": {"gateway": _nonneg(corrupt.get("gateway")), "engine": _nonneg(corrupt.get("engine"))},
            "corrupt_details": _line_issues(corrupt_details.get("gateway"), "gateway") + _line_issues(corrupt_details.get("engine"), "engine"),
        },
        "revalidation": {
            "open_count": _nonneg(revalidation.get("open_count")),
            "current_condition_count": _nonneg(revalidation.get("current_condition_count")),
            "corrupt_lines": len(_l(revalidation.get("corrupt_lines"))),
            "evaluation_errors": len(_l(revalidation.get("evaluation_errors"))),
        },
        "comparisons": comparisons(_d(baseline), target),
        "observations": {
            "by_source": {str(k): _nonneg(v) for k, v in _d(obs_counts.get("source")).items()},
            "corrupt_lines": _nonneg(observations.get("corrupt_lines")),
            "real_hubble_dropped": _nonneg(_d(observations.get("verification")).get("real_hubble_dropped")),
            "origins_by_kind": {
                str(kind): {str(o): _nonneg(n) for o, n in _d(origins).items()}
                for kind, origins in _d(observations.get("origins_by_kind")).items()
            },
        },
    }


def _scalar(value: Any) -> Any:
    return value if value is None or isinstance(value, (str, int, float, bool)) else json.dumps(value, ensure_ascii=False)


def _comparison_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {
            "field": str(row.get("field")),
            "expected": _scalar(row.get("expected")),
            "observed": _scalar(row.get("observed")),
            "source": _str_or_none(row.get("source")),
            "state": str(row.get("state") or "unknown"),
            "observed_at": _str_or_none(row.get("observed_at")),
            "note": _str_or_none(row.get("note")),
        }
        for row in rows if isinstance(row, dict)
    ]


def comparisons(baseline: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    base_meta = _d(baseline.get("baseline"))
    reg = _d(target.get("registration"))
    notes_base = [baseline["model_id_limit"]] if isinstance(baseline.get("model_id_limit"), str) else []
    notes_target = [x for x in _l(target.get("limits")) if isinstance(x, str)]
    return {
        "baseline": {"state": _str_or_none(base_meta.get("state")), "rows": _comparison_rows(_l(baseline.get("rows"))), "notes": notes_base},
        "target": {"state": _str_or_none(reg.get("state")), "rows": _comparison_rows(_l(target.get("comparison"))), "notes": notes_target},
    }


# --- 재검증 → revalidation-request ------------------------------------------------


def _changed_fields(trigger: str, evidence: dict[str, Any]) -> list[dict[str, Any]]:
    def scalar(v: object) -> Any:
        return v if v is None or isinstance(v, (str, int, float, bool)) else json.dumps(v, ensure_ascii=False)

    if trigger == "config_drift":
        return [{"field": str(evidence.get("field")), "expected": scalar(evidence.get("expected")), "observed": scalar(evidence.get("observed"))}]
    if trigger == "policy_content_changed":
        prev = evidence.get("previous") if isinstance(evidence.get("previous"), dict) else {}
        cur = evidence.get("current") if isinstance(evidence.get("current"), dict) else {}
        return [{"field": "policy.sha256", "expected": scalar(prev.get("sha256")), "observed": scalar(cur.get("sha256"))}]
    if trigger == "asset_changed":
        out: list[dict[str, Any]] = []
        paths = evidence.get("paths") if isinstance(evidence.get("paths"), dict) else {}
        for state, items in sorted(paths.items()):
            for path in items if isinstance(items, list) else []:
                # 자산은 경로와 상태만 있다. 기대 해시는 runtime 근거에 없으므로 null.
                out.append({"field": f"asset:{path}", "expected": None, "observed": str(state)})
        if isinstance(evidence.get("manifest_sha256"), str):
            out.append({"field": "asset.manifest_sha256", "expected": evidence["manifest_sha256"], "observed": None})
        return out
    return []


def revalidation_request(item: dict[str, Any]) -> dict[str, Any]:
    trigger = item.get("trigger")
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    origin = item.get("data_origin")
    return {
        "schema_version": REVALIDATION_SCHEMA,
        "revalidation_id": item.get("revalidation_id"),
        "fingerprint": item.get("fingerprint"),
        "event": item.get("last_event"),
        "created_at": item.get("updated_at") or item.get("opened_at"),
        "trigger": trigger,
        "target_ref": _local_id(item.get("target_id")),
        "policy_ref": _policy_ref(item.get("policy")),
        "changed_fields": _changed_fields(str(trigger), evidence),
        "evaluation_refs": [],
        "evidence_refs": [],
        "superseded_by": _str_or_none(item.get("superseded_by")),
        "status": item.get("status") if item.get("status") in ("open", "closed") else "open",
        "data_origin": grade(origin),
        "origin_detail": _str_or_none(origin),
        "opened_at": _str_or_none(item.get("opened_at")),
        "current_evidence_present": _bool(item.get("current_evidence_present")),
    }


# --- 요청 상세 → runtime-request-detail ---------------------------------------------

_EVIDENCE_STATES = {"received", "not_received", "unknown", "gateway_only"}


def request_detail_doc(detail: dict[str, Any]) -> dict[str, Any]:
    records = [r for r in _l(detail.get("gateway_records")) if isinstance(r, dict)]
    final = pick_gateway_record(records) or {}
    decision = _d(final.get("decision"))
    request = _d(final.get("request"))
    context = request.get("context")
    auth = _d(final.get("auth"))
    policy = _d(final.get("policy"))
    delivery = _d(final.get("delivery"))
    receipts = [r for r in _l(detail.get("engine_receipts")) if isinstance(r, dict)]
    live = _d(detail.get("engine_live"))
    corrupt = _d(detail.get("corrupt_lines"))
    request_id = str(detail.get("request_id") or final.get("request_id"))
    state = detail.get("evidence_state")
    origin = final.get("data_origin")
    return {
        "schema_version": DETAIL_SCHEMA,
        "request_id": request_id,
        "event_id": f"req:{request_id}",
        "outcome": gateway_outcome(final) if final else "unknown",
        "environment": _str_or_none(final.get("environment")),
        "run_id": _str_or_none(final.get("run_id")),
        "backend_kind": _str_or_none(detail.get("backend_kind")) or _str_or_none(final.get("backend_kind")),
        "decision": {
            "result": _str_or_none(decision.get("result")),
            "reason_code": _str_or_none(decision.get("reason_code")),
            "message": _str_or_none(decision.get("message")),
            "rule_id": _str_or_none(decision.get("rule_id")),
            "http_status": _int(decision.get("http_status")),
            "engine_http_status": _int(decision.get("engine_http_status")),
        },
        "request": {
            "model": _str_or_none(request.get("model")),
            "max_tokens": _int(request.get("max_tokens")),
            "n": _int(request.get("n")),
            "extra_fields": [
                {"name": str(x.get("name")), "mode": _str_or_none(x.get("mode"))}
                for x in _l(request.get("extra_fields")) if isinstance(x, dict)
            ],
            "context": {
                "result": _str_or_none(context.get("result")),
                "input_tokens": _int(context.get("input_tokens")),
                "limit": _int(context.get("limit")),
            } if isinstance(context, dict) else None,
        },
        "auth": {
            "state": _str_or_none(auth.get("state")),
            "principal_id": _str_or_none(auth.get("principal_id")),
            "roles": [str(r) for r in _l(auth.get("roles"))],
        },
        "policy": {k: _str_or_none(policy.get(k)) for k in ("policy_id", "policy_version", "profile", "sha256")},
        "delivery": {
            "attempted": _bool(delivery.get("attempted")),
            "backend_receipt": _str_or_none(delivery.get("backend_receipt")),
            "response": _str_or_none(delivery.get("response")),
            "error_code": _str_or_none(delivery.get("error_code")),
        },
        "stream": _bool(final.get("stream")),
        "http_start_status": _int(final.get("http_start_status")),
        "final_outcome": _str_or_none(final.get("final_outcome")),
        "backend_cancel": _str_or_none(final.get("backend_cancel")),
        "timeline": [{"phase": _str_or_none(r.get("phase")), "recorded_at": _str_or_none(r.get("recorded_at"))} for r in records],
        "evidence_state": state if state in _EVIDENCE_STATES else None,
        "evidence_summary": _str_or_none(detail.get("evidence_summary")),
        "engine_receipts": {
            "count": len(receipts),
            "first_received_at": _str_or_none(receipts[0].get("received_at")) if receipts else None,
        },
        "engine_live": {"status": _str_or_none(live.get("status")), "queried_at": _str_or_none(live.get("queried_at"))},
        "same_observation_session": _bool(detail.get("same_observation_session")),
        "corrupt_lines": {"gateway": _int(corrupt.get("gateway")), "engine": _int(corrupt.get("engine"))},
        "data_origin": grade(origin),
        "origin_detail": _str_or_none(origin),
        "native": {"gateway_records": records, "engine_receipts": receipts},
    }


# --- lab 시험 보고서 → runtime-lab-report -------------------------------------------


def lab_report_doc(payload: dict[str, Any]) -> dict[str, Any]:
    report = payload.get("report")
    state = payload.get("state")
    return {
        "schema_version": LAB_SCHEMA,
        "generated_at": payload.get("generated_at"),
        "state": state if state in ("not_configured", "missing", "error", "ok") else "error",
        "configured_path": _str_or_none(payload.get("configured_path")),
        "path": _str_or_none(payload.get("path")),
        "error": _str_or_none(payload.get("error")),
        "report": {
            "run_id": str(report.get("run_id")),
            "context": str(report.get("context")),
            "namespace": str(report.get("namespace")),
            "started_at": _str_or_none(report.get("started_at")),
            "finished_at": _str_or_none(report.get("finished_at")),
            "overall": str(report.get("overall")),
            "cases": [
                {
                    "id": str(c.get("id")),
                    "title": str(c.get("title")),
                    "status": str(c.get("status")),
                    "summary": str(c.get("summary")),
                    "evidence": [str(e) for e in _l(c.get("evidence"))],
                    "limitation": _str_or_none(c.get("limitation")),
                    "note": _str_or_none(c.get("note")),
                }
                for c in _l(report.get("cases")) if isinstance(c, dict)
            ],
        } if isinstance(report, dict) else None,
    }


# --- 출처 인터페이스 ----------------------------------------------------------------


@dataclass
class EventBatch:
    candidates: list[Any]
    truncated: bool = False


@dataclass
class DetailResult:
    event: dict[str, Any] | None
    detail: dict[str, Any] | None  # runtime-request-detail 후보(검증 전)


@dataclass
class Summary:
    counts: dict[str, int]
    total: int
    corrupt_lines: int | None
    generated_at: str | None


class RuntimeSource(Protocol):
    mode: str
    detail: str | None

    def events(self) -> EventBatch: ...
    def request_detail(self, request_id: str) -> DetailResult: ...
    def request_evidence(self, request_id: str) -> bytes: ...
    def summary(self) -> Summary: ...
    def status(self) -> Any: ...
    def lab_report(self) -> Any: ...
    def revalidation(self) -> list[Any]: ...
    def ping(self) -> str: ...


def summary_from_events(events: Iterable[dict[str, Any]]) -> Summary:
    counts = {k: 0 for k in ("allowed", "denied", "invalid", "engine_rejected", "error", "unknown")}
    for e in events:
        if isinstance(e, dict) and e.get("source") == "gateway" and e.get("outcome") in counts:
            counts[e["outcome"]] += 1
    return Summary(counts=counts, total=sum(counts.values()), corrupt_lines=None, generated_at=None)


class ExampleRuntimeSource:
    """예시 폴더(contracts/examples 또는 데모 자료)를 읽는 예시 모드. 전부 EXAMPLE 등급."""

    mode = "example"

    def __init__(self, examples_dir: Path) -> None:
        self.dir = examples_dir
        self.detail = str(examples_dir)

    def _json(self, name: str, default: Any = None) -> Any:
        path = self.dir / name
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _events(self) -> list[Any]:
        lines = (self.dir / "runtime-events.jsonl").read_text(encoding="utf-8").splitlines()
        out: list[Any] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as error:
                out.append({"_unparsed": line, "_error": str(error)})
        return out

    def events(self) -> EventBatch:
        return EventBatch(self._events())

    def _details(self) -> list[Any]:
        data = self._json("runtime-request-detail.json", [])
        return data if isinstance(data, list) else [data]

    def request_detail(self, request_id: str) -> DetailResult:
        event = next((e for e in self._events() if isinstance(e, dict) and e.get("event_id") == f"req:{request_id}"), None)
        detail = next((d for d in self._details() if isinstance(d, dict) and d.get("request_id") == request_id), None)
        if event is None and detail is None:
            raise RuntimeNotFound(request_id)
        return DetailResult(event=event, detail=detail)

    def request_evidence(self, request_id: str) -> bytes:
        detail = self.request_detail(request_id).detail
        if not isinstance(detail, dict):
            raise RuntimeNotFound(request_id)
        # 예시 모드에는 runtime 증거 파일이 없으므로 상세의 원본을 그대로 내려준다.
        return json.dumps({"example": True, "request_id": request_id, **detail.get("native", {})}, ensure_ascii=False, indent=2).encode("utf-8")

    def summary(self) -> Summary:
        return summary_from_events(self._events())

    def status(self) -> Any:
        return self._json("runtime-status.json")

    def lab_report(self) -> Any:
        doc = self._json("runtime-lab-report.json")
        if doc is not None:
            return doc
        return {"schema_version": LAB_SCHEMA, "generated_at": "2026-10-25T00:00:00Z", "state": "not_configured",
                "configured_path": None, "path": None, "error": None, "report": None}

    def revalidation(self) -> list[Any]:
        data = self._json("revalidation-request.json", [])
        return data if isinstance(data, list) else [data]

    def ping(self) -> str:
        return "example"


class RuntimeClient:
    """runtime 조회 API 클라이언트. GET 외의 메서드는 보내기 전에 막는다."""

    ALLOWED_METHODS = frozenset({"GET"})

    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 5.0,
    ) -> None:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            event_hooks={"request": [self._guard]},
        )

    @classmethod
    def _guard(cls, request: httpx.Request) -> None:
        if request.method not in cls.ALLOWED_METHODS:
            raise PermissionError(f"runtime에는 GET만 보낸다: {request.method} {request.url.path}")
        if not request.url.path.startswith("/api/"):
            raise PermissionError(f"runtime 조회 API 밖 경로는 부르지 않는다: {request.url.path}")

    def close(self) -> None:
        self._http.close()

    def _response(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        try:
            response = self._http.get(path, params=params)
        except httpx.HTTPError as error:
            # 오류 문자열에 헤더가 섞이지 않게 종류만 남긴다.
            raise RuntimeUnavailable(type(error).__name__) from None
        if response.status_code in (401, 403):
            raise RuntimeUnauthorized(str(response.status_code))
        if response.status_code == 404:
            raise RuntimeNotFound(path)
        if response.status_code >= 400:
            raise RuntimeUnavailable(f"HTTP {response.status_code}")
        return response

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._response(path, params)
        try:
            return response.json()
        except ValueError:
            raise RuntimeUnavailable("invalid JSON") from None

    def get_bytes(self, path: str) -> bytes:
        return self._response(path).content


class _TTLCache:
    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._lock = threading.Lock()
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, load: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        value = load()
        with self._lock:
            self._data[key] = (now, value)
        return value


class LiveRuntimeSource:
    mode = "live"
    REQUEST_LIMIT = 200

    def __init__(self, client: RuntimeClient, base_url: str, cache_ttl: float = 2.5) -> None:
        self.client = client
        self.detail = base_url
        self._cache = _TTLCache(cache_ttl)
        # final 기록이 있는 요청 상세는 바뀌지 않으므로 다시 묻지 않는다(3초 갱신 부하를 줄임).
        self._final_details: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        key = path + "?" + json.dumps(params or {}, sort_keys=True)
        return self._cache.get(key, lambda: self.client.get(path, params))

    def _detail(self, request_id: str) -> Any:
        with self._lock:
            hit = self._final_details.get(request_id)
        if hit is not None:
            return hit
        detail = self._get(f"/api/requests/{request_id}")
        records = detail.get("gateway_records", []) if isinstance(detail, dict) else []
        if any(isinstance(r, dict) and r.get("phase") == "final" for r in records):
            with self._lock:
                if len(self._final_details) > 5000:
                    self._final_details.clear()
                self._final_details[request_id] = detail
        return detail

    def _gateway_events(self) -> EventBatch:
        listed = self._get("/api/requests", {"limit": self.REQUEST_LIMIT})
        views = listed.get("requests", []) if isinstance(listed, dict) else []
        out: list[Any] = []
        for view in views:
            rid = view.get("request_id") if isinstance(view, dict) else None
            if not isinstance(rid, str) or not _REQUEST_ID.fullmatch(rid):
                out.append(view)
                continue
            try:
                detail = self._detail(rid)
            except RuntimeNotFound:
                continue
            record = pick_gateway_record(detail.get("gateway_records", []) if isinstance(detail, dict) else [])
            out.append(gateway_event(record) if record is not None else detail)
        return EventBatch(out, truncated=len(views) >= self.REQUEST_LIMIT)

    def _observation_events(self) -> list[Any]:
        out: list[Any] = []
        for source in ("hubble", "tetragon"):
            payload = self._get("/api/observations", {"source": source})
            for record in payload.get("observations", []) if isinstance(payload, dict) else []:
                out.append(observation_event(record) if isinstance(record, dict) else record)
        return out

    def events(self) -> EventBatch:
        gateway = self._gateway_events()
        return EventBatch(gateway.candidates + self._observation_events(), truncated=gateway.truncated)

    def request_detail(self, request_id: str) -> DetailResult:
        if not _REQUEST_ID.fullmatch(request_id):
            raise RuntimeNotFound(request_id)
        detail = self._detail(request_id)
        record = pick_gateway_record(detail.get("gateway_records", []))
        return DetailResult(
            event=gateway_event(record) if record is not None else None,
            detail=request_detail_doc(detail) if isinstance(detail, dict) else detail,
        )

    def request_evidence(self, request_id: str) -> bytes:
        if not _REQUEST_ID.fullmatch(request_id):
            raise RuntimeNotFound(request_id)
        return self.client.get_bytes(f"/api/requests/{request_id}/evidence")

    def summary(self) -> Summary:
        data = self._get("/api/summary")
        raw = data.get("counts", {}) if isinstance(data, dict) else {}
        counts = {v: int(raw.get(k, 0)) for k, v in SUMMARY_RESULT_TO_OUTCOME.items()}
        counts["unknown"] = int(data.get("incomplete", 0))
        corrupt = data.get("corrupt_lines")
        corrupt_total = sum(v for v in corrupt.values() if isinstance(v, int)) if isinstance(corrupt, dict) else None
        return Summary(
            counts=counts,
            total=int(data.get("total", sum(counts.values()))),
            corrupt_lines=corrupt_total,
            generated_at=_str_or_none(data.get("generated_at")),
        )

    def status(self) -> Any:
        try:
            target = self._get("/api/target")
        except RuntimeNotFound:
            target = None  # target 지원 전 Gateway
        return runtime_status(
            self._get("/api/status"),
            self._get("/api/policy"),
            self._get("/api/collectors"),
            target=target,
            summary=self._get("/api/summary"),
            revalidation=self._get("/api/revalidation"),
            observations=self._get("/api/observations"),
            baseline=self._get("/api/baseline"),
        )

    def lab_report(self) -> Any:
        return lab_report_doc(self._get("/api/lab-report"))

    def revalidation(self) -> list[Any]:
        data = self._get("/api/revalidation")
        items = data.get("requests", []) if isinstance(data, dict) else []
        return [revalidation_request(i) if isinstance(i, dict) else i for i in items]

    def ping(self) -> str:
        try:
            self.client.get("/api/summary")
        except RuntimeUnauthorized:
            return "unauthorized"
        except RuntimeUnavailable:
            return "disconnected"
        return "connected"
