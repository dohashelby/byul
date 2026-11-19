"""SUP-15 owned-record admission through the real read-only executor."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.collectors.lifecycle import prepare_assets
from guard.core.run import _normalize
from guard.executors import registry
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, sha256_hex
from tests.builders import environment, evidence
from tests.test_b3_supply_records import retained
from tests.test_kubernetes_lifecycle import World
from tests.test_kubernetes_sampling import inputs
from tests.test_lifecycle_capture import Docker, IMAGE, external
from tests.test_supply_boot_record_access import PACK, ROOT, base_values, change, good_packet


def _prepare_roles(tmp_path, env, late_owner_change):
    settings, pins, worlds, records = {}, {}, {}, {}
    for index, role in enumerate(("normal", "challenge"), start=1):
        directory = tmp_path / role
        directory.mkdir()
        prepared = prepare_assets(directory / "assets", ROOT.parent)
        engine = inputs()["kubernetes"]["engine"]
        engine.update(namespace=f"guard-{role}", pod=f"engine-{role}", image=IMAGE,
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
