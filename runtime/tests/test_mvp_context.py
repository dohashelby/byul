"""Context-length pre-check: tokenizer-based, fail closed, never relayed when over."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.backend import load_backend_config
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.fake_backend import FakeBackend
from serving_runtime.mvp.mock_engine import MockEngine, simulated_token_count
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.store import JsonlStore

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_POLICY = ROOT / "examples/mvp/policy-v1.1-context.json"
BASE_POLICY = ROOT / "examples/mvp/policy.json"


def write_policy(directory: Path, **updates: object) -> Path:
    document = json.loads(CONTEXT_POLICY.read_text(encoding="utf-8"))
    document.update(updates)
    path = directory / "policy.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def chat_body(
    content: str = "hello there", max_tokens: int = 64, *, stream: bool = False
) -> bytes:
    document = {
        "model": "test-model",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
    }
    if stream:
        document["stream"] = True
    return json.dumps(document).encode("utf-8")


class PolicyFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_example_context_policy_loads(self) -> None:
        state = load_policy(CONTEXT_POLICY)
        self.assertTrue(state.ok, state.error_message)
        self.assertEqual(state.policy.max_context_tokens, 2048)

    def test_invalid_values_are_rejected(self) -> None:
        for value in (0, -1, True, "2048", 128, 64):
            with self.subTest(value=value):
                state = load_policy(write_policy(self.directory, max_context_tokens=value))
                self.assertFalse(state.ok)

    def test_schema_1_0_does_not_accept_the_field(self) -> None:
        document = json.loads(BASE_POLICY.read_text(encoding="utf-8"))
        document["max_context_tokens"] = 2048
        path = self.directory / "v10.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        state = load_policy(path)
        self.assertFalse(state.ok)
        self.assertEqual(state.error_code, "UNKNOWN_FIELD")

    def test_simulated_count_is_deterministic(self) -> None:
        messages = [{"role": "user", "content": "a b c"}]
        self.assertEqual(simulated_token_count(messages), 2 + 3 + 4)
        with self.assertRaises(ValueError):
            simulated_token_count([{"role": "user", "content": 1}])


class ContextCheckHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.data_dir = self.directory / "data"

    def start_mock(self, policy_path: Path) -> tuple[MockEngine, GatewayApp]:
        try:
            engine = MockEngine(port=0, receipts_path=self.data_dir / "engine_receipts.jsonl").start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(engine.stop)
        config = MVPConfig(
            gateway_port=0, engine_port=0, policy_path=str(policy_path), data_dir=str(self.data_dir)
        )
        gateway = GatewayApp(config, load_policy(policy_path), engine.address).start()
        self.addCleanup(gateway.stop)
        return engine, gateway

    def post(self, gateway: GatewayApp, body: bytes) -> tuple[int, str | None, dict]:
        host, port = gateway.address
        request = Request(
            f"http://{host}:{port}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.headers.get("X-Request-ID"), json.loads(response.read())
        except HTTPError as error:
            with closing(error):
                return error.code, error.headers.get("X-Request-ID"), json.loads(error.read())

    def final_record(self, request_id: str | None) -> dict:
        records, _ = JsonlStore(self.data_dir / "gateway_records.jsonl").read_all()
        finals = [r for r in records if r["request_id"] == request_id and r["phase"] == "final"]
        self.assertEqual(len(finals), 1)
        return finals[0]

    def test_within_limit_is_relayed_and_recorded(self) -> None:
        policy = write_policy(self.directory, max_context_tokens=200)
        engine, gateway = self.start_mock(policy)
        status, request_id, _ = self.post(gateway, chat_body("one two three", max_tokens=64))
        self.assertEqual(status, 200)
        self.assertEqual(engine.tokenize_calls, 1)
        context = self.final_record(request_id)["request"]["context"]
        self.assertEqual(context["result"], "within_limit")
        self.assertEqual(context["input_tokens"], 2 + 3 + 4)
        self.assertEqual(context["limit"], 200)

    def test_over_limit_is_denied_before_generation(self) -> None:
        policy = write_policy(self.directory, max_context_tokens=200)
        engine, gateway = self.start_mock(policy)
        long_prompt = " ".join(["word"] * 150)
        status, request_id, body = self.post(gateway, chat_body(long_prompt, max_tokens=64))
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "CONTEXT_LIMIT_EXCEEDED")
        self.assertEqual(engine.tokenize_calls, 1)
        self.assertEqual(engine.receipts, [])
        record = self.final_record(request_id)
        self.assertFalse(record["delivery"]["attempted"])
        self.assertEqual(record["decision"]["rule_id"], "max_context_tokens")
        self.assertEqual(record["request"]["context"]["result"], "exceeded")

    def test_over_limit_stream_is_denied_before_generation(self) -> None:
        policy = write_policy(self.directory, max_context_tokens=200)
        engine, gateway = self.start_mock(policy)
        long_prompt = " ".join(["word"] * 150)
        status, request_id, body = self.post(
            gateway, chat_body(long_prompt, max_tokens=64, stream=True)
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "CONTEXT_LIMIT_EXCEEDED")
        self.assertEqual(engine.receipts, [])
        record = self.final_record(request_id)
        self.assertTrue(record["stream"])
        self.assertFalse(record["delivery"]["attempted"])

    def test_context_record_does_not_leak_to_following_request(self) -> None:
        policy = write_policy(self.directory, max_context_tokens=200)
        engine, gateway = self.start_mock(policy)
        long_prompt = " ".join(["word"] * 150)
        first_status, first_id, _ = self.post(
            gateway, chat_body(long_prompt, max_tokens=64)
        )
        self.assertEqual(first_status, 403)

        gateway.policy_state = load_policy(BASE_POLICY)
        second_status, second_id, _ = self.post(gateway, chat_body())
        self.assertEqual(second_status, 200)
        self.assertEqual(
            self.final_record(first_id)["request"]["context"]["result"], "exceeded"
        )
        self.assertIsNone(self.final_record(second_id)["request"]["context"])
        self.assertEqual(engine.tokenize_calls, 1)

    def test_no_limit_means_no_tokenize_call(self) -> None:
        engine, gateway = self.start_mock(BASE_POLICY)
        status, request_id, _ = self.post(gateway, chat_body())
        self.assertEqual(status, 200)
        self.assertEqual(engine.tokenize_calls, 0)
        self.assertIsNone(self.final_record(request_id)["request"]["context"])

    def test_tokenize_failure_fails_closed_in_vllm_mode(self) -> None:
        try:
            fake = FakeBackend(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(fake.stop)
        fake.tokenize_failure = True
        backend_path = self.directory / "backend.json"
        backend_path.write_text(
            json.dumps(
                {
                    "schema_version": "runtime-backend/1.0",
                    "kind": "vllm",
                    "base_url": fake.base_url,
                    "model_map": {"test-model": "backend-test-model"},
                }
            ),
            encoding="utf-8",
        )
        policy = write_policy(self.directory, max_context_tokens=200)
        config = MVPConfig(
            gateway_port=0,
            backend="vllm",
            backend_config_path=str(backend_path),
            policy_path=str(policy),
            data_dir=str(self.data_dir),
        )
        gateway = GatewayApp(
            config, load_policy(policy), backend_config=load_backend_config(backend_path, environ={})
        ).start()
        self.addCleanup(gateway.stop)
        status, request_id, body = self.post(gateway, chat_body())
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "CONTEXT_CHECK_UNAVAILABLE")
        self.assertEqual(fake.tokenize_calls, 1)
        self.assertEqual(fake.request_count, 0)
        self.assertEqual(self.final_record(request_id)["request"]["context"]["result"], "unavailable")

        fake.tokenize_failure = False
        status, _, _ = self.post(gateway, chat_body())
        self.assertEqual(status, 200)
        self.assertEqual(fake.requests[-1]["model"], "backend-test-model")

    def test_vllm_tokenize_uses_path_prefix_and_authorization(self) -> None:
        try:
            fake = FakeBackend(
                port=0,
                path_prefix="/fixed-prefix",
                required_authorization="Bearer tokenizer-secret",
            ).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(fake.stop)
        backend_path = self.directory / "prefixed-backend.json"
        backend_path.write_text(
            json.dumps(
                {
                    "schema_version": "runtime-backend/1.0",
                    "kind": "vllm",
                    "base_url": fake.base_url,
                    "model_map": {"test-model": "backend-test-model"},
                    "credential": {"env": "TOKENIZER_KEY"},
                }
            ),
            encoding="utf-8",
        )
        policy = write_policy(self.directory, max_context_tokens=200)
        config = MVPConfig(
            gateway_port=0,
            backend="vllm",
            backend_config_path=str(backend_path),
            policy_path=str(policy),
            data_dir=str(self.data_dir),
        )
        gateway = GatewayApp(
            config,
            load_policy(policy),
            backend_config=load_backend_config(
                backend_path, environ={"TOKENIZER_KEY": "tokenizer-secret"}
            ),
        ).start()
        self.addCleanup(gateway.stop)
        long_prompt = " ".join(["word"] * 150)
        status, _, body = self.post(gateway, chat_body(long_prompt, max_tokens=64))
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "CONTEXT_LIMIT_EXCEEDED")
        self.assertEqual(fake.tokenize_calls, 1)
        self.assertEqual(fake.request_count, 0)

    def test_invalid_successful_tokenize_responses_fail_closed(self) -> None:
        try:
            fake = FakeBackend(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(fake.stop)
        backend_path = self.directory / "invalid-tokenize-backend.json"
        backend_path.write_text(
            json.dumps(
                {
                    "schema_version": "runtime-backend/1.0",
                    "kind": "vllm",
                    "base_url": fake.base_url,
                    "model_map": {"test-model": "backend-test-model"},
                }
            ),
            encoding="utf-8",
        )
        policy = write_policy(self.directory, max_context_tokens=200)
        config = MVPConfig(
            gateway_port=0,
            backend="vllm",
            backend_config_path=str(backend_path),
            policy_path=str(policy),
            data_dir=str(self.data_dir),
        )
        gateway = GatewayApp(
            config,
            load_policy(policy),
            backend_config=load_backend_config(backend_path, environ={}),
        ).start()
        self.addCleanup(gateway.stop)
        cases = {
            "missing": b"{}",
            "bool": b'{"count":true}',
            "negative": b'{"count":-1}',
            "deep": b"[" * 2000 + b"0" + b"]" * 2000,
        }
        for name, response_body in cases.items():
            with self.subTest(name=name):
                fake.tokenize_response_body = response_body
                status, request_id, body = self.post(gateway, chat_body())
                self.assertEqual(status, 503)
                self.assertEqual(body["error"]["code"], "CONTEXT_CHECK_UNAVAILABLE")
                record = self.final_record(request_id)
                self.assertEqual(record["request"]["context"]["result"], "unavailable")
                self.assertFalse(record["delivery"]["attempted"])
        self.assertEqual(fake.request_count, 0)


if __name__ == "__main__":
    unittest.main()
