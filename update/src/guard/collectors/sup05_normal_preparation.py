"""Prepare one owned normal file-read input from saved observations."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from guard.adapters.engine_provider import get_provider
from guard.adapters.vllm_file_read_sensor import MAX_SCOPE_BYTES, dependency, fingerprint
from guard.collectors.lifecycle import load_prepared, prepare_assets
from guard.collectors.model_read_scope import build_scope
from guard.executors.kubernetes_supply_boot import _approved_reference, _model_mount
from guard.models import Environment, canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_binding import project_owned_snapshot, validate_capture_target
from guard.update_validation import reserve_output


def _owned_snapshot(snapshot, binding):
    if type(snapshot) is not dict or snapshot.keys() != {"pod", "namespace"}:
        raise ValueError("SUP05_PREPARATION_OWNER_SNAPSHOT_INVALID")
    try:
        owned = project_owned_snapshot(snapshot["pod"], snapshot["namespace"], binding["engine"],
                                       binding["run_id"], require_ready=False)
        status = owned["pod"]["status"]
        container, = status["containerStatuses"]
        if container.get("ready") is not False or any(row.get("status") == "True" for row in status["conditions"]):
            raise ValueError("SUP05_PREPARATION_CONTAINER_ALREADY_READY")
        mount, volume = _model_mount(owned)
    except (AttributeError, KeyError, TypeError):
        raise ValueError("SUP05_PREPARATION_OWNER_SNAPSHOT_INVALID") from None
    return sha256_hex(canonical_json({"mount": mount, "volume": volume}))


def prepare(
    output: Path, repository: Path, *, environment: Path, target: Path, owner_snapshot: Path,
    owner_snapshot_sha256: str, model_manifest: Path, model_manifest_sha256: str, nonsecret_model_weights: bool,
):
    """Save inputs suitable for the existing normal-only registry validator."""
    if not fingerprint(owner_snapshot_sha256) or not fingerprint(model_manifest_sha256):
        raise ValueError("SUP05_PREPARATION_INPUT_DIGEST_INVALID")
    env_raw = dependency(environment, None)
    value = strict_json(env_raw, limit=MAX_SCOPE_BYTES)
    if type(value) is not dict or value.get("kind") != "ENV":
        raise ValueError("SUP05_PREPARATION_SEALED_ENV_REQUIRED")
    env = Environment.model_validate(value)
    if not env.hash_ok():
        raise ValueError("SUP05_PREPARATION_ENV_HASH_INVALID")
    source = get_provider(env.engine.name).evidence_contract("approved_file_read_preparation", env)
    if type(source) is not dict or source.keys() != {"profile", "model_root"}:
        raise ValueError("SUP05_PREPARATION_PROFILE_UNSUPPORTED")

    target_raw = dependency(target, None)
    topology = strict_json(target_raw, limit=65536)
    binding = validate_capture_target(topology, env)
    owner_raw = dependency(owner_snapshot, owner_snapshot_sha256)
    model_mount_sha256 = _owned_snapshot(strict_json(owner_raw, limit=MAX_SCOPE_BYTES), binding)
    reference_raw = dependency(model_manifest, model_manifest_sha256)
    scope = build_scope(env, topology, model_manifest, source["model_root"],
                        nonsecret_model_weights=nonsecret_model_weights, hash_model_bytes=True,
                        approved_manifest_sha256=model_manifest_sha256)
    output = reserve_output(output, protected=[repository, environment, target, owner_snapshot, model_manifest])
    output.chmod(0o700)
    scope_raw = canonical_json(scope)
    paths = {name: output / name for name in ("environment.json", "target.json", "reference-manifest.json",
                                              "model-read-scope.json")}
    for name, body in (("environment.json", env_raw), ("target.json", target_raw),
                       ("reference-manifest.json", reference_raw), ("model-read-scope.json", scope_raw)):
        with paths[name].open("xb") as stream:
            if stream.write(body) != len(body):
                raise OSError("SUP05_PREPARATION_INPUT_PARTIAL_WRITE")
    assets = output / "lifecycle"
    prepare_assets(assets, repository, profile=source["profile"], model_read_scope=paths["model-read-scope.json"])
    manifest = assets / "manifest.json"
    prepared_file_sha256 = sha256_hex(dependency(manifest, None))
    prepared = load_prepared(manifest, expected_sha256=prepared_file_sha256)
    _approved_reference(paths["reference-manifest.json"], model_manifest_sha256, env, topology, prepared)
    receipt = {
        "schema": "guard-sup05-normal-preparation/1",
        "environment": str(paths["environment.json"]),
        "environment_ref": env.ref().model_dump(mode="json"),
        "input_data_origin": env.data_origin.value,
        "producer_profile": prepared["profile"],
        "session": prepared["session"],
        "normal": {"target": str(paths["target.json"]), "prepared": str(manifest),
                   "reference_manifest": str(paths["reference-manifest.json"])},
        "input_sha256": {"environment": sha256_hex(env_raw), "target": sha256_hex(target_raw),
                         "owner_snapshot": owner_snapshot_sha256, "reference_manifest": model_manifest_sha256},
        "model_mount_projection_sha256": model_mount_sha256,
        "scope_input_sha256": sha256_hex(scope_raw),
        "deployed_scope_sha256": prepared["assets"]["model-read-scope.json"]["sha256"],
        "target_sha256": sha256_hex(canonical_json(topology)),
        "prepared_sha256": sha256_hex(canonical_json(prepared)),
        "prepared_file_sha256": prepared_file_sha256,
        "approval_verified": False,
        "live_owner_verified": False,
        "pre_import_verified": False,
        "asset_publication_verified": False,
        "engine_started": False,
        "model_files_read": False,
        "native_consumption_observed": False,
    }
    with TemporaryDirectory(dir=output, prefix=".sup05-preparation-") as staging:
        path = Path(staging) / "preparation.json"
        raw = canonical_json(receipt)
        if path.write_bytes(raw) != len(raw):
            raise OSError("SUP05_PREPARATION_RECEIPT_PARTIAL_WRITE")
        os.link(path, output / path.name)
    return receipt
