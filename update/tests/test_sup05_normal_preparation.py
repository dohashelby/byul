"""Offline source-pinned inputs for the registered SUP-05 normal reader."""

from pathlib import Path
from types import SimpleNamespace
import json
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard.adapters import vllm_lifecycle_catalog as catalog
from guard.adapters.vllm_approved_file_read_composition import PROFILES
from guard.cli import app
from guard.collectors.lifecycle import load_prepared
from guard.executors import registry, run_binding
from guard.executors.kubernetes_sup05_normal_preparation import prepare
from guard.models import canonical_json, load_pack, sha256_hex, utcnow
from tests.builders import environment
from tests.test_kubernetes_sampling import inputs as sampling_inputs
from tests.test_lifecycle_capture import REPOSITORY, external


def _write(path, value):
    raw = canonical_json(value) + b"\n"
    path.write_bytes(raw)
    return sha256_hex(raw)


def _case(external, release, *, engine_mode="V1", model_filename="model.safetensors"):
    profile = PROFILES[release]
    image = catalog.row(profile)["images"][0]
    rows = [{"path": model_filename, "sha256": "a" * 64, "bytes": 73}]
    env = environment(engine={"name": "vllm", "version": release, "image_digest": image},
        runtime={"gpu_count": 1, "platform": f"Linux x86_64; engine_requested={engine_mode}"},
        model={"id": "EXAMPLE/normal-model", "revision": "EXAMPLE-pinned", "inventory_sha256": sha256_hex(canonical_json(rows))})
    engine = {**sampling_inputs()["kubernetes"]["engine"], "image": image}
    target = {"schema": "guard-kubernetes-lifecycle-target/1", "run_id": "sup05-example",
              "environment_ref": env.ref().model_dump(mode="json"), "lab": True,
              "kubernetes": {"context": "EXAMPLE-context", "engine": engine}}
    snapshot = {
        "namespace": {"kind": "Namespace", "metadata": {"name": engine["namespace"], "uid": engine["namespace_uid"]}},
        "pod": {
            "kind": "Pod",
            "metadata": {"name": engine["pod"], "namespace": engine["namespace"], "uid": engine["pod_uid"],
                         "labels": {"guard.run": target["run_id"]}},
            "spec": {"nodeName": engine["node"], "containers": [{"name": engine["container"], "image": image,
                    "volumeMounts": [{"mountPath": "/models", "name": "weights", "readOnly": True}]}],
                     "volumes": [{"name": "weights", "hostPath": {"path": "/private-model-location", "type": "Directory"}}]},
            "status": {"phase": "Running", "podIP": engine["pod_ip"], "conditions": [{"type": "Ready", "status": "False"}],
                "containerStatuses": [{"name": engine["container"], "containerID": engine["container_id"],
                    "imageID": engine["image_id"], "restartCount": engine["restart_count"], "ready": False,
                    "state": {"running": {"startedAt": engine["started_at"]}}}]},
        },
    }
    args = {"environment": external / "input-env.json", "target": external / "input-target.json",
            "owner_snapshot": external / "input-owner.json", "model_manifest": external / "input-manifest.json",
            "nonsecret_model_weights": True}
    _write(args["environment"], env.model_dump(mode="json"))
    _write(args["target"], target)
    args["owner_snapshot_sha256"] = _write(args["owner_snapshot"], snapshot)
    args["model_manifest_sha256"] = _write(args["model_manifest"], {
        "schema": "guard.model-manifest/1", "scope": "all", "model_id": env.model.id,
        "files": rows, "inventory_sha256": env.model.inventory_sha256})
    return SimpleNamespace(env=env, profile=profile, args=args, snapshot=snapshot, output=external / "prepared")


@pytest.mark.parametrize("release", ("0.30.0", "0.31.0"))
def test_cli_prepares_real_registry_inputs_for_each_pinned_release(external, monkeypatch, release):
    case = _case(external, release)

    def unavailable(*args, **kwargs):
        raise AssertionError("Offline preparation cannot invoke subprocesses or connect")

    monkeypatch.setattr(subprocess, "Popen", unavailable)
    monkeypatch.setattr(socket, "socket", unavailable)
    args = case.args
    result = CliRunner().invoke(app, ["prepare-sup05-normal", "--env", str(args["environment"]),
        "--target", str(args["target"]), "--owner-snapshot", str(args["owner_snapshot"]),
        "--owner-snapshot-sha256", args["owner_snapshot_sha256"], "--model-manifest", str(args["model_manifest"]),
        "--model-manifest-sha256", args["model_manifest_sha256"], "--output", str(case.output), "--nonsecret-model-weights"])
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    assert receipt == json.loads((case.output / "preparation.json").read_bytes())
    assert receipt["producer_profile"] == case.profile
    assert receipt["environment_ref"] == case.env.ref().model_dump(mode="json")
    assert receipt["input_data_origin"] == case.env.data_origin.value
    assert Path(receipt["environment"]).read_bytes() == args["environment"].read_bytes()
    normal = receipt["normal"]
    assert Path(normal["target"]).read_bytes() == args["target"].read_bytes()
    assert Path(normal["reference_manifest"]).read_bytes() == args["model_manifest"].read_bytes()
    prepared = load_prepared(normal["prepared"])
    assert prepared["session"] == receipt["session"]
    assert prepared["assets"]["model-read-scope.json"]["sha256"] == receipt["deployed_scope_sha256"]
    assert sha256_hex(Path(normal["prepared"]).read_bytes()) == receipt["prepared_file_sha256"]
    assert all(receipt[key] is False for key in ("approval_verified", "live_owner_verified", "pre_import_verified",
        "asset_publication_verified", "engine_started", "model_files_read", "native_consumption_observed"))
    assert all(b"/private-model-location" not in path.read_bytes() for path in case.output.rglob("*") if path.is_file())
    settings = {"normal_file_reads_only": True, "normal": normal}
    factory = registry.build_executor("supply_boot", "kubernetes", {"supply_boot": settings},
                                      engine=case.env.engine.name, version=case.env.engine.version)
    check = load_pack(REPOSITORY / "update" / "packs" / "PACK-BASELINE").checks["SUP-05"]
    _, values = run_binding.effective_values(check, settings, case.env, {}, side="baseline",
        attempt_id=None, issued_at=utcnow(), expected_target_sha256=receipt["target_sha256"],
        expected_normal_file_sha256={"prepared": receipt["prepared_file_sha256"],
            "reference_manifest": args["model_manifest_sha256"]})
    factory.validate_policy(check, values)
    _, reloaded, target_hash, prepared_hash = factory.normal_file_read_inputs(case.env, values)
    assert (reloaded, target_hash, prepared_hash) == (prepared, receipt["target_sha256"], receipt["prepared_sha256"])


@pytest.mark.parametrize("damage", ("owner-pin", "reference-pin", "wrong-pod", "already-ready", "writable-model", "v2-mode"))
def test_wrong_input_declines_before_publishing_a_preparation(external, damage):
    case = _case(external, "0.30.0", engine_mode="V2" if damage == "v2-mode" else "V1")
    args = case.args
    if damage == "owner-pin":
        args["owner_snapshot_sha256"] = "b" * 64
    if damage == "reference-pin":
        args["model_manifest_sha256"] = "b" * 64
    pod = case.snapshot["pod"]
    if damage == "wrong-pod":
        pod["metadata"]["uid"] = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    if damage == "already-ready":
        pod["status"]["containerStatuses"][0]["ready"] = True
    if damage == "writable-model":
        pod["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] = False
    if damage not in {"owner-pin", "reference-pin", "v2-mode"}:
        args["owner_snapshot_sha256"] = _write(args["owner_snapshot"], case.snapshot)
    with pytest.raises(ValueError):
        prepare(case.output, REPOSITORY, **args)
    assert not case.output.exists()


def test_an_existing_output_is_retained(external):
    case = _case(external, "0.31.0")
    case.output.mkdir()
    prior = case.output / "existing.json"
    prior.write_bytes(b"already preserved")
    with pytest.raises(ValueError, match="existing evidence is never overwritten"):
        prepare(case.output, REPOSITORY, **case.args)
    assert list(case.output.iterdir()) == [prior] and prior.read_bytes() == b"already preserved"


@pytest.mark.parametrize("malformed_row", (False, True))
def test_unusable_approved_reference_declines_before_creating_output(external, malformed_row):
    case = _case(external, "0.31.0", model_filename="./model.safetensors")
    path = case.args["model_manifest"]
    reason = "MODEL_READ_SCOPE_APPROVED_FILE_INVALID"
    if malformed_row:
        manifest = json.loads(path.read_bytes())
        manifest["files"] = ["unexpected row type"]
        case.args["model_manifest_sha256"] = _write(path, manifest)
        reason = "MODEL_READ_SCOPE_APPROVED_MANIFEST_INVALID"
    with pytest.raises(ValueError, match=reason):
        prepare(case.output, REPOSITORY, **case.args)
    assert not case.output.exists()
