"""REQ-15 limited DECLARED group isolation contract; pure, no I/O or authentication."""
from __future__ import annotations

import re

from guard.models import Verdict
from .base import AXES, JudgeOutput

POLICIES = {"public_prefix_sharing_allowed": "bool", "isolated_cache_groups": "list[str]",
            "cache_identity_policy_ref": "str"}
OBSERVATION = "declared_cache_group_evidence"
VERSION = "guard-cache-group-evidence/1"
PROFILE = "private_groups_server_overwrite"
CASES = ("source_warmup", "same_group_reuse", "cross_group_probe", "forged_salt", "omitted_salt", "post_control")
TOP_FIELDS = frozenset(("version", "profile", "identity_policy_ref", "entrypoint_ref", "target_scope",
                        "workload_ref", "classification", "identities", "records"))
IDENTITY_FIELDS = frozenset(("group_id", "principal_ref", "credential_kind", "identity_source",
                            "verification_status", "binding_basis", "evidence"))
ROW_FIELDS = frozenset(("source_group_id", "target_group_id", "case", "trial_id", "event_id",
    "actor_principal_ref", "client_group_claim", "salt_action", "derived_group_id", "consumer_ref",
    "key_relation", "cache_effect", "consumed_group_id", "source_entry_ref", "consumed_entry_ref",
    "request_outcome", "inference", "observation_detail", "evidence"))
IDENTITY_ENUMS = {
    "credential_kind": frozenset(("distinct_authenticated_credential", "shared_service_credential", "client_salt_only", "unknown")),
    "verification_status": frozenset(("verified", "unknown")),
}
ENUMS = {
    "case": frozenset(CASES),
    "client_group_claim": frozenset(("absent", "own_group", "source_group", "unknown")),
    "salt_action": frozenset(("trusted_injected", "trusted_overwritten", "client_controlled", "unknown")),
    "key_relation": frozenset(("same", "different", "unknown")),
    "cache_effect": frozenset(("warmed", "self_group_hit", "cross_group_isolated", "forbidden_cross_group_reuse", "unknown")),
    "request_outcome": frozenset(("accepted", "blocked", "unknown")),
    "inference": frozenset(("succeeded", "failed", "not_attempted", "unknown")),
}


def contract_state(check) -> str:
    """Old definitions remain legacy; any incomplete new marker is a blocked contract."""
    if check.id != "REQ-15":
        return "legacy"
    markers = {*POLICIES, OBSERVATION}
    if not any(p.key in markers for p in [*check.policy_params, *check.required_observations]):
        return "legacy"
    params = [p for p in check.policy_params if p.key in markers]
    obs = [p for p in check.required_observations if p.key in markers]
    complete = (len(params) == len(POLICIES) and {p.key for p in params} == set(POLICIES)
                and all(p.required and p.type == POLICIES[p.key] for p in params)
                and len(obs) == 1 and obs[0].key == OBSERVATION and obs[0].type == "dict")
    return "complete" if complete else "partial"


def _text(value) -> bool:
    return type(value) is str and bool(value.strip()) and value == value.strip() and value != "unknown"


class _Invalid(ValueError):
    """Only constant codes; never echo self-reported payload or dependency exceptions."""


def _require(condition, code):
    if not condition:
        raise _Invalid(code)


def _enums(row, enums):
    _require(all(type(row[k]) is str and bool(row[k].strip()) and row[k] == row[k].strip()
                 for k in enums), "RECORD_INCOMPLETE")
    _require(all(row[k] in choices for k, choices in enums.items()), "ENUM_INVALID")


def _links(refs, artifacts):
    _require(type(refs) is list and bool(refs), "ATTACHMENT_LINK_MISSING")
    seen = set()
    for ref in refs:
        _require(type(ref) is dict and set(ref) == {"sha256", "locator", "basis"}, "ATTACHMENT_LINK_INVALID")
        _require(all(_text(ref[k]) for k in ref), "ATTACHMENT_LINK_INCOMPLETE")
        _require(re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]) is not None
                 and ref["sha256"] in artifacts, "ATTACHMENT_NOT_IN_ENTRY")
        key = (ref["sha256"], ref["locator"], ref["basis"])
        _require(key not in seen, "ATTACHMENT_LINK_DUPLICATE")
        seen.add(key)


def _records(payload, groups, identity_ref, artifacts):
    _require(type(payload) is dict and set(payload) == TOP_FIELDS, "EVIDENCE_INVALID")
    _require(payload["version"] == VERSION, "VERSION_UNSUPPORTED")
    _require(payload["profile"] == PROFILE, "PROFILE_UNSUPPORTED")
    _require(all(_text(payload[k]) for k in ("identity_policy_ref", "entrypoint_ref", "target_scope", "workload_ref")),
             "SCOPE_OR_IDENTITY_UNRESOLVED")
    _require(payload["identity_policy_ref"] == identity_ref, "IDENTITY_POLICY_MISMATCH")
    _enums(payload, {"classification": frozenset(("private", "unknown"))})
    _require(payload["classification"] == "private", "CLASSIFICATION_UNRESOLVED")
    identities = payload["identities"]
    _require(type(identities) is list and bool(identities), "IDENTITIES_MISSING")
    principals, principal_refs = {}, set()
    for identity in identities:
        _require(type(identity) is dict and set(identity) == IDENTITY_FIELDS, "IDENTITY_INVALID")
        _require(all(_text(identity[k]) for k in IDENTITY_FIELDS - {"evidence"} - IDENTITY_ENUMS.keys()),
                 "IDENTITY_INCOMPLETE")
        _enums(identity, IDENTITY_ENUMS)
        group = identity["group_id"]
        _require(group in groups and group not in principals, "IDENTITY_GROUP_MISMATCH")
        _require(identity["principal_ref"] not in principal_refs, "PRINCIPAL_REUSED")
        _links(identity["evidence"], artifacts)
        _require(identity["credential_kind"] == "distinct_authenticated_credential", "CREDENTIAL_PROFILE_UNSUPPORTED")
        _require(identity["verification_status"] == "verified", "IDENTITY_UNVERIFIED")
        principals[group] = identity["principal_ref"]
        principal_refs.add(identity["principal_ref"])
    _require(set(principals) == groups, "IDENTITY_COVERAGE_INCOMPLETE")
    records = payload["records"]
    _require(type(records) is list and bool(records), "RECORDS_MISSING")
    rows, events, consumers = {}, set(), set()
    for row in records:
        _require(type(row) is dict and set(row) == ROW_FIELDS, "RECORD_INVALID")
        _require(all(_text(row[k]) for k in ROW_FIELDS - {"evidence"} - ENUMS.keys()), "RECORD_INCOMPLETE")
        _enums(row, ENUMS)
        source, target = row["source_group_id"], row["target_group_id"]
        _require(source in groups and target in groups and source != target, "FOREIGN_OR_SAME_GROUP_PAIR")
        key = (source, target, row["case"])
        _require(key not in rows, "CASE_DUPLICATE")
        _require(row["event_id"] not in events, "EVENT_REUSED")
        _require(row["actor_principal_ref"] in principal_refs, "FOREIGN_PRINCIPAL")
        _require(row["derived_group_id"] in groups and row["consumed_group_id"] in groups, "FOREIGN_CONSUMED_GROUP")
        _links(row["evidence"], artifacts)
        events.add(row["event_id"])
        consumers.add(row["consumer_ref"])
        rows[key] = row
    _require(len(consumers) == 1, "CONSUMER_MISMATCH")
    # Each key already has two distinct listed groups, an allowed case and no
    # duplicate. Equality with the finite domain's size therefore proves full
    # coverage without materializing its quadratic set for incomplete evidence.
    _require(len(rows) == len(groups) * (len(groups) - 1) * len(CASES), "CASE_COVERAGE_INCOMPLETE")
    trials = set()
    for source in sorted(groups):
        for target in sorted(groups - {source}):
            pair = [rows[(source, target, case)] for case in CASES]
            _require(len({r["trial_id"] for r in pair}) == 1, "TRIAL_MISMATCH")
            _require(pair[0]["trial_id"] not in trials, "TRIAL_REUSED")
            trials.add(pair[0]["trial_id"])
            _require(len({r["source_entry_ref"] for r in pair}) == 1, "SOURCE_ENTRY_MISMATCH")
    return rows, principals


def evaluate(check, evidence, values) -> JudgeOutput | None:
    """PASS ceilings only; DECLARED records and hashes do not authenticate identities or claims."""
    state = contract_state(check)
    if state == "legacy":
        return None
    reasons = [
        "POLICY_CONTEXT: CACHE_GROUP_DECLARED_ONLY; private_groups_server_overwrite; one entrypoint, target and workload; "
        "one representative per declared group; identity policy, credentials, consumer, event order and attachment contents "
        "are self-reported, not authenticated or approved",
        "POLICY_CONTEXT: CACHE_GROUP_UNVERIFIED_SCOPE; public sharing, shared service credentials, safe rejection, "
        "other principals and entrypoints, REQ17 statistics and performance are unverified",
    ]
    ceilings = dict.fromkeys(AXES, Verdict.UNKNOWN)
    missing = []
    try:
        _require(state == "complete", "CONTRACT_INCOMPLETE")
        _require(all(k in values for k in POLICIES), "NO_POLICY_VALUE")
        flag, listed, identity_ref = (values[k] for k in POLICIES)
        _require(type(flag) is bool and type(listed) is list and all(type(g) is str for g in listed)
                 and type(identity_ref) is str, "POLICY_TYPE_INVALID")
        reasons.append(f"POLICY_CONTEXT: CACHE_GROUP_PUBLIC_PREFIX_SHARING_ALLOWED={'true' if flag else 'false'}; declared policy only")
        _require(flag is False, "PUBLIC_PROFILE_UNSUPPORTED")
        _require(len(listed) >= 2 and all(_text(g) for g in listed), "POLICY_GROUPS_INCOMPLETE")
        _require(len(set(listed)) == len(listed), "POLICY_GROUP_DUPLICATE")
        _require(_text(identity_ref), "IDENTITY_POLICY_UNRESOLVED")
        obs = evidence.observations.get(OBSERVATION)
        _require(obs is not None and obs.collected, "EVIDENCE_UNCOLLECTED")
        groups = set(listed)
        rows, principals = _records(obs.value, groups, identity_ref, {a.sha256 for a in evidence.artifacts})
        secure, functional = True, True
        for source in sorted(groups):
            for target in sorted(groups - {source}):
                target_entry = rows[(source, target, "cross_group_probe")]["consumed_entry_ref"]
                for case in CASES:
                    row = rows[(source, target, case)]
                    source_case = case in ("source_warmup", "same_group_reuse")
                    actor = source if source_case else target
                    expected_effect = ("warmed" if case == "source_warmup" else "self_group_hit"
                                       if case in ("same_group_reuse", "post_control") else "cross_group_isolated")
                    expected_entry = row["source_entry_ref"] if source_case else target_entry
                    consistent = (row["actor_principal_ref"] == principals[actor]
                        and row["derived_group_id"] == actor and row["consumed_group_id"] == actor
                        and row["consumed_entry_ref"] == expected_entry
                        and (source_case or target_entry != row["source_entry_ref"])
                        and row["client_group_claim"] == ("source_group" if case == "forged_salt" else "absent")
                        and row["salt_action"] == ("trusted_overwritten" if case == "forged_salt" else "trusted_injected")
                        and row["key_relation"] == ("same" if source_case else "different")
                        and row["cache_effect"] == expected_effect and row["request_outcome"] == "accepted")
                    secure &= consistent
                    functional &= consistent and row["inference"] == "succeeded"
                    if row["inference"] != "succeeded":
                        reasons.append("POLICY_CONTEXT: CACHE_GROUP_INFERENCE_UNRESOLVED; functional PASS withheld")
                    if row["cache_effect"] == "forbidden_cross_group_reuse":
                        reasons.append("POLICY_CONTEXT: CACHE_GROUP_CONTRADICTION; forbidden cross-group reuse declared")
        if secure:
            ceilings["security"] = Verdict.PASS
        else:
            reasons.append("POLICY_CONTEXT: CACHE_GROUP_SECURITY_UNRESOLVED; unsupported or conflicting declared behavior")
        if functional:
            ceilings["functional"] = Verdict.PASS
        else:
            reasons.append("POLICY_CONTEXT: CACHE_GROUP_FUNCTIONAL_UNRESOLVED")
        reasons.append("POLICY_CONTEXT: CACHE_GROUP_RECORDS_LINKED; DECLARED evidence only, not engine verification")
    except _Invalid as exc:
        reasons.append(f"POLICY_CONTEXT: CACHE_GROUP_{exc}; DECLARED PASS withheld")
        missing.append(OBSERVATION)
    except (TypeError, KeyError):
        reasons.append("POLICY_CONTEXT: CACHE_GROUP_EVIDENCE_INVALID; DECLARED PASS withheld")
        missing.append(OBSERVATION)
    return JudgeOutput(verdicts=ceilings, reasons=list(dict.fromkeys(reasons)), missing_observations=missing)
