"""Check REQ-20's bounded startup observation through the owned log producer."""

import json

import pytest

from guard.core.run import _normalize
from guard.executors import kubernetes_normal_graph_capture as graph
from guard.executors.kubernetes_normal_startup_load import capture_startup_log_prefix
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, load_pack, sha256_hex
from tests.builders import evidence
from tests.test_gpu_a_run_inputs import UPDATE
from tests.test_kubernetes_normal_startup_load import ARGS, Adapter, READY, ROOT, StartupWorld, _inputs as startup_inputs

UNRELATED = "EXAMPLE unrelated console data"
_ZERO = "Capturing CUDA graphs (decode, FULL):   0%|          | 0/2 [00:00<?, ?it/s]"
_DONE = "Capturing CUDA graphs (decode, FULL): 100%|██████████| 2/2 [00:00<00:00, 45.41it/s]"


class GraphAdapter(Adapter):
    def graph_capture_source(self, env):
        return {"capture_completion_call_linenumber": 6854 if self.line == 5354 else 6929}


def _run(tmp_path, version, fault=None, graph_option=None):
    env, target, path, initial = startup_inputs(tmp_path, version)
    adapter = GraphAdapter(initial.adapter.line)
    modern = version == "0.31.0"
    world = StartupWorld(target, adapter, modern, None)
    if graph_option:
        args = [*ARGS, graph_option]
        target["comparison_config"]["launch"]["argv"] = args

        def pod_readback(role, document, after):
            world.pod_readback(role, document, after)
            if role == "engine":
                document["spec"]["containers"][0]["args"] = args

        world.mutate = pod_readback
    proc = "(EngineCore pid=96) " if modern else ""
    callsite = adapter.graph_capture_source(env)["capture_completion_call_linenumber"]
    if fault == "wrong-callsite":
        callsite -= 1
    progress = f"2026-10-26T00:00:05.323Z \r{_ZERO}\r{_DONE}"
    if fault == "separate-cri-rows":
        progress = progress.replace(f"\r{_DONE}", f"\n2026-10-26T00:00:05.415Z \r{_DONE}")
    completion = (f"2026-10-26T00:00:06.514Z {proc}INFO 10-26 00:00:06 "
                  f"[gpu_model_runner.py:{callsite}] Graph capturing finished in 1 secs, took 0.03 GiB")
    rows = [f"2026-10-26T00:00:03.324Z INFO {UNRELATED}\rprogress ended",
            "2026-10-26T00:00:04.300Z \rCapturing CUDA graphs (mixed prefill-decode, PIECEWISE): "
            "100%|██████████| 3/3 [00:00<00:00, 48.40it/s]", progress, completion]
    if fault == "no-decode":
        rows.remove(progress)
    if fault in {"zero", "progress-owner-changed", "progress-source-changed"}:
        rows[2] = progress.replace("0/2", "0/0").replace("2/2", "0/0")
    if fault == "invalid-timestamp":
        rows[2] = progress.replace("2026-10-26T00:00:05.323Z", "EXAMPLE-INVALID-TIME")
    if fault == "unfinished":
        rows[2] = progress.replace(_DONE, "")
    if fault == "starts-after-zero":
        rows[2] = progress.replace(_ZERO, "")
    if fault == "other-generation-progress":
        rows.insert(3, progress)
    if fault == "out-of-window":
        rows[2] = progress.replace("00:00:05.", "00:00:15.")
    if fault == "wrong-order":
        rows[2], rows[3] = completion, progress
    if fault == "duplicate-return":
        rows.append(completion)
    if fault == "return-out-of-window":
        rows[3] = completion.replace("00:00:06.514Z", "00:00:16.514Z")
    if fault == "return-at-ready":
        rows[3] = completion.replace("2026-10-26T00:00:06.514Z", READY)
    if fault == "return-after-ready-one-ns":
        rows[3] = completion.replace("2026-10-26T00:00:06.514Z", READY.removesuffix("Z") + ".000000001Z")
    if fault == "return-before-progress-one-ns":
        rows[2] = progress.replace("00:00:05.323Z", "00:00:06.514000002Z")
        rows[3] = completion.replace("00:00:06.514Z", "00:00:06.514000001Z")
    if fault == "skip-and-return":
        rows.insert(2, "2026-10-26T00:00:05.000Z Skipping CUDA graph capture")
    world.raw = ("\n".join(rows) + "\n").encode()

    target["startup_log_prefix"] = capture_startup_log_prefix(target, env, runner=world.runner)
    assert not world.hash_calls and world.log_calls == [65536]
    world.log_calls.clear()
    world.fault = "source-changed" if fault == "progress-source-changed" else fault
    if fault == "progress-owner-changed":
        engine_reads_after_logs = 0

        def pod_readback(role, document, after):
            nonlocal engine_reads_after_logs
            world.pod_readback(role, document, after)
            if role == "engine" and world.log_calls:
                engine_reads_after_logs += 1
                # The logs transport's own owner check succeeded before the parser.
                if engine_reads_after_logs > 1:
                    document["metadata"]["uid"] = "other-generation"

        world.mutate = pod_readback
    path.write_bytes(canonical_json(target))

    def runner(argv, **kwargs):
        result = world.runner(argv, **kwargs)
        if fault == "prefix-changed" and "logs" in argv:
            result["stdout_bytes"] = result["stdout_bytes"].replace(b"0.03 GiB", b"0.04 GiB")
        return result

    pin = sha256_hex(path.read_bytes()) if fault != "wrong-target" else "0" * 64
    executor = graph.KubernetesNormalGraphCaptureExecutor(
        {graph.NORMAL_OPTION: fault != "disabled", "target": str(path)}, expected_target_sha256=pin, runner=runner)
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["REQ-20"]
    if fault in {"disabled", "wrong-target"}:
        with pytest.raises(ValueError, match="^REQ20_CAPTURE_(SCOPE_INVALID|TARGET_OR_SOURCE_UNVERIFIED)$"):
            executor.run(check, env, {}, adapter, tmp_path / "work")
        assert not world.log_calls and not world.hash_calls and not world.execs
        return
    draft = executor.run(check, env, {}, adapter, tmp_path / "work")
    summary = draft.artifacts[0].path.read_text()
    assert all(value not in summary for value in
               (UNRELATED, "pid=96", ROOT, "/models/fixture", "0.03 GiB", "1 secs", "EXAMPLE-INVALID-TIME"))
    assert not world.execs and not draft.normal_control.attempted
    return draft, json.loads(summary), check, world


@pytest.mark.parametrize(("version", "layout"), [("0.30.0", None), ("0.31.0", None), ("0.31.0", "separate-cri-rows")])
def test_req20_observes_nonempty_native_capture_baseline_only(tmp_path, version, layout):
    draft, summary, check, world = _run(tmp_path, version, layout)
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert summary["gap"] is None and len(summary["conditions_sha256"]) == 64
    assert "native_report_diagnostic" not in summary
    assert summary["observation"] == {"mode": "decode_full", "reported_completed_descriptor_iterations": 2,
                                      "native_capture_return_report_observed": True}
    assert [step.attempted for step in draft.challenges] == [True, False, False]
    assert len(world.hash_calls) == 2 and world.log_calls == [len(world.raw)]
    observations = _normalize(check, draft)["observations"]
    assert observations == {"engine_sensor_violation": {"value": None, "collected": False},
                            "engine_sensor_full_scope": {"value": False, "collected": True}}
    result = RuleTableJudge().judge(check, evidence(check_id=check.id, observations=observations), check.judge.params, {})
    assert result.verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("fault", [
    "no-decode", "zero", "unfinished", "starts-after-zero", "other-generation-progress", "out-of-window",
    "wrong-order", "wrong-callsite", "duplicate-return", "wrong-source", "source-changed", "owner-changed",
    "prefix-changed",
])
def test_req20_rejects_without_a_complete_unambiguous_owned_capture_sequence(tmp_path, fault):
    draft, summary, _, world = _run(tmp_path, "0.31.0", fault)
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    assert summary["observation"] is None and summary["gap"] == draft.abort_reason
    assert not any(step.attempted for step in draft.challenges)
    assert bool(world.log_calls) is (fault != "wrong-source")


@pytest.mark.parametrize(("fault", "suffix", "source_reads"), [
    ("zero", "PROGRESS_UNVERIFIED", 2),
    ("no-decode", "LOG_SEQUENCE_UNVERIFIED", 2),
    ("invalid-timestamp", "LOG_TIMESTAMP_UNVERIFIED", 2),
    ("progress-owner-changed", None, 1),
    ("progress-source-changed", None, 2),
])
def test_req20_retains_parser_diagnostic_only_after_owner_and_source_recheck(tmp_path, fault, suffix, source_reads):
    draft, summary, check, world = _run(tmp_path, "0.30.0", fault)
    assert len(world.hash_calls) == source_reads and world.log_calls == [len(world.raw)]
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    expected = "REQ20_CAPTURE_OWNER_AFTER_UNVERIFIED" if suffix is None else "REQ20_CAPTURE_NATIVE_REPORT_UNVERIFIED"
    assert draft.abort_reason == summary["gap"] == expected
    assert summary["observation"] is None and not any(step.attempted for step in draft.challenges)
    if suffix is None:
        assert "native_report_diagnostic" not in summary
    else:
        assert summary["native_report_diagnostic"] == "REQ20_CAPTURE_" + suffix
    if suffix is None:
        assert "native_capture_return_report" not in summary and summary["installed_source_sha256"] is None
    else:
        return_line, = [row for row in world.raw.split(b"\n") if b"Graph capturing finished" in row]
        assert summary["native_capture_return_report"] == {
            "reported_at": "2026-10-26T00:00:06.514000+00:00",
            "log_line_sha256": sha256_hex(return_line),
        }
        assert summary["installed_source_sha256"] and summary["capture_completion_call_linenumber"] == 6929
    observations = _normalize(check, draft)["observations"]
    result = RuleTableJudge().judge(check, evidence(check_id=check.id, observations=observations), check.judge.params, {})
    assert result.verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize(("fault", "complete", "retained_return"), [
    ("return-at-ready", True, False),
    ("return-after-ready-one-ns", False, False),
    ("return-before-progress-one-ns", False, True),
])
def test_req20_uses_nanosecond_boot_window_and_event_order(tmp_path, fault, complete, retained_return):
    draft, summary, _, _ = _run(tmp_path, "0.30.0", fault)
    assert draft.data_origin == DataOrigin.EXAMPLE
    assert draft.status == (ExecStatus.COMPLETED if complete else ExecStatus.ABORTED)
    assert (summary["observation"] is not None) is complete
    assert ("native_capture_return_report" in summary) is retained_return
    if not complete:
        assert summary["native_report_diagnostic"] == "REQ20_CAPTURE_LOG_SEQUENCE_UNVERIFIED"


@pytest.mark.parametrize("fault", ["wrong-callsite", "duplicate-return", "return-out-of-window", "skip-and-return"])
def test_req20_does_not_retain_ambiguous_or_out_of_scope_return(tmp_path, fault):
    draft, summary, _, _ = _run(tmp_path, "0.30.0", fault)
    assert draft.status == ExecStatus.ABORTED and summary["observation"] is None
    assert "native_capture_return_report" not in summary and summary["installed_source_sha256"] is None


@pytest.mark.parametrize("fault", ["disabled", "wrong-target"])
def test_req20_refuses_unselected_or_wrongly_bound_reads(tmp_path, fault):
    _run(tmp_path, "0.30.0", fault)


@pytest.mark.parametrize("graph_option", ["--enforce-eager", "--compilation_config.cudagraph_mode=FULL",
                                         "-cc.cudagraph_mode=FULL", "--cudagraph_capture_sizes=2",
                                         "--max-cudagraph-capture-size=2", "--optimization-level=1"])
def test_req20_rejects_nonstock_capture_options_before_reading_logs_or_sources(tmp_path, graph_option):
    draft, summary, _, world = _run(tmp_path, "0.31.0", graph_option=graph_option)
    assert draft.status == ExecStatus.ABORTED
    assert summary["observation"] is None and not world.log_calls and not world.hash_calls


@pytest.mark.parametrize("version,damage", [
    ("0.30.0", None), ("0.31.0", None), ("0.31.0", "duration"), ("0.30.0", "binary"),
    ("0.30.0", "missing-argv"), ("0.31.0", "invalid-argv"), ("0.31.0", "graph-override"),
    ("0.30.0", "source-override"), ("0.30.0", "prefix"), ("0.31.0", "image"), ("0.30.0", "pin"),
    ("0.30.0", "target-alias"), ("0.31.0", "target-alias"),
])
def test_req20_prepares_the_selected_boot_declaration_without_collection(tmp_path, monkeypatch, version, damage):
    from tools.mvp_versions import versions

    env, target, path, world = startup_inputs(tmp_path, version)
    image = next(item["image"] for item in versions().values() if item["version"] == version)
    if damage == "image":
        image = image.partition("@sha256:")[0] + "@sha256:" + "0" * 64
    env = env.model_copy(update={"engine": env.engine.model_copy(update={"image_digest": image})}).seal()
    target["environment_ref"] = env.ref().model_dump(mode="json")
    target["engine"]["image_digest"] = image
    target["kubernetes"]["engine"].update(image=image, image_id=image)
    if damage != "prefix":
        target["startup_log_prefix"] = capture_startup_log_prefix(target, env, runner=world.runner)
    config = {graph.NORMAL_OPTION: True, "target": str(path)}
    if damage == "duration":
        config["max_duration_seconds"] = 301
    elif damage == "binary":
        config["kubectl_binary"] = " "
    elif damage == "missing-argv":
        del target["comparison_config"]["launch"]["argv"]
    elif damage == "invalid-argv":
        target["comparison_config"]["launch"]["argv"] = [*ARGS, 1]
    elif damage == "graph-override":
        target["comparison_config"]["launch"]["argv"] = [*ARGS, "--compilation_config.cudagraph_mode=FULL"]
    elif damage == "source-override":
        target["comparison_config"]["launch"]["argv"] = [*ARGS, "--logging_config=/example"]

    raw = canonical_json(target)
    path.write_bytes(raw)
    pin = sha256_hex(raw) if damage != "pin" else sha256_hex(b"different-owned-target")
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["REQ-20"]

    def no_collection(*args, **kwargs):
        pytest.fail("REQ-20 declaration validation must not collect from the target")

    executor = graph.KubernetesNormalGraphCaptureExecutor(config, runner=no_collection)
    if damage in (None, "target-alias"):
        executor.prepared_normal_graph_capture(check, env, {}, expected_target_sha256=pin)
        if damage == "target-alias":
            replacement = path.with_name("same-target-bytes.json")
            path.rename(replacement)
            path.symlink_to(replacement)
            monkeypatch.setattr(executor, "_target_inputs", no_collection)
            with pytest.raises(ValueError, match="^REQ20_CAPTURE_TARGET_OR_SOURCE_UNVERIFIED$"):
                executor.prepared_normal_graph_capture(check, env, {}, expected_target_sha256=pin)
            with pytest.raises(ValueError, match="^REQ20_CAPTURE_TARGET_OR_SOURCE_UNVERIFIED$"):
                executor.run(check, env, {}, world.adapter, tmp_path / "work")
    else:
        with pytest.raises(ValueError, match="^REQ20_CAPTURE_TARGET_OR_SOURCE_UNVERIFIED$"):
            executor.prepared_normal_graph_capture(check, env, {}, expected_target_sha256=pin)


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("fault", [None, "lost-profile-boundary", "wrong-actual-memory", "extra-rank",
                                  "worker-replaced", "outside-ready", "changed-helper"])
def test_req20_default_tp2_pairs_profile_and_warmup_reports(tmp_path, side, fault):
    from guard.adapters.engine_provider import get_provider
    from guard.application import _scoped_target_sha256
    from tests.test_gpu_a_run_inputs import deployed
    from tools import gpu_a_run_inputs
    from tools.mvp_versions import versions

    selected = versions()[side]
    version = selected["version"]
    pods, render = deployed(tmp_path, "v2-template", engine_version=version, engine_image=selected["image"],
                            release_launch=True, normal_tp2=True)
    namespace = json.loads((tmp_path / "namespace.json").read_bytes())
    for pod in pods.values():
        pod["kind"] = "Pod"
    ready = "2026-11-04T01:00:18.000000001Z"
    pods["engine"]["status"]["conditions"][0]["lastTransitionTime"] = ready
    env = gpu_a_run_inputs.environment(render, inventory="b" * 64, gpu_model="EXAMPLE L4", driver="EXAMPLE driver",
        observed_engine_version=version, external_platform="Linux x86_64", observed_gpu_count=2,
        engine_pod=pods["engine"], namespace=namespace)
    target = gpu_a_run_inputs.target(env, render, namespace, pods["engine"], pods["probe"], context="fixture-context",
                                    observed_engine_version=version)

    runner_line, profile_line, pool_line = (1066, 655, 825) if side == "baseline" else (1087, 707, 892)
    rows = []
    by_rank = ([], [])
    for phase, (file, line, content) in enumerate((
        ("model_runner", runner_line, "Graph capturing finished in 1 secs, took 0.06 GiB"),
        ("gpu_worker", profile_line,
         "CUDA graph memory profiling is enabled (default since v0.21.0). The estimated memory was recorded."),
        ("model_runner", runner_line, "Graph capturing finished in 1 secs, took 0.03 GiB"),
        ("gpu_worker", pool_line,
         "CUDA graph pool memory: 0.03 GiB (actual), 0.07 GiB (estimated), difference: 0.04 GiB (133.3%)."),
    )):
        for rank in (1, 0):
            if fault == "lost-profile-boundary" and phase == 1 and rank == 1:
                continue
            instant = f"2026-11-04T01:00:0{5 + phase}.00000000{2 - rank}Z"
            pid = 42101 + rank
            if fault == "wrong-actual-memory" and phase == 3 and rank == 1:
                content_for_rank = content.replace("0.03 GiB (actual)", "0.02 GiB (actual)")
            else:
                content_for_rank = content
            if fault == "worker-replaced" and phase >= 2 and rank == 1:
                pid += 10
            if fault == "outside-ready" and phase == 3 and rank == 1:
                instant = ready.replace("000000001Z", "000000002Z")
            row = (f"{instant} (Worker_TP{rank} pid={pid}) INFO 11-04 01:00:08 "
                   f"[{file}.py:{line}] {content_for_rank}").encode()
            rows.append(row)
            by_rank[rank].append(row)
    if fault == "extra-rank":
        rows.append(rows[-1].replace(b"Worker_TP0", b"Worker_TP2"))
    raw = b"\n".join(rows) + b"\n"
    provider = get_provider("vllm")
    helper = "v1/worker/gpu/cudagraph_utils.py"
    helper_pin = {
        "baseline": "6e9c042890603535e300a40df8ee159dbed1058a64a83ae50ff0329e332e05ff",
        "candidate": "8e6ac6f655dd013dd2a3d3561bc88fe05ceeea2b060e5cb6bc85decb0bb2f813",
    }[side]
    source = provider.evidence_contract("startup_load_source", env)
    source_hashes = {**source["file_sha256"], helper: helper_pin}
    hash_reads, log_reads = [], []

    def runner(argv, **kwargs):
        assert argv[:2] == ["kubectl", "--context=fixture-context"]
        command = argv[3:]
        if command == ["get", "namespaces", "serving-lab", "-o", "json"]:
            payload = canonical_json({"kind": "List", "items": [namespace]})
        elif "get" in command:
            pod, = (pod for pod in pods.values() if pod["metadata"]["name"] in command)
            payload = canonical_json(pod)
        elif "logs" in command:
            assert command[3:5] == ["--container=engine", "--timestamps"]
            log_reads.append(kwargs["max_stdout_bytes"])
            payload = raw
        else:
            assert command[1:9] == ["exec", "-i", pods["engine"]["metadata"]["name"],
                                     "--container=engine", "--", "python3", "-I", "-S"]
            request = json.loads(kwargs["input_bytes"])
            hash_reads.append(request)
            assert request["roots"] == [source["source_root"]]
            assert {row["relative"] for row in request["files"]} == source_hashes.keys()
            rows = [{**row, "state": "hashed", "sha256": source_hashes[row["relative"]]} for row in request["files"]]
            if fault == "changed-helper" and len(hash_reads) == 2:
                next(row for row in rows if row["relative"] == helper)["sha256"] = "0" * 64
            payload = canonical_json({"files": rows})
        return {"returncode": 0, "stdout_bytes": payload, "capture": {
            "complete": True, "bounded_at_source": True, "truncated": False, "timed_out": False}}

    target["startup_log_prefix"] = capture_startup_log_prefix(target, env, runner=runner)
    target_raw = canonical_json(target)
    target_file = tmp_path / "bound-graph-target.json"
    target_file.write_bytes(target_raw)
    pin = sha256_hex(target_raw)
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["REQ-20"]
    executor = graph.KubernetesNormalGraphCaptureExecutor(
        {graph.NORMAL_OPTION: True, "target": str(target_file)}, expected_target_sha256=pin, runner=runner)
    assert _scoped_target_sha256(target_raw, env, scoped_check=check.id) == pin
    executor.prepared_normal_graph_capture(check, env, {}, expected_target_sha256=pin)
    assert not hash_reads and log_reads == [65536]
    draft = executor.run(check, env, {}, provider.adapter(), tmp_path / "evidence")
    assert draft.data_origin == DataOrigin.EXAMPLE and not draft.normal_control.attempted
    assert len(hash_reads) == 2 and log_reads == [65536, len(raw)]
    artifact = draft.artifacts[0].path.read_bytes()
    assert b"pid=" not in artifact and b"GiB" not in artifact
    summary = json.loads(artifact)
    if fault:
        assert draft.status == ExecStatus.ABORTED and summary["observation"] is None
        if fault == "changed-helper":
            assert draft.abort_reason == "REQ20_CAPTURE_OWNER_AFTER_UNVERIFIED"
        else:
            assert summary["native_report_diagnostic"] == "REQ20_CAPTURE_RANK_REPORT_UNVERIFIED"
        return

    assert draft.status == ExecStatus.COMPLETED and summary["profile"] == graph.TP2_PROFILE
    assert summary["installed_source_sha256"] == source_hashes
    assert summary["capture_completion_call_linenumber"] == runner_line
    assert summary["profiling_completion_call_linenumber"] == profile_line
    assert summary["warmup_pool_call_linenumber"] == pool_line
    assert summary["observation"]["worker_identity_verified"] is False
    for rank, item in enumerate(summary["observation"]["reported_rank_capture"]):
        assert item["reported_rank"] == rank
        for key, row in zip(("profile_capture", "profiling_completion", "warmup_capture", "warmup_pool_report"),
                            by_rank[rank], strict=True):
            assert item[key] == {"log_timestamp": row.split(b" ", 1)[0].decode(),
                                 "log_line_sha256": sha256_hex(row)}
    assert len(summary["observation"]["reported_rank_capture"]) == 2
    observations = _normalize(check, draft)["observations"]
    assert observations["engine_sensor_full_scope"] == {"value": False, "collected": True}
    result = RuleTableJudge().judge(check, evidence(check_id=check.id, observations=observations), check.judge.params, {})
    assert result.verdicts["security"] == Verdict.UNKNOWN
