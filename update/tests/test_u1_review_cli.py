"""실제 diff CLI가 보존한 CHG를 평가 도구가 읽기 전용으로 결합한다."""

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.store import Store
from tests.builders import PACK_DIR
from tests.test_u1_path_selection_flow import AFTER_PATH, BEFORE_PATH, offline_only, renamed_source
from tools.evaluate_source_selection import main


def _tree(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def _inputs(tmp_path, renamed_source):
    root = tmp_path / "store"
    result = CliRunner().invoke(app, ["diff", "--from", "v1.0.0", "--to", "v1.0.1",
        "--repository", str(renamed_source), "--pack", str(PACK_DIR), "--store", str(root),
        "--config", str(tmp_path / "absent.toml")])
    assert result.exit_code == 0, result.output
    store = Store.read_only(root)
    change, = store.iter_kind("CHG")
    artifact = store.root / change.artifacts[0].path
    # The oracle comes from the source fixture's raw bytes and reviewed path
    # contracts, not from categories produced by the classifier under test.
    raw = (renamed_source / AFTER_PATH).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    expected = {"truth_scope": "partial", "required_categories": ["API_ROUTE"],
        "maximum_certainty": "PARTIAL", "unmapped": True,
        "sides": {
            "baseline": {"path": BEFORE_PATH, "sha256": sha,
                         "required_categories": ["API_ROUTE"], "excluded": False, "unmapped": False},
            "candidate": {"path": AFTER_PATH, "sha256": sha,
                          "forbidden_categories": ["API_ROUTE"], "excluded": False, "unmapped": True}}}
    oracle = {"schema": "guard.source-selection-oracle/1", "cases": [{
        "id": "EXAMPLE-rename", "review_status": "human_pending", "dataset": "regression",
        "baseline_commit": change.baseline.source_commit, "candidate_commit": change.candidate.source_commit,
        "before_path": BEFORE_PATH, "after_path": AFTER_PATH, "before_sha256": sha, "after_sha256": sha,
        "expected": expected}]}
    path = tmp_path / "oracle.json"
    path.write_text(json.dumps(oracle), encoding="utf-8")
    args = ["--oracle", str(path), "--artifact", str(artifact), "--selection", str(PACK_DIR / "change_selection.yaml"),
            "--source-repository", str(renamed_source)]
    return root, change, args


def test_saved_cli_change_binds_exact_artifact_and_certainty_without_writes(tmp_path, renamed_source, offline_only):
    root, change, args = _inputs(tmp_path, renamed_source)
    before = _tree(root)
    output = tmp_path / "evaluation.json"
    assert main([*args, "--pack", str(PACK_DIR), "--change-store", str(root), "--change", f"{change.id}@{change.revision}",
                 "--output", str(output)]) == 0
    result, = json.loads(output.read_text(encoding="utf-8"))["results"]
    assert result["metrics"]["source_blob_mismatch"]["numerator"] == 0
    assert result["metrics"]["artifact_blob_hash_mismatch"]["numerator"] == 0
    assert result["metrics"]["unsupported_confirmed"]["numerator"] == 0
    assert result["metrics"]["unsupported_confirmed"]["denominator"] > 0
    assert result["change_binding"]["historical_pack_provenance"] == "not_recorded"
    assert result["review_counts"]["human_reviewed"] == 0
    assert _tree(root) == before
    assert list(Store.read_only(root).iter_kind("EVD")) == []


def test_without_chg_certainty_is_explicitly_unassessed(tmp_path, renamed_source, offline_only):
    root, _, args = _inputs(tmp_path, renamed_source)
    before = _tree(root)
    output = tmp_path / "without-change.json"
    assert main([*args, "--output", str(output)]) == 0
    result, = json.loads(output.read_text(encoding="utf-8"))["results"]
    assert result["metrics"]["unsupported_confirmed"]["denominator"] == 0
    assert result["metric_assessments"]["unsupported_confirmed"]["status"] == "not_assessed"
    assert _tree(root) == before


def test_resealed_change_with_wrong_rule_provenance_is_rejected(tmp_path, renamed_source, offline_only):
    root, change, args = _inputs(tmp_path, renamed_source)
    newer = change.model_copy(update={"revision": 2, "analysis": {
        **change.analysis, "rules_sha256": "f" * 64}}).seal()
    Store(root).put(newer)
    before = _tree(root)
    output = tmp_path / "must-not-exist.json"
    with pytest.raises(ValueError):
        main([*args, "--pack", str(PACK_DIR), "--change-store", str(root), "--change", f"{newer.id}@2", "--output", str(output)])
    assert not output.exists()
    assert _tree(root) == before
