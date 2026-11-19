"""SUP-15 owned-record admission through the real read-only executor."""

from types import SimpleNamespace

import pytest

from guard.collectors.lifecycle import prepare_assets
from guard.core.run import _normalize
from guard.executors import registry
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, sha256_hex
from tests.builders import environment, evidence
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
