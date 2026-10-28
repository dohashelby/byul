"""EXAMPLE wrong targets and generation changes through the real collector/CLI."""
import json
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.executors import registry
from guard.executors.credential_exposure import CredentialExposureExecutor
from guard.models import canonical_json
from guard.store import Store
from tests.builders import baseline_pack, completion_response, environment, policy, T0
from tests.test_credential_mount_semantics import FakeDocker, context, IMAGE, RUN

PATH = "/EXAMPLE-credentials/key"
CANARY = "EXAMPLE_PRIVATE_TARGET_INSPECTION_SECRET"


class BindingDocker(FakeDocker):
    def __init__(self, *, wrong=None, change_after=None, change="replacement", **kwargs):
        super().__init__(**kwargs)
        self.wrong, self.change_after, self.change = wrong, change_after, change
        self.changed = False
        self.environment_reads = 0

    def inspect_json(self, container, *, timeout):
        row = super().inspect_json(container, timeout=timeout)
        row["Config"]["Env"] = ["EXAMPLE_PRIVATE=" + CANARY]
        row["State"]["StartedAt"] = CANARY
        if self.wrong == "unavailable":
            raise OSError(CANARY)
        if self.wrong == "owner":
            row["Config"]["Labels"]["guard.run"] = "EXAMPLE-other-owner"
        elif self.wrong == "image":
            row["Config"]["Image"] = "example.invalid/other@sha256:" + "d" * 64
        elif self.wrong == "container":
            row["Name"] = "/EXAMPLE-other"
        elif self.wrong == "stopped":
            row["State"]["Running"] = False
        elif self.wrong == "missing-generation":
            row["State"]["StartedAt"] = ""
        if self.change_after == "mount" and self.inspections == 2:
            self.changed = True
        if self.changed:
            if self.change == "restart":
                row["State"]["StartedAt"] = CANARY + "changed"
            elif self.change == "image-id":
                row["Image"] = "sha256:" + "d" * 64
            else:
                row["Id"] = "d" * 64
        return row

    def exec(self, *args, **kwargs):
        self.environment_reads += 1
        value = super().exec(*args, **kwargs)
        if self.change_after == "environment":
            self.changed = True
        return value

    def exec_bounded(self, *args, **kwargs):
        value = super().exec_bounded(*args, **kwargs)
        if self.change_after == "candidate":
            self.changed = True
        return value


def values():
    return {"forbidden_mount_paths": ["/credentials"], "credential_candidate_paths": [PATH]}


@pytest.mark.parametrize("damage", ["missing-ref", "wrong-ref", "missing-run", "wrong-container", "wrong-pin", "broken-seal"])
def test_invalid_declaration_is_unknown_before_reading_any_runtime_source(damage):
    ctx = context(values())
    if damage == "missing-ref":
        del ctx.target["environment_ref"]
    elif damage == "wrong-ref":
        ctx.target["environment_ref"]["sha256"] = "0" * 64
    elif damage == "missing-run":
        del ctx.target["run_id"]
    elif damage == "wrong-container":
        ctx.target["engine"]["container"] = "EXAMPLE-other"
    elif damage == "wrong-pin":
        ctx.env = ctx.env.model_copy(deep=True)
        ctx.env.engine.image_digest = "unpinned:latest"
        ctx.env = ctx.env.seal()
        ctx.target["environment_ref"] = ctx.env.ref().model_dump(mode="json")
    else:
        ctx.env = ctx.env.model_copy(update={"content_sha256": "0" * 64})
    docker = BindingDocker()
    with pytest.raises(ValueError, match="^CREDENTIAL_TARGET_UNVERIFIED$"):
        CredentialExposureExecutor({"container": "engine"}, docker=docker).collect(ctx)
    assert docker.inspections == docker.environment_reads == 0 and not docker.requests


@pytest.mark.parametrize("wrong", ["owner", "image", "container", "stopped", "missing-generation", "unavailable"])
def test_stable_wrong_or_missing_instance_never_becomes_credential_absence(wrong):
    ctx = context(values())
    calls = []
    ctx.cprobe = lambda *args: calls.append(True)
    docker = BindingDocker(wrong=wrong)
    with pytest.raises(ValueError, match="^CREDENTIAL_TARGET_UNVERIFIED$"):
        CredentialExposureExecutor({}, docker=docker).collect(ctx)
    assert docker.inspections == 1 and docker.environment_reads == 0 and not docker.requests and not calls


@pytest.mark.parametrize("phase", ["mount", "environment", "candidate", "normal"])
@pytest.mark.parametrize("change", ["replacement", "restart", "image-id"])
def test_generation_changes_withhold_only_unbound_intervals_and_retain_prior_facts(phase, change):
    docker = BindingDocker(change_after=phase, change=change,
        mounts=[{"Type": "bind", "Destination": "/credentials"}], process_names=["FORBIDDEN_CANARY"])
    ctx = context(values())
    original = ctx.cprobe
    def normal(*args):
        value = original(*args)
        if phase == "normal":
            docker.changed = True
        return value
    ctx.cprobe = normal
    observations, normal_control, _, detail = CredentialExposureExecutor({}, docker=docker).collect(ctx)
    intervals = detail["source_binding"]["intervals_verified"]
    assert intervals["mounts"] is (phase != "mount")
    assert intervals["environment"] is (phase in {"candidate", "normal"})
    assert intervals["candidates"] is (phase == "normal")
    assert intervals["normal_control"] is False and normal_control["succeeded"] is False
    assert normal_control["attempted"] is (phase == "normal")
    assert observations["forbidden_mount_violations"] == (
        {"value": None, "collected": False} if phase == "mount" else {"value": ["/credentials"], "collected": True})
    if phase in {"mount", "environment"}:
        assert observations["worker_env_names"]["collected"] is False
    else:
        assert "FORBIDDEN_CANARY" in observations["worker_env_names"]["value"]
    assert observations["credential_candidate_results"]["value"] == {PATH: "absent" if phase == "normal" else "unknown"}
    assert detail["source_binding"]["environment_ref"] == ctx.env.ref().model_dump(mode="json")
    assert CANARY not in json.dumps([observations, detail])


def test_verified_normal_intervals_keep_existing_limited_observations():
    docker = BindingDocker()
    observations, normal, _, detail = CredentialExposureExecutor({}, docker=docker).collect(context(values()))
    assert normal["succeeded"] and observations["credential_candidate_results"]["value"] == {PATH: "absent"}
    assert observations["credential_candidates_complete"]["value"] is True
    assert all(detail["source_binding"]["intervals_verified"].values())
    assert docker.exec_targets == ["a" * 64, "a" * 64]
    assert CANARY not in json.dumps(detail)


def test_temporary_name_mapping_between_owner_samples_cannot_select_another_generation():
    class NameRemapped(FakeDocker):
        def exec_bounded(self, container, *args, **kwargs):
            # The name transiently selects B while owner samples see A. A's
            # immutable ID still selects A and retains its readable result.
            self.state = "absent" if container == "engine" else "candidate_readable"
            return super().exec_bounded(container, *args, **kwargs)
    docker = NameRemapped()
    observations, _, _, _ = CredentialExposureExecutor({}, docker=docker).collect(context(values()))
    assert observations["credential_candidate_results"]["value"] == {PATH: "candidate_readable"}
    assert observations["credential_candidates_complete"]["value"] is False
    assert docker.exec_targets == ["a" * 64, "a" * 64]


@pytest.mark.parametrize("mode,expected", [("normal", "PASS"), ("wrong-owner", "UNKNOWN"),
                                          ("later-generation", "FAIL"), ("later-attachment", "FAIL")])
def test_binding_product_cli_report_bundle_and_exact_ref_replay(tmp_path, monkeypatch, mode, expected):
    pack = baseline_pack()
    env = environment(engine={"name": "engine-x", "version": "1.0.0", "image_digest": IMAGE}, model={"id": "test"})
    pol = policy(pack.ref(), scope={"include": ["PLT-04"], "exclude": []},
                 values={"PLT-04": {"forbidden_env_names": [], **values()}})
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"lab": True, "probe_image": "EXAMPLE-unused", "run_id": RUN,
        "environment_ref": env.ref().model_dump(mode="json"),
        "engine": {"container": "engine", "trusted_url": "http://engine:8000", "ready_timeout": 1},
        "origins": {"trusted": {"network": "trusted"}}, "gateway": {"url": "http://gateway:8080"}}))
    config = tmp_path / "guard.toml"
    config.write_text('[executors]\ncredential_exposure="real"\n[adapter]\nname="vllm"\n'
                      '[credential_exposure]\ntarget=' + json.dumps(target.as_posix()) + '\n')
    env_path, policy_path = tmp_path / "env.json", tmp_path / "policy.json"
    env_path.write_bytes(canonical_json(env.to_dict())); policy_path.write_bytes(canonical_json(pol.to_dict()))
    violated = mode in {"later-generation", "later-attachment"}
    docker = BindingDocker(wrong="owner" if mode == "wrong-owner" else None,
        change_after="environment" if violated else None,
        mounts=[{"Type": "bind", "Destination": "/credentials"}, None] if violated else [])
    monkeypatch.setitem(registry.EXECUTORS, "credential_exposure:real",
        lambda raw: CredentialExposureExecutor(raw["credential_exposure"], docker=docker,
            container_probe=lambda network, requests, timeout: [completion_response(r) for r in requests]))
    def unavailable(*args, **kwargs):
        raise OSError(CANARY)
    monkeypatch.setattr(subprocess, "Popen", unavailable)
    monkeypatch.setattr(socket, "socket", unavailable)
    runner = CliRunner()
    store_path = tmp_path / "store"
    common = ["--config", str(config), "--store", str(store_path)]
    def cli(args):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, (args, result.stdout, repr(result.exception))
        assert CANARY not in result.stdout
        return result.stdout
    plan = cli(["select", "--pack", str(pack.path), "--env", str(env_path), "--policy", str(policy_path), *common]).strip().splitlines()[-1]
    with monkeypatch.context() as attachment:
        if mode == "later-attachment":
            attachment.setattr(Store, "put_artifact", unavailable)
        cli(["run", "--plan", plan, "--pack", str(pack.path), *common])
    cli(["judge", "--plan", plan, "--pack", str(pack.path), "--as-of", T0.isoformat(), *common])
    cli(["report", "--plan", plan, "--pack", str(pack.path), "--json", str(tmp_path / "report.json"),
         "--as-of", T0.isoformat(), *common])
    cli(["verify", *common])
    store = Store(store_path)
    evd, = store.find_latest("EVD", lambda r: True)
    res, = store.find_latest("RES", lambda r: True)
    rpt, = store.find_latest("RPT", lambda r: True)
    assert res.verdicts.security.value == expected and evd.data_origin.value == "EXAMPLE"
    assert evd.execution.executor.version == "0.5.1" and rpt.official is False
    assert evd.ref() in rpt.inputs_manifest and res.ref() in rpt.inputs_manifest
    assert CANARY not in canonical_json(evd.to_dict()).decode()
    if violated:
        assert evd.observations["forbidden_mount_violations"].value == ["/credentials"]
        assert evd.observations["worker_env_names"].collected is False
    if mode in {"wrong-owner", "later-attachment"}:
        assert evd.execution.status.value == "ABORTED"
    bundle, restored_path = tmp_path / "bundle.zip", tmp_path / "restored"
    cli(["evidence", "export", "--store", str(store_path), "--output", str(bundle)])
    cli(["evidence", "restore", "--bundle", str(bundle), "--destination", str(restored_path)])
    restored = Store(restored_path)
    assert canonical_json(restored.resolve(evd.ref()).to_dict()) == canonical_json(evd.to_dict())
    assert restored.resolve(rpt.ref()).to_dict() == rpt.to_dict()
    restored_common = ["--config", str(config), "--store", str(restored_path)]
    cli(["judge", "--plan", plan, "--pack", str(pack.path), "--as-of", T0.isoformat(), *restored_common])
    cli(["verify", *restored_common])
    replayed, = restored.find_latest("RES", lambda r: True)
    assert replayed.ref() == res.ref() and replayed.verdicts.security.value == expected
