"""The paired product can observe a fixed stock boot without dispatching inference."""

from copy import deepcopy
import json
import subprocess

import pytest

from guard import application
from guard.executors import kubernetes_normal_model_code as stock
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, load_pack, sha256_hex, utcnow
from guard.store import Store
from tests.builders import policy
from tests.test_sup08_normal_code_selection import PACK, OwnedWorld, capsule

READY_AT = "2026-10-26T00:00:01Z"


def target(folder, version):
    env, binding, saved = capsule(folder, version)
    binding["startup_ready_window"] = {"container_started_at": binding["kubernetes"]["engine"]["started_at"],
                                       "ready_at": READY_AT}
    saved.write_bytes(canonical_json(binding))
    return env, binding, saved


class World(OwnedWorld):
    def __init__(self, binding, fault=None):
        super().__init__(binding, fault)
        self.inventory_reads = 0

    def runner(self, args, **kwargs):
        if "exec" in args:
            assert "--container=engine" in args and "-I" in args and "-S" in args and "-B" in args
            if args[-2] == stock._SOURCE:
                self.inventory_reads += 1
                result = {"file_sizes": stock._FILE_SIZES.copy(), "config_sha256": stock._CONFIG_SHA256,
                          "read_only": True, "auto_map_present": False, "pid1_start_ticks": 100}
                if self.fault == "wrong-inventory":
                    del result["file_sizes"]["config.json"]
                if self.fault == "replaced-process" and self.inventory_reads == 2:
                    result["pid1_start_ticks"] = 101
                return self.response(result)
            return super().runner(args, **kwargs)
        result = json.loads(super().runner(args, **kwargs)["stdout_bytes"])
        if result.get("metadata", {}).get("name") == "engine-pod":
            container, = result["spec"]["containers"]
            container["ports"] = [{"name": "http", "containerPort": 8000}]
            path = "/health" if self.fault != "unqualified-probe" else "/other"
            container["readinessProbe"] = {"httpGet": {"path": path, "port": "http"}}
            condition, = result["status"]["conditions"]
            condition["lastTransitionTime"] = READY_AT
        return self.response(result)


@pytest.mark.parametrize("fault, expected", [
    (None, ExecStatus.COMPLETED),
    ("changed-source", ExecStatus.ABORTED),
    ("foreign-owner", ExecStatus.ABORTED),
    ("replaced-process", ExecStatus.ABORTED),
    ("unqualified-probe", ExecStatus.ABORTED),
    ("wrong-inventory", ExecStatus.ABORTED),
])
def test_closed_stock_load_evidence(tmp_path, fault, expected):
    env, binding, saved = target(tmp_path, "0.30.0")
    world = World(binding, fault)
    check = load_pack(PACK).checks["SUP-09"]
    executor = stock.KubernetesNormalModelCodeExecutor({"target": str(saved), stock.NORMAL_MODEL_CODE_OPTION: True},
        runner=world.runner, expected_target_sha256=sha256_hex(saved.read_bytes()))
    draft = executor.run(check, env, {}, None, tmp_path / "work")
    assert draft.status == expected and draft.data_origin == DataOrigin.EXAMPLE
    assert not world.execs and not any(step.attempted for step in draft.challenges)
    assert not draft.observations["supply_boot_violations"].collected
    assert not draft.observations["supply_boot_native_consumption"].collected
    assert draft.observations["supply_boot_complete"].value is False
    assert draft.observations["supply_boot_normal_verified"].collected is (fault is None)
    data = json.loads(draft.artifacts[0].path.read_bytes())
    assert data["stock_load_verified"] is (fault is None)
    assert data["uncollected"] == ["custom_code_import_trace", "trust_branch_comparison"]


def test_paired_update_preserves_run_binding_and_unknown_security(tmp_path, monkeypatch):
    pack = load_pack(PACK)
    pol = policy(pack.ref(), scope={"include": ["SUP-09"]}, values={"SUP-09": {}})
    selected = {side: target(tmp_path / side, version) for side, version in
                (("baseline", "0.30.0"), ("candidate", "0.31.0"))}
    worlds = {str(path): World(binding) for _, binding, path in selected.values()}
    cfg = {"adapter": {"name": "vllm"}, "executors": {"supply_boot": "kubernetes"},
           "update": {"mode": "external", "kubernetes_candidate_binding": "prebound", "allowed_checks": ["SUP-09"]},
           "sides": {side: {"check_config": {"SUP-09": {"target": str(path), stock.NORMAL_MODEL_CODE_OPTION: True}}}
                     for side, (_, _, path) in selected.items()}}
    envs = [row[0] for row in selected.values()]
    original = stock.KubernetesNormalModelCodeExecutor.__init__

    def inject(self, settings, **kwargs):
        original(self, settings, runner=worlds[settings["target"]].runner, **kwargs)

    def no_subprocess(*args, **kwargs):
        pytest.fail("unexpected subprocess")

    monkeypatch.setattr(stock.KubernetesNormalModelCodeExecutor, "__init__", inject)
    monkeypatch.setattr(subprocess, "Popen", no_subprocess)
    ready = application.update_readiness(pack, envs, pol, cfg)
    assert ready["ready"], ready
    support = [item for item in ready["check_support"] if item["check_id"] == "SUP-09"]
    assert len(support) == 2
    assert all(item["profile_available"] and item["status"] == "UNKNOWN" and not item["runtime_qualified"]
               for item in support)
    assert all(world.inventory_reads == 0 for world in worlds.values())
    reuse = deepcopy(cfg)
    reuse["sides"]["candidate"]["check_config"]["SUP-09"]["target"] = str(selected["baseline"][2])
    assert not application.update_readiness(pack, envs, pol, reuse)["ready"]
    store = Store(tmp_path / "store")
    report, _ = application.execute_update(store, pack, envs, pol, cfg, tmp_path / "update", utcnow())
    row = next(item for item in report.by_check if item.check_id == "SUP-09")
    for side, (_, _, path) in selected.items():
        result = store.resolve(getattr(row, side).result)
        evd = store.resolve(result.evidence)
        assert evd.execution.status == ExecStatus.COMPLETED
        assert evd.execution.run_binding["files"] == {"model_code_target_sha256": sha256_hex(path.read_bytes())}
        assert evd.normal_control.attempted and evd.normal_control.succeeded
        assert result.verdicts.security == Verdict.UNKNOWN
