"""선정 범위와 실제 사건의 연결. 관련 사건 수는 보안 판정과 별도로 제공한다."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .checklist_data import CHECKS, FIXED_SHA, MAPPING_VERSION, PACK_REVISION

_IDS = frozenset(row["framework_id"] for row in CHECKS)
_INTERNAL = {row["framework_id"]: row["update"]["id"] for row in CHECKS}
_SHA = re.compile(r"^[0-9a-f]{64}$")


def checks_for_record(record: dict[str, Any], source: str) -> list[str]:
    decision = record.get("decision")
    rule = decision.get("rule_id") if source == "gateway" and isinstance(decision, dict) else record.get("rule_id")
    rule_source = "tetragon" if isinstance(rule, str) and rule.startswith("TG-") else "gateway"
    checks = {row["framework_id"] for row in CHECKS
              if source == rule_source and isinstance(rule, str) and rule in row["rules"]}
    if source in {"gateway", "management_listener"}:
        checks.add("CHK-OBS-001")
    if source == "hubble":
        checks.add("CHK-PLT-007")
    # Source-declared mappings cannot enlarge the selected scope. They remain
    # related evidence only; no verdict is inferred from an event or its grade.
    declared = record.get("related_checks")
    if isinstance(declared, list):
        checks.update(x for x in declared if isinstance(x, str) and x in _IDS)
        checks.update(fid for fid, internal in _INTERNAL.items() if internal in declared)
    return sorted(_INTERNAL[ident] for ident in checks)


def evidence_refs(record: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for value in record.get("evidence_refs", []) if isinstance(record.get("evidence_refs"), list) else []:
        if not isinstance(value, dict):
            continue
        ident, sha = value.get("evidence_id"), value.get("sha256")
        if not isinstance(ident, str) or not 1 <= len(ident) <= 128:
            continue
        if sha is not None and (not isinstance(sha, str) or not _SHA.fullmatch(sha)):
            continue
        ref = {"evidence_id": ident}
        if sha is not None:
            ref["sha256"] = sha
        if ref not in result:
            result.append(ref)
    return result


def coverage(events: list[dict[str, Any]]) -> dict[str, Any]:
    rows = deepcopy(CHECKS)
    for row in rows:
        related = [e for e in events if row["update"]["id"] in e.get("related_checks", [])]
        row["observed_events"] = len(related)
        row["evidence_refs"] = evidence_refs({"evidence_refs": [ref for e in related for ref in e.get("evidence_refs", [])]})
        row["observation_state"] = "observed" if related else "unobserved"
    return {"mapping_version": MAPPING_VERSION, "fixed_sha": FIXED_SHA, "pack_revision": PACK_REVISION,
            "selected_count": 37, "excluded_count": 40, "items": rows}
