"""INV-02: EXAMPLE 자료, 기대값, 실행하지 않은 시험을 MEASURED로 표시하지 않는다."""

import pytest
from pydantic import ValidationError

from guard.models import DataOrigin, ExecStatus
from tests.builders import evidence


@pytest.mark.parametrize("status", [ExecStatus.NOT_RUN, ExecStatus.ABORTED, ExecStatus.PENDING, ExecStatus.RUNNING])
def test_unfinished_execution_cannot_be_measured(status):
    with pytest.raises(ValidationError):
        evidence(status=status, data_origin=DataOrigin.MEASURED)


def test_completed_measured_is_allowed():
    assert evidence(status=ExecStatus.COMPLETED, data_origin=DataOrigin.MEASURED).data_origin == DataOrigin.MEASURED


def test_fake_executor_evidence_is_example(tmp_path):
    from guard.executors.fake import SCENARIOS, FakeExecutor
    from tests.builders import baseline_pack, environment

    check = baseline_pack().checks["REQ-01"]
    for s in SCENARIOS:
        assert FakeExecutor(s).run(check, environment(), {}, None, tmp_path).data_origin == DataOrigin.EXAMPLE


def test_result_from_example_evidence_is_never_measured():
    from tests.judging import OK, judge

    res = judge(evidence(observations=OK), origins=[DataOrigin.MEASURED])
    assert res.verification_level == DataOrigin.EXAMPLE
    assert res.data_origin == DataOrigin.EXAMPLE


def test_not_run_evidence_is_not_measured_even_with_measured_inputs(tmp_path):
    from guard.core.run import RunConfig, run_plan
    from guard.core.select import select
    from guard.store import Store
    from tests.builders import baseline_pack, environment, policy

    store = Store(tmp_path / ".guard")
    pack = baseline_pack()
    env = environment({"gateway": {"value": True, "origin": "MEASURED", "source": "probe"}}, origin=DataOrigin.MEASURED)
    pol = policy(pack.ref(), data_origin=DataOrigin.MEASURED, scope={"include": ["SUP-01"], "exclude": []})
    store.put(env); store.put(pol)
    plan = select(pack, [env], pol); store.put(plan)
    (evd,) = run_plan(store, pack, plan, RunConfig())
    assert evd.execution.status == ExecStatus.NOT_RUN and evd.data_origin == DataOrigin.DECLARED
