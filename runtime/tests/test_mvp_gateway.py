"""End-to-end M2 gateway relay and evidence tests."""

from contextlib import closing
import http.client
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.store import JsonlStore


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_POLICY = ROOT / "examples/mvp/policy.json"


def request_body(**updates: object) -> bytes:
    document: dict[str, object] = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 64,
    }
    document.update(updates)
    return json.dumps(document).encode("utf-8")


class GatewayRelayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name)
        try:
            self.engine = MockEngine(
                port=0,
                receipts_path=self.data_dir / "engine_receipts.jsonl",
            ).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.engine.stop)
        self._start_gateway(load_policy(EXAMPLE_POLICY))

    def _start_gateway(self, policy_state, *, timeout: float = 0.1) -> None:
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(EXAMPLE_POLICY),
            data_dir=str(self.data_dir),
            relay_timeout_s=timeout,
        )
        try:
            self.gateway = GatewayApp(config, policy_state, self.engine.address).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.gateway.stop)
        host, port = self.gateway.address
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"

    def post(
        self,
        body: bytes | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, object, bytes]:
        actual_body = request_body() if body is None else body
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        request = Request(
            self.base_url + "/v1/chat/completions",
            data=actual_body,
            headers=request_headers,
            method="POST",
        )
        try:
            response = urlopen(request, timeout=2)
        except HTTPError as error:
            with closing(error):
                return error.code, error.headers, error.read()
        with closing(response):
            return response.status, response.headers, response.read()

    def raw_post(
        self, headers: dict[str, str], body: bytes = b""
    ) -> tuple[int, object, bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=2)
        self.addCleanup(connection.close)
        connection.putrequest(
            "POST",
            "/v1/chat/completions",
            skip_accept_encoding=True,
        )
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, response.headers, response.read()

    def gateway_records(self) -> list[dict[str, object]]:
        records, corrupt = JsonlStore(self.data_dir / "gateway_records.jsonl").read_all()
        self.assertEqual(corrupt, [])
        return records

    def test_mvp_01_allowed_is_relayed_and_confirmed(self) -> None:
        status, headers, body = self.post()
        request_id = headers.get("X-Request-ID")

        self.assertEqual(status, 200)
        self.assertEqual(len(request_id), 32)
        self.assertEqual(json.loads(body)["model"], "test-model")
        self.assertEqual(self.engine.receipts[0]["request_id"], request_id)
        final = self.gateway_records()[-1]
        self.assertEqual(final["phase"], "final")
        self.assertEqual(final["request_id"], request_id)
        self.assertEqual(final["decision"]["result"], "ALLOWED")
        self.assertEqual(final["delivery"]["backend_receipt"], "confirmed")
        self.assertEqual(final["delivery"]["response"], "completed")

        receipts, corrupt = JsonlStore(
            self.data_dir / "engine_receipts.jsonl"
        ).read_all()
        self.assertEqual(corrupt, [])
        self.assertEqual(receipts[0]["request_id"], request_id)

    def test_mvp_02_limit_boundary_and_no_denied_delivery(self) -> None:
        allowed_status, _, _ = self.post(request_body(max_tokens=128))
        denied_status, denied_headers, denied_body = self.post(
            request_body(max_tokens=129)
        )
        denied_id = denied_headers.get("X-Request-ID")

        self.assertEqual(allowed_status, 200)
        self.assertEqual(denied_status, 403)
        self.assertEqual(json.loads(denied_body)["error"]["code"], "MAX_TOKENS_EXCEEDED")
        self.assertNotIn(denied_id, [receipt["request_id"] for receipt in self.engine.receipts])
        denied_final = self.gateway_records()[-1]
        self.assertEqual(denied_final["request_id"], denied_id)
        self.assertFalse(denied_final["delivery"]["attempted"])
        self.assertEqual(denied_final["delivery"]["backend_receipt"], "not_queried")

    def test_mvp_03_denial_does_not_poison_next_request(self) -> None:
        denied, _, _ = self.post(request_body(max_tokens=129))
        allowed, _, _ = self.post(request_body(max_tokens=64))
        self.assertEqual((denied, allowed), (403, 200))
        self.assertEqual(len(self.engine.receipts), 1)

    def test_mvp_04_disallowed_model_is_not_relayed(self) -> None:
        status, headers, _ = self.post(request_body(model="other-model"))
        request_id = headers.get("X-Request-ID")
        final = self.gateway_records()[-1]
        self.assertEqual(status, 403)
        self.assertEqual(final["decision"]["rule_id"], "allowed_models")
        self.assertFalse(final["delivery"]["attempted"])
        self.assertNotIn(request_id, [receipt["request_id"] for receipt in self.engine.receipts])

    def test_mvp_06_unsupported_inputs_are_not_relayed(self) -> None:
        stream_status, stream_headers, stream_body = self.post(request_body(stream="true"))
        large_status, large_headers, large_body = self.raw_post(
            {"Content-Type": "application/json", "Content-Length": "65537"}
        )
        chunked_status, chunked_headers, chunked_body = self.raw_post(
            {"Content-Type": "application/json", "Transfer-Encoding": "chunked"}
        )

        self.assertEqual(stream_status, 400)
        self.assertEqual(json.loads(stream_body)["error"]["code"], "INVALID_STREAM")
        self.assertEqual(large_status, 413)
        self.assertEqual(json.loads(large_body)["error"]["code"], "BODY_TOO_LARGE")
        self.assertEqual(large_headers.get("Connection"), "close")
        self.assertEqual(chunked_status, 400)
        self.assertEqual(
            json.loads(chunked_body)["error"]["code"],
            "UNSUPPORTED_TRANSFER_ENCODING",
        )
        request_ids = {
            stream_headers.get("X-Request-ID"),
            large_headers.get("X-Request-ID"),
            chunked_headers.get("X-Request-ID"),
        }
        self.assertTrue(request_ids.isdisjoint(
            {receipt["request_id"] for receipt in self.engine.receipts}
        ))

    def test_mvp_07_stopped_engine_is_unreachable_and_receipt_unknown(self) -> None:
        self.engine.stop()
        status, _, body = self.post()
        final = self.gateway_records()[-1]

        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)["error"]["code"], "ENGINE_UNREACHABLE")
        self.assertEqual(final["delivery"]["backend_receipt"], "unknown")
        self.assertEqual(final["delivery"]["response"], "failed")

    def test_mvp_07_engine_timeout(self) -> None:
        self.gateway.stop()
        self.engine.stop()
        self.engine = MockEngine(
            port=0,
            response_delay_s=0.2,
            receipts_path=self.data_dir / "engine_receipts.jsonl",
        ).start()
        self.addCleanup(self.engine.stop)
        self._start_gateway(load_policy(EXAMPLE_POLICY), timeout=0.05)

        status, _, body = self.post()

        self.assertEqual(status, 504)
        self.assertEqual(json.loads(body)["error"]["code"], "ENGINE_TIMEOUT")
        self.assertEqual(self.gateway_records()[-1]["decision"]["result"], "ERROR")

    def test_invalid_policy_is_503_without_relay(self) -> None:
        self.gateway.stop()
        state = load_policy(self.data_dir / "missing-policy.json")
        self._start_gateway(state)

        status, _, body = self.post()

        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"]["code"], "POLICY_UNAVAILABLE")
        self.assertEqual(self.engine.receipts, [])

    def test_store_failure_returns_503_before_relay(self) -> None:
        self.gateway.stop()
        records_path = self.data_dir / "gateway_records.jsonl"
        records_path.unlink(missing_ok=True)
        records_path.mkdir()
        self._start_gateway(load_policy(EXAMPLE_POLICY))

        status, _, body = self.post()

        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"]["code"], "STORE_UNAVAILABLE")
        self.assertEqual(self.engine.receipts, [])
        with closing(urlopen(self.base_url + "/api/status", timeout=2)) as response:
            gateway_status = json.loads(response.read().decode("utf-8"))
        self.assertFalse(gateway_status["record_store"]["ok"])
        self.assertIsNotNone(gateway_status["record_store"]["last_error_at"])

    def test_client_request_ids_are_ignored_and_each_server_id_is_new(self) -> None:
        _, first_headers, _ = self.post(headers={"X-Request-ID": "client-value"})
        _, second_headers, _ = self.post(headers={"X-Request-ID": "client-value"})
        first = first_headers.get("X-Request-ID")
        second = second_headers.get("X-Request-ID")
        self.assertNotEqual(first, "client-value")
        self.assertNotEqual(second, "client-value")
        self.assertNotEqual(first, second)

    def test_prompt_content_is_not_stored(self) -> None:
        secret = "PROMPT-MUST-NOT-BE-STORED"
        status, _, _ = self.post(
            request_body(messages=[{"role": "user", "content": secret}])
        )
        self.assertEqual(status, 200)
        self.assertNotIn(
            secret,
            (self.data_dir / "gateway_records.jsonl").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            secret,
            (self.data_dir / "engine_receipts.jsonl").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
