"""REQ-16 bounded DECLARED cache context evidence; pure, no I/O or authentication."""
from __future__ import annotations

import re

from guard.models import Verdict
from .base import AXES, JudgeOutput

POLICY = "required_cache_contexts"
OBSERVATION = "declared_cache_context_evidence"
VERSION = "guard-cache-context-evidence/1"
PROFILE = "same_authenticated_group_private"
CONTEXTS = frozenset(("model_generation", "adapter_content", "media_identity"))
CASES = ("same_context", "changed_context", "post_control")
TOP_FIELDS = frozenset(("version", "profile", "sharing_group_ref", "identity_basis", "records"))
ROW_FIELDS = frozenset(("context_id", "case", "trial_id", "event_id", "target_scope", "workload_ref",
    "context_before", "context_after", "object_change", "other_conditions", "key_relation",
    "cache_effect", "inference", "observation_detail", "evidence"))
ENUMS = {
    "case": frozenset(CASES),
    "object_change": frozenset(("unchanged", "observed", "unknown")),
    "other_conditions": frozenset(("same", "changed", "unknown")),
    "key_relation": frozenset(("same", "different", "unknown")),
    "cache_effect": frozenset(("before_context_hit", "isolated_after_change", "after_context_hit",
                               "old_context_reused", "unknown")),
    "inference": frozenset(("succeeded", "failed", "not_attempted", "unknown")),
}


def contract_state(check) -> str:
    if check.id != "REQ-16":
        return "legacy"
    markers = (POLICY, OBSERVATION)
    if not any(p.key in markers for p in [*check.policy_params, *check.required_observations]):
        return "legacy"
    params = [p for p in check.policy_params if p.key in markers]
    obs = [p for p in check.required_observations if p.key in markers]
    complete = (len(params) == len(obs) == 1 and params[0].key == POLICY
                and params[0].type == "list[str]" and params[0].required
                and obs[0].key == OBSERVATION and obs[0].type == "dict")
    return "complete" if complete else "partial"


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip() and value != "unknown"


class _Invalid(ValueError):
    """Only internal constant reason codes; never user-provided exception text."""


def _require(condition, code):
    if not condition:
        raise _Invalid(code)


def _rows(payload, contexts, artifacts):
    """Validate only self-reported structure and attachment membership, never content truth."""
    _require(type(payload) is dict and set(payload) == TOP_FIELDS, "EVIDENCE_INVALID")
    _require(payload["version"] == VERSION, "VERSION_UNSUPPORTED")
    _require(payload["profile"] == PROFILE, "PROFILE_UNSUPPORTED")
    _require(_text(payload["sharing_group_ref"]) and _text(payload["identity_basis"]), "IDENTITY_BASIS_UNRESOLVED")
    records = payload["records"]
    _require(type(records) is list and bool(records), "RECORDS_MISSING")
    rows, events, bundle = {}, set(), set()
    for row in records:
        _require(type(row) is dict and set(row) == ROW_FIELDS, "RECORD_INVALID")
        _require(all(_text(row[k]) for k in ROW_FIELDS - {"evidence"}), "RECORD_INCOMPLETE")
        _require(all(row[k] in allowed for k, allowed in ENUMS.items()), "ENUM_INVALID")
        _require(row["context_id"] in contexts, "FOREIGN_CONTEXT")
        key = (row["context_id"], row["case"])
        _require(key not in rows, "CASE_DUPLICATE")
        _require(row["event_id"] not in events, "EVENT_REUSED")
        events.add(row["event_id"])
        bundle.add((row["trial_id"], row["target_scope"], row["workload_ref"]))
        refs = row["evidence"]
        _require(type(refs) is list and bool(refs), "ATTACHMENT_LINK_MISSING")
        seen = set()
        for ref in refs:
            _require(type(ref) is dict and set(ref) == {"sha256", "locator", "basis"}, "ATTACHMENT_LINK_INVALID")
            _require(all(_text(ref[k]) for k in ref), "ATTACHMENT_LINK_INCOMPLETE")
            _require(re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]) is not None
                     and ref["sha256"] in artifacts, "ATTACHMENT_NOT_IN_ENTRY")
            identity = (ref["sha256"], ref["locator"], ref["basis"])
            _require(identity not in seen, "ATTACHMENT_LINK_DUPLICATE")
            seen.add(identity)
        rows[key] = row
    _require(len(bundle) == 1, "TRIAL_TARGET_WORKLOAD_MISMATCH")
    _require(set(rows) == {(context, case) for context in contexts for case in CASES}, "CASE_COVERAGE_INCOMPLETE")
    return rows


def evaluate(check, evidence, values) -> JudgeOutput | None:
    """Return PASS ceilings only. None is legacy; invalid input cannot create FAIL."""
    state = contract_state(check)
    if state == "legacy":
        return None
    reasons = [
        "POLICY_CONTEXT: CACHE_CONTEXT_DECLARED_ONLY; same_authenticated_group_private; "
        "identity, consumer, object change, event order and attachment contents are self-reported, not authenticated; "
        "REQ15 public/cross-group protection and REQ17 statistics are unverified",
        "POLICY_CONTEXT: CACHE_CONTEXT_PERFORMANCE_UNVERIFIED",
    ]
    missing = []
    ceilings = dict.fromkeys(AXES, Verdict.UNKNOWN)
    try:
        _require(state == "complete", "CONTRACT_INCOMPLETE")
        _require(POLICY in values, "NO_POLICY_VALUE")
        contexts = values[POLICY]
        _require(type(contexts) is list and all(type(c) is str for c in contexts), "POLICY_TYPE_INVALID")
        _require(bool(contexts) and all(_text(c) for c in contexts), "POLICY_EMPTY_OR_BLANK")
        _require(len(set(contexts)) == len(contexts), "POLICY_DUPLICATE")
        _require(all(c in CONTEXTS for c in contexts), "POLICY_CONTEXT_UNSUPPORTED")
        obs = evidence.observations.get(OBSERVATION)
        _require(obs is not None and obs.collected, "EVIDENCE_UNCOLLECTED")
        rows = _rows(obs.value, contexts, {a.sha256 for a in evidence.artifacts})
        secure, functional = True, True
        for context in contexts:
            same, changed, post = (rows[(context, case)] for case in CASES)
            _require(same["context_before"] == same["context_after"]
                     and changed["context_before"] == same["context_before"]
                     and changed["context_before"] != changed["context_after"]
                     and (post["context_before"], post["context_after"]) ==
                         (changed["context_before"], changed["context_after"]), "CONTEXT_PAIR_MISMATCH")
            for row in (same, changed, post):
                is_same = row["case"] == "same_context"
                expected_effect = {"same_context": "before_context_hit", "changed_context": "isolated_after_change",
                                   "post_control": "after_context_hit"}[row["case"]]
                controls = (row["object_change"] == ("unchanged" if is_same else "observed")
                            and row["other_conditions"] == "same"
                            and row["key_relation"] == ("same" if is_same else "different"))
                secure &= controls and row["cache_effect"] == expected_effect
                functional &= controls and row["cache_effect"] == expected_effect and row["inference"] == "succeeded"
                if row["cache_effect"] == "old_context_reused":
                    reasons.append("POLICY_CONTEXT: CACHE_CONTEXT_CONTRADICTION; old context reuse declared")
        if secure:
            ceilings["security"] = Verdict.PASS
        else:
            reasons.append("POLICY_CONTEXT: CACHE_CONTEXT_SECURITY_UNRESOLVED")
        if functional:
            ceilings["functional"] = Verdict.PASS
        else:
            reasons.append("POLICY_CONTEXT: CACHE_CONTEXT_FUNCTIONAL_UNRESOLVED")
        reasons.append("POLICY_CONTEXT: CACHE_CONTEXT_RECORDS_LINKED; DECLARED evidence only, not engine verification")
    except _Invalid as exc:
        reasons.append(f"POLICY_CONTEXT: CACHE_CONTEXT_{exc}; DECLARED PASS withheld")
        missing.append(OBSERVATION)
    except (TypeError, KeyError):
        reasons.append("POLICY_CONTEXT: CACHE_CONTEXT_EVIDENCE_INVALID; DECLARED PASS withheld")
        missing.append(OBSERVATION)
    return JudgeOutput(verdicts=ceilings, reasons=reasons, missing_observations=missing)
