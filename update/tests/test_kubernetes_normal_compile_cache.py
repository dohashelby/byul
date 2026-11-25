"""Check the current-boot compiler projection and fixed no-content reader."""

from calendar import timegm
from datetime import datetime
import json
import stat
import sys

import pytest

from guard.core.run import _normalize
from guard.executors import kubernetes_normal_compile_cache as compile_cache
from guard.executors.kubernetes_lifecycle_capture import supervised_source
from guard.executors.kubernetes_normal_startup_load import capture_startup_log_prefix
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, load_pack, sha256_hex
from guard.runtime.bounded import capture_pipes
from tests.builders import evidence
from tests.test_gpu_a_run_inputs import UPDATE
from tests.test_kubernetes_normal_startup_load import Adapter, READY, ROOT, STARTED, StartupWorld, _inputs as startup_inputs

CACHE_ID = "abc012def3"
CACHE_PATH = f"/cache/vllm/torch_compile_cache/{CACHE_ID}/rank_0_0/backbone"
ARTIFACT = "vllm_compile_cache.py"
PRIVATE = "EXAMPLE_unrelated_console_text"
FILES = ("compilation/backends.py", "compilation/monitor.py", "logger.py", "logging_utils/formatter.py")


class CompileAdapter(Adapter):
    def compile_cache_source(self, env):
        return {"file_sha256": {name: sha256_hex(name.encode()) for name in FILES[:2]},
                "directory_call_linenumber": 1090 if self.line == 5354 else 1094, "completion_call_linenumber": 53,
                "cache_env": "VLLM_CACHE_ROOT", "cache_root": "/cache/vllm",
                "relative_directory_pattern": r"torch_compile_cache/[0-9a-f]{10}/rank_0_0/backbone",
                "artifact_name": ARTIFACT}


class CompileWorld(StartupWorld):
    def __init__(self, target, adapter, modern, fault):
        super().__init__(target, adapter, modern, fault)
        self.metadata_reads = []
        self.ready_at = (READY.removesuffix("Z") + ".000000001Z"
                         if fault == "metadata-at-fractional-ready" else READY)
        host = "(EngineCore pid=96) " if modern else ""
        src = adapter.compile_cache_source(None)["directory_call_linenumber"]
        selected = "/models/fixture" if fault == "wrong-directory" else CACHE_PATH
        selected_at = "2026-10-26T00:00:04.517Z"
        completed_at = "2026-10-26T00:00:08.534Z"
        if fault == "compile-at-ready":
            completed_at = READY
        elif fault == "compile-after-ready-one-ns":
            completed_at = READY.removesuffix("Z") + ".000000001Z"
        elif fault == "compile-reversed-one-ns":
            selected_at = "2026-10-26T00:00:08.534000002Z"
            completed_at = "2026-10-26T00:00:08.534000001Z"
        completion = (f"{completed_at} {host}INFO 10-26 00:00:08 [monitor.py:53] "
                      "torch.compile took 3.27 s in total\n")
        self.raw = (f"2026-10-26T00:00:03.324Z INFO {PRIVATE}\rprogress ended\n"
                    f"{selected_at} {host}INFO 10-26 00:00:04 [backends.py:{src}] "
                    f"Using cache directory: {selected} for vLLM's torch.compile\n").encode()
        if fault != "no-completed-compile":
            self.raw += completion.encode()
        if fault == "duplicate-compile":
            self.raw += completion.encode()

    def pod_readback(self, role, document, after):
        super().pod_readback(role, document, after)
        if role == "engine":
            document["status"]["conditions"][0]["lastTransitionTime"] = self.ready_at
            container, = document["spec"]["containers"]
            container["env"].append({"name": "VLLM_CACHE_ROOT", "value": "/cache/vllm"})
            container["volumeMounts"].append({"name": "cache", "mountPath": "/cache"})
            if self.fault == "overlay":
                container["volumeMounts"].append({"name": "nested", "mountPath": "/cache/vllm/torch_compile_cache"})
            if self.fault == "compile-override":
                container["env"].append({"name": "VLLM_USE_AOT_COMPILE", "value": "1"})
            if self.fault in {"changed-after-stat", "metadata-unavailable-owner-changed"} and self.metadata_reads:
                document["metadata"]["uid"] = "new-generation"

    def runner(self, argv, **kwargs):
        if "exec" not in argv:
            answer = super().runner(argv, **kwargs)
            if "logs" in argv and self.fault == "prefix-changed":
                answer["stdout_bytes"] = answer["stdout_bytes"].replace(b"3.27 s", b"3.28 s")
            return answer
        engine = self.target["kubernetes"]["engine"]
        assert argv[:2] == ["kubectl", "--context=fixture-context"]
        args = argv[3:]
        if "input_bytes" in kwargs:
            assert args[:10] == [f"--namespace={engine['namespace']}", "exec", "-i", engine["pod"],
                                 "--container=engine", "--", "python3", "-I", "-S", "-c"]
            request = json.loads(kwargs["input_bytes"])
            assert request["roots"] == [ROOT] and {row["relative"] for row in request["files"]} == set(FILES)
            self.hash_calls.append(request)
            files = [{**row, "sha256": sha256_hex(row["relative"].encode()), "state": "hashed"}
                     for row in request["files"]]
            if self.fault == "wrong-installed-source" or (
                    self.fault in {"metadata-unavailable-source-changed", "metadata-absent-source-changed"}
                    and len(self.hash_calls) == 2):
                files[0]["sha256"] = "0" * 64
            source = {"files": files}
        else:
            assert args[:10] == [f"--namespace={engine['namespace']}", "exec", engine["pod"],
                                "--container=engine", "--", "python3", "-I", "-S", "-B", "-c"]
            assert args[10] == compile_cache._METADATA_PROGRAM and len(args) == 13
            assert 0 < float(args[11]) <= 5 and args[12] == CACHE_ID and kwargs["max_stdout_bytes"] == 4096
            self.metadata_reads.append(args)
            boot_ns = timegm(datetime.fromisoformat(STARTED).utctimetuple()) * 1_000_000_000
            artifact = {"device": 2, "inode": 100, "uid": 2000, "gid": 0, "size": 543,
                        "mode": stat.S_IFREG | 0o640, "mtime_ns": boot_ns + 7000000000, "ctime_ns": boot_ns + 7000000000}
            directory = {**artifact, "inode": 99, "size": 4096, "mode": stat.S_IFDIR | 0o700}
            if self.fault == "metadata-at-fractional-ready":
                artifact["mtime_ns"] = artifact["ctime_ns"] = boot_ns + 12_000_000_001
            elif self.fault == "stale":
                artifact["mtime_ns"] = boot_ns - 1000
            elif self.fault == "non-file":
                artifact["mode"] = stat.S_IFLNK | 0o777
            elif self.fault == "empty":
                artifact["size"] = 0
            source = {"reader_uid": 2000, "directory": directory, "artifact": artifact}
            if self.fault in {"metadata-unavailable", "metadata-unavailable-source-changed",
                              "metadata-unavailable-owner-changed"}:
                source = {}
            elif self.fault in {"metadata-absent", "metadata-absent-source-changed", "metadata-absent-invalid-parent"}:
                source["artifact"] = None
                if self.fault == "metadata-absent-invalid-parent":
                    directory["mode"] = stat.S_IFREG | 0o600
        return {"returncode": 0, "stdout_bytes": canonical_json(source), "capture": {"complete": True}}


def _run(tmp_path, version, fault=None):
    env, target, path, startup = startup_inputs(tmp_path, version)
    adapter = CompileAdapter(startup.adapter.line)
    world = CompileWorld(target, adapter, version == "0.31.0", fault)
    world.fault = None
    target["startup_log_prefix"] = capture_startup_log_prefix(target, env, runner=world.runner)
    assert world.log_calls == [65536] and not world.hash_calls and not world.metadata_reads
    world.log_calls.clear()
    world.fault = fault
    path.write_bytes(canonical_json(target))
    executor = compile_cache.KubernetesNormalCompileCacheExecutor(
        {"normal_compile_cache_only": True, "target": str(path)},
        runner=world.runner, expected_target_sha256=sha256_hex(path.read_bytes()))
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["SUP-07"]
    draft = executor.run(check, env, {}, adapter, tmp_path / "work")
    text = draft.artifacts[0].path.read_text()
    assert all(raw not in text for raw in (CACHE_ID, CACHE_PATH, PRIVATE, ROOT, "pid=96", ARTIFACT))
    return draft, json.loads(text), check, world


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
@pytest.mark.parametrize("settings,accepted", [
    ({"max_duration_seconds": 30.5, "kubectl_binary": "example-kubectl"}, True),
    ({"max_duration_seconds": 0}, False),
    ({"kubectl_binary": " "}, False),
])
def test_prepares_saved_compile_cache_target_without_collecting(tmp_path, version, settings, accepted):
    from tools.mvp_versions import versions

    env, target, path, startup = startup_inputs(tmp_path, version)
    image = next(item["image"] for item in versions().values() if item["version"] == version)
    env = env.model_copy(update={"engine": env.engine.model_copy(update={"image_digest": image})}).seal()
    target["environment_ref"] = env.ref().model_dump(mode="json")
    target["engine"]["image_digest"] = image
    target["kubernetes"]["engine"].update(image=image, image_id=image)
    target["startup_log_prefix"] = capture_startup_log_prefix(target, env, runner=startup.runner)
    raw = canonical_json(target)
    path.write_bytes(raw)
    pin = sha256_hex(raw)
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["SUP-07"]

    def no_collection(*args, **kwargs):
        pytest.fail("SUP-07 preparation must not collect from the target")

    executor = compile_cache.KubernetesNormalCompileCacheExecutor(
        {"normal_compile_cache_only": True, "target": str(path), **settings}, runner=no_collection,
        expected_target_sha256=pin)
    if accepted:
        executor.prepared_normal_compile_cache(check, env, {}, expected_target_sha256=pin)
        other = path.with_name("saved-target.json")
        path.rename(other)
        path.symlink_to(other.name)
        adapter = compile_cache.get_provider(env.engine.name).adapter()
        with pytest.raises(ValueError, match="^SUP07_COMPILE_TARGET_OR_SOURCE_UNVERIFIED$"):
            executor.run(check, env, {}, adapter, tmp_path / "work")
        pin = sha256_hex(b"different-owned-target")
        other.rename(path)
    with pytest.raises(ValueError, match="^SUP07_COMPILE_TARGET_OR_SOURCE_UNVERIFIED$"):
        executor.prepared_normal_compile_cache(check, env, {}, expected_target_sha256=pin)


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
def test_normal_compile_with_owned_boot_cache_metadata_is_partial(tmp_path, version):
    draft, artifact, check, world = _run(tmp_path, version)
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert len(world.hash_calls) == 2 and len(world.log_calls) == len(world.metadata_reads) == 1 and not world.execs
    assert not draft.normal_control.attempted
    assert [row.attempted for row in draft.challenges] == [True, False, False]
    assert artifact["observation"]["compilation"]["reported_compile_elapsed_ms"] == 3270
    cache = artifact["observation"]["cache_file_metadata"]
    assert cache["file_permissions"] == 0o640 and cache["directory_permissions"] == 0o700
    assert cache["file_owner_uid"] == cache["directory_owner_uid"] == cache["reader_uid"] == 2000
    assert cache["file_path_sha256"] == sha256_hex(f"{CACHE_PATH}/{ARTIFACT}".encode())
    assert cache["file_bytes"] == 543
    assert artifact["gap"] is None and "file_read_and_consumption" in artifact["missing"]
    assert artifact["retained_compile_report"] is None
    observations = _normalize(check, draft)["observations"]
    assert observations["supply_boot_complete"] == {"collected": True, "value": False}
    assert observations["supply_boot_violations"] == {"collected": False, "value": None}
    assessed = RuleTableJudge().judge(check, evidence(check_id=check.id, observations=observations), check.judge.params, {})
    assert assessed.verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize(("fault", "complete"), [
    ("compile-at-ready", True),
    ("compile-after-ready-one-ns", False),
    ("compile-reversed-one-ns", False),
    ("metadata-at-fractional-ready", True),
])
def test_compile_uses_nanosecond_boot_window_and_event_order(tmp_path, fault, complete):
    draft, artifact, _, world = _run(tmp_path, "0.31.0", fault)
    assert draft.data_origin == DataOrigin.EXAMPLE
    assert draft.status == (ExecStatus.COMPLETED if complete else ExecStatus.ABORTED)
    assert bool(world.metadata_reads) is complete
    assert (artifact["observation"] is not None) is complete
    assert artifact["gap"] == (None if complete else "SUP07_COMPILE_REPORT_UNVERIFIED")


@pytest.mark.parametrize(("fault", "stat_called"), [
    ("wrong-installed-source", False), ("prefix-changed", False), ("wrong-directory", False), ("overlay", False),
    ("compile-override", False),
    ("no-completed-compile", False), ("duplicate-compile", False), ("non-file", True), ("empty", True),
    ("stale", True), ("changed-after-stat", True), ("metadata-unavailable-source-changed", True),
    ("metadata-unavailable-owner-changed", True),
])
def test_current_path_and_stable_metadata_are_required(tmp_path, fault, stat_called):
    draft, artifact, _, world = _run(tmp_path, "0.31.0", fault)
    assert bool(world.metadata_reads) is stat_called
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    assert artifact["observation"] is None and artifact["gap"] == draft.abort_reason
    assert (artifact["retained_compile_report"] is not None) is (fault in {"non-file", "empty", "stale"})
    if fault in {"changed-after-stat", "metadata-unavailable-source-changed", "metadata-unavailable-owner-changed"}:
        assert draft.abort_reason == "SUP07_COMPILE_OWNER_AFTER_UNVERIFIED"


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
def test_retains_verified_native_compile_report_when_file_metadata_is_unavailable(tmp_path, version):
    draft, artifact, _, world = _run(tmp_path, version, "metadata-unavailable")
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    assert draft.abort_reason == artifact["gap"] == "SUP07_COMPILE_FILE_METADATA_UNVERIFIED"
    assert len(world.hash_calls) == 2 and len(world.log_calls) == len(world.metadata_reads) == 1
    assert artifact["retained_compile_report"] == {
        "scope": "native_ordinary_compile_monitor",
        "selected_at": "2026-10-26T00:00:04.517000+00:00",
        "completed_at": "2026-10-26T00:00:08.534000+00:00",
        "reported_compile_elapsed_ms": 3270,
        "reported_directory_sha256": sha256_hex(CACHE_PATH.encode()),
    }
    assert set(artifact["installed_source_sha256"]) == set(FILES)
    assert artifact["observation"] is None and not draft.observations["supply_boot_sessions"].collected
    assert "ordinary_compile_cache_file_metadata" in artifact["missing"]


@pytest.mark.parametrize("fault", ["metadata-absent", "metadata-absent-source-changed", "metadata-absent-invalid-parent"])
def test_retains_only_same_owner_and_source_bound_cache_file_absence(tmp_path, fault):
    draft, artifact, _, world = _run(tmp_path, "0.31.0", fault)
    assert len(world.metadata_reads) == 1
    assert draft.status == ExecStatus.ABORTED
    gap = ("SUP07_COMPILE_OWNER_AFTER_UNVERIFIED" if fault == "metadata-absent-source-changed"
           else "SUP07_COMPILE_FILE_METADATA_UNVERIFIED")
    assert draft.abort_reason == artifact["gap"] == gap
    assert artifact["observation"] is None and not draft.observations["supply_boot_sessions"].collected
    absence = (artifact["retained_compile_report"] or {}).get("cache_file_absence")
    if fault == "metadata-absent":
        assert absence["artifact_absent_at_capture"] is True
        assert absence["metadata_program_sha256"] == compile_cache._METADATA_PROGRAM_SHA256
        assert len(absence["metadata_snapshot_sha256"]) == 64
    else:
        assert absence is None


@pytest.mark.parametrize("leaf_type", ["unreadable", "symbolic-link", "absent", "missing-parent"])
def test_native_reader_lstats_only_fixed_leaf(tmp_path, leaf_type):
    # Relocate the shell's fixture root in a test copy. The production child
    # receives only the checked compile ID and cannot choose a root.
    source = compile_cache._METADATA_SOURCE.replace("CACHE_ROOT = '/cache/vllm'", f"CACHE_ROOT = {str(tmp_path)!r}", 1)
    folder = tmp_path / "torch_compile_cache" / CACHE_ID / "rank_0_0" / "backbone"
    if leaf_type != "missing-parent":
        folder.mkdir(parents=True)
    leaf = folder / ARTIFACT
    if leaf_type == "unreadable":
        leaf.write_bytes(PRIVATE.encode())
        leaf.chmod(0)
    elif leaf_type == "symbolic-link":
        leaf.symlink_to(tmp_path / "unreadable-elsewhere")
    actual = capture_pipes([sys.executable, "-I", "-S", "-B", "-c", supervised_source(source), "3", CACHE_ID],
                           timeout=5, env_extra={}, max_stdout_bytes=4096, max_stderr_bytes=1024)
    assert actual["capture"]["complete"] and actual["returncode"] == 0 and not actual["stderr_bytes"]
    assert PRIVATE.encode() not in actual["stdout_bytes"] and str(tmp_path).encode() not in actual["stdout_bytes"]
    projected = json.loads(actual["stdout_bytes"])
    if leaf_type == "unreadable":
        assert projected["artifact"]["size"] == len(PRIVATE) and stat.S_IMODE(projected["artifact"]["mode"]) == 0
    elif leaf_type == "absent":
        assert projected["artifact"] is None and stat.S_ISDIR(projected["directory"]["mode"])
    else:
        assert projected == {}
