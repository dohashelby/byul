"""EXAMPLE-only evidence contracts; no Docker, engine, import probe or network."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from guard.adapters.base import LaunchSpec
from guard.adapters.vllm import VllmAdapter
from guard.adapters.vllm_length_cause import (IMAGE, MODEL, MODEL_INVENTORY, MODEL_REVISION, PROFILE,
    HARDENED_TMPFS, NATIVE_LIBRARIES, NATIVE_PRELOAD, NATIVE_PRELOAD_SHA256, SOURCE_REVIEWS,
    VllmLengthCause, ZERO_ERROR, context_valid)
from guard.executors.http_common import Ctx
from guard.executors.input_response_semantics import rejection_proof
from guard.executors.input_validation import InputValidationExecutor
from guard.runtime.docker import CmdResult
from tests.builders import T0, baseline_pack, completion_response, environment


def runtime():
    return {"schema": "guard-vllm-length-runtime/1", "separate_process": True,
        "same_relevant_environment": True, "no_python_path_injection": True,
        "no_unreviewed_loader_environment": True, "isolated_python": True,
        "native_libraries": {"profile": "reviewed-cpu-native-libraries/1",
            "preload_sha256": NATIVE_PRELOAD_SHA256, "library_sha256": deepcopy(NATIVE_LIBRARIES)},
        "same_api_command": True, "same_interpreter": True, "intel_cpu": True, "module_root_matches": True,
        "source_sha256": {name: values[0] for name, values in SOURCE_REVIEWS.items()},
        "platform_class": "vllm.platforms.cpu.CpuPlatform", "cap_method_source": "platforms/interface.py",
        "cap_values": [2**63-1, 2**63-1], "sys_maxsize": 2**63-1}


def context():
    snapshot = {"verified": True, "container_id": "a" * 64, "image_id": "sha256:" + "b" * 64,
        "image_digest": IMAGE, "started_at": "EXAMPLE-start", "owner": "example-owned",
        "trusted_network": "guard-example-owned-engine", "trusted_network_id": "f" * 64,
        "trusted_ip": "172.19.0.2", "engine_url": "http://172.19.0.2:8000",
        "command_sha256": "c" * 64, "mounts_sha256": "d" * 64, "model": MODEL, "max_model_len": 128}
    return {"profile": PROFILE, "before": snapshot, "after": deepcopy(snapshot),
        "models": {side: {"model": MODEL, "max_model_len": 128, "response_sha256": "e" * 64}
                   for side in ("before", "after")},
        "runtime": {side: runtime() for side in ("before", "after")}, "model_inventory_sha256": MODEL_INVENTORY,
        "controls": {case: {"model": MODEL, "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                            "valid_generation": True}
                     for case in ("before", "after_token_id", "after_length")}}


def request():
    return {"model": MODEL, "prompt": [0] * 128, "max_tokens": 1}


def response(payload, status=200):
    raw = json.dumps(payload)
    return {"outcome": status, "body": raw, "body_bytes": len(raw.encode())}


def rejected():
    return response({"error": {"message": ZERO_ERROR, "param": "max_tokens", "type": "BadRequestError", "code": 400}}, 400)


def review(ctx=None, **changes):
    kwargs = {"case": "length", "method": "POST", "path": "/v1/completions", "request_body": request(),
              "response": rejected(), "context": ctx}
    kwargs.update(changes)
    env = SimpleNamespace(engine=SimpleNamespace(version="0.32.0", image_digest=IMAGE))
    return rejection_proof(VllmAdapter(), env, **kwargs)


def test_error_alone_stays_unverified_and_only_complete_context_resolves_cause():
    assert review()["state"] == "unverified"
    result = review(context())
    assert result["state"] == "verified"
    assert result["reason"] == "REVIEWED_CPU_CONTEXT_EXHAUSTION"
    assert all(source["path"].startswith("vllm/") and len(source["sha256"]) == 64 for source in result["sources"])


@pytest.mark.parametrize("mutate", [
    lambda c: c.pop("before"),
    lambda c: c["after"].update(container_id="f" * 64),
    lambda c: c["after"].update(started_at="EXAMPLE-restarted"),
    lambda c: c["before"].update(max_model_len=256),
    lambda c: c["models"]["after"].update(max_model_len=256),
    lambda c: c["models"]["before"].update(model="EXAMPLE-other-model"),
    lambda c: c["models"]["before"].update(response_sha256=""),
    lambda c: c.update(model_inventory_sha256="0" * 64),
    lambda c: c["runtime"]["after"].update(platform_class="EXAMPLE.CustomPlatform"),
    lambda c: c["runtime"]["before"].update(cap_values=[1, 0]),
    lambda c: c["runtime"]["after"].update(same_relevant_environment=False),
    lambda c: c["runtime"]["after"].update(no_python_path_injection=False),
    lambda c: c["runtime"]["after"].update(no_unreviewed_loader_environment=False),
    lambda c: c["runtime"]["after"].update(isolated_python=False),
    lambda c: c["runtime"]["after"].pop("native_libraries"),
    lambda c: c["runtime"]["after"]["native_libraries"].update(preload_sha256="0" * 64),
    lambda c: c["runtime"]["after"]["native_libraries"]["library_sha256"].update({
        "/opt/venv/lib/libiomp5.so": "0" * 64}),
    lambda c: c["runtime"]["after"].update(same_api_command=False),
    lambda c: c["runtime"]["before"].update(same_interpreter=False),
    lambda c: c["runtime"]["before"]["source_sha256"].update({"sampling_params.py": "f" * 64}),
    lambda c: c["controls"].pop("after_length"),
    lambda c: c["controls"]["after_length"].update(completion_tokens=0),
    lambda c: c["controls"]["after_token_id"].update(prompt_tokens=True),
    lambda c: c["controls"]["after_token_id"].update(valid_generation=False),
    lambda c: c["controls"]["before"].update(model="EXAMPLE-other-model"),
])
def test_ambiguous_cap_identity_source_model_or_control_cannot_be_promoted(mutate):
    value = context()
    mutate(value)
    assert review(value)["state"] == "unverified"


@pytest.mark.parametrize("body", [
    {"model": MODEL, "prompt": [0] * 127, "max_tokens": 1},
    {"model": MODEL, "prompt": [0] * 129, "max_tokens": 1},
    {"model": MODEL, "prompt": [0] * 128, "max_tokens": 0},
    {"model": MODEL, "prompt": [0] * 128, "max_tokens": 1, "truncate_prompt_tokens": 127},
    {"model": MODEL, "prompt": [0] * 128, "max_tokens": 1, "echo": True},
])
def test_different_request_does_not_reuse_length_cause_context(body):
    assert review(context(), request_body=body)["state"] == "unverified"


def make_ctx(tmp_path):
    adapter = VllmAdapter()
    options = {"revision": MODEL_REVISION, "models_dir": str(tmp_path), "device": "cpu",
        "launch_profile": "cpu-bounded", "model_layout": "snapshot", "dtype": "float32",
        "max_model_len": 128, "max_num_seqs": 1, "max_num_batched_tokens": 128,
        "kv_cache_memory_bytes": 67108864, "enforce_eager": True, "cpu_threads": 2,
        "resources": {"cpus": 2, "memory_bytes": 4294967296, "memory_swap_bytes": 5368709120,
                      "shm_bytes": 536870912, "pids_limit": 256}}
    plan = adapter.launch_plan(LaunchSpec(model=MODEL, port=8000, options=options, image=IMAGE))
    launch = {key: value for key, value in options.items() if key not in {"device", "models_dir", "launch_profile", "model_layout"}}
    launch.update(model=MODEL, profile="cpu-bounded-v1", model_layout="snapshot", gpu=False,
                  argv=plan.argv, entrypoint=plan.entrypoint)
    target = {"lab": True, "run_id": "example-owned", "engine": {"trusted_url": "http://172.19.0.2:8000",
        "container": "guard-example-owned-engine"}, "origins": {"trusted": {"network": "guard-example-owned-engine"}},
        "comparison_config": {"launch": launch}}
    def cprobe(network, requests, timeout):
        assert timeout <= 10
        assert requests[0]["method"] == "GET" and requests[0]["url"].endswith("/v1/models")
        return [response({"object": "list", "data": [{"id": MODEL, "max_model_len": 128}]})]
    env = environment(engine={"name": "vllm", "version": "0.32.0", "image_digest": IMAGE},
        model={"id": MODEL, "revision": MODEL_REVISION, "inventory_sha256": MODEL_INVENTORY})
    return Ctx(baseline_pack().checks["REQ-09"], env, adapter, target,
        {"max_input_tokens": 127, "rejection_codes": ["400"], "allowed_endpoints": ["POST /v1/completions"],
         "allowed_input_formats": ["token_ids"]}, 10, False, {"allowed": None}, [], cprobe, tmp_path, T0)


class ExampleDocker:
    def __init__(self, ctx):
        self.calls = []
        self.inspected = {"Id": "a" * 64, "Image": "sha256:" + "b" * 64,
            "State": {"Running": True, "StartedAt": "EXAMPLE-start"},
            "HostConfig": {"ReadonlyRootfs": True, "Tmpfs": deepcopy(HARDENED_TMPFS)},
            "Config": {"Labels": {"guard.run": "example-owned"}, "Image": IMAGE,
                "Cmd": ctx.target["comparison_config"]["launch"]["argv"], "Entrypoint": ["/usr/bin/env"],
                "Env": ["EXAMPLE_SECRET=PRIVATE_VALUE_MUST_NOT_PERSIST", "LD_PRELOAD=" + NATIVE_PRELOAD]},
            "NetworkSettings": {"Networks": {"guard-example-owned-engine": {
                "IPAddress": "172.19.0.2", "NetworkID": "f" * 64}}},
            "Mounts": [{"Destination": "/models", "Type": "bind", "RW": False,
                        "Source": "C:/PRIVATE_HOST_PATH_MUST_NOT_PERSIST"}]}
        self.runtime_value = runtime()

    def inspect_json(self, container, *, timeout):
        self.calls.append("inspect")
        return deepcopy(self.inspected)

    def exec_bounded(self, container, argv, *, timeout, max_bytes):
        assert timeout == 30 and max_bytes == 16384 and argv[:3] == ["python3", "-I", "-c"]
        self.calls.append("runtime")
        return CmdResult(0, json.dumps(self.runtime_value), "", capture={
            "bounded_at_source": True, "complete": True, "truncated": False, "timed_out": False})


def samples():
    values = [{"case": case, "request": {"body": {"model": MODEL, "prompt": [0], "max_tokens": 1}},
        "response": response({"model": MODEL, "choices": [{"text": "EXAMPLE", "finish_reason": "length"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})}
        for case in ("before", "after_token_id", "after_length")]
    return [*values, {"case": "length", "request": {"body": request()}, "response": rejected()}]


def test_owned_bounded_observation_keeps_secrets_paths_out_and_checks_both_sides(tmp_path):
    ctx = make_ctx(tmp_path)
    docker = ExampleDocker(ctx)
    collector = VllmLengthCause(ctx, docker)
    collector.start()
    value = collector.finish(samples())
    assert context_valid(value, request())
    assert value["state"] == "verified" and docker.calls == ["inspect", "runtime", "runtime", "inspect"]
    saved = json.dumps(value)
    assert "PRIVATE_VALUE" not in saved and "PRIVATE_HOST_PATH" not in saved and "Config" not in saved
    assert "separate process" in saved


@pytest.mark.parametrize("mutate", [
    lambda d: d.inspected["Config"]["Labels"].update({"guard.run": "someone-else"}),
    lambda d: d.inspected["Config"]["Cmd"].append("--generation-config=EXAMPLE-other"),
    lambda d: d.inspected["Mounts"][0].update(RW=True),
    lambda d: d.inspected["HostConfig"]["Tmpfs"].update({"/tmp": "rw,exec"}),
    lambda d: d.inspected["HostConfig"].update(ReadonlyRootfs=False),
    lambda d: d.runtime_value.update(same_api_command=False),
    lambda d: d.runtime_value.update(cap_values=[1, 0]),
    lambda d: d.inspected["Config"]["Env"].append("PYTHONPATH=/tmp/EXAMPLE-injection"),
    lambda d: d.inspected["Config"]["Env"].append("PYTHONHOME=/tmp/EXAMPLE-injection"),
    lambda d: d.inspected["Config"].update(Env=["LD_PRELOAD=/tmp/EXAMPLE-injection.so"]),
    lambda d: d.inspected["Config"].update(Env=["LD_PRELOAD=" + ":".join(reversed(NATIVE_PRELOAD.split(":")))]),
    lambda d: d.inspected["Config"].update(Env=[]),
    lambda d: d.inspected["NetworkSettings"]["Networks"]["guard-example-owned-engine"].update(IPAddress="172.19.0.3"),
    lambda d: d.inspected["NetworkSettings"]["Networks"]["guard-example-owned-engine"].update(NetworkID=""),
])
def test_unowned_mutable_or_ambiguous_sources_remain_unverified(tmp_path, mutate):
    ctx = make_ctx(tmp_path)
    docker = ExampleDocker(ctx)
    mutate(docker)
    collector = VllmLengthCause(ctx, docker)
    collector.start()
    assert collector.finish(samples())["state"] == "unverified"


def test_restart_during_collection_cannot_reuse_pre_observations(tmp_path):
    ctx = make_ctx(tmp_path)
    docker = ExampleDocker(ctx)
    collector = VllmLengthCause(ctx, docker)
    collector.start()
    docker.inspected["State"]["StartedAt"] = "EXAMPLE-restarted"
    value = collector.finish(samples())
    assert value["state"] == "unverified" and review(value)["state"] == "unverified"


def test_same_container_name_cannot_bind_another_http_target(tmp_path):
    ctx = make_ctx(tmp_path)
    docker = ExampleDocker(ctx)
    ctx.target["engine"]["trusted_url"] = "http://172.19.0.3:8000"
    collector = VllmLengthCause(ctx, docker)
    collector.start()
    assert collector.finish(samples())["state"] == "unverified"
    assert docker.calls == ["inspect"]


@pytest.mark.parametrize("entry", [
    "LD_AUDIT=/guard-cache/EXAMPLE-evil.so",
    "LD_LIBRARY_PATH=/guard-cache/EXAMPLE-dependencies",
    "LD_ORIGIN_PATH=/guard-cache/EXAMPLE-origin",
    "LD_DEBUG=all",
    "LD_OTHER_UNREVIEWED=EXAMPLE-value",
])
def test_unreviewed_loader_environment_is_rejected_before_exec(tmp_path, entry):
    ctx = make_ctx(tmp_path)
    docker = ExampleDocker(ctx)
    docker.inspected["Config"]["Env"].append(entry)
    collector = VllmLengthCause(ctx, docker)
    collector.start()
    value = collector.finish(samples())
    assert value["state"] == "unverified"
    assert docker.calls == ["inspect"]
    assert "EXAMPLE-evil" not in json.dumps(value) and "EXAMPLE-dependencies" not in json.dumps(value)


@pytest.mark.parametrize("capture", [
    {"bounded_at_source": False, "complete": True},
    {"bounded_at_source": True, "complete": False, "timed_out": True},
    {"bounded_at_source": True, "complete": True, "truncated": True},
])
def test_runtime_capture_must_be_bounded_complete_and_not_timed_out(tmp_path, capture):
    ctx = make_ctx(tmp_path)
    docker = ExampleDocker(ctx)
    docker.exec_bounded = lambda *args, **kwargs: CmdResult(0, json.dumps(runtime()), "", capture=capture)
    collector = VllmLengthCause(ctx, docker)
    collector.start()
    assert collector.finish(samples())["state"] == "unverified"


def test_post_observation_timeout_does_not_discard_previous_samples_or_expose_error(tmp_path):
    ctx = make_ctx(tmp_path)
    docker = ExampleDocker(ctx)
    collector = VllmLengthCause(ctx, docker)
    collector.start()
    before = samples()
    snapshot = deepcopy(before)
    def broken(*args, **kwargs):
        raise TimeoutError("PRIVATE_VALUE_MUST_NOT_PERSIST")
    docker.exec_bounded = broken
    value = collector.finish(before)
    assert value["state"] == "unverified" and before == snapshot
    assert "PRIVATE_VALUE" not in json.dumps(value)


def test_collection_setting_is_opt_in_versioned_and_strict():
    cfg = {"vocab_size": 50272, "vocab_source": "EXAMPLE source", "max_probe_tokens": 128}
    assert InputValidationExecutor(cfg).version == "0.5.0"
    assert InputValidationExecutor({**cfg, "length_cause_evidence": False}).version == "0.5.0"
    assert InputValidationExecutor({**cfg, "length_cause_evidence": True}).version == "0.6.0"
    for value in (1, None, "true"):
        with pytest.raises(ValueError, match="explicit boolean"):
            InputValidationExecutor({**cfg, "length_cause_evidence": value}).validate_configuration(None)


def test_opt_in_collector_failure_preserves_http_controls_and_no_consumption(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    def probe(network, requests, timeout):
        req = requests[0]
        if req["body"]["prompt"] == [50272]:
            return [response({"error": {"message": "Token id 50272 is out of vocabulary", "type": "BadRequestError",
                                       "param": None, "code": 400}}, 400)]
        if len(req["body"]["prompt"]) == 128:
            return [rejected()]
        return [completion_response(req)]
    ctx.cprobe = probe
    class BrokenCollector:
        def start(self):
            raise RuntimeError("PRIVATE_VALUE_MUST_NOT_PERSIST")
    monkeypatch.setattr(ctx.adapter, "input_length_cause_collector", lambda _ctx: BrokenCollector())
    executor = InputValidationExecutor({"vocab_size": 50272, "vocab_source": "EXAMPLE source",
        "max_probe_tokens": 128, "length_cause_evidence": True})
    obs, normal, _challenge, summary = executor.collect(ctx)
    assert normal["succeeded"] is True
    assert obs["challenge_outcomes"]["value"] == {"token_id": "400", "length": "400"}
    assert obs["challenge_rejection_results"]["value"] == {"token_id": "verified", "length": "unverified"}
    assert obs["challenge_consumption"]["value"] == {"token_id": "unknown", "length": "unknown"}
    assert obs["confirmed_input_violations"]["value"] == []
    assert "PRIVATE_VALUE" not in json.dumps(summary)


def test_paired_context_is_applied_after_controls_and_attached_without_consumption_claim(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    sent = []
    def probe(network, requests, timeout):
        req = requests[0]
        sent.append(list(req["body"]["prompt"]))
        if req["body"]["prompt"] == [50272]:
            return [response({"error": {"message": "Token id 50272 is out of vocabulary", "type": "BadRequestError",
                                       "param": None, "code": 400}}, 400)]
        if len(req["body"]["prompt"]) == 128:
            return [rejected()]
        return [completion_response(req)]
    ctx.cprobe = probe
    class PairedCollector:
        def start(self):
            assert not sent
        def finish(self, captured):
            assert [sample["case"] for sample in captured] == ["before", "token_id", "after_token_id", "length", "after_length"]
            assert len(sent) == 5
            return context()
    monkeypatch.setattr(ctx.adapter, "input_length_cause_collector", lambda _ctx: PairedCollector())
    executor = InputValidationExecutor({"vocab_size": 50272, "vocab_source": "EXAMPLE source",
        "max_probe_tokens": 128, "length_cause_evidence": True})
    draft = executor._finish(ctx)
    assert draft.normal_control.succeeded is True
    assert draft.observations["challenge_rejection_results"].value == {"token_id": "verified", "length": "verified"}
    assert draft.observations["challenge_consumption"].value == {"token_id": "unknown", "length": "unknown"}
    assert draft.observations["confirmed_input_violations"].value == []
    artifact = json.loads((tmp_path / "input_response_samples.json").read_text(encoding="utf-8"))
    assert artifact["capture"]["summary"]["length_cause_evidence"]["profile"] == PROFILE
    assert {item.name for item in draft.artifacts} == {"probe_log.jsonl", "input_response_samples.json"}
