"""checklist.json의 5개 draft 배열(77행) → packs/<pack>/checks/<ID>.yaml

ID 규칙(SPEC §2.1): <영역>-<2자리>. 한 번 붙인 ID는 id_map.yaml에 고정하고 바꾸지 않는다.
재실행 시 기존 ID와의 대조: 고정 framework_id → 미등록 항목의 질문·유일한 subtest_ids. 사라진 행은 retired.

    uv run python tools/import_checklist.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

AREAS: dict[str, str] = {
    "supply_assessment_draft": "SUP",
    "request_state_assessment_draft": "REQ",
    "platform_assessment_draft": "PLT",
    "lifecycle_assessment_draft": "LIF",
    "observability_assessment_draft": "OBS",
}
PACK_ID = "PACK-BASELINE"
ENGINES = ["vllm"]

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "reference/framework/data/checklist.json"
DEFAULT_PACK = ROOT / "packs" / PACK_ID
DEFAULT_OVERRIDES = ROOT / "tools/overrides"

# POL-P1-06에서 의미를 대조한 중복 subtest 네 행. 새 원본의 legacy 번호도 대조한다.
REVIEWED_DUPLICATES = {
    "CHK-REQ-019": ("REQ-19", "2.5.2"),
    "CHK-REQ-020": ("REQ-20", "2.5.3"),
    "CHK-LIF-005": ("LIF-05", "4.2.1"),
    "CHK-LIF-007": ("LIF-07", "4.3.1"),
}

# docs/UPDATE_SELECTION.md §3.2·3.4·4.1의 승인된 표. 엔진 경로 규칙과 분리한다.
ENGINE_CATEGORIES = {
    "API_ROUTE": ["REQ-01", "REQ-03", "REQ-06"],
    "AUTH": ["REQ-01", "REQ-02", "REQ-03", "REQ-04", "REQ-06", "REQ-15"],
    "STREAMING": ["LIF-04"], "INPUT_PROCESSING": ["REQ-08", "REQ-09", "SUP-12"],
    "TEMPLATE": ["REQ-07", "SUP-13"], "SAMPLING": ["REQ-10"],
    "MODEL_SELECTION": ["REQ-05", "LIF-06"], "CACHE": ["REQ-15", "REQ-16", "REQ-17"],
    "RUNNER_KERNEL": ["REQ-18", "REQ-19", "REQ-20", "REQ-21"],
    "DISTRIBUTED_IPC": ["REQ-14", "PLT-02", "PLT-04", "PLT-06", "PLT-08", "LIF-13", "SUP-12"],
    "MODEL_LOADING": ["SUP-03", "SUP-06", "SUP-12", "LIF-01", "LIF-02"],
    "REMOTE_CODE": ["SUP-08", "SUP-09"], "PLUGIN": ["SUP-10", "SUP-11"],
    "COMPILE_CACHE": ["SUP-07", "SUP-15"], "SCHEDULER": ["LIF-03"],
    "STARTUP_HEALTH": ["LIF-07", "LIF-09"],
    "FAILURE": ["LIF-08", "LIF-10", "LIF-11", "LIF-12"], "PROFILER": ["LIF-14"],
    "USAGE_STATS": ["OBS-04", "OBS-05"], "REPLAY": ["OBS-07", "OBS-08"],
    "OBSERVABILITY": ["OBS-02", "OBS-03", "OBS-09"], "OUTPUT": ["REQ-11", "REQ-12"],
    "ERROR": ["REQ-13"], "OUTPUT_PARSING": [], "DEPENDENCY": ["SUP-04"],
    "IMAGE": ["SUP-04"], "CLI_OPTION": [], "ENV_VAR": [], "GENERIC": [],
}
PATCH_ALWAYS = ["SUP-04", "SUP-11", "SUP-16", "REQ-02", "REQ-04", "REQ-11", "REQ-12", "REQ-13",
                "PLT-07", "LIF-08", "LIF-09", "LIF-10", "LIF-11", "LIF-12", "OBS-02", "OBS-03",
                "OBS-09", "LIF-03", "REQ-09", "REQ-10"]
MODEL_CATEGORIES = {
    "MODEL_WEIGHTS": ["SUP-03", "SUP-05", "SUP-06", "SUP-16", "LIF-01", "LIF-02"],
    "MODEL_CODE": ["SUP-08", "SUP-09", "SUP-12"], "MODEL_TEMPLATE": ["SUP-13", "REQ-07"],
    "MODEL_TOKENIZER": ["REQ-08", "REQ-09", "REQ-10"],
    "MODEL_ARCH": ["REQ-09", "REQ-10", "LIF-03", "REQ-18", "REQ-19", "REQ-20", "REQ-21"],
}
MODEL_COMMON = ["REQ-05", "REQ-16", "SUP-15", "LIF-05", "LIF-06"]

# 구조화 필드 안에 실제 명시된 명칭만 연결한다. 질문·위험 문구는 검색하지 않는다.
FACT_TERMS = {
    "gateway": ["Gateway"], "prefix_cache": ["prefix cache"],
    "prefix_cache_or_shared_cache": ["prefix cache", "공유 cache", "공유 캐시"],
    "shared_or_remote_weights": ["공유·원격 weights", "공유/원격 공급", "weights daemon"],
    "dynamic_import": ["동적 import"],
    "alternate_sources_or_formats_or_derived_cache": ["대체 파일", "파생 cache", "파생/compiled/npcache"],
    "remote_acquisition_or_retry": ["원격 취득", "다운로드"],
    "managed_or_external_fallback": ["fallback"],
    "r10_features": ["R10"], "request_code_revision_selection": ["code_revision", "코드 판본 선택"],
}


def _selection_data(data: dict, assigned_rows: list[tuple[str, str, dict]]) -> dict:
    triggers: dict[str, list[str]] = {}
    sources: dict[str, list[dict]] = {}
    for source_key, cid, row in assigned_rows:
        structured = {"applicability_criteria": row.get("applicability_criteria", {}),
                      "test_branch_conditions": row.get("test_branch_conditions", [])}
        for field, value in structured.items():
            text = json.dumps(value, ensure_ascii=False)
            for fact, terms in FACT_TERMS.items():
                matched = [term for term in terms if term in text]
                if matched:
                    triggers.setdefault(fact, []).append(cid)
                    sources.setdefault(fact, []).append({"check": cid, "framework_id": row.get("id"),
                        "source": f"{source_key}/{row.get('id')}/{field}", "matched_terms": matched})
        # 축 6과 원문 환경 문서의 현재 JSON 입력 키(nodes·tp_groups·gpu_count)의 명시 대응.
        if 6 in row.get("applicability_criteria", {}).get("environment_axes", []):
            for fact in ("runtime.nodes", "runtime.tp_groups", "runtime.gpu_count"):
                triggers.setdefault(fact, []).append(cid)
                sources.setdefault(fact, []).append({"check": cid, "framework_id": row.get("id"),
                    "source": f"{source_key}/{row.get('id')}/applicability_criteria/environment_axes",
                    "axis": 6, "document": data.get("assessment_registry", {}).get("environment_axes_document"),
                    "input_mapping": "02_환경별_적용기준.md#12개-환경-조건과-현재-json-입력-키:6"})
    return {"version": 1, "engine_categories": ENGINE_CATEGORIES, "patch_always": PATCH_ALWAYS,
            "model_categories": MODEL_CATEGORIES, "model_common": MODEL_COMMON,
            "fact_triggers": {k: sorted(set(v)) for k, v in sorted(triggers.items())},
            "fact_trigger_sources": {k: v for k, v in sorted(sources.items())},
            "unlinked_facts": sorted(set(FACT_TERMS) - set(triggers))}


def _dump(data: Any) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000)


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_if_changed(path: Path, text: str) -> None:
    if path.exists() and path.read_bytes() == text.encode("utf-8"):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_check(path: Path, check: dict) -> None:
    """내용이 바뀌면 CheckDef revision을 올린다. 같으면 파일을 건드리지 않는다."""
    if path.exists():
        old = _load(path)
        check["revision"] = old["revision"]
        if old == check:
            return
        check["revision"] = old["revision"] + 1
    _write_if_changed(path, _dump(check))


def _row_to_check(check_id: str, area: str, source_key: str, row: dict) -> dict:
    """원문 필드를 보존한 CheckDef. 구조화는 overrides만."""
    return {
        "id": check_id,
        "framework_id": row.get("id"),
        "mechanism_ids": list(row.get("mechanism_ids") or []),
        "revision": 1,
        "retired": False,
        "area": area,
        "purpose": row["parent_purpose"],
        "secondary_purposes": row.get("secondary_parent_purposes") or [],
        "question": row["question"],
        "scope": row.get("scope"),
        "risk_category": row.get("risk_category"),
        "risk_summary": row.get("risk_summary"),
        "review_order": row["review_order"],
        "impact": None,
        "applies_when": {"prose": row["applies_when"]},
        "policy_params": [],
        "policy_prose": {
            "policy_to_decide": row.get("policy_to_decide"),
            "enterprise_decisions": row.get("enterprise_decisions"),
        },
        "normal_control": row["normal_control"],
        "challenge_steps": list(row["challenge_steps"]),
        "required_observations": [],
        "required_observations_prose": list(row["required_observations"]),
        "satisfied_if": row["satisfied_if"],
        "fail_criteria": row["fail_criteria"],
        "unknown_criteria": row["unknown_criteria"],
        "remediation": row.get("remediation"),
        "method_and_evidence": row.get("base_method_and_evidence"),
        "remaining_setup": row.get("remaining_setup"),
        "executor": None,
        "judge": None,
        "legacy": {
            "q_ids": list(row.get("legacy_q_ids") or []),
            "scenario_ids": list(row.get("scenario_ids") or []),
            "subtest_ids": list(row.get("subtest_ids") or []),
        },
        "source": {"array": source_key},
    }


def _apply_override(check: dict, override: dict) -> dict:
    for key, value in override.items():
        if key == "applies_when":
            # 원문 prose는 항상 유지
            check["applies_when"] = {**value, "prose": check["applies_when"]["prose"]}
        else:
            check[key] = value
    return check


def _assign_ids(area: str, rows: list[dict], existing: list[dict]) -> tuple[list[tuple[str, dict]], list[dict]]:
    """기존 맵(existing: [{id, question, subtest_ids, retired}])과 대조해 각 행의 ID를 정한다.

    반환: [(id, row)] 원본 순서, 갱신된 맵 항목 목록(ID순).
    """
    entries = {e["id"]: dict(e) for e in existing}
    unmatched = {e["id"] for e in existing}
    canonical = [r["id"] for r in rows if r.get("id")]
    mapped = [e["framework_id"] for e in existing if e.get("framework_id")]
    if len(canonical) != len(set(canonical)) or len(mapped) != len(set(mapped)):
        raise ValueError(f"Duplicate framework ID in {area}")
    by_framework = {e["framework_id"]: e["id"] for e in existing if e.get("framework_id")}
    by_question = {e["question"]: e["id"] for e in existing if not e.get("framework_id")}


    def _subtest_key(st: list[str]) -> tuple[str, ...] | None:
        return tuple(st) if st else None

    existing_st: dict[tuple, list[str]] = {}
    for e in existing:
        if e.get("framework_id"):
            continue
        k = _subtest_key(e.get("subtest_ids") or [])
        if k:
            existing_st.setdefault(k, []).append(e["id"])
    row_st: dict[tuple, int] = {}
    for r in rows:
        k = _subtest_key(r.get("subtest_ids") or [])
        if k:
            row_st[k] = row_st.get(k, 0) + 1

    assigned: list[tuple[str | None, dict]] = []
    for r in rows:
        cid = by_framework.get(r.get("id")) or by_question.get(r["question"])
        if cid is not None and cid in unmatched:
            unmatched.discard(cid)
            assigned.append((cid, r))
        else:
            assigned.append((None, r))

    # 2차: subtest_ids가 양쪽에서 유일하면 대조
    for i, (cid, r) in enumerate(assigned):
        if cid is not None:
            continue
        k = _subtest_key(r.get("subtest_ids") or [])
        if k and row_st.get(k) == 1 and len(existing_st.get(k, [])) == 1:
            cand = existing_st[k][0]
            if cand in unmatched:
                unmatched.discard(cand)
                assigned[i] = (cand, r)

    # Preserve main's reviewed migration for old maps that lack framework IDs.
    for i, (cid, r) in enumerate(assigned):
        if cid is not None or r.get("id") not in REVIEWED_DUPLICATES:
            continue
        cand, legacy_number = REVIEWED_DUPLICATES[r["id"]]
        if (cand in unmatched and not entries[cand].get("framework_id")
                and r.get("legacy_display_number") == legacy_number
                and entries[cand].get("subtest_ids", []) == r.get("subtest_ids", [])):
            unmatched.discard(cand)
            assigned[i] = (cand, r)

    next_no = max((int(e["id"].split("-")[1]) for e in existing), default=0) + 1
    result: list[tuple[str, dict]] = []
    for cid, r in assigned:
        if cid is None:
            cid = f"{area}-{next_no:02d}"
            next_no += 1
        entries[cid] = {
            "id": cid,
            **({"framework_id": r["id"]} if r.get("id") else {}),
            "question": r["question"],
            "subtest_ids": list(r.get("subtest_ids") or []),
            "retired": False,
        }
        result.append((cid, r))
    for cid in unmatched:
        entries[cid]["retired"] = True
    return result, [entries[k] for k in sorted(entries)]


def import_checklist(source: Path, pack_dir: Path, overrides_dir: Path) -> dict:
    data = json.loads(Path(source).read_text(encoding="utf-8"))
    pack_dir = Path(pack_dir)
    checks_dir = pack_dir / "checks"
    checks_dir.mkdir(parents=True, exist_ok=True)

    id_map_path = pack_dir / "id_map.yaml"
    old_map = _load(id_map_path) if id_map_path.exists() else {}
    new_map: dict[str, list[dict]] = {}
    all_ids: list[str] = []
    assigned_rows: list[tuple[str, str, dict]] = []

    assignments = {area: _assign_ids(area, data[key], (old_map or {}).get(area, []))
                   for key, area in AREAS.items()}
    for source_key, area in AREAS.items():
        assigned, entries = assignments[area]
        new_map[area] = entries
        for cid, row in assigned:
            assigned_rows.append((source_key, cid, row))
            check = _row_to_check(cid, area, source_key, row)
            ov = Path(overrides_dir) / f"{cid}.yaml"
            if ov.exists():
                check = _apply_override(check, _load(ov) or {})
            _write_check(checks_dir / f"{cid}.yaml", check)
        for e in entries:
            if e["retired"]:
                path = checks_dir / f"{e['id']}.yaml"
                if path.exists():
                    check = _load(path)
                    check["retired"] = True
                    _write_check(path, check)
        all_ids.extend(e["id"] for e in entries)

    _write_if_changed(id_map_path, _dump(new_map))
    if data.get("framework_model"):
        _write_if_changed(pack_dir / "change_selection.yaml", _dump(_selection_data(data, assigned_rows)))

    files = sorted(p for p in pack_dir.rglob("*") if p.is_file() and p.name != "pack.yaml")
    files_sha = [
        {"path": p.relative_to(pack_dir).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in files
    ]
    files_sha.sort(key=lambda f: f["path"])

    manifest_path = pack_dir / "pack.yaml"
    old = _load(manifest_path) if manifest_path.exists() else None
    if old and old.get("files_sha256") == files_sha:
        revision, review = old["revision"], old["review"]
    else:
        revision = (old["revision"] + 1) if old else 1
        review = {"status": "DRAFT", "reviewer": None, "reviewed_at": None}

    area_order = {code: i for i, code in enumerate(AREAS.values())}
    all_ids.sort(key=lambda c: (area_order[c.split("-")[0]], int(c.split("-")[1])))
    manifest = {
        "pack_id": PACK_ID,
        "revision": revision,
        "engines": ENGINES,
        "checks": all_ids,
        "files_sha256": files_sha,
        "review": review,
    }
    _write_if_changed(manifest_path, _dump(manifest))
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    ap.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    args = ap.parse_args()
    m = import_checklist(args.source, args.pack, args.overrides)
    print(f"{m['pack_id']} revision {m['revision']}: {len(m['checks'])} checks")


if __name__ == "__main__":
    main()
