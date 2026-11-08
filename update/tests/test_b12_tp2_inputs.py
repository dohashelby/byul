"""Prepare-only TP2 전달물: 실제 명령/권한/정책/현재 GPU 신원을 만들지 않는다."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import b12_tp2_inputs as tool


def test_real_pack_profiles_and_producers_are_preserved_without_external_calls(tmp_path, monkeypatch):
    calls = []

    def blocked(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Preparation must not start an external process")

    monkeypatch.setattr(subprocess, "run", blocked)
    class BlockedPopen(subprocess.Popen):
        def __new__(cls, *args, **kwargs):
            return blocked(*args, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", BlockedPopen)
    output = tmp_path / "new-plan"
    manifest = tool.prepare(output)
    assert not calls
    assert manifest["schema"] == "guard-b-tp2-prepare/1"
    assert manifest["checks"] == ["LIF-08", "LIF-11", "SUP-06"]
    assert manifest["pack_ref"]["id"] == "PACK-BASELINE"
    assert manifest["preparation_only"] and not manifest["execution_authorized"]
    assert not manifest["ready_for_execution"] and manifest["GPU_or_AWS_operations"] == 0
    assert manifest["time_plan"]["B_total_upper_seconds"] is None
    assert not manifest["time_plan"]["joint_budget_fit_verified"]
    plans = {cid: json.loads((output / (cid + "-plan.json")).read_bytes()) for cid in tool.CHECK_IDS}
    assert plans["LIF-08"]["producer"] is None
    assert {v["reason"] for v in plans["LIF-08"]["unresolved"]} >= {"producer_identity_not_returned"}
    assert plans["LIF-11"]["producer"]["name"] == "guard-engine-failed-group-resources"
    assert plans["SUP-06"]["producer"]["name"] == "guard-engine-callback-worker-shard-load"
    assert not plans["LIF-11"]["producer_supported"] and not plans["SUP-06"]["producer_supported"]
    assert all(not p["commands_executed"] and not p["ready_for_execution"] for p in plans.values())
    assert {cid: p["collection_profile"] for cid, p in plans.items()} == {
        "LIF-08": "kubernetes-mp-rank-failure-observation-v1",
        "LIF-11": "kubernetes-selected-resource-recovery-v1",
        "SUP-06": "supply-boot-worker-shard-v1",
    }
    for cid, plan in plans.items():
        config = "${" + cid.replace("-", "_") + "_CONFIG_FILE}"
        for argv in plan["argv_templates"]:
            assert argv[argv.index("--config") + 1] == config
            assert argv[argv.index("--store") + 1] == "${STORE}"
    assert plans["LIF-08"]["scenario_order"] == [
        "normal-before", "exception", "death", "delay", "communication-stall", "normal-after"]
    for ref in manifest["artifacts"]:
        assert hashlib.sha256((output / ref["path"]).read_bytes()).hexdigest() == ref["file_sha256"]
    assert all(ref["path"].startswith("update/") for ref in manifest["source_refs"])


def test_declared_pointers_and_time_estimates_are_not_runtime_or_joint_budget_proof(tmp_path):
    supplied = {"schema": tool.INPUT_SCHEMA, "checks": {
        cid: {"runtime_refs": {"ENV": {"path": "missing-env.json", "file_sha256": "a" * 64}},
              "planned_upper_seconds": seconds}
        for cid, seconds in zip(tool.CHECK_IDS, (600, 900, 600))}}
    before = deepcopy(supplied)
    result = tool.prepare(tmp_path / "declared", supplied)
    assert supplied == before
    assert result["time_plan"]["B_total_upper_seconds"] == 2100
    assert not result["file_pointers_opened_or_verified"] and not result["ready_for_execution"]
    assert not result["time_plan"]["joint_budget_fit_verified"]
    assert any(g["reason"] == "declared_file_pointers_not_runtime_verified" for g in result["unresolved"])
    assert any(g["field"].startswith("policy_values.") for g in result["unresolved"])


@pytest.mark.parametrize("value", [
    {"schema": "guard-b-tp2-inputs/2", "checks": {}},
    {"schema": tool.INPUT_SCHEMA, "checks": {"REQ-14": {}}},
    {"schema": tool.INPUT_SCHEMA, "checks": {}, "execute": True},
    {"schema": tool.INPUT_SCHEMA, "checks": {"LIF-08": {"execution_authorized": True}}},
    {"schema": tool.INPUT_SCHEMA, "checks": {"LIF-08": {"planned_upper_seconds": True}}},
    {"schema": tool.INPUT_SCHEMA, "checks": {"LIF-08": {"planned_upper_seconds": -1}}},
    {"schema": tool.INPUT_SCHEMA, "checks": {"LIF-08": {"runtime_refs": {"ENV": {"path": "x", "sha256": "a" * 64}}}}},
    {"schema": tool.INPUT_SCHEMA, "checks": {"LIF-08": {"policy_values": {"rank_output_rank": True}}}},
    {"schema": tool.INPUT_SCHEMA, "checks": {"LIF-08": {"policy_values": {"rank_target_sha256": "not-a-sha"}}}},
])
def test_invalid_inputs_are_refused_before_any_output(tmp_path, value):
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ValueError):
        tool.prepare(destination, value)
    assert not destination.exists()


def test_existing_user_output_is_preserved(tmp_path):
    directory = tmp_path / "existing"
    directory.mkdir()
    marker = directory / "keep.bin"
    marker.write_bytes(b"preserve original evidence")
    with pytest.raises(ValueError, match="NEW_B_TP2_OUTPUT_DIRECTORY_REQUIRED"):
        tool.prepare(directory)
    assert marker.read_bytes() == b"preserve original evidence"
    assert list(directory.iterdir()) == [marker]


def test_cli_produces_only_plan_and_rejects_execution_switch(tmp_path):
    script = Path(tool.__file__).resolve()
    command = [sys.executable, "-I", "-B", str(script)]
    output = tmp_path / "cli"
    result = subprocess.run([*command, "--output", str(output)], capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(result.stdout)["schema"] == tool.OUTPUT_SCHEMA
    rejected = subprocess.run([*command, "--output", str(tmp_path / "forbidden"), "--execute"],
                              capture_output=True, text=True, encoding="utf-8")
    assert rejected.returncode == 2 and not (tmp_path / "forbidden").exists()
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema":"guard-b-tp2-inputs/1","checks":{},"checks":{}}', encoding="utf-8")
    rejected = subprocess.run([*command, "--output", str(tmp_path / "duplicate-out"), "--inputs", str(duplicate)],
                              capture_output=True, text=True, encoding="utf-8")
    assert rejected.returncode == 2 and not (tmp_path / "duplicate-out").exists()
