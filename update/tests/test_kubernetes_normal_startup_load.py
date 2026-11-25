"""Check the owned startup capture and exact-byte replay using EXAMPLE process output."""

from copy import deepcopy
import json

import pytest

from guard.core.run import _normalize
from guard.executors.kubernetes_normal_startup_load import (
    KubernetesNormalStartupLoadExecutor, _load_cost, capture_startup_log_prefix, normal_startup_load_transport,
)
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, load_pack, sha256_hex
from tests.builders import evidence
from tests.test_gpu_a_run_inputs import UPDATE
from tests.test_kubernetes_sampling import World, normal_sampling_inputs

STARTED = "2026-10-26T00:00:00Z"
READY = "2026-10-26T00:00:12Z"
PRIVATE = "EXAMPLE-unrelated-startup-text"
ROOT = "/usr/local/lib/python3.12/dist-packages/vllm"
ENTRYPOINT = ["/usr/local/bin/vllm-nonroot-entrypoint.sh"]
ARGS = ["serve", "/models/fixture", "--tensor-parallel-size", "1"]
VALUES = {"supply_stage_budget_ms": {"resolve": 60000, "load": 600000},
          "supply_stage_budget_bytes": {"resolve": 1048576, "load": 17179869184},
          "supply_total_budget_ms": 1800000}
SOURCE_FILES = ("v1/worker/gpu_model_runner.py", "utils/mem_utils.py", "platforms/cuda.py", "logger.py",
                "logging_utils/formatter.py")
CUSTOM_ARGS = {"custom-args": "--logging-config=/example", "custom-args-underscored": "--logging_config=/example"}


class Adapter:
    def __init__(self, line):
        self.line = line

    def startup_load_source(self, env):
        return {"source_root": ROOT, "entrypoint": ENTRYPOINT, "logger_call_linenumber": self.line,
                "file_sha256": {file: sha256_hex(file.encode()) for file in SOURCE_FILES},
                "required_env": {"VLLM_USE_V2_MODEL_RUNNER": "0"}, "optional_env": {"VLLM_LOGGING_LEVEL": "INFO"},
                "forbidden_args": ["--load-format", "--logging-config"]}


class StartupWorld(World):
    def __init__(self, target, adapter, modern, fault):
        super().__init__(target)
        self.adapter = adapter
        self.fault = fault
        self.log_calls = []
        self.ready_samples = []
        self.hash_calls = []
        self.mutate = self.pod_readback
        prefix = "(EngineCore pid=96) " if modern else ""
        event_time = "2026-10-26T00:00:15.962929630Z" if fault == "out-of-window" else "2026-10-26T00:00:05.962929630Z"
        line = (f"{event_time} {prefix}INFO 10-26 00:00:05 [gpu_model_runner.py:{adapter.line}] "
                "Model loading took 0.47 GiB memory and 4.463586 seconds\n")
        if fault == "duplicate":
            line += line
        if fault == "wrong-call-site":
            line = line.replace(str(adapter.line), "119")
        self.raw = (f"2026-10-26T00:00:03.324Z INFO {PRIVATE}\rprogress ended\n" + line).encode()

    def pod_readback(self, role, document, after):
        if role == "namespaces":
            for row in document["items"]:
                row["kind"] = "Namespace"
            return
        document["kind"] = "Pod"
        if role != "engine":
            return
        container, = document["spec"]["containers"]
        container.update(command=ENTRYPOINT, args=ARGS, env=[{"name": "VLLM_USE_V2_MODEL_RUNNER", "value": "0"}],
                         volumeMounts=[{"name": "model", "mountPath": "/models", "readOnly": True}])
        document["status"]["conditions"][0]["lastTransitionTime"] = READY
        if self.fault == "ready-changed" and self.log_calls:
            document["status"]["conditions"][0]["lastTransitionTime"] = "2026-10-26T00:00:13Z"
        self.ready_samples.append((len(self.log_calls), document["status"]["conditions"][0]["lastTransitionTime"]))
        if self.fault == "owner-changed" and self.log_calls:
            document["metadata"]["uid"] = "other-generation"
        if self.fault == "source-mount":
            container["volumeMounts"][0]["mountPath"] = ROOT
        if self.fault == "v2-runner":
            container["env"][0]["value"] = "1"
        if self.fault in CUSTOM_ARGS:
            container["args"] = [*ARGS, CUSTOM_ARGS[self.fault]]

    def runner(self, argv, **kwargs):
        assert argv[:2] == ["kubectl", "--context=fixture-context"]
        assert kwargs["timeout"] > 0
        if "logs" in argv:
            args = argv[3:]
            engine = self.target["kubernetes"]["engine"]
            cap = kwargs["max_stdout_bytes"]
            assert args == [f"--namespace={engine['namespace']}", "logs", engine["pod"], "--container=engine",
                            "--timestamps", f"--since-time={STARTED}", f"--limit-bytes={cap}"]
            self.log_calls.append(cap)
            raw = self.raw
            if self.fault == "changed-prefix":
                raw = raw.replace(b"4.463586", b"4.463587")
            if self.fault == "short-replay":
                raw = raw.splitlines(keepends=True)[0]
            if self.fault == "missing-newline":
                raw = raw.rstrip(b"\n")
            return {"returncode": 0, "stdout_bytes": raw[:cap],
                    "capture": {"complete": True, "bounded_at_source": True, "timed_out": False,
                                "truncated": self.fault == "truncated"}}
        if "exec" in argv:
            engine = self.target["kubernetes"]["engine"]
            assert argv[3:13] == [f"--namespace={engine['namespace']}", "exec", "-i", engine["pod"], "--container=engine",
                                  "--", "python3", "-I", "-S", "-c"]
            payload = json.loads(kwargs["input_bytes"])
            assert payload["roots"] == [ROOT] and {item["relative"] for item in payload["files"]} == set(SOURCE_FILES)
            files = [{**item, "sha256": sha256_hex(item["relative"].encode()), "state": "hashed"}
                     for item in payload["files"]]
            self.hash_calls.append(payload)
            if self.fault == "wrong-source" or self.fault == "source-changed" and len(self.hash_calls) == 2:
                files[0]["sha256"] = "0" * 64
            return {"returncode": 0, "stdout_bytes": canonical_json({"files": files}), "capture": {"complete": True}}
        return super().runner(argv, **kwargs)


def _inputs(tmp_path, version, fault=None):
    modern = version == "0.31.0"
    env, target, _, path = normal_sampling_inputs(tmp_path, version)
    env = env.model_copy(update={"runtime": env.runtime.model_copy(update={"tp_size": 1, "pp_size": 1, "dp_size": 1})}).seal()
    target["environment_ref"] = env.ref().model_dump(mode="json")
    target["kubernetes"]["engine"]["started_at"] = STARTED
    target["comparison_config"] = {"launch": {"argv": [*ARGS, CUSTOM_ARGS[fault]] if fault in CUSTOM_ARGS else ARGS}}
    adapter = Adapter(5354 if modern else 5416)
    world = StartupWorld(target, adapter, modern, fault)
    return env, target, path, world


def _run(tmp_path, version, fault=None, *, values=VALUES):
    env, target, path, world = _inputs(tmp_path, version, fault)
    original_fault = world.fault
    world.fault = None
    prefix = capture_startup_log_prefix(target, env, runner=world.runner)
    assert world.log_calls == [65536] and not world.hash_calls
    assert prefix == {"byte_count": len(world.raw), "sha256": sha256_hex(world.raw),
                      "container_started_at": STARTED, "ready_at": READY}
    target["startup_log_prefix"] = prefix
    path.write_bytes(canonical_json(target))
    world.log_calls.clear()
    world.fault = original_fault
    config = {"normal_startup_load_only": fault != "disabled", "target": str(path)}
    pin = "0" * 64 if fault == "wrong-target-pin" else sha256_hex(path.read_bytes())
    runner = world.runner
    if fault == "target-alias":
        original = path.with_name("original-target.json")
        path.rename(original)
        path.symlink_to(original)

        def runner(*args, **kwargs):
            pytest.fail("A late target alias must be rejected before transport dispatch")

    executor = KubernetesNormalStartupLoadExecutor(config, expected_target_sha256=pin, runner=runner)
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["LIF-01"]
    if fault in {"disabled", "wrong-target-pin", "target-alias"}:
        with pytest.raises(ValueError, match=r"^LIF01_BOOT_(SCOPE_INVALID|TARGET_OR_SOURCE_UNVERIFIED)$"):
            executor.run(check, env, values, world.adapter, tmp_path / "work")
        assert not world.log_calls and not world.hash_calls
        return
    draft = executor.run(check, env, values, world.adapter, tmp_path / "work")
    if fault in {"source-mount", "v2-runner", "wrong-source", *CUSTOM_ARGS}:
        assert not world.log_calls
        if fault in CUSTOM_ARGS:
            assert not world.hash_calls
    else:
        assert world.log_calls == [len(world.raw)]
    raw = draft.artifacts[0].path.read_text()
    assert PRIVATE not in raw and "/models/fixture" not in raw and ROOT not in raw and "pid=96" not in raw
    return draft, json.loads(raw), check, world


@pytest.mark.parametrize("fault", (None, "retained", "current"), ids=("same", "retained-drift", "current-drift"))
def test_lif01_prefix_correlates_saved_ready_with_the_current_window(tmp_path, fault):
    env, target, _, world = _inputs(tmp_path, "0.31.0", "ready-changed" if fault == "current" else None)
    window = {"container_started_at": STARTED, "ready_at": READY}
    target["startup_ready_window"] = dict(window)
    if fault == "retained":
        target["startup_ready_window"]["ready_at"] = "2026-10-26T00:00:12.000000001Z"
    if fault is not None:
        with pytest.raises(ValueError, match="^LIF01_BOOT_PREFIX_UNVERIFIED$"):
            capture_startup_log_prefix(target, env, runner=world.runner)
        assert len(world.log_calls) == int(fault == "current")
        return

    prefix = capture_startup_log_prefix(target, env, runner=world.runner)
    assert world.ready_samples[0] == (0, READY)
    assert world.ready_samples[-1] == (1, READY)
    assert prefix == {"byte_count": len(world.raw), "sha256": sha256_hex(world.raw), **window}
    target["startup_log_prefix"] = prefix

    def forbidden(*args, **kwargs):
        pytest.fail("Retained Ready window validation cannot contact a target")

    normal_startup_load_transport(target, env, duration=1, runner=forbidden)
    target["startup_ready_window"]["ready_at"] = "2026-10-26T00:00:12.000000001Z"
    with pytest.raises(ValueError, match="^LIF01_BOOT_WINDOW_UNVERIFIED$"):
        normal_startup_load_transport(target, env, duration=1, runner=forbidden)


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
def test_lif01_observes_only_current_startup_elapsed_and_reported_gpu_delta(tmp_path, version):
    draft, observation, check, world = _run(tmp_path, version)
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert len(world.hash_calls) == 2 and not world.execs
    assert observation["gap"] is None and len(observation["conditions_sha256"]) == 64
    assert observation["installed_source_sha256"] == world.adapter.startup_load_source(None)["file_sha256"]
    selected = observation["observation"]["engine_reported_load_cost"]
    assert selected["stage"] == "load" and selected["elapsed_us"] == 4463586
    assert selected["reported_allocator_delta_centi_gib"] == 47 and "peak_bytes" not in selected
    assert not draft.normal_control.attempted
    assert [step.attempted for step in draft.challenges] == [True, False, False]
    projected = _normalize(check, draft)["observations"]
    assert projected["supply_boot_sessions"]["value"] == [observation["observation"]]
    assert projected["supply_boot_violations"] == {"value": None, "collected": False}
    assert projected["supply_boot_complete"] == {"value": False, "collected": True}
    assert projected["supply_boot_normal_verified"]["collected"] is False
    evd = evidence(check_id=check.id, observations=projected)
    assert RuleTableJudge().judge(check, evd, check.judge.params, VALUES).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize(("runner_mode", "fault"), [
    ("v1", None), ("v1", "missing-rank"), ("v1", "same-pid"), ("v1", "source-changed"),
    ("v1", "launcher-mounted"), ("default", None), ("default", "wrong-file"),
    ("default", "wrong-call-site"), ("default", "source-changed"), ("default", "runner-override"),
    ("default", "ambiguous-marker"),
])
def test_lif01_reads_two_source_pinned_native_rank_reports(tmp_path, side, runner_mode, fault):
    from guard.adapters.engine_provider import get_provider
    from guard.application import _scoped_target_sha256
    from guard.executors.kubernetes_normal_startup_load import RANKED_PROFILE
    from tests.test_gpu_a_run_inputs import deployed
    from tools import gpu_a_aws_lab, gpu_a_run_inputs, mvp_live_readiness
    from tools.mvp_versions import versions

    selected = versions()[side]
    version = selected["version"]
    generation = "v2-template" if runner_mode == "default" else gpu_a_aws_lab.NORMAL_TP2_V1_EAGER_GENERATION
    pods, render = deployed(tmp_path, generation, engine_version=version, engine_image=selected["image"],
                            release_launch=True, normal_tp2=True)
    namespace = json.loads((tmp_path / "namespace.json").read_bytes())
    for pod in pods.values():
        pod["kind"] = "Pod"
    ready = "2026-11-04T01:00:18.000000001Z"
    pods["engine"]["status"]["conditions"][0]["lastTransitionTime"] = ready
    env = gpu_a_run_inputs.environment(render, inventory="b" * 64, gpu_model="EXAMPLE L4", driver="EXAMPLE driver",
        observed_engine_version=version, external_platform="Linux x86_64", observed_gpu_count=2,
        engine_pod=pods["engine"], namespace=namespace)
    if fault == "ambiguous-marker":
        runtime = env.runtime.model_copy(update={"platform": f"{env.runtime.platform}; runner=v1"})
        env = env.model_copy(update={"runtime": runtime}).seal()
    target = gpu_a_run_inputs.target(env, render, namespace, pods["engine"], pods["probe"], context="fixture-context",
                                    observed_engine_version=version)
    provider = get_provider(env.engine.name)
    adapter = provider.adapter()
    stock_line = {"0.30.0": 428, "0.31.0": 407}[version]
    logger_line = stock_line if runner_mode == "default" else {"0.30.0": 5416, "0.31.0": 5354}[version]
    logger_file = "model_runner.py" if runner_mode == "default" else "gpu_model_runner.py"
    source_file = "v1/worker/gpu/model_runner.py" if runner_mode == "default" else "utils/system_utils.py"
    source = provider.evidence_contract("startup_load_source", env) if fault != "ambiguous-marker" else None
    if fault == "wrong-call-site":
        logger_line += 1
    reported_file = "gpu_model_runner.py" if fault == "wrong-file" else logger_file
    elapsed = ["4.000001", "6.000002"]  # One exceeds the budget if misread as a whole-stage finding.
    timestamps = ["2026-11-04T01:00:05.000000001Z", "2026-11-04T01:00:08.000000002Z"]
    pids = [211011, 211011 if fault == "same-pid" else 211012]
    rows = [f"{timestamps[rank]} (Worker_TP{rank} pid={pids[rank]}) INFO 11-04 01:00:08 "
            f"[{reported_file}:{logger_line}] Model loading took 0.47 GiB memory and {elapsed[rank]} seconds\n"
            for rank in (0, 1)]
    raw = "".join(rows[:1] if fault == "missing-rank" else rows).encode()
    hashes, logs = [], []
    replaying = False

    def runner(argv, **kwargs):
        assert argv[:2] == ["kubectl", "--context=fixture-context"]
        command = argv[3:]
        if command == ["get", "namespaces", "serving-lab", "-o", "json"]:
            stdout = canonical_json({"kind": "List", "items": [namespace]})
        elif "get" in command:
            pod, = (pod for pod in pods.values() if pod["metadata"]["name"] in command)
            if replaying and pod is pods["engine"] and fault in {"launcher-mounted", "runner-override"}:
                pod = deepcopy(pod)
                container = pod["spec"]["containers"][0]
                if fault == "launcher-mounted":
                    container["volumeMounts"].append({"name": "model", "mountPath": "/usr/local/nvidia/bin"})
                else:
                    container["env"].append({"name": "VLLM_USE_V2_MODEL_RUNNER", "value": "1"})
            stdout = canonical_json(pod)
        elif "logs" in command:
            assert command[3:5] == ["--container=engine", "--timestamps"]
            assert "--since-time=" + target["startup_ready_window"]["container_started_at"] in command
            logs.append(kwargs["max_stdout_bytes"])
            stdout = raw
        else:
            assert command[1:9] == ["exec", "-i", pods["engine"]["metadata"]["name"],
                                     "--container=engine", "--", "python3", "-I", "-S"]
            payload = json.loads(kwargs["input_bytes"])
            hashes.append(payload)
            assert payload["roots"] == [source["source_root"]]
            assert {row["relative"] for row in payload["files"]} == source["file_sha256"].keys()
            files = [{**row, "state": "hashed", "sha256": source["file_sha256"][row["relative"]]}
                     for row in payload["files"]]
            if fault == "source-changed" and len(hashes) == 2:
                for row in files:
                    if row["relative"] == source_file:
                        row["sha256"] = "0" * 64
            stdout = canonical_json({"files": files})
        return {"returncode": 0, "stdout_bytes": stdout, "capture": {
            "complete": True, "bounded_at_source": True, "truncated": False, "timed_out": False}}

    target["startup_log_prefix"] = capture_startup_log_prefix(target, env, runner=runner)
    replaying = True
    assert logs == [65536] and not hashes
    path = tmp_path / "bound-target.json"
    target_raw = canonical_json(target)
    path.write_bytes(target_raw)
    pin = sha256_hex(target_raw)
    executor = KubernetesNormalStartupLoadExecutor(
        {"normal_startup_load_only": True, "target": str(path)}, expected_target_sha256=pin, runner=runner)
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["LIF-01"]
    values = deepcopy(VALUES)
    values["supply_stage_budget_ms"]["load"] = 5000
    if fault == "ambiguous-marker":
        with pytest.raises(ValueError, match="^LIF01_BOOT_TARGET_OR_SOURCE_UNVERIFIED$"):
            executor.prepared_normal_startup_load(check, env, values, expected_target_sha256=pin)
        assert mvp_live_readiness.startup_observer_reason(check.id, target, env) == "NORMAL_STARTUP_LOAD_TARGET_UNAVAILABLE"
        assert not hashes and logs == [65536]
        return
    assert mvp_live_readiness.startup_observer_reason(check.id, target, env) is None
    assert _scoped_target_sha256(target_raw, env, scoped_check=check.id) == pin
    with pytest.raises(ValueError, match="^LIF01_BOOT_TARGET_UNVERIFIED$"):
        normal_startup_load_transport(target, env, duration=1, runner=runner)
    executor.prepared_normal_startup_load(check, env, values, expected_target_sha256=pin)
    draft = executor.run(check, env, values, adapter, tmp_path / "result")
    summary = json.loads(draft.artifacts[0].path.read_bytes())
    if fault:
        assert draft.status == ExecStatus.ABORTED and summary["observation"] is None
        gaps = {"source-changed": "LIF01_BOOT_OWNER_AFTER_UNVERIFIED",
                "launcher-mounted": "LIF01_BOOT_OWNER_BEFORE_UNVERIFIED",
                "runner-override": "LIF01_BOOT_OWNER_BEFORE_UNVERIFIED"}
        if fault == "runner-override":
            assert not hashes and logs == [65536]
        assert summary["gap"] == gaps.get(fault, "LIF01_BOOT_RANK_REPORT_UNAVAILABLE")
        return
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert logs == [65536, len(raw)] and len(hashes) == 2
    assert summary["profile"] == RANKED_PROFILE and summary["reported_load_time_budget"] is None
    assert summary["source_log_call_filename"] == logger_file
    assert summary["source_log_call_linenumber"] == logger_line
    assert source_file in summary["installed_source_sha256"]
    assert summary["observation"]["worker_identity_verified"] is False
    projected = summary["observation"]["engine_reported_rank_load_costs"]
    assert [row["reported_rank"] for row in projected] == [0, 1]
    assert [row["elapsed_us"] for row in projected] == [4000001, 6000002]
    assert [row["log_timestamp"] for row in projected] == timestamps
    assert "pid=" not in draft.artifacts[0].path.read_text()
    evd = evidence(check_id=check.id, observations=_normalize(check, draft)["observations"])
    assert RuleTableJudge().judge(check, evd, check.judge.params, values).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("instant,observed_at", [
    ("00:00:00.123456788Z", None),
    ("00:00:00.123456789Z", "2026-10-26T00:00:00.123456+00:00"),
    ("00:00:12.000000000Z", "2026-10-26T00:00:12+00:00"),
    ("00:00:12.000000001Z", None),
])
def test_lif01_load_report_respects_exact_nanosecond_boot_window(instant, observed_at):
    window = {"container_started_at": "2026-10-26T00:00:00.123456789Z", "ready_at": READY}
    raw = (f"2026-10-26T{instant} INFO 10-26 00:00:00 [gpu_model_runner.py:5416] "
           "Model loading took 0.47 GiB memory and 4.463586 seconds\n").encode()
    if observed_at is None:
        with pytest.raises(ValueError):
            _load_cost(raw, window, 5416)
    else:
        observed = _load_cost(raw, window, 5416)
        assert observed["observed_at"] == observed_at
        assert observed["elapsed_us"] == 4463586


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
@pytest.mark.parametrize("budget_ms,exceeded", [(4463, True), (4464, False)])
def test_lif01_consumes_verified_elapsed_against_the_approved_load_time_budget(tmp_path, version, budget_ms, exceeded):
    values = {**VALUES, "supply_stage_budget_ms": {**VALUES["supply_stage_budget_ms"], "load": budget_ms},
              "supply_stage_budget_bytes": {**VALUES["supply_stage_budget_bytes"], "load": 0}}
    draft, artifact, check, _ = _run(tmp_path, version, values=values)
    assert artifact["reported_load_time_budget"] == {"elapsed_us": 4463586, "limit_us": budget_ms * 1000,
                                                      "exceeded": exceeded}
    observations = _normalize(check, draft)["observations"]
    assert observations["supply_boot_violations"] == {
        "value": [{"reason": "stage_budget_exceeded_without_stop", "stage": "load"}] if exceeded else None,
        "collected": exceeded,
    }
    assert observations["supply_boot_complete"]["value"] is False
    evd = evidence(check_id=check.id, observations=observations)
    result = RuleTableJudge().judge(check, evd, check.judge.params, values)
    assert result.verdicts["security"] == (Verdict.FAIL if exceeded else Verdict.UNKNOWN)


@pytest.mark.parametrize("fault", ["source-mount", "v2-runner", "wrong-source", *CUSTOM_ARGS, "changed-prefix", "short-replay",
                                   "missing-newline", "truncated", "duplicate", "out-of-window", "wrong-call-site",
                                   "owner-changed", "source-changed", "ready-changed"])
def test_lif01_rejects_unbound_sources_without_a_load_observation(tmp_path, fault):
    draft, artifact, _, _ = _run(tmp_path, "0.31.0", fault)
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    assert artifact["observation"] is None and artifact["gap"] == draft.abort_reason
    assert not draft.observations["supply_boot_sessions"].collected


@pytest.mark.parametrize("fault", ["disabled", "wrong-target-pin", "target-alias"])
def test_lif01_pre_io_requires_explicit_scope_and_exact_target(tmp_path, fault):
    _run(tmp_path, "0.30.0", fault)


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
@pytest.mark.parametrize("settings", [{}, {"max_duration_seconds": 0}, {"kubectl_binary": " "}])
def test_lif01_preparation_validates_effective_transport_without_collecting(tmp_path, version, settings):
    from tools.mvp_versions import versions

    env, target, path, world = _inputs(tmp_path, version)
    image = next(row["image"] for row in versions().values() if row["version"] == version)
    env = env.model_copy(update={"engine": env.engine.model_copy(update={"image_digest": image})}).seal()
    target["environment_ref"] = env.ref().model_dump(mode="json")
    target["engine"]["image_digest"] = image
    target["kubernetes"]["engine"].update(image=image, image_id=image)
    target["startup_log_prefix"] = capture_startup_log_prefix(target, env, runner=world.runner)
    raw = canonical_json(target)
    path.write_bytes(raw)
    digest = sha256_hex(raw)
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["LIF-01"]

    def forbidden(*args, **kwargs):
        pytest.fail("LIF-01 preparation must not collect logs or installed source")

    cfg = {"normal_startup_load_only": True, "target": str(path), **settings}
    executor = KubernetesNormalStartupLoadExecutor(cfg, runner=forbidden, expected_target_sha256=digest)
    if not settings:
        executor.prepared_normal_startup_load(check, env, VALUES, expected_target_sha256=digest)
    else:
        with pytest.raises(ValueError, match="^LIF01_BOOT_TARGET_OR_SOURCE_UNVERIFIED$"):
            executor.prepared_normal_startup_load(check, env, VALUES, expected_target_sha256=digest)


def test_prefix_capture_refuses_cap_boundary_and_does_not_supply_measured_values(tmp_path):
    env, target, _, world = _inputs(tmp_path, "0.31.0")
    world.raw = b"x" * 65535 + b"\n"
    with pytest.raises(ValueError, match="^LIF01_BOOT_PREFIX_UNVERIFIED$"):
        capture_startup_log_prefix(target, env, runner=world.runner)
    assert world.log_calls == [65536] and not world.hash_calls


def test_stock_adapter_uses_registered_engine_source_contract(tmp_path, monkeypatch):
    from guard.executors import kubernetes_normal_startup_load as startup

    env, _, _, world = _inputs(tmp_path, "0.31.0")
    source = world.adapter.startup_load_source(env)

    class Provider:
        def evidence_contract(self, name, bound_env):
            assert name == "startup_load_source" and bound_env is env
            return source

    def provider(engine):
        assert engine == env.engine.name
        return Provider()

    monkeypatch.setattr(startup, "get_provider", provider)
    assert startup._source_profile(object(), env) == source
