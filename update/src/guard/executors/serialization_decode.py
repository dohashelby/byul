"""Bounded decoder trial collection and policy facts; no parser implementation.

The caller supplies the actual reader and a scoped effect counter. Payloads,
decoded objects and exception messages never become evidence. This is not a
new executor/judge registration and makes no host-wide side-effect claim.
"""
from __future__ import annotations

import hashlib
import re

EFFECTS = frozenset({"callback", "pickle_global", "file_open", "socket_connect", "process_start"})


def collect_decoder_trial(decoder, payload, *, case, control_payload, effect_probe=None, rejection_types=()):
    if type(payload) is not bytes or type(control_payload) is not bytes:
        raise ValueError("one in-memory bytes frame per trial required")
    if type(case) is not str or re.fullmatch(r"[A-Za-z0-9_:-]{1,80}", case) is None:
        raise ValueError("fixed non-content case identifier required")
    gaps = []

    def control():
        try:
            value = decoder.decode([control_payload])
            return type(value) is dict and value == {"guard_control": True}
        except Exception:
            return False

    def measure():
        if effect_probe is None:
            gaps.append("EFFECT_MEASUREMENT_UNAVAILABLE")
            return {}
        try:
            value = effect_probe()
            if (type(value) is not dict or value.keys() - EFFECTS
                    or any(type(count) is not int or count < 0 for count in value.values())):
                raise ValueError("invalid effect counters")
            return dict(value)
        except Exception:
            gaps.append("EFFECT_MEASUREMENT_UNAVAILABLE")
            return {}

    before = control()
    state, attempted, shape = "not_attempted", False, None
    start = measure()
    if before:
        attempted = True
        try:
            value = decoder.decode([payload])
            state = "decoded"
            shape = ("mapping" if type(value) is dict else "sequence" if type(value) in (list, tuple)
                     else "raw_view" if type(value) is memoryview else "other")
        except rejection_types:
            state = "rejected"
        except Exception:
            state = "error"
            gaps.append("DECODE_ERROR_UNCLASSIFIED")
    else:
        gaps.append("NORMAL_BEFORE_FAILED")
    end = measure()
    observed = {key: end[key] - count for key, count in start.items() if key in end and end[key] >= count}
    if start.keys() != end.keys() or any(end.get(key, -1) < count for key, count in start.items()):
        gaps.append("EFFECT_COUNTER_DISCONTINUITY")
    after = control() if before else False
    if before and not after:
        gaps.append("NORMAL_AFTER_FAILED")
    return {"case": case, "fixture_sha256": hashlib.sha256(payload).hexdigest(), "fixture_bytes": len(payload),
            "attempted": attempted, "decode_state": state, "decoded_shape": shape,
            "effect_counts": observed, "normal_before": before, "normal_after": after,
            "coverage_gaps": sorted(set(gaps))}


def analyze_decoder_trial(trial, *, forbidden_effects=None, expected_rejection=None):
    """Produce rule-table-ready branch facts; unset policy never grants PASS."""
    if forbidden_effects is not None and (type(forbidden_effects) is not list
            or any(type(effect) is not str or effect not in EFFECTS for effect in forbidden_effects)):
        raise ValueError("explicit known effect names required")
    if expected_rejection is not None and type(expected_rejection) is not bool:
        raise ValueError("explicit technical rejection expectation required")
    gaps = list(trial["coverage_gaps"])
    if forbidden_effects is None:
        gaps.append("FORBIDDEN_EFFECT_POLICY_UNSET")
    for key in forbidden_effects or ():
        if key not in trial["effect_counts"]:
            gaps.append("REQUIRED_EFFECT_NOT_MEASURED:" + key)
    if expected_rejection is True and trial["decode_state"] != "rejected":
        gaps.append("EXPECTED_REJECTION_UNCONFIRMED")
    if expected_rejection is False and trial["decode_state"] != "decoded":
        gaps.append("NORMAL_DECODE_UNCONFIRMED")
    violations = sorted(key for key in forbidden_effects or () if trial["effect_counts"].get(key, 0) > 0)
    return {"decoder_forbidden_effects_seen": violations, "decoder_coverage_complete": not gaps,
            "decoder_coverage_gaps": sorted(set(gaps)), "normal_controls": bool(
                trial["normal_before"] and trial["normal_after"])}
