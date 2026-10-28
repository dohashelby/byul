"""Content-free HTTP evidence metadata shared by input and sampling collectors.

These are storage projections, not rejection or consumption proofs. Reviewers
receive the original transient response before any projection is persisted.
"""
import math

from .input_response_semantics import response_capture_metadata


def capture_metadata(response):
    try:
        capture = response_capture_metadata(response)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        capture = {"stored_body_is_verbatim": False, "original_json_state": "unavailable",
                   "diagnostic": "INPUT_CAPTURE_METADATA_UNAVAILABLE"}
    return {**capture, "stored_body_representation": "omitted_hash_and_parse_state_only"}


def log_metadata(response):
    value = {"outcome": response.get("outcome"), "detail": "RAW_INPUT_RESPONSE_OMITTED"}
    elapsed = response.get("elapsed_ms")
    if type(elapsed) in (int, float) and 0 <= elapsed <= 86_400_000 and math.isfinite(elapsed):
        value["elapsed_ms"] = elapsed
    return value
