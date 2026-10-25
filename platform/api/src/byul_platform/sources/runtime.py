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

EVENT_SCHEMA = "byul-runtime-event/0.1"
STATUS_SCHEMA = "byul-runtime-status/0.1"
REVALIDATION_SCHEMA = "byul-revalidation-request/0.1"

_LOCAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PHASES = {"loading", "compiling", "inferring", "maintenance", "unknown"}

# contracts README "runtime data_origin → 등급"
_MEASURED = {"real", "external_backend", "kubernetes_api", "local_file"}
_EXAMPLE = {"mock", "synthetic", "actual_test"}

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
        return "observed"  # DROPPED 포함. Hubble은 거부 확인이 아니다.
    if source == "tetragon":
        if record.get("action_mode") == "enforce":
            return "enforce_attempted"
        verdict = record.get("exec_verdict")
        if verdict in _TETRAGON_OBSERVED:
            return "observed"
        if verdict in _TETRAGON_UNKNOWN:
            return "unknown"
        if verdict in _TETRAGON_DETECTED and record.get("action_mode") == "monitor":
            return "detected"
    return "unknown"


def _observation_category(record: dict[str, Any]) -> str:
    if record.get("source") == "hubble":
        return "network"
    kind = str(record.get("kind") or "")
    return "process" if "exec" in kind else "file"


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
    phase = record.get("phase")
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
        "phase": phase if phase in _PHASES else None,
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
        "native": {"schema_version": _str_or_none(record.get("schema_version")), "record": record},
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
        "data_origin": event["data_origin"],
        "origin_detail": event.get("origin_detail"),
    }


# --- 상태 → runtime-status -------------------------------------------------------

_APPROVAL_STATES = {"not_configured", "verified", "last_verified", "unverified", "rejected", "error"}


def runtime_status(status: dict[str, Any], policy: dict[str, Any], collectors: dict[str, Any]) -> dict[str, Any]:
    target = status.get("target") if isinstance(status.get("target"), dict) else {}
    instance = target.get("instance") if isinstance(target.get("instance"), dict) else {}
    current = instance.get("current") if isinstance(instance.get("current"), dict) else {}
    st_policy = status.get("policy") if isinstance(status.get("policy"), dict) else {}
    approval = st_policy.get("approval") if isinstance(st_policy.get("approval"), dict) else {}
    approval_state = approval.get("state")
    current_policy = policy.get("current") if isinstance(policy.get("current"), dict) else {}
    record_store = status.get("record_store") if isinstance(status.get("record_store"), dict) else {}
    store_state = record_store.get("state")
    collector_items = []
    for item in collectors.get("collectors", []) if isinstance(collectors.get("collectors"), list) else []:
        if isinstance(item, dict):
            collector_items.append(
                {
                    "source": str(item.get("source")),
                    "state": item.get("state"),
                    "last_success_at": item.get("last_success_at"),
                }
            )
    return {
        "schema_version": STATUS_SCHEMA,
        "generated_at": collectors.get("generated_at") or policy.get("evaluated_at"),
        "observation_session_id": _str_or_none(status.get("observation_session_id")),
        "target": {
            "target_ref": _local_id(target.get("target_ref")) or _local_id(target.get("target_id")),
            "registration": str(target.get("registration") or "unknown"),
            "instance_id": _str_or_none(current.get("instance_id")),
            "instance_state": str(instance.get("state") or "unknown"),
        },
        "policy": {
            "policy_ref": _policy_ref(st_policy),
            # runtime은 번들 ID를 아직 기록하지 않는다(정책 번들 배포 미구현).
            "bundle_id": None,
            "loaded": st_policy.get("loaded") is True,
            "gateway_active": st_policy.get("gateway_active") is True,
            "approval_state": approval_state if approval_state in _APPROVAL_STATES else "error",
            "valid_until": current_policy.get("valid_until"),
        },
        "collectors": collector_items,
        "store": {"state": store_state if store_state in ("ok", "error") else "unknown"},
    }


def _comparison_rows(rows: Iterable[Any], state_key: str = "state") -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "field": str(row.get("field")),
                "expected": row.get("expected"),
                "observed": row.get("observed"),
                "state": str(row.get(state_key) or "unknown"),
                "source": _str_or_none(row.get("source")),
            }
        )
    return out


def comparisons(baseline: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    base_meta = baseline.get("baseline") if isinstance(baseline.get("baseline"), dict) else {}
    reg = target.get("registration") if isinstance(target.get("registration"), dict) else {}
    notes_base = [baseline["model_id_limit"]] if isinstance(baseline.get("model_id_limit"), str) else []
    notes_target = [s for s in target.get("limits", []) if isinstance(s, str)] if isinstance(target.get("limits"), list) else []
    return {
        "baseline": {
            "state": _str_or_none(base_meta.get("state")),
            "rows": _comparison_rows(baseline.get("rows") or []),
            "notes": notes_base,
        },
        "target": {
            "state": _str_or_none(reg.get("state")),
            "rows": _comparison_rows(target.get("comparison") or []),
            "notes": notes_target,
        },
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
    }


# --- 출처 인터페이스 ----------------------------------------------------------------


@dataclass
class EventBatch:
    candidates: list[Any]
    truncated: bool = False


@dataclass
class DetailResult:
    event: dict[str, Any] | None
    native_detail: Any
    evidence_state: str | None
    evidence_summary: str | None
    evidence_items: list[dict[str, Any]] = field(default_factory=list)


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
    def summary(self) -> Summary: ...
    def status(self) -> Any: ...
    def comparisons(self) -> dict[str, Any]: ...
    def revalidation(self) -> list[Any]: ...
    def ping(self) -> str: ...


def summary_from_events(events: Iterable[dict[str, Any]]) -> Summary:
    counts = {k: 0 for k in ("allowed", "denied", "invalid", "engine_rejected", "error", "unknown")}
    for e in events:
        if isinstance(e, dict) and e.get("source") == "gateway" and e.get("outcome") in counts:
            counts[e["outcome"]] += 1
    return Summary(counts=counts, total=sum(counts.values()), corrupt_lines=None, generated_at=None)


class ExampleRuntimeSource:
    """contracts/examples를 읽는 예시 모드. 전부 EXAMPLE 등급."""

    mode = "example"

    def __init__(self, examples_dir: Path) -> None:
        self.dir = examples_dir
        self.detail = str(examples_dir)

    def _json(self, name: str) -> Any:
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

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

    def request_detail(self, request_id: str) -> DetailResult:
        for e in self._events():
            if isinstance(e, dict) and e.get("event_id") == f"req:{request_id}":
                return DetailResult(event=e, native_detail=None, evidence_state=None, evidence_summary=None)
        raise RuntimeNotFound(request_id)

    def summary(self) -> Summary:
        return summary_from_events(self._events())

    def status(self) -> Any:
        return self._json("runtime-status.json")

    def comparisons(self) -> dict[str, Any]:
        # 예시 계약에는 구성·대상 비교가 없다.
        return {
            "baseline": {"state": None, "rows": [], "notes": []},
            "target": {"state": None, "rows": [], "notes": []},
        }

    def revalidation(self) -> list[Any]:
        data = self._json("revalidation-request.json")
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

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
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
        try:
            return response.json()
        except ValueError:
            raise RuntimeUnavailable("invalid JSON") from None


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

    def __init__(self, client: RuntimeClient, base_url: str, cache_ttl: float = 3.0) -> None:
        self.client = client
        self.detail = base_url
        self._cache = _TTLCache(cache_ttl)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        key = path + "?" + json.dumps(params or {}, sort_keys=True)
        return self._cache.get(key, lambda: self.client.get(path, params))

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
                detail = self._get(f"/api/requests/{rid}")
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
        detail = self._get(f"/api/requests/{request_id}")
        record = pick_gateway_record(detail.get("gateway_records", []))
        try:
            evidence = self._get(f"/api/requests/{request_id}/evidence")
        except RuntimeNotFound:
            evidence = None
        return DetailResult(
            event=gateway_event(record) if record is not None else None,
            native_detail={"detail": detail, "evidence": evidence},
            evidence_state=_str_or_none(detail.get("evidence_state")),
            evidence_summary=_str_or_none(detail.get("evidence_summary")),
        )

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
        return runtime_status(self._get("/api/status"), self._get("/api/policy"), self._get("/api/collectors"))

    def comparisons(self) -> dict[str, Any]:
        return comparisons(self._get("/api/baseline"), self._get("/api/target"))

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
