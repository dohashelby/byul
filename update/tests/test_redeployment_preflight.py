"""Prepared generation files feed public readiness and the immutable run recipe."""

from copy import deepcopy
import json
from pathlib import Path
from uuid import UUID

import pytest

from guard import application
from guard.adapters.vllm_release_profiles import release_profile
from guard.collectors.lifecycle import prepare_assets
from guard.collectors.model_read_scope import build_scope
from guard.core.execution_recipe import bind_recipe
from guard.core.run import run_plan
from guard.core.select import select
from guard.executors import lifecycle_transition
from guard.models import ExecStatus, Side, canonical_json, load_pack, sha256_hex, utcnow
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import environment, policy
from tests.test_kubernetes_sampling import inputs as owned_targets
from tests.test_lifecycle_capture import REPOSITORY, external


def _write(path, value):
    path.write_bytes(canonical_json(value))
    return str(path)


def _generation(directory, name, identity, *, version, owner=None, profile=None, context="fixture-context"):
    directory.mkdir()
    image = next(pin for pin in release_profile(version)["images"] if pin.startswith("vllm/"))
    files = [{"path": "weights.safetensors", "sha256": sha256_hex(b"EXAMPLE approved weight reference"), "bytes": 33}]
    env = environment(id=f"ENV-REDEPLOY-{identity}", engine={"name": "vllm", "version": version, "image_digest": image},
        model={"id": "EXAMPLE-model", "revision": f"EXAMPLE-{name}", "inventory_sha256": sha256_hex(canonical_json(files))})
    target = owned_targets()
    target["kubernetes"]["context"] = context
    engine = owner or {**target["kubernetes"]["engine"], "pod": f"engine-{identity}",
                       "pod_uid": str(UUID(int=identity)), "container_id": f"containerd://{identity:064x}"}
    target["kubernetes"]["engine"] = {**engine, "image": image, "image_id": image}
    target["engine"]["image_digest"] = image
    target.update(schema="guard-kubernetes-request-binding-target/1", environment_ref=env.ref().model_dump(mode="json"),
                  observed_version=version, comparison_config={"EXAMPLE_launch_label": name},
                  model_mapping={"repository_id": env.model.id, "revision": env.model.revision, "api_model": "EXAMPLE-serving"})
    native = {"schema": "guard-kubernetes-lifecycle-target/1", "lab": True, "run_id": target["kubernetes"]["run_id"],
              "environment_ref": target["environment_ref"], "kubernetes": {key: target["kubernetes"][key]
                                                                          for key in ("context", "engine")}}
    reference = _write(directory / "reference.json", {"schema": "guard.model-manifest/1", "scope": "all",
        "model_id": env.model.id, "files": files, "inventory_sha256": env.model.inventory_sha256})
    scope = build_scope(env, native, reference, "/models", nonsecret_model_weights=True)
    prepared = directory / "native"
    prepare_assets(prepared, REPOSITORY, profile=profile or lifecycle_transition.generation_sensor.PROFILE,
                   model_read_scope=_write(directory / "scope.json", scope))
    return env, {"id": name, "environment": _write(directory / "env.json", env.to_dict()),
                 "target": _write(directory / "target.json", target), "prepared": str(prepared / "manifest.json")}


def _configuration(external, check_id, *, change=None):
    pack = load_pack(REPOSITORY / "update" / "packs" / "PACK-BASELINE")
    pol = policy(pack.ref(), scope={"include": [check_id]}, values={})
    cfg = {"adapter": {"name": "vllm"}, "executors": {"lifecycle_transition": "kubernetes"},
           "update": {"allowed_checks": [check_id]}, "sides": {}, "run_binding": {check_id: {
               "phase_generation": {"before": "old", "during": "old", "after": "new", "recovered": "new"},
               "sample_offsets_ms": {"before": 1000, "during": 2000, "after": 3000, "recovered": 4000}}}}
    envs = []
    baseline_new = None
    for i, side in enumerate(("baseline", "candidate")):
        version = "0.30.0" if side == "baseline" and change not in {"same-release-owners", "reused-session"} else "0.31.0"
        env, old = _generation(external / f"{side}-old", "old", 100 + 2 * i, version=version)
        owner = None
        after_version = version
        if side == "candidate":
            if change in {"same-owner", "stale-restart", "same-pod-restart"}:
                owner = json.loads(Path(old["target"]).read_bytes())["kubernetes"]["engine"]
                if change != "same-owner":
                    owner.update(container_id="containerd://" + "f" * 64, started_at="2026-10-26T00:00:01Z")
                    if change == "same-pod-restart":
                        owner["restart_count"] += 1
            if change in {"cross-side-owner", "same-release-owners"}:
                owner = json.loads(Path(baseline_new["target"]).read_bytes())["kubernetes"]["engine"]
            if change == "other-release":
                after_version = "0.30.0"
        if side == "candidate" and change == "reused-session":
            new = baseline_new
        else:
            _, new = _generation(external / f"{side}-new", "new", 101 + 2 * i, version=after_version,
                                 owner=owner, profile=lifecycle_transition.endpoint_sensor.PROFILE,
                                 context="fixture-restore" if side == "candidate" and change == "restore-context" else "fixture-context")
        if side == "baseline":
            baseline_new = new
        elif change == "invalid-http-model":
            path = Path(new["target"])
            target = json.loads(path.read_bytes())
            target["model_mapping"]["revision"] = "EXAMPLE-unrelated-revision"
            _write(path, target)
        envs.append(env)
        cfg["sides"][side] = {"check_config": {check_id: {"generations": [old, new]}}}
    return pack, envs, pol, cfg


def _forbidden(*args, **kwargs):
    pytest.fail("Offline generation admission must not perform a runtime request")


def _bound_plan(store, pack, envs, pol, cfg):
    put_pack(store, pack.path)
    for obj in [*envs, pol]:
        store.put(obj)
    plan = bind_recipe(select(pack, envs, pol, mode="update"), cfg, envs)
    store.put(plan)
    return plan


@pytest.mark.parametrize(("check_id", "change", "error"), [
    ("LIF-05", None, None),
    ("LIF-16", "same-pod-restart", None),
    ("LIF-16", "restore-context", None),
    ("LIF-05", "same-release-owners", None),
    ("LIF-05", "same-owner", "TRANSITION_DISTINCT_GENERATIONS_REQUIRED"),
    ("LIF-05", "stale-restart", "TRANSITION_DISTINCT_GENERATIONS_REQUIRED"),
    ("LIF-16", "other-release", "TRANSITION_GENERATION_RELEASE_MISMATCH"),
    ("LIF-05", "cross-side-owner", "TRANSITION_GENERATION_SIDE_REUSE"),
    ("LIF-16", "reused-session", "TRANSITION_GENERATION_SIDE_REUSE"),
    ("LIF-16", "invalid-http-model", "TRANSITION_GENERATION_HTTP_TARGET_INVALID"),
])
def test_public_preparation_validates_declared_generation_files(external, monkeypatch, check_id, change, error):
    monkeypatch.setattr(lifecycle_transition.KubernetesHTTP, "__call__", _forbidden)
    pack, envs, pol, cfg = _configuration(external, check_id, change=change)
    report = application.update_readiness(pack, envs, pol, cfg)
    inputs = next(row for row in report["checks"] if row["check"] == "inputs")
    assert inputs["ok"] is (error is None), report
    if error:
        assert error in inputs["detail"]
    assert all(not row["runtime_qualified"] for row in report["check_support"])
    assert any("installed native producer" in reason for reason in report["limitations"])


def test_side_reuse_blocks_the_transition_and_leaves_unselected_inputs_nonblocking(external, tmp_path, monkeypatch):
    pack, envs, pol, cfg = _configuration(external, "LIF-05", change="cross-side-owner")
    del cfg["update"]["allowed_checks"]
    store = Store(tmp_path / "store")
    plan = _bound_plan(store, pack, envs, pol, cfg)
    monkeypatch.setattr(lifecycle_transition.LifecycleTransitionExecutor, "run", _forbidden)
    monkeypatch.setattr(lifecycle_transition.KubernetesHTTP, "__call__", _forbidden)
    evidence, = run_plan(store, pack, plan, application.run_config(deepcopy(cfg)), only_side=Side.candidate)
    assert evidence.execution.status == ExecStatus.NOT_RUN
    assert evidence.execution.abort_reason == "RUN_BINDING_TRANSITION_GENERATION_SIDE_REUSE"

    other = policy(pack.ref(), id="POL-UNRELATED-EXAMPLE", scope={"include": ["SUP-10"]}, values={})
    plan = _bound_plan(store, pack, envs, other, cfg)
    evidence, = run_plan(store, pack, plan, application.run_config(deepcopy(cfg)), only_side=Side.candidate)
    assert evidence.check_id == "SUP-10"
    assert evidence.execution.abort_reason == "executor 'supply_boot' not configured in guard.toml [executors]"


@pytest.mark.parametrize("replace_after_check", [False, True])
def test_run_rejects_changed_generation_manifest_before_runtime(external, tmp_path, monkeypatch, replace_after_check):
    from guard.core import run as core_run

    check_id = "LIF-05"
    pack, envs, pol, cfg = _configuration(external, check_id)
    store = Store(tmp_path / "store")
    plan = _bound_plan(store, pack, envs, pol, cfg)
    target = Path(cfg["sides"]["candidate"]["check_config"][check_id]["generations"][1]["target"])
    changed = json.loads(target.read_bytes())
    changed["comparison_config"] = {"EXAMPLE_launch_label": "different"}
    monkeypatch.setattr(lifecycle_transition.LifecycleTransitionExecutor, "run", _forbidden)
    monkeypatch.setattr(lifecycle_transition.KubernetesHTTP, "__call__", _forbidden)
    if replace_after_check:
        original = core_run._run_one

        def replace_then_run(*args, **kwargs):
            _write(target, changed)
            return original(*args, **kwargs)

        monkeypatch.setattr(core_run, "_run_one", replace_then_run)
        evidence, = run_plan(store, pack, plan, application.run_config(deepcopy(cfg)), only_side=Side.candidate)
        assert evidence.execution.status == ExecStatus.NOT_RUN
        assert evidence.execution.abort_reason == "RUN_BINDING_TRANSITION_GENERATIONS_CHANGED"
        assert evidence.execution.run_binding is None
    else:
        _write(target, changed)
        with pytest.raises(ValueError, match="EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED"):
            run_plan(store, pack, plan, application.run_config(deepcopy(cfg)), only_side=Side.candidate)
        assert not store.find_latest("EVD", lambda row: row.plan == plan.ref())
