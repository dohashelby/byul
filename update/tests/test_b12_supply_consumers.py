"""A의 커밋된 원 fixture/producer → B boot consumer → 원 판정. CPU/EXAMPLE만."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.collectors import worker_shard_producer
from guard.core.judge import judge_evidence
from guard.core.run import _normalize
from guard.executors import common_consumption as bridge, supply_boot_contract as contract
from guard.executors import kubernetes_supply_boot as boot
from guard.models import Evidence, canonical_json, load_pack, utcnow
from tests.builders import dummy_ref, environment, policy
from tests.test_dynamic_code_cpu import row as legacy_model_row
from tests.test_supply_boot_record_access import base_values, change, native, read_row
from tests.test_worker_shard import POD, SCOPE

ROOT = Path(__file__).parents[1]
PACK = load_pack(ROOT / "packs/PACK-BASELINE")


def fixture(name):
    return json.loads((Path(__file__).parent / "fixtures" / name).read_bytes())


def packet(check_id, rows, env, role, values):
    value = {"schema": contract.SCHEMA, "profile": contract.CHECK_PROFILES[check_id], "role": role,
             "environment_ref": env.ref().model_dump(mode="json"), "target_sha256": values[f"supply_{role}_target_sha256"],
             "source_sha256": values["supply_record_source_sha256"], "change": change(applied=role == "challenge"), "rows": rows}
    return contract.validate_record(value, check_id=check_id, role=role, environment_ref=value["environment_ref"],
        target_sha256=value["target_sha256"], source_sha256=value["source_sha256"])


def draft_and_judge(tmp_path, cid, rows, *, shard_projection=None):
    env = environment(); values = base_values(env, cid)
    collected = {}
    for role in contract.ROLES:
        value = packet(cid, rows, env, role, values)
        part = {"packet": value, "evaluated": contract.evaluate_record(cid, value, values),
                "row": {"role": role, "collected": True, "owner_binding_verified": True,
                        "diagnostic_codes": [], "native_files_covered": True}, "native": native([read_row()])}
        if shard_projection is not None:
            part["worker_shards"] = shard_projection
            part["row"]["worker_shard_input"] = shard_projection
        collected[role] = part
    draft = boot.KubernetesSupplyBootExecutor({"normal": {}, "challenge": {}})._draft(
        PACK.checks[cid], env, values, tmp_path, utcnow(), collected)
    parts = _normalize(PACK.checks[cid], draft)
    evd = Evidence(id="EVD-B12-SUPPLY", data_origin=draft.data_origin, plan=dummy_ref("PLAN"),
        environment=env.ref(), check_id=cid, side="single", execution={"status": draft.status}, **parts).seal()
    result = judge_evidence(PACK.checks[cid], evd, policy(PACK.ref(), values={cid: values}), utcnow())
    return draft, evd, result


@pytest.mark.parametrize("cid", ["SUP-09", "SUP-11"])
def test_actual_model_entry_fixture_reaches_boot_and_retains_oo_unknown(tmp_path, cid):
    rows = fixture("model_entry_k3d_20261108.json")["rows"]
    draft, evd, result = draft_and_judge(tmp_path, cid, rows)
    missing = evd.observations["supply_boot_missing"].value
    assert any("model_entry_cases_incomplete" in code for code in missing)
    assert any("dynamic_code_cases_incomplete" in code for code in missing)
    assert result.verdicts.security.value == "UNKNOWN"
    assert draft.data_origin.value == "EXAMPLE"


def test_new_plugin_rows_cannot_replace_ten_legacy_cases(tmp_path):
    rows = fixture("plugin_runner_k3d_20261108.json")["rows"]
    _, evd, result = draft_and_judge(tmp_path, "SUP-10", rows)
    assert any("plugin_cases_incomplete" in code for code in evd.observations["supply_boot_missing"].value)
    assert result.verdicts.security.value == "UNKNOWN"


@pytest.mark.parametrize("new_contract", ["model", "plugin"])
def test_legacy_fail_survives_new_raw_and_unknown_contract(tmp_path, new_contract):
    if new_contract == "model":
        cid = "SUP-09"
        legacy = legacy_model_row(events=[{"phase": "import"}], outcome="execution_error")
        rows = fixture("model_entry_k3d_20261108.json")["rows"]
    else:
        cid = "SUP-10"
        legacy = {"case_id": "general-allowlisted", "status": "execution_error", "target_started": True,
                  "events": [{"plugin": "byul-unapproved", "phase": "import"}]}
        rows = fixture("plugin_runner_k3d_20261108.json")["rows"]
    _, evd, result = draft_and_judge(tmp_path, cid, [legacy, *rows, {"contract": "unknown/1"}])
    assert evd.observations["supply_boot_violations"].value
    assert any("unsupported_supply_row_contract" in code for code in evd.observations["supply_boot_missing"].value)
    assert result.verdicts.security.value == "FAIL"


def test_new_plugin_violation_survives_legacy_incomplete(tmp_path):
    rows = deepcopy(fixture("plugin_runner_k3d_20261108.json")["rows"])
    selected = next(row for row in rows if row["case_id"] == "io-config-approved")
    selected["events"][0]["plugin"] = "byul-unapproved"  # CPU fixture 변형; 플러그인 코드 실행 없음.
    _, evd, result = draft_and_judge(tmp_path, "SUP-10", rows)
    assert any(row["reason"] == "unapproved_plugin_code_executed" for row in evd.observations["supply_boot_violations"].value)
    assert result.verdicts.security.value == "FAIL"


@pytest.mark.parametrize("cid", ["SUP-09", "SUP-10", "SUP-11"])
def test_empty_or_unknown_only_rows_remain_incomplete(cid):
    for rows in ([], [{"contract": "unknown/1"}]):
        violations, missing = contract.evaluate_record(cid, {"rows": rows}, {})
        assert not violations and missing


def test_worker_real_fixture_projects_five_keys_to_exact_legacy_three(tmp_path):
    raw = fixture("worker_shard_k3d_20261108.json")["cases"]["sharded"]["rows"]
    record, summary = worker_shard_producer.record(raw, pod=POD, **SCOPE)
    projected = bridge.worker_shards(record, SCOPE, {})
    assert summary["supported"] is False and projected["complete"] is False
    assert "GPU_INPUT_PRODUCER_UNSUPPORTED" in projected["missing"]
    assert all(set(row) == {"rank", "required_missing", "ready_accepted"} for row in projected["rows"])
    assert projected["load_facts"][0]["load_completed"] is True
    _, evd, result = draft_and_judge(tmp_path, "SUP-06", projected["rows"], shard_projection=projected)
    assert evd.observations["supply_boot_sessions"].value[0]["worker_shard_input"]["supported"] is False
    assert result.verdicts.security.value == "UNKNOWN"


def test_missing_shard_and_failed_load_facts_survive_without_registration():
    data = fixture("worker_shard_k3d_20261108.json")
    record, _ = worker_shard_producer.record(data["cases"]["missing-shard"]["rows"], pod=POD, **SCOPE)
    projected = bridge.worker_shards(record, SCOPE, {})
    assert projected["load_facts"][0]["load_failed"] is True
    assert projected["load_facts"][0]["ready_accepted"] is False
    assert not projected["complete"] and not projected["violations"]


def test_unregistered_shard_candidate_does_not_erase_legacy_independent_fail(tmp_path):
    raw = deepcopy(fixture("worker_shard_k3d_20261108.json")["cases"]["sharded"]["rows"])
    row = next(r for r in raw if r.get("point") == "DefaultModelLoader._prepare_weights" and r["phase"] == "RETURN")
    row["fields"]["files"] = row["fields"]["files"][:1]
    row["fields"]["file_bytes"] = row["fields"]["file_bytes"][:1]
    record, _ = worker_shard_producer.record(raw, pod=POD, **SCOPE)
    projected = bridge.worker_shards(record, SCOPE, {})
    assert projected["candidates"] and not projected["violations"]
    # The legacy, separately collected boot row remains an independent FAIL.
    independent_boot_row = {"rank": 7, "required_missing": True, "ready_accepted": True}
    _, _, result = draft_and_judge(tmp_path, "SUP-06", [independent_boot_row], shard_projection=projected)
    assert result.verdicts.security.value == "FAIL"


def test_worker_common_binding_mismatch_is_rejected():
    raw = fixture("worker_shard_k3d_20261108.json")["cases"]["sharded"]["rows"]
    record, _ = worker_shard_producer.record(raw, pod=POD, **SCOPE)
    with pytest.raises(ValueError, match="SCOPE_MISMATCH"):
        bridge.worker_shards(record, {**SCOPE, "session": "0" * 32}, {})


@pytest.mark.parametrize("damaged", [False, True])
def test_optional_worker_file_is_consumed_before_transport_failure(tmp_path, monkeypatch, damaged):
    """B transport fixture only; source/registration gates remain real and unsupported."""
    env = environment(); target = {"lab": True}; values = base_values(env, "SUP-06")
    from guard.models import sha256_hex
    scope = {**SCOPE, "environment_ref": env.ref().model_dump(mode="json"),
             "target_sha256": sha256_hex(canonical_json(target)), "prepared_sha256": values["supply_normal_prepared_sha256"]}
    raw = fixture("worker_shard_k3d_20261108.json")["cases"]["sharded"]["rows"]
    record, _ = worker_shard_producer.record(raw, pod=POD, **scope)
    path = tmp_path / "worker-record.json"; path.write_bytes(canonical_json(record))
    settings = {"path": str(path), "sha256": sha256_hex(path.read_bytes()), "scope": scope}
    if damaged:
        settings["sha256"] = "0" * 64
    executor = boot.KubernetesSupplyBootExecutor({"normal": {"worker_shard_input": settings}})
    # Simulate only unavailable pre-existing runtime assets, never an A source
    # gate or producer registration. A valid role/reader is not claimed.
    monkeypatch.setattr(executor, "_load", lambda *args: (target, {}, scope["target_sha256"]))
    part = executor._collect_role(PACK.checks["SUP-06"], env, values, "normal")
    if damaged:
        assert part["row"]["worker_shard_input_error"] == {"type": "ValueError", "message": "B_COMMON_INPUT_BYTES_MISMATCH"}
        assert "B_WORKER_SHARD_INPUT_UNAVAILABLE" in part["row"]["diagnostic_codes"]
    else:
        assert part["worker_shards"]["supported"] is False
        assert part["row"]["worker_shard_input"]["load_facts"][0]["load_completed"] is True
    assert "SUPPLY_BOOT_PREFLIGHT_UNAVAILABLE" in part["row"]["diagnostic_codes"]
