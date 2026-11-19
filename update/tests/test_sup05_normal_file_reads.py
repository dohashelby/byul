"""Pinned file-read producers and lifecycle capture with an EXAMPLE Kubernetes transport."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from guard.adapters import vllm_lifecycle_sensor as base8, vllm_file_read_sensor as sensor
from guard.collectors.lifecycle import prepare_assets
from guard.collectors.model_read_scope import build_scope
from guard.executors import kubernetes_supply_boot as boot
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex, utcnow
from guard.store import Store
from tests.builders import environment
from tests.test_a1_file_read_sensor import MODEL_BYTES, make_world
from tests.test_kubernetes_lifecycle import World
from tests.test_kubernetes_sampling import inputs as sampling_inputs
from tests.test_lifecycle_capture import IMAGE, REPOSITORY, external
from tests.test_lifecycle_sensor import CONTEXT, FakeOS, SilentThread

PACK_DIRECTORY = REPOSITORY / "update" / "packs" / "PACK-BASELINE"


def normal_case(root, patch, *, mismatch=False, env_id="ENV-SUP05-NORMAL", mode=None, model_root=None, engine_role=None,
                engine_image=IMAGE, platform=None, release=None):
    profile = sensor.PROFILE
    if release is not None:
        from guard.adapters import vllm_lifecycle_catalog as catalog
        from guard.adapters.vllm_approved_file_read_composition import PROFILES

        profile = PROFILES[release]
        approved = catalog.row(profile)
        engine_image = approved["images"][0]
        platform = f"fixture; engine_requested={approved['engine_mode']}"
    root.mkdir()
    model_root = model_root or root / "approved-model"
    model_root.mkdir(exist_ok=True)
    file = model_root / "public-example.safetensors"
    if not file.exists():
        file.write_bytes(MODEL_BYTES)
    patch.setattr(boot, "_MODEL_ROOT", model_root.as_posix())
    reference_sha = ("0" * 64 if mismatch else sha256_hex(MODEL_BYTES))
    files = [{"path": file.name, "bytes": len(MODEL_BYTES), "sha256": reference_sha}]
    inventory_sha = sha256_hex(canonical_json(files))
    manifest = canonical_json({"schema": "guard.model-manifest/1", "scope": "all", "model_id": "example/approved",
                               "files": files, "inventory_sha256": inventory_sha})
    reference = root / "reference.json"
    reference.write_bytes(manifest)
    env = environment(id=env_id, origin=DataOrigin.DECLARED,
                      engine={"name": "vllm", "version": release or "0.31.0", "image_digest": engine_image},
                      runtime={"gpu_count": 1, "gpu_model": "EXAMPLE GPU", "driver": "EXAMPLE driver", "platform": platform},
                      model={"id": "example/approved", "revision": "safe-example", "inventory_sha256": inventory_sha})
    engine = {**sampling_inputs()["kubernetes"]["engine"], **(engine_role or {}),
              "image": engine_image, "image_id": "sha256:" + "c" * 64}
    target = {"schema": "guard-kubernetes-lifecycle-target/1", "lab": True, "run_id": "guard-fixture",
              "environment_ref": env.ref().model_dump(mode="json"),
              "kubernetes": {"context": "fixture-context", "engine": engine}}
    target_file = root / "target.json"
    target_file.write_bytes(canonical_json(target))
    scope = build_scope(env, target, reference, model_root.as_posix(),
                        nonsecret_model_weights=True, hash_model_bytes=True)
    input_scope = root / "input-scope.json"
    input_scope.write_bytes(canonical_json(scope))
    assets = root / "assets"
    prepared = prepare_assets(assets, REPOSITORY, profile=profile, model_read_scope=input_scope)
    if release is None:
        raw = _legacy_recording(root, patch, prepared, file, assets)
    else:
        from tests.test_approved_model_file_reads import produce_normal_file_read_fixture

        _, raw = produce_normal_file_read_fixture(root, patch, release, model_path=file,
                                                  prepared_path=assets / "manifest.json")
    start = json.loads(raw.splitlines()[0])
    pid = start["payload"]["context"]["pid"]
    group = SimpleNamespace(root=root, env=env, prepared=prepared, raw=raw, raw_by_pid={pid: raw})
    world = World(group, target, mode)
    if mode == "unsealed":
        world.docker.group_mode = "unsealed"

    def runner(argv, **options):
        result = world.runner(argv, **options)
        if "pod" in argv and "get" in argv:
            pod = json.loads(result["stdout_bytes"])
            pod["spec"]["containers"][0]["volumeMounts"] = [
                {"name": "model", "mountPath": model_root.as_posix(), "readOnly": mode != "writable-model"},
                {"name": "other", "mountPath": (model_root / "nested").as_posix() if mode == "nested-shadow" else "/cache"},
            ]
            pod["spec"]["volumes"] = [{"name": "model", "hostPath": {"path": "/example/owned-model", "type": "Directory"}}]
            result["stdout_bytes"] = canonical_json(pod)
        if mode == "cleanup" and "exec" in argv:
            result["capture"]["pipes_closed"] = False
        return result

    now = utcnow()
    values = {
        "supply_environment_sha256": env.ref().sha256,
        "supply_normal_target_sha256": sha256_hex(canonical_json(target)),
        "supply_normal_prepared_sha256": sha256_hex(canonical_json(prepared)),
        "supply_normal_reference_manifest_sha256": sha256_hex(manifest),
        "supply_validity_start": (now - timedelta(minutes=5)).isoformat(),
        "supply_validity_end": (now + timedelta(minutes=5)).isoformat(),
    }
    cfg = {boot.NORMAL_FILE_READS_OPTION: True, "normal": {"target": str(target_file),
            "prepared": str(assets / "manifest.json"), "reference_manifest": str(reference)}}
    return SimpleNamespace(env=env, cfg=cfg, values=values, world=world, runner=runner, raw=raw)


def _legacy_recording(root, patch, prepared, file, assets):
    producer = make_world(root, patch, session=prepared["session"], path=file, scope_path=assets / "model-read-scope.json")
    producer.run_loader()
    assert any(row["event"] == "FILE_READ" for row in producer.observer.recorded)

    transport = FakeOS()
    with patch.context() as writer_patch:
        writer_patch.setattr(base8, "PROFILE", sensor.PROFILE)
        writer_patch.setattr(base8, "SCHEMA", sensor.SCHEMA)
        writer_patch.setattr(base8, "__file__", sensor.__file__)
        writer_patch.setattr(base8, "os", transport)
        writer_patch.setattr(base8, "threading", SimpleNamespace(RLock=threading.RLock, Thread=SilentThread, Event=threading.Event))
        writer_patch.setattr(base8, "_open_directory", lambda _: 10)
        writer = base8.Writer(json.loads((assets / "settings.json").read_bytes()),
                              prepared["assets"]["settings.json"]["sha256"], dict(CONTEXT))
        for row in producer.observer.recorded:
            writer.write(row["event"], row["payload"])
        writer.close("SEALED")
    return bytes(transport.data)


def run(case, workdir, *, values=None, cfg=None, check_id="SUP-05"):
    executor = boot.KubernetesSupplyBootExecutor(case.cfg if cfg is None else cfg, runner=case.runner)
    return executor.run(load_pack(PACK_DIRECTORY).checks[check_id], case.env,
                        case.values if values is None else values, None, workdir)


@pytest.mark.parametrize("mismatch", [False, True])
def test_retains_exact_source_read_and_reference_without_challenge(external, monkeypatch, mismatch):
    case = normal_case(external / "case", monkeypatch, mismatch=mismatch)
    executor = boot.KubernetesSupplyBootExecutor(case.cfg, runner=case.runner)
    target, prepared, target_hash, prepared_hash = executor.normal_file_read_inputs(case.env, case.values)
    assert target_hash == case.values["supply_normal_target_sha256"] == sha256_hex(canonical_json(target))
    assert prepared_hash == case.values["supply_normal_prepared_sha256"] == sha256_hex(canonical_json(prepared))
    assert not case.world.calls
    draft = run(case, external / "out")
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert not draft.normal_control.attempted and not any(step.attempted for step in draft.challenges)
    native, = draft.observations["supply_boot_native_consumption"].value
    row, = native["rows"]
    assert row["full_read"] and row["read_bytes"] == len(MODEL_BYTES)
    assert row["matches_approved"] is not mismatch and row["mismatch"] is mismatch
    source = row["source_binding"]
    assert source["recording_sha256"] == sha256_hex(case.raw) and source["recording_owner_binding_verified"]
    assert source["read_return_sha256"] == sha256_hex(MODEL_BYTES)
    assert source["root_event"][1] < source["source_events"][1][1]
    assert not draft.observations["supply_boot_violations"].collected
    assert draft.observations["supply_boot_complete"].value is False
    assert draft.observations["supply_boot_record_authenticated"].value is False
    assert set(case.world.execs) == {"file", "context", "inventory"}
    artifacts = {artifact.name: artifact for artifact in draft.artifacts}
    assert set(artifacts) == {"supply_boot_summary.json", "supply_boot_normal_source_000.jsonl"}
    original = artifacts[source["recording_artifact"]].path.read_bytes()
    assert original == case.raw
    stored = Store(external / "store").put_artifact(original)
    assert stored.sha256 == source["recording_sha256"] and stored.bytes == len(case.raw)
    summary = artifacts["supply_boot_summary.json"].path.read_text()
    assert json.loads(summary)["native"][0]["rows"][0]["source_binding"] == source
    for artifact in artifacts.values():
        retained = artifact.path.read_bytes()
        assert MODEL_BYTES not in retained and b"approved-model" not in retained


@pytest.mark.parametrize("failure", ["noncanonical", "existing-file"])
def test_missing_exact_source_artifact_preserves_verified_read(external, monkeypatch, failure):
    case = normal_case(external / "case", monkeypatch)
    out = external / "out"
    name = "supply_boot_normal_source_000.jsonl"
    if failure == "noncanonical":
        # Only the terminal line has extra whitespace. Its contents and hash-chain
        # predecessor still validate, but the canonical serialization differs.
        original = case.raw[:-1] + b" \n"
        case.world.group.raw_by_pid[CONTEXT["pid"]] = original
        expected = "SUPPLY_BOOT_NORMAL_SOURCE_BYTES_UNVERIFIED"
    else:
        out.mkdir()
        (out / name).write_bytes(b"Existing private output")
        original = case.raw
        expected = "SUPPLY_BOOT_NORMAL_SOURCE_ARTIFACT_UNAVAILABLE"

    draft = run(case, out)
    assert draft.status == ExecStatus.ABORTED
    assert draft.abort_reason == expected
    assert expected in draft.observations["supply_boot_missing"].value
    native, = draft.observations["supply_boot_native_consumption"].value
    row, = native["rows"]
    assert row["matches_approved"] is True
    assert row["source_binding"]["recording_sha256"] == sha256_hex(original)
    assert "recording_artifact" not in row["source_binding"]
    summary, = draft.artifacts
    assert expected in json.loads(summary.path.read_bytes())["missing"]
    assert not draft.observations["supply_boot_violations"].collected
    if failure == "existing-file":
        assert (out / name).read_bytes() == b"Existing private output"
    else:
        assert not (out / name).exists()


@pytest.mark.parametrize("mode", ["unsealed", "late-file-owner", "writable-model", "nested-shadow", "cleanup"])
def test_scope_and_transport_gaps_cannot_make_a_success(external, monkeypatch, mode):
    case = normal_case(external / "case", monkeypatch, mode=mode)
    draft = run(case, external / "out")
    assert draft.status == ExecStatus.ABORTED and draft.abort_reason
    assert not draft.observations["supply_boot_violations"].collected
    if mode == "cleanup":
        assert "SUPPLY_BOOT_PARENT_CLEANUP_UNVERIFIED" in draft.cleanup_errors
    else:
        assert not draft.observations["supply_boot_native_consumption"].collected
    if mode in {"writable-model", "nested-shadow"}:
        assert not case.world.execs


@pytest.mark.parametrize("changed", ["false-marker", "other-check", "wrong-reference", "wrong-prepared", "wrong-target",
                                    "reference-scope-changed"])
def test_invalid_scoped_inputs_fail_before_transport(external, monkeypatch, changed):
    case = normal_case(external / "case", monkeypatch)
    cfg, values, check_id = deepcopy(case.cfg), dict(case.values), "SUP-05"
    if changed == "false-marker":
        cfg[boot.NORMAL_FILE_READS_OPTION] = False
    elif changed == "other-check":
        check_id = "SUP-15"
    elif changed == "reference-scope-changed":
        reference = Path(cfg["normal"]["reference_manifest"])
        updated = reference.read_bytes() + b"\n"
        reference.write_bytes(updated)
        values["supply_normal_reference_manifest_sha256"] = sha256_hex(updated)
    else:
        key = {"wrong-reference": "supply_normal_reference_manifest_sha256", "wrong-prepared": "supply_normal_prepared_sha256",
               "wrong-target": "supply_normal_target_sha256"}[changed]
        values[key] = "0" * 64
    with pytest.raises(ValueError):
        run(case, external / "out", values=values, cfg=cfg, check_id=check_id)
    assert not case.world.calls


def test_other_release_prepared_cannot_use_current_target(external, monkeypatch):
    from guard.adapters import vllm_lifecycle_catalog as catalog
    from guard.adapters.vllm_approved_file_read_composition import PROFILES

    matching_profile = PROFILES["0.31.0"]
    row = catalog.row(matching_profile)
    case = normal_case(external / "case", monkeypatch, engine_image=row["images"][0],
                       platform=f"fixture; engine_requested={row['engine_mode']}")
    executor = boot.KubernetesSupplyBootExecutor(case.cfg, runner=case.runner)
    scope = external / "case" / "input-scope.json"

    for profile in (matching_profile, PROFILES["0.30.0"]):
        assets = external / profile.rsplit("/", 1)[1]
        prepared = prepare_assets(assets, REPOSITORY, profile=profile, model_read_scope=scope)
        case.cfg["normal"]["prepared"] = str(assets / "manifest.json")
        case.values["supply_normal_prepared_sha256"] = sha256_hex(canonical_json(prepared))
        if profile == matching_profile:
            assert executor.normal_file_read_inputs(case.env, case.values)[1] == prepared
        else:
            with pytest.raises(ValueError, match="SUPPLY_BOOT_NORMAL_BINDING_INVALID"):
                executor.normal_file_read_inputs(case.env, case.values)
    assert not case.world.calls
