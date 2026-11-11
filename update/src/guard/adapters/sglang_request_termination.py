"""Strict SGLang lifecycle facts for a selected owned single-worker probe.

Technical source-bound observations and an organization security verdict are
separate: this projector never authenticates a producer or emits security PASS.
"""
from __future__ import annotations

import re
from . import sglang_request_termination_sensor as sensor

SCOPES = frozenset({"asgi_transport", "frontend_request", "scheduler_state",
                    "worker_batch", "kv_references", "gpu_transfers"})
STREAMS = frozenset({"forward_stream", "copy_stream", "schedule_stream", "return_stream"})
POINTS = frozenset(cls + "." + name for classes in sensor.POINTS.values()
                   for cls, methods in classes.items() for name in methods) | {"common.release_kv_cache"}
POINT_MODULE = {cls + "." + name: module for module, classes in sensor.POINTS.items()
                for cls, methods in classes.items() for name in methods}
POINT_MODULE["common.release_kv_cache"] = "sglang.srt.mem_cache.common"


def hashed(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_payload(event, value):
    if type(value) is not dict:
        raise ValueError("SGLANG_NATIVE_PAYLOAD_INVALID")
    if event == "SG_ASGI":
        if (set(value) != {"phase", "request_sha256", "invocation_sha256"}
                or value["phase"] not in {"DISCONNECT_RECEIVED", "APP_RETURN", "APP_RAISE"}
                or any(v is not None and not hashed(v) for k, v in value.items() if k != "phase")):
            raise ValueError("SGLANG_ASGI_INVALID")
    elif event == "SG_NATIVE":
        keys = {"point", "phase", "operation", "source_module", "source_verified", "requests",
                "asgi_request_sha256", "asgi_invocation_sha256", "qualified_single_scheduler"}
        if (set(value) != keys or value["point"] not in POINTS
                or value["phase"] not in {"CALL", "RETURN", "RAISE"}
                or type(value["operation"]) is not int or not 0 < value["operation"] < 2**63
                or value["source_module"] not in sensor.SOURCE_SHA
                or value["source_module"] != POINT_MODULE[value["point"]]
                or type(value["source_verified"]) is not bool
                or type(value["qualified_single_scheduler"]) is not bool
                or any(value[k] is not None and not hashed(value[k])
                       for k in ("asgi_request_sha256", "asgi_invocation_sha256"))
                or type(value["requests"]) is not list or not 0 < len(value["requests"]) <= sensor.MAX_IDS):
            raise ValueError("SGLANG_NATIVE_INVALID")
        seen = set()
        for row in value["requests"]:
            if (type(row) is not dict or set(row) != {"request_sha256", "state_present", "kv_registered",
                                                     "kv_allocated", "mamba_present"}
                    or not hashed(row["request_sha256"]) or row["request_sha256"] in seen
                    or any(row[k] is not None and type(row[k]) is not bool
                           for k in ("state_present", "kv_registered", "mamba_present"))
                    or row["kv_allocated"] is not None and
                    (type(row["kv_allocated"]) is not int or not 0 <= row["kv_allocated"] < 2**63)):
                raise ValueError("SGLANG_REQUEST_STATE_INVALID")
            seen.add(row["request_sha256"])
    elif event == "SG_GPU_FENCE":
        if (set(value) != {"operation", "stream", "requests", "completed", "timed_out", "qualified_single_scheduler"}
                or type(value["operation"]) is not int or not 0 < value["operation"] < 2**63
                or value["stream"] not in STREAMS or type(value["requests"]) is not list
                or not 0 < len(value["requests"]) <= sensor.MAX_IDS
                or not all(hashed(v) for v in value["requests"])
                or len(set(value["requests"])) != len(value["requests"])
                or any(type(value[k]) is not bool for k in ("completed", "timed_out", "qualified_single_scheduler"))
                or value["completed"] == value["timed_out"]):
            raise ValueError("SGLANG_GPU_FENCE_INVALID")
    elif event == "SG_GAP":
        if (not {"code"} <= set(value) <= {"code", "operation"}
                or value["code"] not in {"STATE_OBSERVATION_UNAVAILABLE", "GPU_FENCE_UNAVAILABLE",
                                         "CALL_OBSERVATION_UNAVAILABLE", "REQUEST_TRACKING_BOUND"}
                or "operation" in value and (type(value["operation"]) is not int
                                             or not 0 < value["operation"] < 2**63)):
            raise ValueError("SGLANG_GAP_INVALID")
    else:
        raise ValueError("SGLANG_NATIVE_EVENT_UNSUPPORTED")
    return value


def clock(context):
    keys = ("boot_sha256", "time_namespace", "time_offsets_sha256")
    if (not hashed(context.get(keys[0])) or not hashed(context.get(keys[2]))
            or type(context.get(keys[1])) is not int or context[keys[1]] <= 0):
        return None
    return tuple(context[k] for k in keys)


def project_native(packet, request_sha256, max_delay_ms, required_scopes, *, _checkpoint=False):
    required = set(required_scopes) if type(required_scopes) in (list, tuple, set, frozenset) and all(type(s) is str for s in required_scopes) else set()
    result = {"binding_observed": False, "anchor": None, "events": [], "diagnostic_codes": [],
        "scope_status": [{"scope": s, "required": s in required, "termination_verified": False}
                         for s in sorted(SCOPES)], "native_termination_verified": False,
        "worker_gpu_release_verified": False, "producer_authenticated": False, "security": "UNKNOWN"}
    if _checkpoint:
        from guard.executors.lifecycle_checkpoint import covered, recording_covered
        result.update(closure_basis='live_checkpoint', checkpoint_coverage_verified=False)
    def gap(code):
        if code not in result["diagnostic_codes"]:
            result["diagnostic_codes"].append(code)
    if (not hashed(request_sha256) or type(max_delay_ms) is not int or not 0 < max_delay_ms <= 600000
            or not required or not required <= SCOPES):
        gap("SGLANG_TERMINATION_SELECTION_INVALID")
        return result
    if type(packet) is not dict or type(packet.get("recordings")) is not list:
        gap("SGLANG_NATIVE_PACKET_UNAVAILABLE")
        return result
    if packet.get("recorded_files_covered") is not True or packet.get("live_census_stable") is not True:
        gap("SGLANG_NATIVE_COVERAGE_UNVERIFIED")
    for side in ("before", "after"):
        snapshot = packet.get(side)
        metadata = snapshot.get("metadata") if type(snapshot) is dict else None
        if not _checkpoint and (type(metadata) is not dict or metadata.get("sealed") is not True):
            gap("SGLANG_NATIVE_SESSION_UNSEALED")
    if _checkpoint and not covered(packet):gap("SGLANG_CHECKPOINT_COVERAGE_UNVERIFIED")
    recordings = []
    for index, recording in enumerate(packet["recordings"]):
        capture = recording.get("capture") if type(recording) is dict else None
        rows = capture.get("records") if type(capture) is dict else None
        if (type(rows) is not list or not rows or len(rows) > sensor.MAX_ROWS
                or not all(type(r) is dict and type(r.get("payload")) is dict for r in rows)):
            gap("SGLANG_NATIVE_RECORDING_UNAVAILABLE")
            continue
        if _checkpoint:rows=rows+capture.get("checkpoint_tail",[])
        summary = capture.get("summary")
        if not _checkpoint and (type(summary) is not dict or summary.get("recording_closed") is not True
                or summary.get("recording_end") != "SEALED"
                or summary.get("incomplete_final_row") is not False
                or type(summary.get("discarded_tail_bytes")) is not int
                or summary["discarded_tail_bytes"] != 0
                or rows[-1].get("event") != "END" or rows[-1]["payload"] != {"reason": "SEALED"}
                or any(row.get("event") == "END" for row in rows[:-1])):
            gap("SGLANG_NATIVE_RECORDING_UNCLOSED")
        start = rows[0].get("payload", {})
        context = start.get("context", {})
        if type(context) is not dict:
            gap("SGLANG_NATIVE_PROCESS_UNVERIFIED")
            continue
        if (rows[0].get("event") != "START" or start.get("profile") != sensor.PROFILE
                or start.get("commit") != sensor.COMMIT or start.get("schema") != sensor.SCHEMA):
            gap("SGLANG_NATIVE_PROFILE_UNVERIFIED")
            continue
        physical = (recording.get("session_owner_match") is True
                    and recording.get("listed_file_identity_match") is True
                    and capture.get("process_context_verified") is True
                    and capture.get("image_digest_verified") is True and clock(context) is not None
                    and type(context.get("pid")) is int and context["pid"] > 0
                    and type(context.get("start_ticks")) is int and context["start_ticks"] > 0
                    and context["pid"] == recording.get("pid")
                    and context["start_ticks"] == recording.get("start_ticks"))
        if not physical:
            gap("SGLANG_NATIVE_PROCESS_UNVERIFIED")
        modules = {r.get("payload", {}).get("module"): r.get("payload", {}) for r in rows
                   if r.get("event") == "MODULE"}
        safe = []
        for row in rows:
            event = row.get("event")
            if event not in {"SG_NATIVE", "SG_ASGI", "SG_GPU_FENCE", "SG_GAP"}:
                continue
            try:
                payload = validate_payload(event, row.get("payload"))
                if type(row.get("monotonic_ns")) is not int or row["monotonic_ns"] < 0:
                    raise ValueError("CLOCK_INVALID")
            except (TypeError, ValueError, KeyError):
                gap("SGLANG_NATIVE_RECORD_INVALID")
                continue
            if event == "SG_GAP":
                gap("SGLANG_OBSERVER_GAP")
            if event == "SG_NATIVE":
                proof = modules.get(payload["source_module"], {})
                if (payload["source_verified"] is not True or proof.get("verified") is not True
                        or proof.get("source_sha256") != sensor.SOURCE_SHA[payload["source_module"]]
                        or not hashed(proof.get("code_sha256"))):
                    gap("SGLANG_NATIVE_SOURCE_UNVERIFIED")
            if event in {"SG_NATIVE", "SG_GPU_FENCE"} and payload.get("qualified_single_scheduler"):
                for name in ("sglang.srt.runtime_context", "sglang.srt.managers.scheduler"):
                    proof = modules.get(name, {})
                    if (proof.get("verified") is not True or proof.get("source_sha256") != sensor.SOURCE_SHA[name]
                            or not hashed(proof.get("code_sha256"))):
                        gap("SGLANG_RUNTIME_CONFIG_SOURCE_UNVERIFIED")
            safe.append(row)
        recordings.append((index, context, safe))
    def same_process(left,right):
        if not _checkpoint or packet.get('capture_mode')!='live_checkpoint_rolling':return left==right
        return packet['recordings'][left]['capture']['records'][0]['payload']['context']==packet['recordings'][right]['capture']['records'][0]['payload']['context']
    candidates = []
    for index, context, rows in recordings:
        for row in rows:
            payload = row["payload"]
            if (row["event"] != "SG_NATIVE" or payload["point"] != "TokenizerManager._init_req_state"
                    or payload["phase"] != "RETURN" or payload["asgi_request_sha256"] != request_sha256
                    or not hashed(payload["asgi_invocation_sha256"]) or len(payload["requests"]) != 1
                    or payload["requests"][0]["state_present"] is not True):
                continue
            invocation = payload["asgi_invocation_sha256"]
            disconnect = [r for other_index,_,other_rows in recordings if same_process(index,other_index) for r in other_rows if r["event"] == "SG_ASGI"
                and r["payload"]["phase"] == "DISCONNECT_RECEIVED"
                and r["payload"]["request_sha256"] == request_sha256
                and r["payload"]["invocation_sha256"] == invocation
                and r["monotonic_ns"] >= row["monotonic_ns"]]
            if len(disconnect) != 1:
                continue
            if any(r["event"] == "SG_ASGI" and r["payload"]["invocation_sha256"] == invocation
                   and r["payload"]["phase"] in {"APP_RETURN", "APP_RAISE"}
                   and row["monotonic_ns"] <= r["monotonic_ns"] < disconnect[0]["monotonic_ns"] for other_index,_,other_rows in recordings if same_process(index,other_index) for r in other_rows):
                continue
            candidates.append((index, context, payload["requests"][0]["request_sha256"], invocation, disconnect[0]))
    if len(candidates) != 1:
        gap("SGLANG_UNIQUE_DISCONNECT_BINDING_UNAVAILABLE")
        return result
    front, context, native, invocation, anchor = candidates[0]
    if _checkpoint:
        deadline = anchor['monotonic_ns'] + max_delay_ms * 1_000_000
        if not all(recording_covered(item, packet.get('session'), deadline, context, packet=packet) for item in packet['recordings']):
            gap('SGLANG_CHECKPOINT_PREFIX_UNVERIFIED')
        result['checkpoint_coverage_verified'] = not result['diagnostic_codes']
        if packet.get('capture_mode')=='live_checkpoint_rolling':result['rolling_segments_verified']=result['checkpoint_coverage_verified']
    result["binding_observed"] = True
    result["anchor"] = {"recording": front, "native_request_sha256": native,
        "asgi_invocation_sha256": invocation, "monotonic_ns": anchor["monotonic_ns"]}
    completed = set()
    checkpoint_states={scope:[] for scope in ("frontend_request","scheduler_state","worker_batch","kv_references")}
    operations, fences = [], []
    for index, current, rows in recordings:
        if clock(current) != clock(context):
            gap("SGLANG_CLOCK_DOMAIN_UNVERIFIED")
            continue
        for row in rows:
            event, payload = row["event"], row["payload"]
            delta = row["monotonic_ns"] - anchor["monotonic_ns"]
            if event == "SG_GPU_FENCE" and native in payload["requests"]:
                fences.append((index, row))
                continue
            if event == "SG_ASGI" and same_process(index,front) and payload["invocation_sha256"] == invocation:
                if payload["phase"] in {"APP_RETURN", "APP_RAISE"} and 0 <= delta <= max_delay_ms * 1_000_000:
                    completed.add("asgi_transport")
                continue
            if event != "SG_NATIVE":
                continue
            selected = [r for r in payload["requests"] if r["request_sha256"] == native]
            if len(selected) != 1:
                continue
            state, point = selected[0], payload["point"]
            if _checkpoint and payload['phase']=='RETURN' and delta>=0:
                observed_scope=None;active=None
                if same_process(index,front) and point in {'TokenizerManager._handle_batch_output','TokenizerManager._release_req_states_on_failure'}:
                    observed_scope='frontend_request';active=state['state_present']
                elif point in {'Scheduler.on_idle','Scheduler.ingest_requests'} and payload['qualified_single_scheduler']:
                    observed_scope='scheduler_state';active=state['state_present']
                elif point=='ScheduleBatch.filter_batch':observed_scope='worker_batch';active=state['state_present']
                elif point=='common.release_kv_cache':
                    observed_scope='kv_references'
                    if state['kv_registered'] is False and state['kv_allocated']==0 and state['mamba_present'] is False:active=False
                    elif state['kv_registered'] is True or type(state['kv_allocated']) is int and state['kv_allocated']>0 or state['mamba_present'] is True:active=True
                if observed_scope:checkpoint_states[observed_scope].append((row['monotonic_ns'],active))
            if point == "Scheduler.process_batch_result" and payload["phase"] == "RETURN":
                operations.append((index, row))
            if payload["phase"] != "RETURN" or not 0 <= delta <= max_delay_ms * 1_000_000:
                continue
            scope = None
            if same_process(index,front) and point in {"TokenizerManager._handle_batch_output", "TokenizerManager._release_req_states_on_failure"}:
                if state["state_present"] is False:
                    scope = "frontend_request"
            elif point in {"Scheduler.on_idle", "Scheduler.ingest_requests"} and payload["qualified_single_scheduler"]:
                if state["state_present"] is False:
                    scope = "scheduler_state"
            elif point == "ScheduleBatch.filter_batch" and state["state_present"] is False:
                scope = "worker_batch"
            elif point == "common.release_kv_cache":
                if state["kv_registered"] is False and state["kv_allocated"] == 0 and state["mamba_present"] is False:
                    scope = "kv_references"
            if scope:
                completed.add(scope)
                result["events"].append({"recording": index, "scope": scope, "point": point,
                                         "elapsed_ns": delta, "operation": payload["operation"]})
    if operations:
        owner, latest = max(operations, key=lambda item: item[1]["monotonic_ns"])
        observed = set()
        for index, row in fences:
            payload = row["payload"]
            if (same_process(index,owner) and payload["operation"] == latest["payload"]["operation"]
                    and row["monotonic_ns"] >= latest["monotonic_ns"]
                    and row["monotonic_ns"] - anchor["monotonic_ns"] <= max_delay_ms * 1_000_000
                    and payload["completed"] and not payload["timed_out"]
                    and payload["qualified_single_scheduler"] and latest["payload"]["qualified_single_scheduler"]):
                observed.add(payload["stream"])
        if observed == STREAMS:
            completed.add("gpu_transfers")
    if _checkpoint:
        from .vllm_termination_projection import _settled_completion
        for scope,states in checkpoint_states.items():
            settled=_settled_completion(states)
            if settled is None or settled>anchor['monotonic_ns']+max_delay_ms*1_000_000:
                completed.discard(scope)
    if not result["diagnostic_codes"]:
        for row in result["scope_status"]:
            row["termination_verified"] = row["scope"] in completed
        # A caller may request fewer diagnostic scopes, but that cannot weaken
        # the meaning of the complete native/resource result.
        result["native_termination_verified"] = SCOPES <= completed
        result["worker_gpu_release_verified"] = {"scheduler_state", "worker_batch", "kv_references", "gpu_transfers"} <= completed
    if not result["native_termination_verified"]:
        gap("SGLANG_TERMINATION_REQUIRED_SCOPE_UNVERIFIED")
    return result


def project_checkpoint(packet, request_sha256, max_delay_ms, required_scopes):
    return project_native(packet, request_sha256, max_delay_ms, required_scopes, _checkpoint=True)
