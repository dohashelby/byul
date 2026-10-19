"""REQ-03 role_route: 가상 Gateway(역할 인가)·엔진으로 select→run→judge. Docker·네트워크 없음."""

import json
from urllib.parse import urlsplit

import pytest

from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.fake import SCENARIOS_BY_CHECK
from guard.executors.role_route import RoleRouteExecutor
from guard.models import ExecStatus, Verdict
from guard.store import Store
from tests.builders import baseline_pack, environment, policy

GENERAL, ADMIN, WRONG = "general-key-111", "admin-key-222", "wrong-key-333"
ENGINE_ROUTES = {"/health": "get", "/metrics": "get", "/load": "get", "/v1/models": "get", "/v1/completions": "post",
                 "/is_scaling_elastic_ep": "post", "/scale_elastic_ep": "post"}
INFERENCE = {"/v1/models", "/v1/completions"}


class RoleWorld:
    """gateway: g:8080 (open: 유효 키면 전부 통과 / enforced: 일반 키는 추론 경로만), engine: e:8000."""

    def __init__(self, enforced: bool, routes=None, healthy=True, general_also=()):
        self.enforced = enforced
        self.general_also = set(general_also)  # enforced Gateway가 일반 키에 추가로 허용하는 경로
        self.routes = routes or ENGINE_ROUTES
        self.engine_calls = []
        self.healthy = healthy

    def engine(self, method, path, body):
        self.engine_calls.append((method, path, json.dumps(body, sort_keys=True) if body is not None else None))
        if path == "/openapi.json":
            return 200, json.dumps({"paths": {p: {m: {}} for p, m in self.routes.items()}})
        if path == "/health":
            return (200 if self.healthy else 503), ""
        if path == "/scale_elastic_ep":
            # 실제 v0.31.0 핸들러와 같은 동작: 본문 {}면 엔진 호출 없이 400
            if not (body or {}).get("new_data_parallel_size"):
                return 400, '{"error":{"message":"new_data_parallel_size is required","code":400}}'
            raise AssertionError("scale_elastic_ep executed with a real size")
        return (200, "{}") if path in self.routes else (404, "")

    def handle(self, network, r):
        u = urlsplit(r["url"])
        auth = (r.get("headers") or {}).get("Authorization")
        base = {"id": r.get("id"), "method": r["method"], "url": r["url"], "phase": "response"}
        if u.hostname == "g":
            role = {f"Bearer {GENERAL}": "general", f"Bearer {ADMIN}": "admin"}.get(auth)
            if role is None:
                return {**base, "outcome": 401}
            if self.enforced and role == "general" and u.path not in INFERENCE | self.general_also:
                return {**base, "outcome": 403}
        status, body = self.engine(r["method"], u.path, r.get("body"))
        return {**base, "outcome": status, "body": body if r.get("want_body") else None, "body_preview": body[:160]}

    def container_probe(self, network, requests, timeout):
        return [self.handle(network, r) for r in requests]


def _target(tmp_path):
    t = {"lab": False, "probe_image": "x", "origins": {"external": {"network": "ext"}, "trusted": {"network": "trusted"}},
         "gateway": {"url": "http://g:8080"}, "engine": {"trusted_url": "http://e:8000"}, "direct_targets": [],
         "gateway_known_routes": []}
    p = tmp_path / "target.json"
    p.write_text(json.dumps(t), encoding="utf-8")
    return p


def _run(tmp_path, world, version="0.31.0", general_allowed=("inference",)):
    cfg = {"target": str(_target(tmp_path)), "roles": {"general": "KG", "admin": "KA"}, "wrong_key_env": "KW"}
    environ = {"KG": GENERAL, "KA": ADMIN, "KW": WRONG}
    registry.EXECUTORS["role_route:sim"] = lambda raw: RoleRouteExecutor(cfg, container_probe=world.container_probe,
                                                                         environ=environ)
    try:
        store = Store(tmp_path / ".guard")
        pack = baseline_pack()
        env = environment({"gateway": {"value": True, "origin": "DECLARED", "source": "sim"}},
                          engine={"name": "vllm", "version": version}, model={"id": "facebook/opt-125m"})
        pol = policy(pack.ref(), scope={"include": ["REQ-03"], "exclude": []},
                     values={"REQ-03": {"general_allowed_kinds": list(general_allowed)}})
        store.put(env); store.put(pol)
        plan = select(pack, [env], pol); store.put(plan)
        (evd,) = run_plan(store, pack, plan, RunConfig(executors={"role_route": "sim"}, adapter="vllm"))
        (res,) = judge_plan(store, pack, plan, as_of=env.created_at)
        return store, evd, res
    finally:
        del registry.EXECUTORS["role_route:sim"]


def _summary(store, evd):
    return json.loads((store.root / evd.artifacts[0].path).read_text(encoding="utf-8").strip().splitlines()[-1])


def test_enforced_gateway_is_pass(tmp_path):
    store, evd, res = _run(tmp_path, RoleWorld(enforced=True))
    assert evd.execution.status == ExecStatus.COMPLETED
    assert res.verdicts.security == Verdict.PASS, res.reasons
    assert res.verdicts.functional == Verdict.PASS
    nonadmin = evd.observations["nonadmin_outcomes"].value
    assert set(nonadmin) == {"POST /is_scaling_elastic_ep", "GET /load", "GET /metrics", "POST /scale_elastic_ep"}  # 관리·진단만
    assert set(nonadmin.values()) <= {401, 403}
    s = _summary(store, evd)
    assert {p: r["general"] for p, r in s["outcomes_by_role"].items()} == {p: 403 for p in nonadmin}  # 일반 키는 403
    assert s["admin_outcomes"]["POST /scale_elastic_ep"] == "handler_reached"  # 관리 역할은 핸들러 도달(판정 미사용)
    assert s["safety"]["before"]["ok"] and s["safety"]["after"]["ok"]
    assert "api_router.py" in s["safe_probes"]["POST /scale_elastic_ep"]["source"]


def test_open_gateway_is_fail(tmp_path):
    store, evd, res = _run(tmp_path, RoleWorld(enforced=False))
    assert res.verdicts.security == Verdict.FAIL
    nonadmin = evd.observations["nonadmin_outcomes"].value
    assert nonadmin["GET /metrics"] == 200 and nonadmin["POST /scale_elastic_ep"] == "handler_reached"


def test_safe_probe_never_executes_scaling(tmp_path):
    world = RoleWorld(enforced=False)
    _run(tmp_path, world)
    scale = [c for c in world.engine_calls if c[1] == "/scale_elastic_ep"]
    assert scale and all(c == ("POST", "/scale_elastic_ep", "{}") for c in scale)


def test_unverified_engine_version_leaves_mutating_route_untested(tmp_path):
    world = RoleWorld(enforced=True)
    store, evd, res = _run(tmp_path, world, version="0.30.0")
    assert res.verdicts.security == Verdict.UNKNOWN  # 미시험 경로가 있으면 PASS 아님
    assert "POST /scale_elastic_ep" not in evd.observations["routes_tested"].value
    assert not any(c[1] == "/scale_elastic_ep" for c in world.engine_calls)
    assert "POST /scale_elastic_ep" in _summary(store, evd)["skipped_routes"]


def test_policy_allowed_kinds_are_excluded_from_scope(tmp_path):
    store, evd, res = _run(tmp_path, RoleWorld(enforced=True), general_allowed=("inference", "diagnostic"))
    assert set(evd.observations["routes_registered"].value) == {"POST /is_scaling_elastic_ep", "POST /scale_elastic_ep"}


def test_engine_unhealthy_after_probe_is_recorded(tmp_path):
    store, evd, res = _run(tmp_path, RoleWorld(enforced=True, healthy=False))
    s = _summary(store, evd)
    assert s["safety"]["before"]["ok"] is False and s["safety"]["before"]["health"] == 503


def test_keys_never_stored(tmp_path):
    store, evd, res = _run(tmp_path, RoleWorld(enforced=False))
    blob = "".join(p.read_bytes().decode("utf-8", "replace") for p in store.root.rglob("*") if p.is_file())
    assert GENERAL not in blob and ADMIN not in blob and WRONG not in blob


@pytest.mark.parametrize("scenario, expected", [("pass", Verdict.PASS), ("role_open", Verdict.FAIL),
                                                ("scale_handler_reached", Verdict.FAIL), ("mutating_untested", Verdict.UNKNOWN)])
def test_fake_scenarios(tmp_path, scenario, expected):
    assert set(SCENARIOS_BY_CHECK["REQ-03"]) == {"pass", "role_open", "scale_handler_reached", "mutating_untested"}
    store = Store(tmp_path / ".guard")
    pack = baseline_pack()
    env = environment({"gateway": {"value": True, "origin": "DECLARED", "source": "sim"}})
    pol = policy(pack.ref(), scope={"include": ["REQ-03"], "exclude": []}, values={"REQ-03": {"general_allowed_kinds": ["inference"]}})
    store.put(env); store.put(pol)
    plan = select(pack, [env], pol); store.put(plan)
    run_plan(store, pack, plan, RunConfig(executors={"role_route": "fake"}, raw={"fake": {"scenarios": {"REQ-03": scenario}}}))
    (res,) = judge_plan(store, pack, plan, as_of=env.created_at)
    assert res.verdicts.security == expected
    assert res.verification_level == "EXAMPLE"


def test_policy_allowed_kinds_change_the_verdict(tmp_path):
    """정책 값을 바꾸면 판정이 달라진다: Gateway가 일반 키에 진단 경로를 열어 둔 같은 구성에서
    정책이 진단을 허용하지 않으면 FAIL, 허용하면(진단은 범위 밖) PASS."""
    world = lambda: RoleWorld(enforced=True, general_also={"/metrics", "/load"})  # noqa: E731
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    _, _, strict = _run(tmp_path / "a", world(), general_allowed=("inference",))
    _, _, lenient = _run(tmp_path / "b", world(), general_allowed=("inference", "diagnostic"))
    assert strict.verdicts.security == Verdict.FAIL
    assert lenient.verdicts.security == Verdict.PASS
