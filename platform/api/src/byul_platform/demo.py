"""화면 확인용 가짜(데모) 자료 만들기.

contracts/examples와 같은 파일 구성으로, 그래프가 보일 만큼 많은 기록을 지금 시각 기준으로 만든다.
- 전부 data_origin EXAMPLE, origin_detail "synthetic"이다. 공식 근거가 될 수 없고 화면에도 "예시 데이터" 띠가 뜬다.
- runtime 사건은 runtime 내부 기록 모양을 먼저 만든 뒤 sources/runtime.py의 변환(계약 README 표)으로 바꾼다.
- 만든 문서는 모두 계약 스키마로 검증하고, 하나라도 틀리면 실패한다.

실행:
    uv run python -m byul_platform.demo ../.demo-data            # 지난 24시간, 요청 약 600건
    BYUL_EXAMPLES_DIR=../.demo-data uv run byul-platform
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONTRACTS_DIR
from .contracts import Contracts
from .sources.runtime import gateway_event, lab_report_doc, observation_event, request_detail_doc, runtime_status
from .sources.update import rpt_to_verification_result

TARGET = "mock-engine-001"
SESSION = "d3m0" + "0" * 28
# runtime이 읽는 runtime-policy.json bytes의 해시(contracts/examples/bundle과 같은 값)
RUNTIME_POLICY = {
    "schema_version": "runtime-mvp/1.1",
    "profile": "default",
    "policy_id": "local-policy-001",
    "policy_version": "3",
    "sha256": "55dfcfb1c3cfea1e9b602895535e071d570863f648ee410a7761eafb22573be9",
    "path": "bundle/runtime-policy.json",
}
ULID = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
AREAS = ("SUP", "REQ", "PLT", "LIF", "OBS")


def iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Gen:
    def __init__(self, seed: int, now: datetime, hours: int) -> None:
        self.r = random.Random(seed)
        self.now = now
        self.start = now - timedelta(hours=hours)
        self.hours = hours

    def hex(self, n: int) -> str:
        return "".join(self.r.choice("0123456789abcdef") for _ in range(n))

    def ulid(self, kind: str) -> str:
        return f"{kind}-" + "".join(self.r.choice(ULID) for _ in range(26))

    def ref(self, kind: str) -> dict[str, Any]:
        return {"kind": kind, "id": self.ulid(kind), "revision": 1, "sha256": self.hex(64)}

    def times(self, count: int, spikes: list[tuple[float, float]] = ()) -> list[datetime]:
        """하루 주기(낮에 많음) + 지정한 시점의 몰림으로 시각을 뽑는다."""
        out: list[datetime] = []
        span = (self.now - self.start).total_seconds()
        while len(out) < count:
            x = self.r.random()
            t = self.start + timedelta(seconds=x * span)
            hour = t.astimezone().hour + t.astimezone().minute / 60
            weight = 0.35 + 0.65 * (0.5 + 0.5 * math.sin((hour - 9) / 24 * 2 * math.pi))
            if self.r.random() < weight:
                out.append(t)
        for center, width in spikes:
            for _ in range(int(count * 0.08)):
                out.append(self.start + timedelta(seconds=span * min(max(self.r.gauss(center, width), 0), 1)))
        return sorted(out)


# --- runtime Gateway 기록 ------------------------------------------------------

_INVALID = [
    ("INVALID_MAX_TOKENS", 400),
    ("UNSUPPORTED_FIELD", 400),
    ("INVALID_STREAM", 400),
    ("BODY_TOO_LARGE", 413),
    ("UNSUPPORTED_MEDIA_TYPE", 415),
]
_DENIED = [("MODEL_NOT_ALLOWED", "allowed_models", 403), ("MAX_TOKENS_EXCEEDED", "output_max_tokens", 403), ("CONCURRENCY_LIMIT", "max_concurrency", 429)]
_ERROR = [("BACKEND_TIMEOUT", 504), ("BACKEND_UNREACHABLE", 502), ("ENGINE_AUTH_FAILED", 502)]


def gateway_record(g: Gen, at: datetime, kind: str, run_id: str | None) -> dict[str, Any]:
    r = g.r
    model = r.choices(["test-model", "other-model"], [0.92, 0.08])[0]
    max_tokens: int | None = r.choice([64, 128, 128, 128, 256, None])
    stream = r.random() < 0.3
    decision = {"result": "ALLOWED", "reason_code": "REQUEST_ALLOWED", "rule_id": None, "message": "request is allowed",
                "http_status": 200, "source": "gateway", "engine_http_status": None}
    delivery = {"attempted": True, "backend_receipt": "confirmed", "response": "completed", "error_code": None}
    phase = "final"
    if kind == "DENIED":
        code, rule, status = r.choice(_DENIED)
        if code == "MODEL_NOT_ALLOWED":
            model = "other-model"
        if code == "MAX_TOKENS_EXCEEDED":
            max_tokens = r.choice([512, 1024, 4096])
        decision.update(result="DENIED", reason_code=code, rule_id=rule, message="denied by policy", http_status=status)
        delivery = {"attempted": False, "backend_receipt": "not_queried", "response": "none", "error_code": None}
    elif kind == "INVALID":
        code, status = r.choice(_INVALID)
        if code in ("UNSUPPORTED_MEDIA_TYPE", "BODY_TOO_LARGE"):
            model, max_tokens = None, None
        decision.update(result="INVALID", reason_code=code, message="invalid request", http_status=status)
        delivery = {"attempted": False, "backend_receipt": "not_queried", "response": "none", "error_code": None}
    elif kind == "ENGINE_REJECTED":
        engine = r.choice([400, 429])
        decision.update(result="ENGINE_REJECTED", reason_code="ENGINE_REJECTED", message="engine rejected", http_status=engine, engine_http_status=engine)
        delivery = {"attempted": True, "backend_receipt": "confirmed", "response": "error", "error_code": f"ENGINE_{engine}"}
    elif kind == "ERROR":
        code, status = r.choice(_ERROR)
        decision.update(result="ERROR", reason_code=code, message="processing error", http_status=status,
                        engine_http_status=401 if code == "ENGINE_AUTH_FAILED" else None)
        delivery = {"attempted": True, "backend_receipt": "unknown", "response": "none", "error_code": code}
    elif kind == "INCOMPLETE":
        phase = "started"
        delivery = {"attempted": False, "backend_receipt": "not_queried", "response": "none", "error_code": None}
    return {
        "schema_version": "runtime-mvp-record/1.0",
        "phase": phase,
        "request_id": g.hex(32),
        "observation_session_id": SESSION,
        "environment": "demo",
        "run_id": run_id,
        "target_id": TARGET,
        "target": {"registration": "registered", "target_id": TARGET, "instance_id": "a1b2c3d4e5f60718", "instance_state": "running"},
        "recorded_at": iso(at),
        "policy": dict(RUNTIME_POLICY),
        "auth": {"state": "not_configured", "method": None, "principal_id": None, "roles": []},
        "decision": decision,
        "request": {
            "model": model,
            "max_tokens": max_tokens,
            "n": None,
            "cache_isolation": {"mode": "off", "applied": False},
            "extra_fields": [],
            "context": None,
        },
        "delivery": delivery,
        "stream": stream,
        "backend_kind": "mock",
        "data_origin": "synthetic",
    }


# --- 관측 기록 ------------------------------------------------------------------


# 관측 기록은 runtime enforcement/events.py가 만드는 정규화 형식(runtime-observation/1.0)과 같은 모양으로 만든다.
_POD_ENGINE = {"namespace": "serving-lab", "pod": "serving-engine-0"}


def hubble_record(g: Gen, at: datetime) -> dict[str, Any]:
    dropped = g.r.random() < 0.18
    return {
        "schema": "runtime-observation/1.0",
        "observed_at": iso(at),
        "source": "hubble",
        "kind": "network_flow",
        "data_origin": "synthetic",
        "environment": "kind-runtime-lab",
        "verdict": "dropped" if dropped else "allowed",
        "src": dict(_POD_ENGINE) if g.r.random() < 0.7 else {"namespace": "serving-lab", "pod": "other-client"},
        "dst": {
            "namespace": None if dropped else "serving-lab",
            "pod": None if dropped else "serving-gateway-0",
            "ip": g.r.choice(["10.0.3.17", "172.217.25.14", "10.96.0.10"]),
            "port": g.r.choice([443, 443, 53, 8080]),
            "protocol": g.r.choice(["TCP", "UDP"]),
        },
        "traffic_direction": "egress",
        "reason": "Policy denied" if dropped else None,
        "event_key": g.hex(64),
    }


def tetragon_record(g: Gen, at: datetime) -> dict[str, Any]:
    r = g.r
    base = {"schema": "runtime-observation/1.0", "observed_at": iso(at), "source": "tetragon", "data_origin": "synthetic",
            "environment": "kind-runtime-lab", "pod": dict(_POD_ENGINE), "pod_attribution": "available", "event_key": g.hex(64)}
    if r.random() < 0.3:
        enforce = r.random() < 0.4
        rule = r.choice(["TG-ASSET-01", "TG-SECRET-01", "TG-DENY-01", "TG-CACHE-01"])
        return {**base, "kind": "file_access",
                "binary": r.choice(["/usr/local/bin/vllm", None]),
                "process_attribution": "available" if r.random() < 0.6 else "unavailable",
                "path": r.choice(["/models/test-model/config.json", "/run/secrets/token", "/root/.cache/vllm/torch_compile/cache.bin"]),
                "access": r.choice(["read", "write"]), "rule_id": rule,
                "action": "override" if enforce else "post",
                "action_raw": "KPROBE_ACTION_OVERRIDE" if enforce else "KPROBE_ACTION_POST",
                "action_mode": "enforce" if enforce else "monitor",
                **({"cache_generation": r.choice(["current", "other", "outside"])} if rule == "TG-CACHE-01" else {})}
    verdict = r.choices(
        ["allowed_in_phase", "exception", "suspect_binary", "outside_allowlist", "exec_from_cache", "phase_unknown", "phase_signal_unverified"],
        [0.5, 0.08, 0.12, 0.12, 0.05, 0.08, 0.05],
    )[0]
    return {**base, "kind": "process_exec",
            "binary": r.choice(["/usr/bin/python3", "/bin/sh", "/usr/bin/curl", "/usr/local/bin/nvidia-smi", "/root/.cache/x/run"]),
            "process_attribution": "available", "path": None, "access": None, "rule_id": "TG-EXEC-01",
            "action": "observed", "action_raw": "observed", "action_mode": "monitor", "allowed_binary": verdict == "allowed_in_phase",
            "phase": r.choice([["loading"], ["compiling"], ["inferring"], ["inferring"], ["loading", "compiling"]]),
            "exec_verdict": verdict, "exception_id": "EXC-NVSMI-01" if verdict == "exception" else None,
            "rule_refs": ["SP3-EXEC", "CACHE-03"] if verdict == "exec_from_cache" else ["SP3-EXEC"]}


# --- runtime 조회 API 응답(원본 모양) → 계약 변환기로 만든다 ---------------------------


def native_payloads(g: Gen, records: list[dict[str, Any]], observations: list[dict[str, Any]]) -> dict[str, Any]:
    """runtime /api/status·/policy·/collectors·/target·/summary·/revalidation·/observations·/baseline 응답 모양."""
    finals = [r for r in records if r["phase"] == "final"]
    counts = {k: sum(1 for r in finals if r["decision"]["result"] == k) for k in ("ALLOWED", "DENIED", "INVALID", "ENGINE_REJECTED", "ERROR")}
    sha = RUNTIME_POLICY["sha256"]
    feed = lambda kind, n: {"source": f"observation_feed_{kind}", "state": "ok" if kind == "hubble" else "stale",
        "interval_s": 2.0, "stale_after_s": 30.0, "last_attempt_at": iso(g.now - timedelta(seconds=2)),
        "last_success_at": iso(g.now - timedelta(seconds=2 if kind == "hubble" else 1100)),
        "last_error": None if kind == "hubble" else "observation writer heartbeat is stale (age 1100s)", "data_origin": "synthetic",
        "last_value": {"processed": n, "accepted": n - 2, "duplicates": 1, "rejected": 1,
                       "rejected_details": [{"line": 412, "reason": "$.flow.l4 is missing"}], "corrupt_lines_total": 1,
                       "out_of_window": 0, "out_of_window_total": 3, "idle": False,
                       "writer": {"configured": True, "state": "running", "updated_at": iso(g.now - timedelta(seconds=3)),
                                  "reconnect_count": 2 if kind == "hubble" else 0,
                                  "last_gap": {"opened_at": iso(g.now - timedelta(hours=5)), "closed_at": iso(g.now - timedelta(hours=5) + timedelta(seconds=40))} if kind == "hubble" else None}}}
    collectors = [
        {"source": "engine_health", "state": "ok", "interval_s": 5.0, "stale_after_s": 15.0, "last_attempt_at": iso(g.now), "last_success_at": iso(g.now),
         "last_error": None, "data_origin": "mock", "last_value": {"status": "ok"}},
        {"source": "engine_info", "state": "ok", "interval_s": 5.0, "stale_after_s": 15.0, "last_attempt_at": iso(g.now), "last_success_at": iso(g.now),
         "last_error": None, "data_origin": "mock", "last_value": {"engine.kind": "mock", "engine.version": "mock-1.0"}},
        {"source": "metrics", "state": "ok", "interval_s": 5.0, "stale_after_s": 15.0, "last_attempt_at": iso(g.now), "last_success_at": iso(g.now),
         "last_error": None, "data_origin": "mock", "last_value": {
             "requests_running": {"state": "ok", "declared_type": "gauge", "counter_reset": {}},
             "prompt_tokens_total": {"state": "ok", "declared_type": "counter", "counter_reset": {"detected_this_poll": False, "last_detected": {"detected_at": iso(g.now - timedelta(hours=9))}}},
             "kv_cache_usage": {"state": "unsupported_or_absent", "declared_type": None, "counter_reset": {}}}},
        {"source": "record_retention", "state": "ok", "interval_s": 3600.0, "stale_after_s": 10800.0, "last_attempt_at": iso(g.now - timedelta(minutes=20)),
         "last_success_at": iso(g.now - timedelta(minutes=20)), "last_error": None, "data_origin": "local_file", "last_value": {
             "mode": "report_only", "limits": {"max_age_days": 7.0, "max_bytes": 5368709120}, "total_bytes": 1843200,
             "over_size_limit": False, "holds": {"state": "not_configured"}, "errors": [],
             "files": {"gateway_records.jsonl": {"removable_lines": 0}}, "last_deletion": None}},
        {"source": "asset_integrity", "state": "ok", "interval_s": 60.0, "stale_after_s": 180.0, "last_attempt_at": iso(g.now), "last_success_at": iso(g.now),
         "last_error": None, "data_origin": "local_file", "last_value": {
             "intact": False, "total": 12, "counts": {"match": 10, "changed": 1, "missing": 0, "unreadable": 0, "symlink": 0},
             "extra_file_count": 1, "extra_symlink_count": 0, "walk_error_count": 0, "limit": "해시 일치는 엔진이 실제로 메모리에 올린 바이트를 증명하지 않음"}},
        feed("hubble", 162), feed("tetragon", 81),
        {"source": "cilium_policy", "state": "ok", "interval_s": 30.0, "stale_after_s": 90.0, "last_attempt_at": iso(g.now - timedelta(seconds=8)),
         "last_success_at": iso(g.now - timedelta(seconds=8)), "last_error": None, "data_origin": "kubernetes_api", "last_value": {
             "object_count": 3, "policies": [
                 {"name": "engine-ingress-from-gateway", "status_present": True, "node_status": {"total": 1, "enforcing": 1, "errors": 0, "last_updated": iso(g.now - timedelta(hours=6))},
                  "conditions": [{"type": "Valid", "status": "True", "reason": None}]},
                 {"name": "engine-egress-dns-only", "status_present": True, "node_status": {"total": 1, "enforcing": 1, "errors": 0, "last_updated": iso(g.now - timedelta(hours=6))},
                  "conditions": [{"type": "Valid", "status": "True", "reason": None}]},
                 {"name": "gateway-egress-engine", "status_present": False, "node_status": {}, "conditions": []}]}},
    ]
    real_dropped = 0  # 데모는 합성 자료라 실제 DROPPED는 0
    return {
        "status": {"status": "degraded", "observation_session_id": SESSION, "environment": "demo", "run_id": None,
                   "backend_kind": "mock", "mode": "mock_engine",
                   "cache_isolation": {"mode": "off", "ready": True, "unavailable_reason_code": None},
                   "auth": {"state": "ok", "method": "bearer", "principal_count": 3, "protected_routes": ["GET /api/*", "POST /v1/chat/completions"], "error_code": None},
                   "target": {"registration": "ok", "target_id": TARGET, "target_ref": TARGET, "error_code": None, "gateway_hold": None,
                              "counts": {"match": 3, "mismatch": 1, "unknown": 1, "not_declared": 1},
                              "instance": {"state": "ok", "observed_at": iso(g.now - timedelta(seconds=10)),
                                           "current": {"instance_id": "a1b2c3d4e5f60718", "pod": "serving-engine-0", "restart_count": 1}}},
                   "policy": {"schema_version": RUNTIME_POLICY["schema_version"], "policy_id": RUNTIME_POLICY["policy_id"],
                              "policy_version": RUNTIME_POLICY["policy_version"], "sha256": sha, "loaded_at": iso(g.start),
                              "profile": "default", "test_only": False, "max_n": 1, "extra_fields": [], "validate_count": 0,
                              "cache_isolation": {"mode": "off", "ready": True, "unavailable_reason_code": None},
                              "valid": True, "error_code": None, "error_message": None, "loaded": True, "gateway_active": True,
                              "external_enforcement": "not_connected",
                              "approval": {"state": "verified", "checked_at": iso(g.now - timedelta(seconds=30)), "reason_code": "POLICY_APPROVAL_VERIFIED"}},
                   "mock_engine": {"url": "http://127.0.0.1:8088/healthz", "healthy": True, "reachable": True, "inference_verified": False,
                                   "checked_at": iso(g.now), "error": None, "credential": "configured", "receipts_store": {"state": "ok", "ok": True, "last_error": None}},
                   "concurrency": {"in_flight": 1, "limit": 2},
                   "connections": {"active": 3, "limit": 64, "rejected": 2, "request_header_timeout_s": 5.0, "request_body_timeout_s": 10.0},
                   "record_store": {"data_dir": "/var/lib/serving-runtime/demo", "state": "ok", "ok": True, "last_error": None, "last_error_at": None}},
        "policy": {"evaluated_at": iso(g.now), "current": {"valid_until": "2027-01-01T00:00:00+00:00"},
                   "validity": [{"item": "period", "state": "ok", "result": "within"},
                                {"item": "revocation", "state": "ok", "result": "not_revoked"},
                                {"item": "approval", "state": "ok", "result": "POLICY_APPROVAL_VERIFIED"}],
                   "history": [
                       {"at": iso(g.start), "event": "loaded", "policy_id": "local-policy-001", "policy_version": "3", "sha256": sha,
                        "previous": {"policy_version": "3"}, "same_version_content_changed": True},
                       {"at": iso(g.start - timedelta(days=1)), "event": "loaded", "policy_id": "local-policy-001", "policy_version": "3", "sha256": g.hex(64),
                        "previous": {"policy_version": "2"}, "same_version_content_changed": False},
                       {"at": iso(g.start - timedelta(days=2)), "event": "load_failed", "policy_id": None, "policy_version": None, "sha256": None,
                        "previous": None, "same_version_content_changed": False}],
                   "history_storage": {"state": "ok", "ok": True, "last_error": None}},
        "collectors": {"generated_at": iso(g.now), "collectors": collectors},
        "target": {"limits": ["model.id 일치는 weight 해시·revision·실제 로딩을 증명하지 않음", "관측값 불일치는 표시만 하고 요청을 자동 차단하지 않음"],
                   "registration": {"state": "ok"},
                   "comparison": [
                       {"field": "target_id", "expected": TARGET, "observed": TARGET, "source": "policy", "state": "match", "note": None},
                       {"field": "engine.kind", "expected": "mock", "observed": "mock", "source": "engine_api", "state": "match", "note": None},
                       {"field": "engine.version", "expected": "mock-1.0", "observed": "mock-1.1", "source": "engine_api", "state": "mismatch", "note": None},
                       {"field": "model.id", "expected": "test-model", "observed": "test-model", "source": "engine_api", "state": "match", "note": None},
                       {"field": "engine.image_digest", "expected": "sha256:" + "a" * 64, "observed": None, "source": "kubernetes", "state": "unknown", "note": "Pod 조회 실패"},
                       {"field": "model.revision", "expected": None, "observed": "main", "source": "engine_api", "state": "not_declared", "note": None}],
                   "history": [{"event": "instance_changed", "reason": "container_restarted", "recorded_at": iso(g.now - timedelta(hours=2))}]},
        "baseline": {"baseline": {"state": "ok"}, "model_id_limit": "model.id 일치는 weight 해시·revision·메모리 로딩을 증명하지 않음",
                     "rows": [{"field": "engine.version", "expected": "mock-1.0", "observed": "mock-1.1", "source": "engine_api", "state": "drift", "observed_at": iso(g.now)},
                              {"field": "model.id", "expected": "test-model", "observed": "test-model", "source": "engine_api", "state": "match", "observed_at": iso(g.now)},
                              {"field": "option.max_model_len", "expected": 2048, "observed": None, "source": "engine_api", "state": "not_collected", "observed_at": None}]},
        "summary": {"counts": counts, "incomplete": sum(1 for r in records if r["phase"] == "started" and not any(f["request_id"] == r["request_id"] for f in finals)),
                    "corrupt_lines": {"gateway": 1, "engine": 0}, "corrupt_details": {"gateway": [{"line": 211, "reason": "invalid JSON"}], "engine": []},
                    "record_store": {"data_dir": "/var/lib/serving-runtime/demo", "state": "ok", "ok": True, "last_error": None, "last_error_at": None}},
        "revalidation": {"open_count": 3, "current_condition_count": 2, "corrupt_lines": [], "evaluation_errors": []},
        "observations": {"counts": {"source": {s: sum(1 for o in observations if o["source"] == s) for s in ("hubble", "tetragon")}},
                         "corrupt_lines": 0, "verification": {"real_hubble_dropped": real_dropped},
                         "origins_by_kind": {k: {"synthetic": sum(1 for o in observations if o["kind"] == k)} for k in ("network_flow", "process_exec", "file_access")}},
    }


def lab_report_payload(g: Gen) -> dict[str, Any]:
    """runtime /api/lab-report 응답 모양. 데모 자료(EXAMPLE)이며 실제 시험 결과가 아니다."""
    cases = [
        ("L-01", "정책 적용 전 기준선", "passed", "EXAMPLE 정책 적용 전 통신·파일 기준선 기록"),
        ("L-02", "Cilium 통신 정책", "passed", "EXAMPLE Gateway 경유·DNS 허용, other-client→engine 직통·engine 외부 연결 차단"),
        ("L-03", "Hubble DROPPED 증거", "passed", "EXAMPLE DROPPED 흐름을 관측 기록으로 변환"),
        ("L-04", "Tetragon 파일·실행 관측", "passed", "EXAMPLE TG-ASSET-01 쓰기·TG-SECRET-01 읽기·목록 밖 실행 관측"),
        ("L-05", "Tetragon probe 차단", "failed", "EXAMPLE 적용 직후 EPERM 5회 중 4회 — 재확인 필요"),
        ("L-06", "적용 정책 정리", "not_run", "EXAMPLE 정리 옵션을 지정하지 않아 실행하지 않음"),
    ]
    return {"state": "ok", "configured_path": "/var/lib/serving-runtime/k8s-lab/reports", "generated_at": iso(g.now),
            "path": "/var/lib/serving-runtime/k8s-lab/reports/k8s-lab-DEMO.json", "error": None,
            "report": {"run_id": "k8s-lab-DEMO", "context": "kind-runtime-lab", "namespace": "serving-lab",
                       "started_at": iso(g.now - timedelta(hours=4)), "finished_at": iso(g.now - timedelta(hours=4) + timedelta(minutes=14)),
                       "overall": "partial",
                       "cases": [{"id": i, "title": t, "status": st, "summary": sm, "evidence": [f"raw/{i.lower()}.jsonl"] if st != "not_run" else [],
                                  "limitation": "데모 자료: 실제 시험 결과가 아님", "note": "적용 직후·재시작 후를 따로 확인" if i == "L-05" else None}
                                 for i, t, st, sm in cases]}}


def request_detail_native(g: Gen, records: list[dict[str, Any]]) -> dict[str, Any]:
    """runtime /api/requests/{id} 응답 모양."""
    final = records[-1]
    attempted = final["delivery"]["attempted"]
    receipts = [{"request_id": final["request_id"], "received_at": final["recorded_at"], "model": final["request"]["model"],
                 "max_tokens": final["request"]["max_tokens"], "stream": final["stream"]}] if attempted else []
    state = "received" if attempted else "not_received"
    return {"request_id": final["request_id"], "backend_kind": "mock", "gateway_records": records, "engine_receipts": receipts,
            "engine_live": {"status": "confirmed" if attempted else "not_found", "queried_at": iso(g.now)},
            "corrupt_lines": {"gateway": 0, "engine": 0}, "same_observation_session": True, "evidence_state": state,
            "evidence_summary": "엔진 수신 확인" if attempted else "Gateway 미전달, 엔진 미수신 확인"}


def revalidations(g: Gen) -> list[dict[str, Any]]:
    policy_ref = {k: RUNTIME_POLICY[k] for k in ("policy_id", "policy_version", "sha256")}
    base = {"schema_version": "byul-revalidation-request/0.2", "target_ref": TARGET, "policy_ref": policy_ref,
            "evaluation_refs": [], "evidence_refs": [], "data_origin": "EXAMPLE", "origin_detail": "synthetic"}
    second = f"reval-{g.hex(8)}"
    return [
        {**base, "revalidation_id": f"reval-{g.hex(8)}", "fingerprint": g.hex(64), "event": "opened",
         "created_at": iso(g.now - timedelta(hours=2)), "opened_at": iso(g.now - timedelta(hours=2)), "current_evidence_present": True,
         "trigger": "config_drift", "changed_fields": [{"field": "engine.version", "expected": "mock-1.0", "observed": "mock-1.1"}],
         "superseded_by": None, "status": "open"},
        {**base, "revalidation_id": second, "fingerprint": g.hex(64), "event": "opened",
         "created_at": iso(g.now - timedelta(hours=7)), "opened_at": iso(g.now - timedelta(hours=7)), "current_evidence_present": True,
         "trigger": "asset_changed",
         "changed_fields": [{"field": "asset:model/test-model/tokenizer.json", "expected": None, "observed": "changed"},
                            {"field": "asset:model/test-model/extra.safetensors", "expected": None, "observed": "unlisted_file"}],
         "superseded_by": None, "status": "open"},
        {**base, "revalidation_id": f"reval-{g.hex(8)}", "fingerprint": g.hex(64), "event": "superseded",
         "created_at": iso(g.now - timedelta(hours=7)), "opened_at": iso(g.now - timedelta(hours=9)), "current_evidence_present": False,
         "trigger": "asset_changed", "changed_fields": [{"field": "asset:model/test-model/tokenizer.json", "expected": None, "observed": "changed"}],
         "superseded_by": second, "status": "open"},
        {**base, "revalidation_id": f"reval-{g.hex(8)}", "fingerprint": g.hex(64), "event": "condition_cleared",
         "created_at": iso(g.now - timedelta(hours=20)), "opened_at": iso(g.now - timedelta(hours=23)), "current_evidence_present": False,
         "trigger": "policy_content_changed",
         "changed_fields": [{"field": "policy.sha256", "expected": g.hex(64), "observed": RUNTIME_POLICY["sha256"]}],
         "superseded_by": None, "status": "closed"},
    ]


def _aggregate(verdicts: list[str]) -> str:
    """serving-guard SPEC §4.7 집계 규칙(데모 자료를 만들 때만 쓴다. 화면은 다시 계산하지 않는다)."""
    applied = [v for v in verdicts if v != "NOT_APPLICABLE"]
    if "FAIL" in applied:
        return "FAIL"
    if "UNKNOWN" in applied:
        return "UNKNOWN"
    if "PASS" in applied:
        return "PASS"
    return "NOT_APPLICABLE"


_COMPARE = {("PASS", "PASS"): "NO_CHANGE", ("PASS", "FAIL"): "REGRESSED", ("FAIL", "PASS"): "FIXED", ("FAIL", "FAIL"): "STILL_FAILING"}


# 영역별 미충족·미확인 비율. 데모에서 충족·미충족·미확인이 고루 보이게 한다.
_FAIL_RATE = {"SUP": 0.0, "REQ": 0.18, "PLT": 0.0, "LIF": 0.06, "OBS": 0.0}
_UNKNOWN_RATE = {"SUP": 0.0, "REQ": 0.1, "PLT": 0.0, "LIF": 0.1, "OBS": 0.35}
CATALOG = json.loads((Path(__file__).with_name("demo_checks.json")).read_text(encoding="utf-8"))


def rpt_report(g: Gen, mode: str, created: datetime) -> dict[str, Any]:
    """update RPT 객체 모양의 데모 보고서. 영역·보호 목적 집계는 SPEC §4.7 규칙으로 만든다."""
    r = g.r
    update = mode == "update"
    rows: list[dict[str, Any]] = []
    not_selected: list[dict[str, str]] = []
    manual_required: list[str] = []
    counts = {a: {"manual": 0, "automated": 0, "not_run": 0} for a in AREAS}
    for c in CATALOG:
        area = c["area"]
        applicability = r.choices(["APPLICABLE", "NOT_APPLICABLE", "UNDETERMINED"], [0.84, 0.1, 0.06])[0]
        selected = applicability != "NOT_APPLICABLE"
        if not selected:
            not_selected.append({"check_id": c["check_id"], "reason": "NOT_APPLICABLE"})
        no_exec = 0.3 if area == "OBS" else 0.05
        source = r.choices(["automated", "manual", None], [0.6, 0.4 - no_exec, no_exec])[0] if selected else None
        gaps = [r.choice(["NO_EXECUTOR", "NO_FIXTURE", "NO_ENV_FACT"])] if selected and source is None else []
        if selected and source is None:
            manual_required.append(c["check_id"])

        def verdict() -> str:
            if not selected:
                return "NOT_APPLICABLE"
            if source is None:
                return "UNKNOWN"
            fail, unknown = _FAIL_RATE[area], _UNKNOWN_RATE[area]
            return r.choices(["PASS", "FAIL", "UNKNOWN"], [1 - fail - unknown, fail, unknown])[0]

        def side(sec: str) -> dict[str, Any]:
            return {"result": g.ref("RES") if selected else None, "security": sec,
                    "functional": "PASS" if sec != "UNKNOWN" else "UNKNOWN",
                    "performance": r.choice(["PASS", "UNKNOWN"]) if selected else "NOT_APPLICABLE",
                    "verification_level": "EXAMPLE" if source else None}

        row: dict[str, Any] = {**c, "impact": None, "applicability": applicability, "selected": selected,
                               "tested_while_undetermined": applicability == "UNDETERMINED" and selected,
                               "single": None, "baseline": None, "candidate": None, "comparison": None,
                               "gaps": gaps, "exception": None}
        if update:
            b, cand = verdict(), verdict()
            if b == "PASS" and r.random() < 0.06 and selected and source:
                cand = "FAIL"  # 후퇴 사례가 보이도록
            row["baseline"], row["candidate"] = side(b), side(cand)
            row["comparison"] = _COMPARE.get((b, cand), "UNKNOWN") if selected else None
        else:
            row["single"] = side(verdict())
        if source:
            row["evidence_source"] = source
            counts[area][source] += 1
        elif selected:
            counts[area]["not_run"] += 1
        if selected and source and r.random() < 0.06:
            days = r.choice([-3, 30, 90])
            row["exception"] = {"status": "EXPIRED" if days < 0 else "APPROVED", "reason": "운영 승인 예외(데모)",
                                "expires_at": iso(g.now + timedelta(days=days))}
        if selected and source == "automated" and r.random() < 0.08:
            row["replaced"] = {"source": "manual", "revision": 1, "executor": None, "reason": "자동 측정으로 대체"}
        if selected and c["area"] == "REQ" and r.random() < 0.15:
            probed, of = r.randint(2, 6), r.randint(6, 9)
            row["functional_scope"] = {"probed": probed, "of": of, "label": f"기능 확인 범위: {probed} / 등록 경로 {of} (표본)"}
        rows.append(row)

    def headline(row: dict[str, Any]) -> str:
        return ((row["candidate"] if update else row["single"]) or {}).get("security") or "NOT_APPLICABLE"

    def axis(key: str) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(row[key], []).append(row)
        return {name: {"security": _aggregate([headline(x) for x in items]),
                       "inventory_complete": not any(x["applicability"] == "UNDETERMINED" for x in items)}
                for name, items in groups.items()}

    refs = [g.ref("ENV"), g.ref("POL"), g.ref("PLAN")] + ([g.ref("ENV"), g.ref("CHG")] if update else [])
    return {
        "kind": "RPT", "id": g.ulid("RPT"), "revision": 1, "created_at": iso(created), "data_origin": "EXAMPLE",
        "content_sha256": g.hex(64), "refs": refs, "mode": mode, "official": False,
        "inputs_manifest": [{"kind": "PACK", "id": "PACK-BASELINE", "revision": 22, "sha256": g.hex(64)}] + refs,
        "pack_review_status": "DRAFT", "policy_status": "DRAFT",
        "by_check": rows, "by_purpose": axis("purpose"), "by_area": axis("area"),
        "not_selected": not_selected, "manual_required": manual_required,
        "limitations": ["데모 자료: 화면 확인용 가짜 결과", "검토 전 팩(DRAFT): 공식 아님", "정책 DRAFT: 공식 아님",
                        "EXAMPLE 자료 사용: 공식 판정 근거가 될 수 없음"],
        "approval": None, "evidence_counts": counts,
    }


def evidence(g: Gen, out: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    files = out / "files"
    files.mkdir(parents=True, exist_ok=True)
    items = []
    picked = [e for e in events if e["source"] == "gateway" and e["outcome"] in ("denied", "error")][:12]
    for e in picked:
        rid = e["scope"]["request_id"]
        body = json.dumps({"request_id": rid, "final_state": e["native"]["record"]["decision"]["result"], "demo": True}).encode()
        name = f"request-{rid[:8]}.json"
        (files / name).write_bytes(body)
        items.append({
            "evidence_id": f"ev-{rid[:12]}", "path": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
            "media_type": "application/json", "collector": {"name": "byul-platform/demo", "version": None},
            "collected_at": e["recorded_at"], "window": None, "source": "gateway", "data_origin": "EXAMPLE", "refs": [e["event_id"]],
        })
    return {"schema_version": "byul-evidence-index/0.1", "producer": "runtime", "created_at": iso(g.now), "items": items}


# --- 만들기 ----------------------------------------------------------------------


def build(out: Path, *, seed: int = 7, hours: int = 24, requests: int = 600, now: datetime | None = None,
          contracts_dir: Path = DEFAULT_CONTRACTS_DIR) -> dict[str, int]:
    g = Gen(seed, now or datetime.now(UTC), hours)
    contracts = Contracts(contracts_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    kinds = ["ALLOWED", "DENIED", "INVALID", "ENGINE_REJECTED", "ERROR", "INCOMPLETE"]
    weights = [0.70, 0.09, 0.12, 0.03, 0.035, 0.025]
    run_id = g.ulid("PLAN")
    events: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for at in g.times(requests, spikes=[(0.62, 0.015)]):
        kind = g.r.choices(kinds, weights)[0]
        # 몰림 구간에는 정책 거부가 많다(그래프에서 보이도록)
        if abs((at - g.start).total_seconds() / (g.now - g.start).total_seconds() - 0.62) < 0.03 and g.r.random() < 0.6:
            kind = "DENIED"
        in_run = abs((at - g.start).total_seconds() / (g.now - g.start).total_seconds() - 0.3) < 0.02
        record = gateway_record(g, at, kind, run_id if in_run else None)
        # runtime처럼 정책 판정을 통과한 요청은 started 기록을 먼저 남긴다(정책 거부·입력 오류는 final 하나)
        records = [record]
        if kind in ("ALLOWED", "ENGINE_REJECTED", "ERROR") and record["phase"] == "final":
            started = {**record, "phase": "started", "recorded_at": iso(at - timedelta(milliseconds=g.r.randint(5, 900))),
                       "delivery": {"attempted": False, "backend_receipt": "not_queried", "response": "none", "error_code": None}}
            records = [started, record]
        all_records += records
        events.append(gateway_event(record))
        details.append(request_detail_doc(request_detail_native(g, records)))
    observations = [hubble_record(g, at) for at in g.times(requests // 4, spikes=[(0.8, 0.02)])]
    observations += [tetragon_record(g, at) for at in g.times(requests // 8, spikes=[(0.45, 0.02)])]
    events += [observation_event(o) for o in observations]
    events.sort(key=lambda e: e["recorded_at"])
    native = native_payloads(g, all_records, observations)

    docs: dict[str, Any] = {
        "runtime-status.json": runtime_status(native["status"], native["policy"], native["collectors"], target=native["target"],
                                              summary=native["summary"], revalidation=native["revalidation"],
                                              observations=native["observations"], baseline=native["baseline"]),
        "revalidation-request.json": revalidations(g),
        "runtime-lab-report.json": lab_report_doc(lab_report_payload(g)),
        "runtime-request-detail.json": details,
        "evidence-index.json": evidence(g, out, events),
    }
    # 환경·정책은 계약 예시를 그대로 쓴다(서로의 해시 참조가 맞아야 한다).
    for name in ("environment.json", "policy.json"):
        shutil.copy(contracts_dir / "examples" / name, out / name)

    schema_of = {"runtime-status.json": "runtime-status", "revalidation-request.json": "revalidation-request",
                 "evidence-index.json": "evidence-index", "runtime-lab-report.json": "runtime-lab-report",
                 "runtime-request-detail.json": "runtime-request-detail"}
    errors: list[str] = []
    # update 보고서는 RPT 원본으로 만든다(platform이 보고서 화면과 계약 투영을 둘 다 만든다).
    reports = [rpt_report(g, "update", g.now - timedelta(hours=3)), rpt_report(g, "audit", g.now - timedelta(hours=30))]
    for rpt in reports:
        errors += [f"{rpt['id']} {e.path}: {e.message}" for e in contracts.errors("verification-result", rpt_to_verification_result(rpt))]
    for name, doc in docs.items():
        for i, item in enumerate(doc if isinstance(doc, list) else [doc]):
            errors += [f"{name}[{i}] {e.path}: {e.message}" for e in contracts.errors(schema_of[name], item)]
    for i, e in enumerate(events):
        errors += [f"runtime-events[{i}] {x.path}: {x.message}" for x in contracts.errors("runtime-event", e)]
    if errors:
        raise ValueError("데모 자료가 계약 검증을 통과하지 못했습니다:\n" + "\n".join(errors[:20]))

    with (out / "runtime-events.jsonl").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n")
    for name, doc in docs.items():
        (out / name).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "update-reports").mkdir()
    for rpt in reports:
        (out / "update-reports" / f"{rpt['id']}.json").write_text(json.dumps(rpt, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "README.txt").write_text("byul-platform 데모 자료. 화면 확인용 가짜 기록이며 공식 근거가 아니다. byul_platform.demo로 다시 만든다.\n", encoding="utf-8")

    by_source: dict[str, int] = {}
    for e in events:
        by_source[e["source"]] = by_source.get(e["source"], 0) + 1
    return by_source


def main() -> None:
    parser = argparse.ArgumentParser(description="화면 확인용 데모 자료를 만든다(전부 EXAMPLE).")
    parser.add_argument("out", nargs="?", default=str(Path(__file__).resolve().parents[3] / ".demo-data"))
    parser.add_argument("--hours", type=int, default=24, help="지금부터 거슬러 올라갈 시간(기본 24)")
    parser.add_argument("--requests", type=int, default=600, help="요청 수(기본 600)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    counts = build(out, seed=args.seed, hours=args.hours, requests=args.requests)
    print(f"{out}")
    print("사건: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"실행: BYUL_EXAMPLES_DIR={out} uv run byul-platform")


if __name__ == "__main__":
    main()
