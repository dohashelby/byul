"""REQ-08·SUP-08: 요청 필드 한 개의 거부·제거·소비를 정상 대조와 비교한다.

증거는 응답 상태와 응답이 돌려준 소비 토큰뿐이다. 필드 값은 무해한 고정 fixture이고 본문은
해시로만 남긴다. 2xx를 곧 위반으로 보지 않으며 실제 소비 증거가 있을 때만 위반으로 적는다.
"""
from __future__ import annotations

from copy import deepcopy
import re

from guard.models import canonical_json, sha256_hex
from .content_common import bounded_probe, control_state, json_body, record_content
from .http_common import functional_requests

REUSED_TOKEN_PROFILE = "reused-token-input-v1"
CODE_SELECTION_PROFILE = "request-code-selection-v1"
PROFILES = frozenset({REUSED_TOKEN_PROFILE, CODE_SELECTION_PROFILE})
VERSION = "0.9.0"
UNAPPROVED_REVISION = "byul-unapproved-revision"
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}(\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,3}")


def supported(check):
    return getattr(check, "collection_profile", None) in PROFILES


def _rejections(values):
    codes = values.get("rejection_codes")
    if type(codes) is not list or not codes or any(code not in {"400", "403", "413", "422"} for code in codes):
        raise ValueError("rejection_codes must explicitly name supported rejection responses")
    return codes


def _field(value):
    if type(value) is not str or _FIELD.fullmatch(value) is None:
        raise ValueError("request field names must be bounded dotted identifiers")
    return value


def validate_configuration(check, cfg):
    if getattr(check, "collection_profile", None) != REUSED_TOKEN_PROFILE:
        return
    ids, size = cfg.get("reused_token_ids"), cfg.get("vocab_size")
    if (type(size) is not int or size < 1 or not cfg.get("vocab_source") or type(ids) is not list
            or not 1 <= len(ids) <= 64 or any(type(v) is not int or not 0 <= v < size for v in ids)):
        raise ValueError("execution settings need reused_token_ids inside vocab_size and its source")


def validate_policy(check, values):
    if type(values) is not dict:
        raise ValueError("request field policy must be a mapping")
    _rejections(values)
    if check.collection_profile == REUSED_TOKEN_PROFILE:
        _field(values.get("reused_token_field"))
        if type(values.get("reused_token_allowed")) is not bool:
            raise ValueError("reused_token_allowed must be an explicit boolean")
        return
    fields = values.get("code_selection_fields")
    if (type(fields) is not list or not 1 <= len(fields) <= 8 or len(set(map(str, fields))) != len(fields)
            or any(_field(field) is None for field in fields)):
        raise ValueError("code_selection_fields requires 1..8 distinct field names")
    if type(values.get("code_selection_allowed")) is not bool:
        raise ValueError("code_selection_allowed must be an explicit boolean")


def _assign(body, path, value):
    node = body
    *parents, leaf = path.split(".")
    for part in parents:
        node = node.setdefault(part, {})
    node[leaf] = value


def consumed_tokens(result):
    body, error = json_body(result)
    if error:
        return None
    for holder in (body, *(body.get("choices") or [])):
        tokens = holder.get("prompt_token_ids") if isinstance(holder, dict) else None
        if type(tokens) is list and tokens and all(type(v) is int and v >= 0 for v in tokens):
            return tokens
    return None


def classify_reused(result, *, fixture, control_tokens, rejection_codes):
    outcome = result.get("outcome")
    if type(outcome) is int and str(outcome) in rejection_codes:
        return "rejected"
    if type(outcome) is not int or not 200 <= outcome < 300:
        return "unknown"
    tokens = consumed_tokens(result)
    if tokens is None or control_tokens is None:
        return "accepted_unverified"
    if tokens == fixture:
        return "consumed"
    return "ignored" if tokens == control_tokens else "accepted_unverified"


def classify_selection(result, *, rejection_codes):
    outcome = result.get("outcome")
    if type(outcome) is int and str(outcome) in rejection_codes:
        return "rejected"
    return "accepted_unverified" if type(outcome) is int and 200 <= outcome < 300 else "unknown"


def _hash(value):
    return sha256_hex(canonical_json(value))


def collect(ctx, cfg):
    values = ctx.policy_values
    requests, probes = functional_requests(ctx, via="engine")
    if len(requests) != 1:
        raise ValueError("request field checks need one engine control")
    template, probe = requests[0], next(iter(probes.values()))
    endpoint = probe.path
    expected_model = ctx.api_model or ctx.env.model.id
    reused = ctx.check.collection_profile == REUSED_TOKEN_PROFILE
    if reused:
        template = deepcopy(template)
        template["body"]["return_token_ids"] = True

    def send(request, label):
        result = bounded_probe(ctx, ctx.trusted_net, request)
        record_content(ctx, "trusted", "request-field", label, {key: result.get(key) for key in ("outcome", "body_bytes", "body_sha256")})
        return result

    controls, trials, rejections = {}, [], values["rejection_codes"]
    first = send(deepcopy(template), "before")
    controls["before"] = control_state(first, expected_model, endpoint)
    if controls["before"] == "pass":
        control_tokens = consumed_tokens(first) if reused else None
        cases = ([(values["reused_token_field"], cfg["reused_token_ids"])] if reused
                 else [(field, UNAPPROVED_REVISION) for field in values["code_selection_fields"]])
        for index, (field, fixture) in enumerate(cases):
            request = deepcopy(template)
            _assign(request["body"], field, fixture)
            result = send(request, f"challenge-{index}")
            state = (classify_reused(result, fixture=fixture, control_tokens=control_tokens, rejection_codes=rejections)
                     if reused else classify_selection(result, rejection_codes=rejections))
            trials.append({"field": field, "outcome": str(result.get("outcome")), "state": state,
                           "request_fixture_sha256": _hash(request["body"])})
            label = f"after-{index}"
            controls[label] = control_state(send(deepcopy(template), label), expected_model, endpoint)
    normal = {"attempted": True, "succeeded": len(controls) > 1 and all(v == "pass" for v in controls.values()),
              "detail": "challenge 전후 같은 정상 요청을 대조"}
    planned = list(controls) if len(controls) > 1 else ["before"]
    obs = {"functional_probes_planned": {"value": planned, "collected": True},
           "functional_probe_results": {"value": controls, "collected": True}}
    summary = {"profile": ctx.check.collection_profile, "controls": controls, "trials": trials,
               "fixture": {"profile": ctx.check.collection_profile, "control_sha256": _hash(probe.model_dump(mode="json")),
                           "fields_sha256": _hash([row["field"] for row in trials])}}
    done = bool(trials)
    if reused:
        allowed = values["reused_token_allowed"]
        state = trials[0]["state"] if done else None
        obs.update({"reused_input_trials": {"value": trials, "collected": done},
                    "reused_input_state": {"value": state, "collected": done},
                    "reused_input_forbidden": {"value": not allowed, "collected": True},
                    "reused_input_violations": {"value": [row["field"] for row in trials if row["state"] == "consumed" and not allowed],
                                                "collected": done}})
    else:
        fields = values["code_selection_fields"]
        obs.update({"code_selection_trials": {"value": trials, "collected": done},
                    "code_selection_forbidden": {"value": not values["code_selection_allowed"], "collected": True},
                    "code_selection_fields_planned": {"value": fields, "collected": True},
                    "code_selection_rejected_fields": {"value": [r["field"] for r in trials if r["state"] == "rejected"], "collected": done},
                    "code_selection_unverified_fields": {"value": [r["field"] for r in trials if r["state"] != "rejected"], "collected": done},
                    "code_selection_confirmed_violations": {"value": [], "collected": done},
                    "code_selection_final_code_verified": {"value": False, "collected": True}})
    return obs, normal, [{"step": step, "attempted": done} for step in ctx.check.challenge_steps], summary
