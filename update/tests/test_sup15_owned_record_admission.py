"""SUP-15 owned-record admission through the real read-only executor."""

from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

from guard import application
from guard.collectors.lifecycle import prepare_assets
from guard.core.run import _normalize
from guard.executors import registry
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, sha256_hex
from tests.builders import environment, evidence, policy
from tests.test_b3_supply_records import retained
from tests.test_kubernetes_lifecycle import World
from tests.test_kubernetes_sampling import inputs
from tests.test_lifecycle_capture import Docker, IMAGE, external
from tests.test_supply_boot_record_access import PACK, ROOT, base_values, change, good_packet
from tools import mvp_live_readiness
from tools.j02_run_config import write_config


def _prepare_roles(tmp_path, env, late_owner_change, *, first_owner=1):
    settings, pins, worlds, records = {}, {}, {}, {}
    for index, role in enumerate(("normal", "challenge"), start=first_owner):
        directory = tmp_path / role
        directory.mkdir()
        prepared = prepare_assets(directory / "assets", ROOT.parent)
        engine = inputs()["kubernetes"]["engine"]
        engine.update(namespace=f"guard-{role}", pod=f"engine-{role}", image=env.engine.image_digest,
                      namespace_uid=f"10000000-0000-0000-0000-{index:012d}",
                      pod_uid=f"20000000-0000-0000-0000-{index:012d}",
                      container_id="containerd://" + str(index) * 64,
                      pod_ip=f"127.0.0.{index}", image_id="sha256:" + "c" * 64)
        target = {"schema": "guard-kubernetes-lifecycle-target/1", "lab": True, "run_id": f"guard-{role}",
                  "environment_ref": env.ref().model_dump(mode="json"),
                  "kubernetes": {"context": "fixture-context", "engine": engine}}
        target_path = directory / "target.json"
        target_path.write_bytes(canonical_json(target))
        target_sha256 = sha256_hex(target_path.read_bytes())
        settings[role] = {"target": str(target_path), "prepared": str(directory / "assets" / "manifest.json")}
        pins[f"supply_{role}_target_sha256"] = target_sha256
        pins[f"supply_{role}_prepared_sha256"] = sha256_hex(canonical_json(prepared))

        challenged = role == "challenge"
        packet = good_packet(env.ref().model_dump(mode="json"), target_sha256, "SUP-15")
        packet.update(role=role, change=change(applied=challenged), rows=[{
            "path_sha256": sha256_hex(b"EXAMPLE cache path"), "relevant_change": challenged,
            "reused": True, "compatibility_verified": not challenged,
        }])
        raw = canonical_json(packet)
        world = World(SimpleNamespace(prepared=prepared), target,
                      mode="late-file-owner" if challenged and late_owner_change else None)
        world.docker = Docker(SimpleNamespace(raw=raw))
        worlds[engine["namespace"]] = world
        records[role] = raw
    return settings, pins, worlds, records


@pytest.mark.parametrize(("candidate_input", "prepare_gap", "audit_gap"), [
    ("distinct", None, None),
    ("alias", "SUPPLY_BOOT_DISTINCT_ROLE_INPUTS_REQUIRED", "SUPPLY_BOOT_DISTINCT_ROLE_INPUTS_REQUIRED"),
    ("foreign-target", "SUPPLY_BOOT_INPUT_UNAVAILABLE", "SUPPLY_BOOT_INPUT_UNAVAILABLE"),
    ("missing-preparation", "RUN_BINDING_UNAVAILABLE", "MA7_INPUT_INVALID"),
    ("changed-snapshot", None, "MVP_INPUT_FILE_CHANGED"),
])
def test_sup15_offline_readiness_binds_both_declared_roles(
        external, monkeypatch, candidate_input, prepare_gap, audit_gap):
    def no_runtime(*args, **kwargs):
        pytest.fail("SUP-15 file readiness must not contact or run a target")

    monkeypatch.setattr(subprocess, "Popen", no_runtime)
    monkeypatch.setattr(socket, "socket", no_runtime)
    producer = external / "producer.py"
    producer.write_bytes(b"# EXAMPLE boot-record source\n")
    check_id = "SUP-15"
    pol = policy(PACK.ref(), scope={"include": [check_id]}, values={})
    policy_path = external / "policy.json"
    policy_path.write_bytes(canonical_json(pol.to_dict()))
    cfg = {"adapter": {"name": "vllm"}, "executors": {"supply_boot": "kubernetes"},
           "update": {"mode": "external", "allowed_checks": [check_id]},
           "run_binding": {check_id: {"record_producer": str(producer)}}, "sides": {}}
    envs, entries = [], []
    config_path = external / "guard.toml"
    for side, release in mvp_live_readiness.versions().items():
        root = external / side
        root.mkdir()
        env = environment(id=f"ENV-EXAMPLE-SUP15-{side.upper()}",
            engine={"name": release["engine"], "version": release["version"], "image_digest": release["image"]},
            runtime={"gpu_count": 1, "gpu_model": "EXAMPLE GPU", "driver": "EXAMPLE driver"})
        env_path = root / "env.json"
        env_path.write_bytes(canonical_json(env.to_dict()))
        settings, _, _, _ = _prepare_roles(root, env, late_owner_change=False, first_owner=1 + 2 * len(envs))
        if side == "candidate":
            target_path = Path(settings["challenge"]["target"])
            if candidate_input == "alias":
                alias = json.loads(Path(settings["normal"]["target"]).read_bytes())
                alias["kubernetes"]["context"] = "fixture-alias-context"
                target_path.write_bytes(canonical_json(alias))
                settings["challenge"]["prepared"] = settings["normal"]["prepared"]
            elif candidate_input == "foreign-target":
                target = json.loads(target_path.read_bytes())
                target["environment_ref"] = envs[0].ref().model_dump(mode="json")
                target_path.write_bytes(canonical_json(target))
            elif candidate_input == "missing-preparation":
                settings["challenge"]["prepared"] = str(root / "missing-manifest.json")
        cfg["sides"][side] = {"supply_boot": settings}
        envs.append(env)
        entries.append((side, env_path))

    prepared = application.update_readiness(PACK, envs, pol, cfg)
    inputs = next(item for item in prepared["checks"] if item["check"] == "inputs")
    assert inputs["ok"] is (prepare_gap is None), inputs
    if prepare_gap is not None:
        assert inputs["detail"] == prepare_gap

    write_config(cfg, config_path)
    index = mvp_live_readiness.template(PACK)
    index["inputs"] = [{"check_id": check_id, "side": side, **{
        role: {"path": str(path), "sha256": sha256_hex(path.read_bytes())}
        for role, path in {"environment": env, "policy": policy_path, "configuration": config_path}.items()
    }} for side, env in entries]
    index_path = external / "index.json"
    index_path.write_bytes(canonical_json(index))
    if candidate_input == "changed-snapshot":
        prepared_target = Path(cfg["sides"]["candidate"]["supply_boot"]["challenge"]["target"])
        read = mvp_live_readiness._read

        def replace_after_snapshot(path):
            raw, identity = read(path)
            if Path(path) == prepared_target:
                target = json.loads(raw)
                target["kubernetes"]["context"] = "fixture-new-context"
                prepared_target.write_bytes(canonical_json(target))
            return raw, identity

        monkeypatch.setattr(mvp_live_readiness, "_read", replace_after_snapshot)
    report = mvp_live_readiness.audit(index_path, pack=PACK)
    assert report["sides"] == 138 and report["active_checks"] == 69
    assert report["file_binding_valid_sides"] == (2 if audit_gap is None else 1)
    for row in report["rows"]:
        if row["check_id"] != check_id:
            continue
        assert row["ready_for_execution"] is row["live_target_verified"] is row["engine_executed"] is False
        if row["side"] == "candidate" and audit_gap is not None:
            assert row["blocking_codes"] == [audit_gap]
        else:
            assert row["binding_schema"] == "guard-run-binding/2"
            assert set(row["blocking_codes"]) == {"EXAMPLE_ENV_NOT_ACTUAL", "LIVE_EXECUTOR_PREFLIGHT_REQUIRED"}


@pytest.mark.parametrize(('same_owner', 'same_session'), [(True, True), (True, False), (False, True)])
def test_sup15_rejects_only_role_aliases_at_the_same_owned_record_location(
        tmp_path, external, monkeypatch, same_owner, same_session):
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE})
    settings, pins, _, _ = _prepare_roles(external, env, late_owner_change=False)
    _, _, archived_roles = retained('SUP-15', tmp_path)
    if same_owner:
        normal_target = json.loads(Path(settings['normal']['target']).read_bytes())
        # Context aliases can address the same exact namespace, Pod and container.
        normal_target['kubernetes']['context'] = 'fixture-alias-context'
        challenge_target = Path(settings['challenge']['target'])
        challenge_target.write_bytes(canonical_json(normal_target))
        pins['supply_challenge_target_sha256'] = sha256_hex(challenge_target.read_bytes())
    if same_session:
        settings['challenge']['prepared'] = settings['normal']['prepared']
        pins['supply_challenge_prepared_sha256'] = pins['supply_normal_prepared_sha256']

    check = PACK.checks['SUP-15']
    executor = registry.build_executor('supply_boot', 'kubernetes', {'supply_boot': settings})
    calls = []
    def replay(check, environment, values, role, *, role_inputs):
        assert role_inputs[role] is not None
        calls.append(role)
        return deepcopy(archived_roles[role])
    monkeypatch.setattr(executor, '_collect_role', replay)
    values = base_values(env, check.id, pins)
    if same_owner and same_session:
        with pytest.raises(ValueError, match='SUPPLY_BOOT_DISTINCT_ROLE_INPUTS_REQUIRED'):
            executor.run(check, env, values, None, tmp_path / 'evidence')
        assert calls == []
    else:
        draft = executor.run(check, env, values, None, tmp_path / 'evidence')
        assert calls == ['normal', 'challenge'] and draft.data_origin is DataOrigin.EXAMPLE
        assert not draft.observations['supply_boot_complete'].value


@pytest.mark.parametrize("late_owner_change", [False, True], ids=["stable-owner", "late-owner-change"])
def test_sup15_requires_owner_after_record_read_and_keeps_earlier_role(tmp_path, external, late_owner_change):
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE})
    settings, pins, worlds, records = _prepare_roles(external, env, late_owner_change)

    def runner(argv, **kwargs):
        namespace = argv[5] if argv[3:5] == ["get", "namespace"] else argv[3].removeprefix("--namespace=")
        return worlds[namespace].runner(argv, **kwargs)

    executor = registry.build_executor("supply_boot", "kubernetes", {"supply_boot": settings})
    executor.runner = runner
    check = PACK.checks["SUP-15"]
    values = base_values(env, check.id, pins)
    draft = executor.run(check, env, values, None, tmp_path / "evidence")
    normal, challenge = draft.observations["supply_boot_sessions"].value
    artifacts = {artifact.name: artifact.path for artifact in draft.artifacts}
    assert draft.data_origin is DataOrigin.EXAMPLE
    assert [world.files for world in worlds.values()] == [1, 1]
    assert worlds["guard-challenge"].bad_owner is late_owner_change
    assert normal["collected"] and normal["owner_binding_verified"]
    assert normal["record_sha256"] == sha256_hex(records["normal"])
    assert artifacts["supply_boot_normal_record.json"].read_bytes() == records["normal"]
    assert draft.observations["supply_boot_record_authenticated"].value is False
    projected = _normalize(check, draft)
    evd = evidence(check_id=check.id, status=draft.status, data_origin=draft.data_origin,
                   normal=projected["normal_control"], observations=projected["observations"])
    verdict = RuleTableJudge().judge(check, evd, check.judge.params, values).verdicts["security"]

    if late_owner_change:
        assert draft.status is ExecStatus.ABORTED
        assert not challenge["collected"] and not challenge["owner_binding_verified"]
        assert challenge["record_sha256"] is None
        assert "reported_boot_record" not in challenge
        assert "supply_boot_challenge_record.json" not in artifacts
        assert draft.observations["supply_boot_violations"].value == []
        assert verdict is Verdict.UNKNOWN
    else:
        assert draft.status is ExecStatus.COMPLETED
        assert challenge["collected"] and challenge["owner_binding_verified"]
        assert artifacts["supply_boot_challenge_record.json"].read_bytes() == records["challenge"]
        assert verdict is Verdict.FAIL
