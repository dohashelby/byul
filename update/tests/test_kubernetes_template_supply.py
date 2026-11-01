"""SUP03's same product interface with injected common kubectl responses.

The registry entry is an explicit proposal until A1 integrates it. These CPU
cases do not establish a running cluster, independent consumption or security.
"""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard.adapters.vllm import VllmAdapter
from guard.cli import app
from guard.executors import registry
from guard.executors.kubernetes_template_supply import KubernetesTemplateSupplyExecutor
from guard.executors.reuse import preview_inputs
from guard.models import DataOrigin, canonical_json, sha256_hex
from guard.store import Store
from tests.builders import baseline_pack, policy, T0
from tests.test_model_preparation_integration import full_inputs, FullWorld
from tests.test_template_supply_approval import SupplyWorld, SHA


PRIVATE = "EXAMPLE_KUBE_SUPPLY_PRIVATE_792e"
VALUES = {"allowed_template_sha256": [SHA]}


class World(FullWorld):
    def __init__(self, topology, mode=None):
        super().__init__(topology)
        self.mode = mode
        self.backend = SupplyWorld(context=topology["comparison_config"]["launch"]["max_model_len"],
            **({"different_consumed_ids": True} if mode == "tokens" else
               {"wrong_model": True} if mode == "wrong-model" else {}))

    def runner(self, argv, **kwargs):
        if "exec" in argv and "input_bytes" in kwargs:
            spec = json.loads(kwargs["input_bytes"])
            requests = spec["requests"]
            self.current_label = requests[0]["id"]
            self.packets.append(deepcopy(requests))
            rows = self.backend.probe("EXAMPLE-trusted", requests, spec["timeout"])
            self.after_exec = True
            if self.mode == "http-error" and self.current_label == "before":
                rows[0].update(outcome=500, body='{"error":{"message":"EXAMPLE"}}')
            if self.mode == "wrong-id":
                rows[0]["id"] = "unrelated"
            for row in rows:
                row.update(mode="json", method="POST", capture_complete=True, eof=True,
                    utf8_valid=True, body_truncated=False, body_bytes=len(row["body"].encode()),
                    body_sha256=sha256_hex(row["body"].encode()), detail=PRIVATE, body_preview=PRIVATE)
            result = {"returncode": 0, "stdout_bytes": json.dumps(rows).encode(),
                      "stderr_bytes": PRIVATE.encode(), "capture": {"complete": True}}
            if self.current_label == "before":
                if self.mode == "restart":
                    self.replaced = True
                if self.mode == "cleanup":
                    result["capture"].update(complete=False, pipes_closed=False, read_errors=["child_reap"])
                if self.mode == "http-exception":
                    raise OSError(PRIVATE)
            return result
        result = super().runner(argv, **kwargs)
        value = json.loads(result["stdout_bytes"])
        if "exec" in argv:
            if self.mode == "file-change" and self.template_reads > 1:
                value["sha256"] = "b" * 64
            if self.mode == "bytes-bool":
                value["bytes"] = True
        elif "spec" in value:
            value["metadata"]["annotations"] = {"private": PRIVATE}
            value["spec"]["containers"][0]["env"] = [{"name": "SECRET", "value": PRIVATE}]
            if self.mode == "writable":
                for mount in value["spec"]["containers"][0].get("volumeMounts", []):
                    mount["readOnly"] = False
        result["stdout_bytes"] = json.dumps(value).encode()
        return result


def inputs(tmp_path, mode=None):
    env, topology = full_inputs()
    launch = topology["comparison_config"]["launch"]
    launch["argv"].extend(["--served-model-name", "fixture-model"])
    path = tmp_path / "target.json"
    path.write_bytes(canonical_json(topology))
    return env, topology, World(topology, mode), {"target": str(path), "request_timeout": 1}


def run(tmp_path, mode=None, values=None):
    env, topology, world, cfg = inputs(tmp_path, mode)
    draft = KubernetesTemplateSupplyExecutor(cfg, runner=world.runner).run(
        baseline_pack().checks["SUP-03"], env, VALUES if values is None else values,
        VllmAdapter(), tmp_path / "work")
    return draft, world, env, topology


def summary(draft):
    path = next(a.path for a in draft.artifacts if a.name == "probe_log.jsonl")
    return json.loads(path.read_text(encoding="utf-8").splitlines()[-1])


@pytest.mark.parametrize("approved,member", [([SHA], True), ([], False), (["f" * 64], False)])
def test_current_template_and_api_projection_preserve_unknown_supply_scope(tmp_path, approved, member):
    draft, world, env, topology = run(tmp_path, values={"allowed_template_sha256": approved})
    obs, detail = draft.observations, summary(draft)
    assert draft.data_origin.value == "EXAMPLE" and draft.status.value == "COMPLETED"
    assert obs["template_identity_verified"].value is True
    assert obs["selected_template_sha256"].value == SHA
    assert obs["selected_template_approved"].value is member
    assert obs["input_projection_exact_match"].value is True
    assert obs["template_consumer_verified"].value is obs["supply_scope_complete"].value is False
    assert draft.normal_control.succeeded is True and not any(c.attempted for c in draft.challenges)
    assert world.template_reads == 2
    requests = [r for packet in world.packets for r in packet]
    assert [r["id"] for r in requests] == ["tokenize-A", "before"]
    assert all(r["body"]["model"] == "fixture-model" != env.model.id for r in requests)
    assert detail["model_mapping_sha256"] == sha256_hex(canonical_json(topology["model_mapping"]))
    assert detail["identity_before"] == detail["identity_after"]
    assert detail["identity_before"]["pod_uid"] == topology["kubernetes"]["engine"]["pod_uid"]
    stored = "".join(a.path.read_text(encoding="utf-8") for a in draft.artifacts)
    assert PRIVATE not in stored and "EXAMPLE_PRIVATE_HOST_SNAPSHOT" not in stored
    assert "EXAMPLE-private-model-content" not in stored and "prompt_token_ids" not in stored


@pytest.mark.parametrize("mode", ["tokens", "wrong-model", "wrong-id", "http-error", "http-exception", "cleanup"])
def test_later_response_gap_retains_independent_membership_without_inventing_fail(tmp_path, mode):
    draft, _, _, _ = run(tmp_path, mode, {"allowed_template_sha256": []})
    assert draft.observations["input_projection_exact_match"].value is False
    assert draft.observations["template_identity_verified"].value is True
    assert draft.observations["selected_template_approved"].value is False
    assert draft.observations["selected_template_approved"].collected
    assert draft.observations["template_consumer_verified"].value is False
    assert draft.normal_control.succeeded is False
    if mode == "cleanup":
        assert draft.cleanup_errors == ["KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED"]


@pytest.mark.parametrize("mode", ["restart", "file-change", "writable", "bytes-bool"])
def test_file_or_owner_gap_withholds_identity_and_projection(tmp_path, mode):
    draft, world, _, _ = run(tmp_path, mode)
    assert draft.observations["template_identity_verified"].value is False
    assert not draft.observations["selected_template_approved"].collected
    assert draft.observations["input_projection_exact_match"].value is False
    if mode in {"writable", "bytes-bool"}:
        assert not world.packets


@pytest.mark.parametrize("damage", ["profile", "policy", "seal", "target-ref", "mapping", "pin", "native", "mutating"])
def test_invalid_inputs_rejected_before_transport(tmp_path, damage):
    env, topology, _, cfg = inputs(tmp_path)
    check, values, pin = baseline_pack().checks["SUP-03"], deepcopy(VALUES), None
    if damage == "profile":
        check = baseline_pack().checks["REQ-07"]
    elif damage == "policy":
        values = {"allowed_template_sha256": None}
    elif damage == "seal":
        env = env.model_copy(update={"content_sha256": "0" * 64})
    elif damage == "target-ref":
        topology["environment_ref"]["sha256"] = "0" * 64
    elif damage == "mapping":
        topology["model_mapping"]["served_model_name"] = ""
    elif damage == "pin":
        pin = "0" * 64
    elif damage == "native":
        cfg["native_observation_path"] = "/guard-lifecycle/EXAMPLE.json"
    else:
        cfg["allow_mutating_routes"] = True
    Path(cfg["target"]).write_bytes(canonical_json(topology))
    def forbidden(*a, **k):
        pytest.fail("must reject before kubectl/HTTP")
    with pytest.raises((ValueError, RuntimeError)):
        KubernetesTemplateSupplyExecutor(cfg, runner=forbidden, expected_target_sha256=pin).run(
            check, env, values, VllmAdapter(), tmp_path / "work")


def test_injected_transport_is_example_even_when_env_and_target_declare_actual(tmp_path):
    env, topology, world, cfg = inputs(tmp_path)
    env = env.model_copy(update={"data_origin": DataOrigin.MEASURED}).seal()
    topology.update(lab=False, environment_ref=env.ref().model_dump(mode="json"))
    topology["model_mapping"]["environment_ref"] = env.ref().model_dump(mode="json")
    Path(cfg["target"]).write_bytes(canonical_json(topology))
    draft = KubernetesTemplateSupplyExecutor(cfg, runner=world.runner).run(
        baseline_pack().checks["SUP-03"], env, VALUES, VllmAdapter(), tmp_path / "work")
    assert draft.data_origin.value == "EXAMPLE"


def test_static_reuse_never_proves_current_file_or_pod(tmp_path, monkeypatch):
    env, _, world, cfg = inputs(tmp_path)
    monkeypatch.setitem(registry.EXECUTORS, "template_validation:kubernetes",
        lambda raw: KubernetesTemplateSupplyExecutor(raw["template_validation"], runner=world.runner))
    assert preview_inputs(baseline_pack().checks["SUP-03"], env, VALUES,
        {"executors": {"template_validation": "kubernetes"}, "template_validation": cfg,
         "adapter": {"name": "vllm"}}) is None
    assert not world.calls and not world.packets


@pytest.mark.parametrize("conflict", [False, True])
def test_optional_launch_model_declaration_must_match_the_validated_repository(tmp_path, conflict):
    env, topology, world, cfg = inputs(tmp_path)
    assert "model" not in topology["comparison_config"]["launch"]
    topology["comparison_config"]["launch"]["model"] = "other/repository" if conflict else env.model.id
    Path(cfg["target"]).write_bytes(canonical_json(topology))
    draft = KubernetesTemplateSupplyExecutor(cfg, runner=world.runner).run(
        baseline_pack().checks["SUP-03"], env, VALUES, VllmAdapter(), tmp_path / "work")
    assert draft.observations["template_identity_verified"].value is (not conflict)
    if conflict:
        assert not world.packets and world.template_reads == 0


@pytest.mark.parametrize("mode", [None, "http-error", "restart", "attachment", "missing-policy"])
def test_proposed_registry_cli_preserves_pack31_refs_and_observation_only_verdict(tmp_path, monkeypatch, mode):
    env, topology, world, cfg = inputs(tmp_path, mode)
    pack = baseline_pack()
    pol = policy(pack.ref(), scope={"include": ["SUP-03"]},
        values={} if mode == "missing-policy" else {"SUP-03": {"allowed_template_sha256": []}})
    for name, value in (("environment", env.to_dict()), ("policy", pol.to_dict())):
        (tmp_path / (name + ".json")).write_bytes(canonical_json(value))
    config = tmp_path / "guard.toml"
    config.write_text('[executors]\ntemplate_validation="kubernetes"\n[adapter]\nname="vllm"\n'
        '[template_validation]\ntarget=' + json.dumps(cfg["target"]) + '\nrequest_timeout=1\n', encoding="utf-8")
    # This is the precise A1 registration proposal, not a shipped registration.
    monkeypatch.setitem(registry.EXECUTORS, "template_validation:kubernetes",
        lambda raw: KubernetesTemplateSupplyExecutor(raw["template_validation"], runner=world.runner,
            expected_target_sha256=raw.get("_kubernetes_target_sha256")))
    def forbidden(*a, **k):
        raise AssertionError("no Docker/process/socket outside injected common boundary")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    runner = CliRunner()
    store_path, restored_path = tmp_path / "store", tmp_path / "restored"
    common = ["--store", str(store_path), "--config", str(config)]
    def cli(argv):
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, (argv, result.output, repr(result.exception))
        return result.output
    plan = cli(["select", "--pack", str(pack.path), "--env", str(tmp_path / "environment.json"),
                "--policy", str(tmp_path / "policy.json"), *common]).strip().splitlines()[-1]
    with monkeypatch.context() as capture:
        if mode == "attachment":
            original, calls = Store.put_artifact, []
            def partial(self, data):
                calls.append(1)
                if len(calls) == 2:
                    raise OSError(PRIVATE)
                return original(self, data)
            capture.setattr(Store, "put_artifact", partial)
        cli(["run", "--plan", plan, "--pack", str(pack.path), *common])
    cli(["judge", "--plan", plan, "--as-of", T0.isoformat(), *common])
    cli(["report", "--plan", plan, "--as-of", T0.isoformat(), "--json", str(tmp_path / "report.json"), *common])
    cli(["verify", *common])
    original = Store.read_only(store_path)
    evd, = original.iter_kind("EVD")
    res, = original.iter_kind("RES")
    assert evd.data_origin.value == "EXAMPLE"
    assert all(axis.value == "UNKNOWN" for axis in res.verdicts.model_dump().values())
    if mode == "attachment":
        assert evd.execution.status.value == "ABORTED" and len(evd.artifacts) == 1
        assert evd.observations["selected_template_approved"].value is False
    elif mode == "missing-policy":
        assert evd.execution.status.value == "NOT_RUN" and evd.execution.executor is None
        assert not world.calls and not world.packets
    else:
        assert evd.execution.status.value == "COMPLETED"
        assert evd.execution.executor.name == "template_validation"
    objects = [obj for kind in ("ENV", "POL", "PLAN", "EVD", "RES", "RPT") for obj in original.iter_kind(kind)]
    bundle = tmp_path / "bundle.zip"
    cli(["evidence", "export", "--store", str(store_path), "--output", str(bundle)])
    cli(["evidence", "restore", "--bundle", str(bundle), "--destination", str(restored_path)])
    restored = Store.read_only(restored_path)
    assert all(restored.resolve(obj.ref()).to_dict() == obj.to_dict() for obj in objects)
    cli(["judge", "--plan", plan, "--as-of", T0.isoformat(), "--store", str(restored_path), "--config", str(config)])
    assert [r.ref() for r in restored.iter_kind("RES")] == [res.ref()]
    cli(["verify", "--store", str(restored_path), "--config", str(config)])
