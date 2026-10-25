"""Bounded response evidence, without inferring consumption from HTTP success."""
from __future__ import annotations

import hashlib
import json
import re

from .content_common import MAX_SAMPLE_BYTES


OBSERVATIONS = {"challenge_rejection_results": "dict[str, outcome]",
                "challenge_consumption": "dict[str, outcome]",
                "confirmed_input_violations": "list[str]"}
PROFILE = "input-response-evidence/1"


def contract_state(check) -> str:
    markers = [obs for obs in check.required_observations if obs.key in OBSERVATIONS]
    if not markers:
        return "legacy"
    if (len(markers) == len(OBSERVATIONS) and {obs.key for obs in markers} == set(OBSERVATIONS)
            and all(obs.type.replace(" ", "") == OBSERVATIONS[obs.key].replace(" ", "") for obs in markers)):
        return "current"
    return "incomplete"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("nonfinite JSON constant")


def response_capture_metadata(response: dict) -> dict:
    """Describe original bytes before privacy redaction normalizes JSON text.

    Do not call the stored sample an unmodified wire capture. In particular,
    duplicate-key rejection must remain explainable after JSON redaction.
    """
    raw = response.get("body")
    value = {"stored_body_representation": "masked_normalized_json_or_text",
             "stored_body_is_verbatim": False, "original_json_state": "unavailable"}
    if not isinstance(raw, str):
        return value
    encoded = raw.encode("utf-8")
    value.update(original_body_sha256=hashlib.sha256(encoded).hexdigest(), original_body_bytes=len(encoded))
    try:
        json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except ValueError as exc:
        value["original_json_state"] = "duplicate_keys" if str(exc) == "duplicate JSON key" else "invalid_or_nonfinite"
    except (TypeError, RecursionError):
        value["original_json_state"] = "invalid_or_nonfinite"
    else:
        value["original_json_state"] = "valid"
    return value


def rejection_proof(adapter, env, *, case: str, method: str, path: str,
                    request_body: dict, response: dict, context=None) -> dict:
    """Only complete captures and a reviewed adapter can establish rejection.

    An adapter error is a collection gap, never confirmation of consumption or
    damage. Normal-looking generation responses remain unconfirmed too.
    """
    proof = {"state": "unverified", "reason": "REJECTION_UNVERIFIED", "profile": PROFILE, "sources": []}
    status = response.get("outcome")
    if type(status) is int and 200 <= status <= 299:
        return {**proof, "reason": "HTTP_SUCCESS_DOES_NOT_ESTABLISH_CONSUMPTION"}
    if type(status) is not int or status not in {400, 413, 422}:
        return {**proof, "reason": "RESPONSE_STATUS_NOT_REVIEWED_REJECTION"}
    raw = response.get("body")
    if not isinstance(raw, str):
        return {**proof, "reason": "FULL_RESPONSE_BODY_UNAVAILABLE"}
    encoded = raw.encode("utf-8")
    size = response.get("body_bytes", len(encoded))
    if (type(size) is not int or size != len(encoded) or size >= MAX_SAMPLE_BYTES
            or response.get("body_truncated") or response.get("truncated")):
        return {**proof, "reason": "RESPONSE_CAPTURE_INCOMPLETE"}
    proof["response_sha256"] = hashlib.sha256(encoded).hexdigest()
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (ValueError, TypeError, RecursionError):
        return {**proof, "reason": "RESPONSE_JSON_UNVERIFIED"}
    if type(payload) is not dict:
        return {**proof, "reason": "RESPONSE_JSON_UNVERIFIED"}
    reviewer = getattr(adapter, "input_rejection_proof", None)
    if not callable(reviewer):
        return {**proof, "reason": "ADAPTER_REJECTION_REVIEW_UNAVAILABLE"}
    try:
        extra = {"context": context} if context is not None else {}
        reviewed = reviewer(engine_version=env.engine.version, engine_image_digest=env.engine.image_digest,
                            case=case, method=method, path=path, request_body=request_body,
                            response_status=status, response_body=payload, **extra)
    except Exception:
        return {**proof, "reason": "ADAPTER_REJECTION_REVIEW_FAILED"}
    if (type(reviewed) is not dict or reviewed.get("state") not in {"verified", "unverified"}
            or type(reviewed.get("reason")) is not str
            or re.fullmatch(r"[A-Z0-9_:]+", reviewed["reason"]) is None
            or type(reviewed.get("sources")) is not list
            or reviewed["state"] == "verified" and not reviewed["sources"]):
        return {**proof, "reason": "ADAPTER_REJECTION_REVIEW_INVALID"}
    return {**proof, "state": reviewed["state"], "reason": reviewed["reason"], "sources": reviewed["sources"]}
