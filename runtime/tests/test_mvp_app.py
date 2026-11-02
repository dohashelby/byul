"""HTTP tests for the M0 gateway status skeleton."""

from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.cli import main
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from serving_runtime.enforcement.execpolicy import ExecPolicy
from serving_runtime.enforcement.intent import load_intent


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_POLICY = ROOT / "examples/mvp/policy.json"
INTENT = ROOT / "examples/enforcement/intent-v1.1.example.json"


def stale_timeline(path: Path) -> None:
    path.write_text(json.dumps({
        "schema_version": "runtime-phase-timeline/1.0", "entries": [],
        "collector": {"state": "running", "pod_uid": "old", "pod_name": "old-engine",
                      "written_at": "2026-01-01T00:00:00Z", "last_log_at": None,
                      "log_delivery_lag_seconds": None},
    }), encoding="utf-8")


class StaleTimelineDecisionTests(unittest.TestCase):
    def test_stale_timeline_has_unknown_phase_without_socket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            timeline = Path(directory) / "phase-timeline.json"
            stale_timeline(timeline)
            verdict = ExecPolicy(load_intent(INTENT), timeline_path=timeline).classify_exec(
                binary="/usr/bin/uname", parent=None, image=None,
                observed_at="2026-11-02T10:00:00Z",
            )
            self.assertEqual(verdict["exec_verdict"], "phase_unknown")
            self.assertIs(verdict["phase_timeline_fresh"], False)
            self.assertEqual(verdict["phase_source_error"], "stale")


class GatewayAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        try:
            self.engine = MockEngine(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.engine.stop)
        state = replace(load_policy(EXAMPLE_POLICY), path='<policy&"path">')
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(EXAMPLE_POLICY),
            data_dir=self.temporary.name,
        )
        self.gateway = GatewayApp(config, state, self.engine.address).start()
        self.addCleanup(self.gateway.stop)
        host, port = self.gateway.address
        self.assertEqual(host, "127.0.0.1")
        self.base_url = f"http://{host}:{port}"

    def test_status_json_contains_policy_and_engine_check(self) -> None:
        with closing(urlopen(self.base_url + "/api/status", timeout=2)) as response:
            status = json.loads(response.read().decode("utf-8"))
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["mode"], "mock_engine")
        self.assertTrue(status["policy"]["valid"])
        self.assertEqual(status["policy"]["policy_version"], "1")
        self.assertEqual(len(status["policy"]["sha256"]), 64)
        self.assertIn("loaded_at", status["policy"])
        self.assertTrue(status["mock_engine"]["healthy"])
        self.assertIn("checked_at", status["mock_engine"])
        self.assertTrue(status["record_store"]["ok"])
        self.assertEqual(status["record_store"]["data_dir"], self.temporary.name)
        self.assertEqual(len(status["observation_session_id"]), 32)

    def test_stale_observation_timeline_does_not_block_chat(self) -> None:
        timeline = Path(self.temporary.name) / "phase-timeline.json"
        stale_timeline(timeline)
        feed = Path(self.temporary.name) / "tetragon.jsonl"
        feed.touch()
        config = MVPConfig(
            gateway_port=0, engine_port=0, policy_path=str(EXAMPLE_POLICY),
            data_dir=str(Path(self.temporary.name) / "with-timeline"),
            observation_feeds=(("tetragon", str(feed)),),
            observation_intent_path=str(INTENT),
            observation_timeline_path=str(timeline),
        )
        gateway = GatewayApp(config, load_policy(EXAMPLE_POLICY), self.engine.address).start()
        self.addCleanup(gateway.stop)
        host, port = gateway.address
        request = Request(f"http://{host}:{port}/v1/chat/completions",
                          data=json.dumps({"model": "test-model", "messages": [{"role": "user", "content": "hello"}],
                                           "max_tokens": 4}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        with closing(urlopen(request, timeout=3)) as response:
            self.assertEqual(response.status, 200)

    def test_gateway_serves_no_dashboard(self) -> None:
        # 화면은 byul platform이 맡는다. Gateway는 조회 API(JSON)만 낸다.
        for path in ("/", "/static/app.js", "/static/app.css", "/static/vendor/chart.umd.min.js"):
            with self.subTest(path=path):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(self.base_url + path, timeout=2)
                self.assertEqual(caught.exception.code, 404)
                self.assertEqual(json.loads(caught.exception.read())["error"]["code"], "NOT_FOUND")

    def test_invalid_chat_post_has_m2_error_and_request_id(self) -> None:
        request = Request(
            self.base_url + "/v1/chat/completions",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(len(caught.exception.headers["X-Request-ID"]), 32)
        body = json.loads(caught.exception.read().decode("utf-8"))
        self.assertEqual(body["error"]["code"], "INVALID_MODEL")
        self.assertEqual(body["error"]["request_id"], caught.exception.headers["X-Request-ID"])

class MVPCommandTests(unittest.TestCase):
    def test_non_loopback_binding_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            GatewayApp(
                MVPConfig(host="0.0.0.0", gateway_port=0),
                load_policy(EXAMPLE_POLICY),
                ("127.0.0.1", 1),
            )

    def test_cli_forwards_mvp_options(self) -> None:
        with patch("serving_runtime.mvp.app.run") as run:
            result = main([
                "mvp",
                "--policy", "custom-policy.json",
                "--gateway-port", "18080",
                "--engine-port", "18088",
                "--data-dir", "/tmp/runtime-state",
                "--relay-timeout", "0.25",
                "--stream-idle-timeout", "1.5",
                "--request-deadline", "9",
                "--max-concurrency", "4",
                "--max-connections", "12",
                "--request-header-timeout", "0.75",
                "--request-body-timeout", "3",
                "--environment", "docker-cpu",
                "--run-id", "gateway-run",
                "--revocations", "revocations.json",
                "--policy-approvals", "policy-approvals.json",
                "--baseline", "baseline.json",
                "--manifest", "manifest.json",
                "--collector-interval", "0.5",
                "--collector-stale-after", "2",
                "--lab-report", "reports",
                "--observation-feed", "hubble=hubble.jsonl",
                "--observation-feed", "tetragon=tetragon.jsonl",
                "--observation-heartbeat", "hubble=hubble.heartbeat.json",
                "--observation-heartbeat", "tetragon=tetragon.heartbeat.json",
                "--observation-environment", "kind-lab-wsl",
                "--observation-run-id", "lab-run",
                "--observation-intent", "intent.json",
                "--cilium-policy-status",
                "--kube-context", "formal-cluster",
            ])
        self.assertEqual(result, 0)
        config = run.call_args.args[0]
        self.assertEqual(config.policy_path, "custom-policy.json")
        self.assertEqual(config.gateway_port, 18080)
        self.assertEqual(config.engine_port, 18088)
        self.assertEqual(config.data_dir, "/tmp/runtime-state")
        self.assertEqual(config.relay_timeout_s, 0.25)
        self.assertEqual(config.stream_idle_timeout_s, 1.5)
        self.assertEqual(config.request_deadline_s, 9)
        self.assertEqual(config.max_concurrent_requests, 4)
        self.assertEqual(config.max_connections, 12)
        self.assertEqual(config.request_header_timeout_s, 0.75)
        self.assertEqual(config.request_body_timeout_s, 3)
        self.assertEqual((config.environment, config.run_id), ("docker-cpu", "gateway-run"))
        self.assertEqual(config.revocations_path, "revocations.json")
        self.assertEqual(config.policy_approvals_path, "policy-approvals.json")
        self.assertEqual(config.baseline_path, "baseline.json")
        self.assertEqual(config.manifest_path, "manifest.json")
        self.assertEqual(config.collector_interval_s, 0.5)
        self.assertEqual(config.collector_stale_after_s, 2)
        self.assertEqual(config.lab_report_path, "reports")
        self.assertEqual(config.kube_context, "formal-cluster")
        self.assertEqual(
            config.observation_feeds,
            (("hubble", "hubble.jsonl"), ("tetragon", "tetragon.jsonl")),
        )
        self.assertEqual(
            config.observation_heartbeats,
            (
                ("hubble", "hubble.heartbeat.json"),
                ("tetragon", "tetragon.heartbeat.json"),
            ),
        )
        self.assertEqual(config.observation_environment, "kind-lab-wsl")
        self.assertEqual(config.observation_run_id, "lab-run")
        self.assertEqual(config.observation_intent_path, "intent.json")
        self.assertTrue(config.cilium_policy_status)

    def test_timeout_and_concurrency_config_values_are_validated(self) -> None:
        for options in (
            {"stream_idle_timeout_s": 0},
            {"request_deadline_s": 0},
            {"max_concurrent_requests": 0},
            {"max_concurrent_requests": True},
            {"max_connections": 0},
            {"max_connections": True},
            {"request_header_timeout_s": 0},
            {"request_body_timeout_s": 0},
            {"collector_interval_s": 0},
            {"collector_stale_after_s": 0},
            {"cilium_timeout_s": 0},
            {"observation_feeds": (("hubble", "one"), ("hubble", "two"))},
            {"observation_heartbeats": (("hubble", "heartbeat"),)},
            {
                "observation_feeds": (("hubble", "feed"),),
                "observation_heartbeats": (
                    ("hubble", "one"),
                    ("hubble", "two"),
                ),
            },
            {"observation_intent_path": "intent.json"},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                MVPConfig(**options)


if __name__ == "__main__":
    unittest.main()
