"""Pack39 configuration and recipe snapshots for two bounded supply_boot checks."""

import json
from pathlib import Path

import pytest

from guard import application
from guard.configuration import (
    NORMAL_FILE_READS_SCOPE_ERROR, bounded_metadata_scopes, merge_config, update_allowed_checks,
)
from guard.core.execution_recipe import bind_recipe
from guard.core.select import select
from guard.executors.kubernetes_normal_startup_load import capture_startup_log_prefix
from guard.models import Environment, canonical_json, sha256_hex
from tests.builders import policy
from tests.test_kubernetes_normal_safetensors_reader import ReaderWorld
from tests.test_kubernetes_normal_startup_load import STARTED, Adapter
from tests.test_mvp_metadata_wiring import sources


CHECKS = ("SUP-05", "SUP-12")


def _mixed_config(tmp_path, sides):
    return {
        "adapter": {"name": "vllm"},
        "executors": {"supply_boot": "kubernetes"},
        "update": {"mode": "external", "allowed_checks": list(CHECKS)},
        "check_config": {
            "SUP-05": {"normal_file_reads_only": True},
            "SUP-12": {"normal_safetensors_reader_only": True},
        },
        "sides": {
            side: {"check_config": {
                "SUP-05": {"normal": {
                    "target": str(tmp_path / f"{side}-capture-target.json"),
                    "prepared": str(tmp_path / f"{side}-prepared.json"),
                    "reference_manifest": str(tmp_path / f"{side}-reference.json"),
                }},
                "SUP-12": {"target": str(entry["target"])},
            }}
            for side, entry in sides.items()
        },
    }


def test_prebound_mixed_supply_admits_only_the_check_local_target_shapes(tmp_path, sources):
    pack, sides, _ = sources
    cfg = _mixed_config(tmp_path, sides)
    effective = [merge_config(cfg, settings) for settings in cfg["sides"].values()]
    scopes = bounded_metadata_scopes(effective, pack, update_allowed_checks(cfg, pack))
    selected = {check_id: pack.checks[check_id].executor for check_id in CHECKS}
    for side, raw, scope in zip(sides, effective, scopes, strict=True):
        file_reads = cfg["sides"][side]["check_config"]["SUP-05"]["normal"]
        assert scope == {
            "SUP-05": ({"normal_file_reads_only": True, "normal": file_reads}, None),
            "SUP-12": ({"normal_safetensors_reader_only": True, "target": str(sides[side]["target"])}, None),
        }
        assert application._bounded_kubernetes_targets(raw, selected) == {
            "SUP-05": Path(file_reads["target"]), "SUP-12": sides[side]["target"],
        }


@pytest.mark.parametrize("invalid", ["common-target", "legacy-sibling", "late-candidate"])
def test_prebound_mixed_supply_rejects_scope_widening(tmp_path, sources, invalid):
    pack, sides, _ = sources
    cfg = _mixed_config(tmp_path, sides)
    if invalid == "common-target":
        cfg["sides"]["baseline"]["supply_boot"] = {"target": str(sides["baseline"]["target"])}
    elif invalid == "legacy-sibling":
        cfg["update"]["allowed_checks"].append("SUP-15")
        for side, value in cfg["sides"].items():
            value["check_config"]["SUP-15"] = {
                role: {"target": str(tmp_path / f"{side}-{role}-target.json"),
                       "prepared": str(tmp_path / f"{side}-{role}-prepared.json")}
                for role in ("normal", "challenge")
            }
    else:
        cfg["update"]["kubernetes_candidate_binding"] = "after_baseline"

    with pytest.raises(ValueError, match=f"^{NORMAL_FILE_READS_SCOPE_ERROR}$"):
        application._validate_requested_normal_scopes(cfg, pack, update_allowed_checks(cfg, pack))


@pytest.mark.parametrize("foreign_candidate", [False, True], ids=["both-owned", "foreign-sup05-candidate"])
def test_prebound_mixed_supply_snapshots_pin_each_check_independently(tmp_path, sources, foreign_candidate):
    pack, sides, _ = sources
    cfg = _mixed_config(tmp_path, sides)
    envs, expected = [], {}
    for index, (side, entry) in enumerate(sides.items(), 51):
        env = Environment.model_validate_json(entry["environment"].read_bytes())
        env = env.model_copy(update={"runtime": env.runtime.model_copy(update={"pp_size": 1, "dp_size": 1})}).seal()
        envs.append(env)
        owner = env.ref().model_dump(mode="json")
        stock = json.loads(entry["target"].read_bytes())
        stock["environment_ref"] = stock["model_mapping"]["environment_ref"] = owner
        stock["kubernetes"]["engine"]["started_at"] = STARTED
        modern = env.engine.version == "0.31.0"
        fixture = ReaderWorld(stock, env, Adapter(5354 if modern else 5416), modern, None)
        stock["startup_log_prefix"] = capture_startup_log_prefix(stock, env, runner=fixture.runner)
        entry["target"].write_bytes(canonical_json(stock))

        capture_owner = envs[0].ref().model_dump(mode="json") if side == "candidate" and foreign_candidate else owner
        captured = {
            "schema": "guard-kubernetes-lifecycle-target/1",
            "run_id": stock["kubernetes"]["run_id"],
            "lab": True,
            "environment_ref": capture_owner,
            "kubernetes": {
                "context": stock["kubernetes"]["context"],
                "engine": {
                    **stock["kubernetes"]["engine"],
                    "pod_uid": f"00000000-0000-0000-0000-{index:012d}",
                    "container_id": f"containerd://{index:064x}",
                },
            },
        }
        capture_path = Path(cfg["sides"][side]["check_config"]["SUP-05"]["normal"]["target"])
        capture_path.write_bytes(canonical_json(captured))
        expected[side] = {
            "SUP-05": None if side == "candidate" and foreign_candidate else sha256_hex(canonical_json(captured)),
            "SUP-12": sha256_hex(canonical_json(stock)),
        }

    pol = policy(pack.ref(), scope={"include": list(CHECKS)},
                 values={"SUP-12": {"supply_allowed_readers": ["safetensors"]}})
    plan = select(pack, envs, pol, mode="update")
    selected = {item.check_id: item.executor for item in plan.items if item.selected}
    assert set(selected) == set(CHECKS)
    effective = {side: merge_config(cfg, settings) for side, settings in cfg["sides"].items()}
    paths = {side: application._bounded_kubernetes_targets(raw, selected) for side, raw in effective.items()}
    snapshots = {path: None for target_paths in paths.values() for path in target_paths.values()}
    plan = bind_recipe(plan, cfg, envs, target_snapshots=snapshots)
    assert plan.inputs.execution_recipe_sha256 and len(snapshots) == 4
    for side, env in zip(sides, envs, strict=True):
        application._pin_prebound_kubernetes_targets(effective[side], env, paths[side], snapshots)
        assert effective[side]["_kubernetes_target_sha256_by_check"] == expected[side]
