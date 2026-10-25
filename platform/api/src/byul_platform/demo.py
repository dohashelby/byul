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
from .sources.runtime import gateway_event, observation_event

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
        "request": {"model": model, "max_tokens": max_tokens, "n": None, "extra_fields": [], "context": None},
        "delivery": delivery,
        "stream": stream,
        "backend_kind": "mock",
        "data_origin": "synthetic",
    }


# --- 관측 기록 ------------------------------------------------------------------


def hubble_record(g: Gen, at: datetime) -> dict[str, Any]:
    dropped = g.r.random() < 0.18
    return {
        "schema_version": "runtime-observation/1.0",
        "observed_at": iso(at),
        "source": "hubble",
        "kind": "flow",
        "data_origin": "synthetic",
        "environment": "demo",
        "verdict": "DROPPED" if dropped else "FORWARDED",
        "src_pod": "serving-engine-0",
        "dst_ip": g.r.choice(["10.0.3.17", "172.217.25.14", "10.96.0.10"]),
        "dst_port": g.r.choice([443, 443, 53, 8080]),
        "protocol": g.r.choice(["TCP", "UDP"]),
        "drop_reason": "POLICY_DENIED" if dropped else None,
        "event_key": g.hex(64),
    }


def tetragon_record(g: Gen, at: datetime) -> dict[str, Any]:
    r = g.r
    if r.random() < 0.25:
        enforce = r.random() < 0.5
        return {
            "schema_version": "runtime-observation/1.0",
            "observed_at": iso(at),
            "source": "tetragon",
            "kind": "file_write",
            "data_origin": "synthetic",
            "environment": "demo",
            "path": r.choice(["/root/.cache/vllm/torch_compile/cache.bin", "/models/test-model/config.json", "/tmp/x.so"]),
            "pod": "serving-engine-0",
            "action_mode": "enforce" if enforce else "monitor",
            "action_raw": "KPROBE_ACTION_OVERRIDE" if enforce else "KPROBE_ACTION_POST",
            "cache_generation": r.choice(["current", "other", "outside"]),
            "rule_refs": ["TG-CACHE-01"],
            "event_key": g.hex(64),
        }
    verdict = r.choices(
        ["allowed_in_phase", "exception", "suspect_binary", "outside_allowlist", "exec_from_cache", "phase_unknown"],
        [0.55, 0.08, 0.12, 0.12, 0.05, 0.08],
    )[0]
    enforce = verdict in ("outside_allowlist", "suspect_binary") and r.random() < 0.35
    return {
        "schema_version": "runtime-observation/1.0",
        "observed_at": iso(at),
        "source": "tetragon",
        "kind": "process_exec",
        "data_origin": "synthetic",
        "environment": "demo",
        "binary": r.choice(["/usr/bin/python3", "/bin/sh", "/usr/bin/curl", "/usr/local/bin/nvidia-smi", "/tmp/.x/run"]),
        "pod": "serving-engine-0",
        "action_mode": "enforce" if enforce else "monitor",
        "phase": r.choice(["loading", "compiling", "inferring", "inferring", "inferring"]),
        "exec_verdict": verdict,
        "rule_refs": [r.choice(["SP3-EXEC", "SP3-EXEC", "SP1-SHELL", "SP2-NET-TOOL"])],
        "event_key": g.hex(64),
    }


# --- 상태·재검증·검증 결과·증거 ---------------------------------------------------


def runtime_status(g: Gen) -> dict[str, Any]:
    return {
        "schema_version": "byul-runtime-status/0.1",
        "generated_at": iso(g.now),
        "observation_session_id": SESSION,
        "target": {"target_ref": TARGET, "registration": "registered", "instance_id": "a1b2c3d4e5f60718", "instance_state": "running"},
        "policy": {
            "policy_ref": {k: RUNTIME_POLICY[k] for k in ("policy_id", "policy_version", "sha256")},
            "bundle_id": "bundle-local-policy-001-3",
            "loaded": True,
            "gateway_active": True,
            "approval_state": "verified",
            "valid_until": "2027-01-01T00:00:00+00:00",
        },
        "collectors": [
            {"source": "engine_health", "state": "ok", "last_success_at": iso(g.now - timedelta(seconds=3))},
            {"source": "engine_info", "state": "ok", "last_success_at": iso(g.now - timedelta(seconds=3))},
            {"source": "hubble", "state": "ok", "last_success_at": iso(g.now - timedelta(seconds=8))},
            {"source": "tetragon", "state": "stale", "last_success_at": iso(g.now - timedelta(minutes=17))},
            {"source": "metrics", "state": "disconnected", "last_success_at": None},
        ],
        "store": {"state": "ok"},
    }


def revalidations(g: Gen) -> list[dict[str, Any]]:
    policy_ref = {k: RUNTIME_POLICY[k] for k in ("policy_id", "policy_version", "sha256")}
    base = {"schema_version": "byul-revalidation-request/0.1", "target_ref": TARGET, "policy_ref": policy_ref,
            "evaluation_refs": [], "evidence_refs": [], "data_origin": "EXAMPLE", "origin_detail": "synthetic"}
    second = f"reval-{g.hex(8)}"
    return [
        {**base, "revalidation_id": f"reval-{g.hex(8)}", "fingerprint": g.hex(64), "event": "opened",
         "created_at": iso(g.now - timedelta(hours=2)), "trigger": "config_drift",
         "changed_fields": [{"field": "engine.version", "expected": "0.31.0", "observed": "0.31.1"}],
         "superseded_by": None, "status": "open"},
        {**base, "revalidation_id": second, "fingerprint": g.hex(64), "event": "opened",
         "created_at": iso(g.now - timedelta(hours=7)), "trigger": "asset_changed",
         "changed_fields": [{"field": "asset:model/test-model/tokenizer.json", "expected": None, "observed": "changed"},
                            {"field": "asset:model/test-model/extra.safetensors", "expected": None, "observed": "unlisted_file"}],
         "superseded_by": None, "status": "open"},
        {**base, "revalidation_id": f"reval-{g.hex(8)}", "fingerprint": g.hex(64), "event": "superseded",
         "created_at": iso(g.now - timedelta(hours=9)), "trigger": "asset_changed",
         "changed_fields": [{"field": "asset:model/test-model/tokenizer.json", "expected": None, "observed": "changed"}],
         "superseded_by": second, "status": "open"},
        {**base, "revalidation_id": f"reval-{g.hex(8)}", "fingerprint": g.hex(64), "event": "condition_cleared",
         "created_at": iso(g.now - timedelta(hours=20)), "trigger": "policy_content_changed",
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


# 영역별 미충족 비율. 데모에서 충족·미충족·미확인이 고루 보이게 한다.
_FAIL_RATE = {"SUP": 0.0, "REQ": 0.18, "PLT": 0.0, "LIF": 0.06, "OBS": 0.0}
_UNKNOWN_RATE = {"SUP": 0.0, "REQ": 0.1, "PLT": 0.0, "LIF": 0.1, "OBS": 0.35}


def verification_result(g: Gen, mode: str, created: datetime) -> dict[str, Any]:
    r = g.r
    checks = []
    per_area: dict[str, list[str]] = {a: [] for a in AREAS}
    counts = {a: {"manual": 0, "automated": 0, "not_run": 0} for a in AREAS}
    for area in AREAS:
        for n in range(1, r.randint(5, 9)):
            cid = f"{area}-{n:02d}"
            applicability = r.choices(["APPLICABLE", "NOT_APPLICABLE", "UNDETERMINED"], [0.82, 0.12, 0.06])[0]
            selected = applicability != "NOT_APPLICABLE"
            gaps: list[str] = []
            no_exec = 0.3 if area == "OBS" else 0.0
            source = r.choices(["automated", "manual", None], [0.6, 0.4 - no_exec, no_exec])[0] if selected else None
            if source is None and selected:
                gaps = [r.choice(["NO_EXECUTOR", "NO_FIXTURE", "NO_ENV_FACT"])]

            def verdict() -> str:
                if not selected:
                    return "NOT_APPLICABLE"
                if source is None:
                    return "UNKNOWN"
                fail, unknown = _FAIL_RATE[area], _UNKNOWN_RATE[area]
                return r.choices(["PASS", "FAIL", "UNKNOWN"], [1 - fail - unknown, fail, unknown])[0]

            def side(sec: str) -> dict[str, Any]:
                return {"result_ref": g.ref("RES") if selected else None, "security": sec,
                        "functional": sec if sec != "FAIL" else "PASS", "performance": r.choice(["PASS", "UNKNOWN"]) if selected else "NOT_APPLICABLE",
                        "verification_level": "EXAMPLE" if source else None}

            if mode == "update":
                b, c = verdict(), verdict()
                sides = {"baseline": side(b), "candidate": side(c)}
                comparison = _COMPARE.get((b, c), "UNKNOWN") if selected else None
                per_area[area].append(c)
            else:
                v = verdict()
                sides = {"single": side(v)}
                comparison = None
                per_area[area].append(v)
            row: dict[str, Any] = {"check_id": cid, "applicability": applicability, "selected": selected, "sides": sides,
                                   "comparison": comparison, "gaps": gaps, "evidence_source": source}
            if not selected:
                row["not_selected_reason"] = "환경 사실상 해당 없음"
            if selected and r.random() < 0.07:
                row["exception_approved"] = True
            checks.append(row)
            counts[area][source or "not_run"] += 1 if selected else 0
    return {
        "schema_version": "byul-verification-result/0.1",
        "report_ref": g.ref("RPT"),
        "mode": mode,
        "created_at": iso(created),
        "data_origin": "EXAMPLE",
        "plan_ref": g.ref("PLAN"),
        "environment_refs": [g.ref("ENV")] * (2 if mode == "update" else 1),
        "policy_ref": g.ref("POL"),
        "byul_policy_ref": None,
        "pack": {"id": "PACK-BASELINE", "revision": 22, "review_status": "DRAFT"},
        "change_ref": g.ref("CHG") if mode == "update" else None,
        "summary": {"by_area": {a: {"security": _aggregate(v)} for a, v in per_area.items()}, "evidence_counts": counts},
        "checks": checks,
        "limitations": ["데모 자료: 화면 확인용 가짜 결과", "검토 전 팩(DRAFT): 공식 아님", "EXAMPLE 자료 사용: 공식 판정 근거가 될 수 없음"],
        "approval": None,
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
    for at in g.times(requests, spikes=[(0.62, 0.015)]):
        kind = g.r.choices(kinds, weights)[0]
        # 몰림 구간에는 정책 거부가 많다(그래프에서 보이도록)
        if abs((at - g.start).total_seconds() / (g.now - g.start).total_seconds() - 0.62) < 0.03 and g.r.random() < 0.6:
            kind = "DENIED"
        in_run = abs((at - g.start).total_seconds() / (g.now - g.start).total_seconds() - 0.3) < 0.02
        events.append(gateway_event(gateway_record(g, at, kind, run_id if in_run else None)))
    for at in g.times(requests // 4, spikes=[(0.8, 0.02)]):
        events.append(observation_event(hubble_record(g, at)))
    for at in g.times(requests // 8, spikes=[(0.45, 0.02)]):
        events.append(observation_event(tetragon_record(g, at)))
    events.sort(key=lambda e: e["recorded_at"])

    docs: dict[str, Any] = {
        "runtime-status.json": runtime_status(g),
        "revalidation-request.json": revalidations(g),
        "verification-result.json": [
            verification_result(g, "update", g.now - timedelta(hours=3)),
            verification_result(g, "audit", g.now - timedelta(hours=30)),
        ],
        "evidence-index.json": evidence(g, out, events),
    }
    # 환경·정책은 계약 예시를 그대로 쓴다(서로의 해시 참조가 맞아야 한다).
    for name in ("environment.json", "policy.json"):
        shutil.copy(contracts_dir / "examples" / name, out / name)

    schema_of = {"runtime-status.json": "runtime-status", "revalidation-request.json": "revalidation-request",
                 "verification-result.json": "verification-result", "evidence-index.json": "evidence-index"}
    errors: list[str] = []
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
