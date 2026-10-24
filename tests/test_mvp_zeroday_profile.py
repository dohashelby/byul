"""Tests for the test-only runtime-mvp/1.2 zeroday-lab request field profile."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.backend import BackendConfig, load_backend_config
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.fake_backend import FakeBackend
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.relay import relay_payload
from serving_runtime.mvp.store import JsonlStore
from serving_runtime.mvp.validator import validate_request



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


ROOT = Path(__file__).resolve().parents[1]
ZERODAY_POLICY = ROOT / "examples/mvp/policy-v1.2-zeroday-lab.json"
EXISTING_POLICIES = (
    ROOT / "examples/mvp/policy.json",
    ROOT / "examples/mvp/policy-v1.1.json",
    ROOT / "examples/mvp/policy-v1.1-context.json",
)
SECRET_VALUE = "zeroday-secret-value-7f3a"


def v12_document(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "runtime-mvp/1.2",
        "policy_id": "zeroday-test",
        "policy_version": "1",
        "target_id": "mock-engine-001",
        "allowed_models": ["test-model"],
        "output_default_tokens": 128,
        "output_max_tokens": 128,
        "max_body_bytes": 65536,
        "profile": "zeroday-lab",
        "extra_fields": {
            "temperature": {"mode": "validate", "type": "number", "min": 0, "max": 2},
            "seed": {"mode": "validate", "type": "integer"},
            "logprobs": {"mode": "validate", "type": "boolean"},
            "stop": {
                "mode": "validate",
                "type": "array",
                "max_items": 2,
                "items": {"type": "string", "max_length": 4},
            },
            "response_format": {"mode": "validate", "type": "object", "max_bytes": 32},
            "cache_salt": {"mode": "passthrough"},
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


class ZerodayPolicyTests(PolicyFileMixin, unittest.TestCase):
    def test_example_policy_loads_as_test_only_profile(self) -> None:
        state = load_policy(ZERODAY_POLICY)
        self.assertTrue(state.ok, state.error_message)
        policy = state.policy
        self.assertEqual(policy.profile, "zeroday-lab")
        self.assertEqual(policy.approval_ref, "LAB-ZERODAY-TEST-ONLY")
        modes = {rule.name: rule.mode for rule in policy.extra_fields}
        self.assertEqual(modes["temperature"], "validate")
        self.assertEqual(modes["chat_template_kwargs"], "passthrough")
        self.assertEqual(policy.max_n, 1)

    def test_existing_policies_keep_default_profile(self) -> None:
        for path in EXISTING_POLICIES:
            with self.subTest(path=path.name):
                state = load_policy(path)
                self.assertTrue(state.ok, state.error_message)
                self.assertEqual(state.policy.profile, "default")
                self.assertEqual(state.policy.extra_fields, ())
                self.assertEqual(state.policy.max_n, 1)

    def test_v12_fields_are_unknown_in_older_schemas(self) -> None:
        for schema in ("runtime-mvp/1.0", "runtime-mvp/1.1"):
            for name, value in (
                ("profile", "default"),
                ("extra_fields", {}),
                ("max_n", 1),
            ):
                with self.subTest(schema=schema, field=name):
                    document = v12_document(schema_version=schema)
                    for key in ("profile", "extra_fields"):
                        document.pop(key)
                    document[name] = value
                    state = self.load(document)
                    self.assertFalse(state.ok)
                    self.assertEqual(state.error_code, "UNKNOWN_FIELD")

    def test_profile_mismatch_and_invalid_profile(self) -> None:
        default_with_fields = self.load(v12_document(profile="default"))
        self.assertFalse(default_with_fields.ok)
        self.assertIn("zeroday-lab", default_with_fields.error_message)
        plain_default = v12_document(profile="default")
        plain_default.pop("extra_fields")
        self.assertTrue(self.load(plain_default).ok)
        self.assertFalse(self.load(v12_document(profile="lab")).ok)
        high_n_default = dict(plain_default, max_n=2)
        self.assertFalse(self.load(high_n_default).ok)
        self.assertTrue(self.load(v12_document(max_n=2)).ok)
        for value in (0, -1, True, 1.5, "2"):
            with self.subTest(max_n=value):
                self.assertFalse(self.load(v12_document(max_n=value)).ok)

    def test_reserved_field_names_are_rejected(self) -> None:
        for name in ("model", "messages", "max_tokens", "stream", "n", "max_completion_tokens"):
            with self.subTest(name=name):
                state = self.load(
                    v12_document(extra_fields={name: {"mode": "passthrough"}})
                )
                self.assertFalse(state.ok)
                self.assertIn("reserved", state.error_message)

    def test_invalid_rules_are_rejected(self) -> None:
        bad_rules = {
            "unknown_mode": {"mode": "allow"},
            "passthrough_with_type": {"mode": "passthrough", "type": "string"},
            "unknown_key": {"mode": "validate", "type": "integer", "regex": "x"},
            "unknown_type": {"mode": "validate", "type": "float"},
            "missing_type": {"mode": "validate"},
            "min_above_max": {"mode": "validate", "type": "number", "min": 2, "max": 1},
            "string_with_min": {"mode": "validate", "type": "string", "min": 0},
            "integer_with_float_bound": {"mode": "validate", "type": "integer", "max": 1.5},
            "bool_bound": {"mode": "validate", "type": "number", "max": True},
            "negative_length": {"mode": "validate", "type": "string", "max_length": -1},
            "bool_length": {"mode": "validate", "type": "string", "max_length": True},
            "items_on_object": {"mode": "validate", "type": "object", "items": {"type": "string"}},
            "nested_array_items": {"mode": "validate", "type": "array", "items": {"type": "array"}},
            "item_unknown_key": {
                "mode": "validate",
                "type": "array",
                "items": {"type": "string", "min": 1},
            },
            "negative_bytes": {"mode": "validate", "type": "object", "max_bytes": -5},
            "not_object": "passthrough",
        }
        for label, rule in bad_rules.items():
            with self.subTest(rule=label):
                state = self.load(v12_document(extra_fields={"field_x": rule}))
                self.assertFalse(state.ok)
                self.assertEqual(state.error_code, "INVALID_POLICY")
        self.assertFalse(self.load(v12_document(extra_fields=[])).ok)
        self.assertFalse(
            self.load(v12_document(extra_fields={" spaced ": {"mode": "passthrough"}})).ok
        )


class ZerodayValidatorTests(PolicyFileMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.state = self.load(v12_document(max_n=2))
        self.assertTrue(self.state.ok, self.state.error_message)

    def decide(self, body: bytes, state=None):
        return validate_request(body, "application/json", state or self.state)

    def test_validate_and_passthrough_fields_are_allowed(self) -> None:
        decision = self.decide(
            chat(
                temperature=0.5,
                seed=42,
                logprobs=True,
                stop=["a", "bb"],
                response_format={"type": "json"},
                cache_salt={"anything": [1, None, "x"]},
            )
        )
        self.assertEqual(decision.result, "ALLOWED", decision.message)
        self.assertEqual(
            decision.extra_fields,
            (
                ("cache_salt", "passthrough"),
                ("logprobs", "validate"),
                ("response_format", "validate"),
                ("seed", "validate"),
                ("stop", "validate"),
                ("temperature", "validate"),
            ),
        )

    def test_constraint_violations_name_field_and_constraint_only(self) -> None:
        cases = {
            "temperature-max": ({"temperature": 2.5}, "temperature", "max"),
            "temperature-min": ({"temperature": -0.1}, "temperature", "min"),
            "temperature-type": ({"temperature": "hot"}, "temperature", "type"),
            "seed-float": ({"seed": 1.5}, "seed", "type"),
            "stop-items": ({"stop": ["a", "b", "c"]}, "stop", "max_items"),
            "stop-length": ({"stop": ["toolong"]}, "stop", "items.max_length"),
            "stop-item-type": ({"stop": [1]}, "stop", "items.type"),
            "format-bytes": ({"response_format": {"type": "x" * 40}}, "response_format", "max_bytes"),
            "format-type": ({"response_format": ["x"]}, "response_format", "type"),
        }
        for label, (fields, name, constraint) in cases.items():
            with self.subTest(case=label):
                decision = self.decide(chat(**fields))
                self.assertEqual((decision.result, decision.http_status), ("INVALID", 400))
                self.assertEqual(decision.reason_code, "INVALID_EXTRA_FIELD")
                self.assertEqual(decision.message, f"extra field {name} violates {constraint}")
        secret = self.decide(chat(stop=[SECRET_VALUE]))
        self.assertNotIn(SECRET_VALUE, secret.message)

    def test_bool_is_not_integer_or_number(self) -> None:
        for fields in ({"seed": True}, {"temperature": False}):
            with self.subTest(fields=fields):
                decision = self.decide(chat(**fields))
                self.assertEqual(decision.reason_code, "INVALID_EXTRA_FIELD")
                self.assertTrue(decision.message.endswith("violates type"))
        number_for_boolean = self.decide(chat(logprobs=1))
        self.assertEqual(number_for_boolean.reason_code, "INVALID_EXTRA_FIELD")

    def test_huge_integer_is_compared_without_overflow(self) -> None:
        decision = self.decide(b'{"model":"test-model","messages":[{"role":"user","content":"x"}],"temperature":' + b"9" * 400 + b"}")
        self.assertEqual(decision.message, "extra field temperature violates max")
        seed = self.decide(b'{"model":"test-model","messages":[{"role":"user","content":"x"}],"seed":' + b"9" * 400 + b"}")
        self.assertEqual(seed.result, "ALLOWED")

    def test_non_finite_numbers_are_rejected_before_rules(self) -> None:
        for literal in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(literal=literal):
                body = b'{"model":"test-model","messages":[{"role":"user","content":"x"}],"temperature":' + literal + b"}"
                self.assertEqual(self.decide(body).reason_code, "MALFORMED_JSON")

    def test_unregistered_fields_and_alias_stay_rejected(self) -> None:
        unregistered = self.decide(chat(top_p=0.5))
        self.assertEqual(unregistered.reason_code, "UNSUPPORTED_FIELD")
        alias = self.decide(chat(max_completion_tokens=10, temperature=1))
        self.assertEqual(alias.reason_code, "UNSUPPORTED_ALIAS")

    def test_max_n_is_policy_controlled(self) -> None:
        self.assertEqual(self.decide(chat(n=2)).n, 2)
        too_many = self.decide(chat(n=3))
        self.assertEqual(too_many.reason_code, "N_UNSUPPORTED")
        self.assertEqual(too_many.message, "n must be an integer between 1 and 2")
        default_state = self.load(v12_document())
        self.assertEqual(self.decide(chat(n=2), default_state).reason_code, "N_UNSUPPORTED")
        legacy = load_policy(EXISTING_POLICIES[0])
        legacy_decision = self.decide(chat(n=2), legacy)
        self.assertEqual(legacy_decision.message, "n must be the integer 1")

    def test_default_profile_rejects_extra_fields_like_before(self) -> None:
        for path in EXISTING_POLICIES:
            with self.subTest(path=path.name):
                decision = self.decide(chat(temperature=0.5), load_policy(path))
                self.assertEqual(decision.reason_code, "UNSUPPORTED_FIELD")


class ZerodayRelayPayloadTests(PolicyFileMixin, unittest.TestCase):
    def test_extra_fields_keep_original_values_and_gateway_values_win(self) -> None:
        state = self.load(v12_document(max_n=2))
        body = chat(temperature=0.5, cache_salt={"a": [1, 2]}, max_tokens=7, n=2)
        decision = validate_request(body, "application/json", state)
        self.assertEqual(decision.result, "ALLOWED")
        backend = BackendConfig.mock(("127.0.0.1", 1))
        document = json.loads(body)
        relayed = json.loads(relay_payload(backend, document, decision, stream=True))
        self.assertEqual(relayed["temperature"], 0.5)
        self.assertEqual(relayed["cache_salt"], {"a": [1, 2]})
        self.assertEqual(relayed["max_tokens"], 7)
        self.assertIs(relayed["stream"], True)
        self.assertEqual(relayed["n"], 2)
        self.assertEqual(relayed["model"], backend.mapped_model("test-model"))

    def test_default_relay_document_is_unchanged(self) -> None:
        state = load_policy(EXISTING_POLICIES[0])
        body = chat(n=1)
        decision = validate_request(body, "application/json", state)
        backend = BackendConfig.mock(("127.0.0.1", 1))
        relayed = json.loads(relay_payload(backend, json.loads(body), decision, stream=False))
        self.assertEqual(sorted(relayed), ["max_tokens", "messages", "model", "stream"])


class ZerodayGatewayHttpTests(unittest.TestCase):
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
        backend_path.write_text(json.dumps(backend_document(self.fake.base_url)), encoding="utf-8")
        config = MVPConfig(
            gateway_port=0,
            backend="vllm",
            backend_config_path=str(backend_path),
            policy_path=str(ZERODAY_POLICY),
            data_dir=str(self.directory / "data"),
            collector_interval_s=0.05,
            collector_stale_after_s=1,
        )
        try:
            self.gateway = GatewayApp(
                config,
                load_policy(ZERODAY_POLICY),
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

    def get(self, path: str) -> bytes:
        with urlopen(self.url(path), timeout=3) as response:
            return response.read()

    def test_extra_fields_reach_backend_and_only_names_are_recorded(self) -> None:
        status, headers, _ = self.post(
            chat(
                temperature=0.7,
                stop=["END"],
                cache_salt=SECRET_VALUE,
                logit_bias={"50256": -100},
            )
        )
        self.assertEqual(status, 200)
        request_id = headers["X-Request-ID"]
        received = [item for item in self.fake.requests if item["request_id"] == request_id]
        self.assertEqual(len(received), 1)
        values = received[0]["extra_values"]
        self.assertEqual(values["temperature"], 0.7)
        self.assertEqual(values["stop"], ["END"])
        self.assertEqual(values["cache_salt"], SECRET_VALUE)
        self.assertEqual(values["logit_bias"], {"50256": -100})
        self.assertEqual(received[0]["model"], "backend-test-model")

        records, corrupt = JsonlStore(self.directory / "data" / "gateway_records.jsonl").read_all()
        self.assertEqual(corrupt, [])
        final = [r for r in records if r["request_id"] == request_id and r["phase"] == "final"][-1]
        self.assertEqual(final["policy"]["profile"], "zeroday-lab")
        self.assertEqual(
            final["request"]["extra_fields"],
            [
                {"name": "cache_salt", "mode": "passthrough"},
                {"name": "logit_bias", "mode": "passthrough"},
                {"name": "stop", "mode": "validate"},
                {"name": "temperature", "mode": "validate"},
            ],
        )
        raw_store = (self.directory / "data" / "gateway_records.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(SECRET_VALUE, raw_store)
        evidence = self.get(f"/api/requests/{request_id}/evidence")
        self.assertNotIn(SECRET_VALUE.encode(), evidence)
        self.assertIn(b'"zeroday-lab"', evidence)
        self.assertIn(b'"cache_salt"', evidence)

    def test_stream_with_extra_fields_still_relays(self) -> None:
        status, headers, body = self.post(chat(stream=True, temperature=1, ignore_eos=True))
        self.assertEqual(status, 200)
        self.assertIn(b"[DONE]", body)
        received = [i for i in self.fake.requests if i["request_id"] == headers["X-Request-ID"]]
        self.assertTrue(received[0]["stream"])
        self.assertEqual(received[0]["extra_values"], {"temperature": 1, "ignore_eos": True})

    def test_backend_failure_record_keeps_relayed_field_names(self) -> None:
        self.fake.set_mode("http_500")
        status, headers, _ = self.post(chat(temperature=0.3, cache_salt=SECRET_VALUE))
        self.assertGreaterEqual(status, 500)
        records, _ = JsonlStore(self.directory / "data" / "gateway_records.jsonl").read_all()
        final = [
            r for r in records
            if r["request_id"] == headers["X-Request-ID"] and r["phase"] == "final"
        ][-1]
        self.assertTrue(final["delivery"]["attempted"])
        self.assertEqual(
            final["request"]["extra_fields"],
            [
                {"name": "cache_salt", "mode": "passthrough"},
                {"name": "temperature", "mode": "validate"},
            ],
        )

    def test_invalid_extra_field_is_not_relayed(self) -> None:
        before = self.fake.request_count
        status, _, body = self.post(chat(temperature=9))
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "INVALID_EXTRA_FIELD")
        self.assertEqual(self.fake.request_count, before)

    def test_status_reports_test_only_profile(self) -> None:
        status = json.loads(self.get("/api/status"))
        policy = status["policy"]
        self.assertTrue(policy["test_only"])
        self.assertEqual(policy["profile"], "zeroday-lab")
        self.assertEqual(policy["passthrough_count"], 9)
        self.assertEqual(policy["validate_count"], 8)
        policy_state = json.loads(self.get("/api/policy"))
        self.assertTrue(policy_state["current"]["test_only"])


if __name__ == "__main__":
    unittest.main()
