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
