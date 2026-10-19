"""direct_route 실행기: Gateway 우회 직통 요청 시험 (REQ-01). 판정하지 않는다(INV-14).

순서
1. 신뢰 출발점에서 엔진 /openapi.json → 등록 경로(어댑터가 분류, 정보성 제외) ∪ Gateway 설정의 알려진 경로
2. 외부 호출자 → Gateway, 허용 키로 정상 대조
3. 경로마다 키 없음·잘못된 키·허용 키로 Gateway와 직통(외부 컨테이너, 공개됐으면 호스트) 비교
4. 결과 분류: 상태코드 | refused | timeout | unreachable | dns_error | error (http_probe)
5. 신뢰 출발점 → 엔진 도달 (양성 대조), 3번 직후
6. 요청/응답 요약을 probe_log.jsonl 증거 파일로. 키 값은 마스킹, 헤더는 기록하지 않음

경로별 대표 직통 결과(direct_outcomes)는 "키 없음"·"잘못된 키"로만 만든다:
2xx > 그 밖의 코드·dns_error·error > 401/403 > timeout/unreachable > refused.
허용 키 직통 결과는 direct_allowed_outcomes로 증거 파일에만 남긴다(판정에 쓰지 않음).

설정 (guard.toml [direct_route]):
    target = "path/to/target.json"     # 위상: URL·네트워크·출발점 (키 없음)
    allowed_key_env = "GUARD_LAB_ALLOWED_KEY"
    denied_key_env = "GUARD_LAB_DENIED_KEY"
    request_timeout = 3.0
    allow_mutating_routes = false     # 상태 변경 관리 경로에 직통 요청을 보낼지 (기본: 보내지 않음 → 미시험)
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Callable

from guard.adapters.base import EngineAdapter, Route
from guard.models import CheckDef, DataOrigin, Environment, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.docker import Docker, DockerError, Mount

from . import http_probe
from .base import DraftArtifact, EvidenceDraft

ContainerProbe = Callable[[str, list[dict], float], list[dict]]  # network, requests, timeout → results
HostProbe = Callable[[dict, float], dict]

NO_RESPONSE = ("timeout", "unreachable")
KEY_VARIANTS = ("none", "wrong", "allowed")
MASK = "***"


def outcome_rank(outcome: Any) -> int:
    """작을수록 위험. 대표값은 가장 작은 것."""
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


class DirectRouteExecutor:
    name = "direct_route"
    version = "0.1.0"

    def __init__(self, cfg: dict, container_probe: ContainerProbe | None = None, host_probe: HostProbe = http_probe.probe,
                 environ: dict[str, str] | None = None):
        self.cfg = cfg
        self._container_probe = container_probe
        self._host_probe = host_probe
        self._environ = os.environ if environ is None else environ

    # --- 키 ----------------------------------------------------------------------

    def _keys(self) -> dict[str, str | None]:
        allowed = self._environ.get(self.cfg.get("allowed_key_env", "GUARD_LAB_ALLOWED_KEY"))
        if not allowed:
            raise RuntimeError(f"allowed key env {self.cfg.get('allowed_key_env')!r} is not set")
        wrong = self._environ.get(self.cfg.get("denied_key_env", "GUARD_LAB_DENIED_KEY")) or secrets.token_urlsafe(24)
        if wrong == allowed:
            raise RuntimeError("denied key must differ from allowed key")
        return {"none": None, "wrong": wrong, "allowed": allowed}

    @staticmethod
    def _headers(key: str | None) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}"} if key else {}

    # --- 실행 --------------------------------------------------------------------

    def run(self, check: CheckDef, env: Environment, policy_values: dict, adapter: EngineAdapter | None,
            workdir: Path) -> EvidenceDraft:
        if adapter is None:
            raise RuntimeError("direct_route needs an engine adapter")
        target = json.loads(Path(self.cfg["target"]).read_text(encoding="utf-8"))
        timeout = float(self.cfg.get("request_timeout", 3.0))
        allow_mutating = bool(self.cfg.get("allow_mutating_routes", False))
        keys = self._keys()
        secret_values = [v for v in keys.values() if v]
        started = utcnow()

        if self._container_probe is not None:
            self._wait_engine(target, self._container_probe, alive=None)
            return self._run(check, env, adapter, target, timeout, allow_mutating, keys, secret_values, workdir,
                             self._container_probe, started)
        docker = Docker(f"x{secrets.token_hex(4)}")
        try:
            cprobe = docker_container_probe(docker, target["probe_image"])
            container = target["engine"].get("container")
            alive = (lambda: docker.is_running(container, timeout=30)) if container else None
            self._wait_engine(target, cprobe, alive)
            return self._run(check, env, adapter, target, timeout, allow_mutating, keys, secret_values, workdir,
                             cprobe, started)
        finally:
            docker.cleanup(timeout=60)

    @staticmethod
    def _wait_engine(target: dict, cprobe: ContainerProbe, alive) -> None:
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

    def _run(self, check, env, adapter, target, timeout, allow_mutating, keys, secret_values, workdir,
             cprobe: ContainerProbe, started) -> EvidenceDraft:
        log: list[dict] = []
        trusted_net = target["origins"]["trusted"]["network"]
        external_net = target["origins"]["external"]["network"]
        engine_url = target["engine"]["trusted_url"]
        gateway_url = target["gateway"]["url"]
        model = env.model.id

        def record(origin: str, via: str, route: str, key_label: str, res: dict) -> None:
            entry = {k: res.get(k) for k in ("outcome", "phase", "detail", "elapsed_ms", "body_bytes", "body_preview")}
            log.append(mask({"origin": origin, "via": via, "route": route, "key": key_label, "method": res.get("method"),
                             **entry}, secret_values))

        # 1. 등록 경로 (신뢰 출발점 → 엔진 /openapi.json)
        (doc_res,) = cprobe(trusted_net, [{"id": "openapi", "method": "GET", "url": f"{engine_url}/openapi.json",
                                           "want_body": True}], timeout)
        record("trusted", "engine", "/openapi.json", "none", doc_res)
        routes: dict[str, Route] = {}
        registered_collected = False
        openapi_sha = None
        if doc_res.get("outcome") == 200:
            try:
                doc = json.loads(doc_res.get("body") or "")
                openapi_sha = sha256_hex(canonical_json(doc))
                for r in adapter.routes_from_openapi(doc):
                    if r.kind != "info":
                        routes.setdefault(r.path, r)
                registered_collected = True
            except ValueError:
                pass
        for known in target.get("gateway_known_routes", []):  # [{method, path}] Gateway 설정에서 알려진 경로
            for r in adapter.routes_from_openapi({"paths": {known["path"]: {known["method"].lower(): {}}}}):
                if r.kind != "info":
                    routes.setdefault(r.path, r)
        registered = sorted(routes)

        # 시험 요청 목록
        templates: dict[str, Any] = {}
        skipped: dict[str, str] = {}
        for path, route in routes.items():
            pr = adapter.probe_request(route, model)
            if pr is None and route.mutating and allow_mutating:
                pr = adapter.probe_request(Route(route.method, route.path, route.inference, route.kind, False), model)
            if pr is None:
                skipped[path] = "mutating admin route not sent (allow_mutating_routes=false)"
            else:
                templates[path] = pr

        def req(rid: str, base: str, path: str, key: str | None) -> dict:
            pr = templates[path]
            return {"id": rid, "method": pr.method, "url": f"{base}{pr.path}", "headers": self._headers(key), "body": pr.body}

        # 2·3. 외부 호출자: Gateway(키 3종) + 직통(키 3종)
        ext_requests, index = [], []
        for path in templates:
            for label in KEY_VARIANTS:
                ext_requests.append(req(f"gw|{path}|{label}", gateway_url, path, keys[label]))
                index.append(("gateway", "external", path, label))
            for t in target["direct_targets"]:
                if t["origin"] != "external":
                    continue
                for label in KEY_VARIANTS:
                    ext_requests.append(req(f"direct|{path}|{label}", t["url"], path, keys[label]))
                    index.append(("direct", "external", path, label))
        ext_results = cprobe(external_net, ext_requests, timeout) if ext_requests else []
        results = list(zip(index, ext_results))
        for t in target["direct_targets"]:
            if t["origin"] != "host":
                continue
            for path in templates:
                for label in KEY_VARIANTS:
                    r = self._host_probe(req(f"host|{path}|{label}", t["url"], path, keys[label]), timeout)
                    results.append((("direct", "host", path, label), r))
        for (via, origin, path, label), res in results:
            record(origin, via, path, label, res)

        # 5. 양성 대조: 신뢰 출발점 → 엔진 (직통 시험 직후)
        (pos,) = cprobe(trusted_net, [{"id": "positive", "method": "GET", "url": f"{engine_url}/health"}], timeout)
        record("trusted", "engine", "/health", "none", pos)
        trusted_reachable = isinstance(pos.get("outcome"), int) and not isinstance(pos.get("outcome"), bool)

        # 집계
        direct_denied: dict[str, list] = {p: [] for p in templates}
        direct_allowed: dict[str, list] = {p: [] for p in templates}
        gateway_allowed: dict[str, Any] = {}
        gateway_denied: dict[str, dict] = {p: {} for p in templates}
        for (via, origin, path, label), res in results:
            o = res.get("outcome")
            if via == "direct":
                (direct_allowed if label == "allowed" else direct_denied)[path].append(o)
            elif label == "allowed":
                gateway_allowed[path] = o
            else:
                gateway_denied[path][label] = o
        direct_outcomes = {p: representative(v) for p, v in direct_denied.items() if v}
        direct_allowed_outcomes = {p: representative(v) for p, v in direct_allowed.items() if v}
        routes_tested = sorted(p for p in templates if direct_denied[p] and p in gateway_allowed)
        gateway_outcomes = {p: o for p, o in gateway_allowed.items() if isinstance(o, int)}

        normal_route = "/v1/completions" if "/v1/completions" in templates else (sorted(templates)[0] if templates else None)
        normal_status = gateway_allowed.get(normal_route) if normal_route else None
        normal = {
            "attempted": normal_route is not None,
            "succeeded": (normal_status == 200) if normal_route else None,
            "detail": f"allowed key via gateway {normal_route} → {normal_status}",
        }

        has_direct_targets = bool(target["direct_targets"])
        observations = {
            "routes_registered": {"value": registered if registered_collected else None, "collected": registered_collected},
            "routes_tested": {"value": routes_tested, "collected": True},
            "direct_outcomes": {"value": direct_outcomes, "collected": has_direct_targets and bool(direct_outcomes)},
            "gateway_outcomes": {"value": gateway_outcomes, "collected": bool(gateway_outcomes)},
            "trusted_origin_reachable": {"value": trusted_reachable, "collected": True},
        }
        for k, o in observations.items():
            if not o["collected"]:
                o["value"] = None

        steps = check.challenge_steps
        challenges = [
            {"step": steps[0], "attempted": True,
             "observations": {"openapi_outcome": doc_res.get("outcome"), "routes": registered, "skipped": skipped}},
            {"step": steps[1], "attempted": bool(ext_requests) and has_direct_targets,
             "observations": {"direct_outcomes": direct_outcomes, "gateway_denied": gateway_denied}},
            {"step": steps[2], "attempted": bool(ext_requests),
             "observations": {"gateway_outcomes": gateway_outcomes, "trusted_origin_reachable": trusted_reachable}},
        ] if len(steps) == 3 else [{"step": s, "attempted": bool(ext_requests)} for s in steps]

        # 6. 증거 파일
        workdir.mkdir(parents=True, exist_ok=True)
        summary = mask({
            "summary": True, "topology": target.get("topology"), "lab": bool(target.get("lab")),
            "routes_registered": registered, "openapi_sha256": openapi_sha, "skipped_routes": skipped,
            "direct_outcomes": direct_outcomes, "direct_allowed_outcomes": direct_allowed_outcomes,
            "gateway_outcomes": gateway_outcomes, "gateway_denied_outcomes": gateway_denied,
            "trusted_origin_reachable": trusted_reachable,
        }, secret_values)
        log_path = workdir / "probe_log.jsonl"
        log_path.write_text("".join(json.dumps(x, ensure_ascii=False, default=str) + "\n" for x in [*log, summary]),
                            encoding="utf-8")
        fixture = {p: {"method": t.method, "path": t.path, "body": t.body} for p, t in sorted(templates.items())}
        config = mask({"target": {k: v for k, v in target.items() if k != "images"}, "request_timeout": timeout,
                       "allow_mutating_routes": allow_mutating}, secret_values)

        return EvidenceDraft(
            status=ExecStatus.COMPLETED,
            # 실험실(stub) 대상은 EXAMPLE. 실제 엔진 대상만 MEASURED (DECISIONS 5a)
            data_origin=DataOrigin.EXAMPLE if target.get("lab") else DataOrigin.MEASURED,
            normal_control=normal,
            challenges=challenges,
            observations=observations,
            artifacts=[DraftArtifact(name="probe_log.jsonl", path=log_path, collector=f"{self.name}/{self.version}")],
            comparability={"fixture_sha256": sha256_hex(canonical_json(fixture)), "config_sha256": sha256_hex(canonical_json(config)),
                           "model_inventory_sha256": env.model.inventory_sha256, "gpu": env.runtime.gpu_model,
                           "driver": env.runtime.driver},
            config=config,
            started_at=started,
            finished_at=utcnow(),
        )
