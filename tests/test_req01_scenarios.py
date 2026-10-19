"""REQ-01 가짜 증거 4종: select → run → judge가 PASS / FAIL / UNKNOWN / UNKNOWN."""

import pytest

from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.models import Verdict
from guard.store import Store
from tests.builders import T0, baseline_pack, environment, policy

D = {"origin": "DECLARED", "source": "env.yaml"}


@pytest.mark.parametrize(
    "scenario, expected",
    [("pass", Verdict.PASS), ("direct_200", Verdict.FAIL), ("not_attempted", Verdict.UNKNOWN), ("missing_obs", Verdict.UNKNOWN)],
)
def test_req01_fake_scenarios(tmp_path, scenario, expected):
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
    if expected == Verdict.UNKNOWN:
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
