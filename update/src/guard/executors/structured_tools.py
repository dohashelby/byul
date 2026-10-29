"""B-owned REQ-10 branch; deliberately not registered as sampling-api-v1.

HTTP observations never prove request-class validation or runner consumption.
The offline entry accepts the actual pinned request class, not an emulation.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math

from .http_common import HttpCheckExecutor

SOURCE_COMMIT = "5d35ce3934a7804b298559d2d99eec33792e1eb9"
PROFILE = "structured-tools-v1"
ENDPOINT = "/v1/chat/completions"
SCHEMA = {"type": "object", "properties": {"location": {"type": "string"}}}
TOOL = {"type": "function", "function": {"name": "get_weather", "parameters": SCHEMA}}
CASE_IDS = ("none", "auto", "required", "named-only", "named-json")


def cases(model):
    """Fresh bounded inputs; numbers here are fixtures, not enterprise limits."""
    base = {"model": model, "messages": [{"role": "user", "content": "Return a location."}],
            "max_tokens": 2, "tools": [TOOL]}
    named = {"type": "function", "function": {"name": "get_weather"}}
    result = {}
    for key in CASE_IDS:
        body = deepcopy(base)
        body["tool_choice"] = deepcopy(named) if key.startswith("named") else key
        if key != "named-only":
            body["structured_outputs"] = {"json": deepcopy(SCHEMA)}
        result[key] = body
    return result


def judge_params():
    """Existing rule_table contract; a scoped violation survives missing controls.

    No security PASS rule: downstream conversion/backend/runner are unobserved.
    """
    return {"fail_when_any": [{"obs": "structured_tools_validator_violation", "op": "eq", "value": True}],
            "axes": {"functional": {"pass_when_all": [
                {"obs": "structured_tools_controls", "op": "superset_of", "value_obs": "structured_tools_controls_planned"},
                {"obs": "structured_tools_controls", "op": "all_in", "value": ["pass"]}]}}}


def validate_request_class(request_class, rejection_type, *, model):
    """Execute model_validate at the request boundary; never stringify exceptions.

    Caller must pin installed source and exception identity. Returning a model
    proves boundary acceptance only. Exceptions without the expected type do
    not establish expected rejection. This function does not import vLLM.
    """
    outcomes = {}
    for key, body in cases(model).items():
        try:
            request = request_class.model_validate(body)
            choice = request.tool_choice
            expected = "named" if key.startswith("named") else key
            normalized = "named" if not isinstance(choice, str) and choice is not None else choice
            outcomes[key] = "accepted" if normalized == expected else "normalization_mismatch"
        except rejection_type:
            outcomes[key] = "rejected"
        except Exception:
            outcomes[key] = "execution_error"
    return outcomes


def project_validator(outcomes):
    """Only an explicit accepted forbidden case proves this scoped violation."""
    value = outcomes.get("named-json")
    return {"structured_tools_validator_violation": {
        "value": value == "accepted" if value in {"accepted", "rejected"} else None,
        "collected": value in {"accepted", "rejected"}}}


class StructuredToolsExecutor(HttpCheckExecutor):
    """Reuse existing transport/EvidenceDraft; registration belongs to A.

    No injected HTTP result is promoted into validator evidence. Request-class
    observations are obtained separately through validate_request_class.
    """
    name = "structured_tools"
    version = "0.1.0"
    requires_roles = False
    requires_route_scope = False

    def validate_policy(self, check, values):
        if self.cfg.get("source_commit") != SOURCE_COMMIT:
            raise ValueError("structured tools requires the selected source commit")
        if self.cfg.get("backend") is not None and not isinstance(self.cfg["backend"], str):
            raise ValueError("backend must be a declared string or null")
        timeout = self.cfg.get("request_timeout", 3.0)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("request_timeout must be positive and at most 30")
        if check.id != "REQ-10":
            raise ValueError("structured tools is a REQ-10 branch")
        if values:
            raise ValueError("no enterprise policy is inferred by structured-tools-v1")

    def collect(self, ctx):
        outcomes, controls, attempts = {}, {}, []
        planned = ["before", "none", "auto", "required", "named-only", "after"]
        payloads = cases(ctx.api_model)

        def send(key, body):
            request = {"id": f"structured-tools-{key}", "method": "POST",
                       "url": ctx.engine_url + ENDPOINT, "body": deepcopy(body), "want_body": True}
            attempts.append(key)
            try:
                replies = ctx.cprobe(ctx.trusted_net, [request], ctx.timeout)
                if len(replies) != 1 or replies[0].get("id") != request["id"]:
                    return "missing", False
                reply = replies[0]
                code = reply.get("outcome")
                safe = str(code) if type(code) is int and 100 <= code <= 599 else "uncollected"
                valid = False
                if code == 200 and type(code) is int:
                    try:
                        data = json.loads(reply.get("body") or "")
                        choices, usage = data["choices"], data["usage"]
                        valid = (data.get("model") == ctx.api_model and type(choices) is list
                                 and len(choices) == 1 and type(choices[0]) is dict
                                 and type(choices[0].get("message")) is dict
                                 and choices[0]["message"].get("role") == "assistant"
                                 and (type(choices[0]["message"].get("content")) is str
                                      or bool(choices[0]["message"].get("tool_calls")))
                                 and choices[0].get("finish_reason") in {"stop", "length", "tool_calls"}
                                 and type(usage.get("completion_tokens")) is int
                                 and 0 < usage["completion_tokens"] <= 2)
                    except (ValueError, TypeError, KeyError, AttributeError):
                        pass
                return safe, valid
            except Exception:
                return "execution_error", False

        if self.cfg.get("supported") is True:
            for key in ["before", *CASE_IDS, "after"]:
                status, valid = send(key, payloads["auto"] if key in {"before", "after"} else payloads[key])
                outcomes[key] = status
                if key != "named-json":
                    controls[key] = "pass" if valid else "unknown"
                if key == "before" and not valid:
                    break
        obs = {
            "structured_tools_http_outcomes": {"value": outcomes or None, "collected": bool(outcomes)},
            "structured_tools_controls_planned": {"value": planned, "collected": True},
            "structured_tools_controls": {"value": controls or None, "collected": bool(controls)},
            **project_validator({}),
            "runner_consumption_verified": {"value": False, "collected": True},
        }
        return obs, {"attempted": bool(attempts),
                     "succeeded": set(controls) == set(planned) and all(v == "pass" for v in controls.values()),
                     "detail": "bounded chat generation controls; schema conformance and runner consumption unverified"}, [
            {"step": step, "attempted": "named-json" in attempts} for step in ctx.check.challenge_steps], {
                "profile": PROFILE, "source_commit": SOURCE_COMMIT, "endpoint": ENDPOINT,
                "backend": self.cfg.get("backend"), "requests_sent": len(attempts), "http_outcomes": outcomes,
                "scope": "http" if attempts else "unsupported_or_unconfirmed",
                "fixture": {"profile": PROFILE, "case_ids": list(CASE_IDS), "source_commit": SOURCE_COMMIT},
                "limitations": ["HTTP status does not identify the rejecting validator or prove downstream non-reachability.",
                                "No grammar compilation, model submission, runner consumption or service-wide survival measured.",
                                "Source reference is not installed-target identity; backend support requires separate confirmation."]}
