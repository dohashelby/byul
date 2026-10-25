"""REQ-09 submitted input scope: pure checks shared by execution and rejudging.

These checks establish the bounded submitted profile, never parser consumption or
corporate approval. Absence of all markers preserves historical definitions.
"""
from dataclasses import dataclass
import re

from guard.models import CheckDef


POLICIES = ("allowed_endpoints", "allowed_input_formats")
OBSERVATIONS = ("input_endpoints_tested", "input_formats_tested")


def contract_state(check: CheckDef) -> str:
    if check.id != "REQ-09":
        return "legacy"
    markers = set(POLICIES + OBSERVATIONS)
    if not any(p.key in markers for p in [*check.policy_params, *check.required_observations]):
        return "legacy"
    params = [p for p in check.policy_params if p.key in markers]
    obs = [o for o in check.required_observations if o.key in markers]
    complete = (len(params) == 2 and {p.key for p in params} == set(POLICIES)
                and all(p.type == "list[str]" and p.required for p in params)
                and len(obs) == 2 and {o.key for o in obs} == set(OBSERVATIONS)
                and all(o.type == "list[str]" for o in obs))
    return "complete" if complete else "partial"


@dataclass(frozen=True)
class InputContext:
    blocked: bool
    reasons: tuple[str, ...]
    missing: tuple[str, ...] = ()


def policy_context(check: CheckDef, values: dict, observations: dict | None = None) -> InputContext | None:
    """None is historical; invalid new contracts cap PASS without raising in judges."""
    state = contract_state(check)
    if state == "legacy":
        return None
    errors, missing = [], []
    endpoint = None
    if state != "complete":
        errors.append("INPUT_CONTRACT_INCOMPLETE")
    if len(check.functional_probes) == 1:
        fp = check.functional_probes[0]
        body = fp.body or {}
        prompt = body.get("prompt")
        literal = (re.fullmatch(r"/[A-Za-z0-9_.~!$&'()+,;=@/-]*", fp.path) is not None
                   and "//" not in fp.path and not any(x in (".", "..") for x in fp.path.split("/")))
        if (fp.method == "POST" and fp.via == "engine" and literal
                and set(body) == {"model", "prompt", "max_tokens"} and body["model"] == "{model}"
                and type(body["max_tokens"]) is int and body["max_tokens"] == 1
                and type(prompt) is list and len(prompt) == 1 and type(prompt[0]) is int and prompt[0] == 0
                and len(fp.expect) == 1 and type(fp.expect[0]) is int and fp.expect[0] == 200):
            endpoint = fp.id
    if endpoint is None:
        errors.append("INPUT_NORMAL_PROFILE_UNSUPPORTED")
    expected = ([endpoint], ["token_ids"])
    for key, target in zip(POLICIES, expected):
        value = values.get(key)
        if (type(value) is not list or len(value) != 1 or any(type(v) is not str for v in value)
                or endpoint is None or value != target):
            errors.append(f"INPUT_POLICY_UNSUPPORTED:{key}")
    if observations is not None:
        for key, target in zip(OBSERVATIONS, expected):
            obs = observations.get(key)
            if obs is None or not obs.collected:
                missing.append(key)
                errors.append(f"INPUT_SCOPE_UNCOLLECTED:{key}")
            elif (type(obs.value) is not list or any(type(v) is not str for v in obs.value)
                  or endpoint is None or obs.value != target):
                errors.append(f"INPUT_SCOPE_MISMATCH:{key}")
    reasons = ["POLICY_CONTEXT: INPUT_SCOPE_ENGINE_DIRECT; submitted token-array profile only; "
               "other endpoints/formats, parser consumption and corporate approval are not automatically verified"]
    if not errors:
        reasons.append(f"POLICY_CONTEXT: INPUT_SCOPE_MATCHED; allowed endpoint and tested profile: {endpoint}; format: token_ids"
                       if observations is not None else "POLICY_CONTEXT: INPUT_PROFILE_SUPPORTED")
    reasons.extend(f"POLICY_CONTEXT: {error}; PASS withheld" for error in errors)
    return InputContext(bool(errors), tuple(reasons), tuple(missing))
