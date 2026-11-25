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
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, canonical_json, load_pack, sha256_hex
from guard.store import Store
from tests.builders import baseline_pack, evidence, policy, T0
from tests.test_model_preparation_integration import full_inputs, FullWorld
from tests.test_template_supply_approval import SupplyWorld, SHA


PRIVATE = "EXAMPLE_KUBE_SUPPLY_PRIVATE_792e"
VALUES = {"allowed_template_sha256": [SHA]}


class World(FullWorld):
    def __init__(self, topology, mode=None):
        super().__init__(topology)
        self.mode = mode
        self.response_bytes = {}
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
            self.response_bytes[self.current_label] = rows[0]["body"].encode()
            if self.mode == "tokenize-size" and self.current_label == "tokenize-A":
                rows[0]["body_bytes"] += 1
            if self.mode == "chat-digest" and self.current_label == "before":
                rows[0]["body_sha256"] = "0" * 64
            if self.mode == "chat-eof" and self.current_label == "before":
                rows[0]["eof"] = False
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
            if ((self.mode == "file-change" and self.template_reads > 1)
                    or (self.mode == "middle-file-change" and self.template_reads == 2)):
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


def test_paired_template_fixture_matches_conditions_and_keeps_exact_per_side_mapping(tmp_path):
    from tools.mvp_versions import versions

    seeds = versions()
    drafts = {}
    for side, pin in seeds.items():
        folder = tmp_path / side
        folder.mkdir()
        env, topology, _, cfg = inputs(folder)
        env = env.model_copy(update={
            "id": f"ENV-TEMPLATE-{side.upper()}",
            "engine": env.engine.model_copy(update={"version": pin["version"], "image_digest": pin["image"]}),
        }).seal()
        topology["environment_ref"] = env.ref().model_dump(mode="json")
        topology["observed_version"] = pin["version"]
        topology["engine"]["image_digest"] = pin["image"]
        topology["kubernetes"]["engine"].update(image=pin["image"], image_id=pin["image"])
        topology["model_mapping"].update(environment_ref=topology["environment_ref"],
                                         host_snapshot=f"/srv/EXAMPLE_PRIVATE_{side}/model")
        Path(cfg["target"]).write_bytes(canonical_json(topology))
        world = World(topology)
        drafts[side] = KubernetesTemplateSupplyExecutor(cfg, runner=world.runner).run(
            baseline_pack().checks["SUP-03"], env, VALUES, VllmAdapter(), folder / "work")
        assert drafts[side].observations["template_identity_verified"].value is True
        assert drafts[side].data_origin.value == "EXAMPLE"
        assert summary(drafts[side])["model_mapping_sha256"] == sha256_hex(canonical_json(topology["model_mapping"]))

    baseline, candidate = drafts["baseline"], drafts["candidate"]
    assert baseline.comparability.fixture_sha256 == candidate.comparability.fixture_sha256
    assert baseline.comparability.config_sha256 == candidate.comparability.config_sha256
    assert summary(baseline)["model_mapping_sha256"] != summary(candidate)["model_mapping_sha256"]


@pytest.mark.parametrize("mode,limit,approved,requests,reason", [
    (None, 127, SHA, 2, None),
    (None, 1, SHA, 1, "TEMPLATE_NORMAL_INPUT_BUDGET_EXCEEDED"),
    ("file-change", 127, SHA, 1, "TEMPLATE_NORMAL_IDENTITY_CHANGED_BEFORE_INFERENCE"),
    (None, 127, "f" * 64, 0, "TEMPLATE_NORMAL_APPROVAL_UNVERIFIED"),
])
def test_req07_normal_limit_checked_before_one_fixed_inference(tmp_path, mode, limit, approved, requests, reason):
    env, _, world, cfg = inputs(tmp_path, mode)
    pack = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
    check = pack.checks["REQ-07"]
    values = {"allowed_template_sha256": [approved], "max_rendered_input_tokens": limit,
              "request_template_override_allowed": False}
    executor = KubernetesTemplateSupplyExecutor({**cfg, "req07_normal_only": True}, runner=world.runner)
    draft = executor.run(check, env, values, VllmAdapter(), tmp_path / "work")
    assert len(world.packets) == requests
    assert draft.data_origin.value == "EXAMPLE"
    assert draft.observations["collection_complete"].value is False
    for key in ("max_consumed_prompt_tokens", "tested_rendered_input_limit", "enforcement_observed", "request_override_rejected"):
        assert not draft.observations[key].collected and draft.observations[key].value is None
    detail = summary(draft)
    if reason is None:
        assert draft.observations["rendered_prompt_tokens_exact_match"].value is True
        counts = draft.observations["rendered_input_token_counts"].value
        assert 0 < counts["normal"] <= limit
        assert all("chat_template" not in request["body"] for packet in world.packets for request in packet)
    else:
        assert reason in detail["collection_errors"]
        assert not draft.observations["rendered_prompt_tokens_exact_match"].collected
    assert detail["fixture"]["declared_normal_input_limit"] == limit
    assert not any(challenge.attempted for challenge in draft.challenges)


def sup13_inputs(tmp_path, mode=None, version="0.31.0"):
    env, topology, world, cfg = inputs(tmp_path, mode)
    env = env.model_copy(update={"engine": env.engine.model_copy(update={"version": version})}).seal()
    ref = env.ref().model_dump(mode="json")
    topology.update(lab=False, environment_ref=ref, observed_version=version)
    topology["model_mapping"]["environment_ref"] = ref
    Path(cfg["target"]).write_bytes(canonical_json(topology))
    pack = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
    return pack.checks["SUP-13"], env, topology, world, {**cfg, "sup13_normal_only": True}


@pytest.mark.parametrize("version,mode,requests", [
    ("0.30.0", None, 2), ("0.31.0", None, 2), ("0.31.0", "writable", 0),
    ("0.31.0", "file-change", 1), ("0.31.0", "middle-file-change", 1),
    ("0.31.0", "restart", 2), ("0.31.0", "cleanup", 2), ("0.31.0", "tokens", 2),
    ("0.31.0", "tokenize-size", 1), ("0.31.0", "chat-digest", 2), ("0.31.0", "chat-eof", 2),
])
def test_sup13_observes_only_the_pinned_owned_normal_control(tmp_path, version, mode, requests):
    check, env, target, world, cfg = sup13_inputs(tmp_path, mode, version)
    values = {"forbidden_template_attributes": ["EXAMPLE-policy-attribute", "EXAMPLE-other-attribute"]}
    pin = sha256_hex(canonical_json(target))
    adapter = VllmAdapter()
    executor = KubernetesTemplateSupplyExecutor(cfg, runner=world.runner, expected_target_sha256=pin)
    assert executor.prepared_sup13_normal(check, env, values, adapter, expected_target_sha256=pin) == pin
    assert executor.prepared_sup13_target(env, adapter, expected_target_sha256=pin) == pin
    with pytest.raises(ValueError, match="^KUBERNETES_SUP13_NORMAL_CONFIGURATION_INVALID$"):
        executor.prepared_sup13_normal(check, env, values, adapter, expected_target_sha256="0" * 64)
    assert not world.calls and not world.packets
    draft = executor.run(check, env, values, adapter, tmp_path / "work")
    successful = mode is None
    assert draft.data_origin == DataOrigin.EXAMPLE
    assert draft.status.value == ("COMPLETED" if successful else "ABORTED")
    assert draft.normal_control.succeeded is successful
    assert len(world.packets) == requests
    calls = [request for packet in world.packets for request in packet]
    assert [request["id"] for request in calls] == ["tokenize-A", "before"][:requests]
    assert all(request["body"]["messages"] == [{"role": "user", "content": "guard sandbox control"}]
               and "chat_template" not in request["body"] for request in calls)
    assert not any(challenge.attempted for challenge in draft.challenges)
    obs = draft.observations
    identity_retained = mode in {None, "tokens", "tokenize-size", "chat-digest", "chat-eof"}
    assert obs["template_identity_verified"].collected is identity_retained
    assert obs["template_identity_verified"].value is (True if identity_retained else None)
    assert obs["functional_probe_results"].collected is successful
    assert obs["template_sandbox_scope_complete"].value is False
    for key in ("template_render_witness_verified", "forbidden_template_type_reference_verified"):
        assert not obs[key].collected and obs[key].value is None
    detail = summary(draft)
    assert detail["normal_only_scope"] == {"target_sha256": pin, "environment_sha256": env.ref().sha256,
        "owned_api_input_projection_verified": successful}
    if successful:
        assert world.template_reads == 3
        assert detail["identity_before"] == detail["identity_after"]
        assert calls[-1]["body"]["max_tokens"] == 1
        assert detail["responses"] == [
            {"case": label, "outcome": 200,
             "response_capture": {"body_bytes": len(raw), "body_sha256": sha256_hex(raw)}}
            for label, raw in world.response_bytes.items()
        ]
    if mode == "tokens":
        assert detail["identity_before"] == detail["identity_after"]
        assert "TEMPLATE_SUPPLY_INPUT_PROJECTION_UNVERIFIED" in detail["collection_errors"]
    if mode == "middle-file-change":
        assert detail["identity_before"] == detail["identity_after"]
        assert "TEMPLATE_NORMAL_IDENTITY_CHANGED_BEFORE_INFERENCE" in detail["collection_errors"]
    if mode in {"tokenize-size", "chat-digest", "chat-eof"}:
        assert "TEMPLATE_SUP13_RESPONSE_CAPTURE_UNVERIFIED" in detail["collection_errors"]
        assert detail["responses"][-1] == {"case": calls[-1]["id"], "outcome": 200}
    if mode == "cleanup":
        assert draft.cleanup_errors == ["KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED"]
    stored = "".join(artifact.path.read_text(encoding="utf-8") for artifact in draft.artifacts)
    for private in (PRIVATE, "EXAMPLE-policy-attribute", "EXAMPLE-private-model-content", "prompt_token_ids"):
        assert private not in stored
    saved = evidence(status=draft.status, check_id=check.id, attempted=False, normal=draft.normal_control.model_dump(),
                     observations={key: value.model_dump() for key, value in obs.items()})
    assert RuleTableJudge().judge(check, saved, check.judge.params, values).verdicts["security"].value == "UNKNOWN"


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
def test_sup13_normal_admits_the_exact_budget_for_its_saved_control(tmp_path, version):
    check, env, target, world, cfg = sup13_inputs(tmp_path, version=version)
    prompt_tokens = len(world.backend.tokens(check.functional_probes[0].body))
    exact_budget = 2 * prompt_tokens + 1
    cfg.update(max_total_requests=2, max_total_probe_tokens=exact_budget)
    values = {"forbidden_template_attributes": []}
    pin = sha256_hex(canonical_json(target))
    adapter = VllmAdapter()
    executor = KubernetesTemplateSupplyExecutor(cfg, runner=world.runner, expected_target_sha256=pin)
    assert executor.prepared_sup13_normal(check, env, values, adapter, expected_target_sha256=pin) == pin
    assert not world.calls and not world.packets

    draft = executor.run(check, env, values, adapter, tmp_path / "work")

    assert draft.status.value == "COMPLETED" and draft.data_origin is DataOrigin.EXAMPLE
    assert draft.normal_control.succeeded and not any(step.attempted for step in draft.challenges)
    assert summary(draft)["accepted_tokens"] == exact_budget
    assert [request["id"] for packet in world.packets for request in packet] == ["tokenize-A", "before"]


@pytest.mark.parametrize("damage", ["missing-pin", "drift", "lab", "wrong-option", "unrelated-input",
                                    "single-request", "no-chat-token"])
def test_sup13_rejects_ineligible_saved_declarations_before_dispatch(tmp_path, damage):
    check, env, target, world, cfg = sup13_inputs(tmp_path)
    values = {"forbidden_template_attributes": []}
    old_pin = sha256_hex(canonical_json(target))
    if damage == "drift":
        target["engine"]["command"] += ["EXAMPLE-command-argument"]
    elif damage == "lab":
        target["lab"] = True
    elif damage == "wrong-option":
        cfg["sup13_normal_only"] = 1
    elif damage == "unrelated-input":
        cfg["req07_normal_only"] = True
    elif damage == "single-request":
        cfg["max_total_requests"] = 1
    elif damage == "no-chat-token":
        cfg["max_total_probe_tokens"] = 2
    Path(cfg["target"]).write_bytes(canonical_json(target))
    pin = None if damage == "missing-pin" else old_pin if damage == "drift" else sha256_hex(canonical_json(target))
    executor = KubernetesTemplateSupplyExecutor(cfg, runner=world.runner, expected_target_sha256=pin)
    with pytest.raises(ValueError, match="^KUBERNETES_SUP13_NORMAL_CONFIGURATION_INVALID$"):
        executor.prepared_sup13_normal(check, env, values, VllmAdapter())
    with pytest.raises(ValueError, match="^KUBERNETES_SUP13_NORMAL_CONFIGURATION_INVALID$"):
        executor.prepared_sup13_target(env, VllmAdapter(), expected_target_sha256=pin)
    with pytest.raises(ValueError, match="^KUBERNETES_SUP13_NORMAL_CONFIGURATION_INVALID$"):
        executor.run(check, env, values, VllmAdapter(), tmp_path / "work")
    assert not world.calls and not world.packets


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
