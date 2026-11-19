"""Check the current-boot compiler projection and fixed no-content reader."""

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
from tests.test_kubernetes_normal_startup_load import Adapter, ROOT, STARTED, StartupWorld, _inputs as startup_inputs

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
        host = "(EngineCore pid=96) " if modern else ""
        src = adapter.compile_cache_source(None)["directory_call_linenumber"]
        selected = "/models/fixture" if fault == "wrong-directory" else CACHE_PATH
        completion = (f"2026-10-26T00:00:08.534Z {host}INFO 10-26 00:00:08 [monitor.py:53] "
                      "torch.compile took 3.27 s in total\n")
        self.raw = (f"2026-10-26T00:00:03.324Z INFO {PRIVATE}\rprogress ended\n"
                    f"2026-10-26T00:00:04.517Z {host}INFO 10-26 00:00:04 [backends.py:{src}] "
                    f"Using cache directory: {selected} for vLLM's torch.compile\n").encode()
        if fault != "no-completed-compile":
            self.raw += completion.encode()
        if fault == "duplicate-compile":
            self.raw += completion.encode()

    def pod_readback(self, role, document, after):
        super().pod_readback(role, document, after)
        if role == "engine":
            container, = document["spec"]["containers"]
            container["env"].append({"name": "VLLM_CACHE_ROOT", "value": "/cache/vllm"})
            container["volumeMounts"].append({"name": "cache", "mountPath": "/cache"})
            if self.fault == "overlay":
                container["volumeMounts"].append({"name": "nested", "mountPath": "/cache/vllm/torch_compile_cache"})
            if self.fault == "compile-override":
                container["env"].append({"name": "VLLM_USE_AOT_COMPILE", "value": "1"})
            if self.fault == "changed-after-stat" and self.metadata_reads:
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
            if self.fault == "wrong-installed-source":
                files[0]["sha256"] = "0" * 64
            source = {"files": files}
        else:
            assert args[:10] == [f"--namespace={engine['namespace']}", "exec", engine["pod"],
                                "--container=engine", "--", "python3", "-I", "-S", "-B", "-c"]
            assert args[10] == compile_cache._METADATA_PROGRAM and len(args) == 13
            assert 0 < float(args[11]) <= 5 and args[12] == CACHE_ID and kwargs["max_stdout_bytes"] == 4096
            self.metadata_reads.append(args)
            boot_ns = compile_cache._ns(compile_cache._stamp(STARTED))
            artifact = {"device": 2, "inode": 100, "uid": 2000, "gid": 0, "size": 543,
                        "mode": stat.S_IFREG | 0o640, "mtime_ns": boot_ns + 7000000000, "ctime_ns": boot_ns + 7000000000}
            directory = {**artifact, "inode": 99, "size": 4096, "mode": stat.S_IFDIR | 0o700}
            if self.fault == "stale":
                artifact["mtime_ns"] = boot_ns - 1000
            elif self.fault == "non-file":
                artifact["mode"] = stat.S_IFLNK | 0o777
            elif self.fault == "empty":
                artifact["size"] = 0
            source = {"reader_uid": 2000, "directory": directory, "artifact": artifact}
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
    observations = _normalize(check, draft)["observations"]
    assert observations["supply_boot_complete"] == {"collected": True, "value": False}
    assert observations["supply_boot_violations"] == {"collected": False, "value": None}
    assessed = RuleTableJudge().judge(check, evidence(check_id=check.id, observations=observations), check.judge.params, {})
    assert assessed.verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize(("fault", "stat_called"), [
    ("wrong-installed-source", False), ("prefix-changed", False), ("wrong-directory", False), ("overlay", False),
    ("compile-override", False),
    ("no-completed-compile", False), ("duplicate-compile", False), ("non-file", True), ("empty", True),
    ("stale", True), ("changed-after-stat", True),
])
def test_current_path_and_stable_metadata_are_required(tmp_path, fault, stat_called):
    draft, artifact, _, world = _run(tmp_path, "0.31.0", fault)
    assert bool(world.metadata_reads) is stat_called
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    assert artifact["observation"] is None and artifact["gap"] == draft.abort_reason


@pytest.mark.parametrize("leaf_type", ["unreadable", "symbolic-link"])
def test_native_reader_lstats_only_fixed_leaf_and_rejects_link(tmp_path, leaf_type):
    # Relocate the shell's fixture root in a test copy. The production child
    # receives only the checked compile ID and cannot choose a root.
    source = compile_cache._METADATA_SOURCE.replace("CACHE_ROOT = '/cache/vllm'", f"CACHE_ROOT = {str(tmp_path)!r}", 1)
    folder = tmp_path / "torch_compile_cache" / CACHE_ID / "rank_0_0" / "backbone"
    folder.mkdir(parents=True)
    leaf = folder / ARTIFACT
    if leaf_type == "unreadable":
        leaf.write_bytes(PRIVATE.encode())
        leaf.chmod(0)
    else:
        leaf.symlink_to(tmp_path / "unreadable-elsewhere")
    actual = capture_pipes([sys.executable, "-I", "-S", "-B", "-c", supervised_source(source), "3", CACHE_ID],
                           timeout=5, env_extra={}, max_stdout_bytes=4096, max_stderr_bytes=1024)
    assert actual["capture"]["complete"] and actual["returncode"] == 0 and not actual["stderr_bytes"]
    assert PRIVATE.encode() not in actual["stdout_bytes"] and str(tmp_path).encode() not in actual["stdout_bytes"]
    projected = json.loads(actual["stdout_bytes"])
    if leaf_type == "unreadable":
        assert projected["artifact"]["size"] == len(PRIVATE) and stat.S_IMODE(projected["artifact"]["mode"]) == 0
    else:
        assert projected == {}
