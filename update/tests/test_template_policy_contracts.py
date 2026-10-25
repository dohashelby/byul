"""REQ-07 declared policy/evidence contracts; fixtures are EXAMPLE only."""
import pytest

from guard.automation_policy import validate_template_policy
from guard.judges.declared import DeclaredJudge
from guard.models import Verdict
from tests.builders import T0, baseline_pack, evidence


SHA = "a" * 64


def values():
    return {"allowed_template_sha256": [SHA], "max_rendered_input_tokens": 127,
            "request_template_override_allowed": False}


def sample(raw="PASS"):
    check = baseline_pack().checks["REQ-07"]
    observations = {
        "selected_template_sha256": SHA, "template_identity_verified": True,
        "rendered_prompt_tokens_exact_match": True,
        "rendered_input_token_counts": {"before": 8, "boundary": 127, "overbound": 128, "after": 8},
        "max_consumed_prompt_tokens": 127,
        "tested_rendered_input_limit": 127,
        "enforcement_observed": True, "request_override_rejected": True,
        "security_violations": [], "collection_complete": True, "normal_controls": True,
        "functional_probes_planned": ["before", "boundary", "after"],
        "functional_probe_results": {"before": "pass", "boundary": "pass", "after": "pass"},
        "declared_verdicts": dict.fromkeys(("security", "functional", "performance"), raw),
        "declared_observation_notes": {key: "EXAMPLE supplied note" for key in check.required_observations_prose},
    }
    evd = evidence(check_id="REQ-07", observations={key: {"value": val, "collected": True}
                   for key, val in observations.items()}, artifacts=[
                       {"name": "EXAMPLE", "path": "unit-only", "sha256": "0" * 64,
                        "bytes": 0, "collected_at": T0, "collector": "EXAMPLE"}])
    return check, evd


def test_policy_constraints_do_not_invent_an_enterprise_limit_or_approval():
    vals = values()
    vals["max_rendered_input_tokens"] = 8192
    vals["request_template_override_allowed"] = True
    assert validate_template_policy(vals) is None


@pytest.mark.parametrize("key,bad", [
    ("allowed_template_sha256", []), ("allowed_template_sha256", [SHA, SHA]),
    ("allowed_template_sha256", ["A" * 64]), ("allowed_template_sha256", ["z" * 64]),
    ("allowed_template_sha256", [SHA[:-1]]), ("allowed_template_sha256", [True]),
    ("allowed_template_sha256", SHA), ("allowed_template_sha256", None),
    ("max_rendered_input_tokens", True), ("max_rendered_input_tokens", 0),
    ("max_rendered_input_tokens", -1), ("max_rendered_input_tokens", "127"),
    ("max_rendered_input_tokens", 127.0), ("max_rendered_input_tokens", None),
    ("request_template_override_allowed", 0), ("request_template_override_allowed", 1),
    ("request_template_override_allowed", "false"), ("request_template_override_allowed", None),
])
def test_invalid_policy_is_not_coerced_and_cannot_grant_declared_pass(key, bad):
    vals = values()
    vals[key] = bad
    with pytest.raises(ValueError):
        validate_template_policy(vals)
    check, evd = sample()
    out = DeclaredJudge().judge(check, evd, {}, vals)
    assert out.verdicts == dict.fromkeys(("security", "functional", "performance"), Verdict.UNKNOWN)
    assert any("AUTOMATION_MANUAL_POLICY_UNRESOLVED" in reason for reason in out.reasons)


def test_declared_pass_requires_pinned_rules_and_never_invents_performance():
    check, evd = sample()
    out = DeclaredJudge().judge(check, evd, {}, values())
    assert out.verdicts == {"security": Verdict.PASS, "functional": Verdict.PASS, "performance": Verdict.UNKNOWN}
    assert evd.data_origin.value == "EXAMPLE"


@pytest.mark.parametrize("key", ["template_identity_verified", "rendered_prompt_tokens_exact_match",
    "enforcement_observed", "request_override_rejected", "collection_complete", "normal_controls"])
def test_missing_or_contradictory_template_evidence_cannot_claim_security_pass(key):
    check, evd = sample()
    changed = evd.model_copy(deep=True)
    changed.observations[key] = changed.observations[key].model_copy(update={"value": False})
    out = DeclaredJudge().judge(check, changed.seal(), {}, values())
    assert out.verdicts["security"] == Verdict.UNKNOWN


def test_unsupported_override_allowed_context_withholds_self_reported_pass():
    check, evd = sample()
    vals = values()
    vals["request_template_override_allowed"] = True
    assert DeclaredJudge().judge(check, evd, {}, vals).verdicts["security"] == Verdict.UNKNOWN


def test_stored_consumption_above_a_lower_policy_cannot_claim_declared_pass():
    check, evd = sample()
    vals = {**values(), "max_rendered_input_tokens": 1}
    out = DeclaredJudge().judge(check, evd, {}, vals)
    assert out.verdicts["security"] == Verdict.UNKNOWN
    assert any("OBSERVATION_CONFLICT" in reason for reason in out.reasons)


def test_old_boundary_proof_cannot_authorize_a_new_larger_test_scope():
    check, evd = sample()
    vals = {**values(), "max_rendered_input_tokens": 200}
    assert DeclaredJudge().judge(check, evd, {}, vals).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("count", [0, -1])
def test_nonpositive_consumption_cannot_grant_manual_security_pass(count):
    check, evd = sample()
    changed = evd.model_copy(deep=True)
    changed.observations["max_consumed_prompt_tokens"] = changed.observations[
        "max_consumed_prompt_tokens"].model_copy(update={"value": count})
    assert DeclaredJudge().judge(check, changed.seal(), {}, values()).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("raw", ["FAIL", "UNKNOWN"])
def test_bad_or_unsupported_policy_never_erases_raw_non_pass(raw):
    check, evd = sample(raw)
    for bad in ({**values(), "allowed_template_sha256": []},
                {**values(), "request_template_override_allowed": True}):
        assert DeclaredJudge().judge(check, evd, {}, bad).verdicts == dict.fromkeys(
            ("security", "functional", "performance"), Verdict(raw))


def test_legacy_definition_does_not_gain_the_new_profile_retroactively():
    check, evd = sample()
    old = check.model_copy(update={"revision": 1, "executor": None, "judge": None, "policy_params": []})
    out = DeclaredJudge().judge(old, evd, {}, {})
    assert out.verdicts == dict.fromkeys(("security", "functional", "performance"), Verdict.PASS)
