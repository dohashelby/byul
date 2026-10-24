"""Tests for the SGLang backend adapter, using a test-only fake SGLang backend.

The fake imitates SGLang v0.5.21 routes and metric names. Passing these tests
shows the adapter is ready to connect; it does not prove a real SGLang works.
"""

from __future__ import annotations

from contextlib import closing
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.cli import main
from serving_runtime.mvp.app import GatewayApp, run
from serving_runtime.mvp.backend import BackendConfig, load_backend_config
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.fake_backend import FakeBackend
from serving_runtime.mvp.engine_client import EngineClient
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.relay import relay_payload
from serving_runtime.mvp.store import JsonlStore
from serving_runtime.mvp.validator import validate_request


ROOT = Path(__file__).resolve().parents[1]
SGLANG_EXAMPLE = ROOT / "examples/mvp/backend-sglang.example.json"
VLLM_EXAMPLE = ROOT / "examples/mvp/backend-vllm.example.json"
POLICY = ROOT / "examples/mvp/policy.json"
CONTEXT_POLICY = ROOT / "examples/mvp/policy-v1.1-context.json"
SGLANG_DOC = "https://github.com/sgl-project/sglang/blob/v0.5.21/python/sglang/srt/observability/metrics_collector.py"


def sglang_document(base_url: str, **updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "runtime-backend/1.0",
        "kind": "sglang",
        "base_url": base_url,
        "model_map": {"test-model": "backend-test-model"},
        "metrics_url": base_url + "/metrics",
        "timeouts": {
            "relay_timeout_s": 0.5,
            "stream_idle_timeout_s": 0.5,
            "request_deadline_s": 2,
        },
        "metrics_map": {
            "requests.running": {
                "metric": "sglang:num_running_reqs",
                "type": "gauge",
                "unit": "requests",
                "source_doc": SGLANG_DOC,
                "verified": False,
            },
            "tokens.generated": {
                "metric": "sglang:generation_tokens_total",
                "type": "counter",
                "unit": "tokens",
                "source_doc": SGLANG_DOC,
                "verified": False,
            },
        },
    }
    document.update(updates)
    return document


def chat(**updates: object) -> bytes:
    document: dict[str, object] = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hello"}],
    }
    document.update(updates)
    return json.dumps(document).encode("utf-8")


class SglangConfigTests(unittest.TestCase):
    def write(self, document: dict[str, object]) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "backend.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_example_loads_with_sglang_defaults(self) -> None:
        config = load_backend_config(SGLANG_EXAMPLE, environ={})
        self.assertEqual(config.kind, "sglang")
        self.assertTrue(config.external)
        self.assertIsNone(config.version_path)
        self.assertEqual(config.output_tokens_field, "max_completion_tokens")
        self.assertEqual(config.target.port, 30000)
        self.assertEqual(
            config.metrics_map["requests.running"]["metric"], "sglang:num_running_reqs"
        )

    def test_vllm_example_is_unchanged(self) -> None:
        config = load_backend_config(VLLM_EXAMPLE, environ={})
        self.assertEqual(config.kind, "vllm")
        self.assertEqual(config.version_path, "/version")
        self.assertEqual(config.output_tokens_field, "max_tokens")

    def test_version_path_default_and_override(self) -> None:
        default = load_backend_config(self.write(sglang_document("http://127.0.0.1:1")), environ={})
        self.assertIsNone(default.version_path)
        explicit = load_backend_config(
            self.write(sglang_document("http://127.0.0.1:1", version_path="/model_info")),
            environ={},
        )
        self.assertEqual(explicit.version_path, "/model_info")
        vllm_null = load_backend_config(
            self.write(dict(sglang_document("http://127.0.0.1:1"), kind="vllm", version_path=None)),
            environ={},
        )
        self.assertIsNone(vllm_null.version_path)
        with self.assertRaises(ValueError):
            load_backend_config(
                self.write(sglang_document("http://127.0.0.1:1", version_path="relative")),
                environ={},
            )

    def test_unknown_kind_is_rejected(self) -> None:
        for kind in ("tensorrt", "mock", "SGLang", "", [], {}):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(
                    ValueError, "backend kind must be vllm or sglang"
                ):
                    load_backend_config(
                        self.write(sglang_document("http://127.0.0.1:1", kind=kind)),
                        environ={},
                    )

    def test_unhashable_kind_is_a_clean_cli_failure(self) -> None:
        backend_path = self.write(
            sglang_document("http://127.0.0.1:1", kind=[])
        )
        metrics_path = backend_path.with_name("metrics.txt")
        metrics_path.write_text("", encoding="utf-8")
        commands = (
            [
                "mvp",
                "--backend",
                "sglang",
                "--backend-config",
                str(backend_path),
                "--policy",
                str(POLICY),
                "--data-dir",
                str(backend_path.parent / "data"),
            ],
            [
                "metrics-check",
                "--backend-config",
                str(backend_path),
                "--input",
                str(metrics_path),
            ],
        )
        for command in commands:
            with self.subTest(command=command[0]):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = main(command)
                self.assertEqual(code, 1)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("backend kind must be vllm or sglang", stderr.getvalue())

    def test_mode_and_backend_kind_must_match(self) -> None:
        with self.assertRaises(ValueError):
            MVPConfig(backend="sglang")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = MVPConfig(
            gateway_port=0,
            backend="sglang",
            backend_config_path=str(VLLM_EXAMPLE),
            policy_path=str(POLICY),
            data_dir=directory.name,
        )
        with self.assertRaisesRegex(ValueError, "sglang mode requires a sglang backend config"):
            GatewayApp(config, load_policy(POLICY))


class SglangRelayPayloadTests(unittest.TestCase):
    def test_output_limit_uses_sglang_field_name(self) -> None:
        decision = validate_request(chat(max_tokens=64), "application/json", load_policy(POLICY))
        backend = load_backend_config(SGLANG_EXAMPLE, environ={})
        relayed = json.loads(relay_payload(backend, json.loads(chat(max_tokens=64)), decision, stream=False))
        self.assertEqual(relayed["max_completion_tokens"], 64)
        self.assertNotIn("max_tokens", relayed)
        self.assertEqual(relayed["model"], "example-backend-model-id")

    def test_vllm_and_mock_keep_max_tokens(self) -> None:
        decision = validate_request(chat(max_tokens=64), "application/json", load_policy(POLICY))
        for backend in (
            load_backend_config(VLLM_EXAMPLE, environ={}),
            BackendConfig.mock(("127.0.0.1", 1)),
        ):
            with self.subTest(kind=backend.kind):
                relayed = json.loads(
                    relay_payload(backend, json.loads(chat(max_tokens=64)), decision, stream=False)
                )
                self.assertEqual(relayed["max_tokens"], 64)
                self.assertNotIn("max_completion_tokens", relayed)

    def test_tokenize_payload_is_backend_specific(self) -> None:
        class CapturingClient(EngineClient):
            def __init__(self, backend: BackendConfig) -> None:
                super().__init__(backend, 1.0)
                self.payload = None

            def request(self, method, path, **kwargs):
                self.payload = json.loads(kwargs["body"])
                return 200, {}, b'{"count": 3}'

        messages = [{"role": "user", "content": "hello"}]
        for path, expected_generation_prompt in (
            (VLLM_EXAMPLE, True),
            (SGLANG_EXAMPLE, False),
        ):
            backend = load_backend_config(path, environ={})
            client = CapturingClient(backend)
            self.assertEqual(
                client.count_input_tokens("request", messages, "backend-model"), 3
            )
            self.assertEqual(client.payload["model"], "backend-model")
            self.assertEqual(client.payload["messages"], messages)
            self.assertEqual(
                "add_generation_prompt" in client.payload,
                expected_generation_prompt,
            )

        mock_client = CapturingClient(BackendConfig.mock(("127.0.0.1", 1)))
        mock_client.count_input_tokens("request", messages, "test-model")
        self.assertNotIn("add_generation_prompt", mock_client.payload)

    def test_launcher_names_the_sglang_backend(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = MVPConfig(
            gateway_port=0,
            backend="sglang",
            backend_config_path=str(SGLANG_EXAMPLE),
            policy_path=str(POLICY),
            data_dir=directory.name,
        )
        gateway = SimpleNamespace(
            address=("127.0.0.1", 18080),
            backend=SimpleNamespace(base_url="http://127.0.0.1:30000", kind="sglang"),
        )
        gateway.stop = lambda: None
        output = io.StringIO()
        with (
            patch("serving_runtime.mvp.app.GatewayApp") as gateway_class,
            patch("serving_runtime.mvp.app.threading.Event") as event_class,
            redirect_stdout(output),
        ):
            gateway_class.return_value.start.return_value = gateway
            event_class.return_value.wait.return_value = None
            run(config)
        self.assertIn(
            "External SGLang backend: http://127.0.0.1:30000",
            output.getvalue(),
        )


class SglangGatewayHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        try:
            self.fake = FakeBackend(port=0, flavor="sglang").start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.fake.stop)

    def start(self, policy: Path = POLICY) -> GatewayApp:
        backend_path = self.directory / "backend.json"
        backend_path.write_text(json.dumps(sglang_document(self.fake.base_url)), encoding="utf-8")
        config = MVPConfig(
            gateway_port=0,
            backend="sglang",
            backend_config_path=str(backend_path),
            policy_path=str(policy),
            data_dir=str(self.directory / "data"),
            collector_interval_s=0.05,
            collector_stale_after_s=1,
        )
        try:
            gateway = GatewayApp(
                config, load_policy(policy), backend_config=load_backend_config(backend_path, environ={})
            ).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(gateway.stop)
        self.gateway = gateway
        return gateway

    def url(self, path: str) -> str:
        return f"http://{self.gateway.address[0]}:{self.gateway.address[1]}{path}"

    def post(self, body: bytes) -> tuple[int, dict[str, str], bytes]:
        request = Request(
            self.url("/v1/chat/completions"),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as error:
            with closing(error):
                return error.code, dict(error.headers), error.read()

    def get_json(self, path: str) -> dict[str, object]:
        with urlopen(self.url(path), timeout=3) as response:
            return json.loads(response.read())

    def received(self, request_id: str) -> list[dict[str, object]]:
        return [item for item in self.fake.requests if item["request_id"] == request_id]

    def wait_collector(self, source: str) -> dict[str, object]:
        stop_at = time.monotonic() + 3
        while time.monotonic() < stop_at:
            for item in self.get_json("/api/collectors")["collectors"]:
                if item["source"] == source and item["state"] == "ok":
                    return item
            time.sleep(0.05)
        self.fail(f"collector {source} did not become ok")

    def test_general_request_is_relayed_with_sglang_fields(self) -> None:
        self.start()
        status, headers, _ = self.post(chat(max_tokens=32))
        self.assertEqual(status, 200)
        received = self.received(headers["X-Request-ID"])
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["model"], "backend-test-model")
        self.assertEqual(received[0]["max_completion_tokens"], 32)
        self.assertIsNone(received[0]["max_tokens"])
        records, _ = JsonlStore(self.directory / "data" / "gateway_records.jsonl").read_all()
        final = [r for r in records if r["request_id"] == headers["X-Request-ID"] and r["phase"] == "final"][-1]
        self.assertEqual(final["backend_kind"], "sglang")
        self.assertEqual(final["data_origin"], "external_backend")
        detail = self.get_json(f"/api/requests/{headers['X-Request-ID']}")
        self.assertEqual(detail["evidence_state"], "gateway_only")

    def test_stream_is_relayed(self) -> None:
        self.start()
        status, headers, body = self.post(chat(stream=True))
        self.assertEqual(status, 200)
        self.assertIn(b"[DONE]", body)
        self.assertTrue(self.received(headers["X-Request-ID"])[0]["stream"])

    def test_denied_request_is_not_relayed(self) -> None:
        self.start()
        before = self.fake.request_count
        status, _, body = self.post(chat(max_tokens=129))
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "MAX_TOKENS_EXCEEDED")
        self.assertEqual(self.fake.request_count, before)

    def test_context_check_uses_sglang_tokenize(self) -> None:
        self.start(CONTEXT_POLICY)
        calls = self.fake.tokenize_calls
        status, _, _ = self.post(chat(max_tokens=16))
        self.assertEqual(status, 200)
        self.assertGreater(self.fake.tokenize_calls, calls)

    def test_status_and_collectors_report_sglang(self) -> None:
        self.start()
        status = self.get_json("/api/status")
        self.assertEqual(status["backend_kind"], "sglang")
        self.assertEqual(status["mode"], "external_sglang_backend")
        info = self.wait_collector("engine_info")["last_value"]
        self.assertEqual(info["engine.kind"], "sglang")
        self.assertEqual(info["engine.version"], "not_collected")
        metrics = self.wait_collector("metrics")["last_value"]
        self.assertEqual(metrics["requests.running"]["state"], "ok")
        self.assertEqual(metrics["tokens.generated"]["state"], "ok")


if __name__ == "__main__":
    unittest.main()
