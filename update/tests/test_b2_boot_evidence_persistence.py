"""S5 boot 저장 오류의 원 사실 보존. CPU role-I/O fixture이며 실 worker가 아니다."""
from copy import deepcopy
from pathlib import Path

import pytest

from guard.core.judge import judge_evidence, judge_plan
from guard.core.run import RunConfig, _normalize, run_plan
from guard.core.select import select
from guard.executors import kubernetes_supply_boot as boot, registry, supply_boot_contract as contract
from guard.models import Evidence, canonical_json, new_id, sha256_hex, utcnow
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import dummy_ref, environment, policy
from tests.test_dynamic_code_cpu import row as model_row
from tests.test_supply_boot_record_access import PACK, SOURCE, VALUES, RECORD_CASES, base_values, native, part, read_row

CHECKS = ("SUP-05", "SUP-06", "SUP-09", "SUP-10", "SUP-11")
FAULTS = ("mkdir", "write", "collision")
ORIGINAL = b"EXAMPLE-existing-evidence-preserved"


def rows(cid, violation):
    if cid == "SUP-06":
        return [{"rank": 0, "required_missing": violation, "ready_accepted": True}]
    if cid == "SUP-09":
        return [model_row(events=[{"phase": "import"}] if violation else [],
                          outcome="execution_error" if violation else "rejected_at_trust_gate")]
    if cid == "SUP-10":
        return [{"case_id": "general-disabled", "status": "completed", "target_started": True,
                 "events": [{"plugin": "byul-unapproved", "phase": "import"}] if violation else []}]
    if cid == "SUP-11":
        return [model_row(), model_row(mode="O", events=[{"phase": "import"}] if violation else [],
                                       outcome="accepted" if violation else "rejected_at_trust_gate")]
    for check_id, bad, good, _ in RECORD_CASES:
        if check_id == cid:
            return [deepcopy(bad if violation else good)]
    if cid == "LIF-02":
        return [{"attempt": 0, "at_ms": 0, "bytes": 99 if violation else 1,
                 "outcome": "failed" if violation else "complete", "temporary_left": False}]
    if cid == "PLT-05":
        return [{"case": "expired", "identity_sha256": "other" if violation else "i",
                 "source": "cache" if violation else "approved", "outcome": "fetched" if violation else "denied"}]
    return []


def collected(env, cid, values, *, violation=True):
    """기존 행 fixture를 원 binding validator/evaluator로 소비한다. 등록은 바꾸지 않는다."""
    result = {}
    for role in contract.ROLES:
        bad = violation and role == "challenge"
        item = part(role, cid, values, rows=rows(cid, bad), nat=native([read_row(cid == "SUP-05" and bad)]))
        packet = item["packet"]
        packet["environment_ref"] = env.ref().model_dump(mode="json")
        packet["target_sha256"] = values[f"supply_{role}_target_sha256"]
        contract.validate_record(packet, check_id=cid, role=role, environment_ref=env.ref().model_dump(mode="json"),
            target_sha256=values[f"supply_{role}_target_sha256"], source_sha256=values["supply_record_source_sha256"])
        item["row"]["record_sha256"] = sha256_hex(canonical_json(packet))
        item["evaluated"] = contract.evaluate_record(cid, packet, values)
        result[role] = item
    return result


def storage_fault(patch, workdir, fault):
    summary = workdir / "supply_boot_summary.json"
    if fault == "mkdir":
        workdir.parent.mkdir(parents=True, exist_ok=True)
        workdir.write_bytes(ORIGINAL)
    elif fault == "write":
        original = Path.open
        def opened(path, mode="r", *args, **kwargs):
            if path == summary and any(flag in mode for flag in "wx"):
                raise PermissionError("EXAMPLE-PRIVATE-STORAGE-ERROR")
            return original(path, mode, *args, **kwargs)
        patch.setattr(Path, "open", opened)
    else:
        workdir.mkdir(parents=True, exist_ok=True)
        summary.write_bytes(ORIGINAL)
    return workdir if fault == "mkdir" else summary


def assert_preserved(draft, expected, normal, fault):
    assert draft.status.value == "ABORTED" and draft.data_origin.value == "EXAMPLE"
    assert draft.normal_control.succeeded == normal
    assert draft.observations["supply_boot_sessions"].value == [expected[r]["row"] for r in contract.ROLES]
    assert not draft.observations["supply_boot_complete"].value
    code = "SUPPLY_BOOT_EVIDENCE_DIRECTORY_UNAVAILABLE" if fault == "mkdir" else "SUPPLY_BOOT_SUMMARY_ARTIFACT_UNAVAILABLE"
    assert code in draft.cleanup_errors and code in draft.abort_reason
    assert code in draft.observations["supply_boot_missing"].value
    assert not draft.observations["supply_boot_record_authenticated"].value
    assert not draft.artifacts
    assert "EXAMPLE-PRIVATE-STORAGE-ERROR" not in canonical_json(draft.model_dump(mode="json")).decode()


@pytest.mark.parametrize("cid", CHECKS)
@pytest.mark.parametrize("fault", FAULTS)
def test_assigned_boot_storage_fault_keeps_collected_fail(tmp_path, monkeypatch, cid, fault):
    env = environment(); values = base_values(env, cid)
    parts = collected(env, cid, values)
    executor = registry.build_executor("supply_boot", "kubernetes", {"supply_boot": {"normal": {}, "challenge": {}}})
    expected_violations, _, _, normal, _, _ = executor._project(PACK.checks[cid], parts)
    assert expected_violations
    workdir = tmp_path / "evidence"
    original = storage_fault(monkeypatch, workdir, fault)
    draft = executor._draft(PACK.checks[cid], env, values, workdir, utcnow(), parts)
    assert_preserved(draft, parts, normal, fault)
    assert draft.observations["supply_boot_violations"].value == expected_violations
    evd = Evidence(id="EVD-B2-IO", data_origin=draft.data_origin, plan=dummy_ref("PLAN"), environment=env.ref(),
        check_id=cid, side="single", execution={"status": draft.status}, **_normalize(PACK.checks[cid], draft)).seal()
    result = judge_evidence(PACK.checks[cid], evd, policy(PACK.ref(), values={cid: values}), utcnow())
    assert result.verdicts.security.value == "FAIL"
    assert values["supply_record_source_sha256"] == SOURCE
    if fault != "write":
        assert original.read_bytes() == ORIGINAL


@pytest.mark.parametrize("fault", FAULTS)
def test_clean_native_control_with_storage_gap_never_becomes_pass(tmp_path, monkeypatch, fault):
    env = environment(); cid = "SUP-05"; values = base_values(env, cid)
    parts = collected(env, cid, values, violation=False)
    executor = registry.build_executor("supply_boot", "kubernetes", {"supply_boot": {"normal": {}, "challenge": {}}})
    storage_fault(monkeypatch, tmp_path / "evidence", fault)
    draft = executor._draft(PACK.checks[cid], env, values, tmp_path / "evidence", utcnow(), parts)
    assert_preserved(draft, parts, True, fault)
    assert draft.observations["supply_boot_violations"].value == []
    evd = Evidence(id="EVD-B2-CLEAN-IO", data_origin=draft.data_origin, plan=dummy_ref("PLAN"), environment=env.ref(),
        check_id=cid, side="single", execution={"status": draft.status}, **_normalize(PACK.checks[cid], draft)).seal()
    assert judge_evidence(PACK.checks[cid], evd, policy(PACK.ref(), values={cid: values}), utcnow()).verdicts.security.value == "UNKNOWN"


@pytest.mark.parametrize("fault", FAULTS)
def test_registered_collection_draft_evd_and_judge_keep_fail(tmp_path, monkeypatch, fault):
    """원 factory/run/정규화/Store/judge; 역할 I/O만 재생, K8s admission 실증 아님."""
    cid = "SUP-06"; env = environment(); values = base_values(env, cid)
    parts = collected(env, cid, values)
    calls = []
    def read_role(self, check, selected_env, selected_values, role):
        # @35: guard run opens the validity window; every other value is the policy/binding value given.
        window = ("supply_validity_start", "supply_validity_end")
        assert selected_env.ref() == env.ref()
        assert {k: v for k, v in selected_values.items() if k not in window} == {k: v for k, v in values.items() if k not in window}
        assert all(selected_values[k] != values[k] for k in window)
        item = deepcopy(parts[role])
        contract.validate_record(item["packet"], check_id=cid, role=role,
            environment_ref=selected_env.ref().model_dump(mode="json"),
            target_sha256=selected_values[f"supply_{role}_target_sha256"],
            source_sha256=selected_values["supply_record_source_sha256"])
        calls.append(role)
        return item
    monkeypatch.setattr(boot.KubernetesSupplyBootExecutor, "_collect_role", read_role)
    store = Store(tmp_path / "store"); put_pack(store, PACK.path)
    from tests.run_binding_support import fixed_file_hashes, split
    # @35: guard run computes the binding; only the double's file hashes are fixed.
    policy_values, file_hashes = split(PACK.checks[cid], values)
    pol = policy(PACK.ref(), scope={"include": [cid]}, values={cid: policy_values})
    store.put_many([env, pol]); plan = select(PACK, [env], pol); store.put(plan)
    # 작업 폴더는 실행 시도마다 따로다(work/<plan>/<attempt>/<check>/<side>). 시도 ID를 정해 같은 경로에 장애를 둔다.
    attempt = new_id("ATTEMPT")
    workdir = store.root / "work" / plan.id / attempt / cid / "single"
    original = storage_fault(monkeypatch, workdir, fault)
    with fixed_file_hashes(file_hashes):
        evd, = run_plan(store, PACK, plan, RunConfig(executors={"supply_boot": "kubernetes"}, adapter="vllm", attempt_id=attempt,
            raw={"supply_boot": {"normal": {}, "challenge": {}}}))
    result, = judge_plan(store, PACK, plan, as_of=utcnow())
    assert result.verdicts.security.value == "FAIL"
    assert calls == list(contract.ROLES)
    assert evd.execution.status.value == "ABORTED" and evd.data_origin.value == "EXAMPLE"
    assert evd.observations["supply_boot_violations"].value
    assert not evd.observations["supply_boot_complete"].value and evd.normal_control.succeeded is True
    assert evd.observations["supply_boot_sessions"].value == [parts[r]["row"] for r in contract.ROLES]
    assert evd.environment == env.ref() and store.resolve(plan.inputs.policy).ref() == pol.ref()
    assert evd.execution.abort_reason != "EXECUTOR_FAILURE" and evd.execution.cleanup_errors
    assert "EXAMPLE-PRIVATE-STORAGE-ERROR" not in canonical_json(evd.to_dict()).decode()
    if fault != "write":
        assert original.read_bytes() == ORIGINAL


def test_existing_role_diagnostic_and_cleanup_survive_storage_error(tmp_path, monkeypatch):
    env = environment(); cid = "SUP-06"; values = base_values(env, cid)
    parts = collected(env, cid, values)
    diagnostic = "SUPPLY_BOOT_READER_REPORT_UNAVAILABLE"
    cleanup = "SUPPLY_BOOT_PARENT_CLEANUP_UNVERIFIED"
    parts["normal"]["row"]["diagnostic_codes"] = [diagnostic]
    parts["normal"]["cleanup"] = cleanup
    executor = registry.build_executor("supply_boot", "kubernetes", {"supply_boot": {"normal": {}, "challenge": {}}})
    storage_fault(monkeypatch, tmp_path / "evidence", "write")
    draft = executor._draft(PACK.checks[cid], env, values, tmp_path / "evidence", utcnow(), parts)
    assert_preserved(draft, parts, True, "write")
    assert diagnostic in draft.abort_reason and cleanup in draft.cleanup_errors
    assert draft.observations["supply_boot_violations"].value
    assert values["supply_record_source_sha256"] == SOURCE


@pytest.mark.parametrize("cid,fault", [("SUP-07", "mkdir"), ("SUP-12", "write"), ("LIF-01", "collision"),
                                       ("LIF-02", "write"), ("PLT-03", "mkdir"), ("PLT-05", "collision")])
def test_other_boot_profiles_share_the_same_persistence_guard(tmp_path, monkeypatch, cid, fault):
    env = environment()
    extra = {key: VALUES[key] for key in contract.EXTRA_POLICY.get(cid, {})}
    values = base_values(env, cid, extra)
    parts = collected(env, cid, values)
    executor = registry.build_executor("supply_boot", "kubernetes", {"supply_boot": {"normal": {}, "challenge": {}}})
    expected, _, _, normal, _, _ = executor._project(PACK.checks[cid], parts)
    assert expected
    original = storage_fault(monkeypatch, tmp_path / "evidence", fault)
    draft = executor._draft(PACK.checks[cid], env, values, tmp_path / "evidence", utcnow(), parts)
    assert_preserved(draft, parts, normal, fault)
    assert draft.observations["supply_boot_violations"].value == expected
    evd = Evidence(id="EVD-BOOT-OTHER-IO", data_origin=draft.data_origin, plan=dummy_ref("PLAN"), environment=env.ref(),
        check_id=cid, side="single", execution={"status": draft.status}, **_normalize(PACK.checks[cid], draft)).seal()
    assert judge_evidence(PACK.checks[cid], evd, policy(PACK.ref(), values={cid: values}), utcnow()).verdicts.security.value == "FAIL"
    if fault != "write":
        assert original.read_bytes() == ORIGINAL
