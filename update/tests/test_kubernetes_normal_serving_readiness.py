"""OBS-02 tests run the owned probe protocol against a local EXAMPLE service."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.core.run import _normalize
from guard.executors.kubernetes_normal_serving_readiness import KubernetesNormalServingReadinessExecutor
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, load_pack, sha256_hex
from tests.builders import evidence
from tests.test_gpu_a_run_inputs import UPDATE
from tests.test_kubernetes_sampling import World, normal_sampling_inputs

VALUES = {"observation_state_required_components": ["http_listener", "request_selection", "engine_core", "worker"],
          "profiler_backend_kind": "torch", "profiler_required_ranks": ["0", "1"]}
PRIVATE = "EXAMPLE-private-pod-or-response-data"


@pytest.mark.parametrize("version,fault", [
    ("0.30.0", None), ("0.31.0", "integer-port"), ("0.30.0", "missing-ready"),
    ("0.31.0", "wrong-probe-port"), ("0.30.0", "custom-host"), ("0.31.0", "owner-changed"),
    ("0.31.0", "probe-changed-after"), ("0.30.0", "incomplete-output"),
])
def test_obs02_compares_owned_ready_with_exact_normal_response(tmp_path, version, fault):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("Authorization"), request))
            choices = [] if fault == "incomplete-output" else [{"text": PRIVATE, "finish_reason": "length"}]
            body = canonical_json({"model": "fixture-model", "choices": choices,
                                   "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env, target, _, path = normal_sampling_inputs(tmp_path, version)
        env = env.model_copy(update={"model": env.model.model_copy(update={"inventory_sha256": "a" * 64}),
            "runtime": env.runtime.model_copy(update={"gpu_model": "EXAMPLE GPU", "driver": "EXAMPLE driver"})}).seal()
        target["environment_ref"] = env.ref().model_dump(mode="json")
        port = server.server_port
        target["engine"]["trusted_url"] = f"http://127.0.0.1:{port}"
        path.write_bytes(canonical_json(target))
        world = World(target)
        world.actual_probe = True

        def pod_readback(role, obj, after):
            if role == "namespaces":
                for record in obj["items"]:
                    record["kind"] = "Namespace"
                return
            obj["kind"] = "Pod"
            if role != "engine":
                return
            container, = obj["spec"]["containers"]
            selected_port = port + 1 if fault == "wrong-probe-port" else port
            container["ports"] = [{"name": "http", "containerPort": selected_port}]
            probe = {"path": "/health", "port": port if fault == "integer-port" else "http"}
            if fault == "custom-host":
                probe["host"] = "localhost"
            if fault == "probe-changed-after" and after:
                probe["path"] = "/other"
            container["readinessProbe"] = {"httpGet": probe, "periodSeconds": 5}
            container["env"].append({"name": "EXAMPLE_ONLY", "value": PRIVATE})
            if fault == "missing-ready":
                obj["status"]["conditions"] = []
            if fault == "owner-changed" and after:
                obj["metadata"]["uid"] = "replacement"

        world.mutate = pod_readback
        check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["OBS-02"]
        executor = KubernetesNormalServingReadinessExecutor({"normal_serving_readiness_only": True, "target": str(path)},
            runner=world.runner, expected_target_sha256=sha256_hex(path.read_bytes()))
        draft = executor.run(check, env, VALUES, VllmAdapter(), tmp_path / "work")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    blocked = fault in {"missing-ready", "wrong-probe-port", "custom-host"}
    matched = fault in {None, "integer-port"}
    expected = [("/v1/completions", None, {"model": "fixture-model", "prompt": "ping", "max_tokens": 1})]
    assert requests == ([] if blocked else expected)
    assert draft.normal_control.attempted is (not blocked)
    assert draft.normal_control.succeeded is (True if matched else None)
    assert draft.data_origin == DataOrigin.EXAMPLE
    assert draft.status == (ExecStatus.COMPLETED if matched else ExecStatus.ABORTED)
    assert all(not step.attempted for step in draft.challenges)
    observations = _normalize(check, draft)["observations"]
    assert observations["activation_steps"]["collected"] is matched
    assert observations["activation_violations"] == {"value": None, "collected": False}
    assert observations["activation_scope_complete"] == {"value": False, "collected": True}
    assert set(observations["activation_missing"]["value"]) >= {"request_selection", "engine_core", "worker",
                                                               "startup_transition", "failure", "termination", "profiler"}
    raw = draft.artifacts[0].path.read_text()
    assert PRIVATE not in raw and '"ping"' not in raw and "fixture-model" not in raw
    summary = json.loads(raw)
    assert summary["readiness_correspondence_observed"] is matched
    assert draft.comparability.fixture_sha256 and draft.comparability.model_inventory_sha256 == "a" * 64
    assert draft.comparability.gpu == "EXAMPLE GPU" and draft.comparability.driver == "EXAMPLE driver"
    assert draft.comparability.config_sha256 == summary["conditions_sha256"]
    assert bool(draft.comparability.config_sha256) is matched
    if matched:
        row, = observations["activation_steps"]["value"]
        assert row["component"] == "http_listener"
        assert row["readiness_before"]["readiness_probe"] == row["readiness_after"]["readiness_probe"]
        assert row["ordinary_completion"]["outcome"] == 200
    sealed = evidence(check_id=check.id, observations=observations, normal=draft.normal_control.model_dump())
    assessed = RuleTableJudge().judge(check, sealed, check.judge.params, VALUES)
    assert assessed.verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("fault", ["unbound", "disabled", "wrong-policy-scope"])
def test_obs02_rejects_unbound_scope_before_transport(tmp_path, fault):
    env, target, _, path = normal_sampling_inputs(tmp_path, "0.30.0")
    path.write_bytes(canonical_json(target))
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["OBS-02"]
    values = {**VALUES, "observation_state_required_components": ["worker"]} if fault == "wrong-policy-scope" else VALUES

    def no_remote_calls(*args, **kwargs):
        raise AssertionError("OBS-02 must validate the normal scope and target before any remote calls")

    executor = KubernetesNormalServingReadinessExecutor({"normal_serving_readiness_only": fault != "disabled",
        "target": str(path)}, runner=no_remote_calls,
        expected_target_sha256=None if fault == "unbound" else sha256_hex(path.read_bytes()))
    with pytest.raises(ValueError, match="OBS02_NORMAL_(?:SCOPE_INVALID|POLICY_INVALID|TARGET_UNVERIFIED)"):
        executor.run(check, env, values, VllmAdapter(), tmp_path / "work")
