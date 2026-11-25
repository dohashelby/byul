"""Offline source-pinned inputs for the registered SUP-05 normal reader."""

from copy import deepcopy
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
from guard.executors.kubernetes_sup05_normal_preparation import prepare, read_normal_preparation, read_normal_publication
from guard.models import ExecStatus, canonical_json, load_pack, sha256_hex, utcnow
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
    receipt_path = case.output / "preparation.json"
    assert read_normal_preparation(receipt_path, case.env, expected_sha256=sha256_hex(receipt_path.read_bytes())) == {
        "normal": normal, "target_sha256": receipt["target_sha256"],
        "target_file_sha256": sha256_hex(args["target"].read_bytes()),
        "model_mount_projection_sha256": receipt["model_mount_projection_sha256"],
        "expected_normal_file_sha256": {"prepared": receipt["prepared_file_sha256"],
                                        "reference_manifest": args["model_manifest_sha256"]}}
    assert all(receipt[key] is False for key in ("approval_verified", "live_owner_verified", "pre_import_verified",
        "asset_publication_verified", "engine_started", "model_files_read", "native_consumption_observed"))
    assert all(b"/private-model-location" not in path.read_bytes() for path in case.output.rglob("*") if path.is_file())
    settings = {"normal_file_reads_only": True, "normal": normal, "preparation_receipt": {
        "path": str(receipt_path), "sha256": sha256_hex(receipt_path.read_bytes())}}
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


@pytest.mark.parametrize("release", ("0.30.0", "0.31.0"))
def test_publication_reads_the_exact_assets_for_the_current_owned_generation(external, release):
    case = _case(external, release)
    receipt = prepare(case.output, REPOSITORY, **case.args)
    path = case.output / "preparation.json"
    preparation_sha256 = sha256_hex(path.read_bytes())
    snapshot = deepcopy(case.snapshot)
    snapshot["pod"]["metadata"]["resourceVersion"] = "2"
    owner_path = external / "current-owner.json"
    owner_sha256 = _write(owner_path, snapshot)
    assert owner_sha256 != receipt["input_sha256"]["owner_snapshot"]

    result = read_normal_publication(path, case.env, expected_sha256=preparation_sha256,
                                     owner_snapshot=owner_path, owner_snapshot_sha256=owner_sha256)
    prepared_path = Path(receipt["normal"]["prepared"])
    prepared = load_prepared(prepared_path)
    assert result["files"] == {name: (prepared_path.parent / name).read_bytes()
                               for name in {"manifest.json", *prepared["assets"]}}
    assert result["prepared_file_sha256"] == sha256_hex(result["files"]["manifest.json"])
    for name, expected in prepared["assets"].items():
        raw = result["files"][name]
        assert (len(raw), sha256_hex(raw)) == (expected["bytes"], expected["sha256"])
    target = json.loads(case.args["target"].read_bytes())
    assert result["binding"] == {**target["kubernetes"], "run_id": target["run_id"]}
    assert result["environment_ref"] == case.env.ref().model_dump(mode="json")
    assert result["profile"] == case.profile and result["session"] == prepared["session"]
    assert result["deployed_scope_sha256"] == prepared["assets"]["model-read-scope.json"]["sha256"]
    assert result["owner_snapshot_sha256"] == owner_sha256 and result["preparation_sha256"] == preparation_sha256
    assert result["input_data_origin"] == "EXAMPLE"
    assert result["pre_import_verified"] is result["asset_publication_verified"] is False
    assert all(b"/private-model-location" not in source for source in result["files"].values())


@pytest.mark.parametrize("damage", ("owner-pin", "new-container", "writable-model", "changed-mount", "ready",
                                    "missing-asset", "replaced-asset", "replaced-manifest", "v2"))
def test_publication_rejects_unusable_current_owner_or_asset_bytes(external, monkeypatch, damage):
    from guard.executors import kubernetes_sup05_normal_preparation as preparation

    case = _case(external, "0.31.0")
    receipt = prepare(case.output, REPOSITORY, **case.args)
    path = case.output / "preparation.json"
    prepared_path = Path(receipt["normal"]["prepared"])
    owner = deepcopy(case.snapshot)
    pod = owner["pod"]
    if damage == "new-container":
        pod["status"]["containerStatuses"][0]["containerID"] = "containerd://" + "4" * 64
    elif damage == "writable-model":
        pod["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] = False
    elif damage == "changed-mount":
        pod["spec"]["volumes"][0]["hostPath"]["path"] = "/different-private-model-location"
    elif damage == "ready":
        pod["status"]["containerStatuses"][0]["ready"] = True
    owner_path = external / "current-owner.json"
    owner_sha256 = _write(owner_path, owner)
    if damage == "owner-pin":
        owner_sha256 = "8" * 64
    if damage in {"missing-asset", "replaced-asset", "replaced-manifest"}:
        verify = preparation._read_normal_preparation

        def verify_then_change(*args, **kwargs):
            result = verify(*args, **kwargs)
            asset = prepared_path if damage == "replaced-manifest" else prepared_path.with_name("sitecustomize.py")
            if damage == "missing-asset":
                asset.unlink()
            else:
                asset.write_bytes(asset.read_bytes() + b"\n")
            return result

        monkeypatch.setattr(preparation, "_read_normal_preparation", verify_then_change)
    env = case.env
    reason = "SUP05_NORMAL_PUBLICATION_INVALID"
    if damage == "v2":
        runtime = env.runtime.model_copy(update={"platform": "Linux x86_64; engine_requested=V2"})
        env = env.model_copy(update={"runtime": runtime}).seal()
        reason = "SUP05_PREPARATION_RECEIPT_INVALID"
    with pytest.raises(ValueError, match=f"^{reason}$"):
        read_normal_publication(path, env, expected_sha256=sha256_hex(path.read_bytes()),
                                owner_snapshot=owner_path, owner_snapshot_sha256=owner_sha256)


@pytest.mark.parametrize("release", ("0.30.0", "0.31.0"))
@pytest.mark.parametrize("asset", ("settings.json", "sitecustomize.py"))
def test_preflight_requires_the_prepared_session_and_bootstrap(external, monkeypatch, release, asset):
    from guard.executors.kubernetes_supply_boot import KubernetesSupplyBootExecutor

    case = _case(external, release)
    receipt = prepare(case.output, REPOSITORY, **case.args)
    receipt_path = case.output / "preparation.json"
    manifest_path = Path(receipt["normal"]["prepared"])
    manifest = load_prepared(manifest_path)
    settings = {"normal_file_reads_only": True, "normal": receipt["normal"], "preparation_receipt": {
        "path": str(receipt_path), "sha256": sha256_hex(receipt_path.read_bytes())}}
    check = load_pack(REPOSITORY / "update" / "packs" / "PACK-BASELINE").checks["SUP-05"]
    _, values = run_binding.effective_values(check, settings, case.env, {}, side="baseline",
                                               attempt_id=None, issued_at=utcnow())

    source_path = manifest_path.with_name(asset)
    if asset == "settings.json":
        # A valid ordinary settings document for a separate prepared session.
        other = prepare(case.output.with_name("other-prepared"), REPOSITORY, **case.args)
        assert other["session"] != receipt["session"]
        raw = Path(other["normal"]["prepared"]).with_name(asset).read_bytes()
        reason = "APPROVED_FILE_READ_SETTINGS_MISMATCH"
    else:
        raw = b"# EXAMPLE without a lifecycle bootstrap\n"
        reason = "APPROVED_FILE_READ_BOOTSTRAP_MISMATCH"
    source_path.write_bytes(raw)
    manifest["assets"][asset] = {"sha256": sha256_hex(raw), "bytes": len(raw)}
    receipt["prepared_file_sha256"] = _write(manifest_path, manifest)
    receipt["prepared_sha256"] = sha256_hex(canonical_json(manifest))
    settings["preparation_receipt"]["sha256"] = _write(receipt_path, receipt)
    values["supply_normal_prepared_sha256"] = receipt["prepared_sha256"]

    with pytest.raises(ValueError, match=f"^{reason}$"):
        load_prepared(manifest_path, expected_sha256=receipt["prepared_file_sha256"])

    def unavailable(*args, **kwargs):
        pytest.fail("Unusable observer assets must be rejected before reader construction")

    executor = KubernetesSupplyBootExecutor(settings, runner=unavailable)
    monkeypatch.setattr(executor, "_normal_reader", unavailable)
    with pytest.raises(ValueError, match="^SUP05_PREPARATION_RECEIPT_INVALID$"):
        executor.normal_file_read_inputs(case.env, values)


@pytest.mark.parametrize("changed", (False, True))
def test_receipt_requires_the_prepared_mount_before_native_record_reads(external, monkeypatch, changed):
    from guard.executors import kubernetes_supply_boot as boot

    case = _case(external, "0.30.0")
    receipt = prepare(case.output, REPOSITORY, **case.args)
    receipt_path = case.output / "preparation.json"
    pin = sha256_hex(receipt_path.read_bytes())
    settings = {"normal_file_reads_only": True, "normal": receipt["normal"], "preparation_receipt": {
        "path": str(receipt_path), "sha256": pin}}
    check = load_pack(REPOSITORY / "update" / "packs" / "PACK-BASELINE").checks["SUP-05"]
    _, values = run_binding.effective_values(check, settings, case.env, {}, side="baseline",
                                               attempt_id=None, issued_at=utcnow())

    current = deepcopy(case.snapshot)
    pod = current["pod"]
    pod["status"]["containerStatuses"][0]["ready"] = True
    pod["status"]["conditions"][0]["status"] = "True"
    if changed:
        pod["spec"]["volumes"][0]["hostPath"]["path"] = "/different-private-model-location"

    def runner(argv, **kwargs):
        argv = argv[3:]
        ns = current["namespace"]
        if argv == ["get", "namespace", ns["metadata"]["name"], "-o", "json"]:
            document = ns
        elif argv == ["--namespace=" + ns["metadata"]["name"], "get", "pod", pod["metadata"]["name"], "-o", "json"]:
            document = pod
        else:
            pytest.fail("Only the existing owner snapshot may be read")
        return {"returncode": 0, "stdout_bytes": canonical_json(document),
                "capture": {"complete": True, "bounded_at_source": True, "timed_out": False, "truncated": False}}

    reads = []

    def unavailable_native(reader, target, *, prepared, **kwargs):
        reads.append(prepared["session"])
        raise ValueError("FIXTURE_NATIVE_RECORD_UNAVAILABLE")

    monkeypatch.setattr(boot, "capture_session", unavailable_native)
    executor = boot.KubernetesSupplyBootExecutor(settings, runner=runner)
    draft = executor.run(check, case.env, values, None, external / "work")
    assert reads == ([] if changed else [receipt["session"]])
    assert draft.status == ExecStatus.ABORTED
    assert draft.abort_reason == ("SUPPLY_BOOT_NORMAL_PREPARED_MOUNT_MISMATCH" if changed
                                  else "SUPPLY_BOOT_NORMAL_SEALED_CAPTURE_UNAVAILABLE")
    assert not draft.observations["supply_boot_native_consumption"].collected
    artifact, = draft.artifacts
    raw = artifact.path.read_bytes()
    summary = json.loads(raw)
    assert summary["preparation_receipt_sha256"] == pin
    assert summary["prepared_model_mount_matches_before_capture"] is (not changed)
    assert b"/private-model-location" not in raw and b"/different-private-model-location" not in raw


@pytest.mark.parametrize("key", ("target", "prepared", "reference_manifest"))
@pytest.mark.parametrize("after_receipt_read", (False, True))
def test_supply_executor_revalidates_receipt_pinned_files_before_transport(external, monkeypatch, key, after_receipt_read):
    from guard.executors import kubernetes_sup05_normal_preparation as preparation
    from guard.executors.kubernetes_supply_boot import KubernetesSupplyBootExecutor

    case = _case(external, "0.30.0")
    receipt = prepare(case.output, REPOSITORY, **case.args)
    receipt_path = case.output / "preparation.json"
    settings = {"normal_file_reads_only": True, "normal": receipt["normal"], "preparation_receipt": {
        "path": str(receipt_path), "sha256": sha256_hex(receipt_path.read_bytes())}}
    check = load_pack(REPOSITORY / "update" / "packs" / "PACK-BASELINE").checks["SUP-05"]
    _, values = run_binding.effective_values(check, settings, case.env, {}, side="baseline",
                                               attempt_id=None, issued_at=utcnow())
    file_path = Path(receipt["normal"][key])

    def rewrite():
        file_path.write_bytes(file_path.read_bytes() + b"\n")

    if after_receipt_read:
        def verified_then_rewritten(*args, **kwargs):
            verified = read_normal_preparation(*args, **kwargs)
            rewrite()
            return verified

        monkeypatch.setattr(preparation, "read_normal_preparation", verified_then_rewritten)
    else:
        rewrite()

    def no_transport(*args, **kwargs):
        pytest.fail("A stale pinned input must be rejected before constructing the reader")

    monkeypatch.setattr(KubernetesSupplyBootExecutor, "_normal_reader", no_transport)
    executor = KubernetesSupplyBootExecutor(settings, runner=no_transport)
    reason = "FILE_READ_DEPENDENCY_UNAVAILABLE" if after_receipt_read else "SUP05_PREPARATION_RECEIPT_INVALID"
    with pytest.raises(ValueError, match=f"^{reason}$"):
        executor.run(check, case.env, values, None, external / "work")


@pytest.mark.parametrize("damage", ("environment", "receipt-pin", "promoted", "missing-flag",
                                    "target-bytes", "reference-bytes", "prepared-bytes", "missing-asset"))
def test_read_normal_preparation_rejects_unbound_receipt_or_input(external, damage):
    case = _case(external, "0.30.0")
    receipt = prepare(case.output, REPOSITORY, **case.args)
    receipt_path = case.output / "preparation.json"
    env = case.env
    if damage == "environment":
        env = env.model_copy(update={"id": "ENV-EXAMPLE-OTHER"}).seal()
    elif damage in {"promoted", "missing-flag"}:
        if damage == "promoted":
            receipt["native_consumption_observed"] = True
        else:
            del receipt["live_owner_verified"]
        receipt_path.write_bytes(canonical_json(receipt))
    elif damage in {"target-bytes", "reference-bytes", "prepared-bytes"}:
        key = {"target-bytes": "target", "reference-bytes": "reference_manifest", "prepared-bytes": "prepared"}[damage]
        path = Path(receipt["normal"][key])
        path.write_bytes(path.read_bytes() + b"\n")
    elif damage == "missing-asset":
        (case.output / "lifecycle" / "model-read-scope.json").unlink()
    pin = "b" * 64 if damage == "receipt-pin" else sha256_hex(receipt_path.read_bytes())
    with pytest.raises(ValueError, match="^SUP05_PREPARATION_RECEIPT_INVALID$"):
        read_normal_preparation(receipt_path, env, expected_sha256=pin)


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
