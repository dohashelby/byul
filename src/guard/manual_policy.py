"""Two manual policy evidence contracts; no engine, store, or I/O dependencies."""
from __future__ import annotations

from copy import deepcopy

OBSERVATION = "declared_policy_evidence"
CONTRACT = "guard-policy-evidence/1"
REPORT_PREFIX = "POLICY_ITEMS:"
PARAMETERS = {"SUP-14": "required_checks", "LIF-07": "required_readiness_items"}
CASES = {"SUP-14": ("normal", "failed", "error", "unsupported", "skipped"),
         "LIF-07": ("not_ready", "ready")}
COMMON = {"trial_id": None, "target_scope": None}
FIELDS = {
    "SUP-14": {**COMMON,
        "check_outcome": ("succeeded", "failed", "error", "unsupported", "skipped", "unknown"),
        "gate_outcome": ("advanced", "blocked", "unknown"),
        "exception_assessment": ("none", "reported", "unknown"), "transition_detail": None},
    "LIF-07": {**COMMON,
        "item_state": ("ready", "not_ready", "unknown"),
        "request_outcome": ("accepted", "blocked", "not_attempted", "unknown"),
        "inference_outcome": ("succeeded", "failed", "not_attempted", "unknown"),
        "order_basis": ("same_clock", "independent_order", "unknown"), "order_detail": None},
}


def parameter(check) -> str | None:
    key = PARAMETERS.get(check.id)
    return key if any(p.key == key and p.required and p.type == "list[str]" for p in check.policy_params) else None


def policy_items(values: dict, key: str) -> list[str] | None:
    items = values.get(key)
    if (not isinstance(items, list) or not items
            or any(not isinstance(i, str) or not i.strip() or i != i.strip() for i in items)
            or len(set(items)) != len(items)):
        return None
    return items


def blank_evidence(check, values: dict) -> dict:
    key = parameter(check)
    return {"parameter": key, "records": [
        {"item_id": item, "case": case, "observations": dict.fromkeys(FIELDS[check.id]),
         "evidence": [], "limitation": None}
        for item in policy_items(values, key) or [] for case in CASES[check.id]]}


def normalize(check, values: dict, value, attachments: dict[str, str | None], *, stored: bool = False) -> dict | None:
    """Validate partial data; replace form attachment identifiers with their hashes.

    Null/missing observations and empty evidence are incomplete, not successful.
    At pre-read validation attachments maps names to None; after all reads it maps
    names to hashes. Stored validation instead maps EVD artifact hashes to themselves.
    """
    if value is None:
        return None
    key = parameter(check)
    if key is None:
        raise ValueError("policy_evidence is unsupported for this definition")
    if not isinstance(value, dict) or set(value) - {"parameter", "records", *({"version"} if stored else set())}:
        raise ValueError("policy_evidence must contain only parameter and records")
    if stored and value.get("version") != CONTRACT:
        raise ValueError("unsupported policy evidence contract")
    if value.get("parameter") != key:
        raise ValueError("policy_evidence parameter does not match the CheckDef")
    records = value.get("records", [])
    if not isinstance(records, list):
        raise ValueError("policy_evidence records must be a list")
    given = values.get(key, [])
    known = given if isinstance(given, list) else []
    result, seen = [], set()
    for row in records:
        if not isinstance(row, dict) or set(row) - {"item_id", "case", "observations", "evidence", "limitation"}:
            raise ValueError("policy_evidence record fields are invalid")
        item, case = row.get("item_id"), row.get("case")
        if not isinstance(item, str) or item not in known:
            raise ValueError("policy_evidence item is not in the pinned policy")
        if not isinstance(case, str) or case not in CASES[check.id]:
            raise ValueError("policy_evidence case is invalid")
        if (item, case) in seen:
            raise ValueError("duplicate policy_evidence item/case")
        seen.add((item, case))
        obs = row.get("observations")
        obs = {} if obs is None else obs
        if not isinstance(obs, dict) or set(obs) - FIELDS[check.id].keys():
            raise ValueError("policy_evidence observation fields are invalid")
        for field, choices in FIELDS[check.id].items():
            v = obs.get(field)
            if v is not None and (not isinstance(v, str) or choices is not None and v not in choices):
                raise ValueError(f"policy_evidence observation {field} has an invalid type/value")
        limitation = row.get("limitation")
        if limitation is not None and not isinstance(limitation, str):
            raise ValueError("policy_evidence limitation must be text or null")
        refs = row.get("evidence", [])
        if not isinstance(refs, list):
            raise ValueError("policy_evidence evidence must be a list")
        normalized_refs = []
        field = "sha256" if stored else "attachment"
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) - {field, "locator", "basis"}:
                raise ValueError("policy_evidence reference fields are invalid")
            name = ref.get(field)
            if name is not None and (not isinstance(name, str) or name not in attachments):
                raise ValueError("policy_evidence reference must belong to this entry's attachments")
            for label in ("locator", "basis"):
                if ref.get(label) is not None and not isinstance(ref[label], str):
                    raise ValueError(f"policy_evidence {label} must be text or null")
            normalized_refs.append({"sha256": attachments.get(name), "locator": ref.get("locator"), "basis": ref.get("basis")})
        result.append({"item_id": item, "case": case, "observations": deepcopy(obs),
                       "evidence": normalized_refs, "limitation": limitation})
    return {"version": CONTRACT, "parameter": key, "records": result}
