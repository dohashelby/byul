"""REQ-01 가짜 실행에서 보안 판정과 실패 근거를 구분한다."""

import pytest

from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.models import SecurityFailureTag, Verdict
from guard.store import Store
from tests.builders import T0, baseline_pack, environment, policy

D = {"origin": "DECLARED", "source": "env.yaml"}


@pytest.mark.parametrize(
    "scenario, expected, confirmed",
    [
        ("pass", Verdict.PASS, False),
        ("direct_200", Verdict.FAIL, True),
        ("not_attempted", Verdict.FAIL, False),
        ("missing_obs", Verdict.FAIL, False),
        ("refused_with_positive_control", Verdict.PASS, False),
        ("refused_without_positive_control", Verdict.FAIL, False),
        ("partial_routes", Verdict.FAIL, False),
        ("direct_200_on_invocations", Verdict.FAIL, True),
    ],
)
def test_req01_fake_scenarios(tmp_path, scenario, expected, confirmed):
    store = Store(tmp_path / ".guard")
    pack = baseline_pack()
    env = environment({"gateway": {"value": True, **D}})
    pol = policy(pack.ref(), scope={"include": ["REQ-01"], "exclude": []})
    store.put(env); store.put(pol)
    plan = select(pack, [env], pol); store.put(plan)
    cfg = RunConfig(executors={"direct_route": "fake"}, adapter="fake", raw={"fake": {"scenario": scenario}})
    (evd,) = run_plan(store, pack, plan, cfg)
    assert evd.data_origin == "EXAMPLE"
    (res,) = judge_plan(store, pack, plan, as_of=T0)
    assert res.verdicts.security == expected
    assert res.evidence == evd.ref()
    assert res.verification_level == "EXAMPLE"
    assert (SecurityFailureTag.CONFIRMED_VIOLATION in res.security_failure_tags) is confirmed
    assert bool(res.security_failure_tags) is (expected == Verdict.FAIL)
    assert res.reasons


def test_rejudge_same_evidence_does_not_duplicate(tmp_path):
    store = Store(tmp_path / ".guard")
    pack = baseline_pack()
    env = environment({"gateway": {"value": True, **D}})
    pol = policy(pack.ref(), scope={"include": ["REQ-01"], "exclude": []})
    store.put(env); store.put(pol)
    plan = select(pack, [env], pol); store.put(plan)
    run_plan(store, pack, plan, RunConfig(executors={"direct_route": "fake"}))
    (a,) = judge_plan(store, pack, plan, as_of=T0)
    (b,) = judge_plan(store, pack, plan, as_of=T0)
    assert (a.id, a.revision) == (b.id, b.revision)


def test_same_time_legacy_and_binary_rejudgment_reuse_their_own_revisions(tmp_path):
    store = Store(tmp_path / ".guard")
    pack = baseline_pack()
    env = environment({"gateway": {"value": True, **D}})
    pol = policy(pack.ref(), scope={"include": ["REQ-01"], "exclude": []})
    store.put(env)
    store.put(pol)
    plan = select(pack, [env], pol)
    store.put(plan)
    run_plan(store, pack, plan, RunConfig(executors={}))

    (legacy,) = judge_plan(store, pack, plan, as_of=T0, binary_security=False)
    (binary,) = judge_plan(store, pack, plan, as_of=T0)
    assert legacy.verdicts.security == Verdict.UNKNOWN
    assert "security_verdict_version" not in legacy.to_dict()
    assert "security_failure_tags" not in legacy.to_dict()
    assert binary.verdicts.security == Verdict.FAIL
    assert binary.id == legacy.id and binary.revision == legacy.revision + 1
    assert judge_plan(store, pack, plan, as_of=T0, binary_security=False) == [legacy]
    assert judge_plan(store, pack, plan, as_of=T0) == [binary]
