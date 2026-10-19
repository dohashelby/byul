"""role_route 실행기: 역할별 관리·진단 경로 인가 시험 (REQ-03). 판정하지 않는다(INV-14).

순서
1. 신뢰 출발점에서 엔진 /openapi.json → CheckDef.route_scope 종류(관리·진단)의 등록 경로. 정책이 일반 호출자에게
   허용한 종류(route_scope.exclude_kinds_policy)는 뺀다.
2. 외부 호출자 → Gateway: 경로마다 키 없음·잘못된 키·비관리 역할 키·관리 역할 키로 같은 무해 요청
   (상태 변경 경로는 어댑터의 검증된 안전 프로브만, 없으면 보내지 않음)
3. 결과 분류: 상태코드 | handler_reached(안전 프로브 기대 응답) | 그 밖(http_probe)
4. CheckDef.functional_probes(역할별)로 정상 대조. 안전 프로브를 보냈으면 전후 엔진 생존·추론 1회 확인

경로 키는 "METHOD path". nonadmin_outcomes = 경로별 "키 없음·잘못된 키·비관리 역할" 대표 결과(2xx·handler_reached > 그 밖 > 401/403 > …).
admin_outcomes(관리 역할 결과)는 증거 파일에만 남긴다(판정에 쓰지 않음).
설정은 http_common 참고. 역할: roles = { general = "...", admin = "..." }, admin_role = "admin".
"""

from __future__ import annotations

from typing import Any

from .http_common import (
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


class RoleRouteExecutor(HttpCheckExecutor):
    name = "role_route"
    version = "0.1.0"

    def collect(self, ctx: Ctx):
        admin_role = self.cfg.get("admin_role", "admin")
        if admin_role not in ctx.keys:
            raise RuntimeError(f"admin role {admin_role!r} not configured in roles")
        nonadmin = [label for label in ctx.keys if label != admin_role]  # none, wrong, 비관리 역할들
        rs = discover_routes(ctx)
        templates, skipped = probe_templates(ctx, rs.routes)
        safe_sent = any(t.basis == "safe_probe" for t in templates.values())
        safety_before = safety_check(ctx, "before") if safe_sent else None

        func_reqs, probes = functional_requests(ctx)
        requests: list[dict] = list(func_reqs)
        index: list[tuple] = [("functional", pid, probes[pid].as_role) for pid in probes]
        for path, pr in templates.items():
            for label in [*nonadmin, admin_role]:
                requests.append(ctx.request(f"gw|{path}|{label}", ctx.gateway_url, pr, ctx.keys[label]))
                index.append(("gateway", path, label))
        results = list(zip(index, ctx.cprobe(ctx.external_net, requests, ctx.timeout))) if requests else []
        safety_after = safety_check(ctx, "after") if safe_sent else None

        by_role: dict[str, dict[str, Any]] = {p: {} for p in rs.routes}
        func_raw: dict[str, dict] = {}
        for (via, path, label), res in results:
            if via == "functional":
                func_raw[path] = res
                ctx.record("external", "functional", path, label, res)
                continue
            pr = templates[path]
            o = classify(res, pr)
            ctx.record("external", "gateway", path, label, res, basis=pr.basis, outcome=o)
            by_role[path][label] = o
        nonadmin_outcomes = {p: representative([r[l] for l in nonadmin if l in r])
                             for p, r in by_role.items() if all(l in r for l in nonadmin)}
        admin_outcomes = {p: r[admin_role] for p, r in by_role.items() if admin_role in r}
        routes_tested = sorted(nonadmin_outcomes)
        functional, functional_status, normal = functional_results(probes, func_raw)

        observations = {
            "routes_registered": {"value": rs.registered if rs.collected else None, "collected": rs.collected},
            "routes_tested": {"value": routes_tested, "collected": True},
            "nonadmin_outcomes": {"value": nonadmin_outcomes, "collected": bool(nonadmin_outcomes) or not rs.routes},
            "functional_probes_planned": {"value": list(probes), "collected": True},
            "functional_probe_results": {"value": functional, "collected": bool(probes)},
        }
        steps = ctx.check.challenge_steps
        challenges = [
            {"step": steps[0], "attempted": True,
             "observations": {"openapi_outcome": rs.openapi_outcome, "routes": rs.registered, "skipped": skipped}},
            {"step": steps[1], "attempted": bool(templates),
             "observations": {"by_role": by_role}},
            {"step": steps[2], "attempted": bool(templates),
             "observations": {"safety": {"before": safety_before, "after": safety_after}, "admin_outcomes": admin_outcomes}},
        ] if len(steps) == 3 else [{"step": s, "attempted": bool(templates)} for s in steps]
        summary = {
            "routes_registered": rs.registered, "openapi_sha256": rs.openapi_sha256, "skipped_routes": skipped,
            "outcomes_by_role": by_role, "nonadmin_outcomes": nonadmin_outcomes, "admin_outcomes": admin_outcomes,
            "functional_probe_results": functional, "functional_probe_status": functional_status,
            "safe_probes": {p: {"basis": t.basis, "source": t.source, "expected_handler_reached": t.expected_handler_reached}
                            for p, t in templates.items() if t.basis == "safe_probe"},
            "safety": {"before": safety_before, "after": safety_after},
            "fixture": fixture_of(templates),
        }
        return observations, normal, challenges, summary
