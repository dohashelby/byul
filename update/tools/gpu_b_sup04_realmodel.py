"""SUP04 real CPU model preparation and two existing-factory policy cases."""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import uuid

from guard.application import _credentials, _managed_config
from guard.collectors.file import load_environment, load_policy
from guard.core.select import select
from guard.core.run import RunConfig, run_plan
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.executors import registry
from guard.executors.running_image_approval import _pinned, _snapshot, _target, _IMAGE_ID, _CONTAINER_ID, _timestamp
from guard.models import Ref, load_pack, utcnow
from guard.runtime.docker import Docker
from guard.runtime import sandbox
from guard.store import Store
from guard.store.packs import get_pack, put_pack
from guard.store.bundle import export_bundle, restore_bundle, verify_complete


def bind_image_target(raw, env, before, repo_digests, after):
    """Bind existing target format using actual image snapshots; no approval."""
    if not env.hash_ok() or not isinstance(raw, dict):
        raise ValueError("SUP04_ENVIRONMENT_INVALID")
    reference = env.ref().model_dump(mode="json")
    if raw.get("environment_ref") not in (None, reference):
        raise ValueError("SUP04_EXISTING_ENVIRONMENT_REF_MISMATCH")
    try:
        container, pinned, run_id = raw["engine"]["container"], raw["engine"]["image_digest"], raw["run_id"]
        valid = (_pinned(pinned) and pinned == env.engine.image_digest and type(before) is dict and before == after
                 and before["name"] == "/" + container and before["configured_image"] == pinned
                 and before["owned_run_id"] == run_id and _IMAGE_ID.fullmatch(before["image_id"]) is not None
                 and _CONTAINER_ID.fullmatch(before["container_id"]) is not None and _timestamp(before["started_at"])
                 and type(repo_digests) is list and 0 < len(repo_digests) <= 128
                 and all(_pinned(value) for value in repo_digests) and pinned in repo_digests)
    except (KeyError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("SUP04_ACTUAL_IMAGE_BINDING_UNVERIFIED")
    bound = deepcopy(raw)
    bound["environment_ref"] = reference
    from types import SimpleNamespace
    if _target(SimpleNamespace(env=env, target=bound), None) != (container, pinned, run_id):
        raise ValueError("SUP04_EXISTING_CONSUMER_TARGET_REJECTED")
    return bound


def actual_image_binding(docker, raw, env):
    container, pinned = raw["engine"]["container"], raw["engine"]["image_digest"]
    before = _snapshot(docker.inspect_json(container, timeout=10), container, pinned, docker.run_id)
    if before is None:
        raise ValueError("SUP04_PREPARATION_IDENTITY_UNVERIFIED")
    info = docker.image_info(before["image_id"], timeout=10)
    digests = info.get("digests") if isinstance(info, dict) else None
    after = _snapshot(docker.inspect_json(container, timeout=10), container, pinned, docker.run_id)
    return bind_image_target(raw, env, before, digests, after), {"before": before, "after": after, "repo_digests": digests}


def verify_source(repo, commit, manifest):
    if subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip() != commit:
        raise ValueError("SUP04_FIXED_SOURCE_CHANGED")
    subprocess.run(["git", "-C", str(repo), "diff", "--exit-code", "HEAD", "--", "update/src", "update/release",
                    "update/tools/verify_req10_consumption.py", "update/tests/builders.py",
                    "update/tools/gpu_b_sup04_realmodel.py"], check=True)
    if hashlib.sha256((repo / "update/release/reviewed-source.json").read_bytes()).hexdigest() != manifest:
        raise ValueError("SUP04_FIXED_MANIFEST_CHANGED")
    sys.path.insert(0, str(repo / "update"))
    from tools.verify_req10_consumption import verify
    return verify(repo)


def run(args):
    repo = args.source_root.resolve(strict=True)
    proof = verify_source(repo, args.source_commit, args.manifest_sha256)
    pack = load_pack(args.pack)
    check = pack.checks["SUP-04"]
    if check.executor != "container_config" or check.collection_profile != "running-image-approval-v1":
        raise ValueError("SUP04_CHECK_PROFILE_MISMATCH")
    env = load_environment(args.environment)
    cfg = tomllib.loads(args.config.read_text(encoding="utf-8"))
    managed = _managed_config(env, cfg)
    if managed.get("launch_profile") != "cpu-bounded" or env.runtime.gpu_count != 0:
        raise ValueError("SUP04_EXPLICIT_CPU_PROFILE_REQUIRED")
    policies = [(label, load_policy(path, pack)) for label, path in (("included", args.included_policy), ("empty", args.empty_policy))]
    for label, policy in policies:
        factory = registry.build_executor("container_config", "real", cfg)
        factory.validate_policy(check, policy.values["SUP-04"])
        expected = [env.engine.image_digest] if label == "included" else []
        if (policy.status.value != "DRAFT" or policy.data_origin.value != "EXAMPLE" or policy.scope.include != ["SUP-04"]
                or policy.values["SUP-04"]["approved_image_digests"] != expected):
            raise ValueError("SUP04_TWO_EXPLICIT_LAB_POLICIES_REQUIRED")
    if not args.execute:
        return {"validated": True, "engine_started": False, "factory_executed": False,
                "profile": check.collection_profile, "source": args.source_commit, "source_proof": proof}
    override = os.environ.get("DOCKER_HOST")
    if override and override not in {"unix:///var/run/docker.sock", "npipe:////./pipe/dockerDesktopLinuxEngine"}:
        raise ValueError("SUP04_LOCAL_DOCKER_ONLY")
    endpoint = json.loads(subprocess.check_output(["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"], text=True))
    if endpoint not in {"unix:///var/run/docker.sock", "npipe:////./pipe/dockerDesktopLinuxEngine"}:
        raise ValueError("SUP04_LOCAL_DOCKER_ONLY")
    from guard.update_validation import reserve_output
    output = reserve_output(args.output, protected=[repo, Path(managed["models_dir"]), args.environment, args.config, args.included_policy, args.empty_policy])
    docker = Docker("sup04-realmodel-" + uuid.uuid4().hex[:12])
    record = {"request_id": "S1-SUP04-REALMODEL-PREP-01", "source": args.source_commit,
              "manifest_sha256": args.manifest_sha256, "run_id": docker.run_id, "GPU_count": 0, "AWS_actions": 0,
              "started_at": utcnow().isoformat(), "cases": [], "cleanup_verified": False, "whole_question_complete": False}
    try:
        options = cfg["update"]
        effective, keys = _credentials(cfg, options["gateway_profile"])
        raw = sandbox.up(docker, options.get("topology", "isolated"), output / "runtime-target",
            keys.get("GUARD_ALLOWED_KEY", keys.get("GUARD_GENERAL_KEY")), engine="vllm", vllm=managed,
            gateway_profile=options.get("gateway_profile", "single"), gateway_keys=keys,
            engine_profile=options.get("engine_profile", "default"), credential_canary=False)
        launch = raw.get("comparison_config", {}).get("launch", {})
        if (raw.get("observed_version") != env.engine.version or launch.get("model") != env.model.id
                or launch.get("revision") != env.model.revision):
            raise ValueError("SUP04_ACTUAL_MODEL_VERSION_BINDING_MISMATCH")
        target, binding = actual_image_binding(docker, raw, env)
        with (output / "bound-target.json").open("x", encoding="utf-8") as stream:json.dump(target, stream, indent=2)
        record["actual_image_binding"] = binding
        record["raw_target_preserved"] = str(output / "runtime-target/target.json")
        for label, policy in policies:
            current, _ = actual_image_binding(docker, raw, env)
            if current != target:
                raise ValueError("SUP04_GENERATION_CHANGED_BETWEEN_CASES")
            store = Store(output / label / "store")
            pack_ref = put_pack(store, pack.path)
            store.put(env);store.put(policy)
            plan = select(pack, [env], policy);store.put(plan)
            config = RunConfig(executors={"container_config": "real"}, adapter="vllm",
                raw={"container_config": {**cfg.get("container_config", {}), "target": str(output / "bound-target.json")}})
            evd, = run_plan(store, pack, plan, config)
            asof = utcnow();result, = judge_plan(store, pack, plan, asof)
            report = build_report(store, pack, plan, asof);store.put(report)
            case = {"label": label, "as_of": asof.isoformat(), **{kind: obj.ref().model_dump(mode="json")
                for kind, obj in (("ENV", env), ("POL", policy), ("PLAN", plan), ("EVD", evd), ("RES", result), ("RPT", report))},
                "PACK": pack_ref.model_dump(mode="json"), "data_origin": evd.data_origin.value,
                "execution_status": evd.execution.status.value, "verdicts": result.verdicts.model_dump(mode="json"),
                "official": report.official, "observations": {key: value.model_dump(mode="json") for key, value in evd.observations.items()}}
            record["cases"].append(case)
            verify_complete(store)
            bundle = output / label / "evidence.zip";export_bundle(store.root, bundle)
            restore_bundle(bundle, output / label / "restored")
            restored = Store.read_only(output / label / "restored");verify_complete(restored)
            for kind in ("ENV", "POL", "PLAN", "EVD", "RES", "RPT"):
                restored.resolve(Ref.model_validate(case[kind]))
            # Rejudgment writes only into this separate restored database.
            replay_store = Store(output / label / "restored")
            saved = get_pack(replay_store, pack_ref)
            replay, = judge_plan(replay_store, saved, replay_store.resolve(plan.ref()), datetime.fromisoformat(case["as_of"]))
            if replay.ref().model_dump(mode="json") != case["RES"]:
                raise ValueError("SUP04_SAVED_PACK_REJUDGMENT_CHANGED")
            case.update(bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(), exact_refs_restored_and_rejudged=True)
            if (evd.execution.status.value != "COMPLETED" or not evd.observations["running_image_identity_verified"].value
                    or evd.observations["running_image_unapproved"].value is not (label == "empty")
                    or result.verdicts.security.value != ("FAIL" if label == "empty" else "UNKNOWN")):
                raise ValueError("SUP04_SCOPED_IMAGE_CONTRACT_NOT_REACHED")
    except BaseException as error:
        record.update(error_type=type(error).__name__, error_message=str(error)[:2000])
        raise
    finally:
        try:
            failures = docker.cleanup(timeout=30)
            record.update(cleanup_verified=not failures, cleanup_errors=failures)
        except Exception as error:
            record["cleanup_error_type"] = type(error).__name__
        record["finished_at"] = utcnow().isoformat()
        try:
            with (output / "result.json").open("x", encoding="utf-8") as stream:json.dump(record, stream, indent=2)
        except OSError:
            if "error_type" not in record:raise
    if not record["cleanup_verified"]:
        raise RuntimeError("SUP04_OWNED_CLEANUP_INCOMPLETE; inspect preserved result.json")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "pack", "environment", "config", "included-policy", "empty-policy", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("source-commit", "manifest-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--execute", action="store_true")
    result = run(parser.parse_args())
    print(json.dumps({"validated": result.get("validated"), "cases": len(result.get("cases", [])),
                      "cleanup_verified": result.get("cleanup_verified"), "GPU_count": 0, "AWS_actions": 0}))


if __name__ == "__main__":main()
