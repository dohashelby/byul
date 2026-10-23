"""Pure policy-list shape checks shared by execution and stored-evidence judging.

No engine, network, clock or filesystem dependencies belong in this module.
"""

import re


def string_list(values: dict, key: str, *, empty: bool = False) -> list[str]:
    value = values.get(key)
    if not isinstance(value, list) or (not empty and not value) or any(type(v) is not str or not v.strip() for v in value):
        raise ValueError(f"{key} must be an explicit {'possibly empty ' if empty else 'nonempty '}list[str]")
    if len(set(value)) != len(value):
        raise ValueError(f"{key} must not contain duplicates")
    return value


def validate_template_policy(values: dict) -> None:
    """Validate declared template limits without engine or probe-budget assumptions."""
    hashes = string_list(values, "allowed_template_sha256")
    if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in hashes):
        raise ValueError("allowed_template_sha256 must contain lowercase SHA-256 digests")
    limit = values.get("max_rendered_input_tokens")
    if type(limit) is not int or limit < 1:
        raise ValueError("max_rendered_input_tokens must be a positive integer policy limit")
    if type(values.get("request_template_override_allowed")) is not bool:
        raise ValueError("request_template_override_allowed must be an explicit boolean")
