"""Exercise the normal SUP-05 executor through the canonical update pipeline."""

from pathlib import Path
import socket
import subprocess

from guard import application
from guard.executors import kubernetes_supply_boot, run_binding
from guard.models import Comparison, DataOrigin, ExecStatus, Verdict, load_pack, sha256_hex, utcnow
from guard.store import Store
from tests.builders import policy
from tests.test_sup05_normal_file_reads import normal_case


PACK = Path(__file__).resolve().parents[1] / "packs" / "PACK-BASELINE"


def test_public_update_keeps_the_two_bound_normal_read_sessions(tmp_path, monkeypatch):
    # The immutable /17 producer supports this synthetic release on both sides.
    # Different target, ENV and prepared-session identities must survive routing.
    model_root = tmp_path / "approved-model"
    sides = {
        "baseline": normal_case(tmp_path / "baseline", monkeypatch, model_root=model_root,
                                env_id="ENV-EXAMPLE-SUP05-BASELINE"),
        "candidate": normal_case(tmp_path / "candidate", monkeypatch, model_root=model_root,
                                 env_id="ENV-EXAMPLE-SUP05-CANDIDATE", engine_role={
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

    store = Store(tmp_path / "store")
    report, failed = application.execute_update(
        store, pack, [side.env for side in sides.values()], pol, cfg, tmp_path / "update", utcnow()
    )
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
        assert read["source_binding"]["recording_owner_binding_verified"] is True
        receipt, = evidence.observations["supply_boot_sessions"].value
        manifest = Path(expected.cfg["normal"]["reference_manifest"]).read_bytes()
        assert receipt["approved_reference_manifest_sha256"] == sha256_hex(manifest)
        assert receipt["target_sha256"] == expected.values["supply_normal_target_sha256"]
        assert receipt["prepared_sha256"] == expected.values["supply_normal_prepared_sha256"]
        assert receipt["producer_authenticated"] is False
        assert not any(step.attempted for step in evidence.challenges)
