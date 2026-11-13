"""#196 GPU 공통 센서 입력의 공개 계약(개발 담당, 1단계).

GPU 실증 담당의 소비자(LIF-03·05·06·07·08·09·10·11·12·14, PLT-01·04, OBS-02·03)가 읽을 입력의 닫힌 형식이다.
이 모듈은 생산자가 아니다. 형식, 세대 결합, 그리고 "완료 주장을 받지 않는 조건"만 정한다.

공통 봉투(모든 종류):
- 생산자 이름·판본·소스 SHA-256, 실제 ENV Ref, target SHA-256, prepared Ref SHA-256, 세션.
- 세대 결합: Pod UID, container ID, restart 횟수, worker PID, start ticks, rank.
- 완전성: 원 기록 끝 도달·누락 수·진단 코드.

판정에 쓰는 원칙(#196 본문):
- 지원 생산자 목록(`SUPPORTED_PRODUCERS`)에 없는 생산자의 기록은 형식이 맞아도 `supported=False`다. 소비자는 그 기록으로
  PASS를 만들지 않는다. 1단계에는 목록이 비어 있었고, 2단계 GPU 확인을 마친 생산자의 판본·소스 SHA만 더한다.
- 누락·지원 불일치(`gaps`)와 실제 위반 후보(`violation_candidates`)를 따로 둔다. 후속 수집·정리 오류는 앞선 위반 후보를 지우지 않는다.
- 생산자 인증은 언제나 False다. 같은 시간대라는 이유만으로 다른 세대·세션의 행을 합치지 않는다.
- 비밀 원문은 받지 않는다. 문자열 값은 닫힌 식별자 형식이거나 SHA-256이다.
"""
from __future__ import annotations

import hashlib
import re

SCHEMA = "guard-gpu-common-input/1"
_SHA = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_SESSION = re.compile(r"[0-9a-f]{32}")
MAX_EVENTS = 4096

# (생산자 이름, 판본) -> 소스 SHA-256(관측기 소스 + 변환 소스, `gpu_input_producers.*_producer()`).
# 2단계 GPU 확인(2026-11-05, AWS g6.2xlarge L4, vLLM 0.31.0, 세션 a-p1-20261105-3·-5와
# process-device-provider/V2 runner의 a-p2v2-20261105-1)에서
# 기록이 계약 형식을 통과하고 원 기록 끝까지 읽힌 생산자만 둔다. 소스가 바뀌면 SHA가 달라져 자동으로 미지원이 된다.
# 등록은 생산자 지원일 뿐이다. 각 기록의 진단·누락 gap은 그대로 남고, 판정 PASS를 뜻하지 않는다.
SUPPORTED_PRODUCERS: dict[tuple[str, str], str] = {
    ("guard-engine-callback-request-attempt", "1"): "7927d551395f09945211ae83ad1135d8edf74a41a8306f0e6072ca0727e70610",
    ("guard-engine-callback-generation-transition", "1"): "64437f216798ebf1f85e62b9856b623d4b69570517e44c933be8017e42fb64da",
    ("guard-engine-callback-health-worker", "1"): "0cf549269305cecbfcb5c6ee9de609bf6c70cbb5f1993f53aa59c30e4873dd34",
    ("guard-engine-callback-profiler-control", "1"): "4da009ecc296f50357d1b559b9fce545ab7ae3a10f4544e0e248c7147ba00764",
    ("guard-engine-callback-runner-consumption", "1"): "5c9778f43e58465fd961ea223d966ca54b1320eb55267ce35a0a955eb1aa5095",
    ("guard-engine-callback-supply-reads", "1"): "533ec8c173222136a5d2ab61f66d30e8a82719753e8740f477a5f3cb7a6e5126",
    ("guard-node-process-device-provider", "1"): "442ea49bbc88e7497cda42b359de6ad2fb938d7819f6bcfbb868684406793451",
    ("guard-engine-callback-runner-consumption-v2", "1"): "041353842e9c8a12e50ca6515cc21ba9b09fa389a8bee3842926b565c6eb8c66",
}

# 새 wire 형식은 과거 /1 GPU 등록을 상속하지 않는다. J01 재확인 전에는 비어 있다.
SUPPORTED_V4_PRODUCERS: dict[tuple[str, str], str] = {}

_BINDING = ("pod_uid", "container_id", "restart_count", "worker_pid", "start_ticks", "rank")

# 종류별: 소비 점검, 행 필드(이름 -> 형식), 행 종류 값, 완료 주장에 필요한 행 종류.
# 형식: sha | id | int | bool | ms(0 이상 정수 밀리초) | opt_sha | opt_id | opt_int | enum:<a|b>
KINDS = {
    "request-attempt": {
        "checks": ("LIF-03", "LIF-10", "LIF-12", "REQ-12"),
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
    # LIF-06: actual V1 worker model dispatch objects, separate from scheduled consumption.
    # Complete means closed, source-bound dispatches only; not weights bytes or KV kernel reads.
    "transition-dispatch": {
        "checks": ("LIF-06",),
        "events": ("forward_entered", "forward_returned", "forward_failed"),
        "fields": {"at_ms": "ms", "request_sha256": "sha", "attempt_sha256": "sha",
                   "model_sha256": "sha", "weights_sha256": "sha", "kv_sha256": "sha",
                   "native_session_sha256": "sha", "native_process_sha256": "sha", "native_call_sha256": "sha",
                   "source_sha256": "sha", "entry_sha256": "sha"},
        "complete_requires": ("forward_entered", "forward_returned"),
    },
    "rank-fault": {
        "checks": ("LIF-08",),
        "events": ("fault_applied", "fault_released", "detected", "client_delivered", "recovered"),
        "fields": {"at_ms": "ms", "ack": "bool", "fault_kind": "id", "request_sha256": "opt_sha"},
        "complete_requires": ("fault_applied", "fault_released", "detected", "recovered"),
    },
    # failure_trigger·normal_inference(2026-11-08, B 의존 반환 7): 실패를 일으킨 시각·대상과 다음 세대의 정상 추론 시각.
    # 조건부 기한은 failure_trigger의 at_ms에서 잰다. 기존 세 행과 완료 조건은 그대로다.
    "failed-group-resources": {
        "checks": ("LIF-11",),
        "events": ("inventory_before", "inventory_after", "released", "failure_trigger", "normal_inference"),
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
    # P2 SUP-03(개발 담당 2단계): 선언한 공급 루트 아래에서 실제로 연 파일과 수집 시점 bytes 해시.
    # 경로는 승인 목록과 대조하도록 상대 경로의 평문 SHA-256(path_sha256)으로 남긴다.
    "supply-reads": {
        "checks": ("SUP-03",),
        # file_observed: inotify로 본 파일. 연 프로세스를 모른다(state inotify_unattributed).
        "events": ("file_opened", "file_observed", "file_hashed"),
        "fields": {"at_ms": "ms", "root": "int", "path_sha256": "sha", "access": "enum:read|write", "size": "opt_int",
                   "bytes_sha256": "opt_sha", "state": "opt_id"},
        "complete_requires": ("file_opened", "file_hashed"),
    },
    # PLT-02(개발 담당, B 의존 반환 6): 엔진 컨테이너 안 실제 vLLM 프로세스의 unix socket 소유·상태·상대.
    # 경로는 SHA-256(path_sha256), 경로 파일의 (dev, ino)는 파일 신원(path_file_sha256)으로 따로 두며 socket 커널 inode와 섞지 않는다.
    # peer_binding은 상대 socket을 가진 이 기록의 바인딩(프로세스) 번호이고, 컨테이너 안에서 찾지 못하면 None이다.
    "ipc-ownership": {
        "checks": ("PLT-02",),
        "events": ("process", "unix_socket"),
        "fields": {"role": "enum:api_server|engine_core|worker|utility|other", "uid": "opt_int", "net_ns": "opt_int",
                   "socket_inode": "opt_int", "fd": "opt_int", "state": "enum:none|listen|accepted|connected|unconnected",
                   "path_sha256": "opt_sha", "path_file_sha256": "opt_sha", "peer_socket_inode": "opt_int", "peer_binding": "opt_int"},
        "complete_requires": ("process", "unix_socket"),
    },
    # SUP-06(개발 담당, B 의존 반환 5): worker·rank별 필수 shard·배정 파일·적재 완료/실패·READY 후보. 파일은 세션 keyed 요약.
    "worker-shard-load": {
        "checks": ("SUP-06",),
        "events": ("shard_required", "shard_assigned", "load_completed", "load_failed", "rank_ready"),
        "fields": {"at_ms": "ms", "shard_sha256": "opt_sha", "bytes": "opt_int"},
        "complete_requires": ("shard_assigned", "load_completed", "rank_ready"),
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
    if type(record) is dict and record.get('schema') == 'guard-gpu-common-input/3':
        from .actor_consumption import validate as validate_actors
        return validate_actors(record, kind=kind, environment_ref=environment_ref,
            target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)
    if type(record) is dict and record.get('schema') == 'guard-gpu-common-input/2':
        from .scoped_consumption import validate as validate_scoped
        return validate_scoped(record, kind=kind, environment_ref=environment_ref, target_sha256=target_sha256,
                               prepared_sha256=prepared_sha256, session=session)
    if type(record) is dict and record.get('schema') == 'guard-gpu-common-input/4':
        from .gpu_common_input_v4 import validate as validate_v4
        return validate_v4(record, kind=kind, environment_ref=environment_ref,
            target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)
    return _validate_v1(record, kind=kind, environment_ref=environment_ref, target_sha256=target_sha256,
                        prepared_sha256=prepared_sha256, session=session)


def _validate_v1(record, *, kind, environment_ref, target_sha256, prepared_sha256, session, sampler_logit_bias=False, registered_producers=None):
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
    registered = SUPPORTED_PRODUCERS if registered_producers is None else registered_producers
    supported = registered.get((producer["name"], producer["version"])) == producer["source_sha256"]
    seen = {row["event"] for row in events}
    gaps = []
    if not supported:
        gaps.append("GPU_INPUT_PRODUCER_UNSUPPORTED")
    if not completeness["source_ended"] or completeness["dropped"]:
        gaps.append("GPU_INPUT_SOURCE_INCOMPLETE")
    gaps += [f"GPU_INPUT_REQUIRED_EVENT_MISSING:{name}" for name in spec["complete_requires"] if name not in seen]
    gaps += [f"GPU_INPUT_PRODUCER_DIAGNOSTIC:{code}" for code in completeness["diagnostic_codes"]]
    if kind == 'runner-consumption':
        gaps += _consumption_gaps(events, sampler_logit_bias=sampler_logit_bias)
    if kind == 'transition-dispatch':
        calls = {}
        for row in events:
            if row['native_session_sha256'] != hashlib.sha256(session.encode()).hexdigest():
                raise GpuInputError('GPU_INPUT_SCOPE_MISMATCH')
            key = (row['binding'], row['native_process_sha256'], row['native_call_sha256'],
                   row['request_sha256'], row['attempt_sha256'])
            calls.setdefault(key, []).append(row)
        for rows in calls.values():
            if ([r['event'] for r in rows] not in (["forward_entered", "forward_returned"], ["forward_entered", "forward_failed"])
                    or rows[-1]['at_ms'] < rows[0]['at_ms']
                    or any(rows[0][key] != rows[-1][key] for key in ('native_session_sha256', 'source_sha256', 'entry_sha256'))):
                gaps.append('GPU_INPUT_DISPATCH_CALL_UNPAIRED')
                break
    return {"kind": kind, "checks": list(spec["checks"]), "supported": supported, "producer_authenticated": False,
            "complete": supported and not gaps, "gaps": gaps, "generations": len(generations), "events": len(events),
            "violation_candidates": violation_candidates(kind, events, sampler_logit_bias=sampler_logit_bias)}


def violation_candidates(kind, events, *, sampler_logit_bias=False):
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
    elif kind == 'transition-dispatch':
        identities = {}
        for row in events:
            if row['event'] not in ('forward_entered', 'forward_returned'):
                continue
            key = (row['binding'], row['request_sha256'], row['attempt_sha256'])
            identities.setdefault(key, set()).add(tuple(row[k] for k in ('model_sha256', 'weights_sha256', 'kv_sha256')))
        out += [{'binding': key[0], 'request_sha256': key[1], 'attempt_sha256': key[2],
                 'reason': 'dispatch_identity_changed_within_attempt'} for key, values in sorted(identities.items()) if len(values) > 1]
    elif kind == "failed-group-resources":
        before = {r["resource_sha256"] for r in events if r["event"] == "inventory_before"}
        after = {r["resource_sha256"] for r in events if r["event"] == "inventory_after"}
        released = {r["resource_sha256"] for r in events if r["event"] == "released"}
        out += [{"resource_sha256": item, "reason": "resource_remaining_after_failure"}
                for item in sorted((before & after) - released)]
    elif kind == "supply-reads":
        hashed = {(r["root"], r["path_sha256"]): r for r in events if r["event"] == "file_hashed"}
        for row in events:
            if row["event"] != "file_opened":
                continue
            if row["access"] == "write":
                out.append({"seq": row["seq"], "path_sha256": row["path_sha256"], "reason": "supply_file_written"})
            later = hashed.get((row["root"], row["path_sha256"]))
            if later is not None and row["size"] is not None and later["size"] is not None and row["size"] != later["size"]:
                out.append({"seq": row["seq"], "path_sha256": row["path_sha256"], "reason": "supply_file_changed_after_open"})
    elif kind == "runner-consumption":
        out += _consumption_candidates(events, sampler_logit_bias=sampler_logit_bias)
    elif kind == "ipc-ownership":
        roles = {r["binding"]: r["role"] for r in events if r["event"] == "process"}
        for row in events:
            if row["event"] != "unix_socket":
                continue
            if row["state"] == "listen" and roles.get(row["binding"]) in ("utility", "other"):
                out.append({"seq": row["seq"], "reason": "listener_owned_outside_serving_roles"})
            if row["state"] in ("accepted", "connected") and row["peer_binding"] is None:
                out.append({"seq": row["seq"], "reason": "peer_outside_container_roster"})
    elif kind == "worker-shard-load":
        for binding in sorted({r["binding"] for r in events}):
            rows = [r for r in events if r["binding"] == binding]
            required = {r["shard_sha256"] for r in rows if r["event"] == "shard_required"}
            assigned = {r["shard_sha256"] for r in rows if r["event"] == "shard_assigned"}
            if not any(r["event"] == "rank_ready" for r in rows):
                continue
            if required - assigned:
                out.append({"binding": binding, "reason": "missing_required_shard_reported_ready"})
            if any(r["event"] == "load_failed" for r in rows):
                out.append({"binding": binding, "reason": "failed_load_reported_ready"})
    elif kind == "rank-fault":
        out += [{"seq": r["seq"], "reason": "fault_ack_missing"} for r in events if r["event"] == "fault_applied" and not r["ack"]]
    return out


_COMPARED = ("temperature_milli", "top_p_milli", "top_k", "logprobs", "allowed_tokens")


def _consumption_gaps(events, *, sampler_logit_bias=False):
    gaps = set()
    for request in {r['request_sha256'] for r in events if r['event'] == 'api_validated'}:
        rows = [r for r in events if r['request_sha256'] == request]
        api = next(r for r in rows if r['event'] == 'api_validated')
        if api['logit_bias'] and not sampler_logit_bias:
            gaps.add('GPU_INPUT_CONSUMPTION_FIELD_UNOBSERVED:sampler_applied:logit_bias')
        for stage in ('runner_received', 'sampler_applied'):
            stage_rows = [r for r in rows if r['event'] == stage]
            if not stage_rows:
                gaps.add(f'GPU_INPUT_CONSUMPTION_STAGE_MISSING:{stage}')
            fields = _COMPARED + ('bad_words',) + (('logit_bias',) if sampler_logit_bias and stage == 'sampler_applied' else ()) + (('max_tokens', 'prompt_tokens', 'max_token_id', 'logit_bias')
                                                   if stage == 'runner_received' else ())
            for field in fields:
                if api[field] is None or any(r[field] is None for r in stage_rows):
                    gaps.add(f'GPU_INPUT_CONSUMPTION_FIELD_UNOBSERVED:{stage}:{field}')
    return sorted(gaps)


def _consumption_candidates(events, *, sampler_logit_bias=False):
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
            if sampler_logit_bias and row['event'] == 'sampler_applied':
                fields += ('logit_bias',)
            changed = sorted(f for f in fields if api[f] is not None and row[f] is not None and api[f] != row[f])
            if api["bad_words"] is not None and row["bad_words"] is not None and bool(api["bad_words"]) != bool(row["bad_words"]):
                changed.append("bad_words")
            if changed:
                out.append({"request_sha256": request, "seq": row["seq"], "stage": row["event"], "fields": changed,
                            "reason": "value_changed_before_consumption"})
    return out
