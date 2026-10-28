"""Declared ASGI/auth/serving associations; no authorization or route verdict."""
from __future__ import annotations

import hashlib
import json

from .vllm_lifecycle_profiles import route_observation,request_selection_observation
from .vllm_lifecycle_sensor import ROUTE_POINTS,REQUEST_SELECTION_POINTS

MAX_EVENTS=256


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()


def index(recordings):
    events=[];invocations={};headers={};complete=True
    for capture_index,recording in enumerate(recordings):
        capture=recording.get("capture")
        if capture is None:continue
        context=capture["records"][0]["payload"]["context"]
        domain=_digest({"owner":capture["provenance"]["owner"],"context":context})
        for row in capture["records"]:
            observed=route_observation(row)
            if observed is None:
                if row["event"]=="NATIVE" and row["payload"]["point"] in ROUTE_POINTS:complete=False
                continue
            if len(events)>=MAX_EVENTS:complete=False;continue
            metadata=observed["metadata"];key=_digest([domain,metadata["asgi_invocation_sha256"]])
            entry=invocations.setdefault(key,{"domain_sha256":domain,"asgi_invocation_sha256":metadata["asgi_invocation_sha256"],
                **{k:metadata[k] for k in ("asgi_request_sha256","entry_path_sha256","entry_root_path_sha256","entry_method_sha256")},
                "events":[],"metadata_consistent":True})
            if any(entry[k]!=metadata[k] for k in ("asgi_request_sha256","entry_path_sha256","entry_root_path_sha256","entry_method_sha256")):
                entry["metadata_consistent"]=False;complete=False
            headers.setdefault(metadata["asgi_request_sha256"],set()).add(key)
            reference=[capture_index,row["seq"]]
            entry["events"].append(reference)
            events.append({"event":reference,"domain_sha256":domain,**observed})
    return {"schema":"guard-route-authentication-index/1","events":events,"invocations":invocations,
        "ambiguous_asgi_request_sha256":sorted(key for key,value in headers.items() if len(value)>1),
        "stored_index_complete":complete,"producer_authenticated":False,"authorization_verified":False,
        "registered_handler_verified":False,"whole_route_coverage_verified":False,"security":"UNKNOWN",
        "scope":"declared live ASGI context, auth bool, route lookup and serving call observations; no caller or routing verdict"}


def handler_index(recordings):
    """Link fixed endpoint callbacks to auth observations within one physical recording.

    This reports declared facts only. It does not identify a principal, trust a
    Gateway transformation, prove missing events absent, or issue a verdict.
    """
    from .vllm_lifecycle_profiles import handler_observation
    from .vllm_lifecycle_sensor import HANDLER_POINTS
    events=[];invocations={};headers={};complete=True
    context_keys=("asgi_request_sha256","entry_path_sha256","entry_root_path_sha256","entry_method_sha256")
    for capture_index,recording in enumerate(recordings):
        capture=recording.get("capture")
        if capture is None:
            complete=False
            continue
        rows=capture["records"];header=rows[0]["payload"]
        domain=_digest({"owner":capture["provenance"]["owner"],"context":header["context"],
            "session":header["session"],"profile":header["profile"]})
        ready=set()
        closed=rows[-1]["event"]=="END" and rows[-1]["payload"]["reason"] in {"SEALED","PROCESS_EXIT"}
        for row in rows:
            payload=row["payload"]
            if row["event"]=="MODULE":
                if payload["verified"]:ready.add(payload["module"])
                else:ready.discard(payload["module"])
            observed=route_observation(row) or handler_observation(row)
            if observed is None:
                if row["event"]=="NATIVE" and payload["point"] in ROUTE_POINTS|set(HANDLER_POINTS):complete=False
                continue
            if len(events)>=MAX_EVENTS:
                complete=False
                continue
            metadata=observed["metadata"];key=_digest([domain,metadata["asgi_invocation_sha256"]])
            entry=invocations.setdefault(key,{"domain_sha256":domain,
                "asgi_invocation_sha256":metadata["asgi_invocation_sha256"],
                **{k:metadata[k] for k in context_keys},"events":[],"metadata_consistent":True,
                "recording_closed":closed,"source_modules_observed":True})
            if any(entry[k]!=metadata[k] for k in context_keys):
                entry["metadata_consistent"]=False;complete=False
            module=("vllm.entrypoints.serve.middleware.authenticate" if observed["point"].startswith("AuthenticationMiddleware.")
                else "vllm.entrypoints.openai.completion.api_router" if observed["point"]=="api_router.create_completion"
                else "vllm.entrypoints.openai.models.api_router" if observed["point"].startswith("api_router.")
                else "vllm.entrypoints.openai.models.serving")
            entry["source_modules_observed"] &= module in ready
            if module not in ready:complete=False
            headers.setdefault(metadata["asgi_request_sha256"],set()).add(key)
            reference=[capture_index,row["seq"]]
            entry["events"].append(reference)
            events.append({"event":reference,"domain_sha256":domain,**observed})
        if not closed:complete=False
    by_reference={tuple(event["event"]):event for event in events}
    for entry in invocations.values():
        selected=[by_reference[tuple(ref)] for ref in entry["events"]]
        auth=[event for event in selected if event["point"]=="AuthenticationMiddleware.verify_token"
            and event["phase"]=="RETURN" and event["metadata"]["token_verified"] is not None]
        handler=[event for event in selected if event["point"] in HANDLER_POINTS and event["phase"]=="CALL"]
        # Preserve individual results rather than choose a successful one from
        # duplicate, recursive, contradictory or interrupted authentication calls.
        entry["token_results"]=[{"event":event["event"],"auth_call_sha256":event["metadata"]["auth_call_sha256"],
            "token_verified":event["metadata"]["token_verified"]} for event in auth]
        entry["registered_handler_observed"]=any(event["metadata"]["registered_route_observed"] for event in handler)
        entry["handler_return_observed"]=any(event["point"] in HANDLER_POINTS and event["phase"]=="RETURN"
            and event["metadata"]["handler_returned"] is True for event in selected)
        auth_calls=[event for event in selected if event["point"]=="AuthenticationMiddleware.__call__" and event["phase"]=="CALL"]
        before=[event for event in auth if handler and event["event"][0]==handler[0]["event"][0]
            and event["event"][1]<handler[0]["event"][1]]
        entry["accepted_token_before_handler_observed"]=any(event["metadata"]["token_verified"] for event in before)
        entry["rejected_token_before_handler_observed"]=any(not event["metadata"]["token_verified"] for event in before)
        entry["path_change_observed"]=None
        if len(auth_calls)==1 and len(handler)==1:
            first=auth_calls[0]["metadata"]["observed_path_sha256"]
            last=handler[0]["metadata"]["observed_path_sha256"]
            if first is not None and last is not None:entry["path_change_observed"]=first!=last
        entry["handler_coverage_verified"]=False
        entry["security"]="UNKNOWN"
    return {"schema":"guard-route-handler-index/1","events":events,"invocations":invocations,
        "ambiguous_asgi_request_sha256":sorted(key for key,value in headers.items() if len(value)>1),
        "stored_index_complete":complete,"producer_authenticated":False,"registered_handler_verified":False,
        "authorization_verified":False,"whole_route_coverage_verified":False,"security":"UNKNOWN",
        "scope":"declared registered GET /v1/models and POST /v1/completions callback identity and auth/path association; no principal, policy or absence verdict"}


def selection_index(recordings):
    events=[];invocations={};headers={};complete=True
    for capture_index,recording in enumerate(recordings):
        capture=recording.get("capture")
        if capture is None:continue
        context=capture["records"][0]["payload"]["context"]
        domain=_digest({"owner":capture["provenance"]["owner"],"context":context})
        for row in capture["records"]:
            observed=request_selection_observation(row)
            if observed is None:
                if row["event"] in {"NATIVE","SELECTION"} and (row["payload"]["point"] in REQUEST_SELECTION_POINTS
                        or row["payload"]["point"]=="InputProcessor.assign_request_id"
                            and row["payload"].get("diagnostic")=="SAMPLE_UNAVAILABLE"):complete=False
                continue
            if len(events)>=MAX_EVENTS:complete=False;continue
            metadata=observed["metadata"];key=_digest([domain,metadata["asgi_invocation_sha256"]])
            entry=invocations.setdefault(key,{"domain_sha256":domain,"asgi_invocation_sha256":metadata["asgi_invocation_sha256"],
                **{k:metadata[k] for k in ("asgi_request_sha256","entry_path_sha256","entry_root_path_sha256","entry_method_sha256")},
                "events":[],"metadata_consistent":True})
            if any(entry[k]!=metadata[k] for k in ("asgi_request_sha256","entry_path_sha256","entry_root_path_sha256","entry_method_sha256")):
                entry["metadata_consistent"]=False;complete=False
            if metadata["asgi_request_sha256"] is not None:headers.setdefault(metadata["asgi_request_sha256"],set()).add(key)
            reference=[capture_index,row["seq"]]
            entry["events"].append(reference)
            events.append({"event":reference,"domain_sha256":domain,**observed})
    return {"schema":"guard-request-selection-index/1","events":events,"invocations":invocations,
        "ambiguous_asgi_request_sha256":sorted(key for key,value in headers.items() if len(value)>1),
        "stored_index_complete":complete,"producer_authenticated":False,"principal_identity_verified":False,
        "authorization_verified":False,"worker_consumption_verified":False,"security":"UNKNOWN",
        "scope":"declared native helper returns and InputProcessor handoff fields in live HTTP context; no trusted identity or final worker verdict"}
