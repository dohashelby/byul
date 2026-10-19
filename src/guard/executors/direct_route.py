"""direct_route 실행기: Gateway 우회 직통 요청 시험 (REQ-01). 판정하지 않는다(INV-14).

순서
1. 신뢰 출발점에서 엔진 /openapi.json → 등록 경로(CheckDef.route_scope의 종류) ∪ Gateway 설정의 알려진 경로
2. 외부 호출자 → Gateway, CheckDef.functional_probes(역할 키)로 정상 대조
3. 직통: (출발점·대상, 키) 조합마다 먼저 TCP 연결만. 연결 실패(timeout/refused/unreachable)면 HTTP 없이 모든 등록 경로를
   그 값으로 기록(basis: tcp_connect, 추론). 연결되면 경로별 HTTP(검증된 안전 프로브 포함). Gateway도 경로별 키 3종.
4. 결과 분류: 상태코드 | refused | timeout | unreachable | dns_error | error | handler_reached(안전 프로브 기대 응답)
5. 신뢰 출발점 → 엔진 도달 (양성 대조), 3번 직후. 안전 프로브를 보냈으면 전후 엔진 생존·추론 1회 확인
6. 요청/응답 요약을 probe_log.jsonl 증거 파일로. 키 값은 마스킹

등록·시험·관측의 경로 키는 "METHOD path"다(같은 URL의 메서드를 따로 시험).
경로별 대표 직통 결과(direct_outcomes)는 "키 없음"·"잘못된 키"로만 만든다:
2xx·handler_reached > 그 밖의 코드·dns_error·error > 401/403 > timeout/unreachable > refused.
허용 키 직통 결과는 direct_allowed_outcomes로 증거 파일에만 남긴다(판정에 쓰지 않음).
설정은 http_common 참고. 역할: roles = { allowed = "<키 환경변수>" }.
"""

from __future__ import annotations

from typing import Any

from .http_common import (
    TCP_BLOCKED,
    Ctx,
    HttpCheckExecutor,
    classify,
    discover_routes,
    fixture_of,
    functional_requests,
    functional_results,
    probe_templates,
    representative,
    safety_check,
)
from .http_common import docker_container_probe, mask, outcome_rank  # noqa: F401  (기존 import 경로 호환)

DENIED = ("none", "wrong")


class DirectRouteExecutor(HttpCheckExecutor):
    name = "direct_route"
    version = "0.5.0"

    def validate_policy(self, check, policy_values: dict) -> None:
        """설정의 역할(허용 주체로 취급해 direct_allowed_outcomes에만 남김)은 모두 정책 allowed_callers에 있어야 한다.
        키 없음·잘못된 키(none·wrong)는 항상 비허용 주체이며 허용 주체 목록에 올 수 없다."""
        key = self.cfg.get("principals_policy", "allowed_callers")
        allowed = policy_values.get(key)
        if not isinstance(allowed, list):
            raise RuntimeError(f"{check.id}: policy {key} is missing (cannot map executor roles to principals)")
        anonymous = sorted(set(allowed) & set(DENIED))
        if anonymous:
            raise RuntimeError(f"{check.id}: policy {key} lists anonymous roles {anonymous} as allowed principals")
        unknown = sorted(set(self.roles()) - set(allowed))
        if unknown:
            raise RuntimeError(f"{check.id}: executor roles {unknown} are not in policy {key} {sorted(allowed)}; "
                               "refusing to treat them as allowed principals")

    def collect(self, ctx: Ctx):
        rs = discover_routes(ctx)
        routes = rs.routes
        templates, skipped = probe_templates(ctx, routes)
        key_labels = [*DENIED, *[r for r in ctx.keys if r not in ("none", "wrong")]]
        safe_sent = any(t.basis == "safe_probe" for t in templates.values())
        safety_before = safety_check(ctx, "before") if safe_sent else None

        # 직통 조합: (대상, 키). 먼저 TCP 연결만
        direct_combos = [(t, label) for t in ctx.target["direct_targets"] for label in key_labels]
        ext_combos = [(t, l) for t, l in direct_combos if t["origin"] == "external"]
        tcp = ctx.cprobe(ctx.external_net, [{"id": f"tcp|external|{l}", "mode": "tcp", "method": "TCP", "url": t["url"]}
                                            for t, l in ext_combos], ctx.timeout) if ext_combos else []
        tcp_result: dict[tuple[str, str, str], dict] = {(t["origin"], t["url"], l): r for (t, l), r in zip(ext_combos, tcp)}
        for t, l in direct_combos:
            if t["origin"] == "host":
                tcp_result[("host", t["url"], l)] = self._host_probe(
                    {"id": f"tcp|host|{l}", "mode": "tcp", "method": "TCP", "url": t["url"]}, ctx.timeout)
        for (origin, _, l), res in tcp_result.items():
            ctx.record(origin, "direct-tcp", "*", l, res, basis="tcp_connect")

        # 외부 출발점 요청: 기능 표본 + Gateway(경로×키) + 연결된 직통 조합의 경로별 HTTP
        func_reqs, probes = functional_requests(ctx)
        ext_requests: list[dict] = list(func_reqs)
        index: list[tuple] = [("functional", "external", pid, probes[pid].as_role, "http") for pid in probes]
        for path, pr in templates.items():
            for label in key_labels:
                ext_requests.append(ctx.request(f"gw|{path}|{label}", ctx.gateway_url, pr, ctx.keys[label]))
                index.append(("gateway", "external", path, label, pr.basis))
        results: list[tuple[tuple, dict]] = []
        for t, label in direct_combos:
            tr = tcp_result[(t["origin"], t["url"], label)]
            if tr.get("outcome") in TCP_BLOCKED:
                for path in routes:  # "METHOD path"마다(상태 변경 경로 포함), HTTP 미전송
                    results.append((("direct", t["origin"], path, label, "tcp_connect"),
                                    {"outcome": tr["outcome"], "phase": "connect", "method": routes[path].method,
                                     "detail": f"inferred from TCP connect: {tr.get('detail')}"}))
                continue
            for path, pr in templates.items():
                rq = ctx.request(f"direct|{path}|{label}", t["url"], pr, ctx.keys[label])
                if t["origin"] == "external":
                    ext_requests.append(rq)
                    index.append(("direct", "external", path, label, pr.basis))
                else:
                    results.append((("direct", t["origin"], path, label, pr.basis), self._host_probe(rq, ctx.timeout)))
        ext_results = ctx.cprobe(ctx.external_net, ext_requests, ctx.timeout) if ext_requests else []
        results = list(zip(index, ext_results)) + results

        # 양성 대조 (직통 시험 직후)
        (pos,) = ctx.cprobe(ctx.trusted_net, [{"id": "positive", "method": "GET", "url": f"{ctx.engine_url}/health"}],
                            ctx.timeout)
        ctx.record("trusted", "engine", "/health", "none", pos)
        trusted_reachable = isinstance(pos.get("outcome"), int) and not isinstance(pos.get("outcome"), bool)
        safety_after = safety_check(ctx, "after") if safe_sent else None

        # 집계
        direct_denied: dict[str, list] = {p: [] for p in routes}
        direct_allowed: dict[str, list] = {p: [] for p in routes}
        direct_basis: dict[str, dict[str, list[str]]] = {p: {} for p in routes}
        gateway_allowed: dict[str, Any] = {}
        gateway_denied: dict[str, dict] = {p: {} for p in templates}
        func_raw: dict[str, dict] = {}
        for (via, origin, path, label, basis), res in results:
            o = classify(res, templates.get(path)) if via != "functional" else res.get("outcome")
            ctx.record(origin, via, path, label, res, basis="tcp_connect (inferred)" if basis == "tcp_connect" else basis,
                       outcome=o)
            if via == "functional":
                func_raw[path] = res
            elif via == "direct":
                (direct_denied if label in DENIED else direct_allowed)[path].append(o)
                direct_basis[path].setdefault(label, []).append(f"{origin}:{basis}")
            elif label in DENIED:
                gateway_denied[path][label] = o
            else:
                gateway_allowed[path] = o
        direct_outcomes = {p: representative(v) for p, v in direct_denied.items() if v}
        direct_allowed_outcomes = {p: representative(v) for p, v in direct_allowed.items() if v}
        denied_combos = sum(1 for _, l in direct_combos if l in DENIED)
        routes_tested = sorted(p for p in routes if denied_combos and len(direct_denied[p]) == denied_combos)
        gateway_outcomes = {p: o for p, o in gateway_allowed.items() if isinstance(o, int)}
        functional, functional_status, normal = functional_results(probes, func_raw)

        has_direct = bool(ctx.target["direct_targets"])
        observations = {
            "routes_registered": {"value": rs.registered if rs.collected else None, "collected": rs.collected},
            "routes_tested": {"value": routes_tested, "collected": True},
            "direct_outcomes": {"value": direct_outcomes, "collected": has_direct and bool(direct_outcomes)},
            "gateway_outcomes": {"value": gateway_outcomes, "collected": bool(gateway_outcomes)},
            "trusted_origin_reachable": {"value": trusted_reachable, "collected": True},
            "functional_probes_planned": {"value": list(probes), "collected": True},
            "functional_probe_results": {"value": functional, "collected": bool(probes)},
        }
        tcp_summary = {f"{o}|{l}": r.get("outcome") for (o, _, l), r in tcp_result.items()}
        steps = ctx.check.challenge_steps
        challenges = [
            {"step": steps[0], "attempted": True,
             "observations": {"openapi_outcome": rs.openapi_outcome, "routes": rs.registered, "skipped": skipped}},
            {"step": steps[1], "attempted": bool(direct_combos),
             "observations": {"direct_outcomes": direct_outcomes, "direct_basis": direct_basis, "tcp_connect": tcp_summary,
                              "gateway_denied": gateway_denied}},
            {"step": steps[2], "attempted": bool(ext_requests),
             "observations": {"gateway_outcomes": gateway_outcomes, "trusted_origin_reachable": trusted_reachable,
                              "safety": {"before": safety_before, "after": safety_after}}},
        ] if len(steps) == 3 else [{"step": s, "attempted": bool(ext_requests)} for s in steps]
        summary = {
            "routes_registered": rs.registered, "openapi_sha256": rs.openapi_sha256, "skipped_routes": skipped,
            "direct_outcomes": direct_outcomes, "direct_allowed_outcomes": direct_allowed_outcomes,
            "gateway_outcomes": gateway_outcomes, "gateway_denied_outcomes": gateway_denied,
            "trusted_origin_reachable": trusted_reachable,
            "functional_probe_results": functional, "functional_probe_status": functional_status,
            "direct_basis": direct_basis, "tcp_connect": tcp_summary,
            "safe_probes": {p: {"basis": t.basis, "source": t.source, "expected_handler_reached": t.expected_handler_reached}
                            for p, t in templates.items() if t.basis == "safe_probe"},
            "safety": {"before": safety_before, "after": safety_after},
            "fixture": fixture_of(templates),
        }
        return observations, normal, challenges, summary
