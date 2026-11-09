"""Remove known unrequested response metadata at OpenAI protocol boundaries.

Generated content, reasoning, tool arguments and arbitrary vendor payloads are
opaque. This is an exact metadata-field policy, with no keyword filtering.
"""
from __future__ import annotations

from typing import Any

# Request options are already validated by the caller. Presence in this set is
# permission to return that optional metadata, not proof that an option is safe
# for a particular deployment profile.
_METADATA_OPTIONS = {
    "prompt": frozenset({"return_prompt_text"}),
    "prompt_text": frozenset({"return_prompt_text"}),
    "prompt_token_ids": frozenset({"return_token_ids", "return_tokens_as_token_ids"}),
    "input_token_ids": frozenset({"return_token_ids", "return_tokens_as_token_ids"}),
    "output_token_ids": frozenset({"return_token_ids", "return_tokens_as_token_ids"}),
    "token_ids": frozenset({"return_token_ids", "return_tokens_as_token_ids"}),
    "prompt_logprobs": frozenset({"prompt_logprobs"}),
    "prompt_log_probs": frozenset({"prompt_logprobs"}),
    "logprobs": frozenset({"logprobs"}),
    "private_debug": frozenset(),
    "kv_transfer_params": frozenset(),
}


MAX_METADATA_DEPTH = 64
_CONTENT_FIELDS = frozenset({"content", "text", "reasoning", "reasoning_content"})


class ResponsePrivacyError(ValueError):
    """Metadata cannot be safely projected within the relay's limits."""


def sanitize_response_metadata(value: dict[str, Any], *,
                               requested_fields: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Copy metadata recursively; keep actual generated fields opaque.

    The relay bounds input bytes and handles this ValueError as a safe backend
    response failure. It supplies only validated return-option names; zero is
    a requested prompt_logprobs count. Generated content and tool argument
    values are never keyword-filtered or walked as metadata.
    """
    if not isinstance(value, dict):
        raise ResponsePrivacyError("response privacy requires a JSON object")

    def visit(item, context, depth):
        if depth > MAX_METADATA_DEPTH:
            raise ResponsePrivacyError("response metadata nesting exceeds the supported limit")
        if isinstance(item, list):
            child_context = {"choices": "choice", "tool_calls": "tool_call"}.get(context, "metadata")
            return [visit(child, child_context, depth + 1) for child in item]
        if not isinstance(item, dict):
            return item
        result = {}
        for key, child in item.items():
            if key in _METADATA_OPTIONS and not (_METADATA_OPTIONS[key] & requested_fields):
                continue
            if ((context in {"choice", "message", "delta"} and key in _CONTENT_FIELDS)
                    or (context == "function" and key == "arguments")):
                result[key] = child
                continue
            child_context = "metadata"
            if context == "root" and key == "choices":
                child_context = "choices"
            elif context == "choice" and key in {"message", "delta"}:
                child_context = key
            elif context in {"message", "delta"} and key == "tool_calls":
                child_context = "tool_calls"
            elif ((context == "tool_call" and key == "function")
                  or (context in {"message", "delta"} and key == "function_call")):
                child_context = "function"
            result[key] = visit(child, child_context, depth + 1)
        return result

    return visit(value, "root", 0)
