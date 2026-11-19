"""Exercise the normal SUP-05 executor through the canonical update pipeline."""

from copy import deepcopy
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from guard import application
from guard.adapters.vllm_approved_file_read_composition import PROFILES
from guard.executors import kubernetes_supply_boot, run_binding
from guard.models import Comparison, DataOrigin, ExecStatus, Verdict, load_pack, sha256_hex, utcnow
from guard.store import Store
from tests.builders import policy
from tests.test_lifecycle_capture import external
from tests.test_sup05_normal_file_reads import normal_case


PACK = Path(__file__).resolve().parents[1] / "packs" / "PACK-BASELINE"


@pytest.mark.parametrize("releases", [
    pytest.param((None, None), id="native17"),
    pytest.param(("0.30.0", "0.31.0"), id="reviewed-release-pair", marks=pytest.mark.skipif(
        sys.platform != "linux" or os.uname().machine != "x86_64" or sys.version_info[:3] != (3, 12, 3)
        or sys.flags.optimize != 0, reason="the selected producer ABI is Linux x86_64 CPython 3.12.3")),
])
def test_public_update_keeps_the_two_bound_normal_read_sessions(tmp_path, external, monkeypatch, releases):
    model_root = external / "approved-model"
    sides = {
        "baseline": normal_case(external / "baseline", monkeypatch, model_root=model_root,
                                env_id="ENV-EXAMPLE-SUP05-BASELINE", release=releases[0]),
        "candidate": normal_case(external / "candidate", monkeypatch, model_root=model_root,
                                 env_id="ENV-EXAMPLE-SUP05-CANDIDATE", release=releases[1], engine_role={
                                     "pod_uid": "00000000-0000-0000-0000-000000000099",
                                     "container_id": "containerd://" + "c" * 64,
                                 }),
    }
    pack = load_pack(PACK)
    pol = policy(pack.ref(), scope={"include": ["SUP-05"]}, values={})
    cfg = {
        "adapter": {"name": "vllm"},
        "executors": {"supply_boot": "kubernetes"},
        "update": {"mode": "external", "allowed_checks": ["SUP-05"]},
        "check_config": {"SUP-05": {"normal_file_reads_only": True}},
        "sides": {side: {"supply_boot": {"normal": data.cfg["normal"]}} for side, data in sides.items()},
    }
    runners = {data.cfg["normal"]["target"]: data.runner for data in sides.values()}
    executor = kubernetes_supply_boot.KubernetesSupplyBootExecutor
    original_init = executor.__init__

    def bind_fixture_runner(self, settings, **kwargs):
        original_init(self, settings, runner=runners[settings["normal"]["target"]], **kwargs)

    def forbid_external_runtime(*args, **kwargs):
        raise AssertionError("The exact paired fixture supplies all transport output")

    monkeypatch.setattr(executor, "__init__", bind_fixture_runner)
    monkeypatch.setattr(subprocess, "Popen", forbid_external_runtime)
    monkeypatch.setattr(socket, "socket", forbid_external_runtime)

    envs = [side.env for side in sides.values()]
    ready = application.update_readiness(pack, envs, pol, cfg)
    assert next(item for item in ready["checks"] if item["check"] == "inputs")["ok"], ready
    if all(releases):
        assert ready["ready"], ready
    invalid = deepcopy(cfg)
    invalid["sides"]["candidate"]["supply_boot"]["normal"]["reference_manifest"] = str(external / "missing-reference.json")
    blocked = application.update_readiness(pack, envs, pol, invalid)
    assert not next(item for item in blocked["checks"] if item["check"] == "inputs")["ok"]
    assert all(not side.world.calls for side in sides.values())

    store = Store(tmp_path / "store")
    report, failed = application.execute_update(store, pack, envs, pol, cfg, tmp_path / "update", utcnow())
    assert not failed
    row = next(item for item in report.by_check if item.check_id == "SUP-05")
    assert row.selected and row.comparison == Comparison.UNKNOWN
    for side, expected in sides.items():
        result = store.resolve(getattr(row, side).result)
        evidence = store.resolve(result.evidence)
        plan = store.resolve(evidence.plan)
        assert plan.inputs.policy == pol.ref() and plan.inputs.pack == pack.ref()
        assert evidence.side.value == side and evidence.environment == expected.env.ref()
        assert evidence.execution.status == ExecStatus.COMPLETED
        assert evidence.data_origin == DataOrigin.EXAMPLE
        assert result.verdicts.security == Verdict.UNKNOWN
        assert run_binding.verify(evidence.execution.run_binding, evidence) == []
        assert evidence.hash_ok() and result.hash_ok()

        observation = evidence.observations["supply_boot_native_consumption"]
        assert observation.collected
        native, = observation.value
        read, = native["rows"]
        assert native["role"] == "normal" and read["matches_approved"] is True
        source = read["source_binding"]
        assert source["recording_owner_binding_verified"] is True
        artifact = next(item for item in evidence.artifacts if item.name == source["recording_artifact"])
        assert artifact.sha256 == source["recording_sha256"] == sha256_hex(expected.raw)
        assert artifact.bytes == len(expected.raw) and (store.root / artifact.path).read_bytes() == expected.raw
        receipt, = evidence.observations["supply_boot_sessions"].value
        manifest = Path(expected.cfg["normal"]["reference_manifest"]).read_bytes()
        assert receipt["approved_reference_manifest_sha256"] == sha256_hex(manifest)
        assert evidence.execution.run_binding["files"]["supply_normal_reference_manifest_sha256"] == sha256_hex(manifest)
        assert receipt["target_sha256"] == expected.values["supply_normal_target_sha256"]
        assert receipt["prepared_sha256"] == expected.values["supply_normal_prepared_sha256"]
        assert receipt["producer_authenticated"] is False
        if all(releases):
            assert receipt["producer_profile"] == PROFILES[expected.env.engine.version]
            assert read["source_binding"]["open_event"] == read["source_binding"]["source_events"][0]
        assert not any(step.attempted for step in evidence.challenges)
