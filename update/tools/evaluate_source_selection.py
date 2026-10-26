"""U1 소스 회귀 정답과 추출 근거를 대조한다. 엔진 실행·정답 자동 생성은 하지 않는다.

예: uv run python -m tools.evaluate_source_selection --oracle tests/fixtures/u1_source_oracle.json
    --artifact <engine-source-diff.json> --selection packs/PACK-BASELINE/change_selection.yaml
    --source-repository <기존 git 캐시> --output <저장소 밖 결과.json>
출력의 점검 집합은 core의 패치 규칙 후보이며 실제 PLAN 선정·실행 결과가 아니다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import subprocess

import yaml

from guard.core.change_selection import InputDiff, selection_triggers
from guard.collectors.change import ChangeInput, read_change
from guard.models.checkdef import load_pack
from guard.store.sqlite import Store


def _metric(numerator: int, denominator: int, unassessed_cases: int = 0) -> dict:
    return {"numerator": numerator, "denominator": denominator, "unassessed_cases": unassessed_cases}


def _side_labels(case: dict, record: dict, schema: str) -> tuple[list[dict], dict]:
    """합집합으로 면별 정답을 대신하지 않는다. 없는 라벨은 미평가로 남긴다."""
    labels = case["expected"].get("sides", {})
    if not isinstance(labels, dict) or set(labels) - {"baseline", "candidate"}:
        raise ValueError("oracle_invalid_side_labels")
    counters = {key: 0 for key in ("side_missing", "side_labelled", "side_path_wrong", "side_paths",
        "side_hash_wrong", "side_hashes", "side_category_missing", "side_required", "side_category_wrong",
        "side_negative", "side_exclusion_wrong", "side_exclusions", "side_unmapped_wrong", "side_unmapped")}
    rows = []
    for side, expected in labels.items():
        if not isinstance(expected, dict):
            raise ValueError("oracle_invalid_side_labels")
        prefix = "before" if side == "baseline" else "after"
        for field, source_key in (("path", f"{prefix}_path"), ("sha256", f"{prefix}_sha256")):
            if field in expected and expected[field] != case.get(source_key):
                raise ValueError("oracle_side_source_conflict")
        counters["side_labelled"] += 1
        states = [p for p in record.get("path_classifications", []) if p.get("side") == side]
        if schema == "guard.engine-source-diff/1" or "path_classifications" not in record:
            rows.append({"side": side, "status": "not_assessed", "reason": "path_classifications_unavailable"})
            continue
        # 명시적인 부재 라벨도 실제로 불필요한 면이 없는지 검증한다.
        if expected.get("path", case.get(f"{prefix}_path")) is None:
            counters["side_paths"] += 1
            counters["side_path_wrong"] += bool(states)
            rows.append({"side": side, "status": "evaluated", "expected_absent": True,
                         "errors": ["unexpected_side"] if states else []})
            continue
        if len(states) != 1:
            counters["side_missing"] += 1
            rows.append({"side": side, "status": "missing_or_ambiguous", "errors": ["side_missing_or_ambiguous"]})
            continue
        state = states[0]
        errors = []
        if "path" in expected:
            counters["side_paths"] += 1
            if state.get("path") != expected["path"]:
                counters["side_path_wrong"] += 1
                errors.append("path_mismatch")
        if "sha256" in expected:
            counters["side_hashes"] += 1
            # /2는 해시를 레코드 before/after에 보관하므로 해당 면에 대응한다.
            if record.get(f"{prefix}_sha256") != expected["sha256"]:
                counters["side_hash_wrong"] += 1
                errors.append("blob_hash_mismatch")
        categories = set(state.get("categories", []))
        required = set(expected.get("required_categories", []))
        forbidden = set(expected.get("forbidden_categories", []))
        missing, wrong = required - categories, forbidden & categories
        counters["side_required"] += len(required)
        counters["side_category_missing"] += len(missing)
        counters["side_negative"] += len(forbidden)
        counters["side_category_wrong"] += len(wrong)
        for key, total_key, wrong_key in (("excluded", "side_exclusions", "side_exclusion_wrong"),
                                          ("unmapped", "side_unmapped", "side_unmapped_wrong")):
            if expected.get(key) is not None:
                counters[total_key] += 1
                if state.get(key) != expected[key]:
                    counters[wrong_key] += 1
                    errors.append(f"{key}_mismatch")
        rows.append({"side": side, "status": "evaluated", "path": state.get("path"),
            "missing_categories": sorted(missing), "known_wrong_categories": sorted(wrong), "errors": errors})
    return rows, counters


def _record_key(record: dict) -> tuple:
    return tuple(record.get(key) for key in ("before_path", "after_path", "before_sha256", "after_sha256"))


def _fact_key(fact) -> tuple:
    if any(point is not None and not isinstance(point, dict) for point in (fact.before, fact.after)):
        raise ValueError("change_fact_record_mismatch")
    return tuple((point or {}).get(key) for point, key in
                 ((fact.before, "path"), (fact.after, "path"), (fact.before, "sha256"), (fact.after, "sha256")))


def _change_binding(change_input: ChangeInput | None, artifact: dict, artifact_bytes: bytes | None,
                    selection: dict, pack) -> tuple[dict[tuple, list], dict]:
    """기존 공통 CHG/CAS 검증 뒤 소스·규칙·팩·개별 사실의 대응을 검사한다."""
    if change_input is None:
        return {}, {"status": "not_assessed", "reason": "change_input_missing"}
    if pack is None or artifact_bytes is None:
        raise ValueError("change_binding_requires_pack_and_artifact_bytes")
    if json.loads(artifact_bytes) != artifact:
        raise ValueError("change_artifact_bytes_mismatch")
    value = read_change(change_input, pack)
    change = value.change
    if change.extractor.name != "git_diff":
        raise ValueError("change_source_extractor_unsupported")
    digest = hashlib.sha256(artifact_bytes).hexdigest()
    if not any(a.sha256 == digest and value.payloads.get(a.path) == artifact_bytes
               for a in change.artifacts or []):
        raise ValueError("change_artifact_binding_missing")
    if (change.baseline.source_commit != artifact["baseline"]["commit"]
            or change.candidate.source_commit != artifact["candidate"]["commit"]):
        raise ValueError("change_source_commits_mismatch")
    if not artifact.get("rules_sha256") or (change.analysis or {}).get("rules_sha256") != artifact["rules_sha256"]:
        raise ValueError("change_rules_hash_mismatch")
    if pack.change_selection != selection:
        raise ValueError("change_selection_pack_mismatch")
    refs = [ref for ref in change.refs if ref.kind == "PACK"]
    if refs and refs != [pack.ref()]:
        raise ValueError("change_pack_reference_mismatch")
    records = {_record_key(record): record for record in artifact.get("changes", [])}
    if len(records) != len(artifact.get("changes", [])):
        raise ValueError("change_artifact_duplicate_record")
    if artifact["schema"] == "guard.engine-source-diff/2":
        for record in records.values():
            side_unmapped = any(state.get("unmapped") and not state.get("excluded")
                                for state in record.get("path_classifications", []))
            if bool(record.get("unmapped")) != side_unmapped:
                raise ValueError("change_record_unmapped_side_mismatch")
    bound = {}
    expected_categories = {key: set(record.get("categories", [])) | ({"OTHER"} if record.get("unmapped") else set())
                           for key, record in records.items()}
    for fact in change.facts:
        key = _fact_key(fact)
        if key not in records or fact.category not in expected_categories[key]:
            raise ValueError("change_fact_record_mismatch")
        record = records[key]
        # 실제 선정기는 CHG fact.unmapped를 읽는다. 평가용 artifact의 확대
        # 선정을 실제 CHG의 선정과 같다고 주장하려면 이 표시도 같아야 한다.
        if fact.unmapped is not (fact.category == "OTHER" and bool(record.get("unmapped"))):
            raise ValueError("change_fact_unmapped_mismatch")
        if not any(e.sha256 == digest for e in fact.evidence):
            raise ValueError("change_fact_artifact_binding_missing")
        if set(fact.related_checks) != set(selection.get("engine_categories", {}).get(fact.category, [])):
            raise ValueError("change_fact_pack_mapping_mismatch")
        if fact.target not in {record.get("before_path"), record.get("after_path")}:
            raise ValueError("change_fact_target_mismatch")
        if artifact["schema"] == "guard.engine-source-diff/2":
            if (fact.details or {}).get("path_classifications") != record.get("path_classifications"):
                raise ValueError("change_fact_side_binding_mismatch")
            matching = [state for state in record.get("path_classifications", []) if not state.get("excluded")
                        and (state.get("unmapped") if fact.category == "OTHER" else fact.category in state.get("categories", []))]
            if fact.target not in {state.get("path") for state in matching}:
                raise ValueError("change_fact_target_category_mismatch")
        if (fact.details or {}).get("status", record.get("status")) != record.get("status"):
            raise ValueError("change_fact_status_mismatch")
        bound.setdefault(key, []).append(fact)
    for key, categories in expected_categories.items():
        values = bound.get(key, [])
        if len(values) != len(categories) or {fact.category for fact in values} != categories:
            raise ValueError("change_fact_category_binding_incomplete")
    # 과거 추출기는 PACK Ref를 보존하지 않았다. 현재 매핑 일치를 과거 팩 인증으로 승격하지 않는다.
    return bound, {"status": "verified", "historical_pack_provenance": "verified" if refs else "not_recorded",
        "change_ref": change.ref().model_dump(mode="json"), "artifact_sha256": digest,
        "pack_ref": pack.ref().model_dump(mode="json"), "historical_pack_ref_verified": bool(refs)}


def _source_hash(repo: Path, commit: str, path: str | None) -> str | None:
    if path is None:
        return None
    if len(commit) not in (40, 64) or any(c not in "0123456789abcdef" for c in commit):
        raise ValueError("oracle_invalid_source_commit")
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(["git", "--no-replace-objects", "-c", "protocol.allow=never", "-c",
        "core.fsmonitor=false", "-C", str(repo), "cat-file", "blob", f"{commit}:{path}"],
        capture_output=True, env=env, check=False, timeout=30)
    if result.returncode:
        raise ValueError("oracle_source_blob_unavailable")
    return hashlib.sha256(result.stdout).hexdigest()


def _case_paths(record: dict) -> list[dict]:
    if "path_classifications" in record:
        return record["path_classifications"]
    # /1은 경로별 상태를 보존하지 않는다. 완전한 rename 대조를 추정하지 않는다.
    return [{"side": side, "path": record.get(key), "categories": record.get("categories", []),
             "excluded": record.get("excluded", False), "unmapped": record.get("unmapped", False),
             "legacy_record_only": True}
            for side, key in (("baseline", "before_path"), ("candidate", "after_path")) if record.get(key)]


def _patch_candidates(records: list[dict], selection: dict) -> tuple[set[str], dict]:
    """제품의 기존 선정 함수를 쓴다. 실행·정책·이월 자격은 평가하지 않는다."""
    checks = set(selection.get("patch_always", []))
    checks.update(cid for values in selection.get("engine_categories", {}).values() for cid in values)
    facts = []
    for index, record in enumerate(records):
        for category in record.get("categories", []) or ["OTHER"]:
            facts.append(SimpleNamespace(id=f"evaluation-{index}-{category}", category=category,
                related_checks=[], unmapped=record.get("unmapped", False)))
    pack = SimpleNamespace(change_selection=selection, checks=dict.fromkeys(checks),
                           manifest=SimpleNamespace(checks=sorted(checks)))
    triggers, _ = selection_triggers(pack, "ENGINE_PATCH", SimpleNamespace(facts=facts), InputDiff())
    return set(triggers), {cid: sorted({t.type for t in values}) for cid, values in sorted(triggers.items())}


def evaluate_oracle(oracle: dict, artifact: dict, selection: dict, *,
                    source_repository: Path | None = None, change_input: ChangeInput | None = None,
                    artifact_bytes: bytes | None = None, pack=None) -> dict:
    """부분 정답의 누락과 알려진 음성을 센다. 미라벨 항목을 오탐으로 계산하지 않는다."""
    if oracle.get("schema") != "guard.source-selection-oracle/1":
        raise ValueError("oracle_schema_unsupported")
    if artifact.get("schema") not in {"guard.engine-source-diff/1", "guard.engine-source-diff/2"}:
        raise ValueError("source_artifact_schema_unsupported")
    ids = [case["id"] for case in oracle["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("oracle_duplicate_case_id")
    baseline, candidate = artifact["baseline"]["commit"], artifact["candidate"]["commit"]
    bound_facts, change_binding = _change_binding(change_input, artifact, artifact_bytes, selection, pack)
    matched_cases = [case for case in oracle["cases"]
                     if case["baseline_commit"] == baseline and case["candidate_commit"] == candidate]
    records = [*artifact.get("changes", []), *artifact.get("excluded", [])]
    candidates, triggers = _patch_candidates(artifact.get("changes", []), selection)
    always = set(selection.get("patch_always", []))
    fallback = {cid for cid, values in triggers.items() if "UNMAPPED_ENGINE" in values}
    category_checks = selection.get("engine_categories", {})
    totals = dict(category_missing=0, category_required=0, category_forbidden=0,
        category_negative=0, wrong_exclusion=0, exclusion_assessed=0, unmapped_missing=0,
        unmapped_assessed=0, raw_check_missing=0, required_checks=0, selected_check_missing=0,
        unrelated_selected=0, negative_checks=0, source_mismatch=0, source_sides=0,
        artifact_hash_mismatch=0, artifact_sides=0, record_missing=0, unsupported=0, certainty_assessed=0,
        side_missing=0, side_labelled=0, side_path_wrong=0, side_paths=0, side_hash_wrong=0, side_hashes=0,
        side_category_missing=0, side_required=0, side_category_wrong=0, side_negative=0,
        side_exclusion_wrong=0, side_exclusions=0, side_unmapped_wrong=0, side_unmapped=0,
        side_unassessed=0, side_presence_unavailable=0)
    results = []
    for case in matched_cases:
        matches = [record for record in records if record.get("before_path") == case.get("before_path")
                   and record.get("after_path") == case.get("after_path")]
        if len(matches) != 1:
            totals["record_missing"] += 1
            results.append({"id": case["id"], "status": "record_missing_or_ambiguous",
                            "review_status": case["review_status"]})
            continue
        record = matches[0]
        observed = set(record.get("categories", []))
        expected = case["expected"]
        required_categories = set(expected.get("required_categories", []))
        forbidden_categories = set(expected.get("forbidden_categories", []))
        required_checks = set(expected.get("required_checks", []))
        forbidden_checks = set(expected.get("unrelated_checks", []))
        raw_checks = {cid for category in observed for cid in category_checks.get(category, [])}
        missing_categories = required_categories - observed
        unexpected_categories = forbidden_categories & observed
        raw_missing = required_checks - raw_checks
        selected_missing = required_checks - candidates
        # 사례의 무관 라벨은 해당 record의 원시 연결만 평가한다. 다른 변경과
        # always/fallback이 추가한 전역 후보를 이 사례의 오탐으로 세지 않는다.
        unrelated_selected = forbidden_checks & raw_checks
        totals["category_missing"] += len(missing_categories)
        totals["category_required"] += len(required_categories)
        totals["category_forbidden"] += len(unexpected_categories)
        totals["category_negative"] += len(forbidden_categories)
        totals["raw_check_missing"] += len(raw_missing)
        totals["required_checks"] += len(required_checks)
        totals["selected_check_missing"] += len(selected_missing)
        totals["unrelated_selected"] += len(unrelated_selected)
        totals["negative_checks"] += len(forbidden_checks)
        if expected.get("excluded") is not None:
            totals["exclusion_assessed"] += 1
            totals["wrong_exclusion"] += record.get("excluded", False) != expected["excluded"]
        if expected.get("unmapped") is True:
            totals["unmapped_assessed"] += 1
            totals["unmapped_missing"] += not record.get("unmapped", False)
        certainty_facts = bound_facts.get(_record_key(record), [])
        certainty_assessment = {"status": "not_assessed", "reason": "maximum_certainty_unlabelled"}
        maximum = expected.get("maximum_certainty")
        if maximum is not None:
            levels = {"UNKNOWN": 0, "PARTIAL": 1, "CONFIRMED": 2}
            if maximum not in levels:
                raise ValueError("oracle_invalid_maximum_certainty")
            if certainty_facts:
                totals["certainty_assessed"] += 1
                unsupported = any(levels[fact.certainty] > levels[maximum] for fact in certainty_facts)
                totals["unsupported"] += unsupported
                certainty_assessment = {"status": "evaluated", "maximum_certainty": maximum,
                    "facts": [{"id": fact.id, "category": fact.category, "target": fact.target,
                               "certainty": fact.certainty} for fact in certainty_facts], "unsupported": unsupported,
                    "scope": "bound CHG source facts; not historical pack approval or dynamic behavior"}
            else:
                certainty_assessment = {"status": "not_assessed", "reason": "change_fact_evidence_unavailable"}
        side_states = _case_paths(record)
        side_evaluation, side_totals = _side_labels(case, record, artifact["schema"])
        for key, value in side_totals.items():
            totals[key] += value
        totals["side_presence_unavailable"] += sum(row["status"] == "not_assessed" for row in side_evaluation)
        totals["side_unassessed"] += sum(row["status"] in {"not_assessed", "missing_or_ambiguous"}
                                         for row in side_evaluation)
        side_errors = []
        for side, commit, path_key, hash_key in (("baseline", baseline, "before_path", "before_sha256"),
                                               ("candidate", candidate, "after_path", "after_sha256")):
            path = case.get(path_key)
            expected_hash = case.get(hash_key)
            if path:
                totals["artifact_sides"] += 1
                if record.get(hash_key) != expected_hash:
                    totals["artifact_hash_mismatch"] += 1
                    side_errors.append(f"{side}:artifact_hash_mismatch")
            if path and source_repository is not None:
                totals["source_sides"] += 1
                try:
                    mismatch = _source_hash(source_repository, commit, path) != expected_hash
                except ValueError:
                    mismatch = True
                if mismatch:
                    totals["source_mismatch"] += 1
                    side_errors.append(f"{side}:source_blob_mismatch_or_unavailable")
        results.append({"id": case["id"], "status": "evaluated", "review_status": case["review_status"],
            "dataset": case["dataset"], "truth_scope": expected.get("truth_scope", "partial"),
            "categories": sorted(observed), "missing_categories": sorted(missing_categories),
            "known_wrong_categories": sorted(unexpected_categories), "raw_rule_checks": sorted(raw_checks),
            "required_checks": sorted(required_checks), "raw_rule_missing_checks": sorted(raw_missing),
            "selected_missing_checks": sorted(selected_missing), "known_unrelated_selected": sorted(unrelated_selected),
            "safety_always_covers": sorted(raw_missing & always),
            "safety_fallback_covers": sorted((raw_missing & fallback) - always),
            "other_changed_paths_cover": sorted((raw_missing & candidates) - always - fallback),
            "case_unrelated_patch_candidates": {
                "this_record_raw": sorted(unrelated_selected),
                "always": sorted(forbidden_checks & candidates & always),
                "conservative_fallback": sorted((forbidden_checks & fallback) - always),
                "other_changed_paths": sorted((forbidden_checks & candidates) - raw_checks - always - fallback)},
            "additional_selection": {"always": sorted(always - raw_checks),
                "conservative_fallback": sorted(fallback - raw_checks - always),
                "other_changed_paths": sorted(candidates - raw_checks - always - fallback)},
            "unlabelled_selected_checks": sorted(candidates - required_checks - forbidden_checks
                                                  - set(expected.get("optional_checks", []))),
            "path_classifications": side_states, "side_evaluation": side_evaluation,
            "certainty_assessment": certainty_assessment, "source_errors": side_errors})
    n = len(matched_cases)
    metrics = {
        "required_category_missing": _metric(totals["category_missing"], totals["category_required"]),
        "known_wrong_category": _metric(totals["category_forbidden"], totals["category_negative"],
            sum(case["expected"].get("truth_scope", "partial") != "complete" for case in matched_cases)),
        "wrong_exclusion": _metric(totals["wrong_exclusion"], totals["exclusion_assessed"], n - totals["exclusion_assessed"]),
        "unmapped_not_reported": _metric(totals["unmapped_missing"], totals["unmapped_assessed"], n - totals["unmapped_assessed"]),
        "raw_rule_required_check_missing": _metric(totals["raw_check_missing"], totals["required_checks"]),
        "patch_candidate_required_check_missing": _metric(totals["selected_check_missing"], totals["required_checks"]),
        "known_unrelated_selected": _metric(totals["unrelated_selected"], totals["negative_checks"], n),
        "source_blob_mismatch": _metric(totals["source_mismatch"], totals["source_sides"],
                                       totals["artifact_sides"] - totals["source_sides"]),
        "artifact_blob_hash_mismatch": _metric(totals["artifact_hash_mismatch"], totals["artifact_sides"]),
        "unsupported_confirmed": _metric(totals["unsupported"], totals["certainty_assessed"], n - totals["certainty_assessed"]),
        "missing_or_ambiguous_record": _metric(totals["record_missing"], n),
        "missing_or_ambiguous_side": _metric(totals["side_missing"],
            totals["side_labelled"] - totals["side_presence_unavailable"], totals["side_presence_unavailable"]),
        "side_path_mismatch": _metric(totals["side_path_wrong"], totals["side_paths"], totals["side_unassessed"]),
        "side_blob_hash_mismatch": _metric(totals["side_hash_wrong"], totals["side_hashes"], totals["side_unassessed"]),
        "side_required_category_missing": _metric(totals["side_category_missing"], totals["side_required"], totals["side_unassessed"]),
        "side_known_wrong_category": _metric(totals["side_category_wrong"], totals["side_negative"], totals["side_unassessed"]),
        "side_wrong_exclusion": _metric(totals["side_exclusion_wrong"], totals["side_exclusions"], totals["side_unassessed"]),
        "side_unmapped_mismatch": _metric(totals["side_unmapped_wrong"], totals["side_unmapped"], totals["side_unassessed"]),
    }
    assessments = {}
    for name, metric in metrics.items():
        reasons = []
        if not metric["denominator"]:
            reasons.append("no_assessed_labels_or_evidence")
        if metric["unassessed_cases"]:
            reasons.append("cases_not_fully_assessed")
        if any(case.get("review_status") != "human_reviewed" for case in matched_cases):
            reasons.append("human_review_pending")
        if any(case["expected"].get("truth_scope", "partial") != "complete" for case in matched_cases):
            reasons.append("partial_truth")
        if name == "source_blob_mismatch" and source_repository is None:
            reasons.append("source_repository_missing")
        if name == "unsupported_confirmed" and change_input is None:
            reasons.append("change_input_missing")
        if name.startswith("side_") or name == "missing_or_ambiguous_side":
            if not any(case["expected"].get("sides") for case in matched_cases):
                reasons.append("side_labels_missing")
            if totals["side_presence_unavailable"]:
                reasons.append("path_classifications_unavailable")
            if totals["side_missing"]:
                reasons.append("side_classification_missing_or_ambiguous")
        assessments[name] = {"status": "not_assessed" if not metric["denominator"] else
                            ("partial" if reasons else "assessed"), "reasons": reasons}
    return {"schema": "guard.source-selection-evaluation/1", "baseline_commit": baseline,
        "candidate_commit": candidate, "rules_sha256": artifact.get("rules_sha256"),
        "dataset_counts": {kind: sum(case["dataset"] == kind for case in matched_cases)
                           for kind in ("development", "regression", "independent")},
        "review_counts": {kind: sum(case["review_status"] == kind for case in matched_cases)
                          for kind in ("human_pending", "human_reviewed")},
        "matched_cases": n, "not_in_artifact": len(oracle["cases"]) - n,
        "metrics": metrics, "metric_assessments": assessments, "change_binding": change_binding,
        "cases": results, "patch_rule_candidates": sorted(candidates),
        "candidate_trigger_types": triggers,
        "limitations": ["Partial, human-pending truth is not independent accuracy proof.",
            "Unknown labels are unassessed, not false positives.",
            "Known-unrelated selection metrics cover only this record's raw rule/check links, not global candidates.",
            "Patch rule candidates are not PLAN selections, carried results, or executed checks.",
            "Always/fallback coverage does not repair missing raw category links.",
            "Source bytes are read from existing local git objects only; no engine runs.",
            "Exit zero means supplied proof was not found corrupt; it does not establish accuracy.",
            "CHG certainty uses bound source facts; historical PACK provenance may not have been recorded."]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, action="append", required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--source-repository", type=Path)
    parser.add_argument("--change-store", type=Path, help="기존 CHG 저장소를 읽기 전용으로 사용")
    parser.add_argument("--change", action="append", default=[], help="정확한 CHG-ID@revision, artifact마다 하나")
    parser.add_argument("--pack", type=Path, help="CHG 점검 연결 대조에 사용할 해시 검증 팩")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    oracle = json.loads(args.oracle.read_text(encoding="utf-8"))
    selection = yaml.safe_load(args.selection.read_text(encoding="utf-8"))
    pack, inputs = None, []
    if args.change or args.change_store or args.pack:
        if not (args.change and args.change_store and args.pack):
            raise ValueError("change_evaluation_requires_store_refs_and_pack")
        pack = load_pack(args.pack)
        if pack.change_selection != selection:
            raise ValueError("change_selection_pack_mismatch")
        store = Store.read_only(args.change_store)
        for reference in args.change:
            if not re.fullmatch(r"CHG-[A-Za-z0-9_-]+@[1-9][0-9]*", reference):
                raise ValueError("change_evaluation_requires_exact_revision")
            value = read_change(reference, pack, store)
            if value.change.ref() in [item.change.ref() for item in inputs]:
                raise ValueError("change_evaluation_duplicate_reference")
            inputs.append(value)
    results, used_changes = [], set()
    for path in args.artifact:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        matches = [value for value in inputs if any(a.sha256 == digest for a in value.change.artifacts or [])]
        if inputs and len(matches) != 1:
            raise ValueError("change_artifact_missing_or_ambiguous_reference")
        value = matches[0] if matches else None
        if value is not None:
            used_changes.add((value.change.id, value.change.revision))
        results.append(evaluate_oracle(oracle, json.loads(raw), selection, source_repository=args.source_repository,
                                     change_input=value, artifact_bytes=raw, pack=pack))
    if used_changes != {(value.change.id, value.change.revision) for value in inputs}:
        raise ValueError("change_reference_has_no_matching_artifact")
    output = {"schema": "guard.source-selection-evaluation-set/1",
              "oracle_sha256": hashlib.sha256(args.oracle.read_bytes()).hexdigest(),
              "selection_sha256": hashlib.sha256(args.selection.read_bytes()).hexdigest(), "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "artifact_count": len(results),
                      "matched_cases": sum(result["matched_cases"] for result in results)}, ensure_ascii=False))
    # Category/check misses remain reportable. Missing/corrupt source proof is a failed verification.
    return int(any(result["metrics"][key]["numerator"] for result in results for key in
                   ("source_blob_mismatch", "artifact_blob_hash_mismatch", "missing_or_ambiguous_record",
                    "missing_or_ambiguous_side", "side_path_mismatch", "side_blob_hash_mismatch")))


if __name__ == "__main__":
    raise SystemExit(main())
