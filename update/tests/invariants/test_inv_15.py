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
        (evd,) = run_plan(store, pack, plan, RunConfig(executors={"direct_route": "test-impl"}, adapter="vllm"))
        return store, pack, plan, evd
    finally:
        del registry.EXECUTORS["test-impl"]


def test_executor_exception_becomes_aborted_evidence(tmp_path):
    *_, evd = _run(tmp_path, Boom())
    assert evd.execution.status == ExecStatus.ABORTED
    assert evd.execution.abort_reason == "EXECUTOR_FAILURE"
    assert "sensor offline" not in str(evd.to_dict())
    assert evd.data_origin != DataOrigin.MEASURED
    assert not any(o.collected for o in evd.observations.values())


def test_invalid_draft_becomes_aborted(tmp_path):
    *_, evd = _run(tmp_path, LiesMeasured())
    assert evd.execution.status == ExecStatus.ABORTED
    assert evd.data_origin != DataOrigin.MEASURED



def test_docker_probe_failure_becomes_aborted_evidence(tmp_path):
    """실행 계층(Docker) 실패: probe 컨테이너가 비정상 종료하면 ABORTED → UNKNOWN. 가짜 Docker 실행기 사용."""
    import json

    from guard.core.judge import judge_plan
    from guard.executors.direct_route import DirectRouteExecutor, docker_container_probe
    from guard.models import Verdict
    from guard.runtime.docker import CmdResult, Docker
    from tests.builders import T0

    def failing_runner(argv, *, input, timeout, env_extra):
        return CmdResult(125, "", "docker: Error response from daemon: network not found")

    target = tmp_path / "target.json"
    target.write_text(json.dumps({
        "lab": True, "probe_image": "x", "origins": {"external": {"network": "n1"}, "trusted": {"network": "n2"}},
        "gateway": {"url": "http://g:8080"}, "engine": {"trusted_url": "http://e:8000"},
        "direct_targets": [{"origin": "external", "url": "http://e:8000"}], "gateway_known_routes": [],
    }), encoding="utf-8")
    probe = docker_container_probe(Docker("t1abc", runner=failing_runner), "x")
    impl = DirectRouteExecutor({"target": str(target), "allowed_key_env": "K"}, container_probe=probe, environ={"K": "k1"})
    store, pack, plan, evd = _run(tmp_path, impl)
    assert evd.execution.status == ExecStatus.ABORTED
    assert evd.execution.abort_reason == "EXECUTOR_FAILURE"
    assert "network not found" not in str(evd.to_dict())
    (res,) = judge_plan(store, pack, plan, as_of=T0)
    assert res.verdicts.security == Verdict.UNKNOWN


# 실제 vLLM 기동 실패 전파는 tests/test_vllm_engine.py::test_inv15_real_engine_start_failure_is_aborted_unknown (-m engine)
