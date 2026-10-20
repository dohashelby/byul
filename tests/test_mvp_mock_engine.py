"""HTTP tests for the local mock engine and receipt lookup."""

from contextlib import closing
import json
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.mock_engine import MockEngine


class MockEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.engine = MockEngine(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.engine.stop)
        host, port = self.engine.address
        self.assertEqual(host, "127.0.0.1")
        self.base_url = f"http://{host}:{port}"

    def get_json(self, path: str) -> tuple[int, dict[str, object]]:
        with closing(urlopen(self.base_url + path, timeout=2)) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_healthz(self) -> None:
        status, body = self.get_json("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["receipts_store"]["state"], "disabled")

    def test_chat_response_and_receipt_found_or_missing(self) -> None:
        request = Request(
            self.base_url + "/v1/chat/completions",
            data=json.dumps({
                "model": "test-model",
                "max_tokens": 64,
                "messages": [{"role": "user", "content": "do not retain this"}],
            }).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Request-ID": "request-1"},
            method="POST",
        )
        with closing(urlopen(request, timeout=2)) as response:
            body = json.loads(response.read().decode("utf-8"))
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["choices"][0]["message"]["content"], "mock response")

        _, found = self.get_json("/_mock/receipts?request_id=request-1")
        self.assertTrue(found["found"])
        self.assertEqual(found["receipts"][0]["model"], "test-model")
        self.assertEqual(found["receipts"][0]["max_tokens"], 64)
        self.assertNotIn("messages", found["receipts"][0])
        self.assertIn("queried_at", found)

        _, missing = self.get_json("/_mock/receipts?request_id=not-received")
        self.assertFalse(missing["found"])
        self.assertEqual(missing["receipts"], [])

    def test_missing_request_id_is_400_and_not_recorded(self) -> None:
        request = Request(
            self.base_url + "/v1/chat/completions",
            data=b'{"model":"test-model","max_tokens":64}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.engine.receipts, [])

    def test_unknown_path_and_wrong_method(self) -> None:
        for request, expected in (
            (Request(self.base_url + "/unknown"), 404),
            (Request(self.base_url + "/healthz", data=b"", method="POST"), 405),
        ):
            with self.subTest(expected=expected), self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=2)
            self.assertEqual(caught.exception.code, expected)


if __name__ == "__main__":
    unittest.main()
