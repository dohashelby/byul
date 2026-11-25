"""Pinned file-read producers and lifecycle capture with an EXAMPLE Kubernetes transport."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
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


def _model_mount(pod, model_root, mode):
    pod["spec"]["containers"][0]["volumeMounts"] = [
        {"name": "model", "mountPath": model_root.as_posix(), "readOnly": mode != "writable-model"},
        {"name": "other", "mountPath": (model_root / "nested").as_posix() if mode == "nested-shadow" else "/cache"},
    ]
    pod["spec"]["volumes"] = [{"name": "model", "hostPath": {"path": "/example/owned-model", "type": "Directory"}}]


def _prepare_normal_fixture(root, patch, env, target, target_file, reference, model_root):
    from guard.adapters import vllm_approved_file_read_composition as composition
    from guard.collectors.lifecycle import load_prepared
    from guard.executors.kubernetes_sup05_normal_preparation import prepare, read_normal_publication

    patch.setattr(composition, "MODEL_ROOT", model_root.as_posix())
    world = World(None, target, "not-ready")
    engine = target["kubernetes"]["engine"]
    kubectl = ["kubectl", "--context=fixture-context", "--request-timeout=5s"]
    namespace = json.loads(world.runner(kubectl + ["get", "namespace", engine["namespace"], "-o", "json"],
                                      timeout=5, max_stderr_bytes=4096)["stdout_bytes"])
    pod = json.loads(world.runner(kubectl + ["--namespace=" + engine["namespace"], "get", "pod", engine["pod"],
                                           "-o", "json"], timeout=5, max_stderr_bytes=4096)["stdout_bytes"])
    _model_mount(pod, model_root, None)
    environment = root / "environment.json"
    environment.write_bytes(canonical_json(env.model_dump(mode="json")))
    owner = root / "owner-before-import.json"
    owner.write_bytes(canonical_json({"pod": pod, "namespace": namespace}))
    owner_sha256 = sha256_hex(owner.read_bytes())
    output = root / "prepared"
    receipt = prepare(output, REPOSITORY, environment=environment, target=target_file, owner_snapshot=owner,
                      owner_snapshot_sha256=owner_sha256, model_manifest=reference,
                      model_manifest_sha256=sha256_hex(reference.read_bytes()), nonsecret_model_weights=True)
    receipt_path = output / "preparation.json"
    receipt_sha256 = sha256_hex(receipt_path.read_bytes())
    publication = read_normal_publication(receipt_path, env, expected_sha256=receipt_sha256,
                                          owner_snapshot=owner, owner_snapshot_sha256=owner_sha256)
    installed = root / "installed-observer"
    installed.mkdir()
    for name, raw in publication["files"].items():
        (installed / name).write_bytes(raw)
    assert not publication["pre_import_verified"] and not publication["asset_publication_verified"]
    assert load_prepared(installed / "manifest.json")["session"] == receipt["session"] == publication["session"]
    return receipt["normal"], installed, {"path": str(receipt_path), "sha256": receipt_sha256}


def normal_case(root, patch, *, mismatch=False, env_id="ENV-SUP05-NORMAL", mode=None, model_root=None, engine_role=None,
                engine_image=IMAGE, platform=None, release=None, missing_file=False, observed=True, through_preparation=False):
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
    if through_preparation:
        from guard.collectors.lifecycle import load_prepared

        assert release is not None
        normal, assets, preparation_receipt = _prepare_normal_fixture(
            root, patch, env, target, target_file, reference, model_root)
        prepared = load_prepared(normal["prepared"])
    else:
        scope = build_scope(env, target, reference, model_root.as_posix(),
                            nonsecret_model_weights=True, hash_model_bytes=True)
        input_scope = root / "input-scope.json"
        input_scope.write_bytes(canonical_json(scope))
        assets = root / "assets"
        prepared = prepare_assets(assets, REPOSITORY, profile=profile, model_read_scope=input_scope)
        normal = {"target": str(target_file), "prepared": str(assets / "manifest.json"),
                  "reference_manifest": str(reference)}
        preparation_receipt = None
    if release is None:
        raw = _legacy_recording(root, patch, prepared, file, assets)
    else:
        from tests.test_approved_model_file_reads import produce_normal_file_read_fixture

        _, raw = produce_normal_file_read_fixture(root, patch, release, model_path=file,
                                                  prepared_path=assets / "manifest.json", missing_file=missing_file,
                                                  observed=observed)
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
            _model_mount(pod, model_root, mode)
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
    cfg = {boot.NORMAL_FILE_READS_OPTION: True, "normal": normal}
    if preparation_receipt is not None:
        cfg["preparation_receipt"] = preparation_receipt
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
    summary = artifacts["supply_boot_summary.json"].path.read_bytes()
    assert json.loads(summary)["native"][0]["rows"][0]["source_binding"] == source
    assert summary == json.dumps(json.loads(summary), ensure_ascii=False, sort_keys=True).encode("utf-8")
    for artifact in artifacts.values():
        retained = artifact.path.read_bytes()
        assert MODEL_BYTES not in retained and b"approved-model" not in retained


@pytest.mark.parametrize("elapsed_microseconds", [0, 1])
def test_normal_read_requires_final_owned_capture_within_bound_window(external, monkeypatch, elapsed_microseconds):
    case = normal_case(external / "case", monkeypatch)
    start = datetime.fromisoformat(case.values["supply_validity_start"])
    end = datetime.fromisoformat(case.values["supply_validity_end"])
    finished_at = end + timedelta(seconds=1)
    times = iter((start, end + timedelta(microseconds=elapsed_microseconds), finished_at))
    monkeypatch.setattr(boot, "utcnow", lambda: next(times))

    draft = run(case, external / "out")

    assert draft.data_origin == DataOrigin.EXAMPLE and draft.finished_at == finished_at
    assert set(case.world.execs) == {"file", "context", "inventory"}
    assert "pod" in case.world.calls[-1]
    assert not draft.normal_control.attempted and not any(step.attempted for step in draft.challenges)
    native = draft.observations["supply_boot_native_consumption"]
    session, = draft.observations["supply_boot_sessions"].value
    artifacts = {artifact.name: artifact for artifact in draft.artifacts}
    summary = json.loads(artifacts["supply_boot_summary.json"].path.read_bytes())
    if elapsed_microseconds == 0:
        assert draft.status == ExecStatus.COMPLETED and draft.abort_reason is None
        assert native.collected and session["owner_bound_records"]
        assert artifacts["supply_boot_normal_source_000.jsonl"].path.read_bytes() == case.raw
        assert summary["gap"] is None
    else:
        gap = "SUPPLY_BOOT_NORMAL_CAPTURE_WINDOW_EXPIRED"
        assert draft.status == ExecStatus.ABORTED and draft.abort_reason == gap
        assert not native.collected and native.value is None
        assert not session["owner_bound_records"]
        assert set(artifacts) == {"supply_boot_summary.json"}
        assert summary["gap"] == gap and summary["native"] == []
        assert gap in draft.observations["supply_boot_missing"].value
        assert not draft.cleanup_errors


@pytest.mark.parametrize("failure", ["existing-file", "short-write"])
def test_missing_exact_source_artifact_preserves_verified_read(external, monkeypatch, failure):
    case = normal_case(external / "case", monkeypatch)
    out = external / "out"
    name = "supply_boot_normal_source_000.jsonl"
    if failure == "existing-file":
        out.mkdir()
        (out / name).write_bytes(b"Existing private output")
    else:
        original_open = Path.open

        @contextmanager
        def short_write(path, *args, **kwargs):
            with original_open(path, *args, **kwargs) as stream:
                if path.parent.name.startswith(".sup05-source-") and path.name == name:
                    yield SimpleNamespace(write=lambda data: stream.write(data[:1]))
                else:
                    yield stream

        monkeypatch.setattr(Path, "open", short_write)

    draft = run(case, out)
    expected = "SUPPLY_BOOT_NORMAL_SOURCE_ARTIFACT_UNAVAILABLE"
    assert draft.status == ExecStatus.ABORTED
    assert draft.abort_reason == expected
    assert expected in draft.observations["supply_boot_missing"].value
    native, = draft.observations["supply_boot_native_consumption"].value
    row, = native["rows"]
    assert row["matches_approved"] is True
    assert row["source_binding"]["recording_sha256"] == sha256_hex(case.raw)
    assert "recording_artifact" not in row["source_binding"]
    summary, = draft.artifacts
    assert expected in json.loads(summary.path.read_bytes())["missing"]
    assert not draft.observations["supply_boot_violations"].collected
    if failure == "existing-file":
        assert (out / name).read_bytes() == b"Existing private output"
    else:
        assert {path.name for path in out.iterdir()} == {"supply_boot_summary.json"}


def test_short_summary_write_preserves_exact_normal_source(external, monkeypatch):
    case = normal_case(external / "case", monkeypatch)
    out = external / "out"
    short_writes = []
    original_open = Path.open

    @contextmanager
    def short_write(path, mode="r", *args, **kwargs):
        with original_open(path, mode, *args, **kwargs) as stream:
            if path.name == "supply_boot_summary.json" and mode in ("x", "xb"):
                def write(data):
                    short_writes.append(len(data))
                    return stream.write(data[:1])

                yield SimpleNamespace(write=write)
            else:
                yield stream

    monkeypatch.setattr(Path, "open", short_write)
    draft = run(case, out)
    assert any(length > 1 for length in short_writes)
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    code = "SUPPLY_BOOT_SUMMARY_ARTIFACT_UNAVAILABLE"
    assert draft.abort_reason == code and draft.cleanup_errors == [code]
    assert code in draft.observations["supply_boot_missing"].value
    assert not draft.observations["supply_boot_complete"].value
    assert not draft.normal_control.attempted
    assert not draft.observations["supply_boot_record_authenticated"].value
    assert not draft.observations["supply_boot_violations"].collected
    native, = draft.observations["supply_boot_native_consumption"].value
    row, = native["rows"]
    source, = draft.artifacts
    assert source.name == row["source_binding"]["recording_artifact"]
    assert source.path.read_bytes() == case.raw
    assert row["source_binding"]["recording_sha256"] == sha256_hex(case.raw)
    assert {path.name for path in out.iterdir()} == {source.name}


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


@pytest.mark.skipif(
    sys.platform != "linux" or os.uname().machine != "x86_64"
    or sys.version_info[:3] != (3, 12, 3) or sys.flags.optimize != 0,
    reason="the selected GPU observer ABI is Linux x86_64 CPython 3.12.3",
)
@pytest.mark.parametrize(("release", "mode", "missing_file"), [
    ("0.30.0", None, True), ("0.31.0", None, True), ("0.31.0", "unsealed", True),
    ("0.31.0", "late-file-owner", True), ("0.31.0", "cleanup", True),
    ("0.30.0", None, False), ("0.31.0", None, False), ("0.31.0", "late-file-owner", False),
])
def test_only_verified_incomplete_approved_recording_retains_gap_counts(external, monkeypatch, release, mode,
                                                                        missing_file):
    case = normal_case(external / "case", monkeypatch, release=release, mode=mode,
                       missing_file=missing_file, observed=missing_file)
    draft = run(case, external / "out")

    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    assert not draft.observations["supply_boot_native_consumption"].collected
    assert not draft.observations["supply_boot_violations"].collected
    summary, = draft.artifacts
    raw = summary.path.read_bytes()
    result = json.loads(raw)
    if mode is None:
        assert draft.abort_reason == "SUPPLY_BOOT_NORMAL_OWNED_READ_UNAVAILABLE"
        expected_events = 1 if missing_file else 0
        assert result["normal_read_gap_recordings"] == [
            {"recording_sha256": sha256_hex(case.raw), "file_read_event_count": expected_events,
             "open_raise_count": expected_events},
        ]
    else:
        assert "normal_read_gap_recordings" not in result
    assert b"public-example.safetensors" not in raw and b"exception_kind" not in raw


@pytest.mark.skipif(
    sys.platform != "linux" or os.uname().machine != "x86_64"
    or sys.version_info[:3] != (3, 12, 3) or sys.flags.optimize != 0,
    reason="the selected GPU observer ABI is Linux x86_64 CPython 3.12.3",
)
@pytest.mark.parametrize("release", ["0.30.0", "0.31.0"])
def test_retains_verified_no_read_sibling_alongside_the_owned_read(external, monkeypatch, release):
    from tests.lifecycle_fixtures import revised
    from tests.test_approved_model_file_reads import produce_normal_file_read_fixture

    case_root = external / "case"
    case = normal_case(case_root, monkeypatch, release=release)
    _, sibling = produce_normal_file_read_fixture(external / "sibling", monkeypatch, release,
        model_path=case_root / "approved-model/public-example.safetensors",
        prepared_path=case.cfg["normal"]["prepared"], observed=False)
    group = case.world.group
    sibling_pid = max(44, *group.raw_by_pid) + 1

    def sibling_process(rows):
        rows[0]["payload"]["context"]["pid"] = sibling_pid

    sibling = revised(sibling, sibling_process)
    group.raw_by_pid[sibling_pid] = sibling
    draft = run(case, external / "out")

    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    native, = draft.observations["supply_boot_native_consumption"].value
    row, = native["rows"]
    assert row["matches_approved"] and row["source_binding"]["recording_sha256"] == sha256_hex(case.raw)
    artifacts = {artifact.name: artifact.path for artifact in draft.artifacts}
    assert set(artifacts) == {"supply_boot_summary.json", "supply_boot_normal_source_000.jsonl"}
    assert artifacts["supply_boot_normal_source_000.jsonl"].read_bytes() == case.raw
    summary = json.loads(artifacts["supply_boot_summary.json"].read_bytes())
    assert summary["normal_read_gap_recordings"] == [
        {"recording_sha256": sha256_hex(sibling), "file_read_event_count": 0, "open_raise_count": 0},
    ]
    assert not draft.normal_control.attempted
    assert draft.observations["supply_boot_complete"].value is False
    assert draft.observations["supply_boot_record_authenticated"].value is False


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


@pytest.mark.parametrize("option,value", [("max_duration_seconds", 0), ("kubectl_binary", " ")])
def test_normal_file_read_preparation_rejects_unusable_reader_settings(external, monkeypatch, option, value):
    case = normal_case(external / "case", monkeypatch)
    cfg = {**case.cfg, option: value}
    executor = boot.KubernetesSupplyBootExecutor(cfg, runner=case.runner)
    with pytest.raises(ValueError, match="^KUBERNETES_CAPTURE_BINDING_UNAVAILABLE$"):
        executor.normal_file_read_inputs(case.env, case.values)
    with pytest.raises(ValueError, match="^KUBERNETES_CAPTURE_BINDING_UNAVAILABLE$"):
        run(case, external / "out", cfg=cfg)
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


@pytest.mark.skipif(
    sys.platform != "linux" or os.uname().machine != "x86_64"
    or sys.version_info[:3] != (3, 12, 3) or sys.flags.optimize != 0,
    reason="the selected GPU observer ABI is Linux x86_64 CPython 3.12.3",
)
@pytest.mark.parametrize(("release", "mode"), [
    ("0.30.0", None), ("0.31.0", None), ("0.31.0", "unsealed"),
    ("0.31.0", "late-file-owner"), ("0.31.0", "boot-read-cleanup"),
])
def test_sup16_retains_only_verified_closed_normal_read_gap(external, monkeypatch, release, mode):
    from tests.test_supply_boot_record_access import base_values

    case = normal_case(external / "case", monkeypatch, release=release, observed=False,
                       mode=None if mode == "boot-read-cleanup" else mode)
    normal = {key: case.cfg["normal"][key] for key in ("target", "prepared")}
    cfg = {"normal": normal, "challenge": {"target": str(external / "absent-target.json"),
                                          "prepared": str(external / "absent-prepared.json")}}
    prepared_bytes = Path(normal["prepared"]).read_bytes()
    prepared = json.loads(prepared_bytes)
    boot_path = boot.record_path(prepared["session"])

    def runner(argv, **kwargs):
        if "exec" in argv and boot_path in argv:
            pipes_closed = mode != "boot-read-cleanup"
            return {"returncode": 1, "stdout_bytes": b"", "stderr_bytes": b"",
                    "capture": {"complete": pipes_closed, "bounded_at_source": True, "timed_out": False,
                                "pipes_closed": pipes_closed, "truncated": False}}
        return case.runner(argv, **kwargs)

    def no_seal(*args, **kwargs):
        pytest.fail("The selected approved record must be read without requesting a seal")

    monkeypatch.setattr(boot.SupplyBootReader, "seal_session", no_seal)
    pins = {key: case.values[key] for key in ("supply_normal_target_sha256", "supply_normal_prepared_sha256")}
    role_pins = {"normal": sha256_hex(prepared_bytes), "challenge": sha256_hex(b"EXAMPLE missing input")}
    check = load_pack(PACK_DIRECTORY).checks["SUP-16"]
    values = base_values(case.env, check.id, pins)
    executor = boot.KubernetesSupplyBootExecutor(cfg, runner=runner, expected_prepared_sha256_by_role=role_pins)
    draft = executor.run(check, case.env, values, None, external / "out")

    assert draft.status is ExecStatus.ABORTED and draft.data_origin is DataOrigin.EXAMPLE
    assert not draft.normal_control.attempted and not any(step.attempted for step in draft.challenges)
    assert not draft.observations["supply_boot_native_consumption"].collected
    assert not draft.observations["supply_boot_complete"].value
    assert not draft.observations["supply_boot_record_authenticated"].value
    session, missing_challenge = draft.observations["supply_boot_sessions"].value
    assert not session["collected"] and session["record_sha256"] is None
    assert missing_challenge.get("normal_read_gap_recordings") is None
    assert "SUPPLY_BOOT_INPUT_UNAVAILABLE" in missing_challenge["diagnostic_codes"]
    assert "SUPPLY_BOOT_RECORD_UNAVAILABLE" in session["diagnostic_codes"]
    assert "SUPPLY_BOOT_NORMAL_APPROVED_NATIVE_UNVERIFIED" in session["diagnostic_codes"]
    summary, = draft.artifacts
    assert summary.name == "supply_boot_summary.json"
    summary_raw = summary.path.read_bytes()
    assert json.loads(summary_raw)["sessions"] == [session, missing_challenge]
    assert b"public-example.safetensors" not in summary_raw and b"exception_kind" not in summary_raw
    if mode is None:
        assert session["sealed"] and session["native_files_covered"]
        assert session["normal_read_gap_recordings"] == [
            {"recording_sha256": sha256_hex(case.raw), "file_read_event_count": 0, "open_raise_count": 0},
        ]
    else:
        assert "normal_read_gap_recordings" not in session
    if mode == "boot-read-cleanup":
        assert draft.cleanup_errors == ["SUPPLY_BOOT_PARENT_CLEANUP_UNVERIFIED"]
