"""HTTP 경계 점검 실행기들의 공통 부분(경로 수집·요청 모양·키·분류·기록·안전 확인). 판정하지 않는다(INV-14).

점검별 실행기(direct_route: REQ-01, role_route: REQ-03)는 이 모듈 위에서 "누가 어디로 무엇을 보내는지"만 정한다.
점검마다 다른 것은 CheckDef(route_scope, functional_probes)와 실행기 설정(roles)으로 받는다.

설정 공통 (guard.toml [<실행기>]):
    target = "path/to/target.json"           # 위상: URL·네트워크·출발점 (키 없음)
    roles = { allowed = "GUARD_LAB_ALLOWED_KEY" }   # 역할 이름 → 키 환경변수 이름 (값은 파일에 두지 않음)
    wrong_key_env = "GUARD_LAB_DENIED_KEY"   # 없으면 실행 중 임의 생성
    request_timeout = 3.0
    allow_mutating_routes = false             # 검증된 안전 프로브가 없는 상태 변경 경로에 보낼지
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from guard.adapters.base import EngineAdapter, ProbeRequest, Route
from guard.models import CheckDef, DataOrigin, Environment, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.docker import Docker, DockerError, Mount
from guard.runtime.comparison import execution_conditions

from . import http_probe
from .base import DraftArtifact, EvidenceDraft

ContainerProbe = Callable[[str, list[dict], float], list[dict]]  # network, requests, timeout → results
HostProbe = Callable[[dict, float], dict]

NO_RESPONSE = ("timeout", "unreachable")
TCP_BLOCKED = ("timeout", "refused", "unreachable")
HANDLER_REACHED = "handler_reached"  # 검증된 안전 프로브가 인가 없이 핸들러에 닿음(어댑터 표의 기대 응답과 일치)
MASK = "***"


def outcome_rank(outcome: Any) -> int:
    """작을수록 위험. 대표값은 가장 작은 것."""
    if outcome == HANDLER_REACHED:
        return 0
    if isinstance(outcome, int) and not isinstance(outcome, bool):
        if 200 <= outcome <= 299:
            return 0
        if outcome in (401, 403):
            return 2
        return 1
    if outcome in NO_RESPONSE:
        return 3
    if outcome == "refused":
        return 4
    return 1  # dns_error, error, 그 밖


def representative(outcomes: list[Any]) -> Any:
    return min(outcomes, key=outcome_rank) if outcomes else None


def mask(value: Any, secrets_: list[str]) -> Any:
    if isinstance(value, str):
        for s in secrets_:
            if s:
                value = value.replace(s, MASK)
        return value
    if isinstance(value, dict):
        return {k: mask(v, secrets_) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v, secrets_) for v in value]
    return value


def fill_model(body: Any, model: str | None) -> Any:
    """본문 문자열의 "{model}"을 ENV 모델 이름으로 바꾼다(없으면 그대로)."""
    if isinstance(body, str):
        return body.replace("{model}", model) if model else body
    if isinstance(body, dict):
        return {k: fill_model(v, model) for k, v in body.items()}
    if isinstance(body, list):
        return [fill_model(v, model) for v in body]
    return body


def route_key(route: Route) -> str:
    """등록·시험·관측의 키: "METHOD path" (같은 URL의 메서드를 따로 시험·관측한다)."""
    return f"{route.method.upper()} {route.path}"


def classify(res: dict, pr: ProbeRequest | None) -> Any:
    """응답을 판정용 값으로. 검증된 안전 프로브의 기대 응답과 일치하면 handler_reached."""
    o = res.get("outcome")
    exp = pr.expected_handler_reached if pr is not None else None
    if exp and o == exp["status"] and exp["body_contains"] in (res.get("body") or res.get("body_preview") or ""):
        return HANDLER_REACHED
    return o


def docker_container_probe(docker: Docker, image: str) -> ContainerProbe:
    script = Path(http_probe.__file__).resolve()
    counter = {"n": 0}

    def run(network: str, requests: list[dict], timeout: float) -> list[dict]:
        counter["n"] += 1
        res = docker.run_once(
            image, f"probe{counter['n']}", network=network, timeout=timeout * max(len(requests), 1) + 60,
            mounts=[Mount(script, "/probe/http_probe.py")], command=["python", "/probe/http_probe.py"],
            input=json.dumps({"timeout": timeout, "requests": requests}).encode(),  # 키는 stdin으로만
        )
        if res.returncode != 0:
            raise DockerError(f"probe container exited {res.returncode}: {res.stderr.strip()[:300]}")
        return json.loads(res.stdout)

    return run


@dataclass
class Ctx:
    check: CheckDef
    env: Environment
    adapter: EngineAdapter
    target: dict
    policy_values: dict
    timeout: float
    allow_mutating: bool
    keys: dict[str, str | None]
    secrets: list[str]
    cprobe: ContainerProbe
    workdir: Path
    started: Any
    log: list[dict] = field(default_factory=list)

    @property
    def trusted_net(self) -> str:
        return self.target["origins"]["trusted"]["network"]

    @property
    def external_net(self) -> str:
        return self.target["origins"]["external"]["network"]

    @property
    def engine_url(self) -> str:
        return self.target["engine"]["trusted_url"]

    @property
    def gateway_url(self) -> str:
        return self.target["gateway"]["url"]

    def record(self, origin: str, via: str, route: str, key_label: str, res: dict, basis: str = "http",
               outcome: Any = None) -> None:
        entry = {k: res.get(k) for k in ("outcome", "phase", "detail", "elapsed_ms", "body_bytes", "body_preview")}
        if outcome is not None and outcome != entry["outcome"]:
            entry["classified"] = outcome
        self.log.append(mask({"origin": origin, "via": via, "route": route, "key": key_label,
                              "method": res.get("method"), "basis": basis, **entry}, self.secrets))

    def request(self, rid: str, base: str, pr: ProbeRequest, key: str | None, want_body: bool = False) -> dict:
        headers = {**pr.headers, **({"Authorization": f"Bearer {key}"} if key else {})}
        return {"id": rid, "method": pr.method, "url": f"{base}{pr.path}", "headers": headers, "body": pr.body,
                "want_body": want_body or pr.expected_handler_reached is not None}


class HttpCheckExecutor:
    """공통 실행 골격. 하위 클래스는 collect(ctx) → (observations, normal_control, challenges, summary)를 구현한다."""

    name = "http_check"
    version = "0.2.0"
    requires_roles = True        # 역할 키가 필요한가(직통·역할 점검). 컨테이너 설정 점검은 아님
    requires_route_scope = True  # CheckDef.route_scope가 필요한가
    functional_via = "gateway"   # 이 실행기가 보내는 기능 표본의 경로

    def __init__(self, cfg: dict, container_probe: ContainerProbe | None = None, host_probe: HostProbe = http_probe.probe,
                 environ: dict[str, str] | None = None):
        self.cfg = cfg
        self._container_probe = container_probe
        self._host_probe = host_probe
        self._environ = os.environ if environ is None else environ

    # --- 키 ----------------------------------------------------------------------

    def roles(self) -> dict[str, str]:
        roles = dict(self.cfg.get("roles") or {})
        if not roles and self.cfg.get("allowed_key_env"):
            roles = {"allowed": self.cfg["allowed_key_env"]}  # 5a·5b 설정 호환
        if not roles and self.requires_roles:
            raise RuntimeError(f"{self.name}: no roles configured")
        return roles

    def keys(self) -> dict[str, str | None]:
        keys: dict[str, str | None] = {"none": None}
        for role, env_name in self.roles().items():
            v = self._environ.get(env_name)
            if not v:
                raise RuntimeError(f"key env {env_name!r} for role {role!r} is not set")
            keys[role] = v
        wrong_env = self.cfg.get("wrong_key_env") or self.cfg.get("denied_key_env")
        keys["wrong"] = (self._environ.get(wrong_env) if wrong_env else None) or secrets.token_urlsafe(24)
        values = [v for v in keys.values() if v]
        if len(values) != len(set(values)):
            raise RuntimeError("role keys and the wrong key must all differ")
        return keys

    # --- 실행 골격 -----------------------------------------------------------------

    def run(self, check: CheckDef, env: Environment, policy_values: dict, adapter: EngineAdapter | None,
            workdir: Path) -> EvidenceDraft:
        if adapter is None:
            raise RuntimeError(f"{self.name} needs an engine adapter")
        if self.requires_route_scope and check.route_scope is None:
            raise RuntimeError(f"{check.id}: route_scope is required for {self.name}")
        self.validate_policy(check, policy_values)  # 시험 시작 전(요청 0건)
        target = json.loads(Path(self.cfg["target"]).read_text(encoding="utf-8"))
        keys = self.keys()

        def make_ctx(cprobe: ContainerProbe) -> Ctx:
            return Ctx(check=check, env=env, adapter=adapter, target=target, policy_values=policy_values,
                       timeout=float(self.cfg.get("request_timeout", 3.0)),
                       allow_mutating=bool(self.cfg.get("allow_mutating_routes", False)), keys=keys,
                       secrets=[v for v in keys.values() if v], cprobe=cprobe, workdir=workdir, started=utcnow())

        if self._container_probe is not None:
            wait_engine(target, self._container_probe, alive=None)
            return self._finish(make_ctx(self._container_probe))
        docker = Docker(f"x{secrets.token_hex(4)}")
        try:
            cprobe = docker_container_probe(docker, target["probe_image"])
            container = target["engine"].get("container")
            alive = (lambda: docker.is_running(container, timeout=30)) if container else None
            wait_engine(target, cprobe, alive)
            return self._finish(make_ctx(cprobe))
        finally:
            if docker.cleanup(timeout=60):
                raise RuntimeError("probe resource cleanup incomplete")

    def validate_policy(self, check: CheckDef, policy_values: dict) -> None:
        """시험 시작 전 정책 값과 실행기 설정의 일치 확인. 불일치면 예외(→ ABORTED). 기본은 확인 없음."""

    def collect(self, ctx: Ctx) -> tuple[dict, dict, list[dict], dict]:  # pragma: no cover - 하위 클래스
        raise NotImplementedError

    def _finish(self, ctx: Ctx) -> EvidenceDraft:
        observations, normal, challenges, summary = self.collect(ctx)
        for o in observations.values():
            if not o["collected"]:
                o["value"] = None
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        summary = mask({"summary": True, "topology": ctx.target.get("topology"), "lab": bool(ctx.target.get("lab")),
                        **summary}, ctx.secrets)
        log_path = ctx.workdir / "probe_log.jsonl"
        log_path.write_text("".join(json.dumps(x, ensure_ascii=False, default=str) + "\n" for x in [*ctx.log, summary]),
                            encoding="utf-8")
        fixture = summary.get("fixture", {})
        config = mask(execution_conditions(ctx.target, {
            **self.cfg, "request_timeout": ctx.timeout, "allow_mutating_routes": ctx.allow_mutating,
        }, list(self.roles())), ctx.secrets)
        return EvidenceDraft(
            status=ExecStatus.COMPLETED,
            # 실험실(stub) 대상은 EXAMPLE. 실제 엔진 대상만 MEASURED (DECISIONS 5a/5b)
            data_origin=DataOrigin.EXAMPLE if ctx.target.get("lab") else DataOrigin.MEASURED,
            normal_control=normal,
            challenges=challenges,
            observations=observations,
            artifacts=[DraftArtifact(name="probe_log.jsonl", path=log_path, collector=f"{self.name}/{self.version}")],
            comparability={"fixture_sha256": sha256_hex(canonical_json(fixture)),
                           "config_sha256": sha256_hex(canonical_json(config)) if ctx.target.get("comparison_config") else None,
                           "model_inventory_sha256": ctx.env.model.inventory_sha256, "gpu": ctx.env.runtime.gpu_model,
                           "driver": ctx.env.runtime.driver, "cpu": ctx.env.runtime.cpu},
            config=config,
            started_at=ctx.started,
            finished_at=utcnow(),
        )


# --- 공통 단계 ---------------------------------------------------------------------


def wait_engine(target: dict, cprobe: ContainerProbe, alive) -> None:
    """신뢰 출발점에서 엔진 /health 200을 기다린다. 상한(ready_timeout)이 지나거나 엔진이 죽으면 예외(→ ABORTED)."""
    deadline = target["engine"].get("ready_timeout")
    if not deadline:
        return
    net = target["origins"]["trusted"]["network"]
    url = f"{target['engine']['trusted_url']}/health"
    end = time.monotonic() + float(deadline)
    last = None
    while time.monotonic() < end:
        (res,) = cprobe(net, [{"id": "ready", "method": "GET", "url": url}], 2.0)
        last = res.get("outcome")
        if last == 200:
            return
        if alive is not None and not alive():
            raise RuntimeError(f"engine container exited before ready (last /health: {last})")
        time.sleep(2)
    raise RuntimeError(f"engine not ready within {deadline}s (last /health: {last})")


@dataclass
class RouteSet:
    routes: dict[str, Route]  # "METHOD path" → Route
    registered: list[str]
    collected: bool
    openapi_sha256: str | None
    openapi_outcome: Any


def discover_routes(ctx: Ctx) -> RouteSet:
    """신뢰 출발점 → 엔진 /openapi.json ∪ Gateway 설정의 알려진 경로. CheckDef.route_scope의 종류만, 정책이 뺀 종류 제외."""
    scope = ctx.check.route_scope
    include = set(scope.include_kinds)
    if scope.exclude_kinds_policy:
        include -= set(ctx.policy_values.get(scope.exclude_kinds_policy) or [])
    (doc_res,) = ctx.cprobe(ctx.trusted_net, [{"id": "openapi", "method": "GET", "url": f"{ctx.engine_url}/openapi.json",
                                               "want_body": True}], ctx.timeout)
    ctx.record("trusted", "engine", "/openapi.json", "none", doc_res)
    routes: dict[str, Route] = {}
    collected, sha = False, None
    if doc_res.get("outcome") == 200:
        try:
            doc = json.loads(doc_res.get("body") or "")
            sha = sha256_hex(canonical_json(doc))
            for r in ctx.adapter.routes_from_openapi(doc):
                if r.kind in include:
                    routes.setdefault(route_key(r), r)
            collected = True
        except ValueError:
            pass
    for known in ctx.target.get("gateway_known_routes", []):  # [{method, path}]
        for r in ctx.adapter.routes_from_openapi({"paths": {known["path"]: {known["method"].lower(): {}}}}):
            if r.kind in include:
                routes.setdefault(route_key(r), r)
    return RouteSet(routes, sorted(routes), collected, sha, doc_res.get("outcome"))


def probe_templates(ctx: Ctx, routes: dict[str, Route]) -> tuple[dict[str, ProbeRequest], dict[str, str]]:
    """"METHOD path" → 시험 요청(어댑터가 정함). 검증된 안전 프로브가 없는 상태 변경 경로는 보내지 않는다."""
    templates: dict[str, ProbeRequest] = {}
    skipped: dict[str, str] = {}
    model, version = ctx.env.model.id, ctx.env.engine.version
    for key, route in routes.items():
        pr = ctx.adapter.probe_request(route, model, version)
        if pr is None and route.mutating and ctx.allow_mutating:
            pr = ctx.adapter.probe_request(Route(route.method, route.path, route.inference, route.kind, False), model, version)
        if pr is None:
            skipped[key] = "mutating route without a verified safe probe for this engine version: not sent"
        elif pr.method.upper() != route.method.upper():
            raise RuntimeError(f"{key}: adapter probe uses method {pr.method} (must match the registered method)")
        else:
            templates[key] = pr
    return templates, skipped


def functional_requests(ctx: Ctx, via: str = "gateway") -> tuple[list[dict], dict[str, Any]]:
    """CheckDef.functional_probes → (요청 목록, id→probe). gateway: 역할(as_role) 키로 Gateway 경유(외부 출발점),
    engine: 신뢰 출발점에서 엔진으로 직접(키는 역할이 설정돼 있을 때만). 실행기가 다룰 수 없는 경로면 거부."""
    probes = {fp.id: fp for fp in ctx.check.functional_probes}
    reqs = []
    for pid, fp in probes.items():
        if fp.via != via:
            raise RuntimeError(f"functional probe {pid}: via={fp.via} is not supported by this executor (via={via})")
        pr = ProbeRequest(fp.method.upper(), fp.path, fill_model(fp.body, ctx.env.model.id))
        if via == "gateway":
            if fp.as_role not in ctx.keys:
                raise RuntimeError(f"functional probe {pid}: role {fp.as_role!r} not configured")
            reqs.append(ctx.request(f"func|{pid}", ctx.gateway_url, pr, ctx.keys[fp.as_role]))
        else:
            reqs.append(ctx.request(f"func|{pid}", ctx.engine_url, pr, ctx.keys.get(fp.as_role)))
    return reqs, probes


def functional_results(probes: dict[str, Any], results: dict[str, dict]) -> tuple[dict[str, str], dict[str, Any], dict]:
    functional: dict[str, str] = {}
    status: dict[str, Any] = {}
    for pid, fp in probes.items():
        o = (results.get(pid) or {}).get("outcome")
        status[pid] = o
        if isinstance(o, int) and not isinstance(o, bool):
            functional[pid] = "pass" if o in fp.expect else "fail"
        else:
            functional[pid] = "no_response"
    normal = {
        "attempted": bool(probes),
        "succeeded": (all(v == "pass" for v in functional.values()) and len(functional) == len(probes)) if probes else None,
        "detail": "gateway functional probes: " + ", ".join(f"{pid} [{probes[pid].as_role}] → {status.get(pid)}" for pid in probes),
    }
    return functional, status, normal


def safety_check(ctx: Ctx, label: str) -> dict:
    """안전 프로브 전후 엔진 생존(/health)과 기능 표본 1회(첫 항목)."""
    (health,) = ctx.cprobe(ctx.trusted_net, [{"id": f"safety-{label}", "method": "GET", "url": f"{ctx.engine_url}/health"}],
                           ctx.timeout)
    ctx.record("trusted", f"safety-{label}", "/health", "none", health)
    out = {"health": health.get("outcome")}
    if ctx.check.functional_probes:
        reqs, probes = functional_requests(ctx)
        (res,) = ctx.cprobe(ctx.external_net, reqs[:1], max(ctx.timeout, 30.0))
        pid = next(iter(probes))
        ctx.record("external", f"safety-{label}", pid, probes[pid].as_role, res)
        out["inference"] = res.get("outcome")
        out["inference_ok"] = res.get("outcome") in probes[pid].expect
    out["ok"] = out["health"] == 200 and out.get("inference_ok", True)
    return out


def fixture_of(templates: dict[str, ProbeRequest]) -> dict:
    return {k: {"method": t.method, "path": t.path, "body": t.body, "basis": t.basis} for k, t in sorted(templates.items())}
