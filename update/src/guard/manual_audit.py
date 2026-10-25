"""OBS-11 declared policy/evidence shape. Pure helpers; no store or file access."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re

from guard.manual_policy import policy_items

OBSERVATION = "declared_audit_evidence"
CONTRACT = "guard-audit-evidence/1"
PREFIX = "AUDIT_COLLECTION:"
CASES = ("normal", "collection_fault", "clock_uncertain")
BUDGETS = ("collection_deadline_ms", "max_clock_uncertainty_ms", "detection_deadline_ms", "alert_deadline_ms")
POLICY_TYPES = {"required_records": "list[str]", **dict.fromkeys(BUDGETS, "dict[str,int]")}
TIMES = ("occurred_at", "received_at", "window_start_at", "window_end_at", "detected_at",
         "alert_received_at", "alert_window_start_at", "alert_window_end_at")
CLAIMS = {"profile", "occurrence", "independence", "collection", "window", "clock", "collector",
          "detection", "alert", "alert_window", "dependent", "dependent_failure", "recovery"}
TEXT = ("trial_id", "event_id", "target_scope", "path_id", "profile_detail", "case_detail",
        "occurrence_source", "collection_source", "independence_detail", "clock_source",
        "window_source", "window_detail", "alert_receiver", "alert_source", "alert_window_detail")
ENUMS = {
    "profile_applicability": ("confirmed", "unknown", "unsupported"),
    "fault_method": ("collector_interrupt", "write_denied", "unknown"),
    "occurrence": ("confirmed", "unknown"), "record": ("received", "absent", "unknown"),
    "clock_basis": ("bounded_common_time", "unknown"),
    "collection_window": ("complete", "incomplete", "unknown"),
    "collector": ("healthy", "interrupted", "write_denied", "unknown"),
    "missing_handling": ("identified", "treated_normal", "unknown"),
    "alert": ("received", "absent", "unknown"), "alert_window": ("complete", "incomplete", "unknown"),
    "recovery": ("restored", "not_restored", "not_attempted", "unknown"),
}
FIELDS = (*TEXT, *TIMES, *ENUMS, "attempted", "time_errors_ms", "dependent")
DEPENDENT = {"test_id", "execution_status", "security", "depends_on_record", "independent_failure"}
_RFC3339 = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})\Z")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def enabled(check) -> bool:
    return check.id == "OBS-11" and all(
        any(p.key == key and p.type == type_ and p.required for p in check.policy_params)
        for key, type_ in POLICY_TYPES.items())


def policy_state(values):
    """Return identifiable items, completeness, and per-item valid budgets.

    Incomplete other items must not erase an independently supported failure.
    No policy value is normalized or fabricated here.
    """
    items = policy_items(values, "required_records")
    complete = items is not None
    if items is None:
        given = values.get("required_records")
        items = [i for i in given if isinstance(i, str) and i.strip() and i == i.strip() and given.count(i) == 1] if isinstance(given, list) else []
    budgets = {item: {} for item in items}
    for key in BUDGETS:
        mapping = values.get(key)
        if not isinstance(mapping, dict):
            complete = False
            continue
        if set(mapping) != set(items):
            complete = False
        for item in items:
            value = mapping.get(item)
            if type(value) is int and value >= (1 if key == "collection_deadline_ms" else 0):
                budgets[item][key] = value
            else:
                complete = False
    return items, complete, budgets


def timestamp(value) -> int | None:
    """Strict, quoted RFC3339 with at most millisecond precision; no rounding."""
    if value is None:
        return None
    if not isinstance(value, str) or not _RFC3339.fullmatch(value):
        raise ValueError("audit timestamp must be quoted RFC3339 with timezone and at most 3 fractional digits")
    if value.endswith("-00:00") or (not value.endswith("Z") and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59)):
        raise ValueError("audit timestamp requires a supported, explicit timezone offset")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        delta = dt - _EPOCH
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid audit timestamp") from exc
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def blank_evidence(check, values):
    return {"contract": CONTRACT, "records": [
        {"record_id": item, "case": case, "observations": dict.fromkeys(FIELDS), "evidence": [], "limitation": None}
        for item in policy_items(values, "required_records") or [] for case in CASES]}


def normalize(check, values, value, attachments, *, stored=False):
    if value is None:
        return None
    if not enabled(check):
        raise ValueError("audit_evidence is unsupported for this definition")
    if not isinstance(value, dict) or set(value) - {"contract", "records"} or value.get("contract") != CONTRACT:
        raise ValueError("unsupported audit_evidence contract/fields")
    records = value.get("records", [])
    if not isinstance(records, list):
        raise ValueError("audit_evidence records must be a list")
    known = values.get("required_records", [])
    known = known if isinstance(known, list) else []
    result, seen = [], set()
    for row in records:
        if not isinstance(row, dict) or set(row) - {"record_id", "case", "observations", "evidence", "limitation"}:
            raise ValueError("invalid audit record fields")
        item, case = row.get("record_id"), row.get("case")
        if not isinstance(item, str) or item not in known or not isinstance(case, str) or case not in CASES:
            raise ValueError("audit record/case is not in the pinned policy/contract")
        if (item, case) in seen:
            raise ValueError("duplicate audit record/case")
        seen.add((item, case))
        obs = row.get("observations")
        obs = {} if obs is None else obs
        if not isinstance(obs, dict) or set(obs) - set(FIELDS):
            raise ValueError("invalid audit observation fields")
        for field in TEXT:
            if obs.get(field) is not None and not isinstance(obs[field], str):
                raise ValueError(f"audit {field} must be text or null")
        for field in TIMES:
            timestamp(obs.get(field))
        for field, choices in ENUMS.items():
            if obs.get(field) is not None and obs[field] not in choices:
                raise ValueError(f"invalid audit {field}")
        if obs.get("attempted") is not None and type(obs["attempted"]) is not bool:
            raise ValueError("audit attempted must be boolean or null")
        errors = obs.get("time_errors_ms")
        if errors is not None and (not isinstance(errors, dict) or set(errors) - set(TIMES)
                                  or any(type(v) is not int or v < 0 for v in errors.values())):
            raise ValueError("audit time_errors_ms must map timestamp fields to nonnegative integers")
        dep = obs.get("dependent")
        if dep is not None:
            if not isinstance(dep, dict) or set(dep) - DEPENDENT:
                raise ValueError("invalid audit dependent fields")
            if dep.get("test_id") is not None and not isinstance(dep["test_id"], str):
                raise ValueError("audit dependent test_id must be text or null")
            if dep.get("execution_status") not in (None, "COMPLETED", "ABORTED", "NOT_RUN", "unknown"):
                raise ValueError("invalid audit dependent execution_status")
            if dep.get("security") not in (None, "PASS", "FAIL", "UNKNOWN"):
                raise ValueError("invalid audit dependent security")
            if any(dep.get(f) is not None and type(dep[f]) is not bool for f in ("depends_on_record", "independent_failure")):
                raise ValueError("audit dependent flags must be boolean or null")
        if row.get("limitation") is not None and not isinstance(row["limitation"], str):
            raise ValueError("audit limitation must be text or null")
        refs = row.get("evidence", [])
        if not isinstance(refs, list):
            raise ValueError("audit evidence must be a list")
        normalized = []
        field = "sha256" if stored else "attachment"
        for ref in refs:
            if (not isinstance(ref, dict) or set(ref) - {"claim", field, "locator", "basis"}
                    or not isinstance(ref.get("claim"), str) or ref["claim"] not in CLAIMS):
                raise ValueError("invalid audit evidence claim/fields")
            name = ref.get(field)
            if name is not None and (not isinstance(name, str) or name not in attachments):
                raise ValueError("audit evidence must belong to the current entry attachments")
            if any(ref.get(f) is not None and not isinstance(ref[f], str) for f in ("locator", "basis")):
                raise ValueError("audit evidence locator/basis must be text or null")
            normalized.append({"claim": ref["claim"], "sha256": attachments.get(name),
                               "locator": ref.get("locator"), "basis": ref.get("basis")})
        result.append({"record_id": item, "case": case, "observations": deepcopy(obs),
                       "evidence": normalized, "limitation": row.get("limitation")})
    return {"contract": CONTRACT, "records": result}
