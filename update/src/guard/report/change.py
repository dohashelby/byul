"""U1 보고용 변경·도달 근거. 저장된 관측만 읽고 선정·판정을 바꾸지 않는다."""

from __future__ import annotations

import json
from collections import defaultdict

from guard.models import DataOrigin, ExecStatus, Mode, Side, sha256_hex
from guard.core.change_selection import source_image_relation


def _observation(evidence, name):
    value = evidence.observations.get(name)
    return value.value if value is not None and value.collected else None


def _trace(store, evidence):
    """내용 주소·크기 검증을 통과한 기존 실행 trace만 읽는다."""
    entries, errors = [], []
    for artifact in evidence.artifacts:
        if artifact.name != "probe_log.jsonl":
            continue
        path = (store.root / artifact.path).resolve()
        if not path.is_relative_to(store.root.resolve()):
            errors.append("TRACE_PATH_OUTSIDE_STORE")
            continue
        try:
            raw = path.read_bytes()
            if len(raw) != artifact.bytes or sha256_hex(raw) != artifact.sha256:
                errors.append("TRACE_INTEGRITY_ERROR")
                continue
            parsed = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
            if any(not isinstance(entry, dict) for entry in parsed):
                errors.append("TRACE_INVALID_FORMAT")
                continue
        except (OSError, UnicodeError, ValueError):
            errors.append("TRACE_UNAVAILABLE")
            continue
        entries.extend(parsed)
    return entries, errors


def _route_stage(check, item, evidence, entries, route, kind):
    """경로 등록·시도·응답·handler 확인을 별도 값으로 보존한다."""
    selected = bool(item and item.selected)
    stage = dict(selected=selected, in_scope=None, role_probe=None, attempted=None,
                 observed=None, observation_scope=[], handler_reached=False,
                 evidence=evidence.ref().model_dump(mode="json") if evidence else None,
                 limitations=[])
    if not selected:
        stage["limitations"].append("CHECK_NOT_SELECTED_FOR_CURRENT_RUN")
    if evidence is None:
        stage["limitations"].append("NO_CURRENT_EXECUTION_EVIDENCE")
        return stage
    stage["data_origin"] = evidence.data_origin.value
    if evidence.execution.status != ExecStatus.COMPLETED:
        stage["limitations"].append("EXECUTION_NOT_COMPLETED")
        return stage
    if evidence.data_origin != DataOrigin.MEASURED:
        stage["limitations"].append("NON_MEASURED_EVIDENCE_NOT_DYNAMIC_REACH_PROOF")
        return stage
    if kind != "http":
        stage["in_scope"] = False if check.route_scope is not None else None
        stage["limitations"].append("ENTRYPOINT_TRANSPORT_NOT_OBSERVED")
        return stage
    if route is None:
        stage["limitations"].append("STATIC_ENDPOINT_NOT_RESOLVED")
        return stage

    registered = _observation(evidence, "routes_registered")
    if isinstance(registered, list):
        stage["in_scope"] = route in registered
    summaries = [entry for entry in entries if entry.get("summary") is True]
    skipped = {key for summary in summaries if isinstance(summary.get("skipped_routes"), (dict, list))
               for key in summary["skipped_routes"] if isinstance(key, str)}
    if route in skipped:
        stage["role_probe"] = False
        stage["limitations"].append("SAFE_PROBE_UNAVAILABLE")
    logs = [entry for entry in entries if entry.get("route") == route
            and entry.get("via") in ("direct", "gateway")]
    tcp = [entry for entry in logs if str(entry.get("basis", "")).startswith("tcp_connect")]
    http = [entry for entry in logs if not str(entry.get("basis", "")).startswith("tcp_connect")]
    fixtures = [summary["fixture"] for summary in summaries if isinstance(summary.get("fixture"), dict)]
    planned_roles = {role for fixture in fixtures if isinstance(fixture.get("roles"), list)
                     for role in fixture["roles"] if isinstance(role, str)}
    stage["roles_observed"] = sorted({entry["key"] for entry in http if isinstance(entry.get("key"), str)})
    if logs:
        stage["attempted"] = bool(http)
        if http and planned_roles:
            stage["role_probe"] = planned_roles <= set(stage["roles_observed"])
        elif http:
            stage["limitations"].append("REQUIRED_ROLE_COVERAGE_UNCONFIRMED")
        if any(entry.get("outcome") in ("refused", "timeout", "unreachable") for entry in tcp):
            stage["observation_scope"].append("TCP_BLOCKED")
        if any(type(entry.get("outcome")) is int for entry in http):
            stage["observation_scope"].append("HTTP_RESPONSE")
        if any(entry.get("classified") == "handler_reached" for entry in http):
            stage["observation_scope"].append("HANDLER_REACHED")
            stage["handler_reached"] = True
        # TCP 추론은 경로에 HTTP 요청을 보냈다는 근거가 아니다.
        stage["observed"] = bool(set(stage["observation_scope"]) & {"HTTP_RESPONSE", "HANDLER_REACHED"})
    elif entries:
        stage["attempted"] = False
        stage["observed"] = False
    if stage["in_scope"] is False:
        stage["limitations"].append("ENTRYPOINT_OUTSIDE_COLLECTED_CHECK_SCOPE")
    if not stage["handler_reached"]:
        stage["limitations"].append("HANDLER_REACH_NOT_PROVEN")
    if not logs:
        stage["limitations"].append("NO_MATCHING_REQUEST_TRACE")
    return stage


def summarize_change(store, pack, plan, evidences):
    """JSON 원본에 넣을 정적 변경과 side별 5단계 도달 확인을 만든다."""
    previous = getattr(plan.inputs, "previous_plan", None)
    change_type = getattr(plan, "change_type", None)
    if not plan.inputs.change and previous is None and change_type is None:
        return None
    change = store.resolve(plan.inputs.change) if plan.inputs.change else None
    summary = dict(change_type=change_type, categories=[], unmapped=[], checklist_gaps=[],
                   entrypoints=[], manual_targets=[], limitations=[])
    if change is None:
        return summary
    summary["change"] = change.ref().model_dump(mode="json")
    summary["extractor"] = change.extractor.model_dump(mode="json")
    summary["analysis"] = getattr(change, "analysis", None)
    if change.extractor.name != "model_manifest":
        envs = [store.resolve(ref) for ref in plan.inputs.environments]
        summary["source_image_relation"] = source_image_relation(change, envs)
        summary["limitations"].append("SOURCE_IMAGE_RELATION_USES_DECLARED_METADATA_NOT_IMAGE_CONTENT_ATTESTATION")
    grouped = defaultdict(lambda: {"paths": set(), "related_checks": set(), "fact_ids": []})
    trace_cache = {}
    sides = [Side.single] if plan.mode == Mode.audit else [Side.baseline, Side.candidate]
    items = {item.check_id: item for item in plan.items}
    for fact in change.facts:
        details = getattr(fact, "details", None) or {}
        group = grouped[fact.category]
        classified_paths = details.get("path_classifications")
        if not isinstance(classified_paths, list):
            classified_paths = []
        paths = sorted({p["path"] for p in classified_paths if isinstance(p, dict)
                        and isinstance(p.get("path"), str) and p.get("excluded") is False
                        and (p.get("unmapped") is True if fact.category == "OTHER" else
                             isinstance(p.get("categories"), list) and fact.category in p["categories"])})
        group["paths"].update(paths or [fact.target])
        group["related_checks"].update(fact.related_checks)
        group["fact_ids"].append(fact.id)
        fact_ref = dict(fact_id=fact.id, category=fact.category, target=fact.target,
                        evidence=[ev.model_dump(mode="json") for ev in fact.evidence])
        if paths:
            fact_ref["paths"] = paths
        if getattr(fact, "unmapped", False):
            summary["unmapped"].append(fact_ref)
        if not fact.related_checks and fact.category not in {
                "CLI_OPTION", "ENV_VAR", "GENERIC", "OTHER", "DEFAULT_VALUE", "ADVISORY"}:
            summary["checklist_gaps"].append(fact_ref)
        if details.get("entrypoint") is not True:
            continue
        kind = details.get("entrypoint_kind", "unknown")
        routes = details.get("routes") or [None]
        for route in routes:
            route_id = f"{str(route.get('method', '')).upper()} {route.get('path', '')}" if route else None
            entry = dict(fact_id=fact.id, source_path=(route.get("source_path") if route else None)
                         or details.get("source_path", fact.target),
                         entrypoint_kind=kind, route=route_id, used=None,
                         usage_limitation="ENV_ENTRYPOINT_USAGE_NOT_STRUCTURED", stages=[])
            if route and route.get("source_side") in {"baseline", "candidate"}:
                entry["source_side"] = route["source_side"]
            for cid in fact.related_checks:
                if cid not in pack.checks:
                    continue
                for side in sides:
                    evidence = evidences.get((cid, side))
                    entries, errors = [], []
                    if evidence:
                        key = (evidence.id, evidence.revision, evidence.content_sha256)
                        if key not in trace_cache:
                            trace_cache[key] = _trace(store, evidence)
                        entries, errors = trace_cache[key]
                    stage = _route_stage(pack.checks[cid], items.get(cid), evidence, entries, route_id, kind)
                    stage.update(check_id=cid, side=side.value)
                    stage["limitations"].extend(errors)
                    entry["stages"].append(stage)
            summary["entrypoints"].append(entry)
            # HTTP 응답만으로 handler 도달이나 변경 보안 속성을 확인했다고 하지 않는다.
            if not entry["stages"] or any(not stage["handler_reached"] or stage["role_probe"] is not True
                                          or stage["in_scope"] is not True for stage in entry["stages"]):
                summary["manual_targets"].append(dict(fact_id=fact.id, source_path=entry["source_path"],
                                                     route=route_id, reason="ENTRYPOINT_REACH_REVIEW_REQUIRED"))
    summary["categories"] = [dict(category=category, file_count=len(value["paths"]), paths=sorted(value["paths"]),
                                  related_checks=sorted(value["related_checks"]), fact_ids=value["fact_ids"])
                              for category, value in sorted(grouped.items())]
    if summary["unmapped"]:
        summary["limitations"].append("UNMAPPED_FILES_REQUIRE_RULE_REVIEW")
    if summary["manual_targets"]:
        summary["limitations"].append("ENTRYPOINT_REACH_UNCONFIRMED_JUDGEMENTS_UNCHANGED")
    if summary["entrypoints"]:
        summary["limitations"].append("ENV_ENTRYPOINT_USAGE_NOT_STRUCTURED")
    summary["limitations"].append("STATIC_ANALYSIS_DOES_NOT_PROVE_DYNAMIC_REACH_OR_INTERNAL_CODE_COVERAGE")
    return summary
