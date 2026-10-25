"""Verifier contracts using synthetic object graphs; no engine/network is run."""
from copy import deepcopy

import pytest

from guard.models.objects import object_from_dict
from guard.update_acceptance import CHECKS, assess_update
from tests.update_builders import build_pure_update_case as build_update_case


def patch_case(case, kind, change, choose=lambda obj: True):
    """Reseal changed inputs and every descendant Ref, avoiding stale-hash tests."""
    report, objects, pack = deepcopy(case[:2]) + (case[2],)
    refs = {}
    rewritten = []

    def replace(value):
        if isinstance(value, list):
            return [replace(item) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {"kind", "id", "revision", "sha256"}:
            return refs.get(tuple(value[key] for key in ("kind", "id", "revision", "sha256")), value)
        return {key: replace(item) for key, item in value.items()}

    for original in objects:
        current = replace(original)
        if original["kind"] == kind and choose(original):
            change(current)
        obj = object_from_dict(current).seal()
        old_ref = object_from_dict(original).ref()
        refs[tuple(getattr(old_ref, key) for key in ("kind", "id", "revision", "sha256"))] = obj.ref().model_dump(mode="json")
        rewritten.append(obj.to_dict())
    return next(obj for obj in rewritten if obj["id"] == report["id"]), rewritten, pack


def assess(case, **kw):
    return assess_update(*case, **kw)


def test_concrete_pass_pass_accepts_but_remains_nonofficial_and_cpu_only(tmp_path):
    case = build_update_case(tmp_path)
    before = deepcopy(case[:2])
    result = assess(case)
    assert result["accepted"] is True, result["problems"]
    assert result["evidence_contract_valid"] is True
    assert result["comparison_kind"] == "version-upgrade" and result["version_upgrade_verified"] is True
    assert result["official"] is False
    assert result["policy_status"] == result["pack_review_status"] == "DRAFT"
    assert tuple(result["checks"]) == CHECKS
    for check in result["checks"].values():
        assert check["comparison"] == check["functional_comparison"] == "NO_CHANGE"
        for side in ("baseline", "candidate"):
            assert check[side]["actual"] == {"security": "PASS", "functional": "PASS", "performance": "UNKNOWN"}
            assert check[side]["pass_gate_blocks"] == []
    assert case[:2] == before


@pytest.mark.parametrize("baseline,candidate,comparison", [
    (False, True, "REGRESSED"), (True, False, "FIXED"), (True, True, "STILL_FAILING"),
])
def test_confirmed_fail_is_valid_trial_and_never_overwritten_to_pass(tmp_path, baseline, candidate, comparison):
    result = assess(build_update_case(tmp_path, baseline_fail=baseline, candidate_fail=candidate))
    assert result["accepted"] is True, result["problems"]
    assert all(row["comparison"] == comparison for row in result["checks"].values())
    assert all(row["candidate"]["actual"]["security"] == ("FAIL" if candidate else "PASS")
               for row in result["checks"].values())


def test_distinct_digest_same_version_is_image_comparison_not_version_upgrade(tmp_path):
    result = assess(build_update_case(tmp_path, same_version=True))
    assert result["accepted"] is True, result["problems"]
    assert result["comparison_kind"] == "image-comparison"
    assert result["version_upgrade_verified"] is False


def test_same_digest_never_counts_as_two_images_even_with_different_repository_alias(tmp_path):
    case = build_update_case(tmp_path, same_image=True)
    case = patch_case(case, "ENV", lambda obj: obj["engine"].update(
        image_digest="other/repository@sha256:" + "a" * 64), lambda obj: "CANDIDATE" in obj["id"])
    result = assess(case)
    assert result["accepted"] is False
    assert result["evidence_contract_valid"] is True
    assert any("repository aliases" in problem for problem in result["problems"])


@pytest.mark.parametrize("diagnostic", [False, True])
def test_profile_change_preserves_not_comparable_and_can_never_claim_image_trial(tmp_path, diagnostic):
    result = assess(build_update_case(tmp_path, candidate_fail=True, different_config=True), diagnostic=diagnostic)
    assert result["evidence_contract_valid"] is True, result["problems"]
    assert result["accepted"] is False
    assert all(row["comparison"] == row["functional_comparison"] == "NOT_COMPARABLE"
               for row in result["checks"].values())
    assert all(row["comparison_reasons"] == ["config_sha256"] for row in result["checks"].values())
    assert all(row["candidate"]["actual"]["security"] == "FAIL" for row in result["checks"].values())
    assert any("NOT_COMPARABLE" in problem for problem in result["problems"])


def test_diagnostic_mode_never_counts_as_update_acceptance_even_if_conditions_equal(tmp_path):
    result = assess(build_update_case(tmp_path), diagnostic=True)
    assert result["evidence_contract_valid"] is True
    assert result["accepted"] is False
    assert any("profile diagnostic only" in problem for problem in result["problems"])


@pytest.mark.parametrize("missing", ["normal", "challenge", "observation"])
def test_collection_gaps_do_not_accept_measured_completed_as_success(tmp_path, missing):
    def mutate(cid, side, raw):
        if (cid, side) != ("REQ-01", "candidate"):
            return
        if missing == "normal":
            raw["normal_control"]["succeeded"] = False
        elif missing == "challenge":
            raw["challenges"][0]["attempted"] = False
        else:
            raw["observations"]["direct_outcomes"] = {"value": None, "collected": False}
    result = assess(build_update_case(tmp_path, mutate_evidence=mutate))
    assert result["accepted"] is False
    assert result["evidence_contract_valid"] is True, result["problems"]
    assert result["checks"]["REQ-01"]["candidate"]["pass_gate_blocks"]
    assert any("incomplete normal control/challenges/observations" in problem for problem in result["problems"])


def test_independent_fail_survives_normal_control_failure_and_trial_still_rejects(tmp_path):
    def mutate(cid, side, raw):
        if (cid, side) == ("REQ-01", "candidate"):
            raw["normal_control"]["succeeded"] = False
    result = assess(build_update_case(tmp_path, candidate_fail=True, mutate_evidence=mutate))
    assert result["accepted"] is False and result["evidence_contract_valid"] is True
    assert result["checks"]["REQ-01"]["candidate"]["actual"]["security"] == "FAIL"
    assert result["checks"]["REQ-01"]["candidate"]["pass_gate_blocks"]


def test_all_unknown_is_not_accepted_even_when_pinned_rules_and_collection_are_consistent(tmp_path):
    def mutate(cid, side, raw):
        for observation in raw["observations"].values():
            observation.update(value=None, collected=False)
    result = assess(build_update_case(tmp_path, mutate_evidence=mutate))
    assert result["evidence_contract_valid"] is True, result["problems"]
    assert result["accepted"] is False
    assert result["checks"]["REQ-01"]["candidate"]["actual"]["security"] == "UNKNOWN"
    assert all(row[side]["actual"]["security"] == "UNKNOWN"
               for row in result["checks"].values() for side in ("baseline", "candidate"))
    assert any("concrete security and functional" in problem for problem in result["problems"])


def test_cleanup_error_keeps_concrete_fail_but_rejects_trial(tmp_path):
    def mutate(cid, side, raw):
        if (cid, side) == ("PLT-01", "candidate"):
            raw["execution"]["cleanup_errors"] = ["synthetic owned container cleanup incomplete"]
    result = assess(build_update_case(tmp_path, candidate_fail=True, mutate_evidence=mutate))
    assert result["evidence_contract_valid"] is True, result["problems"]
    assert result["accepted"] is False
    assert result["checks"]["PLT-01"]["candidate"]["actual"]["security"] == "FAIL"
    assert any("cleanup incomplete" in problem for problem in result["problems"])


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("stage", ["", " after startup/execution failure"])
def test_product_managed_cleanup_limitation_cannot_be_lost_in_saved_analysis(tmp_path, side, stage):
    case = build_update_case(tmp_path, candidate_fail=True)
    case = patch_case(case, "RPT", lambda obj: obj["limitations"].append(
        f"{side}: cleanup incomplete{stage} (DockerError); guard.run=u123456789abc"))
    result = assess(case)
    assert result["evidence_contract_valid"] is True, result["problems"]
    assert result["accepted"] is False
    assert all(row["candidate"]["actual"]["security"] == "FAIL" for row in result["checks"].values())
    assert any(f"{side}: product-managed cleanup incomplete" in problem for problem in result["problems"])


def test_unavailable_separate_diagnostic_is_not_misclassified_as_cleanup_failure(tmp_path):
    case = patch_case(build_update_case(tmp_path), "RPT", lambda obj: obj["limitations"].extend([
        "baseline: separate run diagnostic unavailable (PermissionError)",
        "candidate: separate run diagnostic unavailable (DockerError)",
    ]))
    result = assess(case)
    assert result["evidence_contract_valid"] is True and result["accepted"] is True, result["problems"]


@pytest.mark.parametrize("gap", ["route", "token-challenge", "functional", "process"])
def test_confirmed_fail_never_hides_incomplete_subtrials(tmp_path, gap):
    cid = {"route": "REQ-01", "token-challenge": "REQ-09", "functional": "REQ-09", "process": "PLT-01"}[gap]
    def mutate(check_id, side, raw):
        if (check_id, side) != (cid, "candidate"):
            return
        obs = raw["observations"]
        if gap == "route":
            obs["routes_tested"]["value"] = ["POST /v1/completions"]
            obs["direct_outcomes"]["value"] = {"POST /v1/completions": 200}
        elif gap == "token-challenge":
            obs["challenges_planned"]["value"] = ["token_id"]
            obs["challenge_outcomes"]["value"] = {"token_id": "200"}
            obs["accepted_challenges"]["value"] = ["token_id"]
        elif gap == "functional":
            obs["functional_probes_planned"]["value"] = []
            obs["functional_probe_results"]["value"] = {}
        else:
            obs["process_scan_complete"]["value"] = False
    result = assess(build_update_case(tmp_path, candidate_fail=True, mutate_evidence=mutate))
    assert result["evidence_contract_valid"] is True, result["problems"]
    assert result["accepted"] is False
    assert result["checks"][cid]["candidate"]["actual"]["security"] == "FAIL"
    assert result["checks"][cid]["candidate"]["collection_blocks"]
    assert any("incomplete collection coverage" in problem for problem in result["problems"])


@pytest.mark.parametrize("change,expected", [
    (lambda raw: raw.update(data_origin="EXAMPLE"), "automated original measurement"),
    (lambda raw: raw["execution"]["executor"].update(name="fake"), "automated original measurement"),
    (lambda raw: raw["execution"]["executor"].update(default_judge="declared"), "automated original measurement"),
    (lambda raw: raw["execution"].update(finished_at=None), "execution timestamps"),
    (lambda raw: raw["comparability"].update(model_inventory_sha256="d" * 64), "CPU/model/fixture/config"),
    (lambda raw: raw.update(artifacts=[]), "probe log attachment"),
    (lambda raw: raw["challenges"].append(deepcopy(raw["challenges"][0])), "duplicate or unrecognized challenge"),
])
def test_invalid_source_or_identity_rejected_after_valid_resealing(tmp_path, change, expected):
    def mutate(cid, side, raw):
        if (cid, side) == ("REQ-01", "candidate"):
            change(raw)
    result = assess(build_update_case(tmp_path, mutate_evidence=mutate))
    assert result["accepted"] is False and result["evidence_contract_valid"] is False
    assert any(expected in problem for problem in result["problems"]), result["problems"]


def test_human_measurement_cannot_masquerade_as_automated_source(tmp_path):
    def mutate(cid, side, raw):
        if (cid, side) == ("REQ-01", "candidate"):
            raw["data_origin"] = "DECLARED"
            raw["execution"]["operator"] = {"by": "synthetic declared operator", "at": raw["created_at"]}
    result = assess(build_update_case(tmp_path, mutate_evidence=mutate))
    assert not result["accepted"] and not result["evidence_contract_valid"]
    assert any("automated original measurement" in problem for problem in result["problems"])


def test_damaged_object_hash_is_detected_before_verdict_analysis(tmp_path):
    report, objects, pack = build_update_case(tmp_path)
    next(obj for obj in objects if obj["kind"] == "EVD")["normal_control"]["succeeded"] = False
    result = assess_update(report, objects, pack)
    assert not result["accepted"] and not result["evidence_contract_valid"]
    assert any("object hash mismatch" in problem for problem in result["problems"])


def test_report_json_must_be_exact_original_stored_report(tmp_path):
    report, objects, pack = build_update_case(tmp_path)
    report["limitations"].append("not originally stored")
    report = object_from_dict(report).seal().to_dict()
    result = assess_update(report, objects, pack)
    assert not result["accepted"] and not result["evidence_contract_valid"]
    assert any("broken saved reference" in problem for problem in result["problems"])


@pytest.mark.parametrize("missing", ["EVD", "RES", "PLAN", "POL", "ENV"])
def test_missing_required_stored_object_cannot_be_filtered_out(tmp_path, missing):
    report, objects, pack = build_update_case(tmp_path)
    objects.remove(next(obj for obj in objects if obj["kind"] == missing))
    result = assess_update(report, objects, pack)
    assert not result["accepted"] and not result["evidence_contract_valid"]
    assert any("broken saved reference" in problem for problem in result["problems"])


@pytest.mark.parametrize("kind,change,reason", [
    ("PLAN", lambda obj: next(item for item in obj["items"] if item["selected"]).update(
        selected=False, not_selected_reason="POLICY_SCOPE"),
     "PLAN selection differs"),
    ("PLAN", lambda obj: obj["items"].append(deepcopy(obj["items"][0])), "duplicate PLAN check"),
    ("RPT", lambda obj: obj["by_check"].append(deepcopy(obj["by_check"][0])), "duplicate report check"),
    ("RPT", lambda obj: next(row for row in obj["by_check"] if row["selected"]).update(selected=False),
     "selected report checks differ"),
    ("RPT", lambda obj: obj.update(official=True), "official flag"),
    ("RES", lambda obj: obj["judge"].update(version="unsupported-version"), "judge version/parameters"),
    ("RES", lambda obj: obj["verdicts"].update(security="UNKNOWN"), "saved RES differs"),
])
def test_contract_mismatches_are_rejected_with_descendant_refs_resealed(tmp_path, kind, change, reason):
    result = assess(patch_case(build_update_case(tmp_path), kind, change))
    assert not result["accepted"] and not result["evidence_contract_valid"]
    assert any(reason in problem for problem in result["problems"]), result["problems"]


def test_report_cannot_relabel_config_diagnostic_as_fixed(tmp_path):
    def change(obj):
        for row in obj["by_check"]:
            if row["selected"]:
                row["comparison"] = "FIXED"
                row["functional_comparison"] = "NO_CHANGE"
                row["comparison_reasons"] = []
    result = assess(patch_case(build_update_case(tmp_path, candidate_fail=True, different_config=True), "RPT", change))
    assert not result["accepted"] and not result["evidence_contract_valid"]
    assert any("original comparison rules" in problem for problem in result["problems"])


def test_report_side_cannot_hide_result_fail(tmp_path):
    def change(obj):
        next(row for row in obj["by_check"] if row["check_id"] == "REQ-01")["candidate"]["security"] = "PASS"
    result = assess(patch_case(build_update_case(tmp_path, candidate_fail=True), "RPT", change))
    assert not result["accepted"] and not result["evidence_contract_valid"]
    assert result["checks"]["REQ-01"]["candidate"]["actual"]["security"] == "FAIL"
    assert any("original RES/ref" in problem for problem in result["problems"])


def test_swapping_side_environment_ref_is_invalid_even_when_all_hashes_are_resealed(tmp_path):
    case = build_update_case(tmp_path)
    baseline_env = next(obj for obj in case[1] if obj["kind"] == "ENV" and "BASELINE" in obj["id"])
    env_ref = object_from_dict(baseline_env).ref().model_dump(mode="json")
    def change(obj):
        obj["environment"] = env_ref
        obj["refs"] = [ref if ref["kind"] != "ENV" else env_ref for ref in obj["refs"]]
    result = assess(patch_case(case, "EVD", change, lambda obj: obj["side"] == "candidate"))
    assert not result["accepted"] and not result["evidence_contract_valid"]
    assert any("PLAN/environment mismatch" in problem for problem in result["problems"])
