"""New automated contracts also cap self-reported manual PASS. All inputs are EXAMPLE."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from guard.judges.declared import DeclaredJudge
from guard.judges.rule_table import RuleTableJudge
from guard.models import CheckDef, Verdict
from tests.builders import T0, evidence


ROOT = Path(__file__).resolve().parents[1]
AXES = ("security", "functional", "performance")
CHECKS = ("REQ-10", "REQ-13", "OBS-03")


def fixture(cid, raw="PASS"):
    check = CheckDef.model_validate(yaml.safe_load((ROOT / f"packs/PACK-BASELINE/checks/{cid}.yaml").read_text(encoding="utf-8")))
    common = {"functional_probes_planned": ["before", "after"],
              "functional_probe_results": {"before": "pass", "after": "pass"}}
    if cid == "REQ-10":
        values = {"allowed_sampling_options": ["temperature", "top_p", "min_tokens"], "rejection_codes": ["400", "422"]}
        observations = {**common, "sampling_options_planned": values["allowed_sampling_options"],
                        "sampling_options_tested": values["allowed_sampling_options"],
                        "sampling_challenge_outcomes": {o: "400" for o in values["allowed_sampling_options"]},
                        "sampling_accepted_options": [], "sampling_health_results": {o: "200" for o in values["allowed_sampling_options"]},
                        "runner_consumption_verified": True}
    elif cid == "REQ-13":
        values = {"allowed_response_fields": ["id", "choices[].text"], "allowed_error_fields": ["error.message"],
                  "forbidden_return_options": ["return_prompt_text"], "required_targets": ["engine"]}
        observations = {**common, "response_coverage_complete": True, "response_coverage_gaps": [],
                        "response_cases_tested": ["normal", "error", "return_prompt_text"],
                        "response_fields_seen": ["id", "choices[].text"], "error_fields_seen": ["error.message"],
                        "response_options_returned": [], "response_options_tested": ["return_prompt_text"],
                        "response_targets_tested": ["engine"], "private_markers_returned": []}
    else:
        values = {"allowed_log_fields": ["request_id", "event", "exception"], "forbidden_log_fields": ["prompt", "credential"],
                  "forbidden_marker_contexts": ["caller_input", "error_input"], "required_sources": ["engine"],
                  "required_branches": ["normal", "error", "exception_dump"]}
        observations = {**common, "log_coverage_complete": True, "log_coverage_gaps": [],
                        "log_sources_tested": {"engine": ["normal", "error", "exception_dump"]},
                        "log_fields_seen": ["request_id", "event", "exception"], "log_marker_contexts_seen": [],
                        "log_source_ids_tested": ["engine"], "log_required_branches_tested": values["required_branches"]}
    observations.update(declared_verdicts=dict.fromkeys(AXES, raw),
                        declared_observation_notes={k: "EXAMPLE supplied observation note" for k in check.required_observations_prose})
    evd = evidence(check_id=cid, observations={k: {"value": v, "collected": True} for k, v in observations.items()},
                   artifacts=[{"name": "EXAMPLE attachment", "path": "unit-only", "sha256": "0" * 64,
                               "bytes": 0, "collected_at": T0, "collector": "EXAMPLE"}])
    return check, values, evd


@pytest.mark.parametrize("cid", CHECKS)
def test_pinned_normal_candidate_only_retains_axes_that_have_pass_rules(cid):
    check, values, evd = fixture(cid)
    result = DeclaredJudge().judge(check, evd, {}, values)
    assert result.verdicts == {"security": Verdict.PASS, "functional": Verdict.PASS, "performance": Verdict.UNKNOWN}
    assert any("AUTOMATION_MANUAL_RULE_UNRESOLVED" in r and "axis=performance" in r for r in result.reasons)
    assert evd.data_origin == "EXAMPLE"


@pytest.mark.parametrize("cid", CHECKS)
def test_absent_observations_cannot_be_replaced_by_manual_pass(cid):
    check, values, evd = fixture(cid)
    evd.observations = {k: v for k, v in evd.observations.items() if k.startswith("declared_")}
    result = DeclaredJudge().judge(check, evd, {}, values)
    assert all(result.verdicts[a] == Verdict.UNKNOWN for a in AXES)
    assert result.missing_observations


@pytest.mark.parametrize("cid", CHECKS)
def test_collected_but_incomplete_security_scope_is_not_manual_pass(cid):
    check, values, evd = fixture(cid)
    coverage = {"REQ-10": "runner_consumption_verified", "REQ-13": "response_coverage_complete", "OBS-03": "log_coverage_complete"}[cid]
    evd.observations[coverage].value = False
    result = DeclaredJudge().judge(check, evd, {}, values)
    assert result.verdicts["security"] == Verdict.UNKNOWN
    assert result.verdicts["functional"] == Verdict.PASS


@pytest.mark.parametrize("cid,key,value", [
    ("REQ-10", "sampling_challenge_outcomes", {"temperature": "200", "top_p": "400", "min_tokens": "400"}),
    ("REQ-13", "private_markers_returned", ["EXAMPLE_PRIVATE_MARKER"]),
    ("OBS-03", "log_fields_seen", ["prompt"]),
])
def test_contradicting_observations_withhold_pass_without_inventing_declared_fail(cid, key, value):
    check, values, evd = fixture(cid)
    evd.observations[key].value = value
    result = DeclaredJudge().judge(check, evd, {}, values)
    assert result.verdicts["security"] == Verdict.UNKNOWN
    assert result.verdicts["functional"] == Verdict.PASS
    assert any("AUTOMATION_MANUAL_" in r and "axis=security" in r for r in result.reasons)


@pytest.mark.parametrize("cid", CHECKS)
def test_functional_failure_withholds_only_that_declared_pass(cid):
    check, values, evd = fixture(cid)
    evd.observations["functional_probe_results"].value["after"] = "fail"
    result = DeclaredJudge().judge(check, evd, {}, values)
    assert result.verdicts["security"] == Verdict.PASS
    assert result.verdicts["functional"] == Verdict.UNKNOWN


@pytest.mark.parametrize("cid", CHECKS)
@pytest.mark.parametrize("raw", ["FAIL", "UNKNOWN"])
def test_independent_raw_fail_and_unknown_survive_missing_everything(cid, raw):
    check, _, evd = fixture(cid, raw)
    evd.observations = {"declared_verdicts": evd.observations["declared_verdicts"]}
    evd.artifacts = []
    result = DeclaredJudge().judge(check, evd, {}, {})
    assert all(result.verdicts[a] == raw for a in AXES)


@pytest.mark.parametrize("cid", CHECKS)
def test_missing_policy_values_withhold_manual_pass(cid):
    check, _, evd = fixture(cid)
    assert all(v == Verdict.UNKNOWN for v in DeclaredJudge().judge(check, evd, {}, {}).verdicts.values())


@pytest.mark.parametrize("cid", CHECKS)
@pytest.mark.parametrize("issue", ["empty_rules", "invalid_operator", "dependency_error"])
@pytest.mark.parametrize("raw", ["PASS", "FAIL", "UNKNOWN"])
def test_bad_rule_dependency_is_safe_and_does_not_erase_fail(cid, issue, raw, monkeypatch):
    check, values, evd = fixture(cid, raw)
    if issue == "empty_rules":
        check.judge.params = {}
    elif issue == "invalid_operator":
        check.judge.params["pass_when_all"][0]["op"] = "EXAMPLE_UNSUPPORTED"
    else:
        def error(*args, **kwargs):
            raise RuntimeError("sensitive dependency detail MUST NOT APPEAR")
        monkeypatch.setattr(RuleTableJudge, "judge", error)
    result = DeclaredJudge().judge(check, evd, {}, values)
    expected = "UNKNOWN" if raw == "PASS" else raw
    assert all(result.verdicts[a] == expected for a in AXES)
    assert "sensitive dependency detail" not in " ".join(result.reasons)


@pytest.mark.parametrize("cid", CHECKS)
def test_legacy_definition_without_new_executor_preserves_previous_manual_behavior(cid, monkeypatch):
    check, values, evd = fixture(cid)
    check.executor = None
    check.judge = None

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy must not invoke a new automatic contract")

    monkeypatch.setattr(RuleTableJudge, "judge", forbidden)
    assert all(v == Verdict.PASS for v in DeclaredJudge().judge(check, evd, {}, values).verdicts.values())


@pytest.mark.parametrize("cid", CHECKS)
def test_inputs_are_immutable_and_pinned_rules_ignore_supplied_override_params(cid):
    check, values, evd = fixture(cid)
    snapshots = deepcopy((check.model_dump(mode="json"), values, evd.to_dict()))
    params = {"pass_when_all": [], "axes": {"performance": {"pass_when_all": []}}}
    result = DeclaredJudge().judge(check, evd, params, values)
    assert result.verdicts["performance"] == Verdict.UNKNOWN
    assert (check.model_dump(mode="json"), values, evd.to_dict()) == snapshots
    assert DeclaredJudge().judge(check, evd, {}, values) == result


@pytest.mark.parametrize("cid", CHECKS)
def test_shared_attachment_and_observation_note_requirements_still_apply(cid):
    check, values, evd = fixture(cid)
    evd.artifacts = []
    evd.observations["declared_observation_notes"].value = {}
    assert all(v == Verdict.UNKNOWN for v in DeclaredJudge().judge(check, evd, {}, values).verdicts.values())


def update_scope_observations(cid, evd, policy_key, value):
    """Reproduce a self-report whose table values match an invalid policy."""
    obs = evd.observations
    if cid == "REQ-10":
        if policy_key == "allowed_sampling_options":
            for name in ("sampling_options_planned", "sampling_options_tested"):
                obs[name].value = deepcopy(value)
            if type(value) is list:
                obs["sampling_challenge_outcomes"].value = {o: "400" for o in value if type(o) is str}
                obs["sampling_health_results"].value = {o: "200" for o in value if type(o) is str}
        elif policy_key == "rejection_codes" and value:
            obs["sampling_challenge_outcomes"].value = {o: value[0] for o in ("temperature", "top_p", "min_tokens")}
    elif cid == "REQ-13":
        name = {"allowed_response_fields": "response_fields_seen", "allowed_error_fields": "error_fields_seen",
                "forbidden_return_options": "response_options_tested", "required_targets": "response_targets_tested"}[policy_key]
        obs[name].value = deepcopy(value)
    elif policy_key in {"allowed_log_fields", "required_sources", "required_branches"}:
        name = {"allowed_log_fields": "log_fields_seen", "required_sources": "log_source_ids_tested",
                "required_branches": "log_required_branches_tested"}[policy_key]
        obs[name].value = deepcopy(value)


def automatic_validate_policy(check, values):
    """Use real method binding without running executor initialization or I/O."""
    from guard.executors.sampling_validation import SamplingValidationExecutor
    from guard.executors.response_content import ResponseContentExecutor
    from guard.executors.log_analysis import LogAnalysisExecutor

    cls, settings = {
        "REQ-10": (SamplingValidationExecutor, {"constraint_source": "EXAMPLE fixed source", "constraint_engine_version": "0.31.0",
                    "max_output_tokens": 2, "max_total_requests": 32, "max_probe_tokens": 64, "max_duration_seconds": 30}),
        "REQ-13": (ResponseContentExecutor, {}),
        "OBS-03": (LogAnalysisExecutor, {"log_sources": [{"id": "engine", "container": "engine", "kind": "docker_logs"}]}),
    }[check.id]
    executor = object.__new__(cls)
    executor.cfg = settings
    executor.validate_policy(check, values)


INVALID_POLICY_CASES = [
    ("REQ-10", "allowed_sampling_options", []),
    ("REQ-10", "allowed_sampling_options", ["unsupported_sampling_option"]),
    ("REQ-10", "allowed_sampling_options", ["temperature", "temperature"]),
    ("REQ-10", "rejection_codes", []),
    ("REQ-10", "rejection_codes", ["500"]),
    ("REQ-13", "required_targets", []),
    ("REQ-13", "required_targets", ["unsupported_target"]),
    ("REQ-13", "required_targets", ["engine", "engine"]),
    ("REQ-13", "allowed_response_fields", []),
    ("REQ-13", "allowed_error_fields", []),
    ("REQ-13", "forbidden_return_options", ["unsupported_return_option"]),
    ("OBS-03", "required_sources", []),
    ("OBS-03", "required_branches", []),
    ("OBS-03", "required_branches", ["normal", "error"]),
    ("OBS-03", "required_branches", ["normal", "error", "exception_dump", "unsupported_branch"]),
    ("OBS-03", "allowed_log_fields", []),
    ("OBS-03", "forbidden_marker_contexts", ["unsupported_marker_context"]),
]


@pytest.mark.parametrize("cid,policy_key,value", INVALID_POLICY_CASES)
@pytest.mark.parametrize("raw", ["PASS", "FAIL", "UNKNOWN"])
def test_automatic_invalid_policy_cannot_be_bypassed_by_manual_coverage(cid, policy_key, value, raw):
    check, values, evd = fixture(cid, raw)
    values[policy_key] = deepcopy(value)
    update_scope_observations(cid, evd, policy_key, value)
    with pytest.raises(ValueError):
        automatic_validate_policy(check, values)
    before = deepcopy((values, evd.to_dict()))
    result = DeclaredJudge().judge(check, evd, {}, values)
    assert all(v == (Verdict.UNKNOWN if raw == "PASS" else raw) for v in result.verdicts.values())
    if raw == "PASS":
        assert any("AUTOMATION_MANUAL_POLICY_UNRESOLVED" in r for r in result.reasons)
    assert (values, evd.to_dict()) == before


@pytest.mark.parametrize("cid,policy_key,value", [("REQ-13", "required_targets", []), ("OBS-03", "required_branches", [])])
def test_empty_policy_bypass_reproduces_table_pass_but_declared_withholds_it(cid, policy_key, value):
    check, values, evd = fixture(cid)
    values[policy_key] = value
    update_scope_observations(cid, evd, policy_key, value)
    assert RuleTableJudge().judge(check, evd, check.judge.params, values).verdicts["security"] == Verdict.PASS
    assert DeclaredJudge().judge(check, evd, {}, values).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("cid,optional_keys", [("REQ-13", ["forbidden_return_options"]),
                                              ("OBS-03", ["forbidden_log_fields", "forbidden_marker_contexts"])])
def test_valid_explicit_empty_optional_lists_are_not_treated_as_missing(cid, optional_keys):
    check, values, evd = fixture(cid)
    for policy_key in optional_keys:
        values[policy_key] = []
        update_scope_observations(cid, evd, policy_key, [])
    automatic_validate_policy(check, values)
    result = DeclaredJudge().judge(check, evd, {}, values)
    assert result.verdicts["security"] == Verdict.PASS
    assert result.verdicts["functional"] == Verdict.PASS


@pytest.mark.parametrize("cid", CHECKS)
def test_manual_semantics_never_construct_an_executor_or_contact_docker(cid, monkeypatch):
    from guard.executors.sampling_validation import SamplingValidationExecutor
    from guard.executors.response_content import ResponseContentExecutor
    from guard.executors.log_analysis import LogAnalysisExecutor
    from guard.runtime.docker import Docker

    def forbidden(*args, **kwargs):
        raise AssertionError("manual judgment cannot construct executors or perform Docker I/O")

    for cls in (SamplingValidationExecutor, ResponseContentExecutor, LogAnalysisExecutor):
        monkeypatch.setattr(cls, "__init__", forbidden)
    monkeypatch.setattr(Docker, "_docker", forbidden)
    check, values, evd = fixture(cid)
    assert DeclaredJudge().judge(check, evd, {}, values).verdicts["security"] == Verdict.PASS
