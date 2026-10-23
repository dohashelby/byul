"""Pure policy and DECLARED ceilings for bounded attribution/log profiles.

The notes, fields and attachment are EXAMPLE declarations. Passing these rules
does not authenticate a human or replace an actual engine observation.
"""
from pathlib import Path

import pytest
import yaml

from guard.automation_policy import validate_log_integrity_policy, validate_request_binding_policy
from guard.judges.declared import DeclaredJudge
from guard.models import CheckDef, Verdict
from tests.builders import T0, evidence


ROOT = Path(__file__).resolve().parents[1]
CASES = ["json:generated", "json:distinct_body", "json:collision_body", "json:collision_header",
         "sse:generated", "sse:distinct_body", "sse:collision_body", "sse:collision_header"]
TRIALS = ["before", "multiline", "cr", "crlf", "reserved", "html", "after"]


def values(check_id):
    if check_id == "REQ-11":
        return {"result_binding": "originating_connection", "client_request_id_trust": "untrusted_correlation"}
    return {"collector_profile": "guard-vllm-native-info-v1", "required_trusted_fields": ["process", "level", "source"],
            "client_request_id_trust": "untrusted_correlation"}


def validator(check_id):
    return validate_request_binding_policy if check_id == "REQ-11" else validate_log_integrity_policy


def sample(check_id, raw="PASS"):
    definition = yaml.safe_load((ROOT / f"packs/PACK-BASELINE/checks/{check_id}.yaml").read_text(encoding="utf-8"))
    definition.update(yaml.safe_load((ROOT / f"tools/overrides/{check_id}.yaml").read_text(encoding="utf-8")))
    check = CheckDef.model_validate(definition)
    if check_id == "REQ-11":
        observations = {"profile_supported": True, "source_contract_verified": True,
            "template_identity_verified": True, "connection_binding_verified": True, "connection_overlap_verified": True,
            "tested_result_binding": "originating_connection", "tested_client_request_id_trust": "untrusted_correlation",
            "cases_planned": list(CASES), "cases_attempted": list(CASES), "cases_verified": list(CASES),
            "security_violations": [], "collection_complete": True}
    else:
        observations = {"log_collector_profile": "guard-vllm-native-info-v1",
            "log_client_request_id_trust": "untrusted_correlation", "log_trusted_fields": ["process", "level", "source"],
            "log_required_trusted_fields": ["process", "level", "source"],
            "log_identity_verified": True, "log_collection_complete": True, "log_boundaries_preserved": True,
            "log_trusted_fields_preserved": True, "log_display_verified": True, "log_trials_planned": list(TRIALS),
            "log_trials_reached": list(TRIALS), "log_security_violations": [], "log_coverage_gaps": []}
    observations.update(normal_controls=True, functional_probes_planned=["before", "after"],
        functional_probe_results={"before": "pass", "after": "pass"},
        declared_verdicts=dict.fromkeys(("security", "functional", "performance"), raw),
        declared_observation_notes={key: "EXAMPLE supplied observation note" for key in check.required_observations_prose})
    evd = evidence(check_id=check_id, observations={key: {"value": value, "collected": True}
                   for key, value in observations.items()}, artifacts=[
                       {"name": "EXAMPLE", "path": "unit-only", "sha256": "0" * 64,
                        "bytes": 0, "collected_at": T0, "collector": "EXAMPLE"}])
    return check, evd


@pytest.mark.parametrize("check_id", ["REQ-11", "OBS-09"])
def test_valid_policy_shapes_do_not_claim_support_for_other_enterprise_contexts(check_id):
    vals = values(check_id)
    if check_id == "REQ-11":
        vals["result_binding"] = "tenant_principal"
    else:
        vals["collector_profile"] = "enterprise-approved-siem"
        vals["required_trusted_fields"] = ["principal", "actor", "status"]
    assert validator(check_id)(vals) is None
    check, evd = sample(check_id)
    out = DeclaredJudge().judge(check, evd, {}, vals)
    assert out.verdicts == dict.fromkeys(("security", "functional", "performance"), Verdict.UNKNOWN)
    assert any("AUTOMATION_MANUAL_POLICY_UNRESOLVED" in reason for reason in out.reasons)


@pytest.mark.parametrize("check_id,key,bad", [
    ("REQ-11", "result_binding", None), ("REQ-11", "result_binding", ""),
    ("REQ-11", "result_binding", " \n"), ("REQ-11", "result_binding", True),
    ("REQ-11", "client_request_id_trust", None), ("REQ-11", "client_request_id_trust", []),
    ("REQ-11", "client_request_id_trust", 1), ("REQ-11", "client_request_id_trust", ""),
    ("OBS-09", "collector_profile", None), ("OBS-09", "collector_profile", " \t"),
    ("OBS-09", "collector_profile", 1), ("OBS-09", "client_request_id_trust", None),
    ("OBS-09", "client_request_id_trust", True), ("OBS-09", "client_request_id_trust", ""),
    ("OBS-09", "required_trusted_fields", []), ("OBS-09", "required_trusted_fields", ["level", "level"]),
    ("OBS-09", "required_trusted_fields", [True]), ("OBS-09", "required_trusted_fields", [""]),
    ("OBS-09", "required_trusted_fields", "level"), ("OBS-09", "required_trusted_fields", None),
])
def test_malformed_policy_is_not_coerced_or_allowed_to_grant_declared_pass(check_id, key, bad):
    vals = {**values(check_id), key: bad}
    with pytest.raises(ValueError):
        validator(check_id)(vals)
    check, evd = sample(check_id)
    assert DeclaredJudge().judge(check, evd, {}, vals).verdicts == dict.fromkeys(
        ("security", "functional", "performance"), Verdict.UNKNOWN)


@pytest.mark.parametrize("check_id", ["REQ-11", "OBS-09"])
def test_policy_requires_every_explicit_field_and_has_no_implicit_defaults(check_id):
    original = values(check_id)
    for missing in original:
        vals = {key: value for key, value in original.items() if key != missing}
        with pytest.raises(ValueError):
            validator(check_id)(vals)
        check, evd = sample(check_id)
        assert DeclaredJudge().judge(check, evd, {}, vals).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("check_id", ["REQ-11", "OBS-09"])
def test_complete_pinned_evidence_contract_is_required_and_performance_remains_unknown(check_id):
    check, evd = sample(check_id)
    assert DeclaredJudge().judge(check, evd, {}, values(check_id)).verdicts == {
        "security": Verdict.PASS, "functional": Verdict.PASS, "performance": Verdict.UNKNOWN}
    assert evd.data_origin.value == "EXAMPLE"


@pytest.mark.parametrize("check_id,key", [
    ("REQ-11", "profile_supported"), ("REQ-11", "source_contract_verified"),
    ("REQ-11", "template_identity_verified"), ("REQ-11", "connection_binding_verified"),
    ("REQ-11", "connection_overlap_verified"), ("REQ-11", "collection_complete"),
    ("REQ-11", "normal_controls"), ("OBS-09", "log_identity_verified"),
    ("OBS-09", "log_collection_complete"), ("OBS-09", "log_boundaries_preserved"),
    ("OBS-09", "log_trusted_fields_preserved"), ("OBS-09", "log_display_verified"),
    ("OBS-09", "normal_controls"),
])
def test_missing_or_contradictory_required_observation_withholds_declared_security_pass(check_id, key):
    check, evd = sample(check_id)
    for change in ({"collected": False, "value": None}, {"value": False}):
        changed = evd.model_copy(deep=True)
        changed.observations[key] = changed.observations[key].model_copy(update=change)
        assert DeclaredJudge().judge(check, changed.seal(), {}, values(check_id)).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("check_id,key,incomplete", [
    ("REQ-11", "cases_attempted", CASES[:-1]), ("REQ-11", "cases_verified", CASES[:-1]),
    ("REQ-11", "cases_planned", []), ("OBS-09", "log_trials_reached", TRIALS[:-1]),
    ("OBS-09", "log_trials_planned", []), ("OBS-09", "log_trusted_fields", ["level"]),
])
def test_self_reported_completion_flag_cannot_replace_required_case_or_field_coverage(check_id, key, incomplete):
    check, evd = sample(check_id)
    changed = evd.model_copy(deep=True)
    changed.observations[key] = changed.observations[key].model_copy(update={"value": incomplete})
    assert DeclaredJudge().judge(check, changed.seal(), {}, values(check_id)).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("check_id,policy_change", [
    ("REQ-11", {"client_request_id_trust": "trusted_authority"}),
    ("OBS-09", {"client_request_id_trust": "trusted_authority"}),
    ("OBS-09", {"required_trusted_fields": ["process", "level", "source", "actor"]}),
])
def test_existing_evidence_cannot_authorize_changed_identity_or_expanded_trusted_fields(check_id, policy_change):
    check, evd = sample(check_id)
    assert DeclaredJudge().judge(check, evd, {}, {**values(check_id), **policy_change}).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("check_id", ["REQ-11", "OBS-09"])
def test_confirmed_violation_cannot_be_overridden_by_manual_pass(check_id):
    check, evd = sample(check_id)
    key = "security_violations" if check_id == "REQ-11" else "log_security_violations"
    changed = evd.model_copy(deep=True)
    changed.observations[key] = changed.observations[key].model_copy(update={"value": ["EXAMPLE confirmed violation"]})
    assert DeclaredJudge().judge(check, changed.seal(), {}, values(check_id)).verdicts["security"] == Verdict.UNKNOWN


def test_explicit_log_coverage_gap_cannot_be_overridden_by_completion_flags():
    check, evd = sample("OBS-09")
    changed = evd.model_copy(deep=True)
    changed.observations["log_coverage_gaps"] = changed.observations["log_coverage_gaps"].model_copy(
        update={"value": ["EXAMPLE parser did not consume one required native event"]})
    assert DeclaredJudge().judge(check, changed.seal(), {}, values("OBS-09")).verdicts["security"] == Verdict.UNKNOWN


def test_explicit_supported_trusted_field_subset_is_not_silently_expanded():
    check, evd = sample("OBS-09")
    vals = {**values("OBS-09"), "required_trusted_fields": ["level"]}
    declared_subset = evd.model_copy(deep=True)
    declared_subset.observations["log_required_trusted_fields"] = declared_subset.observations[
        "log_required_trusted_fields"].model_copy(update={"value": ["level"]})
    assert validate_log_integrity_policy(vals) is None
    assert DeclaredJudge().judge(check, declared_subset.seal(), {}, vals).verdicts["security"] == Verdict.PASS
    assert DeclaredJudge().judge(check, evd, {}, vals).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("check_id", ["REQ-11", "OBS-09"])
def test_declared_pass_still_needs_an_attachment_and_each_prose_observation(check_id):
    check, evd = sample(check_id)
    unattached = evd.model_copy(update={"artifacts": []}).seal()
    assert DeclaredJudge().judge(check, unattached, {}, values(check_id)).verdicts["security"] == Verdict.UNKNOWN
    if check.required_observations_prose:
        changed = evd.model_copy(deep=True)
        changed.observations["declared_observation_notes"] = changed.observations["declared_observation_notes"].model_copy(
            update={"value": {}})
        assert DeclaredJudge().judge(check, changed.seal(), {}, values(check_id)).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("check_id", ["REQ-11", "OBS-09"])
@pytest.mark.parametrize("raw", ["FAIL", "UNKNOWN"])
def test_raw_non_pass_is_preserved_under_invalid_or_unsupported_policy(check_id, raw):
    check, evd = sample(check_id, raw)
    for vals in ({}, {**values(check_id), "client_request_id_trust": "unsupported"}):
        assert DeclaredJudge().judge(check, evd, {}, vals).verdicts == dict.fromkeys(
            ("security", "functional", "performance"), Verdict(raw))


@pytest.mark.parametrize("check_id", ["REQ-11", "OBS-09"])
def test_legacy_rev1_definition_does_not_gain_a_new_automation_ceiling(check_id):
    check, evd = sample(check_id)
    old = check.model_copy(update={"revision": 1, "executor": None, "judge": None,
                                   "policy_params": [], "required_observations": []})
    assert DeclaredJudge().judge(old, evd, {}, {}).verdicts == dict.fromkeys(
        ("security", "functional", "performance"), Verdict.PASS)
