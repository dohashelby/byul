"""EXAMPLE-only judgment contracts, historical definitions and portable reports.

No engine/network is used. Confirmed violation values are independent synthetic
fixture facts, never conclusions invented from an HTTP status or a mount name.
"""
from copy import deepcopy

import pytest

from guard.core.judge import judge_evidence, judge_plan
from guard.core.report import build_report
from guard.models import Evidence, canonical_json
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import get_pack
from guard.validation_evidence import inventory_tree
from tests.builders import T0, baseline_pack, evidence, policy
from tests.response_semantics_support import with_legacy_check
from tests.update_builders import VALUES as UPDATE_VALUES, build_update_case


@pytest.fixture(scope="module")
def pack():
    return baseline_pack()


def values(check_id):
    if check_id == "REQ-09":
        return {"max_input_tokens": 8, "rejection_codes": ["400", "413", "422"],
                "allowed_endpoints": ["POST /v1/completions"], "allowed_input_formats": ["token_ids"]}
    return {"forbidden_env_names": ["EXAMPLE_SECRET"], "forbidden_mount_paths": ["/EXAMPLE-forbidden"],
            "credential_candidate_paths": []}


def observations(check_id):
    if check_id == "REQ-09":
        return {"accepted_challenges": [], "challenge_outcomes": {"token_id": "400", "length": "400"},
                "challenges_planned": ["token_id", "length"],
                "challenge_rejection_results": {"token_id": "verified", "length": "verified"},
                "challenge_consumption": {"token_id": "unknown", "length": "unknown"},
                "confirmed_input_violations": [], "input_endpoints_tested": ["POST /v1/completions"],
                "input_formats_tested": ["token_ids"],
                "functional_probes_planned": ["before", "after_token_id", "after_length"],
                "functional_probe_results": dict.fromkeys(["before", "after_token_id", "after_length"], "pass")}
    return {"process_scan_complete": True, "worker_env_names": [],
            "exposed_credential_paths": [], "forbidden_mount_violations": [],
            "credential_candidates_planned": [], "credential_candidate_results": {},
            "credential_candidates_complete": True,
            "functional_probes_planned": ["POST /v1/completions"],
            "functional_probe_results": {"POST /v1/completions": "pass"}}


def fixture_evidence(check, data, *, claimed=None):
    obs = {key: {"value": deepcopy(value), "collected": True} for key, value in data.items()}
    executor = {"name": check.executor, "version": "EXAMPLE-only", "config_sha256": "0" * 64}
    if claimed is not None:
        executor.update(name="manual-entry", default_judge="declared")
        obs["declared_verdicts"] = {"value": {"security": claimed, "functional": "PASS", "performance": "UNKNOWN"},
                                    "collected": True}
        obs["declared_observation_notes"] = {
            "value": {key: "EXAMPLE synthetic observation note" for key in check.required_observations_prose},
            "collected": True}
    evd = evidence(check_id=check.id, observations=obs, execution={"status": "COMPLETED", "executor": executor},
        artifacts=[{"name": "EXAMPLE-only evidence", "path": "EXAMPLE-unit-only",
                    "sha256": "e" * 64, "bytes": 1, "collected_at": T0, "collector": "EXAMPLE-only"}])
    raw = evd.to_dict()
    raw["challenges"] = [{"step": step, "attempted": True} for step in check.challenge_steps]
    return Evidence.model_validate(raw).seal()


def judge(pack, check, data, *, supplied=None, claimed=None, missing=None):
    evd = fixture_evidence(check, data, claimed=claimed)
    if missing is not None:
        evd.observations.pop(missing)
        evd = evd.seal()
    before = canonical_json(evd.to_dict())
    pol = policy(pack.ref(), values={check.id: values(check.id) if supplied is None else supplied})
    result = judge_evidence(check, evd, pol, T0)
    assert canonical_json(evd.to_dict()) == before
    assert evd.data_origin.value == "EXAMPLE"
    return evd, result


def test_req09_same_raw_206_is_legacy_fail_but_new_unconfirmed_unknown(pack):
    data = observations("REQ-09")
    data["challenge_outcomes"]["length"] = "206"
    data["accepted_challenges"] = ["length"]
    data["challenge_rejection_results"]["length"] = "unverified"
    legacy = with_legacy_check(pack, "REQ-09").checks["REQ-09"]
    current = pack.checks["REQ-09"]
    assert legacy.revision == 5 and current.revision == 7
    assert len(current.required_observations) == 10
    _evd, old = judge(pack, legacy, data)
    evd, new = judge(pack, current, data)
    assert old.verdicts.security.value == "FAIL"
    assert new.verdicts.security.value == "UNKNOWN" and new.verdicts.functional.value == "PASS"
    assert evd.observations["challenge_outcomes"].value["length"] == "206"
    assert evd.observations["accepted_challenges"].value == ["length"]
    assert evd.observations["confirmed_input_violations"].value == []


@pytest.mark.parametrize("check_id", ["REQ-09", "PLT-04"])
def test_independent_confirmed_violation_survives_missing_required_observation(pack, check_id):
    data = observations(check_id)
    if check_id == "REQ-09":
        data["confirmed_input_violations"] = ["EXAMPLE-confirmed-consumption-or-damage"]
        missing = "challenge_rejection_results"
    else:
        data["forbidden_mount_violations"] = ["/EXAMPLE-forbidden"]
        missing = "credential_candidate_results"
    _evd, result = judge(pack, pack.checks[check_id], data, missing=missing)
    assert result.verdicts.security.value == "FAIL"
    assert result.verdicts.functional.value == "UNKNOWN"  # INV-11 still caps PASS.
    assert missing in result.missing_observations
    assert any("FAIL kept despite" in reason for reason in result.reasons)


@pytest.mark.parametrize("check_id", ["REQ-09", "PLT-04"])
@pytest.mark.parametrize("claim", ["PASS", "FAIL"])
def test_manual_pass_cannot_bypass_unknown_but_independent_declared_fail_remains(pack, check_id, claim):
    data = observations(check_id)
    if check_id == "REQ-09":
        data["challenge_outcomes"]["length"] = "200"
        data["accepted_challenges"] = ["length"]
        data["challenge_rejection_results"]["length"] = "unverified"
    else:
        data["credential_candidates_complete"] = False
    _evd, result = judge(pack, pack.checks[check_id], data, claimed=claim)
    assert result.judge.name == "declared"
    assert result.verdicts.security.value == ("UNKNOWN" if claim == "PASS" else "FAIL")
    assert result.verdicts.functional.value == "PASS"


@pytest.mark.parametrize("scenario", ["explicit_empty", "missing_policy", "candidate_unknown", "partial_scan"])
def test_credential_candidate_collection_is_separate_from_functional_health(pack, scenario):
    data, supplied = observations("PLT-04"), values("PLT-04")
    if scenario == "missing_policy":
        supplied.pop("credential_candidate_paths")
        data["credential_candidates_complete"] = False
    elif scenario == "candidate_unknown":
        supplied["credential_candidate_paths"] = data["credential_candidates_planned"] = ["/EXAMPLE-candidate"]
        data["credential_candidate_results"] = {"/EXAMPLE-candidate": "unknown"}
        data["credential_candidates_complete"] = False
    elif scenario == "partial_scan":
        data["process_scan_complete"] = False
    _evd, result = judge(pack, pack.checks["PLT-04"], data, supplied=supplied)
    assert result.verdicts.security.value == ("PASS" if scenario == "explicit_empty" else "UNKNOWN")
    assert result.verdicts.functional.value == "PASS"


def test_manual_completeness_flag_cannot_hide_a_missing_planned_candidate(pack):
    data, supplied = observations("PLT-04"), values("PLT-04")
    supplied["credential_candidate_paths"] = data["credential_candidates_planned"] = ["/EXAMPLE-a", "/EXAMPLE-b"]
    data["credential_candidate_results"] = {"/EXAMPLE-a": "absent"}
    data["credential_candidates_complete"] = True  # A contradictory manual assertion.
    _evd, result = judge(pack, pack.checks["PLT-04"], data, supplied=supplied, claimed="PASS")
    assert result.verdicts.security.value == "UNKNOWN"
    assert result.verdicts.functional.value == "PASS"


@pytest.mark.parametrize("basis", ["mount_policy", "environment", "candidate_permission"])
def test_confirmed_credential_basis_survives_other_incomplete_collection(pack, basis):
    data, supplied = observations("PLT-04"), values("PLT-04")
    data["credential_candidates_complete"] = False
    if basis == "mount_policy":
        data["forbidden_mount_violations"] = ["/EXAMPLE-forbidden"]
    elif basis == "environment":
        data["worker_env_names"] = ["EXAMPLE_SECRET"]
    else:
        supplied["credential_candidate_paths"] = data["credential_candidates_planned"] = ["/EXAMPLE-candidate"]
        data["credential_candidate_results"] = {"/EXAMPLE-candidate": "candidate_readable"}
    _evd, result = judge(pack, pack.checks["PLT-04"], data, supplied=supplied)
    assert result.verdicts.security.value == "FAIL" and result.verdicts.functional.value == "PASS"


def test_mount_policy_fail_does_not_turn_unknown_candidate_into_credential_access(pack):
    data = observations("PLT-04")
    data.update(exposed_credential_paths=["/EXAMPLE-forbidden/token"],
                forbidden_mount_violations=["/EXAMPLE-forbidden/token"],
                credential_candidates_planned=["/EXAMPLE-forbidden/token"],
                credential_candidate_results={"/EXAMPLE-forbidden/token": "unknown"},
                credential_candidates_complete=False)
    supplied = values("PLT-04")
    supplied["forbidden_mount_paths"] = supplied["credential_candidate_paths"] = ["/EXAMPLE-forbidden/token"]
    old = with_legacy_check(pack, "PLT-04").checks["PLT-04"]
    current = pack.checks["PLT-04"]
    assert old.revision == 2 and current.revision == 4 and len(current.required_observations) == 8
    _old_evd, old_result = judge(pack, old, data, supplied=supplied)
    evd, new_result = judge(pack, current, data, supplied=supplied)
    assert old_result.verdicts.security.value == new_result.verdicts.security.value == "FAIL"
    assert evd.observations["credential_candidate_results"].value == {"/EXAMPLE-forbidden/token": "unknown"}
    assert any("forbidden_mount_violations" in reason for reason in new_result.reasons)
    assert not any("exposed_credential_paths nonempty" in reason for reason in new_result.reasons)


def test_separated_axes_survive_report_export_snapshot_rejudge_and_original_bytes(tmp_path):
    def mutate(check_id, side, raw):
        raw["data_origin"] = "EXAMPLE"  # Never record this graph as an engine measurement.
        if side != "candidate":
            return
        obs = raw["observations"]
        if check_id == "REQ-09":
            obs["challenge_outcomes"]["value"]["length"] = "206"
            obs["accepted_challenges"]["value"] = ["length"]
            obs["challenge_rejection_results"]["value"]["length"] = "unverified"
        elif check_id == "PLT-04":
            obs["worker_env_names"]["value"] = list(UPDATE_VALUES["PLT-04"]["forbidden_env_names"])
            obs["credential_candidates_complete"]["value"] = False

    report, _objects, _pack = build_update_case(tmp_path, mutate_evidence=mutate)
    rows = {row["check_id"]: row for row in report["by_check"] if row["selected"]}
    assert rows["REQ-09"]["candidate"]["security"] == "UNKNOWN"
    assert rows["REQ-09"]["comparison"] == "UNKNOWN"
    assert rows["PLT-04"]["candidate"]["security"] == "FAIL"
    assert rows["PLT-04"]["comparison"] == "REGRESSED"
    assert all(rows[cid]["candidate"]["functional"] == "PASS"
               and rows[cid]["functional_comparison"] == "NO_CHANGE" for cid in ("REQ-09", "PLT-04"))
    assert report["official"] is False and report["data_origin"] == "EXAMPLE"
    for check_id in ("REQ-09", "PLT-04"):
        result_ref = rows[check_id]["candidate"]["result"]
        assert result_ref["id"].startswith("RES-") and result_ref["revision"] >= 1

    original = Store(tmp_path / "store")
    original_evds = {item.id: canonical_json(item.to_dict()) for item in original.iter_kind("EVD")}
    before = inventory_tree(original.root)
    export_bundle(original.root, tmp_path / "EXAMPLE-evidence.zip")
    restore_bundle(tmp_path / "EXAMPLE-evidence.zip", tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    plan = next(restored.iter_kind("PLAN"))
    saved_pack = get_pack(restored, plan.inputs.pack)
    judge_plan(restored, saved_pack, plan, T0)
    replay = build_report(restored, saved_pack, plan, T0).to_dict()
    assert replay["by_check"] == report["by_check"]
    assert replay["official"] is False and replay["data_origin"] == "EXAMPLE"
    assert {item.id: canonical_json(item.to_dict()) for item in restored.iter_kind("EVD")} == original_evds
    assert inventory_tree(original.root) == before
    assert original.verify() == restored.verify() == []
