"""HTTP coverage for the policy-controlled POST /v1/completions route."""

from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.auth import token_digest
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.relay import (
    validate_completion_response,
    validate_completion_stream,
)
from serving_runtime.mvp.store import JsonlStore


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "examples/mvp/policy.json"
TOKEN = "completion-test-token-123"


class CompletionRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        try:
            self.engine = MockEngine(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.engine.stop)
        self.start_gateway()

    def start_gateway(self, *, auth: bool = False) -> None:
        auth_path = None
        if auth:
            auth_path = self.root / "auth.json"
            auth_path.write_text(
                json.dumps(
                    {
                        "schema_version": "runtime-auth/1.0",
                        "principals": [
                            {
                                "principal_id": "completion-user",
                                "roles": ["inference"],
                                "token_sha256": token_digest(TOKEN),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(POLICY),
            data_dir=str(self.root),
            auth_config_path=str(auth_path) if auth_path else None,
        )
        try:
            self.gateway = GatewayApp(
                config, load_policy(POLICY), self.engine.address
            ).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.gateway.stop)
        host, port = self.gateway.address
        self.url = f"http://{host}:{port}/v1/completions"

    def post(self, document: dict[str, object], *, token: str | None = None):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(
            self.url,
            data=json.dumps(document).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            response = urlopen(request, timeout=2)
        except HTTPError as error:
            with closing(error):
                return error.code, error.headers, error.read()
        with closing(response):
            return response.status, response.headers, response.read()

    @staticmethod
    def body(**updates: object) -> dict[str, object]:
        document: dict[str, object] = {
            "model": "test-model",
            "prompt": "one short prompt",
            "max_tokens": 8,
        }
        document.update(updates)
        return document

    def records(self):
        records, corrupt = JsonlStore(self.root / "gateway_records.jsonl").read_all()
        self.assertEqual(corrupt, [])
        return records

    def test_success_and_route_record_without_prompt_text(self) -> None:
        status, _, body = self.post(self.body())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["object"], "text_completion")
        final = self.records()[-1]
        self.assertEqual(final["route"], "POST /v1/completions")
        self.assertNotIn("prompt", json.dumps(final))
        self.assertEqual(self.engine.receipts[-1]["route"], "POST /v1/completions")

    def test_mock_completion_shape_matches_vllm_contract(self) -> None:
        status, _, body = self.post(self.body())
        self.assertEqual(status, 200)
        document = json.loads(body)
        self.assertTrue(document["id"].startswith("cmpl-"))
        self.assertEqual(
            set(document["usage"]),
            {"prompt_tokens", "completion_tokens", "total_tokens"},
        )

    def test_completion_response_validation_rejects_wrong_shapes(self) -> None:
        valid = {
            "object": "text_completion",
            "choices": [{"text": "ok"}],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "total_tokens": 3,
            },
        }
        self.assertTrue(validate_completion_response(json.dumps(valid).encode()))
        invalid = (
            {**valid, "object": "chat.completion"},
            {**valid, "choices": []},
            {**valid, "choices": [{"text": 1}]},
            {**valid, "usage": {"prompt_tokens": 1, "completion_tokens": 2}},
            {**valid, "usage": {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3}},
        )
        for document in invalid:
            with self.subTest(document=document):
                self.assertFalse(
                    validate_completion_response(json.dumps(document).encode())
                )

    def test_completion_stream_validation_requires_text_chunks_and_final_done(self) -> None:
        chunk = b'data: {"choices":[{"text":"ok"}]}\n\ndata: [DONE]\n\n'
        self.assertTrue(validate_completion_stream(chunk))
        for payload in (
            b'data: {"choices":[{"text":1}]}\n\ndata: [DONE]\n\n',
            b'data: {broken}\n\ndata: [DONE]\n\n',
            b'data: {"choices":[{"text":"ok"}]}\n\n',
            b'data: [DONE]\n\ndata: {"choices":[{"text":"late"}]}\n\n',
        ):
            with self.subTest(payload=payload):
                self.assertFalse(validate_completion_stream(payload))

    def test_invalid_completion_shapes_use_engine_bad_response_502(self) -> None:
        with patch(
            "serving_runtime.mvp.relay.validate_completion_response",
            return_value=False,
        ):
            status, _, body = self.post(self.body())
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)["error"]["code"], "ENGINE_BAD_RESPONSE")

        with patch(
            "serving_runtime.mvp.relay.validate_completion_stream",
            return_value=False,
        ):
            status, _, body = self.post(self.body(stream=True))
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)["error"]["code"], "ENGINE_BAD_RESPONSE")

    def test_max_tokens_denied_invalid_prompt_and_unsupported_field(self) -> None:
        denied = self.post(self.body(max_tokens=129))
        invalid = self.post(self.body(prompt=["not", "single"]))
        unsupported = self.post(self.body(echo=True))
        self.assertEqual(denied[0], 403)
        self.assertEqual(json.loads(denied[2])["error"]["code"], "MAX_TOKENS_EXCEEDED")
        self.assertEqual(invalid[0], 400)
        self.assertEqual(json.loads(invalid[2])["error"]["code"], "INVALID_PROMPT_TYPE")
        self.assertEqual(unsupported[0], 400)
        self.assertEqual(json.loads(unsupported[2])["error"]["code"], "UNSUPPORTED_FIELD")

    def test_stream_has_text_completion_chunks(self) -> None:
        status, headers, body = self.post(self.body(stream=True))
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "text/event-stream")
        self.assertIn(b'"object":"text_completion"', body)
        self.assertIn(b"data: [DONE]", body)

    def test_auth_is_required_with_default_role_inference(self) -> None:
        self.gateway.stop()
        self.start_gateway(auth=True)
        missing = self.post(self.body())
        allowed = self.post(self.body(), token=TOKEN)
        self.assertEqual(missing[0], 401)
        self.assertEqual(json.loads(missing[2])["error"]["code"], "AUTH_REQUIRED")
        self.assertEqual(allowed[0], 200)


if __name__ == "__main__":
    unittest.main()
