"""LIF-03 normal observation with the pinned client and a local EXAMPLE service."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.core.run import _normalize
from guard.executors.kubernetes_normal_request_latency import KubernetesNormalRequestLatencyExecutor
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, load_pack, sha256_hex
from guard.runtime.kubernetes_http import KubernetesHTTP
from tests.builders import evidence
from tests.test_gpu_a_run_inputs import UPDATE
from tests.test_kubernetes_sampling import World, normal_sampling_inputs

VALUES = {"buffer_max_write_bytes": 1048576, "buffer_max_process_rss_bytes": 17179869184,
          "buffer_max_send_wait_ms": 5000, "buffer_release_deadline_ms": 5000, "buffer_read_chunk_bytes": 4096,
          "buffer_read_interval_ms": 50, "buffer_read_max_reads": 32, "buffer_close_chunk_bytes": 4096,
          "buffer_close_interval_ms": 50, "buffer_close_max_reads": 8, "buffer_debug_mode": "unverified"}
PRIVATE = "EXAMPLE-private-completion"


@pytest.mark.parametrize("version,fault", [("0.30.0", None), ("0.31.0", None), ("0.30.0", "owner-changed"),
                                           ("0.31.0", "unordered"), ("0.30.0", "non-integer")])
def test_lif03_observes_monotonic_bound_timing_from_one_normal_request(tmp_path, version, fault):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("Authorization"), body))
            raw = canonical_json({"model": "fixture-model", "choices": [{"text": PRIVATE, "finish_reason": "length"}],
                                  "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    class TimingWorld(World):
        def runner(self, argv, **kwargs):
            result = super().runner(argv, **kwargs)
            if "exec" in argv and fault in {"unordered", "non-integer"}:
                rows = json.loads(result["stdout_bytes"])
                row, = rows
                if fault == "unordered":
                    row["headers_ms"] = row["end_ms"] + 1
                else:
                    row["dispatch_ms"] = True
                result["stdout_bytes"] = canonical_json(rows)
            return result

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env, target, _, path = normal_sampling_inputs(tmp_path, version)
        env = env.model_copy(update={"model": env.model.model_copy(update={"inventory_sha256": "a" * 64}),
            "runtime": env.runtime.model_copy(update={"gpu_count": 1, "gpu_model": "EXAMPLE GPU", "driver": "EXAMPLE driver"})}).seal()
        target["environment_ref"] = env.ref().model_dump(mode="json")
        target["engine"]["trusted_url"] = f"http://127.0.0.1:{server.server_port}"
        path.write_bytes(canonical_json(target))
        world = TimingWorld(target)
        world.actual_probe = True
        if fault == "owner-changed":
            def change_owner(role, obj, after):
                if role == "engine" and after:
                    obj["metadata"]["uid"] = "replacement"
            world.mutate = change_owner
        check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["LIF-03"]
        executor = KubernetesNormalRequestLatencyExecutor({"normal_request_latency_only": True, "target": str(path)},
            runner=world.runner, expected_target_sha256=sha256_hex(path.read_bytes()))
        draft = executor.run(check, env, VALUES, VllmAdapter(), tmp_path / "work")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert requests == [("/v1/completions", None, {"model": "fixture-model", "prompt": "ping", "max_tokens": 1})]
    assert len(world.execs) == 1
    assert draft.status == (ExecStatus.COMPLETED if fault is None else ExecStatus.ABORTED)
    assert draft.data_origin == DataOrigin.EXAMPLE
    assert draft.normal_control.succeeded is (True if fault is None else None)
    assert all(not step.attempted for step in draft.challenges)
    observations = _normalize(check, draft)["observations"]
    assert observations["activation_violations"] == {"value": None, "collected": False}
    assert observations["activation_scope_complete"] == {"value": False, "collected": True}
    assert set(observations["activation_missing"]["value"]) >= {"process_rss", "send_wait", "queue", "KV", "fairness"}
    assert observations["activation_steps"]["collected"] is (fault is None)
    raw = draft.artifacts[0].path.read_text()
    assert PRIVATE not in raw and '"ping"' not in raw and "fixture-model" not in raw
    summary = json.loads(raw)
    if fault is None:
        timing = summary["observation"]["client_timing"]
        offsets = timing["offsets_ms"]
        assert type(timing["client_elapsed_ms"]) is int
        assert timing["client_elapsed_ms"] == offsets["end_ms"] - offsets["dispatch_ms"]
        assert timing["client_time_to_headers_ms"] == offsets["headers_ms"] - offsets["dispatch_ms"]
        assert timing["client_sent_to_headers_ms"] == offsets["headers_ms"] - offsets["request_sent_ms"]
        assert draft.comparability.config_sha256 == summary["conditions_sha256"]
        assert draft.comparability.model_inventory_sha256 == "a" * 64
    else:
        assert summary["observation"] is None and draft.comparability.config_sha256 is None
    evd = evidence(check_id=check.id, observations=observations, normal=draft.normal_control.model_dump())
    assert RuleTableJudge().judge(check, evd, check.judge.params, VALUES).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("fault", ["unbound", "disabled", "foreign-check"])
def test_lif03_normal_rejects_unapproved_scope_before_transport(tmp_path, fault):
    env, target, _, path = normal_sampling_inputs(tmp_path, "0.30.0")
    path.write_bytes(canonical_json(target))
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["LIF-04" if fault == "foreign-check" else "LIF-03"]

    def no_remote_calls(*args, **kwargs):
        raise AssertionError("LIF-03 must validate its exact scope and pin before transport")

    executor = KubernetesNormalRequestLatencyExecutor({"normal_request_latency_only": fault != "disabled",
        "target": str(path)}, runner=no_remote_calls,
        expected_target_sha256=None if fault == "unbound" else sha256_hex(path.read_bytes()))
    with pytest.raises(ValueError, match="LIF03_NORMAL_(?:SCOPE_INVALID|TARGET_UNVERIFIED)"):
        executor.run(check, env, VALUES, VllmAdapter(), tmp_path / "work")


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
@pytest.mark.parametrize("settings,error", [
    ({}, None),
    ({"max_duration_seconds": 12.5, "kubectl_binary": "example-kubectl", "request_timeout": 3.5}, None),
    ({"max_duration_seconds": 0}, "^LIF03_NORMAL_TARGET_UNVERIFIED$"),
    ({"max_duration_seconds": True}, "^LIF03_NORMAL_TARGET_UNVERIFIED$"),
    ({"max_duration_seconds": 301}, "^LIF03_NORMAL_TARGET_UNVERIFIED$"),
    ({"kubectl_binary": " "}, "^LIF03_NORMAL_TARGET_UNVERIFIED$"),
    ({"request_timeout": 31}, "^request_timeout must be a finite number"),
])
def test_lif03_prepares_effective_settings_without_building_requests(tmp_path, monkeypatch, version, settings, error):
    env, target, _, path = normal_sampling_inputs(tmp_path, version)
    raw = canonical_json(target)
    path.write_bytes(raw)
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["LIF-03"]

    def no_request(*args, **kwargs):
        pytest.fail("LIF-03 preparation must not build or dispatch requests")

    monkeypatch.setattr(KubernetesHTTP, "declare_input_contract", no_request)
    executor = KubernetesNormalRequestLatencyExecutor({"normal_request_latency_only": True,
        "target": str(path), **settings}, runner=no_request)
    digest = sha256_hex(raw)
    if error is None:
        executor.prepared_normal_request_latency(check, env, VALUES, expected_target_sha256=digest)
    else:
        with pytest.raises(ValueError, match=error):
            executor.prepared_normal_request_latency(check, env, VALUES, expected_target_sha256=digest)


@pytest.mark.parametrize("damage", ["wrong-model", "caller-pin", "executor-pin", "no-pin"])
def test_lif03_prepares_only_the_selected_target(tmp_path, damage):
    env, target, _, path = normal_sampling_inputs(tmp_path, "0.30.0")
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["LIF-03"]
    if damage == "wrong-model":
        target["model_mapping"]["revision"] = "another-revision"
    raw = canonical_json(target)
    path.write_bytes(raw)
    digest = sha256_hex(raw)
    wrong = sha256_hex(b"different-owned-target")

    def no_request(*args, **kwargs):
        pytest.fail("LIF-03 preparation must not dispatch")

    executor = KubernetesNormalRequestLatencyExecutor({"normal_request_latency_only": True, "target": str(path)},
        runner=no_request, expected_target_sha256=wrong if damage == "executor-pin" else None)
    pin = {"caller-pin": wrong, "no-pin": None}.get(damage, digest)
    with pytest.raises(ValueError, match="^LIF03_NORMAL_TARGET_UNVERIFIED$"):
        executor.prepared_normal_request_latency(check, env, VALUES, expected_target_sha256=pin)
