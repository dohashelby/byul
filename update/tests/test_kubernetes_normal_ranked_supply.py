"""Verify the ordinary TP2 compile cache and reader reports from one owned boot."""

from calendar import timegm
from datetime import datetime
import json
import stat

import pytest

from guard.adapters.engine_provider import get_provider
from guard.application import _scoped_target_sha256
from guard.core.run import _normalize
from guard.executors import kubernetes_normal_compile_cache as cache
from guard.executors import kubernetes_normal_safetensors_reader as reader
from guard.executors.kubernetes_normal_startup_load import capture_startup_log_prefix
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, load_pack, sha256_hex
from tests.builders import evidence
from tests.test_gpu_a_run_inputs import UPDATE, deployed
from tools import gpu_a_run_inputs
from tools.mvp_versions import versions

CACHE_ID = "abc012def3"
AOT_ID = "d1"*32
AOT_PATH = f"/cache/vllm/torch_compile_cache/torch_aot_compile/{AOT_ID}/rank_0_0/model"


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize(("check_id", "aot"), [("SUP-07", False), ("SUP-07", True), ("SUP-12", False)])
@pytest.mark.parametrize("fault", [None, "wrong-rank", "worker-replaced"])
def test_owned_tp2_boot_publishes_scoped_rank_zero_observation(tmp_path, side, check_id, aot, fault):
    selected = versions()[side]
    release = selected["version"]
    pods, render = deployed(tmp_path, "v2-template", engine_version=release, engine_image=selected["image"],
                            release_launch=True, normal_tp2=True)
    namespace = json.loads((tmp_path / "namespace.json").read_bytes())
    for pod in pods.values():
        pod["kind"] = "Pod"
    pods["engine"]["status"]["conditions"][0]["lastTransitionTime"] = "2026-11-04T01:00:18.000000001Z"
    env = gpu_a_run_inputs.environment(render, inventory="c" * 64, gpu_model="EXAMPLE L4", driver="EXAMPLE driver",
        observed_engine_version=release, external_platform="Linux x86_64", observed_gpu_count=2,
        engine_pod=pods["engine"], namespace=namespace)
    target = gpu_a_run_inputs.target(env, render, namespace, pods["engine"], pods["probe"], context="fixture-context",
                                    observed_engine_version=release)
    provider = get_provider("vllm")
    compile_branch = check_id == "SUP-07"
    startup = provider.evidence_contract("startup_load_source", env)
    source = provider.evidence_contract("compile_cache_source" if compile_branch else "safetensors_reader_source", env)
    expected = {**source["file_sha256"], **{
        name: startup["file_sha256"][name] for name in (
            "logger.py", "logging_utils/formatter.py", "v1/executor/multiproc_executor.py", "utils/system_utils.py",
        )}}
    rows = (
        ("backends", 1094 if side == "baseline" else 1090,
         f"Using cache directory: /cache/vllm/torch_compile_cache/{CACHE_ID}/rank_0_0/backbone for vLLM's torch.compile"),
        ("monitor", 53, "torch.compile took 3.27 s in total"),
    ) if compile_branch else (
        ("weight_utils", 895 if side == "baseline" else 1063,
         "Filesystem type for checkpoints: EXT4. Checkpoint size: 0.92 GiB. Available RAM: 19.10 GiB."),
        ("weight_utils", 918 if side == "baseline" else 1086,
         "Auto-prefetch is disabled because the filesystem (EXT4) is not a recognized network FS (NFS/Lustre). "
         "If you want to force prefetching, start vLLM with --safetensors-load-strategy=prefetch."),
        ("default_loader", 430 if side == "baseline" else 484, "Loading weights took 0.10 seconds"),
    )
    if aot:
        publication_line = 719 if side == "baseline" else 717
        assert source["aot_save_call_linenumber"] == publication_line
        rows = (rows[0], ("decorators", publication_line, f"saved AOT compiled function to {AOT_PATH}"), rows[1])
    lines = []
    for index, (file, line, body) in enumerate(rows):
        rank = 1 if fault == "wrong-rank" and index == len(rows) - 1 else 0
        pid = 41106 if fault == "worker-replaced" and index == len(rows) - 1 else 41105
        lines.append(f"2026-11-04T01:00:0{4+index}.00000000{index+1}Z "
                     f"(Worker_TP{rank} pid={pid}) INFO 11-04 01:00:05 [{file}.py:{line}] {body}".encode())
    raw = b"\n".join(lines) + b"\n"
    hashes, logs, stats = [], [], []

    def runner(argv, **kwargs):
        assert argv[:2] == ["kubectl", "--context=fixture-context"]
        command = argv[3:]
        if command == ["get", "namespaces", "serving-lab", "-o", "json"]:
            result = {"kind": "List", "items": [namespace]}
        elif "get" in command:
            result, = (pod for pod in pods.values() if pod["metadata"]["name"] in command)
        elif "logs" in command:
            logs.append(kwargs["max_stdout_bytes"])
            return {"returncode": 0, "stdout_bytes": raw,
                    "capture": {"complete": True, "bounded_at_source": True, "truncated": False, "timed_out": False}}
        elif "input_bytes" in kwargs:
            assert command[1:9] == ["exec", "-i", pods["engine"]["metadata"]["name"],
                                    "--container=engine", "--", "python3", "-I", "-S"]
            request = json.loads(kwargs["input_bytes"])
            hashes.append(request)
            assert request["roots"] == [startup["source_root"]]
            assert {row["relative"] for row in request["files"]} == expected.keys()
            result = {"files": [{**row, "sha256": expected[row["relative"]], "state": "hashed"}
                                for row in request["files"]]}
        else:
            assert compile_branch
            assert command[1:10] == ["exec", pods["engine"]["metadata"]["name"], "--container=engine",
                                     "--", "python3", "-I", "-S", "-B", "-c"]
            program = cache._AOT_METADATA_PROGRAM if aot else cache._METADATA_PROGRAM
            assert command[10] == program and command[12] == (AOT_ID if aot else CACHE_ID)
            stats.append(command)
            begin = datetime.fromisoformat(target["startup_ready_window"]["container_started_at"])
            instant = timegm(begin.utctimetuple()) * 10**9 + 4 * 10**9
            file = {"device": 2, "inode": 100, "uid": 2000, "gid": 0, "size": 543,
                    "mode": stat.S_IFREG | 0o640, "mtime_ns": instant, "ctime_ns": instant}
            directory = {**file, "inode": 99, "size": 4096, "mode": stat.S_IFDIR | 0o700}
            result = {"reader_uid": 2000, "directory": directory, "artifact": file}
        return {"returncode": 0, "stdout_bytes": canonical_json(result), "capture": {"complete": True}}

    target["startup_log_prefix"] = capture_startup_log_prefix(target, env, runner=runner)
    wire = canonical_json(target)
    target_file = tmp_path / "boot-target.json"
    target_file.write_bytes(wire)
    pin = sha256_hex(wire)
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks[check_id]
    assert _scoped_target_sha256(wire, env, scoped_check=check_id) == pin
    if compile_branch:
        executor = cache.KubernetesNormalCompileCacheExecutor(
            {cache.NORMAL_OPTION: True, "target": str(target_file)}, expected_target_sha256=pin, runner=runner)
        executor.prepared_normal_compile_cache(check, env, {}, expected_target_sha256=pin)
        values = {}
    else:
        executor = reader.KubernetesNormalSafetensorsReaderExecutor(
            {reader.NORMAL_OPTION: True, "target": str(target_file)}, expected_target_sha256=pin, runner=runner)
        values = {"supply_allowed_readers": ["safetensors"]}
        executor.prepared_normal_reader(check, env, values, expected_target_sha256=pin)
    assert not hashes and not stats and logs == [65536]
    draft = executor.run(check, env, values, provider.adapter(), tmp_path / "evidence")
    assert draft.data_origin == DataOrigin.EXAMPLE and not draft.normal_control.attempted
    artifact = draft.artifacts[0].path.read_bytes()
    assert CACHE_ID.encode() not in artifact and AOT_ID.encode() not in artifact and b"pid=" not in artifact
    summary = json.loads(artifact)
    assert logs == [65536, len(raw)]
    if fault:
        assert draft.status == ExecStatus.ABORTED and summary["observation"] is None
        assert not stats and len(hashes) == 1
        return

    assert draft.status == ExecStatus.COMPLETED and summary["installed_source_sha256"] == expected
    assert len(hashes) == 2 and len(stats) == int(compile_branch)
    assert summary["observation"] is not None and summary["gap"] is None
    if compile_branch:
        observation = summary["observation"]["compilation"]
        assert observation["reported_tp_rank"] == 0
        assert observation["directory_line_sha256"] == sha256_hex(lines[0])
        assert observation["completion_line_sha256"] == sha256_hex(lines[-1])
        if aot:
            assert observation["artifact_kind"] == "native_aot_model"
            assert observation["publication_line_sha256"] == sha256_hex(lines[1])
            assert summary["observation"]["cache_file_metadata"]["file_path_sha256"] == sha256_hex(AOT_PATH.encode())
        else:
            assert "artifact_kind" not in observation
        assert "other_tp_rank_compile_artifacts" in summary["missing"]
    else:
        observation = summary["observation"]["reader"]
        assert observation["reported_tp_rank"] == 0
        assert list(observation["report_line_sha256"].keys()) == ["filesystem", "loader_return", "strategy"]
        assert set(observation["report_line_sha256"].values()) == {sha256_hex(line) for line in lines}
        assert "other_tp_rank_reader_selection" in summary["missing"]
    normalized = _normalize(check, draft)["observations"]
    assert normalized["supply_boot_complete"] == {"value": False, "collected": True}
    result = RuleTableJudge().judge(check, evidence(check_id=check_id, observations=normalized), check.judge.params, values)
    assert result.verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("symlink", [False, True])
def test_tp2_aot_metadata_only_accepts_the_regular_fixed_leaf(tmp_path, symlink):
    import sys

    from guard.executors.kubernetes_lifecycle_capture import supervised_source
    from guard.runtime.bounded import capture_pipes

    source = cache._metadata_source("model", ["torch_aot_compile"], ["rank_0_0"], r"[0-9a-f]{64}")
    source = source.replace("CACHE_ROOT = '/cache/vllm'", f"CACHE_ROOT = {str(tmp_path)!r}", 1)
    folder = tmp_path / "torch_compile_cache" / "torch_aot_compile" / AOT_ID / "rank_0_0"
    folder.mkdir(parents=True)
    file = folder / "model"
    if symlink:
        file.symlink_to(tmp_path / "other")
    else:
        file.write_bytes(b"EXAMPLE opaque artifact bytes")
        file.chmod(0)
    result = capture_pipes([sys.executable, "-I", "-S", "-B", "-c", supervised_source(source), "3", AOT_ID],
                           timeout=5, env_extra={}, max_stdout_bytes=4096, max_stderr_bytes=1024)
    assert result["capture"]["complete"] and not result["stderr_bytes"] and result["returncode"] == 0
    raw = result["stdout_bytes"]
    assert b"EXAMPLE" not in raw and str(tmp_path).encode() not in raw
    snapshot = json.loads(raw)
    if symlink:
        assert snapshot == {}
    else:
        assert stat.S_IMODE(snapshot["artifact"]["mode"]) == 0
        assert snapshot["artifact"]["size"] == file.stat().st_size
