"""INV-15: 플러그인(실행기·센서·어댑터) 실패는 근거 미확보로 전파한다. 예외를 삼켜 PASS·NOT_APPLICABLE로 바꾸지 않는다."""

from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.base import EvidenceDraft
from guard.models import DataOrigin, ExecStatus
from guard.store import Store
from tests.builders import baseline_pack, environment, policy

D = {"origin": "DECLARED", "source": "env.yaml"}


class Boom:
    name, version = "boom", "0"

    def run(self, check, env, policy_values, adapter, workdir):
        raise RuntimeError("sensor offline")


class LiesMeasured:
    """실행을 끝내지 않았는데 MEASURED라고 주장하는 실행기."""

    name, version = "liar", "0"

    def run(self, check, env, policy_values, adapter, workdir):
        return EvidenceDraft(status=ExecStatus.ABORTED, data_origin=DataOrigin.MEASURED,
                             normal_control={"attempted": False}, abort_reason="x")


def _run(tmp_path, impl):
    registry.EXECUTORS["test-impl"] = lambda cfg: impl
    try:
        store = Store(tmp_path / ".guard")
        pack = baseline_pack()
        env, pol = environment({"gateway": {"value": True, **D}}), policy(pack.ref(), scope={"include": ["REQ-01"], "exclude": []})
        store.put(env); store.put(pol)
        plan = select(pack, [env], pol); store.put(plan)
        (evd,) = run_plan(store, pack, plan, RunConfig(executors={"direct_route": "test-impl"}))
        return store, pack, plan, evd
    finally:
        del registry.EXECUTORS["test-impl"]


def test_executor_exception_becomes_aborted_evidence(tmp_path):
    *_, evd = _run(tmp_path, Boom())
    assert evd.execution.status == ExecStatus.ABORTED
    assert "sensor offline" in evd.execution.abort_reason
    assert evd.data_origin != DataOrigin.MEASURED
    assert not any(o.collected for o in evd.observations.values())


def test_invalid_draft_becomes_aborted(tmp_path):
    *_, evd = _run(tmp_path, LiesMeasured())
    assert evd.execution.status == ExecStatus.ABORTED
    assert evd.data_origin != DataOrigin.MEASURED


import pytest  # noqa: E402


@pytest.mark.engine
@pytest.mark.skip(reason="실제 엔진 어댑터(vLLM)의 기동 실패·ready 시간 초과를 ABORTED로 전파하는 시험은 5단계(실제 엔진 필요).")
def test_real_adapter_start_failure_is_aborted():
    raise AssertionError
