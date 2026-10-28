"""REQ-13 error-branch observations, separate from OBS-03 log observations.

In-memory analysis only. No request/response/exception text is returned. These
are branch observations for the existing executor, not a new CheckDef judge.
An unset enterprise policy does not prevent collecting reference-code facts.
"""
from __future__ import annotations

import hashlib

from .content_common import MAX_SAMPLE_BYTES, json_body, leaves
from .log_analysis_contract import strict_json_loads

CONTEXTS = frozenset({"caller_input", "error_input", "private_system", "internal_path", "internal_address"})


def _policy(marks, forbidden_contexts):
    if (not isinstance(marks, dict) or not marks or marks.keys() - CONTEXTS
            or any(type(value) is not str or not value for value in marks.values())):
        raise ValueError("explicit nonempty synthetic markers with known contexts required")
    if forbidden_contexts is not None and (
            type(forbidden_contexts) is not list
            or any(type(key) is not str or key not in marks for key in forbidden_contexts)
            or len(set(forbidden_contexts)) != len(forbidden_contexts)):
        raise ValueError("forbidden contexts must explicitly name supplied synthetic markers")


def _summary(text, marks, forbidden_contexts, gaps):
    hits = sorted(key for key, marker in marks.items() if marker in text)
    violations = sorted(set(hits) & set(forbidden_contexts or ()))
    if forbidden_contexts is None:
        gaps.append("MARKER_POLICY_UNSET")
    return {"marker_contexts_seen": hits, "forbidden_contexts_seen": violations,
            "capture_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "capture_bytes": len(text.encode()), "violations": ["FORBIDDEN_MARKER:" + key for key in violations]}


def observe_error_response(result, *, marks, allowed_fields=None, forbidden_contexts=None,
                           expected_status=400):
    """Inspect retained error bytes, preserving marker violations on parse gaps.

    Only an explicitly configured policy identifies forbidden contexts/fields.
    Hash unknown field paths too: a literal JSON key can itself carry content.
    This does not establish the absence of any unmarked private information.
    """
    _policy(marks, forbidden_contexts)
    if type(expected_status) is not int or not 400 <= expected_status <= 599:
        raise ValueError("one explicit error status required")
    if allowed_fields is not None and (type(allowed_fields) is not list
            or any(type(path) is not str or not path for path in allowed_fields)):
        raise ValueError("allowed fields must be explicit nonempty path strings")
    gaps = []
    if not isinstance(result, dict):
        result = {}
    text = result.get("body")
    if type(text) is not str:
        text = ""
        gaps.append("BODY_UNAVAILABLE")
    summary = _summary(text, marks, forbidden_contexts, gaps)
    status = result.get("outcome")
    if type(status) is not int or status != expected_status:
        gaps.append("EXPECTED_ERROR_STATUS_UNCONFIRMED")
    try:
        value, reason = json_body({**result, "body": text})
    except (ValueError, TypeError, RecursionError):
        value, reason = None, "unavailable"
    if reason:
        gaps.append("JSON_CAPTURE_UNAVAILABLE")
    else:
        try:
            value = strict_json_loads(text)
        except (ValueError, TypeError, RecursionError):
            value = None
            gaps.append("JSON_AMBIGUOUS")
    fields = leaves(value) if isinstance(value, (dict, list)) else {}
    if not fields:
        gaps.append("ERROR_FIELDS_UNAVAILABLE")
    if allowed_fields is None:
        gaps.append("FIELD_POLICY_UNSET")
    unexpected = sorted(set(fields) - set(allowed_fields or ())) if allowed_fields is not None else []
    summary["violations"].extend("NONPERMITTED_FIELD_SHA256:" + hashlib.sha256(path.encode()).hexdigest()
                                 for path in unexpected)
    # JSON escaping can hide a marker in the raw encoding.
    if value is not None:
        decoded_hits = {key for key, marker in marks.items()
                        if any(marker in path for path in fields)
                        or any(marker in item for items in fields.values() for item in items if isinstance(item, str))}
        summary["marker_contexts_seen"] = sorted(set(summary["marker_contexts_seen"]) | decoded_hits)
        forbidden = sorted(decoded_hits & set(forbidden_contexts or ()))
        summary["forbidden_contexts_seen"] = sorted(set(summary["forbidden_contexts_seen"]) | set(forbidden))
        summary["violations"] = sorted(set(summary["violations"]) | {"FORBIDDEN_MARKER:" + key for key in forbidden})
    return {**summary, "surface": "error_response", "outcome": status if type(status) is int else None,
            "field_path_sha256": sorted(hashlib.sha256(path.encode()).hexdigest() for path in fields),
            "coverage_gaps": sorted(set(gaps)), "complete": not gaps}


def observe_error_log(text, *, marks, forbidden_contexts=None, source_complete=False):
    """Inspect exactly one designated handler logger capture, never all logs."""
    _policy(marks, forbidden_contexts)
    gaps = []
    if type(text) is not str:
        text = ""
        gaps.append("LOG_UNAVAILABLE")
    if source_complete is not True:
        gaps.append("LOG_CAPTURE_INCOMPLETE")
    if len(text.encode()) >= MAX_SAMPLE_BYTES:
        gaps.append("LOG_CAPTURE_BOUND_REACHED")
    summary = _summary(text, marks, forbidden_contexts, gaps)
    return {**summary, "surface": "designated_error_logger", "coverage_gaps": sorted(set(gaps)),
            "complete": not gaps}
