"""같은 URL에 메서드가 여럿 등록된 경우: 등록·시험·관측 키는 "METHOD path"여야 한다 (Step 1, 외부 검토).

재현: GET /multi → 401, POST /multi → 200 인 엔진에서 GET만 시험하고 PASS가 나오면 안 된다.
"""

import json
from urllib.parse import urlsplit

from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.direct_route import DirectRouteExecutor
from guard.executors.role_route import RoleRouteExecutor
from guard.models import Verdict
from guard.store import Store
from tests.builders import baseline_pack, environment, policy

ALLOWED, GENERAL, ADMIN, WRONG = "allowed-k1", "general-k2", "admin-k3", "wrong-k4"


class MultiWorld:
    """엔진: /multi는 GET·POST 둘 다 등록. 직통 GET은 401, 직통 POST는 200(인가 누락)."""

    def __init__(self, exposed=True, gateway_post_open=False):
        self.exposed = exposed
        self.gateway_post_open = gateway_post_open  # REQ-03: Gateway가 GET만 막고 POST는 통과시키는 결함
        self.calls = []

    def openapi(self, extra):
        return {"paths": {"/multi": {"get": {}, "post": {}}, "/v1/completions": {"post": {}}, **extra}}

    def engine(self, method, path, auth, extra):
        self.calls.append((method, path))
        if path == "/openapi.json":
            return 200, json.dumps(self.openapi(extra))
        if path == "/health":
            return 200, ""
        if path == "/multi":
            return (401, "") if method == "GET" else (200, "{}")
        if path in extra:
            return 200, "{}"
        if path == "/v1/completions":  # 엔진 자체 키: 허용 키(또는 Gateway 경유 역할 키)만 200
            return (200, "{}") if auth in (f"Bearer {ALLOWED}", f"Bearer {GENERAL}", f"Bearer {ADMIN}") else (401, "")
        return 404, ""

    def handle(self, network, r, extra=None):
        extra = extra or {}
        u = urlsplit(r["url"])
        auth = (r.get("headers") or {}).get("Authorization")
        base = {"id": r.get("id"), "method": r["method"], "url": r["url"], "phase": "response"}
        if r.get("mode") == "tcp":
            if network == "trusted" or self.exposed:
                return {**base, "outcome": "connected", "phase": "connect"}
            return {**base, "outcome": "timeout", "phase": "connect"}
        if u.hostname == "g":
            role = {f"Bearer {ALLOWED}": "allowed", f"Bearer {GENERAL}": "general", f"Bearer {ADMIN}": "admin"}.get(auth)
            if role is None:
                return {**base, "outcome": 401}
            if role == "general" and u.path in extra and not (self.gateway_post_open and r["method"] == "POST"):
                return {**base, "outcome": 403}
        elif network != "trusted" and not self.exposed:
            return {**base, "outcome": "timeout", "phase": "connect"}
        status, body = self.engine(r["method"], u.path, auth, extra)
        return {**base, "outcome": status, "body": body if r.get("want_body") else None, "body_preview": body[:160]}


def _target(tmp_path, exposed):
    t = {"lab": False, "probe_image": "x", "origins": {"external": {"network": "ext"}, "trusted": {"network": "trusted"}},
         "gateway": {"url": "http://g:8080"}, "engine": {"trusted_url": "http://e:8000"},
         "direct_targets": [{"origin": "external", "url": "http://e:8000"}] if exposed is not None else [],
         "gateway_known_routes": []}
    p = tmp_path / "target.json"
    p.write_text(json.dumps(t), encoding="utf-8")
    return p


def _run(tmp_path, check_id, executor_key, make_executor, values):
    registry.EXECUTORS[executor_key] = make_executor
    try:
        store = Store(tmp_path / ".guard")
        pack = baseline_pack()
        env = environment({"gateway": {"value": True, "origin": "DECLARED", "source": "sim"}},
                          engine={"name": "vllm", "version": "0.31.0"}, model={"id": "m"})
        pol = policy(pack.ref(), scope={"include": [check_id], "exclude": []}, values=values)
        store.put(env); store.put(pol)
        plan = select(pack, [env], pol); store.put(plan)
        name = executor_key.split(":")[0]
        (evd,) = run_plan(store, pack, plan, RunConfig(executors={name: executor_key.split(":")[1]}, adapter="vllm"))
        (res,) = judge_plan(store, pack, plan, as_of=env.created_at)
        return store, evd, res
    finally:
        del registry.EXECUTORS[executor_key]


def _req01(tmp_path, world):
    cfg = {"target": str(_target(tmp_path, world.exposed)), "roles": {"allowed": "KA"}, "wrong_key_env": "KW"}
    make = lambda raw: DirectRouteExecutor(cfg, container_probe=lambda n, rs, t: [world.handle(n, r) for r in rs],  # noqa: E731
                                           host_probe=lambda r, t: world.handle("host", r),
                                           environ={"KA": ALLOWED, "KW": WRONG})
    return _run(tmp_path, "REQ-01", "direct_route:sim", make, {"REQ-01": {"allowed_callers": ["allowed"]}})


def test_req01_post_only_violation_is_not_pass(tmp_path):
    """재현: POST /multi 직통 200인데 GET만 보고 PASS가 나오던 결함."""
    _, evd, res = _req01(tmp_path, MultiWorld(exposed=True))
    assert res.verdicts.security == Verdict.FAIL, res.reasons


def test_req01_both_methods_registered_tested_observed(tmp_path):
    _, evd, _ = _req01(tmp_path, MultiWorld(exposed=True))
    reg = evd.observations["routes_registered"].value
    assert "GET /multi" in reg and "POST /multi" in reg
    assert {"GET /multi", "POST /multi"} <= set(evd.observations["routes_tested"].value)
    out = evd.observations["direct_outcomes"].value
    assert out["GET /multi"] == 401 and out["POST /multi"] == 200


def test_req01_tcp_inference_basis_recorded_per_method(tmp_path):
    store, evd, res = _req01(tmp_path, MultiWorld(exposed=False))
    assert res.verdicts.security == Verdict.PASS, res.reasons
    summary = json.loads((store.root / evd.artifacts[0].path).read_text(encoding="utf-8").strip().splitlines()[-1])
    assert summary["direct_basis"]["GET /multi"]["none"] == ["external:tcp_connect"]
    assert summary["direct_basis"]["POST /multi"]["none"] == ["external:tcp_connect"]
    assert evd.observations["direct_outcomes"].value["POST /multi"] == "timeout"


def test_req03_post_only_gateway_gap_is_not_pass(tmp_path):
    """재현(REQ-03): Gateway가 같은 진단 경로의 GET만 막고 POST는 일반 키로 통과시킴."""
    world = MultiWorld(exposed=None, gateway_post_open=True)
    extra = {"/metrics": {"get": {}, "post": {}}}
    world.openapi = lambda e, _extra=extra: {"paths": {"/v1/completions": {"post": {}}, **_extra}}
    handle = world.handle
    world.handle = lambda n, r, _h=handle: _h(n, r, extra)
    cfg = {"target": str(_target(tmp_path, None)), "roles": {"general": "KG", "admin": "KD"}, "wrong_key_env": "KW"}
    make = lambda raw: RoleRouteExecutor(cfg, container_probe=lambda n, rs, t: [world.handle(n, r) for r in rs],  # noqa: E731
                                         environ={"KG": GENERAL, "KD": ADMIN, "KW": WRONG})
    _, evd, res = _run(tmp_path, "REQ-03", "role_route:sim", make, {"REQ-03": {"general_allowed_kinds": ["inference"]}})
    assert res.verdicts.security == Verdict.FAIL, res.reasons
    nonadmin = evd.observations["nonadmin_outcomes"].value
    assert nonadmin["GET /metrics"] in (401, 403) and nonadmin["POST /metrics"] == 200
