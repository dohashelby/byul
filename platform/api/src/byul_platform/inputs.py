"""기존 카드 → 실제 Pack 정책 연결. 원본·미확인 값은 추정하거나 삭제하지 않는다."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from .input_models import InputBinding, InputCatalog, InputVersion, PolicyInputPreview
from .store import InvalidDocId

MVP_PACK = {"id": "PACK-BASELINE", "revision": 36}  # 2026-11-14 RB·RB2: 실행 결합값·실행 준비값은 정책이 아니라 guard run이 계산한다
# 의미가 같은 필드만 연결한다. 역할표→호출자 목록, 응답 옵션→응답 필드는 추정하지 않는다.
_GROUPS = {
    "1.2": {"REQ-01": ["allowed_callers"], "REQ-03": ["general_allowed_kinds"]},
    "1.4": {"PLT-01": ["allowed_capabilities", "allowed_mounts", "allowed_devices", "require_non_root", "allowed_exec_writable_paths"]},
    "1.5": {"PLT-04": ["forbidden_env_names", "forbidden_mount_paths", "credential_candidate_paths"]},
    "3.1": {"REQ-09": ["allowed_endpoints", "allowed_input_formats", "rejection_codes"],
            "REQ-10": ["allowed_sampling_options", "rejection_codes"]},
    "3.2": {"SUP-08": ["approved_code_revisions"]},
    "4.1": {"REQ-11": ["result_binding", "client_request_id_trust"]},
    "4.2": {"REQ-15": ["public_prefix_sharing_allowed", "isolated_cache_groups", "cache_identity_policy_ref"]},
    "4.3": {"REQ-16": ["required_cache_contexts"]},
    "5.1": {"REQ-13": ["allowed_response_fields", "allowed_error_fields", "forbidden_return_options", "required_targets"]},
    "5.2": {"OBS-03": ["allowed_log_fields", "forbidden_log_fields", "forbidden_marker_contexts", "required_sources", "required_branches"]},
    "5.3": {"OBS-05": ["usage_allowed", "allowed_usage_fields", "allowed_usage_sinks", "usage_report_paths", "usage_settings_fresh_process"]},
    "5.6": {"REQ-17": ["cache_timing_max_auc_percent", "cache_timing_min_samples"]},
}
BINDINGS = [InputBinding(requirement=r, field=k, check_id=c, key=k)
            for r, checks in _GROUPS.items() for c, keys in checks.items() for k in keys]
BINDINGS += [InputBinding(requirement="3.1", field="max_input_tokens", check_id="REQ-09", key="max_input_tokens", source="numbers")]

# user-decisions-20261108 §6-1. 문서의 data_origin은 변경하지 않는다.
FIXED_VALUES = {
    "REQ-13": {"allowed_response_fields": ["id", "object", "created", "model", "choices", "usage"],
               "allowed_error_fields": ["message", "type", "code"],
               "forbidden_return_options": ["echo", "prompt_logprobs", "logprobs"], "required_targets": ["gateway"]},
    "REQ-17": {"cache_timing_max_auc_percent": 55, "cache_timing_min_samples": 200},
    "OBS-05": {"usage_allowed": False},
}
# REQ-03의 작업 범위와 LIF-13은 정본 정책 파일의 정확한 표현을 기다린다.
LIMITATIONS = ["REQ-03·LIF-13 확정 기준은 정본 정책 파일의 정확한 키·값 확인이 필요합니다. 임의로 확대 해석하지 않습니다."]
FIXED_REQUIREMENTS = {"REQ-03": "1.2", "REQ-13": "5.1", "REQ-17": "5.6", "OBS-05": "5.3", "LIF-13": "4.4"}


def _issue(path, message):
    return {"path": path, "message": message}


def _read_versions(update_dir: Path):
    try:
        rows = json.loads((update_dir / "src/guard/adapters/assets/engine_profiles.json").read_text(encoding="utf-8"))["profiles"]
    except (OSError, ValueError, KeyError):
        return []
    result = {}
    for row in rows:
        for version in row.get("versions", []):
            key = (row["engine"], version)
            first = key not in result
            item = result.setdefault(key, {"engine": key[0], "version": version, "profiles": [], "features": [], "hidden_fields": []})
            item["profiles"].append(row["id"])
            item["features"] = sorted(set(item["features"]) | set(row.get("features", [])))
            # 명시된 비적용 필드만 숨긴다. features에 없다는 것만으로 비사용을 판단하지 않는다.
            excluded = set(row.get("input_fields_not_applicable", []))
            item["hidden_fields"] = sorted(excluded if first else set(item["hidden_fields"]) & excluded)
    return [InputVersion(**item) for item in result.values()]


def input_catalog(state) -> InputCatalog:
    pack = None
    reason = None
    try:
        from guard.models import load_pack
        pack = load_pack(state.settings.update_dir / "packs/PACK-BASELINE")
        if {"id": pack.ref().id, "revision": pack.ref().revision} != MVP_PACK:
            reason = f"정본 Pack {pack.ref().revision}이 등록되어 있습니다. MVP Pack {MVP_PACK['revision']} 준비가 필요합니다."
            pack = None
    except ImportError:
        reason = f"update 입력 연동 모듈을 불러오지 못했습니다. Pack {MVP_PACK['revision']} 준비 상태를 확인할 수 없습니다."
    except (OSError, ValueError) as exc:
        reason = f"Pack {MVP_PACK['revision']}를 불러오지 못했습니다 ({type(exc).__name__})."
    parameters = {c.id: [{"key": p.key, "type": p.type, "required": p.required} for p in c.policy_params]
                  for c in pack.active_checks()} if pack else {}
    bindings = [b.model_copy(update={"type": p["type"]}) for b in BINDINGS
                for p in parameters.get(b.check_id, []) if p["key"] == b.key]
    fixed, limitations = deepcopy(FIXED_VALUES), list(LIMITATIONS)
    if state.settings.mvp_policy_file:
        try:
            import yaml  # 기존 update 패키지가 제공한다. 플랫폼 의존성을 늘리지 않는다.
            try:
                source = yaml.safe_load(state.settings.mvp_policy_file.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise ValueError("MVP_POLICY_SOURCE_INVALID") from exc
            if state.contracts.errors("policy", source) or source.get("data_origin") == "EXAMPLE" or source["verification"]["pack"] != MVP_PACK:
                raise ValueError("MVP_POLICY_SOURCE_INVALID")
            for cid in FIXED_REQUIREMENTS:
                params = {p["key"]: p for p in parameters.get(cid, [])}
                for key, value in source["verification"]["values"].get(cid, {}).items():
                    # 다른 환경의 준비 해시를 기업 정책 기준으로 재사용하지 않는다.
                    if key in params and not key.startswith("sensor_") and _valid(value, params[key]["type"]):
                        fixed.setdefault(cid, {})[key] = deepcopy(value)
            missing = [cid for cid in FIXED_REQUIREMENTS if not fixed.get(cid)]
            limitations = [f"정본 정책의 정확한 키·값 확인이 필요한 점검: {', '.join(missing)}"] if missing else []
        except (OSError, ValueError, ImportError, KeyError, TypeError):
            limitations.append("정본 MVP 정책 파일을 확인하지 못했습니다. 승인·실행 준비 완료로 처리하지 않습니다.")
    return InputCatalog(pack=MVP_PACK, available=pack is not None, reason=reason,
                        versions=_read_versions(state.settings.update_dir), bindings=bindings,
                        parameters=parameters, fixed_values=fixed, limitations=limitations)


def _valid(value, kind):
    if kind == "bool":
        return type(value) is bool
    if kind == "int":
        return type(value) is int
    if kind == "str":
        return type(value) is str
    if kind.startswith("list["):
        inner = kind[5:-1]
        return type(value) is list and all(_valid(v, inner) for v in value)
    if kind == "dict[str,int]":
        return type(value) is dict and all(type(k) is str and type(v) is int for k, v in value.items())
    return False


def _same(left, right):
    return type(left) is type(right) and left == right


def resolve_policy(state, document, catalog=None):
    doc = deepcopy(document)
    errors, warnings, gaps, connected = [], [], [], []
    catalog = catalog or input_catalog(state)
    verification = doc.setdefault("verification", {})
    wanted = verification.get("pack")
    try:
        previous = state.policies.revisions(doc.get("policy_id", ""))
    except InvalidDocId:
        previous = []
    # 과거 정책의 Pack은 변경하지 않는다. 새 MVP 정책에는 MVP_PACK 판본을 강제한다.
    legacy = bool(previous and wanted != MVP_PACK and previous[-1].document.get("verification", {}).get("pack") == wanted)
    if legacy:
        warnings.append(_issue("/verification/pack", "과거 Pack 정책을 보존합니다. MVP는 새 정책 ID로 등록하세요."))
        return PolicyInputPreview(document=doc, errors=[], warnings=warnings, gaps=[], connected=[], pack_available=False)
    if wanted and wanted.get("id") and wanted != MVP_PACK:
        errors.append(_issue("/verification/pack", f"새 MVP 정책은 백엔드에서 지정한 {MVP_PACK['id']}@{MVP_PACK['revision']}만 사용합니다."))
    verification["pack"] = dict(MVP_PACK)
    values = verification.setdefault("values", {})
    if not isinstance(values, dict):
        return PolicyInputPreview(document=doc, errors=[_issue("/verification/values", "정책값은 객체여야 합니다.")],
                                  warnings=[], gaps=[], connected=[], pack_available=catalog.available)
    reqs = (doc.get("framework_values") or {}).get("requirements", {})
    base = next((r for r in reversed(previous) if r.document.get("status") == "CONFIRMED"), None)
    base_ref = {"id": base.doc_id, "revision": base.revision, "sha256": base.sha256} if base else None
    unresolved_keep = set()
    env_ref = doc.get("environment_ref")
    environment = None
    if isinstance(env_ref, dict):
        try:
            environment = state.environments.get(env_ref.get("id", ""), env_ref.get("revision", 0))
        except (InvalidDocId, TypeError):
            pass
    if environment is None:
        gaps.append(_issue("/environment_ref", "연결된 저장 환경 판본이 없습니다. 초안은 보존하지만 실행 준비로 처리하지 않습니다."))
    else:
        target = environment.document.get("target", {})
        if doc.get("target_ref") != target.get("target_ref"):
            errors.append(_issue("/target_ref", "정책 대상과 저장된 환경의 대상이 다릅니다."))
        engine = target.get("engine", {})
        if not any(v.engine == engine.get("name") and v.version == engine.get("version") for v in catalog.versions):
            gaps.append(_issue("/environment_ref", "선택한 엔진 판본의 지원 정보가 없습니다. 비사용으로 판단하지 않습니다."))
    for rid, entry in reqs.items():
        path = f"/framework_values/requirements/{rid}"
        if entry.get("decision") == "keep":
            old = ((base.document.get("framework_values") or {}).get("requirements", {}).get(rid) if base else None)
            if base and not old:
                # 카드 도입 전 승인 정책도 실제 저장된 실행 정책값을 재사용할 수 있다.
                direct = {b.field: deepcopy(base.document.get("verification", {}).get("values", {}).get(b.check_id, {})[b.key])
                          for b in catalog.bindings if b.requirement == rid and b.source == "values"
                          and b.key in base.document.get("verification", {}).get("values", {}).get(b.check_id, {})}
                if direct:
                    old = {"values": direct, "numbers": {}, "basis": None, "note": None}
            if not old or not (old.get("values") or old.get("numbers")):
                gaps.append(_issue(path, "유지할 이전 승인 정책값이 없습니다. 미정으로 남깁니다."))
                unresolved_keep.add(rid)
                continue
            entry["values"], entry["numbers"] = deepcopy(old.get("values", {})), deepcopy(old.get("numbers", {}))
            entry["basis"] = old.get("basis")
            entry["note"] = f"{old.get('note') or ''}\n유지 기준: {base.doc_id}@{base.revision} sha256:{base.sha256}".strip()
        if entry.get("decision") == "needs_confirmation":
            gaps.append(_issue(path, "정책 확인이 필요합니다. 결정 완료·실행 준비 완료로 처리하지 않습니다."))
    if not catalog.available:
        gaps.append(_issue("/verification/pack", catalog.reason or f"Pack {MVP_PACK['revision']} 준비 미완료"))
    for b in catalog.bindings:
        entry = reqs.get(b.requirement, {})
        if b.requirement in unresolved_keep:
            continue
        if entry.get("decision") not in ("change", "keep"):
            continue
        if entry.get("decision") == "keep" and not base:
            continue
        source = entry.get(b.source, {})
        if b.field not in source:
            continue
        value = source[b.field]
        path = f"/framework_values/requirements/{b.requirement}/{b.source}/{b.field}"
        if b.source == "numbers":
            if not isinstance(value, dict) or value.get("status") != "confirmed" or value.get("value") is None:
                gaps.append(_issue(path, "수치가 확정되지 않았습니다."))
                continue
            if value.get("unit") != "token" or value.get("scope") != "요청별" or value.get("condition"):
                gaps.append(_issue(path, "현재 Pack의 요청당 토큰 기준과 단위·범위·조건이 일치하지 않아 자동 연결하지 않습니다."))
                continue
            value = value["value"]
        if value is None:
            gaps.append(_issue(path, "미확인 값은 실행 정책값으로 변환하지 않습니다."))
            continue
        if not _valid(value, b.type):
            errors.append(_issue(path, f"{b.check_id}.{b.key}은 {b.type} 형식이어야 합니다."))
            continue
        current = values.setdefault(b.check_id, {})
        if not isinstance(current, dict):
            continue  # 아래 공통 검사에서 필드 위치를 알린다.
        if b.key in current and not _same(current[b.key], value):
            stored = previous[-1].document if previous else {}
            old_values = stored.get("verification", {}).get("values", {}).get(b.check_id, {})
            old_entry = (stored.get("framework_values") or {}).get("requirements", {}).get(b.requirement, {})
            old_card = old_entry.get(b.source, {}).get(b.field)
            if b.source == "numbers" and isinstance(old_card, dict):
                old_card = old_card.get("value")
            # 저장 시 자동 연결한 값만 카드 편집을 따라 갱신한다. 사용자가 JSON을 바꿨다면 충돌이다.
            if b.key not in old_values or not _same(current[b.key], old_values[b.key]) or not _same(current[b.key], old_card):
                errors.append(_issue(path, f"고급 JSON의 {b.check_id}.{b.key}와 카드값이 다릅니다. 충돌을 먼저 해결하세요."))
                continue
        current[b.key] = deepcopy(value)
        connected.append(f"{b.requirement}:{b.field} → {b.check_id}.{b.key}")
    # 확정 기준 중 실제 Pack에 존재하는 값만 재사용한다. 명시 입력은 덮어쓰지 않는다.
    if catalog.available:
        for cid, fixed in catalog.fixed_values.items():
            params = {p["key"]: p for p in catalog.parameters.get(cid, [])}
            owner = FIXED_REQUIREMENTS.get(cid)
            if reqs.get(owner, {}).get("decision") == "needs_confirmation" or owner in unresolved_keep:
                continue
            for key, value in fixed.items():
                bindings = [b for b in catalog.bindings if b.check_id == cid and b.key == key]
                if any(b.field in reqs.get(b.requirement, {}).get(b.source, {}) for b in bindings):
                    continue  # 미확인·명시값을 확정 기준이나 기본값으로 대체하지 않는다.
                if key in params and _valid(value, params[key]["type"]) and isinstance(values.get(cid, {}), dict):
                    values.setdefault(cid, {}).setdefault(key, deepcopy(value))
    # 고급 JSON도 같은 Pack 타입으로 검사한다. 빈 목록·False도 명시값이다.
    for cid, data in values.items():
        if not isinstance(data, dict):
            errors.append(_issue(f"/verification/values/{cid}", "점검별 정책값은 객체여야 합니다."))
            continue
        params = {p["key"]: p for p in catalog.parameters.get(cid, [])}
        for key, value in data.items():
            if catalog.available and key not in params:
                errors.append(_issue(f"/verification/values/{cid}/{key}", f"Pack {MVP_PACK['revision']}에 없는 정책 키입니다."))
            elif key in params and not _valid(value, params[key]["type"]):
                errors.append(_issue(f"/verification/values/{cid}/{key}", f"{params[key]['type']} 형식이어야 합니다."))
    for cid, params in catalog.parameters.items():
        scope = verification.get("scope", {})
        if cid in scope.get("exclude", []) or (scope.get("include") and cid not in scope["include"]):
            continue
        for param in params:
            if param["required"] and param["key"] not in values.get(cid, {}):
                gaps.append(_issue(f"/verification/values/{cid}/{param['key']}", f"{cid}에 필요한 값이 없습니다. 준비·수집 단계 또는 정책 입력에서 보완해야 합니다."))
    for rid, entry in reqs.items():
        for source in ("values", "numbers"):
            mapped = {b.field for b in catalog.bindings if b.requirement == rid and b.source == source}
            for key in entry.get(source, {}):
                if key not in mapped:
                    warnings.append(_issue(f"/framework_values/requirements/{rid}/{source}/{key}", "현재 검증에 연결되지 않음 — 원본 답변을 보존합니다."))
    return PolicyInputPreview(document=doc, errors=errors, warnings=warnings, gaps=gaps,
                              connected=sorted(set(connected)), pack_available=catalog.available, previous_policy=base_ref)


def environment_input_errors(document):
    entry = ((document.get("framework_profile") or {}).get("conditions", {}).get("entry") or {}).get("values", {})
    block = entry.get("direct_block")
    if isinstance(block, list) and "none" in block and len(block) > 1:
        return [_issue("/framework_profile/conditions/entry/values/direct_block", "차단 없음과 접근 제한 수단을 동시에 선택할 수 없습니다.")]
    return []
