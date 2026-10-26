"""Bounded content inspection shared by response and log checks.

Field paths describe JSON leaves (``choices[].text``), never a regex. Marker
matches keep their field location so caller prompt quotations are not confused
with a separately supplied private system context.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
from pathlib import Path
from typing import Any

from guard.executors.base import DraftArtifact
from guard.automation_policy import string_list
from .http_common import incomplete_artifact_capture, mask

MAX_SAMPLE_BYTES = 1_000_000
_CREDENTIAL_KEY = re.compile(r"(?i)^(authorization|password|secret|api[_-]?key|access[_-]?token|credential)$")
_CREDENTIAL_ASSIGNMENT = re.compile(r'''(?i)(?<![A-Za-z0-9_])["']?(?:authorization|password|secret|api[_-]?key|access[_-]?token|credential)["']?\s*[:=]''')


def redact(text: str, secrets_: list[str]) -> str:
    # Plain logger/Python repr values can be quoted, lists, SecretStr wrappers
    # or truncated. Their boundary is not safely inferable: redact the line.
    if _CREDENTIAL_ASSIGNMENT.search(text):
        return "[REDACTED: credential-bearing content]" + ("\n" if text.endswith("\n") else "")
    return mask(text, secrets_)


def redact_value(value: Any, secrets_: list[str]) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _CREDENTIAL_KEY.fullmatch(str(key)) else redact_value(child, secrets_)
                for key, child in value.items()}
    if isinstance(value, list):
        return [redact_value(child, secrets_) for child in value]
    if isinstance(value, str):
        # Log text can itself contain JSON lines. Redact their labelled values
        # structurally before falling back to labelled plain-text assignments.
        lines = []
        for line in value.splitlines(keepends=True):
            prefix_match = re.match(r"^(\d{4}-\d{2}-\d{2}T[^\s]+\s+)(\{.*)$", line.rstrip("\r\n"))
            prefix, payload = (prefix_match.group(1), prefix_match.group(2)) if prefix_match else ("", line)
            try:
                structured = json.loads(payload)
            except (ValueError, TypeError):
                # Previews and bounded captures may truncate JSON before its
                # closing delimiter. Do not depend on successful parsing to
                # remove a labelled credential from that partial line.
                lines.append(redact(line, secrets_))
            else:
                if isinstance(structured, (dict, list)):
                    lines.append(prefix + json.dumps(redact_value(structured, secrets_), ensure_ascii=False)
                                 + ("\n" if line.endswith("\n") else ""))
                elif isinstance(structured, str):
                    lines.append(json.dumps(redact_value(structured, secrets_), ensure_ascii=False)
                                 + ("\n" if line.endswith("\n") else ""))
                else:
                    lines.append(redact(line, secrets_))
        return "".join(lines)
    return value


def markers() -> dict[str, str]:
    token = secrets.token_hex(6)
    return {"caller_input": f"G{token}U", "private_system": f"G{token}S", "error_input": f"G{token}E"}


def leaves(value: Any, prefix: str = "") -> dict[str, list[Any]]:
    """Return leaf locations, retaining duplicate array locations as values.

    Ordinary API keys keep dotted paths. Empty/literal ``.[]`` keys use JSON
    quoted brackets (``[\"a.b\"]``), distinct from object/array traversal. Policy
    strings are compared verbatim; they are never automatically normalized.
    """
    result: dict[str, list[Any]] = {}
    if isinstance(value, dict) and value:
        for key, child in value.items():
            name = str(key)
            special = not name or any(char in name for char in ".[]")
            path_prefix = prefix + "[" + json.dumps(name, ensure_ascii=False) + "]" if special else (
                f"{prefix}.{name}" if prefix else name)
            for path, values in leaves(child, path_prefix).items():
                result.setdefault(path, []).extend(values)
    elif isinstance(value, list) and value:
        for child in value:
            for path, values in leaves(child, prefix + "[]").items():
                result.setdefault(path, []).extend(values)
    else:
        result[prefix] = [value]
    return result


def marker_fields(value: Any, marker: str) -> list[str]:
    return sorted(path for path, values in leaves(value).items()
                  if any(isinstance(item, str) and marker in item for item in values))


def json_body(result: dict) -> tuple[Any, str | None]:
    raw = result.get("body")
    if not isinstance(raw, str):
        return None, "full response body unavailable"
    size = result.get("body_bytes", len(raw.encode()))
    if type(size) is not int or size < 0:
        return None, "invalid response capture size"
    if size >= MAX_SAMPLE_BYTES or len(raw.encode()) >= MAX_SAMPLE_BYTES:
        return None, "response reached the bounded capture limit"
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None, "response is not JSON"
    if not isinstance(value, dict):
        return None, "response is not a JSON object"
    return value, None


def sample_artifact(ctx, filename: str, value: Any) -> None:
    ctx._sample_artifacts = {*getattr(ctx, "_sample_artifacts", ()), filename}
    try:
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        # Mask before persistence. The unredacted capture digest is provenance,
        # not a claim that the persisted redacted file contains those raw bytes.
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        payload = {"unredacted_capture_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                   "encoding": "UTF-8; source CLI decoding may replace invalid bytes",
                   "redaction": "configured authorization keys and labelled credential values",
                   "capture": redact_value(value, ctx.secrets)}
        (ctx.workdir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # Keep collecting; common finish/append excludes partial files and
        # returns the valid observations as ABORTED with a fixed diagnostic.
        ctx._failed_artifacts = {*getattr(ctx, "_failed_artifacts", ()), filename}


def text_artifact(ctx, filename: str, value: str) -> None:
    """Capture exact UTF-8 text bytes through the same partial-artifact boundary."""
    ctx._sample_artifacts = {*getattr(ctx, "_sample_artifacts", ()), filename}
    try:
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        (ctx.workdir / filename).write_bytes(value.encode("utf-8"))
    except Exception:
        ctx._failed_artifacts = {*getattr(ctx, "_failed_artifacts", ()), filename}


def append_artifact(draft, ctx, filename: str, collector: str):
    if filename in getattr(ctx, "_failed_artifacts", ()):
        return incomplete_artifact_capture(draft)
    path = Path(ctx.workdir) / filename
    try:
        if path.exists():
            draft.artifacts.append(DraftArtifact(name=filename, path=path, collector=collector))
        elif filename in getattr(ctx, "_sample_artifacts", ()):
            ctx._failed_artifacts = {*getattr(ctx, "_failed_artifacts", ()), filename}
            return incomplete_artifact_capture(draft)
    except Exception:
        ctx._failed_artifacts = {*getattr(ctx, "_failed_artifacts", ()), filename}
        return incomplete_artifact_capture(draft)
    return draft


def content_request(ctx, target: str, label: str, endpoint: str, body: dict) -> dict:
    from guard.adapters.base import ProbeRequest
    key = ctx.keys.get("allowed")
    base = ctx.engine_url if target == "engine" else ctx.gateway_url
    return ctx.request(label, base, ProbeRequest("POST", endpoint, body), key, want_body=True)


def send_content(ctx, target: str, label: str, endpoint: str, body: dict) -> dict:
    request = content_request(ctx, target, label, endpoint, body)
    network = ctx.trusted_net if target == "engine" else ctx.external_net
    result = bounded_probe(ctx, network, request)
    record_content(ctx, "trusted" if target == "engine" else "external", target, label, result)
    return result


def bounded_probe(ctx, network: str, request: dict) -> dict:
    """Retain earlier observations if a later transport/collector call fails.

    Exception messages may contain authorization headers, daemon diagnostics or
    caller data. Only the exception type is evidence of the collection failure.
    """
    try:
        results = ctx.cprobe(network, [request], ctx.timeout)
    except Exception as exc:
        return {"outcome": "probe_error", "phase": "collection", "detail": type(exc).__name__}
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        return {"outcome": "probe_error", "phase": "collection", "detail": "invalid_probe_response"}
    result = results[0]
    outcome = result.get("outcome")
    if not (type(outcome) is int and 100 <= outcome <= 599 or type(outcome) is str and outcome in {
            "refused", "timeout", "unreachable", "dns_error", "error", "probe_error"}):
        return {"outcome": "probe_error", "phase": "collection", "detail": "invalid_probe_outcome"}
    if result.get("body") is not None and not isinstance(result["body"], str):
        return {"outcome": "probe_error", "phase": "collection", "detail": "invalid_probe_body"}
    return result


def record_content(ctx, origin: str, via: str, label: str, result: dict) -> None:
    ctx.record(origin, via, label, "allowed", redact_value(result, ctx.secrets))


def validate_content_settings(cfg: dict) -> None:
    timeout = cfg.get("request_timeout", 3.0)
    if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("request_timeout must be a finite number greater than 0 and at most 30 seconds")


def control_state(result: dict, expected_model: str | None = None, endpoint: str | None = None) -> str:
    value = result.get("outcome")
    if type(value) is int:
        if value != 200:
            return "fail"
        body, error = json_body(result)
        if error or not isinstance(body.get("model"), str) or (expected_model and body["model"] != expected_model):
            return "no_response"
        choices, usage = body.get("choices"), body.get("usage")
        if not isinstance(choices, list) or not choices or not isinstance(usage, dict):
            return "no_response"
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if type(usage.get(name)) is not int or usage[name] < 1:
                return "no_response"
        if usage["total_tokens"] < usage["prompt_tokens"] + usage["completion_tokens"]:
            return "no_response"
        for choice in choices:
            if (not isinstance(choice, dict) or not isinstance(choice.get("finish_reason"), str)
                    or choice["finish_reason"] not in {"stop", "length"}):
                return "no_response"
            if endpoint == "/v1/chat/completions":
                message = choice.get("message")
                if not isinstance(message, dict) or message.get("role") != "assistant" or not isinstance(message.get("content"), str):
                    return "no_response"
            elif not isinstance(choice.get("text"), str):
                return "no_response"
        return "pass"
    return "no_response"
