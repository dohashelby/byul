"""추적표의 누락·등록 변동·과거 증거 승격 방지 계약."""
from __future__ import annotations

import copy
import csv
import io
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

import json

from tools.checklist_status import (
    CSV_PATH, MD_PATH, ROOT, build_rows, gpu_runs, gpu_status, historical_evidence, load_phase1, load_rows,
    main, phase1_owner, registry_keys, render_csv, render_markdown,
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
    assert row["priority"] == "P1_OBSERVATION_GAP"  # No blanket P0 rerun of the old five.


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


def test_kubernetes_only_executor_is_connected_but_not_reported_as_real():
    checks, matrix, kwargs = _inputs()
    kwargs["executor_keys"] = {"fake", "direct_route:kubernetes"}
    row, = build_rows(checks, matrix, **kwargs)
    assert row["real_executor_registered"] == "false"
    assert row["supported_implementations"] == "kubernetes"
    assert row["implementation"] == "SUPPORTED_EXECUTOR_AND_JUDGE_CONNECTED"
    assert "kubernetes 구현만 있는 점검 1개" in render_markdown([row])


def test_fake_and_cpu_only_implementations_do_not_count_as_connected():
    checks, matrix, kwargs = _inputs()
    kwargs["executor_keys"] = {"fake", "direct_route:reference-cpu"}
    row, = build_rows(checks, matrix, **kwargs)
    assert row["supported_implementations"] == "reference-cpu"
    assert row["implementation"] == "PARTIALLY_CONNECTED"


def _gpu_record(status, **extra):
    return {"source": "s", "run": "r", "pack_revision": 32, "status": status, "verification_level": None,
            "data_origin": None, "verdicts": {"security": "UNKNOWN"}, "result_ref": "", "evidence_ref": "", **extra}


def test_gpu_status_takes_strongest_record_and_never_claims_whole_question():
    assert gpu_status([]) == "DEFERRED_NOT_MEASURED"
    assert gpu_status([_gpu_record("GPU_ATTEMPTED_NOT_COLLECTED")]) == "GPU_ATTEMPTED_NOT_COLLECTED"
    assert gpu_status([_gpu_record("GPU_ATTEMPTED_NOT_COLLECTED"), _gpu_record("GPU_COMPONENT_DECLARED_ONLY")]) == "GPU_COMPONENT_DECLARED_ONLY"
    assert gpu_status([_gpu_record("GPU_PARTIAL_EXAMPLE"), _gpu_record("GPU_PARTIAL_MEASURED")]) == "GPU_PARTIAL_MEASURED"
    checks, matrix, kwargs = _inputs()
    kwargs["gpu"] = {"REQ-01": [_gpu_record("GPU_PARTIAL_MEASURED", verdicts={"security": "FAIL"})]}
    row, = build_rows(checks, matrix, **kwargs)
    assert row["current_pack_actual_status"] == "GPU_PARTIAL_MEASURED"
    assert json.loads(row["gpu_runs"])[0]["verdicts"] == {"security": "FAIL"}
    kwargs["gpu"] = {"REQ-99": [_gpu_record("GPU_PARTIAL_MEASURED")]}
    with pytest.raises(ValueError, match="gpu_or_phase1_unknown_check"):
        build_rows(checks, matrix, **kwargs)


def test_real_gpu_summaries_keep_exact_refs_and_separate_declared_components():
    runs = gpu_runs(ROOT)
    assert {r["pack_revision"] for rows in runs.values() for r in rows} == {32, 33}
    old11 = [r for r in runs["REQ-11"] if r["pack_revision"] == 32]
    assert old11 and not any(r["current_definition"] for r in old11)  # rev6 기록은 이력만
    assert all(r["current_definition"] for cid, rows in runs.items() if cid != "REQ-11" for r in rows)
    assert gpu_status(runs["REQ-11"]) == "GPU_PARTIAL_EXAMPLE"  # 팩33 rev7 기록에서만
    assert gpu_status(old11) == "DEFERRED_NOT_MEASURED"
    assert all(r["result_ref"].startswith("RES-") for rows in runs.values() for r in rows)
    assert gpu_status(runs["LIF-13"]) == "GPU_COMPONENT_DECLARED_ONLY"
    assert gpu_status(runs["PLT-01"]) == "GPU_PARTIAL_MEASURED"
    assert gpu_status(runs["REQ-05"]) == "GPU_PARTIAL_EXAMPLE"
    assert "OBS-03" not in runs  # 11-02 PACK31 결과는 현행 팩 기록이 아니다.
    first = [r for r in runs["REQ-17"] if r["source"].endswith("GPU_RESULTS.json")]
    assert first and {r["status"] for r in first} == {"GPU_ATTEMPTED_NOT_COLLECTED"}


@pytest.mark.parametrize("flavour", [PureWindowsPath, PurePosixPath])
@pytest.mark.parametrize("family", ["A", "B"])
def test_gpu_summary_order_is_identical_across_path_flavours(tmp_path, flavour, family):
    """같은 evidence 파일을 플랫폼별 Path 비교로 재정렬하지 않는다."""
    from tools.checklist_status import GPU_A_GLOB, GPU_B_GLOB
    names = ["REQ-18.json", "dev1.json"]
    prefix = "docs/gpu-b"
    if family == "A":
        names = ["gpu-a-REQ-18.json", "gpu-a-dev1.json"]
        prefix = "docs/evidence"
    class SummaryPath(flavour):
        def read_text(self, **kwargs):
            if family == "A":
                result = {"id": "RES-example", "revision": 1, "check_id": "REQ-18",
                          "verification_level": "EXAMPLE", "data_origin": "EXAMPLE", "verdicts": {}}
                evidence = {"id": "EVD-example", "revision": 1, "execution": {"status": "COMPLETED"}}
                return json.dumps({"schema": "byul-gpu-a-check-summary/1", "runs": [{
                    "run": self.name, "pack_ref": {"revision": 33}, "evidence_kind": "automated",
                    "results": [result], "evidence": [evidence]}]})
            return json.dumps({"schema": "byul-independent-GPU-public-summary/1", "runs": [{
                "episode": self.name, "check_id": "REQ-18", "whole_question_complete": False,
                "EVD_status": "COMPLETED", "EVD_origin": "EXAMPLE", "verification_level": "EXAMPLE",
                "verdicts": {}, "refs": [{"kind": "PACK", "revision": 33}]}]})
    class ExampleRoot:
        def __fspath__(self):
            return "fixture"
        def __truediv__(self, other):
            # 현행 팩 판본은 하드코딩하지 않고 실제 팩 파일에서 읽는다(엔진·판본 앵커링 금지).
            if other in ("packs/PACK-BASELINE", "reference/pack-definitions/PACK-BASELINE.json"):
                return ROOT / other
            return tmp_path / "no-first-run.json"
        def glob(self, pattern):
            expected = GPU_A_GLOB if family == "A" else GPU_B_GLOB
            return [SummaryPath("fixture") / prefix / name for name in reversed(names)] if pattern == expected else []
    rows = gpu_runs(ExampleRoot())["REQ-18"]
    assert [row["run"] for row in rows] == names
    assert [row["source"] for row in rows] == [prefix + "/" + name for name in names]
    assert all(row["status"] == "GPU_PARTIAL_EXAMPLE" for row in rows)


def test_gpu_summary_from_other_pack_or_unknown_schema_is_rejected(tmp_path):
    (tmp_path / "docs/gpu-b").mkdir(parents=True)
    run = {"episode": "M1", "check_id": "PLT-01", "EVD_status": "COMPLETED", "EVD_origin": "MEASURED",
           "verification_level": "MEASURED", "verdicts": {}, "whole_question_complete": False,
           "refs": [{"kind": "PACK", "id": "PACK-BASELINE", "revision": 31, "sha256": "x"}]}
    path = tmp_path / "docs/gpu-b/x.json"
    path.write_text(json.dumps({"schema": "byul-independent-GPU-public-summary/1", "runs": [run]}), encoding="utf-8")
    with pytest.raises(ValueError, match="gpu_run_not_current_pack"):
        gpu_runs(tmp_path)
    run["refs"][0]["revision"] = 32
    run["whole_question_complete"] = True
    path.write_text(json.dumps({"schema": "byul-independent-GPU-public-summary/1", "runs": [run]}), encoding="utf-8")
    with pytest.raises(ValueError, match="gpu_b_whole_question_flag"):
        gpu_runs(tmp_path)
    path.write_text(json.dumps({"schema": "other", "runs": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="gpu_b_schema"):
        gpu_runs(tmp_path)


def test_phase1_owner_follows_executor_files_and_state_files_are_owner_scoped(tmp_path):
    assert phase1_owner({"executor": "engine_sensor"}) == "dev-1"
    assert phase1_owner({"executor": "supply_boot"}) == "gpu"
    assert phase1_owner({"executor": None, "retired": True}) == ""
    rows = load_rows()
    owners = {r["check_id"]: r["phase1_owner"] for r in rows}
    assert sum(v == "dev-1" for v in owners.values()) == 26 and sum(v == "gpu" for v in owners.values()) == 43
    assert all(r["phase1_state"] != "UNSET" for r in rows)
    (tmp_path / "docs/phase1").mkdir(parents=True)
    (tmp_path / "docs/phase1/dev-1.yaml").write_text("owner: gpu\nchecks: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="phase1_owner_mismatch"):
        load_phase1(tmp_path)
    (tmp_path / "docs/phase1/dev-1.yaml").write_text("owner: dev-1\nchecks: {REQ-01: {state: PASS}}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="phase1_state"):
        load_phase1(tmp_path)
    checks, matrix, kwargs = _inputs()
    kwargs["phase1"] = {"REQ-01": {"owner": "gpu", "state": "DONE", "note": ""}}
    with pytest.raises(ValueError, match="phase1_wrong_owner"):
        build_rows(checks, matrix, **kwargs)
