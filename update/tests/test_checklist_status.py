"""추적표의 누락·등록 변동·과거 증거 승격 방지 계약."""
from __future__ import annotations

import copy
import csv
import io
from pathlib import Path

import pytest

from tools.checklist_status import (
    CSV_PATH, MD_PATH, ROOT, build_rows, historical_evidence, load_rows,
    main, registry_keys, render_csv, render_markdown,
)


def _inputs():
    check = {
        "id": "REQ-01", "area": "REQ", "revision": 6, "question": "직통 작업이 제한되는가?",
        "executor": "direct_route", "judge": {"plugin": "rule_table"},
        "policy_params": [{"key": "allowed_callers"}], "normal_control": "허용 요청은 동작",
        "challenge_steps": ["비허용 요청 대조"], "fail_criteria": "비허용 작업 실행",
        "unknown_criteria": "실제 경로 미확인", "required_observations": [{"key": "routes_tested"}],
    }
    matrix = {
        "id": "REQ-01", "executor_kind": "http_boundary", "grade": "A", "uncertain": "",
        "verification_status": "HISTORICAL_PASS", "remaining_scope": "미검토 경로",
    }
    kwargs = {"pack_revision": 23, "executor_keys": {"fake", "direct_route:real"},
              "judge_keys": {"rule_table"}, "evidence": {}}
    return [check], [matrix], kwargs


def test_all_pack_checks_are_tracked_once_and_source_order_is_preserved():
    rows = load_rows()
    with (ROOT / "docs/automation_matrix.csv").open(encoding="utf-8", newline="") as stream:
        original_ids = [r["id"] for r in csv.DictReader(stream)]
    assert [r["check_id"] for r in rows] == original_ids
    assert len(rows) == len({r["check_id"] for r in rows}) == 77
    assert {r["check_id"] for r in rows} == {p.stem for p in (ROOT / "packs/PACK-BASELINE/checks").glob("*.yaml")}
    assert all(r["normal_case"] and r["violation_case"] and r["missing_case"] and r["unsupported_case"] for r in rows)
    assert len({r["violation_case"] for r in rows}) > 60


@pytest.mark.parametrize("which", ["check", "matrix"])
def test_duplicate_id_is_rejected_instead_of_silently_overwriting(which):
    checks, matrix, kwargs = _inputs()
    (checks if which == "check" else matrix).append(copy.deepcopy((checks if which == "check" else matrix)[0]))
    with pytest.raises(ValueError, match=f"duplicate_{which}_id"):
        build_rows(checks, matrix, **kwargs)


def test_missing_or_extra_matrix_check_is_rejected():
    checks, matrix, kwargs = _inputs()
    with pytest.raises(ValueError, match="matrix_check_ids_differ"):
        build_rows(checks, [], **kwargs)
    matrix[0]["id"] = "REQ-99"
    with pytest.raises(ValueError, match="matrix_check_ids_differ"):
        build_rows(checks, matrix, **kwargs)


def test_real_registry_and_judge_removal_change_connected_status(tmp_path):
    checks, matrix, kwargs = _inputs()
    registry = tmp_path / "registry.py"
    registry.write_text('EXECUTORS: dict = {"fake": lambda cfg: None, "direct_route:real": lambda cfg: None}\n', encoding="utf-8")
    kwargs["executor_keys"] = registry_keys(registry, "EXECUTORS")
    assert build_rows(checks, matrix, **kwargs)[0]["implementation"] == "REAL_EXECUTOR_AND_JUDGE_CONNECTED"
    registry.write_text('EXECUTORS: dict = {"fake": lambda cfg: None}\n', encoding="utf-8")
    kwargs["executor_keys"] = registry_keys(registry, "EXECUTORS")
    assert build_rows(checks, matrix, **kwargs)[0]["real_executor_registered"] == "false"
    assert build_rows(checks, matrix, **kwargs)[0]["implementation"] == "PARTIALLY_CONNECTED"
    kwargs["executor_keys"] = {"direct_route:real"}
    kwargs["judge_keys"] = set()
    assert build_rows(checks, matrix, **kwargs)[0]["implementation"] == "PARTIALLY_CONNECTED"


def test_registry_reader_rejects_ambiguous_or_dynamic_keys(tmp_path):
    path = tmp_path / "registry.py"
    path.write_text('EXECUTORS: dict = {"same": 1, "same": 2}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate_registry_key"):
        registry_keys(path, "EXECUTORS")
    path.write_text('EXECUTORS: dict = {**other}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="registry_keys_not_literal"):
        registry_keys(path, "EXECUTORS")


def test_historical_pass_never_becomes_current_pack_measured():
    checks, matrix, kwargs = _inputs()
    kwargs["evidence"] = {"REQ-01": [{"pack": {"revision": 17}, "verdicts": {"security": "PASS"}}]}
    row = build_rows(checks, matrix, **kwargs)[0]
    assert row["pack_revision"] == "23"
    assert row["historical_pack_revisions"] == "17"
    assert row["historical_verification"] == "HISTORICAL_PASS"
    assert row["current_pack_actual_status"] == "DEFERRED_NOT_MEASURED"
    assert row["windows_cpu_status"] == "HISTORICAL_LIMITED_PROFILE_ONLY"


def test_empty_security_rules_are_counted_as_observation_only():
    checks, matrix, kwargs = _inputs()
    checks[0]["judge"]["params"] = {"pass_when_all": [], "fail_when_any": []}
    row, = build_rows(checks, matrix, **kwargs)
    assert row["real_executor_registered"] == row["judge_registered"] == "true"
    assert row["implementation"] == "OBSERVATION_ONLY_NO_SECURITY_RULES"
    assert "독립 근거" in row["next_experiment"]
    text = render_markdown([row])
    assert "실행·보안 판정 규칙 연결 0개, 관측 전용 1개" in text
    assert row["current_pack_actual_status"] == "DEFERRED_NOT_MEASURED"


def test_selected_history_preserves_refs_and_does_not_import_synthetic_wheel_runs():
    history = historical_evidence(ROOT)
    refs = [item for rows in history.values() for item in rows]
    assert refs
    assert all(item["source"].startswith("docs/evidence/automation-phase") for item in refs)
    assert all(item["pack"]["revision"] in {17, 18, 19} for item in refs)
    assert any(item.get("evidence_ref", {}).get("kind") == "EVD" for item in refs)
    assert any(item.get("result_ref", {}).get("kind") == "RES" for item in refs)
    assert "REQ-15" not in history and "REQ-16" not in history


def test_generation_is_deterministic_roundtrippable_and_matches_committed_outputs():
    rows = load_rows()
    before = copy.deepcopy(rows)
    first = render_csv(rows)
    assert first == render_csv(load_rows())
    assert list(csv.DictReader(io.StringIO(first))) == rows
    assert rows == before
    assert (ROOT / CSV_PATH).read_bytes() == first.encode("utf-8")
    assert (ROOT / MD_PATH).read_bytes() == render_markdown(rows).encode("utf-8")


def test_check_mode_rejects_a_stale_output_without_writing(tmp_path, monkeypatch):
    rows = build_rows(*_inputs()[:2], **_inputs()[2])
    (tmp_path / "docs").mkdir()
    csv_path = tmp_path / CSV_PATH
    csv_path.write_bytes(b"stale\n")
    (tmp_path / MD_PATH).write_text(render_markdown(rows), encoding="utf-8")
    monkeypatch.setattr("tools.checklist_status.load_rows", lambda root: rows)
    assert main(["--root", str(tmp_path), "--check"]) == 1
    assert csv_path.read_bytes() == b"stale\n"
