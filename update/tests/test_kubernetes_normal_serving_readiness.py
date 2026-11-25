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
READY_TRANSITION = "2026-10-26T00:00:11Z"


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
            else:
                condition, = obj["status"]["conditions"]
                condition["lastTransitionTime"] = READY_TRANSITION
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
        assert row["readiness_before"]["pod_ready_last_transition_at"] == "2026-10-26T00:00:11+00:00"
        assert row["ordinary_completion"]["outcome"] == 200
    sealed = evidence(check_id=check.id, observations=observations, normal=draft.normal_control.model_dump())
    assessed = RuleTableJudge().judge(check, sealed, check.judge.params, VALUES)
    assert assessed.verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("after_transition,matched", [
    ("2026-10-26T00:00:11+00:00", True),
    ("2026-10-26T00:00:12Z", False),
    (None, False),
    ("2026-10-25T23:59:59Z", False),
])
def test_obs02_keeps_response_and_ready_samples_without_matching_transition(tmp_path, after_transition, matched):
    env, target, _, path = normal_sampling_inputs(tmp_path, "0.31.0")
    path.write_bytes(canonical_json(target))
    world = World(target)

    def pod_readback(role, document, after):
        if role == "namespaces":
            for row in document["items"]:
                row["kind"] = "Namespace"
            return
        document["kind"] = "Pod"
        if role != "engine":
            return
        container, = document["spec"]["containers"]
        container["readinessProbe"] = {"httpGet": {"path": "/health", "port": 8000}}
        condition, = document["status"]["conditions"]
        timestamp = after_transition if after else READY_TRANSITION
        if timestamp is not None:
            condition["lastTransitionTime"] = timestamp

    raw_response = canonical_json({"model": "fixture-model", "choices": [{"text": PRIVATE, "finish_reason": "length"}],
                                   "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    def runner(argv, **kwargs):
        result = world.runner(argv, **kwargs)
        if "exec" in argv:
            reply, = json.loads(result["stdout_bytes"])
            result["stdout_bytes"] = canonical_json([{
                "id": reply["id"], "method": "POST", "mode": "json", "outcome": 200,
                "body": raw_response.decode(), "body_bytes": len(raw_response), "body_sha256": sha256_hex(raw_response),
                "capture_complete": True, "eof": True, "body_truncated": False, "utf8_valid": True, "stop_reason": "eof",
            }])
        return result

    world.mutate = pod_readback
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["OBS-02"]
    executor = KubernetesNormalServingReadinessExecutor(
        {"normal_serving_readiness_only": True, "target": str(path)},
        runner=runner, expected_target_sha256=sha256_hex(path.read_bytes()))
    draft = executor.run(check, env, VALUES, VllmAdapter(), tmp_path / "work")
    artifact, = draft.artifacts
    summary_text = artifact.path.read_text()
    summary = json.loads(summary_text)
    scope = summary["observed_scope"]
    assert PRIVATE not in summary_text
    assert len(world.execs) == 1 and draft.data_origin is DataOrigin.EXAMPLE
    assert scope["readiness_before"]["pod_ready"] is scope["readiness_after"]["pod_ready"] is True
    assert scope["ordinary_completion"]["response_sha256"] == sha256_hex(raw_response)
    assert summary["readiness_correspondence_observed"] is matched
    assert draft.observations["activation_steps"].collected is matched
    assert draft.normal_control.succeeded is (True if matched else None)
    assert draft.status is (ExecStatus.COMPLETED if matched else ExecStatus.ABORTED)
    if not matched:
        assert draft.abort_reason == "OBS02_NORMAL_READY_TRANSITION_UNVERIFIED"
        assert "pod_ready_transition_unverified" in draft.observations["activation_missing"].value


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


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
@pytest.mark.parametrize("settings,accepted", [
    ({}, True),
    ({"max_duration_seconds": 1.5, "kubectl_binary": "example-kubectl"}, True),
    ({"max_duration_seconds": 0}, False),
    ({"max_duration_seconds": True}, False),
    ({"kubectl_binary": "  "}, False),
])
def test_obs02_prepares_effective_settings_without_dispatch(tmp_path, version, settings, accepted):
    env, target, _, path = normal_sampling_inputs(tmp_path, version)
    content = canonical_json(target)
    path.write_bytes(content)
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["OBS-02"]

    def no_remote_calls(*args, **kwargs):
        pytest.fail("OBS-02 declaration validation must not dispatch")

    executor = KubernetesNormalServingReadinessExecutor({"normal_serving_readiness_only": True,
        "target": str(path), **settings}, runner=no_remote_calls)
    digest = sha256_hex(content)
    if accepted:
        executor.prepared_normal_serving_readiness(check, env, VALUES, expected_target_sha256=digest)
    else:
        with pytest.raises(ValueError, match="^OBS02_NORMAL_TARGET_UNVERIFIED$"):
            executor.prepared_normal_serving_readiness(check, env, VALUES, expected_target_sha256=digest)


@pytest.mark.parametrize("damage", ["model-mapping", "caller-pin", "executor-pin", "no-pin"])
def test_obs02_prepares_only_the_selected_target(tmp_path, damage):
    env, target, _, path = normal_sampling_inputs(tmp_path, "0.31.0")
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["OBS-02"]
    if damage == "model-mapping":
        target["model_mapping"]["revision"] = "another-revision"
    content = canonical_json(target)
    path.write_bytes(content)
    digest = sha256_hex(content)
    wrong = sha256_hex(b"another-owned-target")

    def no_remote_calls(*args, **kwargs):
        pytest.fail("OBS-02 declaration validation must not dispatch")

    executor = KubernetesNormalServingReadinessExecutor({"normal_serving_readiness_only": True, "target": str(path)},
        runner=no_remote_calls, expected_target_sha256=wrong if damage == "executor-pin" else None)
    pin = {"caller-pin": wrong, "no-pin": None}.get(damage, digest)
    with pytest.raises(ValueError, match="^OBS02_NORMAL_TARGET_UNVERIFIED$"):
        executor.prepared_normal_serving_readiness(check, env, VALUES, expected_target_sha256=pin)


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
def test_obs02_run_requires_the_same_regular_target_as_preparation(tmp_path, monkeypatch, version):
    env, target, _, path = normal_sampling_inputs(tmp_path, version)
    content = canonical_json(target)
    path.write_bytes(content)
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["OBS-02"]

    def no_io(*args, **kwargs):
        pytest.fail("OBS-02 invalid saved target reached transport")

    executor = KubernetesNormalServingReadinessExecutor({"normal_serving_readiness_only": True, "target": str(path)},
        runner=no_io, expected_target_sha256=sha256_hex(content))
    executor.prepared_normal_serving_readiness(check, env, VALUES)
    replacement = path.with_name("same-target-bytes.json")
    path.rename(replacement)
    path.symlink_to(replacement)
    monkeypatch.setattr(executor, "_target_transport", no_io)
    with pytest.raises(ValueError, match="^OBS02_NORMAL_TARGET_UNVERIFIED$"):
        executor.prepared_normal_serving_readiness(check, env, VALUES)
    with pytest.raises(ValueError, match="^OBS02_NORMAL_TARGET_UNVERIFIED$"):
        executor.run(check, env, VALUES, VllmAdapter(), tmp_path / "work")
