"""4단계: 적용 판단 규칙 5개와 선정 규칙 (SPEC §4.4)."""

import pytest

from guard.core.select import evaluate_check, select
from guard.models import Applicability as A
from guard.models import CheckDef, CondResult, Gap, NotSelectedReason
from tests.builders import baseline_pack, environment, policy

D = {"origin": "DECLARED", "source": "env.yaml"}


def check(conditions=None, always=False, **kw) -> CheckDef:
    base = dict(
        id="REQ-90", revision=1, area="REQ", purpose="P", question="q", review_order="reference_baseline_first",
        applies_when={"all": conditions or [], "always": always, "prose": "원문"},
        normal_control="n", challenge_steps=["s"], satisfied_if="s", fail_criteria="f", unknown_criteria="u",
    )
    base.update(kw)
    return CheckDef.model_validate(base)


def fact(value, **kw):
    return {"value": value, **D, **kw}


def evaluate(chk, facts):
    return evaluate_check(chk, [environment(facts)])


# --- 규칙 1~5 ---------------------------------------------------------------


def test_rule1_evidenced_false_is_not_applicable_and_keeps_other_conditions():
    chk = check([{"fact": "gateway", "eq": True}, {"fact": "prefix_cache", "eq": True}])
    app, conds, _ = evaluate(chk, {"gateway": fact(False, absence_evidence="EVD-1#cfg")})
    assert app == A.NOT_APPLICABLE
    assert [c.result for c in conds] == [CondResult.FALSE, CondResult.NULL]


def test_rule2_all_true_is_applicable():
    chk = check([{"fact": "gateway", "eq": True}, {"fact": "prefix_cache", "eq": True}])
    app, conds, _ = evaluate(chk, {"gateway": fact(True), "prefix_cache": fact(True)})
    assert app == A.APPLICABLE
    assert all(c.result == CondResult.TRUE for c in conds)
    assert conds[0].origin == "DECLARED" and conds[0].actual is True


def test_rule3_conflict():
    chk = check([{"fact": "gateway", "eq": True}])
    app, conds, _ = evaluate(chk, {"gateway": fact(None, conflict=[True, False])})
    assert app == A.CONFLICT
    assert conds[0].result == CondResult.CONFLICT


def test_rule1_beats_conflict():
    chk = check([{"fact": "gateway", "eq": True}, {"fact": "prefix_cache", "eq": True}])
    app, _, _ = evaluate(chk, {"gateway": fact(None, conflict=[1, 2]), "prefix_cache": fact(False, absence_evidence="x")})
    assert app == A.NOT_APPLICABLE


@pytest.mark.parametrize(
    "facts",
    [
        {},  # 사실 없음
        {"gateway": fact(None)},  # 값 모름
        {"gateway": {"value": True}},  # 근거(origin/source) 없음
        {"gateway": fact("yes")},  # 형식 불일치는 참이 아님
    ],
)
def test_rule4_no_basis_is_undetermined(facts):
    chk = check([{"fact": "gateway", "eq": True}])
    app, conds, _ = evaluate(chk, facts)
    assert app == A.UNDETERMINED
    assert conds[0].result == CondResult.NULL


def test_rule5_empty_conditions_not_always_true():
    app, conds, _ = evaluate(check([]), {"gateway": fact(True)})
    assert app == A.UNDETERMINED and conds == []


def test_rule5_explicit_always():
    app, _, _ = evaluate(check([], always=True), {})
    assert app == A.APPLICABLE


def test_always_with_conditions_is_invalid():
    with pytest.raises(ValueError):
        check([{"fact": "gateway", "eq": True}], always=True)


# --- 선정 규칙 ----------------------------------------------------------------


@pytest.fixture(scope="module")
def pack():
    return baseline_pack()


def _plan(pack, facts, **pol):
    return select(pack, [environment(facts)], policy(pack.ref(), **pol))


def test_req01_applicable_and_selected_with_executor(pack):
    plan = _plan(pack, {"gateway": fact(True)})
    item = plan.item("REQ-01")
    assert item.applicability == A.APPLICABLE and item.selected
    assert item.gaps == []
    assert item.executor == "direct_route"
    assert item.priority == "reference_baseline_first"


def test_not_applicable_not_selected_with_reason(pack):
    plan = _plan(pack, {"gateway": fact(False, absence_evidence="EVD-1#net")})
    item = plan.item("REQ-01")
    assert not item.selected
    assert item.not_selected_reason == NotSelectedReason.NOT_APPLICABLE
    assert item.reason


def test_undetermined_is_still_selected(pack):
    plan = _plan(pack, {})
    item = plan.item("REQ-01")
    assert item.applicability == A.UNDETERMINED and item.selected


def test_prose_only_rows_selected_as_manual(pack):
    plan = _plan(pack, {"gateway": fact(True)})
    sup = plan.item("SUP-05")  # Unchanged prose-only control; SUP-03 now has an observation profile.
    assert sup.applicability == A.UNDETERMINED
    assert sup.selected and sup.gaps == [Gap.NO_EXECUTOR]
    assert len(plan.items) == 77


def test_template_supply_observation_profile_requires_its_explicit_policy(pack):
    plan = _plan(pack, {"gateway": fact(True)}, values={})
    sup = plan.item("SUP-03")
    assert sup.applicability == A.APPLICABLE and sup.selected
    assert sup.executor == "template_validation" and sup.gaps == [Gap.NO_POLICY_VALUE]
    assert pack.checks["SUP-03"].collection_profile == "fixed-template-supply-v1"
    assert pack.checks["SUP-03"].judge.params["pass_when_all"] == []
    assert pack.checks["SUP-03"].judge.params["fail_when_any"] == []


def test_policy_exclude(pack):
    plan = _plan(pack, {"gateway": fact(True)}, scope={"include": [], "exclude": ["REQ-01"]})
    item = plan.item("REQ-01")
    assert not item.selected and item.not_selected_reason == NotSelectedReason.POLICY_SCOPE
    assert item.applicability == A.APPLICABLE  # 적용 판단은 그대로 남긴다


def test_policy_include_pattern(pack):
    plan = _plan(pack, {"gateway": fact(True)}, scope={"include": ["REQ-0*"], "exclude": []})
    selected = sorted(i.check_id for i in plan.items if i.selected)
    assert selected == [f"REQ-0{i}" for i in range(1, 10)]
    assert plan.item("SUP-01").not_selected_reason == NotSelectedReason.POLICY_SCOPE


def test_missing_policy_value_is_gap(pack):
    plan = _plan(pack, {"gateway": fact(True)}, values={})
    assert plan.item("REQ-01").gaps == [Gap.NO_POLICY_VALUE]


def test_plan_refs_and_origin(pack):
    env = environment({"gateway": fact(True)})
    pol = policy(pack.ref())
    plan = select(pack, [env], pol)
    assert plan.hash_ok()
    assert plan.inputs.environments == [env.ref()]
    assert plan.inputs.policy == pol.ref()
    assert plan.inputs.pack == pack.ref()
    assert plan.data_origin == "EXAMPLE"
    assert plan.items[0].check_revision == pack.checks[plan.items[0].check_id].revision


def test_audit_needs_exactly_one_environment(pack):
    with pytest.raises(ValueError):
        select(pack, [], policy(pack.ref()))


def test_policy_must_reference_same_pack(pack):
    from tests.builders import dummy_ref

    with pytest.raises(ValueError):
        select(pack, [environment({})], policy(dummy_ref("PACK", "PACK-OTHER")))
