"""Selected request-boundary acceptance using the existing bounded HTTP client.

Only synthetic requests and viewer reads are sent. No policy/deployment changes,
native inference, whole-CHK security verdicts or arbitrary response bodies.
"""
from __future__ import annotations

import hashlib
import json
import time
from uuid import uuid4

from tools.selected_runtime_check import (
    UUID, concurrent_precondition, document, fact, header, json_body, origin, request,
)


def record_for(base, rid, viewer, timeout, *, marker=None):
    """Bind all raw rows to the queried ID before using any trusted context."""
    if not isinstance(rid, str) or not UUID.fullmatch(rid) or not viewer:
        return {"state": "unknown", "reason": "viewer_or_server_uuid_missing"}
    deadline = time.monotonic() + min(timeout, 5)
    while time.monotonic() < deadline:
        reply = request("GET", base + "/api/requests/" + rid, token=viewer,
                        timeout=max(.001, deadline - time.monotonic()))
        if reply.status != 200:
            return {"state": "unknown", "http_status": reply.status}
        value = document(reply)
        rows = value.get("gateway_records")
        if (not isinstance(rows, list) or not rows or
                any(not isinstance(row, dict) or row.get("request_id") != rid for row in rows)):
            return {"state": "mismatched_query_id", "record_request_ids_match_query": False}
        starts = [r for r in rows if r.get("phase") == "started"]
        finals = [r for r in rows if r.get("phase") == "final"]
        if len(finals) == 1:
            final = finals[0]
            decision = final.get("decision") or {}
            message = decision.get("message")
            if marker and isinstance(message, str):
                message = message.replace(marker, "[synthetic_marker]")
            context = ("request_id", "upstream_request_id", "observation_session_id", "run_id", "target_id", "target", "policy", "policy_bundle", "route", "auth")
            stable = len(starts) == 1 and all(starts[0].get(k) == final.get(k) for k in context)
            return {"state": "observed", "request_id": rid, "record_request_ids_match_query": True,
                    "synthetic_marker_present": marker in json.dumps(value) if marker else None,
                    "started_count": len(starts), "final_count": 1, "context_stable": stable,
                    "started_decision_result": (starts[0].get("decision") or {}).get("result") if len(starts) == 1 else None,
                    "principal_id": (final.get("auth") or {}).get("principal_id"),
                    "auth_state": (final.get("auth") or {}).get("state"),
                    "policy_sha256": (final.get("policy") or {}).get("sha256"),
                    "target_id": final.get("target_id"), "target_instance_id": (final.get("target") or {}).get("instance_id"),
                    "run_id": final.get("run_id"), "observation_session_id": final.get("observation_session_id"),
                    "decision_result": (final.get("decision") or {}).get("result"),
                    "decision_rule_id": decision.get("rule_id"),
                    "decision_message": message,
                    "record_http_status": (final.get("decision") or {}).get("http_status"),
                    "reason_code": (final.get("decision") or {}).get("reason_code"),
                    "delivery_attempted": (final.get("delivery") or {}).get("attempted"),
                    "final_outcome": final.get("final_outcome"),
                    "backend_kind": final.get("backend_kind"),
                    "cache_scope_sha256": hashlib.sha256(json.dumps((final.get("request") or {}).get("cache_isolation"), sort_keys=True).encode()).hexdigest()}
        if len(finals) > 1:
            return {"state": "ambiguous_finals", "final_count": len(finals)}
        time.sleep(min(.01, max(0, deadline - time.monotonic())))
    return {"state": "unknown", "reason": "final_deadline"}


def route_record_for(base, rid, viewer, timeout, *, marker=None):
    if not isinstance(rid, str) or not UUID.fullmatch(rid):
        return {"state": "unknown", "reason": "server_uuid_not_exposed", "witness": "external_http_only"}
    reply = request("GET", base + "/api/route-rejections?limit=100", token=viewer, timeout=timeout)
    value = document(reply) if reply.status == 200 else {}
    rows = [row for row in value.get("items", []) if isinstance(row, dict) and row.get("request_id") == rid]
    if len(rows) != 1:
        return {"state": "unknown", "http_status": reply.status, "reason": "route_audit_not_unique_in_window", "window": value.get("window")}
    row = rows[0]
    return {"state": "observed", "request_id": rid, "record_request_ids_match_query": True,
            "synthetic_marker_present": (marker.encode() in reply.body or marker in json.dumps(value)) if marker else None,
            "delivery_attempted": row.get("forwarded_to_engine"), "reason_code": row.get("reason"),
            "http_status": row.get("response_code"), "run_id": row.get("run_id"), "journal_kind": "route_rejection"}


def platform_comparison(platform, rid, raw, timeout, *, marker=None):
    if not platform or raw.get("state") != "observed":
        return {"state": "not_run"}
    reply = request("GET", origin(platform) + "/api/runtime/requests/" + rid, timeout=timeout)
    data = document(reply) if reply.status == 200 else {}
    detail = data.get("detail") or {}
    pairs = {"request_id": detail.get("request_id"), "principal_id": (detail.get("auth") or {}).get("principal_id"),
             "policy_sha256": (detail.get("policy") or {}).get("sha256"), "run_id": detail.get("run_id"),
             "decision_result": (detail.get("decision") or {}).get("result"),
             "delivery_attempted": (detail.get("delivery") or {}).get("attempted"), "final_outcome": detail.get("final_outcome")}
    return {"state": "observed" if detail else "unknown", "http_status": reply.status,
            "synthetic_marker_present": (marker.encode() in reply.body or marker in json.dumps(data)) if marker else None,
            "same_trusted_fields": bool(detail) and all(raw.get(k) == v for k, v in pairs.items()),
            "invalid": bool(data.get("detail_invalid") or data.get("event_invalid"))}


def request_matrix(gateway_url, platform_url, model, inference, viewer, *, timeout=15):
    base = origin(gateway_url)
    cases = []
    status_reply = request("GET", base + "/api/status", token=viewer, timeout=timeout)
    policy_reply = request("GET", base + "/api/policy", token=viewer, timeout=timeout)
    status = document(status_reply) if status_reply.status == 200 else {}
    current = document(policy_reply).get("current", {}) if policy_reply.status == 200 else {}
    protected = "GET /api/*" in (status.get("auth") or {}).get("protected_routes", [])
    ready = status_reply.status == policy_reply.status == 200 and protected
    cases.append(fact("REQUEST-MATRIX-PREFLIGHT", ready if status_reply.status == 200 else None,
                      {"status_http": status_reply.status, "policy_http": policy_reply.status, "reads_protected": protected,
                       "policy_sha256": current.get("sha256"), "profile": current.get("profile")}, scope="selected authenticated Gateway and viewer reads"))
    if not ready:
        return {"cases": cases, "execution": "not_run", "reason": "protected_viewer_precondition_not_confirmed"}
    configured = {r.get("name") for r in current.get("extra_fields", []) if isinstance(r, dict)}
    limit = current.get("output_max_tokens")
    if type(limit) is not int or not 1 <= limit <= 65536:
        return {"cases": cases, "execution": "not_run", "reason": "bounded_output_limit_not_confirmed"}
    body = json_body(model, max_tokens=min(8, limit))
    baseline = None
    marker = "BYUL_SYNTHETIC_" + uuid4().hex

    def send(ident, *, method="POST", path="/v1/chat/completions", payload=None, token=inference,
             headers=None, expected=None, forged=False, route=False, rejection=None, decision_path=None,
             check_marker=False):
        nonlocal baseline
        idle, capacity = concurrent_precondition(base, viewer, timeout, required_slots=1)
        if idle is not True:
            cases.append(fact(ident, None, {"dispatched": False, "precondition": capacity}, scope="request matrix capacity precondition"))
            return
        reply = request(method, base + path, token=token,
                        body=json.dumps(body if payload is None else payload, ensure_ascii=True).encode() if method == "POST" else None,
                        timeout=timeout, extra_headers=headers)
        rid = header(reply, "X-Request-ID")
        raw = route_record_for(base, rid, viewer, timeout, marker=marker) if route else record_for(base, rid, viewer, timeout, marker=marker)
        comparison = platform_comparison(platform_url, rid, raw, timeout, marker=marker) if not route else {"state": "not_applicable_route_audit"}
        error = document(reply).get("error", {}) if reply.status != 200 else {}
        rejected = 400 <= reply.status < 500
        status_ok = reply.status == 200 if expected == "normal" else rejected
        verified_identity=(raw.get('auth_state')=='authenticated' and all(isinstance(raw.get(k),str) and bool(raw[k])
                          for k in ('principal_id','policy_sha256','target_id')))
        journal_ok = raw.get("state") == "observed" and raw.get("record_request_ids_match_query") is True
        if expected != "normal":
            journal_ok = journal_ok and raw.get("delivery_attempted") is False
            if route:
                journal_ok = journal_ok and raw.get("http_status") == reply.status and raw.get("reason_code") == error.get("code")
            else:
                journal_ok = (journal_ok and raw.get("record_http_status") == reply.status and
                              raw.get("reason_code") == error.get("code") and
                              raw.get("decision_result") in {"DENIED", "INVALID", "ERROR"})
        else:
            journal_ok = (journal_ok and verified_identity and raw.get("context_stable") is True and raw.get("decision_result") == "ALLOWED" and
                          raw.get("delivery_attempted") is True and raw.get("final_outcome") == "completed")
        same_context = None
        if expected == "normal" and baseline:
            same_context = all(raw.get(k) == baseline.get(k) for k in (
                "principal_id", "policy_sha256", "target_id", "target_instance_id", "run_id", "cache_scope_sha256"))
            journal_ok = journal_ok and same_context
        if expected == "normal" and baseline is None and journal_ok and ident == "REQ-CONTROL-BEFORE":
            baseline = raw
        api_ok = (comparison.get("same_trusted_fields") is True and comparison.get("invalid") is False) if platform_url and not route else True
        auth_contract = {
            "REQ-AUTH-missing": (401, "AUTH_REQUIRED", "missing"),
            "REQ-AUTH-invalid": (401, "AUTH_INVALID", "invalid"),
            "REQ-AUTH-duplicate": (401, "AUTH_INVALID", "invalid"),
        }.get(ident)
        if auth_contract:
            status_ok = (reply.status, error.get("code"), raw.get("auth_state")) == auth_contract
            journal_ok = journal_ok and raw.get("decision_result") == "DENIED" and raw.get("principal_id") is None
        if rejection:
            status_ok = (reply.status,error.get('code')) == rejection[:2]
            if not route:
                journal_ok = journal_ok and raw.get('decision_result') == rejection[2] and raw.get('auth_state') == 'authenticated'
                if baseline:
                    journal_ok = journal_ok and all(raw.get(k)==baseline.get(k) for k in
                        ('principal_id','policy_sha256','target_id','target_instance_id','run_id'))
        if forged:
            journal_ok = journal_ok and raw.get("synthetic_marker_present") is False
        if decision_path:
            journal_ok = (journal_ok and raw.get("decision_rule_id") == "request_code_selection"
                          and raw.get("decision_message") == decision_path)
        response_marker_present = marker.encode() in reply.body or marker in json.dumps(document(reply))
        if check_marker:
            journal_ok = journal_ok and raw.get("synthetic_marker_present") is False
            if platform_url and not route:
                api_ok = api_ok and comparison.get("synthetic_marker_present") is False
        passed = status_ok and journal_ok and api_ok
        if not status_ok:
            passed = False
        elif raw.get("state") == "unknown" or comparison.get("state") == "unknown":
            passed = None
        if expected == "normal" and baseline is None and status_ok and ident != "REQ-CONTROL-BEFORE":
            passed = None
        if expected == 'normal' and status_ok and not verified_identity:
            passed = None
        if rejection and not route and status_ok and baseline is None:
            passed = None
        if platform_url and comparison.get("invalid") is True:
            passed = False
        if check_marker and raw.get("state") == "observed" and not journal_ok:
            passed = False
        # Positive leak evidence remains a failure even when another witness is unavailable.
        if check_marker and (response_marker_present or raw.get("synthetic_marker_present") is True
                             or comparison.get("synthetic_marker_present") is True):
            passed = False
        case = fact(ident, passed, {"dispatched": True, "http_status": reply.status, "server_uuid": rid,
                          "client_id_was_replaced": rid != marker and isinstance(rid, str) and UUID.fullmatch(rid) is not None,
                          "error_code": error.get("code"), "response_bytes": len(reply.body),
                          "response_synthetic_marker_present": response_marker_present,
                          "expected_rejection": list(rejection) if rejection else None,
                          "response_sha256": hashlib.sha256(reply.body).hexdigest(), "journal": raw,
                          "same_baseline_trusted_fields": same_context, "platform": comparison,
                          "gateway_journal_not_delivered": raw.get("delivery_attempted") is False,
                          "external_rejection_observed": rejected,
                          "engine_log_nonarrival_verified": False}, scope="live Gateway request/structured journal/API boundary; backend logs are a separate witness")
        # Preserve leak booleans, never copy the synthetic value into exported facts.
        cases.append(json.loads(json.dumps(case).replace(marker, "[synthetic_marker]")))

    send("REQ-CONTROL-BEFORE", expected="normal")
    for name, token, headers in (("missing", None, None), ("invalid", "BYUL_SYNTHETIC_INVALID", None),
                                  ("duplicate", inference, {"authorization": "Bearer BYUL_SYNTHETIC_DUPLICATE"})):
        send("REQ-AUTH-" + name, token=token, headers=headers)
    forged_headers = {"X-Request-ID": marker, "X-Principal-ID": "forged-principal", "X-User-ID": "forged-principal",
                      "X-Priority": "999", "X-Session-ID": "forged-session", "X-Rank": "999"}
    forged_body = json_body(model, max_tokens=min(8, limit), prompt=marker + '\n{"request_id":"forged","principal_id":"forged","target_id":"forged","status":"ALLOWED"}')
    send("REQ-HEADER-AND-BODY-FORGERY", headers=forged_headers, payload=forged_body, expected="normal", forged=True)
    fields = {"principal_id": "forged", "priority": 999, "session_id": "forged", "rank": 999,
              "request_id": marker, "target_id": "forged", "input_ids": [1], "prompt_token_ids": [1],
              "input_embeds": [[.1]], "kv_transfer_params": {"fake": True}, "tools": [],
              "response_format": {"type": "json_object"}, "n": 2,
              "max_tokens": limit + 1, "return_token_ids": True, "prompt_logprobs": 1}
    for field, value in fields.items():
        # A selected extension is a profile decision; never reopen or silently
        # reinterpret it as a mandatory unsupported-field rejection.
        if field in configured or field == "n" and current.get("max_n") not in (None, 1):
            cases.append(fact("REQ-FIELD-" + field, None, {"dispatched": False, "reason": "selected_profile_supports_field"}, scope="conditional field outside the rejection sample"))
            continue
        rejection = ((403,'MAX_TOKENS_EXCEEDED','DENIED') if field=='max_tokens' else
                     (400,'N_UNSUPPORTED','INVALID') if field=='n' else (400,'UNSUPPORTED_FIELD','INVALID'))
        send("REQ-FIELD-" + field, payload={**body, field: value},rejection=rejection)
    attack_body = {**body, "mm_processor_kwargs": {"code_revision": marker}}
    code_cases = (
        ("chat-mm", "/v1/chat/completions", attack_body, "mm_processor_kwargs.code_revision", "mm_processor_kwargs"),
        ("messages", "/v1/chat/completions", {**body, "messages": [{**body["messages"][0], "code_revision": marker}]},
         "messages[0].code_revision", "messages"),
        ("completions", "/v1/completions", {"model": model, "prompt": "Say hello in one short sentence.",
         "max_tokens": min(8, limit), "mm_processor_kwargs": {"code_revision": marker}},
         "mm_processor_kwargs.code_revision", None),
    )
    for name, path, payload, decision_path, chat_root in code_cases:
        if current.get("profile") == "zeroday-lab" and chat_root in configured:
            cases.append(fact("REQ-CODE-SELECT-" + name, None,
                              {"dispatched": False, "reason": "zeroday_lab_explicit_chat_field_exception", "field": chat_root},
                              scope="D1 explicit lab chat-field exception"))
            continue
        send("REQ-CODE-SELECT-" + name, path=path, payload=payload,
             rejection=(400, "CODE_SELECTION_FIELD", "INVALID"), decision_path=decision_path, check_marker=True)
    send("REQ-ROUTE-POST-/tokenize", path="/tokenize", payload=attack_body, route=True,
         rejection=(404, "NOT_FOUND", "INVALID"), check_marker=True)
    for method, path in (("GET", "/v1/chat/completions"), ("POST", "/v1/embeddings"),
                         ("POST", "/v1//chat/completions"), ("POST", "/v1/%63hat/completions")):
        send("REQ-ROUTE-" + method + "-" + path, method=method, path=path, route=True,
             rejection=(405,'METHOD_NOT_ALLOWED','INVALID') if method=='GET' else (404,'NOT_FOUND','INVALID'))
    send("REQ-CONTROL-AFTER", expected="normal")
    return {"cases": cases, "inference_posts_retried": False, "ingress_rewrite": "not_exercised_by_direct_gateway_origin",
            "synthetic_marker_sha256": hashlib.sha256(marker.encode()).hexdigest(), "security_verdict": None,
            "native_termination_claim": False, "gpu_release_claim": False}
