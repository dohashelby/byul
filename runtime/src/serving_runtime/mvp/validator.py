"""Pure request validation and policy decisions for the MVP gateway."""

from dataclasses import dataclass
import json
import math
from typing import Any, Literal

from .policy import (
    DuplicateKeyError,
    ExtraFieldRule,
    PolicyState,
    _object_without_duplicates,
    _reject_json_constant,
)
from .selected_profile import UNSUPPORTED_ENTRY_GROUPS


_CODE_SELECTION_GROUPS = {
    name: group
    for group in ("code_selection", "input_processors")
    for name in UNSUPPORTED_ENTRY_GROUPS[group]
}
_FIELD_PATH_LIMIT = 256  # Match the route audit's bounded path policy.


def _field_path(parent: str, key: str | int) -> str:
    if isinstance(key, int):
        path = f"{parent}[{key}]"
    else:
        # Only keys enter the diagnostic. Bound work before sanitizing and keep
        # the final field visible even when its ancestors exceed the limit.
        part = "".join(char if char.isprintable() else "?" for char in key[:_FIELD_PATH_LIMIT])
        path = f"{parent}.{part}" if parent else part
    if len(path) > _FIELD_PATH_LIMIT:
        path = "..." + path[-(_FIELD_PATH_LIMIT - 3):]
    return path


def _code_selection_field(
    document: dict[str, Any], ignored_roots: set[str]
) -> tuple[str, str] | None:
    """Find a forbidden key without recursion or inspecting string values.

    Visit children before their container key so mm_processor_kwargs.code_revision
    identifies the specific selector. Iterator frames use memory bounded by JSON
    depth; every retained path is bounded independently of request depth/width.
    """
    pending = [(iter(document.items()), "", None)]
    while pending:
        entries, parent, parent_group = pending[-1]
        try:
            key, value = next(entries)
        except StopIteration:
            pending.pop()
            if parent_group is not None:
                return parent, parent_group
            continue
        if len(pending) == 1 and key in ignored_roots:
            continue
        path = _field_path(parent, key)
        group = _CODE_SELECTION_GROUPS.get(key)
        if isinstance(value, dict):
            pending.append((iter(value.items()), path, group))
        elif isinstance(value, list):
            pending.append((iter(enumerate(value)), path, group))
        elif group is not None:
            return path, group
    return None


@dataclass(frozen=True)
class Decision:
    result: Literal["ALLOWED", "DENIED", "INVALID", "ENGINE_REJECTED", "ERROR"]
    http_status: int
    reason_code: str
    rule_id: str | None
    message: str
    model: str | None
    max_tokens: int | None
    # Additional runtime-mvp/1.2 fields that passed policy validation, as
    # (name, mode) pairs. Values are never kept here.
    extra_fields: tuple[tuple[str, str], ...] = ()
    n: int | None = None
    source: str = "gateway"
    engine_http_status: int | None = None
    retry_after: str | None = None


def _type_matches(kind: str, value: object) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        if isinstance(value, bool):
            return False
        # Arbitrarily large JSON integers are finite; math.isfinite would overflow.
        return isinstance(value, int) or (
            isinstance(value, float) and math.isfinite(value)
        )
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    return False


def _scalar_violation(rule: dict[str, Any], value: object) -> str | None:
    """Return the violated constraint name for a scalar, or None."""

    if not _type_matches(rule["type"], value):
        return "type"
    if "min" in rule and value < rule["min"]:
        return "min"
    if "max" in rule and value > rule["max"]:
        return "max"
    if "max_length" in rule and len(value) > rule["max_length"]:
        return "max_length"
    return None


def _extra_field_violation(rule: ExtraFieldRule, value: object) -> str | None:
    constraints = rule.constraints
    violation = _scalar_violation(
        {key: item for key, item in constraints.items() if key in {"type", "min", "max", "max_length"}},
        value,
    )
    if violation is not None:
        return violation
    if "max_items" in constraints and len(value) > constraints["max_items"]:
        return "max_items"
    if "items" in constraints:
        for item in value:
            item_violation = _scalar_violation(constraints["items"], item)
            if item_violation is not None:
                return f"items.{item_violation}"
    if "max_bytes" in constraints:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > constraints["max_bytes"]:
            return "max_bytes"
    return None


def _decision(
    result: Literal["ALLOWED", "DENIED", "INVALID", "ERROR"],
    status: int,
    reason_code: str,
    message: str,
    *,
    rule_id: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> Decision:
    return Decision(result, status, reason_code, rule_id, message, model, max_tokens)


def _require_utf8_strings(value: object) -> None:
    """Reject lone surrogates decoded from JSON escapes, including object keys."""

    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            current.encode("utf-8")
        elif isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def validate_request(
    body: bytes,
    content_type: str | None,
    policy_state: PolicyState,
    *,
    request_kind: Literal["chat", "completions"] = "chat",
) -> Decision:
    """Validate one request in the specified order and return its disposition."""

    if not policy_state.ok or policy_state.policy is None:
        return _decision("ERROR", 503, "POLICY_UNAVAILABLE", "policy is unavailable")
    policy = policy_state.policy

    media_type = content_type.split(";", 1)[0].strip().lower() if content_type else ""
    if media_type != "application/json":
        return _decision(
            "INVALID", 415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json"
        )
    if len(body) > policy.max_body_bytes:
        return _decision("INVALID", 413, "BODY_TOO_LARGE", "request body exceeds the policy limit")

    try:
        text = body.decode("utf-8")
        document = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError:
        return _decision("INVALID", 400, "MALFORMED_JSON", "request body is not UTF-8")
    except DuplicateKeyError as error:
        # A duplicate key is reported before the decoded document is available.
        # Do not put an unencodable key into the HTTP error response or record.
        try:
            str(error).encode("utf-8")
        except UnicodeEncodeError:
            return _decision("INVALID", 400, "INVALID_UNICODE", "request contains invalid Unicode")
        return _decision("INVALID", 400, "DUPLICATE_KEY", str(error))
    except (json.JSONDecodeError, ValueError, RecursionError) as error:
        return _decision("INVALID", 400, "MALFORMED_JSON", f"invalid JSON: {error}")

    try:
        _require_utf8_strings(document)
    except UnicodeEncodeError:
        return _decision("INVALID", 400, "INVALID_UNICODE", "request contains invalid Unicode")

    if not isinstance(document, dict):
        return _decision("INVALID", 400, "INVALID_REQUEST", "request must be a JSON object")

    # The lab deliberately forwards explicitly opened chat fields (including
    # chat_template_kwargs) and their arbitrary values to /tokenize and inference.
    # Preserve that experiment contract; default and completions have no bypass.
    lab_fields = (
        {rule.name for rule in policy.extra_fields}
        if policy.profile == "zeroday-lab" and request_kind == "chat"
        else set()
    )
    forbidden = _code_selection_field(document, lab_fields)
    if forbidden is not None:
        path, group = forbidden
        return _decision(
            "INVALID",
            400,
            "CODE_SELECTION_FIELD" if group == "code_selection" else "INPUT_PROCESSOR_FIELD",
            path,
            rule_id="request_code_selection",
        )

    if "max_completion_tokens" in document:
        return _decision(
            "INVALID",
            400,
            "UNSUPPORTED_ALIAS",
            "max_completion_tokens is not supported; use max_tokens",
        )
    input_field = "messages" if request_kind == "chat" else "prompt"
    allowed_fields = {"model", input_field, "max_tokens", "stream", "n"}
    # Extra-field rules apply only to chat completions. Until each route has its
    # own rules, completions keeps its fixed schema for every policy profile.
    extra_rules = (
        {rule.name: rule for rule in policy.extra_fields}
        if request_kind == "chat"
        else {}
    )
    allowed_fields |= extra_rules.keys()
    unsupported = sorted(document.keys() - allowed_fields)
    if unsupported:
        return _decision(
            "INVALID",
            400,
            "UNSUPPORTED_FIELD",
            f"unsupported field(s): {', '.join(unsupported)}",
        )

    model_value = document.get("model")
    if not isinstance(model_value, str) or not model_value.strip():
        return _decision("INVALID", 400, "INVALID_MODEL", "model must be a non-empty string")
    model = model_value

    if request_kind == "chat":
        messages = document.get("messages")
        valid_messages = isinstance(messages, list) and bool(messages)
        if valid_messages:
            for message in messages:
                if (
                    not isinstance(message, dict)
                    or set(message) != {"role", "content"}
                    or not isinstance(message.get("role"), str)
                    or message.get("role") not in {"system", "user", "assistant"}
                    or not isinstance(message.get("content"), str)
                ):
                    valid_messages = False
                    break
        if not valid_messages:
            return _decision(
                "INVALID",
                400,
                "INVALID_MESSAGES",
                "messages do not match the supported format",
                model=model,
            )
    elif not isinstance(document.get("prompt"), str):
        return _decision(
            "INVALID",
            400,
            "INVALID_PROMPT_TYPE",
            "prompt must be a single string; arrays and token-id arrays are unsupported",
            model=model,
        )

    max_tokens_value = document.get("max_tokens", policy.output_default_tokens)
    if (
        not isinstance(max_tokens_value, int)
        or isinstance(max_tokens_value, bool)
        or max_tokens_value <= 0
    ):
        return _decision(
            "INVALID",
            400,
            "INVALID_MAX_TOKENS",
            "max_tokens must be a positive integer",
            model=model,
        )
    max_tokens = max_tokens_value

    if "stream" in document:
        stream = document["stream"]
        if not isinstance(stream, bool):
            return _decision(
                "INVALID",
                400,
                "INVALID_STREAM",
                "stream must be a boolean",
                model=model,
                max_tokens=max_tokens,
            )

    n: int | None = None
    if "n" in document:
        n = document["n"]
        if (
            not isinstance(n, int)
            or isinstance(n, bool)
            or n < 1
            or n > policy.max_n
        ):
            return _decision(
                "INVALID",
                400,
                "N_UNSUPPORTED",
                (
                    "n must be the integer 1"
                    if policy.max_n == 1
                    else f"n must be an integer between 1 and {policy.max_n}"
                ),
                model=model,
                max_tokens=max_tokens,
            )

    passed_extras: list[tuple[str, str]] = []
    for name in sorted(document.keys() & extra_rules.keys()):
        rule = extra_rules[name]
        if rule.mode == "validate":
            violation = _extra_field_violation(rule, document[name])
            if violation is not None:
                # Name the field and constraint only; never echo the value.
                return _decision(
                    "INVALID",
                    400,
                    "INVALID_EXTRA_FIELD",
                    f"extra field {name} violates {violation}",
                    model=model,
                    max_tokens=max_tokens,
                )
        passed_extras.append((name, rule.mode))

    if model not in policy.allowed_models:
        return _decision(
            "DENIED",
            403,
            "MODEL_NOT_ALLOWED",
            "model is not allowed by policy",
            rule_id="allowed_models",
            model=model,
            max_tokens=max_tokens,
        )
    if max_tokens > policy.output_max_tokens:
        return _decision(
            "DENIED",
            403,
            "MAX_TOKENS_EXCEEDED",
            "max_tokens exceeds the policy limit",
            rule_id="output_max_tokens",
            model=model,
            max_tokens=max_tokens,
        )
    return Decision(
        "ALLOWED",
        200,
        "REQUEST_ALLOWED",
        None,
        "request is allowed",
        model,
        max_tokens,
        extra_fields=tuple(passed_extras),
        n=n,
    )
