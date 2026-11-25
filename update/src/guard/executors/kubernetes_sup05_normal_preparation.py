"""Prepare one owned normal file-read input from saved observations."""

import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory

from guard.adapters.engine_provider import get_provider
from guard.adapters.vllm_lifecycle_catalog import asset_names
from guard.adapters.vllm_file_read_sensor import MAX_SCOPE_BYTES, dependency, fingerprint, validate_scope
from guard.collectors.lifecycle import load_prepared, prepare_assets
from guard.collectors.model_read_scope import build_scope, deploy_scope
from guard.executors.kubernetes_supply_boot import _approved_reference, _model_mount, _model_mount_sha256
from guard.models import Environment, canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_binding import project_owned_snapshot, validate_capture_target
from guard.update_validation import _plain_path, reserve_output


_PREPARATION_ONLY = {"approval_verified", "live_owner_verified", "pre_import_verified",
                     "asset_publication_verified", "engine_started", "model_files_read", "native_consumption_observed"}
_RECEIPT_DIGESTS = {"model_mount_projection_sha256", "scope_input_sha256", "deployed_scope_sha256",
                    "target_sha256", "prepared_sha256", "prepared_file_sha256"}
_RECEIPT_FIELDS = {"schema", "environment", "environment_ref", "input_data_origin", "producer_profile",
                   "session", "normal", "input_sha256"} | _PREPARATION_ONLY | _RECEIPT_DIGESTS


def read_normal_preparation(path, env, *, expected_sha256):
    """Read an independently pinned local preparation receipt for configuration assembly."""
    try:
        if not fingerprint(expected_sha256):
            raise ValueError
        path = _plain_path(Path(path))
        receipt = strict_json(dependency(path, expected_sha256), limit=MAX_SCOPE_BYTES)
        if (type(receipt) is not dict or receipt.keys() != _RECEIPT_FIELDS
                or receipt["schema"] != "guard-sup05-normal-preparation/1"
                or any(receipt[key] is not False for key in _PREPARATION_ONLY)
                or any(not fingerprint(receipt[key]) for key in _RECEIPT_DIGESTS)
                or not env.hash_ok() or receipt["environment_ref"] != env.ref().model_dump(mode="json")
                or receipt["input_data_origin"] != env.data_origin.value):
            raise ValueError

        directory = path.parent
        normal = {"target": str(directory / "target.json"), "prepared": str(directory / "lifecycle" / "manifest.json"),
                  "reference_manifest": str(directory / "reference-manifest.json")}
        environment_path = directory / "environment.json"
        hashes = receipt["input_sha256"]
        if (receipt["normal"] != normal or receipt["environment"] != str(environment_path)
                or type(hashes) is not dict or hashes.keys() != {"environment", "target", "owner_snapshot", "reference_manifest"}
                or any(not fingerprint(value) for value in hashes.values())):
            raise ValueError

        saved_env = Environment.model_validate(strict_json(dependency(environment_path, hashes["environment"]),
                                                          limit=MAX_SCOPE_BYTES))
        if not saved_env.hash_ok() or saved_env.ref() != env.ref():
            raise ValueError
        target = strict_json(dependency(normal["target"], hashes["target"]), limit=65536)
        validate_capture_target(target, env)
        source = get_provider(env.engine.name).evidence_contract("approved_file_read_preparation", env)
        if type(source) is not dict or source.get("profile") != receipt["producer_profile"]:
            raise ValueError
        manifest = _plain_path(Path(normal["prepared"]))
        if any(not stat.S_ISREG((manifest.parent / name).lstat().st_mode) for name in asset_names(source["profile"])):
            raise ValueError
        prepared = load_prepared(manifest, expected_sha256=receipt["prepared_file_sha256"])
        if (sha256_hex(canonical_json(target)) != receipt["target_sha256"]
                or sha256_hex(canonical_json(prepared)) != receipt["prepared_sha256"]
                or prepared["profile"] != receipt["producer_profile"] or prepared["session"] != receipt["session"]
                or prepared["assets"]["model-read-scope.json"]["sha256"] != receipt["deployed_scope_sha256"]):
            raise ValueError
        _approved_reference(normal["reference_manifest"], hashes["reference_manifest"], env, target, prepared)
        scope_raw = dependency(directory / "model-read-scope.json", receipt["scope_input_sha256"])
        scope = validate_scope(strict_json(scope_raw, limit=MAX_SCOPE_BYTES), session=None)
        if sha256_hex(deploy_scope(scope, scope_raw, prepared["session"])) != receipt["deployed_scope_sha256"]:
            raise ValueError
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        raise ValueError("SUP05_PREPARATION_RECEIPT_INVALID") from None

    return {"normal": normal, "target_sha256": receipt["target_sha256"], "target_file_sha256": hashes["target"],
            "model_mount_projection_sha256": receipt["model_mount_projection_sha256"],
            "expected_normal_file_sha256": {"prepared": receipt["prepared_file_sha256"],
                                            "reference_manifest": hashes["reference_manifest"]}}


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
    return _model_mount_sha256(mount, volume)


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
