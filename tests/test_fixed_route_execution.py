"""Fixed request material, real registration, and unchanged judgments (EXAMPLE transports)."""
import json
from copy import deepcopy

import pytest

from guard.adapters.vllm import SAFE_PROBE_REVIEWED_IMAGES
from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.direct_route import DirectRouteExecutor
from guard.executors.role_route import RoleRouteExecutor
from guard.models import ExecStatus, Verdict
from guard.store import Store
from tests.builders import baseline_pack, environment, policy
from tests.test_direct_route import ALLOWED, DENIED, World, _target as direct_target
from tests.test_role_route import ADMIN, GENERAL, WRONG, RoleWorld, _target as role_target


def run(tmp_path, monkeypatch, check_id, world, version, routes):
    tmp_path.mkdir()
    is_direct = check_id == "REQ-01"
    target = direct_target(tmp_path, world.exposed) if is_direct else role_target(tmp_path)
    value = json.loads(target.read_text(encoding="utf-8"))
    value["comparison_config"] = {"EXAMPLE": "same bounded launch"}
    target.write_text(json.dumps(value), encoding="utf-8")
    cls = DirectRouteExecutor if is_direct else RoleRouteExecutor
    cfg = {"target": str(target), "comparison_routes": routes,
           "roles": {"allowed": "KA"} if is_direct else {"general": "KG", "admin": "KA"},
           "wrong_key_env": "KW"}
    keys = {"KA": ALLOWED, "KW": DENIED} if is_direct else {"KG": GENERAL, "KA": ADMIN, "KW": WRONG}
    submitted = []

    def transport(network, requests, timeout):
        submitted.extend(deepcopy(requests))
        return world.container_probe(network, requests, timeout)

    kwargs = {"host_probe": world.host_probe} if is_direct else {}
    plugin = cls(cfg, container_probe=transport, environ=keys, **kwargs)
    monkeypatch.setitem(registry.EXECUTORS, cls.name + ":fixed-test", lambda _: plugin)
    pack = baseline_pack()
    env = environment({"gateway": {"value": True, "origin": "EXAMPLE", "source": "unit"}},
        engine={"name": "vllm", "version": version, "image_digest": SAFE_PROBE_REVIEWED_IMAGES[version]},
        model={"id": "facebook/opt-125m", "inventory_sha256": "1" * 64},
        runtime={"gpu_count": 0, "cpu": {"architecture": "x86_64", "model": "EXAMPLE", "system": "Linux",
                                      "kernel": "EXAMPLE", "threads": 2}})
    values = {"REQ-01": {"allowed_callers": ["allowed"]}} if is_direct else {
        "REQ-03": {"general_allowed_kinds": ["inference"]}}
    pol = policy(pack.ref(), values=values, scope={"include": [check_id], "exclude": []})
    store = Store(tmp_path / "store")
    store.put_many([env, pol])
    plan = select(pack, [env], pol)
    store.put(plan)
    (evd,) = run_plan(store, pack, plan, RunConfig(executors={cls.name: "fixed-test"}, adapter="vllm"))
    (result,) = judge_plan(store, pack, plan, as_of=env.created_at)
    summary = (json.loads((store.root / evd.artifacts[0].path).read_text(encoding="utf-8").splitlines()[-1])
               if evd.artifacts else None)
    return evd, result, summary, submitted


@pytest.mark.parametrize("exposed", [False, True])
def test_same_union_is_submitted_without_inventing_registration(tmp_path, monkeypatch, exposed):
    old = {"/v1/models": "get", "/v1/completions": "post", "/invocations": "post"}
    new = {**old, "/v1/systemone": "post"}
    fixed = sorted(f"{method.upper()} {path}" for path, method in new.items())
    baseline = run(tmp_path / "old", monkeypatch, "REQ-01", World(exposed, engine_routes=old), "0.31.0", fixed)
    candidate = run(tmp_path / "new", monkeypatch, "REQ-01", World(exposed, engine_routes=new), "0.32.0", fixed)
    for evd, result, summary, submitted in (baseline, candidate):
        assert evd.execution.status == ExecStatus.COMPLETED, evd.execution.abort_reason
        assert result.verdicts.security == (Verdict.FAIL if exposed else Verdict.PASS)
        assert result.verdicts.functional == Verdict.PASS
        assert evd.execution.executor.version == "0.7.0"
        assert any(r["url"].endswith("/v1/systemone") for r in submitted)
        assert summary["fixture"]["protocol"] == "fixed-route-fixture/1"
    assert "POST /v1/systemone" not in baseline[0].observations["routes_registered"].value
    assert "POST /v1/systemone" not in baseline[0].observations["routes_tested"].value
    assert "POST /v1/systemone" in candidate[0].observations["routes_registered"].value
    assert baseline[0].comparability == candidate[0].comparability
    assert baseline[2]["fixture"] == candidate[2]["fixture"]


@pytest.mark.parametrize("enforced", [True, False])
def test_reviewed_admin_probes_keep_the_same_material_and_fail_detection(tmp_path, monkeypatch, enforced):
    fixed = sorted(["GET /load", "GET /metrics", "POST /is_scaling_elastic_ep", "POST /scale_elastic_ep"])
    old = run(tmp_path / "old", monkeypatch, "REQ-03", RoleWorld(enforced), "0.31.0", fixed)
    new = run(tmp_path / "new", monkeypatch, "REQ-03", RoleWorld(enforced), "0.32.0", fixed)
    for evd, result, summary, submitted in (old, new):
        assert evd.execution.status == ExecStatus.COMPLETED, evd.execution.abort_reason
        assert result.verdicts.security == (Verdict.PASS if enforced else Verdict.FAIL)
        assert result.verdicts.functional == Verdict.PASS
        assert set(evd.observations["routes_registered"].value) == set(evd.observations["routes_tested"].value)
        assert summary["skipped_routes"] == {}
        scaling = [r for r in submitted if r["url"].endswith("/scale_elastic_ep")]
        assert scaling and all(r["body"] == {} for r in scaling)
        assert summary["admin_outcomes"]["POST /scale_elastic_ep"] == "handler_reached"
    assert old[0].comparability == new[0].comparability


def test_unexpected_registered_route_aborts_before_security_requests(tmp_path, monkeypatch):
    world = World(False, engine_routes={"/v1/models": "get", "/new-route": "post"})
    evd, result, summary, submitted = run(tmp_path / "new", monkeypatch, "REQ-01", world, "0.32.0",
        sorted(["GET /v1/models", "POST /invocations", "POST /v1/completions"]))
    assert evd.execution.status == ExecStatus.ABORTED
    assert result.verdicts.security == Verdict.UNKNOWN
    assert result.verdicts.functional == Verdict.UNKNOWN
    assert summary is None
    assert all(r["url"].endswith("/openapi.json") for r in submitted)


@pytest.mark.parametrize("damage", ["tcp-id", "http-id", "tcp-count"])
def test_mislabelled_transport_cannot_establish_route_coverage(tmp_path, monkeypatch, damage):
    class Broken(World):
        def container_probe(self, network, requests, timeout):
            responses = super().container_probe(network, requests, timeout)
            tcp = requests and requests[0].get("mode") == "tcp"
            http = requests and requests[0]["id"].startswith("func|")
            if damage == "tcp-count" and tcp:
                return responses[:-1]
            if (damage == "tcp-id" and tcp) or (damage == "http-id" and http):
                responses[0]["id"] = "EXAMPLE-mismatched-label"
            return responses

    fixed = sorted(["GET /v1/models", "POST /invocations", "POST /v1/completions"])
    evd, result, summary, submitted = run(tmp_path / "broken", monkeypatch, "REQ-01", Broken(False), "0.32.0", fixed)
    assert evd.execution.status == ExecStatus.ABORTED
    assert result.verdicts.security == Verdict.UNKNOWN
    assert summary is None and submitted
