"""D3 protocol fixtures: fake Gateway, viewer and platform HTTP only."""
import json
from urllib.parse import urlsplit

import pytest

from tools import selected_request_checks as checks
from tools.selected_runtime_check import HTTPResponse


CODE_IDS = ("REQ-CODE-SELECT-chat-mm", "REQ-CODE-SELECT-messages", "REQ-CODE-SELECT-completions")
ROUTE_ID = "REQ-ROUTE-POST-/tokenize"


def fake_matrix(monkeypatch, *, target=None, fault=None, profile=None, configured=(), platform=True):
    state = {"rows": {}, "posts": [], "marker": None}
    faults = set(fault.split("+")) if fault else set()

    def reply(status, value, rid=None):
        encoded = json.dumps(value)
        if "escaped" in faults and state["marker"]:
            encoded = encoded.replace(state["marker"], "".join(f"\\u{ord(c):04x}" for c in state["marker"]))
        return HTTPResponse(status, {"X-Request-ID": rid} if rid else {}, encoded.encode())

    def request(method, url, **kwargs):
        path = urlsplit(url).path
        if path == "/api/status":
            return reply(200, {"auth": {"protected_routes": ["GET /api/*"]}})
        if path == "/api/policy":
            return reply(200, {"current": {"output_max_tokens": 16, "profile": profile,
                                          "extra_fields": [{"name": name} for name in configured]}})
        if method == "GET" and path.startswith("/api/"):
            rid = path.rsplit("/", 1)[-1]
            if path == "/api/route-rejections":
                rid = state["last_id"]
            entry = state["rows"][rid]
            broken = entry["id"] == target
            if broken and ("lookup" in faults or "platform_lookup" in faults and path.startswith("/api/runtime/")):
                return reply(503, {})
            if path == "/api/route-rejections":
                row = {"request_id": rid, "forwarded_to_engine": broken and "delivery" in faults,
                       "response_code": 404, "reason": "NOT_FOUND"}
                value = {"items": [row]}
            else:
                final = entry["final"]
                if path.startswith("/api/runtime/requests/"):
                    value = {"detail": final}
                    if broken and "platform_leak" in faults:
                        value["unexpected"] = state["marker"]
                    if broken and "platform_mismatch" in faults:
                        value = {"detail": {**final, "auth": {"principal_id": "other"}}}
                else:
                    value = {"gateway_records": [{**final, "phase": phase} for phase in ("started", "final")]}
            if broken and "journal_leak" in faults and not path.startswith("/api/runtime/"):
                value["unexpected"] = state["marker"]
            return reply(200, value)

        payload = json.loads(kwargs["body"]) if kwargs.get("body") else {}
        ident = None
        decision_path = "mm_processor_kwargs.code_revision"
        if "mm_processor_kwargs" in payload:
            state["marker"] = payload["mm_processor_kwargs"]["code_revision"]
            ident = ROUTE_ID if path == "/tokenize" else CODE_IDS[2] if path == "/v1/completions" else CODE_IDS[0]
        elif payload.get("messages") and "code_revision" in payload["messages"][0]:
            state["marker"] = payload["messages"][0]["code_revision"]
            ident, decision_path = CODE_IDS[1], "messages[0].code_revision"
        state["posts"].append((method, path, payload, ident))
        rid = f'{len(state["posts"]):032x}'
        state["last_id"] = rid
        status, code = (404, "NOT_FOUND") if ident == ROUTE_ID else (400, "CODE_SELECTION_FIELD") if ident else (200, None)
        broken = ident is not None and ident == target
        if broken and "status" in faults:
            status, code = 200, None
        final = {"request_id": rid, "auth": {"state": "authenticated", "principal_id": "client"},
                 "policy": {"sha256": "policy"}, "target_id": "target", "target": {"instance_id": "instance"},
                 "run_id": "run", "decision": {"result": "INVALID" if ident else "ALLOWED", "http_status": status,
                 "reason_code": code, "rule_id": "wrong" if broken and "rule" in faults else "request_code_selection",
                 "message": state["marker"] if broken and "message_leak" in faults else "wrong.path" if broken and "path" in faults else decision_path},
                 "delivery": {"attempted": True if broken and "delivery" in faults else not bool(ident)},
                 "final_outcome": "rejected" if ident else "completed"}
        state["rows"][rid] = {"id": ident, "final": final}
        value = {"error": {"code": code}} if ident else {}
        if broken and "response_leak" in faults:
            value["unexpected"] = state["marker"]
        return reply(status, value, rid)

    monkeypatch.setattr(checks, "request", request)
    monkeypatch.setattr(checks, "concurrent_precondition", lambda *args, **kwargs: (True, {}))
    result = checks.request_matrix("http://gateway", "http://platform" if platform else None,
                                   "test-model", "inference", "viewer", timeout=.1)
    return {case["id"]: case for case in result["cases"]}, state


@pytest.mark.parametrize("platform", [True, False])
def test_new_cases_pass_with_bound_journals_and_no_marker(monkeypatch, platform):
    cases, state = fake_matrix(monkeypatch, platform=platform)
    for ident in (*CODE_IDS, ROUTE_ID):
        case = cases[ident]
        assert case["result"] == "passed"
        assert case["facts"]["journal"]["delivery_attempted"] is False
        assert case["facts"]["response_synthetic_marker_present"] is False
        assert case["facts"]["engine_log_nonarrival_verified"] is False
    assert cases[ROUTE_ID]["facts"]["journal"]["journal_kind"] == "route_rejection"
    completion = next(payload for _, path, payload, _ in state["posts"] if path == "/v1/completions")
    assert completion["model"] == "test-model" and completion["max_tokens"] == 8
    assert isinstance(completion["prompt"], str) and "messages" not in completion
    assert state["posts"][0][3] is None and state["posts"][-1][3] is None
    assert state["marker"] not in json.dumps(cases)


@pytest.mark.parametrize("ident", (*CODE_IDS, ROUTE_ID))
@pytest.mark.parametrize("fault", ["delivery", "response_leak", "journal_leak", "status", "lookup"])
def test_new_cases_fail_on_contradiction_and_are_unknown_on_lookup_failure(monkeypatch, ident, fault):
    cases, state = fake_matrix(monkeypatch, target=ident, fault=fault)
    assert cases[ident]["result"] == ("unknown" if fault == "lookup" else "failed")
    assert state["marker"] not in json.dumps(cases)


@pytest.mark.parametrize("ident", CODE_IDS)
@pytest.mark.parametrize("fault", ["rule", "path", "message_leak", "platform_leak", "platform_mismatch",
                                 "delivery+platform_lookup", "rule+platform_lookup", "response_leak+lookup",
                                 "response_leak+escaped", "platform_leak+escaped", "journal_leak+escaped"])
def test_code_cases_require_rule_path_and_platform_agreement(monkeypatch, ident, fault):
    cases, state = fake_matrix(monkeypatch, target=ident, fault=fault)
    assert cases[ident]["result"] == "failed"
    assert state["marker"] not in json.dumps(cases)


def test_platform_lookup_failure_is_unknown(monkeypatch):
    cases, _ = fake_matrix(monkeypatch, target=CODE_IDS[0], fault="platform_lookup")
    assert cases[CODE_IDS[0]]["result"] == "unknown"


@pytest.mark.parametrize("profile", [None, "zeroday-lab"])
def test_only_explicit_lab_chat_root_is_skipped(monkeypatch, profile):
    cases, state = fake_matrix(monkeypatch, profile=profile, configured=("mm_processor_kwargs", "code_revision"))
    case = cases[CODE_IDS[0]]
    assert case["facts"]["dispatched"] is (profile != "zeroday-lab")
    if profile == "zeroday-lab":
        assert case["facts"]["reason"] == "zeroday_lab_explicit_chat_field_exception"
        assert not any(ident == CODE_IDS[0] for *_, ident in state["posts"])
    for ident in (CODE_IDS[1], CODE_IDS[2], ROUTE_ID):
        assert cases[ident]["result"] == "passed" and cases[ident]["facts"]["dispatched"] is True
