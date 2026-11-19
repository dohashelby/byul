"""Check the owned startup capture and exact-byte replay using EXAMPLE process output."""

import json

import pytest

from guard.core.run import _normalize
from guard.executors.kubernetes_normal_startup_load import (
    KubernetesNormalStartupLoadExecutor, capture_startup_log_prefix,
)
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, sha256_hex
from tests.builders import baseline_pack, evidence
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
        if self.fault == "owner-changed" and self.log_calls:
            document["metadata"]["uid"] = "other-generation"
        if self.fault == "source-mount":
            container["volumeMounts"][0]["mountPath"] = ROOT
        if self.fault == "v2-runner":
            container["env"][0]["value"] = "1"
        if self.fault == "custom-args":
            container["args"] = [*ARGS, "--logging-config=/example"]

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
    target["comparison_config"] = {"launch": {"argv": [*ARGS, "--logging-config=/example"] if fault == "custom-args" else ARGS}}
    adapter = Adapter(5354 if modern else 5416)
    world = StartupWorld(target, adapter, modern, fault)
    return env, target, path, world


def _run(tmp_path, version, fault=None):
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
    executor = KubernetesNormalStartupLoadExecutor(config, expected_target_sha256=pin, runner=world.runner)
    check = baseline_pack().checks["LIF-01"]
    if fault in {"disabled", "wrong-target-pin"}:
        with pytest.raises(ValueError, match=r"^LIF01_BOOT_(SCOPE_INVALID|TARGET_OR_SOURCE_UNVERIFIED)$"):
            executor.run(check, env, VALUES, world.adapter, tmp_path / "work")
        assert not world.log_calls and not world.hash_calls
        return
    draft = executor.run(check, env, VALUES, world.adapter, tmp_path / "work")
    if fault in {"source-mount", "v2-runner", "wrong-source", "custom-args"}:
        assert not world.log_calls
    else:
        assert world.log_calls == [len(world.raw)]
    raw = draft.artifacts[0].path.read_text()
    assert PRIVATE not in raw and "/models/fixture" not in raw and ROOT not in raw and "pid=96" not in raw
    return draft, json.loads(raw), check, world


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


@pytest.mark.parametrize("fault", ["source-mount", "v2-runner", "wrong-source", "custom-args", "changed-prefix", "short-replay",
                                   "missing-newline", "truncated", "duplicate", "out-of-window", "wrong-call-site",
                                   "owner-changed", "source-changed", "ready-changed"])
def test_lif01_rejects_unbound_sources_without_a_load_observation(tmp_path, fault):
    draft, artifact, _, _ = _run(tmp_path, "0.31.0", fault)
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    assert artifact["observation"] is None and artifact["gap"] == draft.abort_reason
    assert not draft.observations["supply_boot_sessions"].collected


@pytest.mark.parametrize("fault", ["disabled", "wrong-target-pin"])
def test_lif01_pre_io_requires_explicit_scope_and_exact_target(tmp_path, fault):
    _run(tmp_path, "0.30.0", fault)


def test_prefix_capture_refuses_cap_boundary_and_does_not_supply_measured_values(tmp_path):
    env, target, _, world = _inputs(tmp_path, "0.31.0")
    world.raw = b"x" * 65535 + b"\n"
    with pytest.raises(ValueError, match="^LIF01_BOOT_PREFIX_UNVERIFIED$"):
        capture_startup_log_prefix(target, env, runner=world.runner)
    assert world.log_calls == [65536] and not world.hash_calls
