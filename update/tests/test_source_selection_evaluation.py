"""정답의 모르는 부분·안전 확대·원본 변조를 구분하는 오프라인 평가 계약."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from tools.evaluate_source_selection import evaluate_oracle, main


def sample():
    record = {"status": "M", "before_path": "product.py", "after_path": "product.py",
              "before_sha256": "a" * 64, "after_sha256": "b" * 64,
              "categories": ["CACHE"], "excluded": False, "unmapped": False}
    expected = {"truth_scope": "partial", "required_categories": ["CACHE", "AUTH"],
                "forbidden_categories": ["OUTPUT"], "required_checks": ["REQ-01"],
                "optional_checks": ["REQ-15"], "unrelated_checks": [],
                "excluded": False, "unmapped": None}
    case = {"id": "example", "baseline_commit": "1" * 40, "candidate_commit": "2" * 40,
            "before_path": record["before_path"], "after_path": record["after_path"],
            "before_sha256": record["before_sha256"], "after_sha256": record["after_sha256"],
            "review_status": "human_pending", "dataset": "regression", "expected": expected}
    oracle = {"schema": "guard.source-selection-oracle/1", "cases": [case]}
    artifact = {"schema": "guard.engine-source-diff/2", "baseline": {"commit": "1" * 40},
                "candidate": {"commit": "2" * 40}, "changes": [record], "excluded": []}
    selection = {"patch_always": ["REQ-01"], "engine_categories": {
        "AUTH": ["REQ-01"], "CACHE": ["REQ-15"], "OUTPUT": ["REQ-11"]}}
    return oracle, artifact, selection


def test_always_coverage_does_not_hide_raw_missing_link():
    oracle, artifact, selection = sample()
    result = evaluate_oracle(oracle, artifact, selection)
    assert result["metrics"]["required_category_missing"] == {
        "numerator": 1, "denominator": 2, "unassessed_cases": 0}
    assert result["metrics"]["raw_rule_required_check_missing"]["numerator"] == 1
    assert result["metrics"]["patch_candidate_required_check_missing"]["numerator"] == 0
    assert result["cases"][0]["safety_always_covers"] == ["REQ-01"]
    assert result["dataset_counts"] == {"development": 0, "regression": 1, "independent": 0}
    assert result["review_counts"]["human_pending"] == 1


def test_conservative_fallback_and_unknown_truth_are_not_false_positive():
    oracle, artifact, selection = sample()
    artifact["changes"].append({"categories": [], "unmapped": True, "before_path": None,
                                "after_path": "unclassified.new"})
    result = evaluate_oracle(oracle, artifact, selection)
    assert result["patch_rule_candidates"] == ["REQ-01", "REQ-11", "REQ-15"]
    assert result["cases"][0]["additional_selection"]["conservative_fallback"] == ["REQ-11"]
    assert result["cases"][0]["unlabelled_selected_checks"] == ["REQ-11"]
    assert result["metrics"]["known_unrelated_selected"]["denominator"] == 0
    assert result["metrics"]["known_unrelated_selected"]["numerator"] == 0


def test_labelled_negative_category_and_check_are_measured():
    oracle, artifact, selection = sample()
    artifact["changes"][0]["categories"].append("OUTPUT")
    oracle["cases"][0]["expected"]["unrelated_checks"] = ["REQ-11"]
    result = evaluate_oracle(oracle, artifact, selection)
    assert result["metrics"]["known_wrong_category"]["numerator"] == 1
    assert result["metrics"]["known_wrong_category"]["denominator"] == 1
    assert result["metrics"]["known_unrelated_selected"]["numerator"] == 1
    assert result["metrics"]["known_unrelated_selected"]["denominator"] == 1


def test_other_changed_path_coverage_is_not_conservative_fallback():
    oracle, artifact, selection = sample()
    selection["patch_always"] = []
    artifact["changes"].append({"categories": ["AUTH"], "unmapped": False,
                                "before_path": None, "after_path": "auth.py"})
    result = evaluate_oracle(oracle, artifact, selection)
    case = result["cases"][0]
    assert case["raw_rule_missing_checks"] == ["REQ-01"]
    assert case["selected_missing_checks"] == []
    assert case["safety_always_covers"] == []
    assert case["safety_fallback_covers"] == []
    assert case["other_changed_paths_cover"] == ["REQ-01"]


@pytest.mark.parametrize("reason", ["always", "conservative_fallback", "other_changed_paths"])
def test_global_candidate_reasons_do_not_count_as_this_record_false_positive(reason):
    oracle, artifact, selection = sample()
    oracle["cases"][0]["expected"]["unrelated_checks"] = ["REQ-11"]
    if reason == "always":
        selection["patch_always"].append("REQ-11")
    else:
        artifact["changes"].append({"categories": [] if reason == "conservative_fallback" else ["OUTPUT"],
            "unmapped": reason == "conservative_fallback", "before_path": None, "after_path": "other.py"})
    result = evaluate_oracle(oracle, artifact, selection)
    assert "REQ-11" in result["patch_rule_candidates"]
    assert result["metrics"]["known_unrelated_selected"]["numerator"] == 0
    assert result["metrics"]["known_unrelated_selected"]["denominator"] == 1
    assert result["cases"][0]["known_unrelated_selected"] == []
    assert result["cases"][0]["case_unrelated_patch_candidates"][reason] == ["REQ-11"]


def test_rename_unknown_side_cannot_be_hidden_by_known_union():
    oracle, artifact, selection = sample()
    record = artifact["changes"][0]
    record["after_path"] = oracle["cases"][0]["after_path"] = "unknown.new"
    oracle["cases"][0]["expected"]["unmapped"] = True
    record["path_classifications"] = [
        {"side": "baseline", "path": "product.py", "categories": ["CACHE"],
         "excluded": False, "unmapped": False},
        {"side": "candidate", "path": "unknown.new", "categories": [],
         "excluded": False, "unmapped": True}]
    result = evaluate_oracle(oracle, artifact, selection)
    assert result["metrics"]["unmapped_not_reported"]["numerator"] == 1
    assert result["metrics"]["unmapped_not_reported"]["denominator"] == 1
    record["unmapped"] = True
    result = evaluate_oracle(oracle, artifact, selection)
    assert result["metrics"]["unmapped_not_reported"]["numerator"] == 0
    assert "REQ-11" in result["patch_rule_candidates"]
    assert result["cases"][0]["path_classifications"][1]["unmapped"] is True


def test_wrong_exclusion_and_source_hash_are_not_silent():
    oracle, artifact, selection = sample()
    record = artifact["changes"].pop()
    artifact["excluded"].append(record)
    record["excluded"] = True
    record["after_sha256"] = "0" * 64
    result = evaluate_oracle(oracle, artifact, selection)
    assert result["metrics"]["wrong_exclusion"]["numerator"] == 1
    assert result["metrics"]["artifact_blob_hash_mismatch"]["numerator"] == 1
    assert result["cases"][0]["source_errors"] == ["candidate:artifact_hash_mismatch"]


def test_absent_record_is_not_a_successful_zero_case():
    oracle, artifact, selection = sample()
    artifact["changes"] = []
    result = evaluate_oracle(oracle, artifact, selection)
    assert result["matched_cases"] == 1
    assert result["metrics"]["missing_or_ambiguous_record"]["numerator"] == 1
    assert result["cases"][0]["status"] == "record_missing_or_ambiguous"


def test_legacy_artifact_has_explicit_per_record_limit():
    oracle, artifact, selection = sample()
    artifact["schema"] = "guard.engine-source-diff/1"
    result = evaluate_oracle(oracle, artifact, selection)
    assert all(row["legacy_record_only"] for row in result["cases"][0]["path_classifications"])
    assert result["metrics"]["unsupported_confirmed"]["denominator"] == 0


def test_evaluation_does_not_mutate_source_inputs():
    oracle, artifact, selection = sample()
    inputs = deepcopy((oracle, artifact, selection))
    evaluate_oracle(oracle, artifact, selection)
    assert (oracle, artifact, selection) == inputs


def test_duplicate_case_id_is_rejected():
    oracle, artifact, selection = sample()
    oracle["cases"].append(deepcopy(oracle["cases"][0]))
    with pytest.raises(ValueError, match="oracle_duplicate_case_id"):
        evaluate_oracle(oracle, artifact, selection)


def test_local_blob_bytes_are_verified_without_worktree_conversion(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    def git(*args, input=None):
        return subprocess.run(["git", "-C", str(tmp_path), *args], input=input,
                              capture_output=True, check=True).stdout.decode().strip()
    before_raw, after_raw = b"old\r\n", b"new\n"
    before_blob = git("hash-object", "-w", "--stdin", input=before_raw)
    after_blob = git("hash-object", "-w", "--stdin", input=after_raw)
    before_tree = git("mktree", input=f"100644 blob {before_blob}\tproduct.py\n".encode())
    after_tree = git("mktree", input=f"100644 blob {after_blob}\tproduct.py\n".encode())
    commits = [git("-c", "user.name=EXAMPLE", "-c", "user.email=example@example.invalid",
                   "commit-tree", tree, "-m", "EXAMPLE") for tree in (before_tree, after_tree)]
    oracle, artifact, selection = sample()
    for side, sha, raw in zip(("before", "after"), commits, (before_raw, after_raw)):
        oracle["cases"][0][f"{side}_sha256"] = hashlib.sha256(raw).hexdigest()
        artifact["changes"][0][f"{side}_sha256"] = hashlib.sha256(raw).hexdigest()
    oracle["cases"][0]["baseline_commit"], oracle["cases"][0]["candidate_commit"] = commits
    artifact["baseline"]["commit"], artifact["candidate"]["commit"] = commits
    result = evaluate_oracle(oracle, artifact, selection, source_repository=tmp_path)
    assert result["metrics"]["source_blob_mismatch"]["numerator"] == 0
    assert result["metrics"]["source_blob_mismatch"]["denominator"] == 2
    oracle["cases"][0]["after_sha256"] = "0" * 64
    result = evaluate_oracle(oracle, artifact, selection, source_repository=tmp_path)
    assert result["metrics"]["source_blob_mismatch"]["numerator"] == 1
    assert result["metrics"]["artifact_blob_hash_mismatch"]["numerator"] == 1


def test_cli_writes_external_result_and_preserves_missing_links(tmp_path):
    oracle, artifact, selection = sample()
    paths = [tmp_path / name for name in ("oracle.json", "artifact.json", "selection.json")]
    for path, value in zip(paths, (oracle, artifact, selection)):
        path.write_text(json.dumps(value), encoding="utf-8")
    out = tmp_path / "output/evaluation.json"
    code = main(["--oracle", str(paths[0]), "--artifact", str(paths[1]),
                 "--selection", str(paths[2]), "--output", str(out)])
    assert code == 0
    result = json.loads(out.read_text())["results"][0]
    assert result["metrics"]["raw_rule_required_check_missing"]["numerator"] == 1
    assert result["metrics"]["source_blob_mismatch"]["denominator"] == 0
    artifact["changes"][0]["after_sha256"] = "0" * 64
    paths[1].write_text(json.dumps(artifact), encoding="utf-8")
    assert main(["--oracle", str(paths[0]), "--artifact", str(paths[1]),
                 "--selection", str(paths[2]), "--output", str(out)]) == 1


def test_repository_oracle_is_partial_human_pending_regression_only():
    oracle = json.loads((Path(__file__).parent / "fixtures/u1_source_oracle.json").read_text(encoding="utf-8"))
    original_ids = {"video-reader", "import-error-trimming", "compile-dtype-guard", "docker-abi-cache",
        "quantization-forwarding", "topk-cudagraph-reset", "cache-admission-cap", "zentorch-op-gate",
        "deepseek-fp8-op", "model-config-registry", "documentation-exclusion", "rust-output-grammar",
        "v1-attention-backend"}
    assert len(original_ids) == 13
    assert original_ids <= {case["id"] for case in oracle["cases"]}
    assert oracle["independent_evaluation"]["count"] == 0
    assert all(case["dataset"] == "regression" and case["review_status"] == "human_pending"
               and case["expected"]["truth_scope"] == "partial" for case in oracle["cases"])
    assert all(len(case[key]) == 64 for case in oracle["cases"] for key in
               ("before_sha256", "after_sha256") if case[key] is not None)
    assert all(case["source_fact"] and case["rationale"] and case["unverified_boundary"]
               for case in oracle["cases"])


def labelled_rename():
    oracle, artifact, selection = sample()
    record, case = artifact["changes"][0], oracle["cases"][0]
    record["status"] = "R100"
    record["after_path"] = case["after_path"] = "other.py"
    record["categories"] = ["AUTH", "CACHE"]
    record["path_classifications"] = [
        {"side": "baseline", "path": "product.py", "categories": ["CACHE"], "excluded": False, "unmapped": False},
        {"side": "candidate", "path": "other.py", "categories": ["AUTH"], "excluded": False, "unmapped": False}]
    case["expected"]["sides"] = {
        "baseline": {"path": "product.py", "sha256": "a" * 64, "required_categories": ["CACHE"],
                     "forbidden_categories": ["AUTH"], "excluded": False, "unmapped": False},
        "candidate": {"path": "other.py", "sha256": "b" * 64, "required_categories": ["AUTH"],
                      "forbidden_categories": ["CACHE"], "excluded": False, "unmapped": False}}
    return oracle, artifact, selection


def test_side_labels_detect_swap_even_when_record_union_stays_equal():
    oracle, artifact, selection = labelled_rename()
    before = evaluate_oracle(oracle, artifact, selection)
    assert before["metrics"]["side_required_category_missing"] == {
        "numerator": 0, "denominator": 2, "unassessed_cases": 0}
    artifact["changes"][0]["path_classifications"][0]["categories"] = ["AUTH"]
    artifact["changes"][0]["path_classifications"][1]["categories"] = ["CACHE"]
    after = evaluate_oracle(oracle, artifact, selection)
    assert after["metrics"]["required_category_missing"]["numerator"] == 0
    assert after["metrics"]["side_required_category_missing"]["numerator"] == 2
    assert after["metrics"]["side_known_wrong_category"]["numerator"] == 2


@pytest.mark.parametrize("key,value,metric", [
    ("path", "forged.py", "side_path_mismatch"),
    ("excluded", True, "side_wrong_exclusion"),
    ("unmapped", True, "side_unmapped_mismatch")])
def test_side_fields_are_verified_directly(key, value, metric):
    oracle, artifact, selection = labelled_rename()
    artifact["changes"][0]["path_classifications"][1][key] = value
    result = evaluate_oracle(oracle, artifact, selection)
    assert result["metrics"][metric]["numerator"] == 1
    assert result["metrics"][metric]["denominator"] == 2


def test_side_hash_mismatch_and_missing_side_are_not_hidden():
    oracle, artifact, selection = labelled_rename()
    artifact["changes"][0]["after_sha256"] = "f" * 64
    result = evaluate_oracle(oracle, artifact, selection)
    assert result["metrics"]["side_blob_hash_mismatch"]["numerator"] == 1
    artifact["changes"][0]["path_classifications"].pop()
    result = evaluate_oracle(oracle, artifact, selection)
    assert result["metrics"]["missing_or_ambiguous_side"]["numerator"] == 1
    assert result["metrics"]["missing_or_ambiguous_side"] == {
        "numerator": 1, "denominator": 2, "unassessed_cases": 0}
    assert result["metrics"]["side_required_category_missing"] == {
        "numerator": 0, "denominator": 1, "unassessed_cases": 1}
    assert result["cases"][0]["side_evaluation"][1]["status"] == "missing_or_ambiguous"


@pytest.mark.parametrize("ambiguous", [False, True])
def test_side_presence_failure_keeps_detailed_labels_unassessed(ambiguous):
    oracle, artifact, selection = labelled_rename()
    states = artifact["changes"][0]["path_classifications"]
    if ambiguous:
        states.append(deepcopy(states[1]))
    else:
        states.pop()
    result = evaluate_oracle(oracle, artifact, selection)
    assert result["metrics"]["missing_or_ambiguous_side"] == {
        "numerator": 1, "denominator": 2, "unassessed_cases": 0}
    for key in ("side_path_mismatch", "side_blob_hash_mismatch", "side_required_category_missing",
                "side_known_wrong_category", "side_wrong_exclusion", "side_unmapped_mismatch"):
        assert result["metrics"][key]["unassessed_cases"] == 1
        assert "side_classification_missing_or_ambiguous" in result["metric_assessments"][key]["reasons"]


def test_unavailable_and_missing_sides_have_distinct_presence_denominators():
    oracle, artifact, selection = labelled_rename()
    second = deepcopy(oracle["cases"][0])
    second["id"] = "second"
    second["before_path"], second["after_path"] = "second-before.py", "second-after.py"
    second["expected"]["sides"]["baseline"]["path"] = second["before_path"]
    second["expected"]["sides"]["candidate"]["path"] = second["after_path"]
    record = deepcopy(artifact["changes"][0])
    record["before_path"], record["after_path"] = second["before_path"], second["after_path"]
    record["path_classifications"][0]["path"] = second["before_path"]
    record["path_classifications"].pop()
    del artifact["changes"][0]["path_classifications"]
    oracle["cases"].append(second)
    artifact["changes"].append(record)
    result = evaluate_oracle(oracle, artifact, selection)
    assert result["metrics"]["missing_or_ambiguous_side"] == {
        "numerator": 1, "denominator": 2, "unassessed_cases": 2}
    assert result["metrics"]["side_required_category_missing"] == {
        "numerator": 0, "denominator": 1, "unassessed_cases": 3}
    reasons = result["metric_assessments"]["side_required_category_missing"]["reasons"]
    assert "path_classifications_unavailable" in reasons
    assert "side_classification_missing_or_ambiguous" in reasons


def test_legacy_side_truth_is_unassessed_instead_of_record_union_proxy():
    oracle, artifact, selection = labelled_rename()
    artifact["schema"] = "guard.engine-source-diff/1"
    result = evaluate_oracle(oracle, artifact, selection)
    assert result["metrics"]["side_required_category_missing"] == {
        "numerator": 0, "denominator": 0, "unassessed_cases": 2}
    assert result["metric_assessments"]["side_required_category_missing"]["status"] == "not_assessed"
    assert all(row["status"] == "not_assessed" for row in result["cases"][0]["side_evaluation"])


def test_artifact_certainty_is_not_authoritative_without_bound_chg():
    oracle, artifact, selection = sample()
    oracle["cases"][0]["expected"]["maximum_certainty"] = "PARTIAL"
    artifact["changes"][0]["certainty"] = "CONFIRMED"
    result = evaluate_oracle(oracle, artifact, selection)
    assert result["metrics"]["unsupported_confirmed"] == {
        "numerator": 0, "denominator": 0, "unassessed_cases": 1}
    assert result["metric_assessments"]["unsupported_confirmed"]["status"] == "not_assessed"
    assert "change_input_missing" in result["metric_assessments"]["unsupported_confirmed"]["reasons"]
    assert result["cases"][0]["certainty_assessment"]["reason"] == "change_fact_evidence_unavailable"


def test_zero_denominator_and_partial_human_truth_have_explicit_assessment():
    result = evaluate_oracle(*sample())
    assert result["metric_assessments"]["known_unrelated_selected"]["status"] == "not_assessed"
    assert result["metric_assessments"]["required_category_missing"]["status"] == "partial"
    assert "human_review_pending" in result["metric_assessments"]["required_category_missing"]["reasons"]
    assert "partial_truth" in result["metric_assessments"]["required_category_missing"]["reasons"]
    assert "side_labels_missing" in result["metric_assessments"]["side_path_mismatch"]["reasons"]


def test_oracle_side_source_cannot_disagree_with_case_source():
    oracle, artifact, selection = labelled_rename()
    oracle["cases"][0]["expected"]["sides"]["candidate"]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="oracle_side_source_conflict"):
        evaluate_oracle(oracle, artifact, selection)
