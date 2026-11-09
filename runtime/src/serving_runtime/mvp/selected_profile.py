"""Deployment validation for the selected 37-check default text profile.

This adapter narrows one declared deployment profile. The general policy loader
and zeroday-lab experimentation keep their existing behavior.
"""
from __future__ import annotations

from .policy import PolicyState

PROFILE = "selected-default-text/1.0"
# These four bounded sampling rules already have a retained default-profile
# example. Opening another engine option needs a separate selected-profile review.
SAMPLING_FIELDS = frozenset({"temperature", "top_p", "seed", "stop"})
UNSUPPORTED_ENTRY_GROUPS = {
    "token_reuse": ["prompt_token_ids", "input_ids", "kv_transfer_params", "prompt_embeds"],
    "code_selection": ["code_revision", "revision", "tokenizer_revision", "trust_remote_code"],
    "input_processors": ["processor", "processor_kwargs", "mm_processor_kwargs", "media_io_kwargs", "chat_template", "chat_template_kwargs"],
    "structured_tools": ["response_format", "structured_outputs", "guided_json", "guided_regex", "tools", "tool_choice", "parallel_tool_calls"],
    "native_execution": ["native", "native_options", "sampling_params", "extra_body"],
    "response_options": ["echo", "return_prompt_text", "return_token_ids", "return_tokens_as_token_ids", "logprobs", "top_logprobs", "prompt_logprobs", "private_debug"],
}


def validate_selected_profile(state: PolicyState) -> dict:
    """Return an A0/preflight-consumable result over the exact loaded policy."""
    report = {"schema_version": PROFILE, "status": "unsupported", "supported": False,
              "policy_sha256": state.sha256, "policy_id": None, "policy_version": None,
              "runtime_profile": None, "opened_sampling_fields": [], "violations": [],
              "unsupported_entry_groups": {name: list(fields) for name, fields in UNSUPPORTED_ENTRY_GROUPS.items()},
              "selected_profile": PROFILE,
              "response_privacy": {"mode": "remove_unrequested_known_metadata",
                                   "content_usage_stream": "preserved", "vendor_extensions": "preserved_except_known_private_metadata",
                                   "content_keyword_filtering": False, "allowed_return_options": []}}
    if not state.ok or state.policy is None:
        report["violations"].append({"code": "POLICY_UNAVAILABLE", "fields": [], "reason": state.error_code})
        return report
    policy = state.policy
    report.update(policy_id=policy.policy_id, policy_version=policy.policy_version, runtime_profile=policy.profile)
    if policy.profile != "default":
        report["violations"].append({"code": "SELECTED_PROFILE_MISMATCH", "fields": ["profile"], "reason": "selected deployment requires runtime profile default"})
    unsupported = sorted(rule.name for rule in policy.extra_fields if rule.name not in SAMPLING_FIELDS)
    if unsupported:
        report["violations"].append({"code": "UNSUPPORTED_SELECTED_ENTRY", "fields": unsupported,
                                     "reason": "extra_fields reopens options outside the selected text profile"})
    for rule in policy.extra_fields:
        if rule.name not in SAMPLING_FIELDS:
            continue
        kind = rule.constraints.get("type")
        expected = {"temperature": {"integer", "number"}, "top_p": {"integer", "number"},
                    "seed": {"integer"}, "stop": {"string", "array"}}[rule.name]
        bounded = (rule.mode == "validate" and kind in expected)
        if kind in {"integer", "number"}:
            bounded = bounded and {"min", "max"} <= rule.constraints.keys()
        elif kind == "string":
            bounded = bounded and "max_length" in rule.constraints
        elif kind == "array":
            item = rule.constraints.get("items", {})
            bounded = bounded and "max_items" in rule.constraints and item.get("type") == "string" and "max_length" in item
        if not bounded:
            report["violations"].append({"code": "UNSUPPORTED_SELECTED_SAMPLING_RULE", "fields": [rule.name],
                                         "reason": "selected sampling requires its bounded validated type"})
        else:
            report["opened_sampling_fields"].append(rule.name)
    report["supported"] = not report["violations"]
    report["status"] = "ok" if report["supported"] else "unsupported"
    return report
