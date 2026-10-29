"""Tests for bounded runtime-mvp/1.2 default-profile request fields."""

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
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.store import JsonlStore
from serving_runtime.mvp.validator import validate_request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "examples/mvp/policy-v1.2-default-sampling.json"
ZERODAY_POLICY = ROOT / "examples/mvp/policy-v1.2-zeroday-lab.json"
INT64_MIN = -9223372036854775808
INT64_MAX = 9223372036854775807


def backend_document(base_url: str) -> dict[str, object]:
    return {
        "schema_version": "runtime-backend/1.0",
        "kind": "vllm",
        "base_url": base_url,
        "model_map": {"test-model": "backend-test-model"},
        "health_path": "/health",
        "models_path": "/v1/models",
        "version_path": "/version",
        "timeouts": {
            "relay_timeout_s": 0.5,
            "stream_idle_timeout_s": 0.5,
            "request_deadline_s": 2,
        },
    }


def default_document(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "runtime-mvp/1.2",
        "policy_id": "default-extra-fields-test",
        "policy_version": "1",
        "target_id": "mock-engine-001",
        "allowed_models": ["test-model"],
        "output_default_tokens": 128,
        "output_max_tokens": 128,
        "max_body_bytes": 65536,
        "profile": "default",
        "extra_fields": {
            "temperature": {
                "mode": "validate",
                "type": "number",
                "min": 0,
                "max": 1,
            },
            "top_p": {
                "mode": "validate",
                "type": "number",
                "min": 0,
                "max": 1,
            },
            "seed": {
                "mode": "validate",
                "type": "integer",
                "min": INT64_MIN,
                "max": INT64_MAX,
            },
            "stop": {
                "mode": "validate",
                "type": "array",
                "max_items": 4,
                "items": {"type": "string", "max_length": 64},
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


class PolicyFileMixin:
    def load(self, document: dict[str, object]):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "policy.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return load_policy(path)


class DefaultExtraFieldPolicyTests(PolicyFileMixin, unittest.TestCase):
    def test_default_profile_accepts_bounded_validate_rules(self) -> None:
        state = self.load(default_document())
        self.assertTrue(state.ok, state.error_message)
        self.assertEqual(state.policy.profile, "default")
        self.assertEqual(
            {rule.name: rule.mode for rule in state.policy.extra_fields},
            {"seed": "validate", "stop": "validate", "temperature": "validate", "top_p": "validate"},
        )

    def test_default_profile_rejects_passthrough_and_names_the_field(self) -> None:
        state = self.load(
            default_document(extra_fields={"unsafe_option": {"mode": "passthrough"}})
        )
        self.assertFalse(state.ok)
        self.assertEqual(state.error_code, "INVALID_POLICY")
        self.assertIn("unsafe_option", state.error_message)
        self.assertIn("zeroday-lab", state.error_message)

    def test_default_profile_rejects_unbounded_validate_rules(self) -> None:
        bad_rules = {
            "number_min_only": {"mode": "validate", "type": "number", "min": 0},
            "number_max_only": {"mode": "validate", "type": "number", "max": 1},
            "number_no_bounds": {"mode": "validate", "type": "number"},
            "string_no_max_length": {"mode": "validate", "type": "string"},
            "array_no_max_items": {
                "mode": "validate",
                "type": "array",
                "items": {"type": "string", "max_length": 64},
            },
            "array_no_items": {
                "mode": "validate",
                "type": "array",
                "max_items": 4,
            },
            "array_number_item_min_only": {
                "mode": "validate",
                "type": "array",
                "max_items": 4,
                "items": {"type": "integer", "min": 0},
            },
            "array_string_item_no_max_length": {
                "mode": "validate",
                "type": "array",
                "max_items": 4,
                "items": {"type": "string"},
            },
        }
        for label, rule in bad_rules.items():
            with self.subTest(rule=label):
                state = self.load(default_document(extra_fields={"field_x": rule}))
                self.assertFalse(state.ok)
                self.assertEqual(state.error_code, "INVALID_POLICY")
                self.assertIn("field_x", state.error_message)

    def test_default_profile_rejects_object_rules(self) -> None:
        state = self.load(
            default_document(
                extra_fields={
                    "response_format": {
                        "mode": "validate",
                        "type": "object",
                        "max_bytes": 128,
                    }
                }
            )
        )
        self.assertFalse(state.ok)
        self.assertEqual(state.error_code, "INVALID_POLICY")
        self.assertIn("response_format", state.error_message)
        self.assertIn("object", state.error_message)

    def test_boolean_rules_remain_allowed_in_default_profile(self) -> None:
        state = self.load(
            default_document(
                extra_fields={"flag": {"mode": "validate", "type": "boolean"}}
            )
        )
        self.assertTrue(state.ok, state.error_message)

    def test_default_and_existing_zeroday_examples_load(self) -> None:
        default_state = load_policy(DEFAULT_POLICY)
        self.assertTrue(default_state.ok, default_state.error_message)
        self.assertEqual(default_state.policy.profile, "default")
        self.assertEqual(default_state.policy.approval_ref, "EXAMPLE-NOT-APPROVED")

        zeroday_state = load_policy(ZERODAY_POLICY)
        self.assertTrue(zeroday_state.ok, zeroday_state.error_message)
        self.assertEqual(zeroday_state.policy.profile, "zeroday-lab")
        self.assertTrue(any(rule.mode == "passthrough" for rule in zeroday_state.policy.extra_fields))


class DefaultExtraFieldValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.state = load_policy(DEFAULT_POLICY)
        if not cls.state.ok:
            raise AssertionError(cls.state.error_message)

    def decide(self, body: bytes, *, request_kind: str = "chat"):
        return validate_request(
            body,
            "application/json",
            self.state,
            request_kind=request_kind,
        )

    def assert_invalid_extra(self, fields: dict[str, object], value_text: str) -> None:
        decision = self.decide(chat(**fields))
        self.assertEqual((decision.http_status, decision.reason_code), (400, "INVALID_EXTRA_FIELD"))
        self.assertNotIn(value_text, decision.message)

    def test_temperature_boundaries_and_types(self) -> None:
        for value in (0, 1.0):
            with self.subTest(value=value):
                self.assertEqual(self.decide(chat(temperature=value)).result, "ALLOWED")
        for value in (1.01, -0.01, "0.5", True):
            with self.subTest(value=value):
                self.assert_invalid_extra({"temperature": value}, str(value))

    def test_stop_count_and_item_length_boundaries(self) -> None:
        self.assertEqual(self.decide(chat(stop=["a", "b", "c", "d"])).result, "ALLOWED")
        self.assert_invalid_extra({"stop": ["a", "b", "c", "d", "e"]}, "a, b, c")
        long_value = "SENSITIVE-" + "x" * 55
        self.assertEqual(len(long_value), 65)
        self.assert_invalid_extra({"stop": [long_value]}, long_value)

    def test_seed_int64_boundaries(self) -> None:
        for value in (INT64_MIN, INT64_MAX):
            with self.subTest(value=value):
                self.assertEqual(self.decide(chat(seed=value)).result, "ALLOWED")
        self.assert_invalid_extra({"seed": INT64_MAX + 1}, str(INT64_MAX + 1))

    def test_completions_rejects_declared_temperature(self) -> None:
        body = json.dumps(
            {"model": "test-model", "prompt": "hello", "temperature": 0.5}
        ).encode("utf-8")
        decision = self.decide(body, request_kind="completions")
        self.assertEqual((decision.http_status, decision.reason_code), (400, "UNSUPPORTED_FIELD"))

    def test_default_profile_status_is_not_test_only(self) -> None:
        status = GatewayApp._profile_status(self.state.policy)
        self.assertEqual(status["profile"], "default")
        self.assertFalse(status["test_only"])
        self.assertEqual(status["passthrough_count"], 0)
        self.assertEqual(status["validate_count"], 4)


class DefaultExtraFieldGatewayHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        try:
            self.fake = FakeBackend(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.fake.stop)

        backend_path = self.directory / "backend.json"
        backend_path.write_text(
            json.dumps(backend_document(self.fake.base_url)), encoding="utf-8"
        )
        config = MVPConfig(
            gateway_port=0,
            backend="vllm",
            backend_config_path=str(backend_path),
            policy_path=str(DEFAULT_POLICY),
            data_dir=str(self.directory / "data"),
            collector_interval_s=0.05,
            collector_stale_after_s=1,
        )
        try:
            self.gateway = GatewayApp(
                config,
                load_policy(DEFAULT_POLICY),
                backend_config=load_backend_config(backend_path, environ={}),
            ).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.gateway.stop)

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

    def test_rejected_request_does_not_reach_backend_and_allowed_values_do(self) -> None:
        before = self.fake.request_count
        status, _, body = self.post(chat(temperature=1.01))
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "INVALID_EXTRA_FIELD")
        self.assertEqual(self.fake.request_count, before)

        marker = "record-secret-stop"
        status, headers, _ = self.post(
            chat(temperature=0.7, top_p=0.9, seed=INT64_MIN, stop=[marker])
        )
        self.assertEqual(status, 200)
        request_id = headers["X-Request-ID"]
        received = [item for item in self.fake.requests if item["request_id"] == request_id]
        self.assertEqual(len(received), 1)
        self.assertEqual(
            received[0]["extra_values"],
            {"temperature": 0.7, "top_p": 0.9, "seed": INT64_MIN, "stop": [marker]},
        )

        records, corrupt = JsonlStore(
            self.directory / "data" / "gateway_records.jsonl"
        ).read_all()
        self.assertEqual(corrupt, [])
        final = [
            record
            for record in records
            if record["request_id"] == request_id and record["phase"] == "final"
        ][-1]
        self.assertEqual(final["policy"]["profile"], "default")
        self.assertEqual(
            final["request"]["extra_fields"],
            [
                {"name": "seed", "mode": "validate"},
                {"name": "stop", "mode": "validate"},
                {"name": "temperature", "mode": "validate"},
                {"name": "top_p", "mode": "validate"},
            ],
        )
        stored = (self.directory / "data" / "gateway_records.jsonl").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(marker, stored)
        self.assertNotIn(str(INT64_MIN), stored)


if __name__ == "__main__":
    unittest.main()
