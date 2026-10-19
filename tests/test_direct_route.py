"""direct_route 실행기: 가상 위상(가짜 probe)으로 select→run→judge 검증. Docker·네트워크 없음."""

import json
from urllib.parse import urlsplit

import pytest

from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.direct_route import DirectRouteExecutor, mask, outcome_rank, representative
from guard.models import DataOrigin, ExecStatus, Verdict
from guard.store import Store
from tests.builders import baseline_pack, environment, policy

ALLOWED, DENIED = "allowed-test-key-123", "denied-test-key-456"
ENGINE_ROUTES = {"/health": "get", "/v1/models": "get", "/v1/completions": "post", "/invocations": "post"}
KNOWN = [{"method": "GET", "path": "/v1/models"}, {"method": "POST", "path": "/v1/completions"},
         {"method": "POST", "path": "/invocations"}]


class World:
    """가상 위상. engine(e:8000), gateway(g:8080), 네트워크 도달성."""

    def __init__(self, exposed: bool, *, engine_routes=None, engine_key=None, trusted_reachable=True,
                 blocked="timeout", openapi_ok=True):
        self.exposed = exposed
        self.routes = engine_routes or ENGINE_ROUTES
        self.engine_key = engine_key
        self.trusted_reachable = trusted_reachable
        self.blocked = blocked
        self.openapi_ok = openapi_ok
        self.engine_hits = []

    def engine(self, method, path, headers):
        self.engine_hits.append((method, path))
        if path == "/openapi.json":
            if not self.openapi_ok:
                return 500, ""
            return 200, json.dumps({"paths": {p: {m: {}} for p, m in self.routes.items()}})
        if self.engine_key and path.startswith(("/v1", "/invocations")) and headers.get("Authorization") != f"Bearer {self.engine_key}":
            return 401, ""
        return (200, "{}") if path in self.routes else (404, "")

    def handle(self, network, r):
        u = urlsplit(r["url"])
        headers = r.get("headers") or {}
        base = {"id": r.get("id"), "method": r["method"], "url": r["url"]}
        if u.hostname == "g":
            if headers.get("Authorization") != f"Bearer {ALLOWED}":
                return {**base, "outcome": 401, "phase": "response"}
            # Gateway는 클라이언트 키를 지우고, 엔진 자체 키가 있으면 그것을 붙여 전달한다
            fwd = {"Authorization": f"Bearer {self.engine_key}"} if self.engine_key else {}
            status, body = self.engine(r["method"], u.path, fwd)
            return {**base, "outcome": status, "phase": "response"}
        if u.hostname in ("e", "127.0.0.1"):
            if network == "trusted":
                if not self.trusted_reachable:
                    return {**base, "outcome": "timeout", "phase": "connect"}
            elif not self.exposed:
                return {**base, "outcome": self.blocked, "phase": "connect"}
            status, body = self.engine(r["method"], u.path, headers)
            out = {**base, "outcome": status, "phase": "response"}
            if r.get("want_body"):
                out["body"] = body
            return out
        return {**base, "outcome": "dns_error", "phase": "connect"}

    def container_probe(self, network, requests, timeout):
        return [self.handle(network, r) for r in requests]

    def host_probe(self, r, timeout):
        return self.handle("host", r)


def _target(tmp_path, exposed, lab=True):
    t = {
        "lab": lab, "run_id": "sim", "topology": "exposed" if exposed else "isolated", "probe_image": "x",
        "origins": {"external": {"network": "external"}, "trusted": {"network": "trusted"}},
        "gateway": {"url": "http://g:8080", "origin": "external"},
        "engine": {"trusted_url": "http://e:8000"},
        "direct_targets": [{"origin": "external", "url": "http://e:8000"}]
        + ([{"origin": "host", "url": "http://127.0.0.1:18000"}] if exposed else []),
        "gateway_known_routes": KNOWN,
    }
    path = tmp_path / "target.json"
    path.write_text(json.dumps(t), encoding="utf-8")
    return path


def _run(tmp_path, world, *, lab=True, environ=None, cfg_extra=None):
    target = _target(tmp_path, world.exposed, lab=lab)
    cfg = {"target": str(target), "allowed_key_env": "K_ALLOWED", "denied_key_env": "K_DENIED", **(cfg_extra or {})}
    env_vars = {"K_ALLOWED": ALLOWED, "K_DENIED": DENIED} if environ is None else environ
    registry.EXECUTORS["direct_route:sim"] = lambda raw: DirectRouteExecutor(
        cfg, container_probe=world.container_probe, host_probe=world.host_probe, environ=env_vars)
    try:
        store = Store(tmp_path / ".guard")
        pack = baseline_pack()
        env = environment({"gateway": {"value": True, "origin": "DECLARED", "source": "sim"}})
        pol = policy(pack.ref(), scope={"include": ["REQ-01"], "exclude": []})
        store.put(env); store.put(pol)
        plan = select(pack, [env], pol); store.put(plan)
        (evd,) = run_plan(store, pack, plan, RunConfig(executors={"direct_route": "sim"}, adapter="vllm"))
        (res,) = judge_plan(store, pack, plan, as_of=env.created_at)
        return store, evd, res
    finally:
        del registry.EXECUTORS["direct_route:sim"]


def _log(store, evd):
    return (store.root / evd.artifacts[0].path).read_text(encoding="utf-8")


def test_exposed_is_fail_and_keys_never_stored(tmp_path):
    world = World(exposed=True)
    store, evd, res = _run(tmp_path, world)
    assert evd.execution.status == ExecStatus.COMPLETED
    assert res.verdicts.security == Verdict.FAIL
    assert evd.observations["direct_outcomes"].value == {"/invocations": 200, "/v1/completions": 200, "/v1/models": 200}
    assert evd.normal_control.succeeded is True
    blob = _log(store, evd) + json.dumps(evd.to_dict()) + json.dumps(res.to_dict())
    for p in store.root.rglob("*"):
        if p.is_file():
            blob += p.read_bytes().decode("utf-8", "replace")
    assert ALLOWED not in blob and DENIED not in blob


def test_isolated_timeout_with_positive_control_is_pass(tmp_path):
    store, evd, res = _run(tmp_path, World(exposed=False))
    assert res.verdicts.security == Verdict.PASS, res.reasons
    assert res.verdicts.functional == Verdict.PASS
    assert set(evd.observations["direct_outcomes"].value.values()) == {"timeout"}
    assert evd.observations["trusted_origin_reachable"].value is True
    assert evd.data_origin == DataOrigin.EXAMPLE  # 실험실 대상


def test_isolated_without_positive_control_is_unknown(tmp_path):
    _, evd, res = _run(tmp_path, World(exposed=False, trusted_reachable=False))
    assert res.verdicts.security == Verdict.UNKNOWN
    # 신뢰 출발점도 닿지 않으면 openapi도 못 읽는다 → 등록 경로 미수집
    assert evd.observations["routes_registered"].collected is False


@pytest.mark.parametrize("blocked, expected", [("refused", Verdict.PASS), ("unreachable", Verdict.PASS),
                                               ("dns_error", Verdict.UNKNOWN), ("error", Verdict.UNKNOWN)])
def test_blocked_outcome_kinds(tmp_path, blocked, expected):
    _, _, res = _run(tmp_path, World(exposed=False, blocked=blocked))
    assert res.verdicts.security == expected


def test_openapi_failure_is_unknown(tmp_path):
    _, evd, res = _run(tmp_path, World(exposed=False, openapi_ok=False))
    assert evd.observations["routes_registered"].collected is False
    assert res.verdicts.security == Verdict.UNKNOWN


def test_extra_registered_route_is_tested(tmp_path):
    routes = {**ENGINE_ROUTES, "/tokenize": "post"}
    _, evd, res = _run(tmp_path, World(exposed=False, engine_routes=routes))
    assert "/tokenize" in evd.observations["routes_tested"].value
    assert res.verdicts.security == Verdict.PASS


def test_mutating_route_not_sent_and_unknown(tmp_path):
    world = World(exposed=True, engine_routes={**ENGINE_ROUTES, "/sleep": "post"})
    store, evd, res = _run(tmp_path, world)
    assert ("POST", "/sleep") not in world.engine_hits  # 상태 변경 요청을 보내지 않았다
    assert "/sleep" in evd.observations["routes_registered"].value
    assert "/sleep" not in evd.observations["routes_tested"].value
    assert res.verdicts.security == Verdict.FAIL  # 다른 경로 직통 200은 그대로 FAIL
    world2 = World(exposed=False, engine_routes={**ENGINE_ROUTES, "/sleep": "post"})
    (tmp_path / "b").mkdir()
    _, _, res2 = _run(tmp_path / "b", world2)
    assert res2.verdicts.security == Verdict.UNKNOWN  # 미시험 경로가 있으면 PASS 아님


def test_allowed_key_direct_result_is_evidence_only(tmp_path):
    # 엔진이 자체 키로 Gateway 허용 키와 같은 값을 쓰는 경우: 허용 키 직통은 200, 무키·오키는 401
    store, evd, res = _run(tmp_path, World(exposed=True, engine_key=ALLOWED))
    assert set(evd.observations["direct_outcomes"].value.values()) == {401}
    assert res.verdicts.security == Verdict.PASS
    summary = json.loads(_log(store, evd).strip().splitlines()[-1])
    assert set(summary["direct_allowed_outcomes"].values()) == {200}
    assert ALLOWED not in _log(store, evd)


def test_missing_allowed_key_is_aborted_unknown(tmp_path):
    _, evd, res = _run(tmp_path, World(exposed=True), environ={})
    assert evd.execution.status == ExecStatus.ABORTED
    assert res.verdicts.security == Verdict.UNKNOWN


def test_non_lab_target_is_measured(tmp_path):
    _, evd, _ = _run(tmp_path, World(exposed=False), lab=False)
    assert evd.data_origin == DataOrigin.MEASURED


def test_representative_order():
    assert representative([401, "refused", 200]) == 200
    assert representative([401, 404]) == 404
    assert representative(["refused", "timeout"]) == "timeout"
    assert representative(["refused", "unreachable"]) == "unreachable"
    assert representative(["refused", 403]) == 403
    assert representative(["dns_error", 401]) == "dns_error"
    assert outcome_rank(204) < outcome_rank(500) < outcome_rank(401) < outcome_rank("timeout") < outcome_rank("refused")


def test_mask():
    assert mask({"a": ["x Bearer s3 y"], "b": 1}, ["s3"]) == {"a": ["x Bearer *** y"], "b": 1}


def test_engine_never_ready_is_aborted(tmp_path):
    class Dead(World):
        def handle(self, network, r):
            return {"id": r.get("id"), "method": r["method"], "url": r["url"], "outcome": "refused", "phase": "connect"}

    world = Dead(exposed=False)
    target = _target(tmp_path, False)
    t = json.loads(target.read_text()); t["engine"]["ready_timeout"] = 0.2
    target.write_text(json.dumps(t))
    impl = DirectRouteExecutor({"target": str(target), "allowed_key_env": "K"}, container_probe=world.container_probe,
                               host_probe=world.host_probe, environ={"K": ALLOWED})
    with pytest.raises(RuntimeError, match="not ready"):
        impl.run(baseline_pack().checks["REQ-01"], environment(), {}, __import__("guard.adapters.vllm", fromlist=["x"]).VllmAdapter(), tmp_path / "w")
