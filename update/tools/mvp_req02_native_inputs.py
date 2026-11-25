"""Bind the two owned REQ-02 route targets and native assets for a DRAFT UPDATE.

The input JSON has baseline and candidate objects, each with environment,
target, prepared and record_session. Paths are relative to the input file.
The existing Gateway producer must publish its record at the session's fixed
path on the same target. This tool neither installs sensors nor reads Pods.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from guard.adapters.vllm_lifecycle_catalog import ROWS as NATIVE_RELEASE_ROWS
from guard.adapters.vllm_lifecycle_sensor import CONFIG_SCHEMA
from guard.collectors.lifecycle import load_prepared
from guard.core.select import _in_scope
from guard.executors import run_binding
from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
from guard.executors.kubernetes_native_route import KubernetesNativeRouteExecutor
from guard.executors.kubernetes_route_record import COMPOSITE_PROFILE, KubernetesRouteRecordExecutor
from guard.models import Environment, Policy, canonical_json, load_pack, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import KubernetesHTTP, ROUTE_AUTHENTICATION_PROFILE
from guard.update_validation import _plain_path, reserve_output
from tools.j02_run_config import write_config
from tools.mvp_a_input_preflight import _read
from tools.mvp_versions import versions


ROOT = Path(__file__).resolve().parents[1]
CHECK = "REQ-02"
SIDE_FIELDS = {"environment", "target", "prepared", "record_session"}


def prepare(spec_path, policy_path, output):
    """Write scoped guard.toml and file custody for a new paired attempt."""
    pinned = {}

    def read(path, limit):
        path = _plain_path(Path(path))
        snapshot = _read(path, max_bytes=limit)
        if path in pinned and snapshot != pinned[path]:
            raise ValueError("REQ02_INPUT_CHANGED")
        pinned[path] = snapshot
        return path, snapshot[0]

    spec_path, spec_bytes = read(spec_path, 16384)
    spec = strict_json(spec_bytes, limit=16384)
    releases = versions()
    if type(spec) is not dict or set(spec) != releases.keys():
        raise ValueError("REQ02_PAIRED_INPUTS_REQUIRED")

    pack = load_pack(ROOT / "packs/PACK-BASELINE")
    check = pack.checks[CHECK]
    policy_path, policy_bytes = read(policy_path, 262144)
    policy_document = strict_json(policy_bytes, limit=262144)
    if type(policy_document) is not dict or policy_document.get("kind") != "POL":
        raise ValueError("REQ02_SEALED_EXAMPLE_POLICY_REQUIRED")
    policy = Policy.model_validate(policy_document)
    if (not policy.hash_ok() or policy.pack != pack.ref()
            or policy.status != "DRAFT" or policy.data_origin != "EXAMPLE"
            or check.collection_profile != COMPOSITE_PROFILE or not _in_scope(CHECK, policy)):
        raise ValueError("REQ02_SEALED_EXAMPLE_POLICY_REQUIRED")

    config_sides, receipts = {}, {}
    route_owners, route_sessions, sensor_sessions, model_identities = set(), set(), set(), set()
    issued_at = utcnow()
    for side, release in releases.items():
        inputs = spec[side]
        if (type(inputs) is not dict or set(inputs) != SIDE_FIELDS
                or any(type(value) is not str or not value for value in inputs.values())):
            raise ValueError("REQ02_SIDE_INPUTS_INVALID")
        env_path, env_bytes = read(spec_path.parent / inputs["environment"], 65536)
        env_document = strict_json(env_bytes, limit=65536)
        if type(env_document) is not dict or env_document.get("kind") != "ENV":
            raise ValueError("REQ02_SIDE_ENVIRONMENT_INVALID")
        env = Environment.model_validate(env_document)
        if (not env.hash_ok() or env.data_origin == "EXAMPLE"
                or (env.engine.name, env.engine.version, env.engine.image_digest)
                != (release["engine"], release["version"], release["image"])
                or not env.model.id or not env.model.revision):
            raise ValueError("REQ02_SIDE_ENVIRONMENT_INVALID")

        target_path, target_bytes = read(spec_path.parent / inputs["target"], 65536)
        target = strict_json(target_bytes, limit=65536)
        transport = KubernetesHTTP(target, env, duration=120, profile=ROUTE_AUTHENTICATION_PROFILE)
        binding = transport.binding
        prepared_path, prepared_bytes = read(spec_path.parent / inputs["prepared"], 262144)
        prepared_sha = sha256_hex(prepared_bytes)
        prepared = load_prepared(prepared_path, expected_sha256=prepared_sha)
        _, settings_bytes = read(prepared_path.parent / "settings.json", 4096)
        sensor_settings = strict_json(settings_bytes, limit=4096)
        session = prepared["session"]
        directory = f"/guard-lifecycle/data/{session}"
        expected_settings = {"schema": CONFIG_SCHEMA, "session": session, "output_dir": directory,
                             "control_path": f"{directory}/SEALED"}
        if (sensor_settings != expected_settings
                or sha256_hex(settings_bytes) != prepared["assets"]["settings.json"]["sha256"]):
            raise ValueError("REQ02_NATIVE_SETTINGS_MISMATCH")
        commit = prepared["public_commit"]
        observed_commit = env.engine.source_commit
        release_commits = {row["commit"] for row in NATIVE_RELEASE_ROWS.values()
                           if row["engine"] == release["engine"] and row["version"] == release["version"]
                           and release["image"] in row["images"]}
        abbreviated_match = (env.engine.source_commit_status == "ABBREVIATED_MATCH"
                             and bool(observed_commit) and commit.startswith(observed_commit))
        if (prepared["profile"] not in KubernetesNativeRouteExecutor.supported_native_profiles
                or release_commits != {commit}
                or env.engine.source_commit_status == "MISMATCH"
                or env.engine.expected_source_commit not in (None, commit)
                or (observed_commit not in (None, commit) and not abbreviated_match)):
            raise ValueError("REQ02_NATIVE_PREPARATION_INVALID")
        native_target = {
            "schema": "guard-kubernetes-lifecycle-target/1", "lab": True,
            "environment_ref": env.ref().model_dump(mode="json"), "run_id": binding["run_id"],
            "kubernetes": {"context": binding["context"], "engine": binding["engine"]},
        }
        KubernetesLifecycleReader(native_target, env, prepared)

        settings = {"target": str(target_path), "native_route": {
            "target": str(target_path), "prepared": str(prepared_path),
        }}
        run_cfg = {"record_session": inputs["record_session"]}
        computed, values = run_binding.effective_values(
            check, settings, env, policy.values.get(CHECK, {}), side=side, attempt_id=None,
            issued_at=issued_at, valid_from=policy.valid_from, valid_until=policy.valid_until,
            run_cfg=run_cfg, policy_ref=policy.ref(),
        )
        if computed["files"].get("route_native_prepared_sha256") != sha256_hex(canonical_json(prepared)):
            raise ValueError("REQ02_NATIVE_PREPARATION_CHANGED")
        KubernetesRouteRecordExecutor(settings).validate_policy(check, values)
        route_owners.add(binding["engine"]["pod_uid"])
        route_sessions.add(run_cfg["record_session"])
        sensor_sessions.add(prepared["session"])
        model_identities.add((env.model.id, env.model.revision, env.model.inventory_sha256))
        config_sides[side] = {check.executor: settings, "run_binding": {CHECK: run_cfg}}
        receipts[side] = {
            "environment": {"path": str(env_path), "sha256": sha256_hex(env_bytes)},
            "target": {"path": str(target_path), "sha256": sha256_hex(target_bytes)},
            "prepared": {"path": str(prepared_path), "sha256": prepared_sha, "profile": prepared["profile"]},
            "binding_files": computed["files"],
        }

    if (len(model_identities) != 1 or any(len(values) != len(releases)
                                         for values in (route_owners, route_sessions, sensor_sessions))):
        raise ValueError("REQ02_PAIR_BINDING_INVALID")
    if any(_read(path, max_bytes=len(snapshot[0])) != snapshot for path, snapshot in pinned.items()):
        raise ValueError("REQ02_INPUT_CHANGED")

    output = reserve_output(output, protected=[pack.path, *pinned, *(Path(r["prepared"]["path"]).parent
                                                                      for r in receipts.values())])
    config = {
        "store": str(output / "store"), "adapter": {"name": releases["baseline"]["engine"]},
        "update": {"mode": "external", "kubernetes_candidate_binding": "prebound", "allowed_checks": [CHECK]},
        "executors": {check.executor: "kubernetes"}, "sides": config_sides,
    }
    config_path = output / "guard.toml"
    write_config(config, config_path)
    result = {
        "check_id": CHECK, "data_origin": "EXAMPLE", "policy_ref": policy.ref().model_dump(mode="json"),
        "official": False, "static_input_binding_valid": True, "live_target_verified": False,
        "config_sha256": sha256_hex(config_path.read_bytes()), "sides": receipts,
    }
    (output / "inputs.json").write_bytes(canonical_json(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("spec", "policy", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    prepare(args.spec, args.policy, args.output)


if __name__ == "__main__":
    main()
