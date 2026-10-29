"""Reviewed native termination facts. Declared links never prove GPU reclaim."""
from __future__ import annotations
import hashlib
import json
import re
from .vllm_lifecycle_links import assignment, asgi_assignment

SCOPES = frozenset({"asgi_transport", "frontend_request", "engine_core", "scheduler_state",
    "worker_batch", "kv_references", "gpu_transfers"})
MAX_EVENTS = 4096


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()


def clock(context):
    return tuple(context.get(k) for k in ("boot_sha256","time_namespace","time_offsets_sha256"))


def project_native(packet, request_sha256, max_delay_ms, required_scopes):
    result={"binding_observed":False,"anchor":None,"events":[],"deadline_conflict_candidates":[],
        "scope_status":[{"scope":s,"required":s in required_scopes,"observed_events":0,
            "termination_verified":False} for s in sorted(SCOPES)],
        "diagnostic_codes":[],"whole_scope_complete":False,"worker_gpu_release_verified":False,
        "scope":"source-correlated native claims; no authenticated producer, absence or full resource verdict"}
    if not packet:
        result["diagnostic_codes"].append("TERMINATION_NATIVE_PACKET_UNAVAILABLE");return result
    if type(request_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}",request_sha256) is None:
        result["diagnostic_codes"].append("TERMINATION_SELECTED_REQUEST_UNAVAILABLE");return result
    if not packet.get("recorded_files_covered"):
        result["diagnostic_codes"].append("TERMINATION_NATIVE_COVERAGE_UNVERIFIED")
    matches=[]
    for index,recording in enumerate(packet.get("recordings",[])):
        capture=recording.get("capture")
        if not capture:continue
        rows=capture["records"];context=rows[0]["payload"]["context"]
        # A live selection callback supplies invocation identity; an inherited
        # header ContextVar alone cannot establish a fresh native assignment.
        selections=[r for r in rows if r["event"]=="SELECTION" and r["payload"]["phase"]=="RETURN"
            and r["payload"]["point"]=="InputProcessor.assign_request_id"
            and r["payload"]["metadata"].get("request_selection",{}).get("asgi_request_sha256")==request_sha256]
        for row in rows:
            pair=assignment(row) if row["event"]=="NATIVE" else None
            if pair is None or asgi_assignment(row)!=request_sha256:continue
            selected=[s for s in selections if s["payload"]["metadata"]["request_selection"].get("selected_request_id_sha256")==pair[1]
                and s["payload"]["metadata"]["request_selection"].get("request_id_observed") is True
                and type(s["payload"]["metadata"]["request_selection"].get("asgi_invocation_sha256")) is str
                and re.fullmatch(r"[0-9a-f]{64}",s["payload"]["metadata"]["request_selection"]["asgi_invocation_sha256"])
                and s["payload"]["metadata"]["request_selection"].get("entry_path_sha256")==hashlib.sha256(b"/v1/completions").hexdigest()
                and s["payload"]["metadata"]["request_selection"].get("entry_method_sha256")==hashlib.sha256(b"POST").hexdigest()]
            if len(selected)!=1:continue
            invocation=selected[0]["payload"]["metadata"]["request_selection"]["asgi_invocation_sha256"]
            disconnects=[r for r in rows if r["event"]=="ASGI" and r["payload"]["request_sha256"]==request_sha256
                and r["payload"]["phase"]=="DISCONNECT_RECEIVED" and r["monotonic_ns"]>=row["monotonic_ns"]]
            if len(disconnects)!=1:continue
            if any(r["event"]=="ASGI" and r["payload"]["request_sha256"]==request_sha256
                    and r["payload"]["phase"] in {"APP_RETURN","APP_RAISE"}
                    and row["seq"]<r["seq"]<disconnects[0]["seq"] for r in rows):continue
            matches.append((index,recording,capture,context,pair[1],invocation,disconnects[0]))
    if len(matches)!=1:
        result["diagnostic_codes"].append("TERMINATION_UNIQUE_NATIVE_DISCONNECT_BINDING_UNAVAILABLE");return result
    index,recording,capture,context,native_id,invocation,anchor=matches[0]
    physical=bool(recording.get("session_owner_match") and recording.get("listed_file_identity_match")
        and capture.get("process_context_verified"))
    result["binding_observed"]=True
    result["anchor"]={"recording":index,"seq":anchor["seq"],"pid":context["pid"],"start_ticks":context["start_ticks"],
        "native_request_sha256":native_id,"asgi_request_sha256":request_sha256,"asgi_invocation_sha256":invocation,
        "monotonic_ns":anchor["monotonic_ns"],"clock_domain_sha256":digest(clock(context)),
        "physical_process_binding_verified":physical,"producer_authenticated":False}
    bounded=True
    for recording_index,item in enumerate(packet.get("recordings",[])):
        value=item.get("capture")
        if not value:continue
        current=value["records"][0]["payload"]["context"]
        comparable=bool(physical and item.get("session_owner_match") and item.get("listed_file_identity_match")
            and value.get("process_context_verified") and clock(current)==clock(context))
        for row in value["records"]:
            payload=row["payload"];scope=None;active=None;remaining=None
            if row["event"]=="ASGI" and recording_index==index and payload["request_sha256"]==request_sha256:
                if payload["phase"] not in {"APP_RETURN","APP_RAISE"}:continue
                scope="asgi_transport"
            elif row["event"]=="NATIVE":
                selected=[r for r in payload["requests"] if r["request_sha256"]==native_id]
                if len(selected)!=1:continue
                point=payload["point"];state=selected[0]
                if point.startswith("OutputProcessor."):scope="frontend_request";active=state["output_state_present"]
                elif point=="EngineCore.abort_requests":scope="engine_core";active=state["request_present"]
                elif point.startswith("Scheduler."):scope="scheduler_state";active=state["request_present"]
                elif point.startswith(("GPUModelRunner.","InputBatch.")):scope="worker_batch";active=state["batch_row_present"]
                elif point.startswith(("KVCacheManager.","SingleTypeKVCacheManager.")):
                    scope="kv_references"
                    ownership=payload["metadata"].get("ownership",{})
                    groups=[g for g in ownership.get("groups",[]) if g["request_sha256"]==native_id]
                    if ownership.get("complete") is True and groups:
                        remaining=sum(len(g["block_sha256"]) for g in groups);active=remaining>0
                else:continue
            else:continue
            if len(result["events"])>=MAX_EVENTS:bounded=False;continue
            delta=(row["monotonic_ns"]-anchor["monotonic_ns"]) if comparable else None
            if delta is not None and delta<0:continue
            fact={"recording":recording_index,"seq":row["seq"],"pid":current["pid"],"start_ticks":current["start_ticks"],
                "scope":scope,"point":payload.get("point"),"phase":payload["phase"],
                "native_request_sha256":native_id,"monotonic_ns":row["monotonic_ns"],
                "declared_elapsed_ns":delta,"same_clock_domain_verified":comparable,
                "active_state_observed":active,"remaining_declared_blocks":remaining,
                "image_digest_verified":value.get("image_digest_verified") is True,
                "producer_authenticated":False,"whole_scope_verified":False}
            result["events"].append(fact)
            if (scope in required_scopes and delta is not None and delta>max_delay_ms*1_000_000 and active is True):
                result["deadline_conflict_candidates"].append({**fact,"configured_deadline_ms":max_delay_ms,"confirmed_violation":False})
    for row in result["scope_status"]:row["observed_events"]=sum(e["scope"]==row["scope"] for e in result["events"])
    if not bounded:result["diagnostic_codes"].append("TERMINATION_EVENT_BOUND_REACHED")
    if not physical:result["diagnostic_codes"].append("TERMINATION_NATIVE_PROCESS_BINDING_UNVERIFIED")
    if any(not e["same_clock_domain_verified"] for e in result["events"]):result["diagnostic_codes"].append("TERMINATION_CLOCK_DOMAIN_UNVERIFIED")
    result["diagnostic_codes"].append("TERMINATION_FULL_RESOURCE_RECLAIM_UNVERIFIED")
    return result
