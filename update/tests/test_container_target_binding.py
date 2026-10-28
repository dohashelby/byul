"""EXAMPLE permission sources are tied to one owned engine generation."""
import json
import socket
import subprocess
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.executors import registry
from guard.executors.container_config import ContainerConfigExecutor, PROC_CMD, SENTINEL_CMD, MOUNTS_CMD
from guard.models import canonical_json
from guard.store import Store
from tests.builders import baseline_pack, environment, policy, T0
from tests.test_container_config import FakeDocker, IMAGE, CONTAINER_ID, RUN, LAB_POLICY, _probe
from tests.test_credential_mount_semantics import context as credential_context

CANARY = "EXAMPLE_PRIVATE_PERMISSION_OWNER_SECRET"


class World(FakeDocker):
    def __init__(self, *, wrong=None, change_after=None, change="replacement", privileged=False):
        super().__init__("hardened")
        self.wrong, self.change_after, self.change = wrong, change_after, change
        self.privileged, self.changed, self.data_reads = privileged, False, 0

    def inspect_json(self, container, *, timeout):
        row = super().inspect_json(container, timeout=timeout)
        row["State"]["StartedAt"] = CANARY
        row["Config"]["Env"] = ["PRIVATE=" + CANARY]
        row["HostConfig"]["Privileged"] = self.privileged
        if self.wrong == "unavailable":
            raise OSError(CANARY)
        if self.wrong == "owner":
            row["Config"]["Labels"]["guard.run"] = "EXAMPLE-other-run"
        elif self.wrong == "image":
            row["Config"]["Image"] = "example.invalid/other@sha256:" + "d" * 64
        elif self.wrong == "name":
            row["Name"] = "/EXAMPLE-other"
        elif self.wrong == "stopped":
            row["State"]["Running"] = False
        elif self.wrong == "missing-generation":
            row["State"]["StartedAt"] = ""
        if self.changed:
            if self.change == "restart":
                row["State"]["StartedAt"] += "changed"
            elif self.change == "image-id":
                row["Image"] = "sha256:" + "d" * 64
            else:
                row["Id"] = "d" * 64
        return row

    def exec(self, container, argv, *, timeout):
        self.data_reads += 1
        result = super().exec(container, argv, timeout=timeout)
        phase = "process" if argv[2] == PROC_CMD else "mount_table" if argv[2] == MOUNTS_CMD else "sentinel"
        if phase == self.change_after:
            self.changed = True
        return result


def context():
    ctx = credential_context({})
    ctx.check = baseline_pack().checks["PLT-01"]
    ctx.env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE}, model={"id": "m"})
    ctx.target = {"run_id": RUN, "engine": {"container": "guard-lab-engine"},
                  "environment_ref": ctx.env.ref().model_dump(mode="json")}
    ctx.policy_values = LAB_POLICY
    ctx.cprobe = _probe
    ctx.record = lambda *args: None
    return ctx


@pytest.mark.parametrize("damage", ["missing-ref", "wrong-ref", "missing-run", "wrong-container", "wrong-pin", "broken-seal"])
def test_bad_permission_target_is_rejected_before_any_inspect_or_sentinel(damage):
    ctx, docker = context(), World()
    if damage == "missing-ref": del ctx.target["environment_ref"]
    elif damage == "wrong-ref": ctx.target["environment_ref"]["sha256"] = "0" * 64
    elif damage == "missing-run": del ctx.target["run_id"]
    elif damage == "wrong-container": ctx.target["engine"]["container"] = "EXAMPLE-other"
    elif damage == "wrong-pin":
        ctx.env = ctx.env.model_copy(deep=True); ctx.env.engine.image_digest = "unpinned:latest"; ctx.env = ctx.env.seal()
        ctx.target["environment_ref"] = ctx.env.ref().model_dump(mode="json")
    else: ctx.env = ctx.env.model_copy(update={"content_sha256": "0" * 64})
    with pytest.raises(ValueError, match="^CONTAINER_TARGET_UNVERIFIED$"):
        ContainerConfigExecutor({"container": "guard-lab-engine"}, docker=docker).collect(ctx)
    assert not docker.calls and not docker.files


@pytest.mark.parametrize("wrong", ["owner", "image", "name", "stopped", "missing-generation", "unavailable"])
def test_stable_wrong_instance_does_not_run_permission_readers_or_write_probes(wrong):
    docker = World(wrong=wrong)
    with pytest.raises(ValueError, match="^CONTAINER_TARGET_UNVERIFIED$"):
        ContainerConfigExecutor({}, docker=docker).collect(context())
    assert len(docker.calls) == 1 and not docker.data_reads and not docker.files


def test_all_permission_data_and_write_probes_use_the_verified_immutable_id():
    docker = World()
    observations, normal, _, summary = ContainerConfigExecutor({}, docker=docker).collect(context())
    assert observations["permission_scan_complete"]["value"] is True and normal["succeeded"]
    assert {row[1] for row in docker.calls if row[0] == "exec"} == {CONTAINER_ID}
    assert ("inspect", CONTAINER_ID) in docker.calls
    assert set(summary["source_binding"]["intervals_verified"]) == {
        "inventory", "process", "sentinel", "mount_table", "exec_sentinel", "normal_control"}
    assert all(summary["source_binding"]["intervals_verified"].values())
    assert CANARY not in json.dumps(summary)


@pytest.mark.parametrize("phase", ["process", "sentinel", "mount_table", "normal_control"])
@pytest.mark.parametrize("change", ["replacement", "restart", "image-id"])
def test_later_generation_change_withholds_new_source_and_keeps_bound_inventory(phase, change):
    docker = World(change_after=phase, change=change, privileged=True)
    ctx = context()
    def probe(*args):
        result = _probe(*args)
        if phase == "normal_control": docker.changed = True
        return result
    ctx.cprobe = probe
    observations, normal, _, summary = ContainerConfigExecutor({}, docker=docker).collect(ctx)
    assert observations["privileged"] == {"value": True, "collected": True}
    assert observations["permission_scan_complete"]["value"] is False
    assert normal["succeeded"] is False and normal["attempted"] is (phase == "normal_control")
    assert summary["source_binding"]["intervals_verified"]["inventory"] is True
    assert summary["source_binding"]["intervals_verified"][phase] is False
    if phase == "process":
        assert observations["capabilities_effective"]["collected"] is False
        assert observations["root_uid_present"]["collected"] is False
    assert CANARY not in json.dumps([observations, summary])


def test_saved_definition_without_new_permission_gate_keeps_fields_with_bound_live_sources():
    ctx = context()
    ctx.check = SimpleNamespace(collection_profile=None, required_observations=[],
        functional_probes=ctx.check.functional_probes, challenge_steps=ctx.check.challenge_steps)
    docker = World()
    observations, _, _, summary = ContainerConfigExecutor({}, docker=docker).collect(ctx)
    assert "permission_scan_complete" not in observations and "source_binding" in summary
    assert {row[1] for row in docker.calls if row[0] == "exec"} == {CONTAINER_ID}


def test_legacy_permission_live_also_rejects_a_stable_wrong_owner():
    ctx = context()
    ctx.check = SimpleNamespace(collection_profile=None, required_observations=[],
        functional_probes=ctx.check.functional_probes, challenge_steps=ctx.check.challenge_steps)
    docker = World(wrong="owner")
    with pytest.raises(ValueError, match="^CONTAINER_TARGET_UNVERIFIED$"):
        ContainerConfigExecutor({}, docker=docker).collect(ctx)
    assert not docker.data_reads and not docker.files


@pytest.mark.parametrize("mode,expected", [("normal", "PASS"), ("wrong-owner", "UNKNOWN"),
    ("later-generation", "FAIL"), ("normal-generation", "UNKNOWN"), ("later-attachment", "FAIL")])
def test_permission_binding_product_cli_report_restore_and_exact_refs(tmp_path, monkeypatch, mode, expected):
    pack = baseline_pack()
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE}, model={"id": "m"})
    pol = policy(pack.ref(), scope={"include": ["PLT-01"], "exclude": []}, values={"PLT-01": LAB_POLICY})
    env_path, pol_path, target = tmp_path / "env.json", tmp_path / "policy.json", tmp_path / "target.json"
    env_path.write_bytes(canonical_json(env.to_dict())); pol_path.write_bytes(canonical_json(pol.to_dict()))
    target.write_text(json.dumps({"lab": True, "probe_image": "EXAMPLE-unused", "run_id": RUN,
        "environment_ref": env.ref().model_dump(mode="json"),
        "engine": {"container": "guard-lab-engine", "trusted_url": "http://e:8000", "ready_timeout": 1},
        "origins": {"trusted": {"network": "trusted"}}, "gateway": {"url": "http://g:8080"}}))
    config = tmp_path / "guard.toml"
    config.write_text('[executors]\ncontainer_config="real"\n[adapter]\nname="vllm"\n'
                      '[container_config]\ntarget=' + json.dumps(target.as_posix()) + '\n')
    violated = mode in {"later-generation", "later-attachment"}
    docker = World(wrong="owner" if mode == "wrong-owner" else None,
                   change_after="process" if violated else None, privileged=violated)
    def probe(*args):
        result = _probe(*args)
        if mode == "normal-generation" and docker.data_reads:
            docker.changed = True
        return result
    monkeypatch.setitem(registry.EXECUTORS, "container_config:real", lambda raw:
        ContainerConfigExecutor(raw["container_config"], docker=docker, container_probe=probe))
    def unavailable(*args, **kwargs): raise OSError(CANARY)
    monkeypatch.setattr(subprocess, "Popen", unavailable); monkeypatch.setattr(socket, "socket", unavailable)
    runner = CliRunner(); store_path = tmp_path / "store"
    common = ["--config", str(config), "--store", str(store_path)]
    def cli(args):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, (args, result.stdout, repr(result.exception))
        assert CANARY not in result.stdout
        return result.stdout
    plan = cli(["select", "--pack", str(pack.path), "--env", str(env_path), "--policy", str(pol_path), *common]).strip().splitlines()[-1]
    with monkeypatch.context() as attachment:
        if mode == "later-attachment": attachment.setattr(Store, "put_artifact", unavailable)
        cli(["run", "--plan", plan, "--pack", str(pack.path), *common])
    cli(["judge", "--plan", plan, "--pack", str(pack.path), "--as-of", T0.isoformat(), *common])
    cli(["report", "--plan", plan, "--pack", str(pack.path), "--json", str(tmp_path / "report.json"), "--as-of", T0.isoformat(), *common])
    cli(["verify", *common])
    store = Store(store_path)
    evd, = store.find_latest("EVD", lambda r: True); res, = store.find_latest("RES", lambda r: True)
    rpt, = store.find_latest("RPT", lambda r: True)
    assert evd.data_origin.value == "EXAMPLE" and evd.execution.executor.version == "0.6.2"
    assert res.verdicts.security.value == expected and rpt.official is False
    assert evd.ref() in rpt.inputs_manifest and res.ref() in rpt.inputs_manifest
    assert CANARY not in canonical_json(evd.to_dict()).decode()
    if violated:
        assert evd.observations["privileged"].value is True
        assert evd.observations["capabilities_effective"].collected is False
    if mode in {"wrong-owner", "later-attachment"}: assert evd.execution.status.value == "ABORTED"
    bundle, restored_path = tmp_path / "bundle.zip", tmp_path / "restored"
    cli(["evidence", "export", "--store", str(store_path), "--output", str(bundle)])
    cli(["evidence", "restore", "--bundle", str(bundle), "--destination", str(restored_path)])
    restored = Store(restored_path)
    assert canonical_json(restored.resolve(evd.ref()).to_dict()) == canonical_json(evd.to_dict())
    assert restored.resolve(rpt.ref()).to_dict() == rpt.to_dict()
    cli(["judge", "--plan", plan, "--pack", str(pack.path), "--as-of", T0.isoformat(),
         "--config", str(config), "--store", str(restored_path)])
    cli(["verify", "--config", str(config), "--store", str(restored_path)])
    replayed, = restored.find_latest("RES", lambda r: True)
    assert replayed.ref() == res.ref() and replayed.verdicts.security.value == expected
