"""PLT-07's narrow rule table uses EXAMPLE metadata, never real-engine proof."""
from copy import deepcopy
import json

import pytest
import yaml
from pydantic import ValidationError

from guard.core.select import PolicyValueError, select
from guard.judges.rule_table import RuleTableJudge
from guard.models import Evidence, Gap, Verdict
from tests.builders import ROOT, baseline_pack, environment, evidence, policy


POLICY_TYPES = {
    "subject_scope": "str",
    "subject_container_id": "str",
    "subject_uid": "int",
    "subject_net_namespace_inode": "int",
    "forbidden_receiver_container_id": "str",
    "forbidden_receiver_ip": "str",
    "forbidden_receiver_port": "int",
    "forbidden_protocol": "str",
    "forbidden_operation": "str",
    "validity_start": "str",
    "validity_end": "str",
}
EXAMPLE_POLICY = {
    "subject_scope": "all_processes_in_container_uid_netns",
    "subject_container_id": "a" * 64,
    "subject_uid": 1000,
    "subject_net_namespace_inode": 123,
    "forbidden_receiver_container_id": "b" * 64,
    "forbidden_receiver_ip": "192.0.2.10",
    "forbidden_receiver_port": 18001,
    "forbidden_protocol": "tcp",
    "forbidden_operation": "tcp_connect",
    "validity_start": "2026-10-19T00:00:00+00:00",
    "validity_end": "2026-10-19T00:01:00+00:00",
}


def _check():
    return baseline_pack().checks["PLT-07"]


def _observations(successes=(), *, complete=False):
    return {
        "forbidden_egress_successes": {"value": list(successes), "collected": True},
        "egress_trials": {"value": [{"outcome": "EXAMPLE-denied"}], "collected": True},
        "egress_scope_complete": {"value": complete, "collected": True},
    }


def _judge(observations, *, normal=True):
    check = _check()
    value = evidence(check_id=check.id, observations=observations,
        normal={"attempted": True, "succeeded": normal, "detail": "EXAMPLE"}).to_dict()
    value["challenges"] = [{"step": step, "attempted": True} for step in check.challenge_steps]
    stored = Evidence.model_validate(value).seal()
    before = stored.to_dict()
    out = RuleTableJudge().judge(check, stored, check.judge.params, deepcopy(EXAMPLE_POLICY))
    assert stored.to_dict() == before
    return out


def test_definition_registers_only_explicit_policy_and_three_observations():
    pack = baseline_pack()
    check = pack.checks["PLT-07"]
    override = yaml.safe_load((ROOT / "tools/overrides/PLT-07.yaml").read_text(encoding="utf-8"))
    assert pack.manifest.revision == 31 and check.revision == 5
    assert check.executor == "boundary_egress"
    assert check.collection_profile == "explicit-forbidden-egress-v1"
    assert {p.key: p.type for p in check.policy_params} == POLICY_TYPES
    assert len(check.policy_params) == len(POLICY_TYPES)
    assert all(p.required for p in check.policy_params)
    assert all(set(p) == {"key", "type", "required"} for p in override["policy_params"])
    assert {o.key: o.type for o in check.required_observations} == {
        "forbidden_egress_successes": "list[str]",
        "egress_trials": "list[dict]",
        "egress_scope_complete": "bool",
    }
    assert check.functional_probes == []
    assert check.judge.plugin == "rule_table"
    assert check.judge.params["pass_when_all"] == []
    assert check.judge.params["fail_when_any"] == [
        {"obs": "forbidden_egress_successes", "op": "nonempty"}]
    for axis in ("functional", "performance"):
        assert check.judge.params["axes"][axis] == {"pass_when_all": [], "fail_when_any": []}


def test_original_acceptance_is_preserved_in_the_narrow_definition():
    source = json.loads((ROOT / "reference/framework/data/checklist.json").read_text(encoding="utf-8"))
    original, = [row for row in source["platform_assessment_draft"] if row["id"] == "CHK-PLT-007"]
    check = _check()
    for field in ("question", "scope", "normal_control", "satisfied_if", "fail_criteria", "unknown_criteria"):
        assert getattr(check, field) == original[field]
    assert check.fail_criteria == "금지 목적지·작업의 송신이 실제 성공한다."


@pytest.mark.parametrize("missing", [None, *POLICY_TYPES])
def test_absent_explicit_policy_is_a_gap_and_is_never_filled_from_environment(missing):
    pack = baseline_pack()
    values = {} if missing is None else {k: deepcopy(v) for k, v in EXAMPLE_POLICY.items() if k != missing}
    pol = policy(pack.ref(), scope={"include": ["PLT-07"]}, values={"PLT-07": values})
    before = pol.to_dict()
    plan = select(pack, [environment()], pol)
    item, = [row for row in plan.items if row.check_id == "PLT-07"]
    assert item.selected and item.executor == "boundary_egress"
    assert Gap.NO_POLICY_VALUE in item.gaps and Gap.NO_EXECUTOR not in item.gaps
    assert pol.to_dict() == before


def test_explicit_primitive_policy_reaches_selection_without_new_common_policy_type():
    pack = baseline_pack()
    pol = policy(pack.ref(), scope={"include": ["PLT-07"]},
                 values={"PLT-07": deepcopy(EXAMPLE_POLICY)})
    before = pol.to_dict()
    plan = select(pack, [environment()], pol)
    item, = [row for row in plan.items if row.check_id == "PLT-07"]
    assert item.selected and Gap.NO_POLICY_VALUE not in item.gaps
    assert pol.to_dict() == before


@pytest.mark.parametrize("key,value", [
    ("subject_scope", {}), ("subject_container_id", None), ("subject_uid", True),
    ("subject_net_namespace_inode", "123"), ("forbidden_receiver_container_id", []),
    ("forbidden_receiver_ip", 123), ("forbidden_receiver_port", True),
    ("forbidden_protocol", []), ("forbidden_operation", {}),
    ("validity_start", 0), ("validity_end", None),
])
def test_wrong_primitive_policy_types_are_rejected_before_execution(key, value):
    pack = baseline_pack()
    values = {**deepcopy(EXAMPLE_POLICY), key: value}
    pol = policy(pack.ref(), scope={"include": ["PLT-07"]}, values={"PLT-07": values})
    with pytest.raises(PolicyValueError):
        select(pack, [environment()], pol)


@pytest.mark.parametrize("complete", [False, True])
def test_even_complete_normal_metadata_has_no_pass_rule(complete):
    out = _judge(_observations(complete=complete))
    assert out.verdicts == {axis: Verdict.UNKNOWN for axis in ("security", "functional", "performance")}


@pytest.mark.parametrize("outcome", ["denied", "timeout", "unknown", "connected"])
def test_trial_labels_without_confirmed_success_do_not_establish_a_verdict(outcome):
    observations = _observations()
    observations["egress_trials"]["value"] = [{"outcome": outcome}]
    out = _judge(observations)
    assert out.verdicts == {axis: Verdict.UNKNOWN for axis in ("security", "functional", "performance")}


@pytest.mark.parametrize("gap", ["egress_trials", "egress_scope_complete"])
def test_confirmed_independent_success_survives_other_missing_observations(gap):
    observations = _observations(["EXAMPLE-confirmed-forbidden-event-sha256"])
    observations[gap] = {"value": None, "collected": False}
    out = _judge(observations, normal=False)
    assert out.verdicts["security"] == Verdict.FAIL
    assert out.verdicts["functional"] == out.verdicts["performance"] == Verdict.UNKNOWN


@pytest.mark.parametrize("missing", [False, True])
def test_uncollected_or_absent_success_is_not_a_failure(missing):
    observations = _observations(["EXAMPLE-unverified-claim"])
    if missing:
        observations.pop("forbidden_egress_successes")
    else:
        observations["forbidden_egress_successes"] = {"value": None, "collected": False}
    out = _judge(observations)
    assert out.verdicts["security"] == Verdict.UNKNOWN


def test_uncollected_observation_cannot_smuggle_a_nonempty_violation_value():
    observations = _observations(["EXAMPLE-unverified-claim"])
    observations["forbidden_egress_successes"]["collected"] = False
    with pytest.raises(ValidationError, match="uncollected observation must not carry a value"):
        _judge(observations)
