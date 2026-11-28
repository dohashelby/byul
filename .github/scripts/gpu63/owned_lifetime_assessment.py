"""Project sampled per-connection ownership without treating a gap as release."""

from collections import Counter
import errno

from owned_stream_observer import COUNT_FIELDS, RESOURCE_NAMES
from owned_stream_poll import CASE_IDS


REQUIRED_COMPONENTS = (*RESOURCE_NAMES, "response_debug_buffer")

def sample_point(row, value):
    return {"start_ns": row.get("sample_started_monotonic_ns", row["mono_ns"]),
            "end_ns": row["mono_ns"], "utc_ns": row.get("utc_ns"), "phase": row["phase"], "value": value}


def ownership_timeline(records, extract, deadline, *, inactive=False):
    known = [sample_point(row, value) for row in records if (value := extract(row)) is not None]
    held = [point for point in known if point["value"] > 0]
    first_empty = next((point for point in known if point["value"] == 0), None)
    initial, last_held = (held[0], held[-1]) if held else (None, None)
    first_release = next((point for point in known if last_held is not None and point["value"] == 0
                          and point["start_ns"] >= last_held["end_ns"]), None)
    boundaries = [point for point in known if deadline and point["start_ns"] >= deadline["latest_ns"]]
    boundary = boundaries[0] if boundaries else None
    status = "conditional_inactive" if inactive else "unobserved"
    if not inactive and known:
        if deadline is None:
            status = "close_origin_unavailable"
        elif first_release and last_held["start_ns"] >= deadline["latest_ns"]:
            status = "released_after_deadline"
        elif last_held and last_held["start_ns"] >= deadline["latest_ns"]:
            status = "retained_at_or_after_deadline"
        elif first_release and first_release["end_ns"] <= deadline["earliest_ns"] and boundary and boundary["value"] == 0:
            status = "released_within_deadline"
        elif first_release and first_release["end_ns"] > deadline["earliest_ns"]:
            status = "release_interval_crosses_deadline"
        elif not held and first_empty and first_empty["end_ns"] <= deadline["earliest_ns"] and boundary:
            status = "empty_in_samples_through_deadline"
        else:
            status = "deadline_state_unobserved"
    return {"status": status, "initial_sample": known[0] if known else None,
            "first_held": initial, "last_held": last_held, "first_empty": first_empty,
            "first_release_after_last_held": first_release,
            "release_interval_ns": {"lower": last_held["start_ns"], "upper": first_release["end_ns"]}
                                   if first_release is not None else None,
            "first_sample_at_or_after_deadline": boundary,
            "highest_measured_value": max((point["value"] for point in known), default=None),
            "last_supported_sample": known[-1] if known else None, "supported_samples": len(known)}


APP_COMPLETE = frozenset(("conditional_inactive", "released_within_deadline", "empty_in_samples_through_deadline"))
APP_RESIDUAL = frozenset(("released_after_deadline", "retained_at_or_after_deadline"))
KERNEL_RESIDUAL = frozenset(("control_block_at_or_after_deadline", "socket_at_or_after_deadline"))


def socket_followup(rows, deadline, stop):
    observed = [row for row in rows if row["status"] in ("present", "time_wait")]
    last = observed[-1] if observed else None
    later = [row for row in rows if last and row["start_ns"] >= last["end_ns"]]
    absent = next((row for row in later if row["status"] == "absent"), None)
    failures = sorted({row["status"] for row in later if row["status"] != "absent"
                       and (absent is None or row["start_ns"] < absent["start_ns"])})
    after = deadline and absent and absent["start_ns"] >= deadline["latest_ns"]
    if absent is not None:
        status = "exact_absence_observed_after_last_owned_state"
        reason = "intervening_lookup_or_identity_uncertainty" if failures else None
    elif last is not None:
        status = "disappearance_unconfirmed"
        reason = stop.get("reason", "followup_finished_without_terminal_exact_sample")
    else:
        status, reason = "owned_positive_unavailable", "no_same_cookie_positive_on_this_endpoint"
    last_time_wait = next((row for row in reversed(rows) if row["status"] == "time_wait"), None)
    listener_replies = [row for row in rows if last_time_wait and row["start_ns"] >= last_time_wait["end_ns"]
                        and row["status"] == "listener_reply" and row.get("cookie_errno") == errno.ENOENT]
    fallback = None
    if listener_replies:
        fallback = {"status": "connected_state_no_longer_resolved_by_this_exact_lookup",
                    "last_same_cookie_time_wait": last_time_wait,
                    "first_listener_fallback": listener_replies[0],
                    "last_listener_fallback": listener_replies[-1],
                    "listener_fallback_samples": [{"start_ns": row["start_ns"], "end_ns": row["end_ns"]}
                                                  for row in listener_replies],
                    "connected_lookup_transition_interval_ns": {"lower": last_time_wait["start_ns"],
                                                                "upper": listener_replies[0]["end_ns"]},
                    "proves_exact_absence_or_memory_release": False}
    return {"status": status, "last_owned_sample": last, "first_exact_absence_after_last_owned": absent,
            "first_exact_absence_is_after_five_second_window": bool(after),
            "disappearance_interval_ns": {"lower": last["start_ns"], "upper": absent["end_ns"]} if absent else None,
            "right_censored_or_uncertain_reason": reason, "intervening_lookup_failures": failures,
            "last_sample": rows[-1] if rows else None, "stop": stop, "time_wait_then_listener_fallback": fallback}


def full_assessment(cases):
    case_ids = [case["http"]["case_id"] for case in cases]
    counts = Counter(case_ids)
    missing_cases = [case for case in CASE_IDS if not counts[case]]
    duplicate_cases = sorted(case for case, count in counts.items() if count > 1)
    unexpected_cases = sorted(set(counts).difference(CASE_IDS))
    results = {}
    for case in cases:
        case_id = case["http"]["case_id"]
        if case_id not in CASE_IDS or case_id in results:
            continue
        app = case.get("resource_assessment", {})
        endpoints = case.get("endpoints", {})
        gaps, retained = {}, {}
        if counts[case_id] > 1:
            gaps["case_id"] = "duplicate_case_id"
        if case["status"] != "independent_observation_collected":
            gaps["request_binding"] = case["status"]
        missing_controls = []
        for role in ("engine", "probe"):
            window = endpoints.get(role, {}).get("window", {})
            if window.get("positive_control") is not True:
                missing_controls.append(role)
                gaps[f"tcp_{role}_positive_control"] = "same_generation_positive_unavailable"
            result = window.get("status", "unobserved")
            if result in KERNEL_RESIDUAL:
                retained[f"tcp_{role}"] = result
            elif result != "absent_observed_within_window":
                gaps[f"tcp_{role}"] = result
                sample = window.get("first_observation_at_or_after_deadline") or {}
                if sample.get("status") in ("present", "time_wait"):
                    retained[f"tcp_{role}"] = sample["status"]
        missing_fields, extra_fields = {}, {}
        for category, required in (("components", REQUIRED_COMPONENTS), ("counts_and_queues", COUNT_FIELDS)):
            observed = app.get(category, {})
            missing_fields[category] = [name for name in required if name not in observed]
            extra_fields[category] = sorted(set(observed).difference(required))
            for resource in missing_fields[category]:
                gaps[resource] = "required_observation_missing"
            for resource in extra_fields[category]:
                gaps[resource] = "unexpected_observation_field"
            for resource in required:
                if resource not in observed:
                    continue
                status = observed[resource].get("status", "observation_status_missing")
                if status in APP_RESIDUAL:
                    retained[resource] = status
                elif status not in APP_COMPLETE:
                    gaps[resource] = status
        results[case_id] = {
            "whole_resource_reclamation_verified": not gaps and not retained,
            "observed_retention_at_or_after_deadline": retained,
            "unconfirmed_resources_or_observations": gaps,
            "missing_producer_fields": missing_fields, "unexpected_producer_fields": extra_fields,
            "missing_same_generation_positive_controls": missing_controls,
        }
    return {"whole_resource_reclamation_verified": not (missing_cases or duplicate_cases or unexpected_cases) and
                all(case["whole_resource_reclamation_verified"] for case in results.values()),
            "required_case_ids": list(CASE_IDS), "missing_case_ids": missing_cases,
            "duplicate_case_ids": duplicate_cases, "unexpected_case_ids": unexpected_cases,
            "required_producer_fields": {"components": list(REQUIRED_COMPONENTS), "counts_and_queues": list(COUNT_FIELDS)},
            "by_case": results}
