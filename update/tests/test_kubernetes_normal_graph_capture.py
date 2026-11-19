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
from tests.test_kubernetes_normal_startup_load import ARGS, Adapter, ROOT, StartupWorld, _inputs as startup_inputs

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
    if fault == "zero":
        rows[2] = progress.replace("0/2", "0/0").replace("2/2", "0/0")
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
    world.raw = ("\n".join(rows) + "\n").encode()

    target["startup_log_prefix"] = capture_startup_log_prefix(target, env, runner=world.runner)
    assert not world.hash_calls and world.log_calls == [65536]
    world.log_calls.clear()
    world.fault = fault
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
    assert all(value not in summary for value in (UNRELATED, "pid=96", ROOT, "/models/fixture", "0.03 GiB", "1 secs"))
    assert not world.execs and not draft.normal_control.attempted
    return draft, json.loads(summary), check, world


@pytest.mark.parametrize(("version", "layout"), [("0.30.0", None), ("0.31.0", None), ("0.31.0", "separate-cri-rows")])
def test_req20_observes_nonempty_native_capture_baseline_only(tmp_path, version, layout):
    draft, summary, check, world = _run(tmp_path, version, layout)
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert summary["gap"] is None and len(summary["conditions_sha256"]) == 64
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
    ("0.30.0", "prefix"), ("0.31.0", "image"), ("0.30.0", "pin"),
])
def test_req20_prepares_the_selected_boot_declaration_without_collection(tmp_path, version, damage):
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

    raw = canonical_json(target)
    path.write_bytes(raw)
    pin = sha256_hex(raw) if damage != "pin" else sha256_hex(b"different-owned-target")
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["REQ-20"]

    def no_collection(*args, **kwargs):
        pytest.fail("REQ-20 declaration validation must not collect from the target")

    executor = graph.KubernetesNormalGraphCaptureExecutor(config, runner=no_collection)
    if damage is None:
        executor.prepared_normal_graph_capture(check, env, {}, expected_target_sha256=pin)
    else:
        with pytest.raises(ValueError, match="^REQ20_CAPTURE_TARGET_OR_SOURCE_UNVERIFIED$"):
            executor.prepared_normal_graph_capture(check, env, {}, expected_target_sha256=pin)
