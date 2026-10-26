"""CHG 소스 사실의 무결성과 평가 범위를 오프라인에서 확인한다."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from guard.collectors.change import ChangeInput
from guard.models import DataOrigin, Ref, canonical_json, sha256_hex, utcnow
from guard.models.objects import Artifact, Change, ChangeEvidence, ChangeFact, Extractor, VersionPoint
from tools.evaluate_source_selection import evaluate_oracle


def bound_example(*, recorded_pack=True):
    selection = {"patch_always": ["REQ-01"], "engine_categories": {"AUTH": ["REQ-01"]}}
    reference = Ref(kind="PACK", id="PACK-EXAMPLE", revision=1, sha256="f" * 64)
    pack = SimpleNamespace(checks={"REQ-01": None}, change_selection=selection, ref=lambda: reference)
    states = [{"side": "baseline", "path": "auth.py", "categories": ["AUTH"],
               "excluded": False, "unmapped": False},
              {"side": "candidate", "path": "auth.py", "categories": ["AUTH"],
               "excluded": False, "unmapped": False}]
    record = {"status": "M", "before_path": "auth.py", "after_path": "auth.py",
              "before_sha256": "a" * 64, "after_sha256": "b" * 64,
              "categories": ["AUTH"], "unmapped": False, "excluded": False,
              "path_classifications": states}
    artifact = {"schema": "guard.engine-source-diff/2", "baseline": {"commit": "1" * 40},
                "candidate": {"commit": "2" * 40}, "rules_sha256": "c" * 64,
                "changes": [record], "excluded": []}
    raw = canonical_json(artifact)
    digest = sha256_hex(raw)
    path = f"artifacts/{digest}"
    fact = ChangeFact(id="source-1-auth", category="AUTH", target="auth.py",
        before={"path": "auth.py", "sha256": "a" * 64},
        after={"path": "auth.py", "sha256": "b" * 64}, certainty="PARTIAL", unmapped=False, related_checks=["REQ-01"],
        evidence=[ChangeEvidence(path=path, sha256=digest)], details={"path_classifications": states})
    change = Change(id="CHG-EXAMPLE", data_origin=DataOrigin.EXAMPLE,
        baseline=VersionPoint(version="EXAMPLE-before", source_commit="1" * 40),
        candidate=VersionPoint(version="EXAMPLE-after", source_commit="2" * 40),
        extractor=Extractor(name="git_diff", version="EXAMPLE"), facts=[fact],
        refs=[reference] if recorded_pack else [], analysis={"rules_sha256": "c" * 64},
        artifacts=[Artifact(name="engine-source-diff.json", path=path, sha256=digest,
                            bytes=len(raw), collected_at=utcnow(), collector="EXAMPLE")]).seal()
    oracle = {"schema": "guard.source-selection-oracle/1", "cases": [{
        "id": "example", "dataset": "regression", "review_status": "human_pending",
        "baseline_commit": "1" * 40, "candidate_commit": "2" * 40,
        "before_path": "auth.py", "after_path": "auth.py", "before_sha256": "a" * 64,
        "after_sha256": "b" * 64, "expected": {"truth_scope": "partial",
        "required_categories": ["AUTH"], "maximum_certainty": "PARTIAL"}}]}
    return oracle, artifact, selection, ChangeInput(change, {path: raw}), raw, pack


def evaluate_bound(values):
    oracle, artifact, selection, change_input, raw, pack = values
    return evaluate_oracle(oracle, artifact, selection, change_input=change_input, artifact_bytes=raw, pack=pack)


def replace_change(values, **updates):
    values = list(values)
    values[3] = ChangeInput(values[3].change.model_copy(update=updates).seal(), values[3].payloads)
    return values


@pytest.mark.parametrize("recorded_pack", [True, False])
def test_source_certainty_uses_bound_chg_facts_and_preserves_historical_pack_limit(recorded_pack):
    values = bound_example(recorded_pack=recorded_pack)
    result = evaluate_bound(values)
    assert result["metrics"]["unsupported_confirmed"] == {
        "numerator": 0, "denominator": 1, "unassessed_cases": 0}
    assert result["change_binding"]["change_ref"] == values[3].change.ref().model_dump(mode="json")
    assert result["change_binding"]["historical_pack_provenance"] == ("verified" if recorded_pack else "not_recorded")
    assert result["metric_assessments"]["unsupported_confirmed"]["status"] == "partial"


def test_reviewed_confirmed_fact_exceeds_partial_truth_even_if_record_has_no_certainty():
    values = bound_example()
    fact = values[3].change.facts[0].model_copy(update={"certainty": "CONFIRMED"})
    result = evaluate_bound(replace_change(values, facts=[fact], reviewed_by="EXAMPLE self-reported reviewer"))
    assert "certainty" not in values[1]["changes"][0]
    assert result["metrics"]["unsupported_confirmed"] == {
        "numerator": 1, "denominator": 1, "unassessed_cases": 0}


@pytest.mark.parametrize("field,value,reason", [
    ("baseline", VersionPoint(version="EXAMPLE", source_commit="3" * 40), "change_source_commits_mismatch"),
    ("analysis", {"rules_sha256": "d" * 64}, "change_rules_hash_mismatch"),
    ("refs", [Ref(kind="PACK", id="PACK-EXAMPLE", revision=2, sha256="f" * 64)], "change_pack_reference_mismatch"),
    ("facts", [], "change_fact_category_binding_incomplete")])
def test_validly_sealed_but_mixed_source_rules_pack_or_facts_are_rejected(field, value, reason):
    with pytest.raises(ValueError, match=reason):
        evaluate_bound(replace_change(bound_example(), **{field: value}))


@pytest.mark.parametrize("field,value,reason", [
    ("related_checks", [], "change_fact_pack_mapping_mismatch"),
    ("target", "unrelated.py", "change_fact_target_mismatch"),
    ("after", {"path": "auth.py", "sha256": "9" * 64}, "change_fact_record_mismatch"),
    ("details", {"path_classifications": []}, "change_fact_side_binding_mismatch")])
def test_fact_record_target_and_side_correspondence_are_checked(field, value, reason):
    values = bound_example()
    fact = values[3].change.facts[0].model_copy(update={field: value})
    with pytest.raises(ValueError, match=reason):
        evaluate_bound(replace_change(values, facts=[fact]))


def test_chg_payload_missing_or_tampered_is_not_evaluated_as_zero_failures():
    values = list(bound_example())
    values[3] = ChangeInput(values[3].change, {})
    with pytest.raises(ValueError, match="CHG artifact is missing or changed"):
        evaluate_bound(values)
    values = list(bound_example())
    path = next(iter(values[3].payloads))
    values[3] = ChangeInput(values[3].change, {path: values[4] + b" "})
    with pytest.raises(ValueError, match="CHG evidence hash mismatch"):
        evaluate_bound(values)


def test_missing_chg_hash_cannot_be_replaced_by_valid_artifact_hash():
    values = list(bound_example())
    values[3] = ChangeInput(values[3].change.model_copy(update={"content_sha256": "0" * 64}), values[3].payloads)
    with pytest.raises(ValueError, match="CHG content hash is invalid"):
        evaluate_bound(values)


def test_identical_json_with_different_bytes_is_not_the_bound_artifact():
    values = list(bound_example())
    values[4] += b"\r\n"
    with pytest.raises(ValueError, match="change_artifact_binding_missing"):
        evaluate_bound(values)


def test_selection_must_match_verified_pack_and_supplied_bytes_must_match_parsed_artifact():
    values = list(bound_example())
    values[2] = deepcopy(values[2])
    values[2]["patch_always"] = []
    with pytest.raises(ValueError, match="change_selection_pack_mismatch"):
        evaluate_bound(values)
    values = list(bound_example())
    values[1] = deepcopy(values[1])
    values[1]["rules_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="change_artifact_bytes_mismatch"):
        evaluate_bound(values)


@pytest.mark.parametrize("value", [True, None])
def test_known_fact_cannot_add_or_omit_unmapped_marker(value):
    values = bound_example()
    fact = values[3].change.facts[0].model_copy(update={"unmapped": value})
    with pytest.raises(ValueError, match="change_fact_unmapped_mismatch"):
        evaluate_bound(replace_change(values, facts=[fact]))


def unknown_example():
    values = list(bound_example())
    artifact = deepcopy(values[1])
    record = artifact["changes"][0]
    record.update(categories=[], unmapped=True)
    for state in record["path_classifications"]:
        state.update(categories=[], unmapped=True)
    raw = canonical_json(artifact)
    digest, previous = sha256_hex(raw), values[3].change
    path = f"artifacts/{digest}"
    fact = previous.facts[0].model_copy(update={"category": "OTHER", "certainty": "UNKNOWN",
        "unmapped": True, "related_checks": [], "evidence": [ChangeEvidence(path=path, sha256=digest)],
        "details": {"path_classifications": record["path_classifications"]}})
    change = previous.model_copy(update={"facts": [fact], "artifacts": [
        Artifact(name="engine-source-diff.json", path=path, sha256=digest, bytes=len(raw),
                 collected_at=utcnow(), collector="EXAMPLE")]}).seal()
    values[1], values[3], values[4] = artifact, ChangeInput(change, {path: raw}), raw
    return values


@pytest.mark.parametrize("value", [False, None])
def test_other_fact_cannot_hide_artifact_and_side_unmapped_marker(value):
    values = unknown_example()
    fact = values[3].change.facts[0].model_copy(update={"unmapped": value})
    with pytest.raises(ValueError, match="change_fact_unmapped_mismatch"):
        evaluate_bound(replace_change(values, facts=[fact]))


def test_valid_unknown_chg_preserves_conservative_candidate_expansion():
    result = evaluate_bound(unknown_example())
    assert result["change_binding"]["status"] == "verified"
    assert "UNMAPPED_ENGINE" in result["candidate_trigger_types"]["REQ-01"]
    assert result["metrics"]["unsupported_confirmed"]["denominator"] == 1


def test_resealed_artifact_record_cannot_hide_its_unmapped_side():
    values = unknown_example()
    artifact = deepcopy(values[1])
    artifact["changes"][0]["unmapped"] = False
    raw = canonical_json(artifact)
    digest, previous = sha256_hex(raw), values[3].change
    path = f"artifacts/{digest}"
    fact = previous.facts[0].model_copy(update={"evidence": [ChangeEvidence(path=path, sha256=digest)]})
    change = previous.model_copy(update={"facts": [fact], "artifacts": [
        Artifact(name="engine-source-diff.json", path=path, sha256=digest, bytes=len(raw),
                 collected_at=utcnow(), collector="EXAMPLE")]}).seal()
    values[1], values[3], values[4] = artifact, ChangeInput(change, {path: raw}), raw
    with pytest.raises(ValueError, match="change_record_unmapped_side_mismatch"):
        evaluate_bound(values)
