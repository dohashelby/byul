"""Pure request validation and policy decisions for the MVP gateway."""

from dataclasses import dataclass
import json
from typing import Any, Literal

from .policy import DuplicateKeyError, PolicyState, _object_without_duplicates, _reject_json_constant


@dataclass(frozen=True)
class Decision:
    result: Literal["ALLOWED", "DENIED", "INVALID", "ERROR"]
    http_status: int
    reason_code: str
    rule_id: str | None
    message: str
    model: str | None
    max_tokens: int | None


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


def validate_request(
    body: bytes, content_type: str | None, policy_state: PolicyState
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
        return _decision("INVALID", 400, "DUPLICATE_KEY", str(error))
    except (json.JSONDecodeError, ValueError, RecursionError) as error:
        return _decision("INVALID", 400, "MALFORMED_JSON", f"invalid JSON: {error}")

    if not isinstance(document, dict):
        return _decision("INVALID", 400, "INVALID_REQUEST", "request must be a JSON object")

    if "max_completion_tokens" in document:
        return _decision(
            "INVALID",
            400,
            "UNSUPPORTED_ALIAS",
            "max_completion_tokens is not supported; use max_tokens",
        )
    allowed_fields = {"model", "messages", "max_tokens", "stream", "n"}
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
            "INVALID", 400, "INVALID_MESSAGES", "messages do not match the supported format", model=model
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
        if stream:
            return _decision(
                "INVALID",
                400,
                "STREAM_UNSUPPORTED",
                "streaming is not supported",
                model=model,
                max_tokens=max_tokens,
            )

    if "n" in document:
        n = document["n"]
        if not isinstance(n, int) or isinstance(n, bool) or n != 1:
            return _decision(
                "INVALID",
                400,
                "N_UNSUPPORTED",
                "n must be the integer 1",
                model=model,
                max_tokens=max_tokens,
            )

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
    return _decision(
        "ALLOWED",
        200,
        "REQUEST_ALLOWED",
        "request is allowed",
        model=model,
        max_tokens=max_tokens,
    )
