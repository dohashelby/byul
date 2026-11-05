"""#196 GPU 공통 센서 입력의 공개 계약(개발 담당, 1단계).

GPU 실증 담당의 소비자(LIF-03·05·06·07·08·09·10·11·12·14, PLT-01·04, OBS-02·03)가 읽을 입력의 닫힌 형식이다.
이 모듈은 생산자가 아니다. 형식, 세대 결합, 그리고 "완료 주장을 받지 않는 조건"만 정한다.

공통 봉투(모든 종류):
- 생산자 이름·판본·소스 SHA-256, 실제 ENV Ref, target SHA-256, prepared Ref SHA-256, 세션.
- 세대 결합: Pod UID, container ID, restart 횟수, worker PID, start ticks, rank.
- 완전성: 원 기록 끝 도달·누락 수·진단 코드.

판정에 쓰는 원칙(#196 본문):
- 지원 생산자 목록(`SUPPORTED_PRODUCERS`)에 없는 생산자의 기록은 형식이 맞아도 `supported=False`다. 소비자는 그 기록으로
  PASS를 만들지 않는다. 1단계에는 생산자가 없으므로 목록이 비어 있다. 2단계에서 GPU 생산자를 만들 때 판본·소스 SHA를 더한다.
- 누락·지원 불일치(`gaps`)와 실제 위반 후보(`violation_candidates`)를 따로 둔다. 후속 수집·정리 오류는 앞선 위반 후보를 지우지 않는다.
- 생산자 인증은 언제나 False다. 같은 시간대라는 이유만으로 다른 세대·세션의 행을 합치지 않는다.
- 비밀 원문은 받지 않는다. 문자열 값은 닫힌 식별자 형식이거나 SHA-256이다.
"""
from __future__ import annotations

import re

SCHEMA = "guard-gpu-common-input/1"
_SHA = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_SESSION = re.compile(r"[0-9a-f]{32}")
MAX_EVENTS = 4096

# (생산자 이름, 판본) -> 소스 SHA-256. 1단계에는 구현된 생산자가 없다.
SUPPORTED_PRODUCERS: dict[tuple[str, str], str] = {}

_BINDING = ("pod_uid", "container_id", "restart_count", "worker_pid", "start_ticks", "rank")

# 종류별: 소비 점검, 행 필드(이름 -> 형식), 행 종류 값, 완료 주장에 필요한 행 종류.
# 형식: sha | id | int | bool | ms(0 이상 정수 밀리초) | opt_sha | opt_id | opt_int | enum:<a|b>
KINDS = {
    "request-attempt": {
        "checks": ("LIF-03", "LIF-10", "LIF-12"),
        "events": ("admitted", "queued", "dequeued", "stage", "kv_owned", "budget", "limit_enforced", "abort", "finished",
                   "gpu_fence", "buffer_released", "kv_released", "retry"),
        "fields": {"request_sha256": "sha", "attempt": "int", "at_ms": "ms", "stage": "opt_id", "cost": "opt_int",
                   "kv_blocks": "opt_int", "gpu_bytes": "opt_int", "retry_cause": "opt_id", "next_attempt": "opt_int",
                   "consumed_by_attempt": "opt_int"},
        "complete_requires": ("admitted", "finished", "gpu_fence", "kv_released"),
    },
    "generation-transition": {
        "checks": ("LIF-05", "LIF-06", "LIF-07", "LIF-09"),
        "events": ("in_flight", "consumed", "drain", "routing", "rank_ready", "model_loaded", "backend_ready", "graph_ready",
                   "service_gate", "accepted", "failure", "recovery"),
        "fields": {"at_ms": "ms", "request_sha256": "opt_sha", "generation": "id", "component": "enum:worker|weights|adapter|cache|service",
                   "state": "opt_id", "error_sha256": "opt_sha", "alias_sha256": "opt_sha"},
        "complete_requires": ("in_flight", "consumed", "service_gate"),
    },
    "rank-fault": {
        "checks": ("LIF-08",),
        "events": ("fault_applied", "fault_released", "detected", "client_delivered", "recovered"),
        "fields": {"at_ms": "ms", "ack": "bool", "fault_kind": "id", "request_sha256": "opt_sha"},
        "complete_requires": ("fault_applied", "fault_released", "detected", "recovered"),
    },
    "failed-group-resources": {
        "checks": ("LIF-11",),
        "events": ("inventory_before", "inventory_after", "released"),
        "fields": {"at_ms": "ms", "resource": "enum:pid|ipc_socket|shm|nccl_communicator|device", "resource_sha256": "sha",
                   "owner_group_sha256": "sha", "next_generation": "opt_id"},
        "complete_requires": ("inventory_before", "inventory_after"),
    },
    "process-device-provider": {
        "checks": ("PLT-01", "PLT-04"),
        "events": ("gpu_process", "device_access", "provider_access"),
        "fields": {"gpu_uuid_sha256": "opt_sha", "nvml_host_pid": "opt_int", "namespace_pid": "opt_int", "cgroup_sha256": "opt_sha",
                   "env_name": "opt_id", "provider_kind": "opt_id", "receiver_sha256": "opt_sha", "credential_generation": "opt_id",
                   "nonsecret_id_sha256": "opt_sha", "result": "enum:allowed|denied|unknown"},
        "complete_requires": ("gpu_process", "device_access"),
    },
    "health-worker-causal": {
        "checks": ("OBS-02",),
        "events": ("api_health", "api_record", "worker_state", "request_accepted"),
        "fields": {"at_ms": "ms", "generation": "id", "causal_sha256": "sha", "state": "id"},
        "complete_requires": ("api_health", "worker_state"),
    },
    # P2(개발 담당 2단계): API가 받은 입력·옵션이 runner와 sampler까지 같은 값으로 갔는가. V1 runner 경로.
    "runner-consumption": {
        "checks": ("REQ-08", "REQ-09", "REQ-10"),
        "events": ("api_validated", "api_rejected", "runner_received", "sampler_applied"),
        "fields": {"request_sha256": "sha", "at_ms": "ms", "stage": "opt_id", "prompt_tokens": "opt_int", "max_token_id": "opt_int",
                   "temperature_milli": "opt_int", "top_p_milli": "opt_int", "top_k": "opt_int", "max_tokens": "opt_int",
                   "logprobs": "opt_int", "allowed_tokens": "opt_int", "bad_words": "opt_int", "logit_bias": "opt_int"},
        "complete_requires": ("api_validated", "runner_received", "sampler_applied"),
    },
    "profiler-control": {
        "checks": ("LIF-14", "OBS-03"),
        "events": ("api_call", "rpc", "worker_control", "backend_state", "stop_failed", "trace_callback", "output_complete",
                   "next_collection"),
        "fields": {"at_ms": "ms", "causal_sha256": "sha", "artifact_sha256": "opt_sha", "file_generation": "opt_id", "state": "opt_id"},
        "complete_requires": ("api_call", "rpc", "worker_control", "output_complete"),
    },
}


class GpuInputError(ValueError):
    pass


def _check(value, kind):
    optional = kind.startswith("opt_")
    if optional and value is None:
        return True
    base = kind[4:] if optional else kind
    if base == "sha":
        return type(value) is str and _SHA.fullmatch(value) is not None
    if base == "id":
        return type(value) is str and _ID.fullmatch(value) is not None
    if base in ("int", "ms"):
        return type(value) is int and 0 <= value <= 2**63 - 1
    if base == "bool":
        return type(value) is bool
    if base.startswith("enum:"):
        return value in base[5:].split("|")
    raise GpuInputError("GPU_INPUT_CONTRACT_INTERNAL")


def _binding(value):
    if type(value) is not dict or set(value) != set(_BINDING):
        raise GpuInputError("GPU_INPUT_BINDING_INVALID")
    if (not _check(value["pod_uid"], "id") or not _check(value["container_id"], "id")
            or not all(_check(value[k], "int") for k in ("restart_count", "worker_pid", "start_ticks", "rank"))
            or value["worker_pid"] < 1):
        raise GpuInputError("GPU_INPUT_BINDING_INVALID")
    return tuple(value[k] for k in _BINDING)


def validate(record, *, kind, environment_ref, target_sha256, prepared_sha256, session):
    """닫힌 형식 검사와 판정 재료 정리. 실패는 GpuInputError. 형식이 맞아도 지원·완료는 별도 필드로 낸다."""
    spec = KINDS.get(kind)
    if spec is None:
        raise GpuInputError("GPU_INPUT_KIND_UNSUPPORTED")
    keys = {"schema", "kind", "producer", "environment_ref", "target_sha256", "prepared_sha256", "session",
            "bindings", "events", "completeness"}
    if type(record) is not dict or set(record) != keys or record["schema"] != SCHEMA or record["kind"] != kind:
        raise GpuInputError("GPU_INPUT_ENVELOPE_INVALID")
    producer = record["producer"]
    if (type(producer) is not dict or set(producer) != {"name", "version", "source_sha256"}
            or not _check(producer["name"], "id") or not _check(producer["version"], "id")
            or not _check(producer["source_sha256"], "sha")):
        raise GpuInputError("GPU_INPUT_PRODUCER_INVALID")
    if (record["environment_ref"] != environment_ref or record["target_sha256"] != target_sha256
            or record["prepared_sha256"] != prepared_sha256 or record["session"] != session
            or type(session) is not str or _SESSION.fullmatch(session) is None):
        raise GpuInputError("GPU_INPUT_SCOPE_MISMATCH")
    bindings = record["bindings"]
    if type(bindings) is not list or not 1 <= len(bindings) <= 64:
        raise GpuInputError("GPU_INPUT_BINDING_INVALID")
    generations = [_binding(b) for b in bindings]
    if len(set(generations)) != len(generations):
        raise GpuInputError("GPU_INPUT_BINDING_INVALID")
    completeness = record["completeness"]
    if (type(completeness) is not dict or set(completeness) != {"source_ended", "dropped", "diagnostic_codes"}
            or type(completeness["source_ended"]) is not bool or not _check(completeness["dropped"], "int")
            or type(completeness["diagnostic_codes"]) is not list
            or not all(_check(c, "id") for c in completeness["diagnostic_codes"])):
        raise GpuInputError("GPU_INPUT_COMPLETENESS_INVALID")
    events = record["events"]
    if type(events) is not list or len(events) > MAX_EVENTS:
        raise GpuInputError("GPU_INPUT_EVENTS_INVALID")
    row_keys = {"seq", "event", "binding", *spec["fields"]}
    for index, row in enumerate(events):
        if (type(row) is not dict or set(row) != row_keys or row["seq"] != index or row["event"] not in spec["events"]
                or type(row["binding"]) is not int or not 0 <= row["binding"] < len(generations)
                or not all(_check(row[name], form) for name, form in spec["fields"].items())):
            raise GpuInputError("GPU_INPUT_EVENT_INVALID")
    supported = SUPPORTED_PRODUCERS.get((producer["name"], producer["version"])) == producer["source_sha256"]
    seen = {row["event"] for row in events}
    gaps = []
    if not supported:
        gaps.append("GPU_INPUT_PRODUCER_UNSUPPORTED")
    if not completeness["source_ended"] or completeness["dropped"]:
        gaps.append("GPU_INPUT_SOURCE_INCOMPLETE")
    gaps += [f"GPU_INPUT_REQUIRED_EVENT_MISSING:{name}" for name in spec["complete_requires"] if name not in seen]
    gaps += [f"GPU_INPUT_PRODUCER_DIAGNOSTIC:{code}" for code in completeness["diagnostic_codes"]]
    if kind == 'runner-consumption':
        gaps += _consumption_gaps(events)
    return {"kind": kind, "checks": list(spec["checks"]), "supported": supported, "producer_authenticated": False,
            "complete": supported and not gaps, "gaps": gaps, "generations": len(generations), "events": len(events),
            "violation_candidates": violation_candidates(kind, events)}


def violation_candidates(kind, events):
    """형식만으로 보이는 위반 후보. 확정 위반이 아니며 소비자가 정책과 지원 여부로 판정한다. 뒤의 오류가 지우지 않는다."""
    out = []
    if kind == "request-attempt":
        finished = {(r["request_sha256"], r["attempt"]) for r in events if r["event"] in ("abort", "finished")}
        for row in events:
            if row["event"] == "retry" and row["next_attempt"] is None:
                out.append({"seq": row["seq"], "reason": "retry_without_new_attempt"})
            if (row["event"] in ("stage", "kv_owned") and row["consumed_by_attempt"] is not None
                    and (row["request_sha256"], row["consumed_by_attempt"]) in finished
                    and row["consumed_by_attempt"] != row["attempt"]):
                out.append({"seq": row["seq"], "reason": "consumption_by_finished_attempt"})
    elif kind == "generation-transition":
        in_flight = {r["request_sha256"] for r in events if r["event"] == "in_flight" and r["request_sha256"]}
        mixed = {}
        for row in events:
            if row["event"] == "consumed" and row["request_sha256"] in in_flight:
                mixed.setdefault((row["request_sha256"], row["component"]), set()).add(row["generation"])
        out += [{"request_sha256": key[0], "component": key[1], "reason": "mixed_generation_consumption"}
                for key, values in sorted(mixed.items()) if len(values) > 1]
    elif kind == "failed-group-resources":
        before = {r["resource_sha256"] for r in events if r["event"] == "inventory_before"}
        after = {r["resource_sha256"] for r in events if r["event"] == "inventory_after"}
        released = {r["resource_sha256"] for r in events if r["event"] == "released"}
        out += [{"resource_sha256": item, "reason": "resource_remaining_after_failure"}
                for item in sorted((before & after) - released)]
    elif kind == "runner-consumption":
        out += _consumption_candidates(events)
    elif kind == "rank-fault":
        out += [{"seq": r["seq"], "reason": "fault_ack_missing"} for r in events if r["event"] == "fault_applied" and not r["ack"]]
    return out


_COMPARED = ("temperature_milli", "top_p_milli", "top_k", "logprobs", "allowed_tokens")


def _consumption_gaps(events):
    gaps = set()
    for request in {r['request_sha256'] for r in events if r['event'] == 'api_validated'}:
        rows = [r for r in events if r['request_sha256'] == request]
        api = next(r for r in rows if r['event'] == 'api_validated')
        if api['logit_bias']:
            gaps.add('GPU_INPUT_CONSUMPTION_FIELD_UNOBSERVED:sampler_applied:logit_bias')
        for stage in ('runner_received', 'sampler_applied'):
            stage_rows = [r for r in rows if r['event'] == stage]
            if not stage_rows:
                gaps.add(f'GPU_INPUT_CONSUMPTION_STAGE_MISSING:{stage}')
            fields = _COMPARED + ('bad_words',) + (('max_tokens', 'prompt_tokens', 'max_token_id', 'logit_bias')
                                                   if stage == 'runner_received' else ())
            for field in fields:
                if api[field] is None or any(r[field] is None for r in stage_rows):
                    gaps.add(f'GPU_INPUT_CONSUMPTION_FIELD_UNOBSERVED:{stage}:{field}')
    return sorted(gaps)


def _consumption_candidates(events):
    """API에서 받은 값과 runner·sampler 값의 차이. bad_words는 단어와 토큰 변형 수가 달라 있음/없음만 본다."""
    out = []
    by = {}
    for row in events:
        by.setdefault(row["request_sha256"], []).append(row)
    for request, rows in sorted(by.items()):
        api = next((r for r in rows if r["event"] == "api_validated"), None)
        if any(r["event"] == "api_rejected" for r in rows) and any(r["event"] in ("runner_received", "sampler_applied") for r in rows):
            out.append({"request_sha256": request, "reason": "rejected_input_consumed"})
        if api is None:
            continue
        for row in rows:
            if row["event"] not in ("runner_received", "sampler_applied"):
                continue
            fields = _COMPARED + (("max_tokens", "prompt_tokens", "max_token_id") if row["event"] == "runner_received" else ())
            changed = sorted(f for f in fields if api[f] is not None and row[f] is not None and api[f] != row[f])
            if api["bad_words"] is not None and row["bad_words"] is not None and bool(api["bad_words"]) != bool(row["bad_words"]):
                changed.append("bad_words")
            if changed:
                out.append({"request_sha256": request, "seq": row["seq"], "stage": row["event"], "fields": changed,
                            "reason": "value_changed_before_consumption"})
    return out
