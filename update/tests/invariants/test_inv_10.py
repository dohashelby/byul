"""INV-10: 미선정 점검은 EVD·RES를 만들지 않는다. 선정됐지만 실행 못 한 점검은 EVD(NOT_RUN/ABORTED)와 RES(UNKNOWN)."""

from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.models import ExecStatus
from guard.store import Store
from tests.builders import baseline_pack, environment, policy

D = {"origin": "DECLARED", "source": "env.yaml"}


def _setup(tmp_path, include):
    store = Store(tmp_path / ".guard")
    pack = baseline_pack()
    env, pol = environment({"gateway": {"value": True, **D}}), policy(pack.ref(), scope={"include": include, "exclude": []})
    store.put(env); store.put(pol)
    plan = select(pack, [env], pol)
    store.put(plan)
    return store, pack, plan


def test_run_creates_evidence_only_for_selected(tmp_path):
    store, pack, plan = _setup(tmp_path, ["REQ-01", "SUP-01"])
    evds = run_plan(store, pack, plan, RunConfig(executors={"direct_route": "fake"}))
    by_check = {e.check_id: e for e in evds}
    assert set(by_check) == {"REQ-01", "SUP-01"}
    assert by_check["REQ-01"].execution.status == ExecStatus.COMPLETED
    assert by_check["SUP-01"].execution.status == ExecStatus.NOT_RUN
    assert "NO_EXECUTOR" in by_check["SUP-01"].execution.abort_reason
    stored = {e.check_id for e in store.iter_kind("EVD")}
    assert stored == {"REQ-01", "SUP-01"}


def test_unconfigured_executor_is_not_run(tmp_path):
    store, pack, plan = _setup(tmp_path, ["REQ-01"])
    (evd,) = run_plan(store, pack, plan, RunConfig(executors={}))
    assert evd.execution.status == ExecStatus.NOT_RUN
    assert "direct_route" in evd.execution.abort_reason


def test_rerun_appends_new_revision(tmp_path):
    store, pack, plan = _setup(tmp_path, ["REQ-01"])
    cfg = RunConfig(executors={"direct_route": "fake"})
    (first,) = run_plan(store, pack, plan, cfg)
    (second,) = run_plan(store, pack, plan, cfg)
    assert second.id == first.id and second.revision == first.revision + 1
    assert store.get(first.id, 1) == first


def test_judge_makes_unknown_result_for_not_run_and_nothing_for_unselected(tmp_path):
    from guard.core.judge import judge_plan
    from guard.models import Verdict
    from tests.builders import T0

    store, pack, plan = _setup(tmp_path, ["REQ-01", "SUP-01"])
    run_plan(store, pack, plan, RunConfig(executors={"direct_route": "fake"}))
    results = {r.check_id: r for r in judge_plan(store, pack, plan, as_of=T0)}
    assert set(results) == {"REQ-01", "SUP-01"}  # 미선정 75행은 RES 없음
    assert results["SUP-01"].verdicts.security == Verdict.UNKNOWN
    assert results["REQ-01"].verdicts.security == Verdict.PASS
