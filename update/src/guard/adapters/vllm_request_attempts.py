"""Source-correlated request attempts; session tags never replace native IDs."""
from __future__ import annotations
import hashlib
from .vllm_lifecycle_links import assignment, asgi_assignment
from . import vllm_attempt_sensor as attempt_sensor

MAX_EVENTS=4096


def project_attempts(packet,trials):
    result={"bindings":[],"events":[],"conflict_candidates":[],"unbound_asgi_events":[],"asgi_events":[],
        "diagnostic_codes":[],"native_attempt_binding_observed":False,"late_delivery_verified":False,
        "automatic_retry_verified":False,"whole_scope_complete":False,"asgi_invocation_binding_observed":False}
    if not packet:
        result["diagnostic_codes"].append("REQUEST_ATTEMPT_NATIVE_UNAVAILABLE");return result
    for trial in trials:
        if not trial.get("attempted"):continue
        matches=[]
        for index,recording in enumerate(packet.get("recordings",[])):
            capture=recording.get("capture")
            if not capture:continue
            rows=capture["records"];context=rows[0]["payload"]["context"]
            selected=[r for r in rows if r["event"]=="SELECTION" and r["payload"]["phase"]=="RETURN"
                and r["payload"]["point"]=="InputProcessor.assign_request_id"
                and r["payload"]["metadata"]["request_selection"].get("asgi_request_sha256")==trial["external_request_sha256"]
                and r["payload"]["metadata"]["request_selection"].get("session_observed") is True
                and r["payload"]["metadata"]["request_selection"].get("selected_session_sha256")==trial["auxiliary_session_sha256"]
                and r["payload"]["metadata"]["request_selection"].get("entry_path_sha256")==hashlib.sha256(b"/v1/completions").hexdigest()
                and r["payload"]["metadata"]["request_selection"].get("entry_method_sha256")==hashlib.sha256(b"POST").hexdigest()]
            for row in rows:
                pair=assignment(row) if row["event"]=="NATIVE" else None
                if pair is None or asgi_assignment(row)!=trial["external_request_sha256"]:continue
                aligned=[s for s in selected if s["payload"]["metadata"]["request_selection"].get("request_id_observed") is True
                    and s["payload"]["metadata"]["request_selection"].get("selected_request_id_sha256")==pair[1]]
                if len(aligned)!=1:continue
                meta=aligned[0]["payload"]["metadata"]["request_selection"]
                if not meta.get("asgi_invocation_sha256"):continue
                matches.append({"case_id":trial["case_id"],"recording":index,"seq":row["seq"],
                    "pid":context["pid"],"start_ticks":context["start_ticks"],"native_request_sha256":pair[1],
                    "external_request_sha256":trial["external_request_sha256"],"auxiliary_session_sha256":trial["auxiliary_session_sha256"],
                    "asgi_invocation_sha256":meta["asgi_invocation_sha256"],"producer_authenticated":False,
                    "entry_root_path_sha256":meta.get("entry_root_path_sha256"),
                    "physical_process_binding_verified":bool(capture.get("process_context_verified") and recording.get("session_owner_match")
                        and recording.get("listed_file_identity_match")),"image_digest_verified":capture.get("image_digest_verified") is True})
        if len(matches)==1:result["bindings"].append(matches[0])
        else:result["diagnostic_codes"].append("REQUEST_ATTEMPT_UNIQUE_SELECTION_UNAVAILABLE")
    bycase={b["case_id"]:b for b in result["bindings"]}
    a,b=(bycase.get("req12-"+s) for s in ("disconnect","replacement"))
    if a and b:
        result["native_attempt_binding_observed"]=True
        same_process=(a["pid"],a["start_ticks"],a["recording"])==(b["pid"],b["start_ticks"],b["recording"])
        if same_process and (a["native_request_sha256"]==b["native_request_sha256"] or a["asgi_invocation_sha256"]==b["asgi_invocation_sha256"]):
            result["conflict_candidates"].append({"kind":"distinct_auxiliary_sessions_share_native_or_invocation_identity",
                "cases":[a["case_id"],b["case_id"]],"confirmed_violation":False})
    shared=a["external_request_sha256"] if a else None
    for index,recording in enumerate(packet.get("recordings",[])):
        capture=recording.get("capture")
        if not capture:continue
        context=capture["records"][0]["payload"]["context"]
        for row in capture["records"]:
            if len(result["events"])+len(result["unbound_asgi_events"])+len(result["asgi_events"])>=MAX_EVENTS:
                result["diagnostic_codes"].append("REQUEST_ATTEMPT_EVENT_BOUND_REACHED")
                return result
            payload=row["payload"]
            if row["event"]=="ATTEMPT" and capture["records"][0]["payload"]["profile"]==attempt_sensor.PROFILE:
                aligned=[v for v in result["bindings"] if v["recording"]==index
                    and v["asgi_invocation_sha256"]==payload["asgi_invocation_sha256"]
                    and v["external_request_sha256"]==payload["asgi_request_sha256"]
                    and v["entry_root_path_sha256"]==payload["entry_root_path_sha256"]]
                matched=len(aligned)==1 and aligned[0]["physical_process_binding_verified"] and payload["context_live"]
                result["asgi_events"].append({"recording":index,"seq":row["seq"],"pid":context["pid"],"start_ticks":context["start_ticks"],
                    "case_id":aligned[0]["case_id"] if matched else None,"candidate_case_ids":[v["case_id"] for v in aligned],
                    "asgi_invocation_sha256":payload["asgi_invocation_sha256"],"native_request_sha256":aligned[0]["native_request_sha256"] if matched else None,
                    "monotonic_ns":row["monotonic_ns"],"phase":payload["phase"],"context_live":payload["context_live"],
                    "body_bytes":payload["body_bytes"],"body_sha256":payload["body_sha256"],"body_hash_complete":payload["body_hash_complete"],
                    "more_body":payload["more_body"],"observer_runtime":payload["observer_runtime"],"observer_code_sha256":payload["observer_code_sha256"],
                    "observer_code_contract_matched":True,"invocation_binding_observed":bool(matched),
                    "client_receipt_verified":False,"native_output_origin_verified":False,"producer_authenticated":False})
                if capture["summary"].get("attempt_incomplete_invocations"):
                    result["diagnostic_codes"].append("REQUEST_ATTEMPT_INVOCATION_CAPTURE_INCOMPLETE")
            if row["event"]=="ASGI" and shared and payload["request_sha256"]==shared:
                # Existing ASGI rows contain no invocation/native-attempt ID.
                # Neither sequence order nor a repeated header can disambiguate delivery.
                result["unbound_asgi_events"].append({"recording":index,"seq":row["seq"],"phase":payload["phase"],
                    "monotonic_ns":row["monotonic_ns"],"more_body":payload["more_body"],"attempt_case_id":None})
            if row["event"]!="NATIVE":continue
            point=payload["point"]
            if not point.startswith(("OutputProcessor.","AsyncLLM.","EngineCore.","Scheduler.","GPUModelRunner.","InputBatch.")):continue
            for state in payload["requests"]:
                bindings=[v for v in result["bindings"] if v["native_request_sha256"]==state["request_sha256"]]
                if not bindings:continue
                if len(result["events"])+len(result["unbound_asgi_events"])+len(result["asgi_events"])>=MAX_EVENTS:
                    result["diagnostic_codes"].append("REQUEST_ATTEMPT_EVENT_BOUND_REACHED");return result
                result["events"].append({"recording":index,"seq":row["seq"],"pid":context["pid"],"start_ticks":context["start_ticks"],
                    "point":point,"phase":payload["phase"],"native_request_sha256":state["request_sha256"],
                    "candidate_case_ids":[v["case_id"] for v in bindings],"request_present":state["request_present"],
                    "output_state_present":state["output_state_present"],"batch_row_present":state["batch_row_present"],
                    "monotonic_ns":row["monotonic_ns"],"delivery_recipient_verified":False})
    result["asgi_invocation_binding_observed"]=all(any(e["case_id"]=="req12-"+case and e["invocation_binding_observed"] for e in result["asgi_events"])
        for case in ("disconnect","replacement"))
    if result["asgi_events"]:
        for trial in trials:
            if trial.get("normal_shape_verified") and not any(e["case_id"]==trial["case_id"] and e["phase"]=="BODY_SEND_RETURN" for e in result["asgi_events"]):
                result["diagnostic_codes"].append("REQUEST_ATTEMPT_BODY_CALLBACK_UNAVAILABLE")
    result["diagnostic_codes"]=sorted(set(result["diagnostic_codes"]+["REQUEST_ATTEMPT_LATE_DELIVERY_UNVERIFIED","REQUEST_ATTEMPT_AUTOMATIC_RETRY_UNVERIFIED"]))
    return result
