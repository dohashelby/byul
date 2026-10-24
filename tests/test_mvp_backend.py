"""S3A strict backend config, external relay, collection, and evidence tests."""

from __future__ import annotations

from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.backend import (
    BackendConfig,
    is_valid_credential_value,
    load_backend_config,
    parse_prometheus_metrics,
)
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.engine_client import EngineClient
from serving_runtime.mvp.fake_backend import FakeBackend
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.store import JsonlStore


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "examples/mvp/policy.json"
DOC = "https://docs.vllm.ai/en/latest/usage/metrics/"


def backend_document(base_url: str, **updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "runtime-backend/1.0",
        "kind": "vllm",
        "base_url": base_url,
        "model_map": {"test-model": "backend-test-model"},
        "health_path": "/health",
        "models_path": "/v1/models",
        "version_path": "/version",
        "metrics_url": base_url.removesuffix("/prefix") + "/metrics",
        "timeouts": {
            "relay_timeout_s": 0.2,
            "stream_idle_timeout_s": 0.3,
            "request_deadline_s": 2,
        },
        "metrics_map": {
            "requests.running": {
                "metric": "vllm:num_requests_running",
                "type": "gauge",
                "unit": "requests",
                "source_doc": DOC,
                "verified": False,
            },
            "missing.metric": {
                "metric": "vllm:not_in_fake_response",
                "type": "gauge",
                "unit": "items",
                "source_doc": DOC,
                "verified": False,
            },
        },
    }
    document.update(updates)
    return document


def chat_body(**updates: object) -> bytes:
    document: dict[str, object] = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 64,
    }
    document.update(updates)
    return json.dumps(document).encode("utf-8")


class BackendConfigTests(unittest.TestCase):
    def write(self, directory: Path, document: object, name: str = "backend.json") -> Path:
        path = directory / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_strict_config_prefix_timeouts_and_metric_parser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = self.write(
                directory,
                backend_document("https://example.invalid:8443/prefix"),
            )
            config = load_backend_config(path, environ={})
            self.assertEqual(config.kind, "vllm")
            self.assertEqual(config.target.path("/v1/chat/completions"), "/prefix/v1/chat/completions")
            self.assertEqual(config.timeouts["relay_timeout_s"], 0.2)
            self.assertEqual(config.credential_status, "configured")
            parsed = parse_prometheus_metrics(
                b"vllm:num_requests_running 2\nunrelated 9\n", config.metrics_map
            )
            self.assertEqual(parsed["requests.running"]["state"], "ok")
            self.assertEqual(parsed["requests.running"]["samples"][0]["value"], 2.0)
            self.assertEqual(parsed["missing.metric"]["state"], "unsupported_or_absent")

    def test_unknown_duplicate_and_invalid_security_options_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cases = [
                backend_document("ftp://example.invalid"),
                backend_document("http://user:pass@example.invalid"),
                backend_document("http://example.invalid", tls_verify=False),
                backend_document(
                    "http://example.invalid",
                    credential={"env": "TOKEN", "file": "token"},
                ),
                backend_document(
                    "http://example.invalid",
                    metrics_map={
                        "x": {
                            "metric": "valid_name",
                            "type": "gauge",
                            "unit": "x",
                            "source_doc": DOC,
                            "verified": True,
                        }
                    },
                ),
            ]
            for index, document in enumerate(cases):
                with self.subTest(index=index), self.assertRaises(ValueError):
                    load_backend_config(self.write(directory, document, f"bad-{index}.json"))
            duplicate = directory / "duplicate.json"
            duplicate.write_text(
                '{"schema_version":"runtime-backend/1.0","kind":"vllm",'
                '"base_url":"http://one","base_url":"http://two","model_map":{}}',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_backend_config(duplicate)

    def test_credentials_are_read_once_and_permission_state_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            env_path = self.write(
                directory,
                backend_document(
                    "http://example.invalid", credential={"env": "BACKEND_TOKEN"}
                ),
            )
            missing = load_backend_config(env_path, environ={})
            configured = load_backend_config(env_path, environ={"BACKEND_TOKEN": "secret"})
            self.assertEqual(missing.credential_status, "missing")
            self.assertEqual(configured.credential_status, "configured")
            self.assertNotIn("secret", repr(configured))
            self.assertNotIn("secret", json.dumps(configured.safe_status()))

            credential = directory / "credential"
            credential.write_text("file-secret", encoding="utf-8")
            credential.chmod(0o644)
            file_path = self.write(
                directory,
                backend_document(
                    "http://example.invalid", credential={"file": "credential"}
                ),
                "file-config.json",
            )
            insecure = load_backend_config(file_path)
            self.assertEqual(insecure.credential_status, "insecure_file")
            credential.write_text("changed-secret", encoding="utf-8")
            self.assertEqual(insecure.authorization, "Bearer file-secret")

    def test_metrics_credential_is_separate_even_on_the_same_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            inference_only = load_backend_config(
                self.write(
                    directory,
                    backend_document(
                        "http://example.invalid",
                        credential={"env": "INFERENCE_TOKEN"},
                    ),
                ),
                environ={"INFERENCE_TOKEN": "inference-secret"},
            )
            self.assertEqual(
                inference_only.request_headers()["Authorization"],
                "Bearer inference-secret",
            )
            self.assertNotIn(
                "Authorization", inference_only.request_headers(metrics=True)
            )
            self.assertEqual(
                inference_only.metrics_credential_status, "not_configured"
            )

            separate = load_backend_config(
                self.write(
                    directory,
                    backend_document(
                        "http://example.invalid",
                        credential={"env": "INFERENCE_TOKEN"},
                        metrics_credential={"env": "METRICS_TOKEN"},
                    ),
                    "separate.json",
                ),
                environ={
                    "INFERENCE_TOKEN": "inference-secret",
                    "METRICS_TOKEN": "metrics-secret",
                },
            )
            self.assertEqual(
                separate.request_headers(metrics=True)["Authorization"],
                "Bearer metrics-secret",
            )
            self.assertNotIn("inference-secret", repr(separate))
            self.assertNotIn("metrics-secret", repr(separate))

    def test_engine_client_metrics_request_never_uses_inference_authorization(self) -> None:
        class Response:
            status = 200

            @staticmethod
            def getheaders() -> list[tuple[str, str]]:
                return []

            @staticmethod
            def read() -> bytes:
                return b"metric 1\n"

        class Connection:
            def __init__(self) -> None:
                self.headers = None

            def request(self, _method, _path, *, body, headers) -> None:
                self.headers = headers

            @staticmethod
            def getresponse() -> Response:
                return Response()

            @staticmethod
            def close() -> None:
                pass

        class Target:
            display_url = "http://same-host.invalid"
            host = "same-host.invalid"
            port = 80

            def __init__(self, connection: Connection) -> None:
                self._connection = connection

            def connection(self, _timeout: float) -> Connection:
                return self._connection

            @staticmethod
            def path(route: str) -> str:
                return route

            def url(self, route: str) -> str:
                return self.display_url + route

        connection = Connection()
        target = Target(connection)
        config = BackendConfig(
            schema_version="runtime-backend/1.0",
            kind="vllm",
            target=target,
            model_map={},
            metrics_target=target,
            metrics_path="/metrics",
            _authorization="Bearer inference-secret",
        )
        EngineClient(config, 1).request("GET", "", metrics=True)
        self.assertNotIn("Authorization", connection.headers)

    def test_credentials_with_header_unsafe_characters_are_invalid_and_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            env_path = self.write(
                directory,
                backend_document(
                    "http://example.invalid", credential={"env": "BACKEND_TOKEN"}
                ),
            )
            credential = directory / "credential"
            file_path = self.write(
                directory,
                backend_document(
                    "http://example.invalid", credential={"file": "credential"}
                ),
                "file-invalid.json",
            )
            invalid_values = (
                " leading-space",
                "trailing-space ",
                "internal\nnewline",
                "internal\rreturn",
                "trailing-return\r",
                "two-newlines\n\n",
                "two-crlf\r\n\r\n",
                "has\x00null",
                "non-ascii-한",
            )
            for secret in invalid_values:
                with self.subTest(source="env", secret=repr(secret)):
                    config = load_backend_config(
                        env_path, environ={"BACKEND_TOKEN": secret}
                    )
                    self.assertEqual(config.credential_status, "invalid")
                    self.assertIsNone(config.authorization)
                    self.assertNotIn(secret, repr(config))
                    self.assertNotIn(secret, json.dumps(config.safe_status()))
                with self.subTest(source="file", secret=repr(secret)):
                    credential.write_bytes(secret.encode("utf-8"))
                    credential.chmod(0o600)
                    config = load_backend_config(file_path)
                    self.assertEqual(config.credential_status, "invalid")
                    self.assertIsNone(config.authorization)

            self.assertTrue(is_valid_credential_value("visible-ASCII_123"))
            self.assertFalse(is_valid_credential_value("not valid"))

    def test_credentials_allow_one_terminal_lf_or_crlf_for_env_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            env_path = self.write(
                directory,
                backend_document(
                    "http://example.invalid", credential={"env": "BACKEND_TOKEN"}
                ),
            )
            credential = directory / "credential"
            file_path = self.write(
                directory,
                backend_document(
                    "http://example.invalid", credential={"file": "credential"}
                ),
                "file-newline.json",
            )
            for ending in ("\n", "\r\n"):
                with self.subTest(source="env", ending=repr(ending)):
                    config = load_backend_config(
                        env_path, environ={"BACKEND_TOKEN": f"abc123{ending}"}
                    )
                    self.assertEqual(config.credential_status, "configured")
                    self.assertEqual(config.authorization, "Bearer abc123")
                with self.subTest(source="file", ending=repr(ending)):
                    credential.write_bytes(f"abc123{ending}".encode("ascii"))
                    credential.chmod(0o600)
                    config = load_backend_config(file_path)
                    self.assertEqual(config.credential_status, "configured")
                    self.assertEqual(config.authorization, "Bearer abc123")

    def test_mvp_config_requires_vllm_config_and_keeps_mock_default(self) -> None:
        self.assertEqual(MVPConfig().backend, "mock")
        with self.assertRaises(ValueError):
            MVPConfig(backend="vllm")
        with self.assertRaises(ValueError):
            MVPConfig(backend="mock", backend_config_path="unused.json")


class ExternalBackendHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        try:
            self.fake = FakeBackend(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.fake.stop)
        self.gateways: list[GatewayApp] = []

    def tearDown(self) -> None:
        for gateway in reversed(self.gateways):
            gateway.stop()

    def start_gateway(
        self,
        *,
        document: dict[str, object] | None = None,
        environ: dict[str, str] | None = None,
        data_dir: Path | None = None,
        collector_interval: float = 0.05,
    ) -> GatewayApp:
        backend_path = self.directory / f"backend-{len(self.gateways)}.json"
        backend_path.write_text(
            json.dumps(document or backend_document(self.fake.base_url)),
            encoding="utf-8",
        )
        config = MVPConfig(
            gateway_port=0,
            backend="vllm",
            backend_config_path=str(backend_path),
            policy_path=str(POLICY),
            data_dir=str(data_dir or self.directory / "data"),
            collector_interval_s=collector_interval,
            collector_stale_after_s=1,
        )
        backend = load_backend_config(backend_path, environ=environ or {})
        try:
            gateway = GatewayApp(
                config, load_policy(POLICY), backend_config=backend
            ).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.gateways.append(gateway)
        return gateway

    @staticmethod
    def url(gateway: GatewayApp, path: str) -> str:
        return f"http://{gateway.address[0]}:{gateway.address[1]}{path}"

    def request(
        self,
        gateway: GatewayApp,
        body: bytes,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        request = Request(
            self.url(gateway, "/v1/chat/completions"),
            data=body,
            headers=request_headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as error:
            with closing(error):
                return error.code, dict(error.headers), error.read()

    def get_json(self, gateway: GatewayApp, path: str) -> dict[str, object]:
        with urlopen(self.url(gateway, path), timeout=3) as response:
            result = json.loads(response.read())
        self.assertIsInstance(result, dict)
        return result

    def records(self, data_dir: Path | None = None) -> list[dict[str, object]]:
        records, corrupt = JsonlStore(
            (data_dir or self.directory / "data") / "gateway_records.jsonl"
        ).read_all()
        self.assertEqual(corrupt, [])
        return records

    def wait_collector(self, gateway: GatewayApp, source: str, state: str) -> dict[str, object]:
        stop_at = time.monotonic() + 2
        while time.monotonic() < stop_at:
            payload = self.get_json(gateway, "/api/collectors")
            item = next(
                (
                    entry
                    for entry in payload["collectors"]
                    if entry["source"] == source and entry["state"] == state
                ),
                None,
            )
            if item is not None:
                return item
            time.sleep(0.02)
        self.fail(f"collector {source} did not reach {state}")

    def test_general_relay_maps_model_and_uses_external_evidence_text(self) -> None:
        gateway = self.start_gateway()
        status, headers, body = self.request(gateway, chat_body())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["model"], "backend-test-model")
        self.assertEqual(self.fake.requests[-1]["model"], "backend-test-model")
        request_id = headers["X-Request-ID"]
        final = self.records()[-1]
        self.assertEqual(final["backend_kind"], "vllm")
        self.assertEqual(final["delivery"]["backend_receipt"], "not_available")
        self.assertEqual(final["backend_cancel"], "not_observed")
        detail = self.get_json(gateway, f"/api/requests/{request_id}")
        self.assertEqual(detail["backend_kind"], "vllm")
        self.assertEqual(detail["engine_receipts"], [])
        self.assertEqual(
            detail["evidence_summary"],
            "Gateway 전달·응답 수신, backend 측 수신 기록 미연결",
        )

    def test_stream_is_incremental_and_reaches_done(self) -> None:
        gateway = self.start_gateway()
        connection = http.client.HTTPConnection(*gateway.address, timeout=3)
        self.addCleanup(connection.close)
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=chat_body(stream=True),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        first = response.read1(1)
        payload = first + response.read()
        self.assertEqual(response.status, 200)
        self.assertTrue(first)
        self.assertTrue(payload.endswith(b"data: [DONE]\n\n"))

    def test_policy_denials_unmapped_model_and_invalid_credentials_never_call_chat(self) -> None:
        gateway = self.start_gateway()
        for body, expected, status in (
            (chat_body(model="other-model"), "MODEL_NOT_ALLOWED", 403),
            (chat_body(max_tokens=129), "MAX_TOKENS_EXCEEDED", 403),
        ):
            actual, headers, response = self.request(gateway, body)
            self.assertEqual(actual, status)
            self.assertEqual(json.loads(response)["error"]["code"], expected)
            detail = self.get_json(gateway, f"/api/requests/{headers['X-Request-ID']}")
            self.assertIn("backend 미수신은 미확인", detail["evidence_summary"])
        self.assertEqual(self.fake.request_count, 0)

        gateway.stop()
        unmapped = backend_document(self.fake.base_url, model_map={})
        gateway = self.start_gateway(document=unmapped)
        status, _, body = self.request(gateway, chat_body())
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"]["code"], "BACKEND_MODEL_UNMAPPED")
        self.assertEqual(self.fake.request_count, 0)

        gateway.stop()
        missing = backend_document(
            self.fake.base_url, credential={"env": "ABSENT_BACKEND_TOKEN"}
        )
        gateway = self.start_gateway(document=missing, environ={})
        status, _, body = self.request(gateway, chat_body())
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"]["code"], "BACKEND_CREDENTIAL_MISSING")
        self.assertEqual(self.fake.request_count, 0)

        gateway.stop()
        secret = "MUST-NOT-LEAK\r\nInjected: value"
        invalid = backend_document(
            self.fake.base_url, credential={"env": "INVALID_BACKEND_TOKEN"}
        )
        gateway = self.start_gateway(
            document=invalid, environ={"INVALID_BACKEND_TOKEN": secret}
        )
        status, headers, body = self.request(gateway, chat_body())
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"]["code"], "BACKEND_CREDENTIAL_INVALID")
        self.assertNotIn(secret.encode(), body)
        detail = self.get_json(gateway, f"/api/requests/{headers['X-Request-ID']}")
        self.assertEqual(detail["gateway_records"][-1]["delivery"]["attempted"], False)
        self.assertEqual(self.fake.request_count, 0)

    def test_backend_failures_are_distinct_and_redirect_is_not_followed(self) -> None:
        document = backend_document(
            self.fake.base_url,
            timeouts={
                "relay_timeout_s": 0.04,
                "stream_idle_timeout_s": 0.1,
                "request_deadline_s": 1,
            },
        )
        gateway = self.start_gateway(document=document)
        for mode, stream, expected_status, expected_code in (
            ("http_500", False, 502, "ENGINE_BAD_RESPONSE"),
            ("invalid_json", False, 502, "ENGINE_BAD_RESPONSE"),
            ("disconnect", False, 502, "ENGINE_UNREACHABLE"),
            ("redirect", False, 502, "ENGINE_BAD_RESPONSE"),
            ("header_timeout", False, 504, "ENGINE_TIMEOUT"),
        ):
            self.fake.set_mode(mode)
            with self.subTest(mode=mode):
                status, _, body = self.request(gateway, chat_body(stream=stream))
                self.assertEqual(status, expected_status)
                self.assertEqual(json.loads(body)["error"]["code"], expected_code)
        self.assertEqual(self.fake.redirect_follow_count, 0)

        self.fake.set_mode("sse_truncated")
        status, headers, body = self.request(gateway, chat_body(stream=True))
        self.assertEqual(status, 200)
        self.assertNotIn(b"[DONE]", body)
        final = next(
            record
            for record in reversed(self.records())
            if record["request_id"] == headers["X-Request-ID"] and record["phase"] == "final"
        )
        self.assertEqual(final["final_outcome"], "backend_truncated")
        self.assertEqual(final["decision"]["reason_code"], "STREAM_TRUNCATED")

    def test_credential_is_forwarded_but_never_exposed(self) -> None:
        secret = "S3A-SECRET-MUST-NOT-LEAK"
        self.fake.stop()
        self.fake = FakeBackend(
            port=0, required_authorization=f"Bearer {secret}"
        ).start()
        self.addCleanup(self.fake.stop)
        document = backend_document(
            self.fake.base_url, credential={"env": "BACKEND_TOKEN"}
        )
        gateway = self.start_gateway(document=document, environ={"BACKEND_TOKEN": secret})
        status, headers, _ = self.request(gateway, chat_body())
        self.assertEqual(status, 200)
        self.assertTrue(self.fake.requests[-1]["authorization_present"])
        api_payloads = []
        for path in (
            "/api/status",
            "/api/policy",
            "/api/collectors",
            f"/api/requests/{headers['X-Request-ID']}",
            f"/api/requests/{headers['X-Request-ID']}/evidence",
        ):
            with urlopen(self.url(gateway, path), timeout=3) as response:
                api_payloads.append(response.read())
        for path in (self.directory / "data").glob("*"):
            if path.is_file():
                api_payloads.append(path.read_bytes())
        self.assertNotIn(secret.encode(), b"\n".join(api_payloads))
        self.assertEqual(self.get_json(gateway, "/api/status")["backend"]["credential"], "configured")

    def test_collectors_use_external_origin_and_metrics_failure_does_not_stop_inference(self) -> None:
        gateway = self.start_gateway()
        health = self.wait_collector(gateway, "engine_health", "ok")
        info = self.wait_collector(gateway, "engine_info", "ok")
        metrics = self.wait_collector(gateway, "metrics", "ok")
        self.assertEqual(health["data_origin"], "external_backend")
        self.assertEqual(info["last_value"]["engine.kind"], "vllm")
        self.assertEqual(info["last_value"]["model.id"], "backend-test-model")
        self.assertEqual(metrics["data_origin"], "external_backend")
        self.assertEqual(
            metrics["last_value"]["missing.metric"]["state"],
            "unsupported_or_absent",
        )
        self.fake.set_metrics_failure("http_500")
        failed = self.wait_collector(gateway, "metrics", "error")
        self.assertIsNotNone(failed["last_error"])
        status, _, _ = self.request(gateway, chat_body())
        self.assertEqual(status, 200)

    def test_client_cannot_change_destination_and_mixed_history_stays_separate(self) -> None:
        data_dir = self.directory / "mixed"
        gateway = self.start_gateway(data_dir=data_dir)
        status, headers, _ = self.request(
            gateway,
            chat_body(),
            headers={
                "Host": "127.0.0.1:1",
                "X-Forwarded-Host": "127.0.0.1:1",
                "X-Evil": "must-not-forward",
            },
        )
        self.assertEqual(status, 200)
        vllm_id = headers["X-Request-ID"]
        received = self.fake.requests[-1]
        self.assertNotEqual(received["host"], "127.0.0.1:1")
        self.assertIsNone(received["x_forwarded_host"])
        self.assertNotIn("x-evil", received["header_names"])
        status, _, body = self.request(
            gateway, chat_body(base_url="http://127.0.0.1:1")
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "UNSUPPORTED_FIELD")
        gateway.stop()

        mock = MockEngine(
            port=0, receipts_path=data_dir / "engine_receipts.jsonl"
        ).start()
        self.addCleanup(mock.stop)
        mock_config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(POLICY),
            data_dir=str(data_dir),
        )
        mock_gateway = GatewayApp(
            mock_config, load_policy(POLICY), mock.address
        ).start()
        self.gateways.append(mock_gateway)
        status, mock_headers, _ = self.request(mock_gateway, chat_body())
        self.assertEqual(status, 200)
        old_detail = self.get_json(mock_gateway, f"/api/requests/{vllm_id}")
        new_detail = self.get_json(
            mock_gateway, f"/api/requests/{mock_headers['X-Request-ID']}"
        )
        self.assertEqual(old_detail["backend_kind"], "vllm")
        self.assertEqual(old_detail["engine_receipts"], [])
        self.assertEqual(new_detail["backend_kind"], "mock")
        self.assertEqual(new_detail["evidence_state"], "received")


if __name__ == "__main__":
    unittest.main()
