"""Policy, HMAC, relay, readiness, and non-disclosure tests for cache isolation."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.auth import token_digest
from serving_runtime.mvp.backend import load_backend_config
from serving_runtime.mvp.cache_isolation import (
    CACHE_SALT_KEY_ENV,
    CacheSaltKey,
)
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.fake_backend import FakeBackend
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.store import JsonlStore


KEY = "cache-isolation-key-material-32-bytes-minimum"
OTHER_KEY = "different-cache-isolation-key-material-32-bytes"
TOKEN_A = "cache-principal-a-token"
TOKEN_B = "cache-principal-b-token"
ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_POLICY = ROOT / "examples/mvp/policy-v1.2-cache-isolation.json"


def policy_document(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "runtime-mvp/1.2",
        "policy_id": "cache-isolation-test",
        "policy_version": "1",
        "target_id": "test-vllm-target",
        "allowed_models": ["test-model"],
        "output_default_tokens": 16,
        "output_max_tokens": 32,
        "max_body_bytes": 65536,
        "profile": "default",
        "cache_isolation": "per_principal",
    }
    document.update(updates)
    return document


def backend_document(base_url: str, *, kind: str = "vllm") -> dict[str, object]:
    return {
        "schema_version": "runtime-backend/1.0",
        "kind": kind,
        "base_url": base_url,
        "model_map": {"test-model": "backend-test-model"},
        "health_path": "/health",
        "models_path": "/v1/models" if kind == "vllm" else "/get_model_info",
        "version_path": "/version" if kind == "vllm" else None,
        "timeouts": {
            "relay_timeout_s": 0.5,
            "stream_idle_timeout_s": 0.5,
            "request_deadline_s": 2,
        },
    }


class PolicyAndSaltTests(unittest.TestCase):
    def load(self, document: dict[str, object]):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "policy.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return load_policy(path)

    def test_v12_defaults_off_and_accepts_per_principal(self) -> None:
        default = policy_document()
        default.pop("cache_isolation")
        off = self.load(default)
        enabled = self.load(policy_document())
        self.assertTrue(off.ok, off.error_message)
        self.assertEqual(off.policy.cache_isolation, "off")
        self.assertTrue(enabled.ok, enabled.error_message)
        self.assertEqual(enabled.policy.cache_isolation, "per_principal")

        example = load_policy(EXAMPLE_POLICY)
        self.assertTrue(example.ok, example.error_message)
        self.assertEqual(example.policy.cache_isolation, "per_principal")
        self.assertEqual(example.policy.approval_ref, "EXAMPLE-NOT-APPROVED")

    def test_invalid_value_and_v11_field_are_rejected(self) -> None:
        for value in ("per_tenant", [], {}, 1, True, None):
            with self.subTest(value=value):
                invalid = self.load(policy_document(cache_isolation=value))
                self.assertEqual(
                    (invalid.ok, invalid.error_code),
                    (False, "INVALID_POLICY"),
                )
                self.assertEqual(
                    invalid.error_message,
                    "cache_isolation must be off or per_principal",
                )
        v11 = policy_document(schema_version="runtime-mvp/1.1")
        v11.pop("profile")
        old_schema = self.load(v11)
        self.assertEqual((old_schema.ok, old_schema.error_code), (False, "UNKNOWN_FIELD"))

    def test_per_principal_rejects_cache_salt_extra_field_in_every_profile(self) -> None:
        rules = (
            {"mode": "validate", "type": "string", "max_length": 128},
            {"mode": "passthrough"},
        )
        for profile in ("default", "zeroday-lab"):
            for rule in rules:
                with self.subTest(profile=profile, mode=rule["mode"]):
                    state = self.load(
                        policy_document(
                            profile=profile,
                            extra_fields={"cache_salt": rule},
                        )
                    )
                    self.assertFalse(state.ok)
                    self.assertEqual(state.error_code, "INVALID_POLICY")
                    self.assertIn("cache_salt", state.error_message)

    def test_hmac_salt_properties(self) -> None:
        first_key = CacheSaltKey.from_environ({CACHE_SALT_KEY_ENV: KEY})
        second_key = CacheSaltKey.from_environ({CACHE_SALT_KEY_ENV: OTHER_KEY})
        self.assertEqual(
            CacheSaltKey.from_environ({CACHE_SALT_KEY_ENV: "x" * 31}).state,
            "too_short",
        )
        self.assertEqual(
            CacheSaltKey.from_environ({CACHE_SALT_KEY_ENV: "x" * 32}).state,
            "configured",
        )
        self.assertNotIn(KEY, repr(first_key))
        alice_1 = first_key.derive("alice")
        alice_2 = first_key.derive("alice")
        bob = first_key.derive("bob")
        self.assertEqual(alice_1, alice_2)
        self.assertNotEqual(alice_1, bob)
        self.assertNotEqual(alice_1, second_key.derive("alice"))
        self.assertRegex(alice_1, re.compile(r"^[0-9a-f]{64}$"))
        self.assertLessEqual(len(alice_1), 128)
        self.assertFalse(any(character in alice_1 for character in "@/\\\x00"))


class CacheIsolationHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        try:
            self.fake = FakeBackend(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.fake.stop)
        self.gateways: list[GatewayApp] = []
        self.policy_path = self.write_json("policy.json", policy_document())
        self.auth_path = self.write_json(
            "auth.json",
            {
                "schema_version": "runtime-auth/1.0",
                "principals": [
                    {
                        "principal_id": "principal-a",
                        "roles": ["inference"],
                        "token_sha256": token_digest(TOKEN_A),
                    },
                    {
                        "principal_id": "principal-b",
                        "roles": ["inference"],
                        "token_sha256": token_digest(TOKEN_B),
                    },
                ],
            },
        )
        self.backend_path = self.write_json(
            "backend.json", backend_document(self.fake.base_url)
        )

    def tearDown(self) -> None:
        for gateway in reversed(self.gateways):
            gateway.stop()

    def write_json(self, name: str, document: object) -> Path:
        path = self.root / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def start_external(
        self,
        *,
        policy_path: Path | None = None,
        auth_path: Path | None = None,
        key: str | None = KEY,
        kind: str = "vllm",
        suffix: str = "main",
    ) -> GatewayApp:
        actual_policy = policy_path or self.policy_path
        backend_path = self.backend_path
        if kind != "vllm":
            backend_path = self.write_json(
                f"backend-{suffix}.json",
                backend_document(self.fake.base_url, kind=kind),
            )
        data_dir = self.root / f"data-{suffix}"
        config = MVPConfig(
            gateway_port=0,
            backend=kind,
            backend_config_path=str(backend_path),
            policy_path=str(actual_policy),
            auth_config_path=str(auth_path) if auth_path is not None else None,
            data_dir=str(data_dir),
            collector_interval_s=60,
            collector_stale_after_s=120,
        )
        environ = {} if key is None else {CACHE_SALT_KEY_ENV: key}
        backend = load_backend_config(backend_path, environ={})
        try:
            gateway = GatewayApp(
                config,
                load_policy(actual_policy),
                backend_config=backend,
                cache_salt_environ=environ,
            ).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.gateways.append(gateway)
        return gateway

    @staticmethod
    def url(gateway: GatewayApp, path: str) -> str:
        return f"http://{gateway.address[0]}:{gateway.address[1]}{path}"

    def post(
        self,
        gateway: GatewayApp,
        *,
        token: str,
        route: str = "/v1/chat/completions",
        cache_salt: str | None = None,
    ) -> tuple[int, bytes]:
        document: dict[str, object] = {
            "model": "test-model",
            "max_tokens": 8,
        }
        if route.endswith("/completions") and "/chat/" not in route:
            document["prompt"] = "hello"
        else:
            document["messages"] = [{"role": "user", "content": "hello"}]
        if cache_salt is not None:
            document["cache_salt"] = cache_salt
        request = Request(
            self.url(gateway, route),
            data=json.dumps(document).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.read()
        except HTTPError as error:
            with closing(error):
                return error.code, error.read()

    def get_json(self, gateway: GatewayApp, path: str) -> dict[str, object]:
        with urlopen(self.url(gateway, path), timeout=3) as response:
            value = json.loads(response.read())
        self.assertIsInstance(value, dict)
        return value

    def records(self, suffix: str) -> list[dict[str, object]]:
        rows, corrupt = JsonlStore(
            self.root / f"data-{suffix}" / "gateway_records.jsonl"
        ).read_all()
        self.assertEqual(corrupt, [])
        return rows

    def test_chat_and_completions_are_stable_per_principal_and_separated(self) -> None:
        gateway = self.start_external(auth_path=self.auth_path, suffix="relay")
        for token, route in (
            (TOKEN_A, "/v1/chat/completions"),
            (TOKEN_A, "/v1/chat/completions"),
            (TOKEN_B, "/v1/chat/completions"),
            (TOKEN_A, "/v1/completions"),
        ):
            self.assertEqual(self.post(gateway, token=token, route=route)[0], 200)

        requests = self.fake.requests[-4:]
        salts = [item["extra_values"]["cache_salt"] for item in requests]
        self.assertEqual(salts[0], salts[1])
        self.assertEqual(salts[0], salts[3])
        self.assertNotEqual(salts[0], salts[2])
        self.assertEqual(requests[3]["route"], "POST /v1/completions")
        for record in self.records("relay"):
            self.assertEqual(
                record["request"]["cache_isolation"],
                {"mode": "per_principal", "applied": True},
            )

    def test_client_cache_salt_is_rejected_without_backend_delivery(self) -> None:
        gateway = self.start_external(auth_path=self.auth_path, suffix="client")
        before = self.fake.request_count
        for route in ("/v1/chat/completions", "/v1/completions"):
            with self.subTest(route=route):
                status, body = self.post(
                    gateway,
                    token=TOKEN_A,
                    route=route,
                    cache_salt="client-controlled-value",
                )
                self.assertEqual(status, 400)
                self.assertEqual(
                    json.loads(body)["error"]["code"], "UNSUPPORTED_FIELD"
                )
        self.assertEqual(self.fake.request_count, before)
        for record in self.records("client"):
            self.assertEqual(
                record["request"]["cache_isolation"],
                {"mode": "per_principal", "applied": False},
            )

    def test_auth_key_and_backend_readiness_fail_closed_before_delivery(self) -> None:
        cases = (
            ("auth", None, KEY, "vllm", "CACHE_ISOLATION_AUTH_NOT_CONFIGURED"),
            ("missing", self.auth_path, None, "vllm", "CACHE_ISOLATION_KEY_MISSING"),
            ("short", self.auth_path, "short", "vllm", "CACHE_ISOLATION_KEY_TOO_SHORT"),
            ("sglang", self.auth_path, KEY, "sglang", "CACHE_ISOLATION_BACKEND_UNSUPPORTED"),
        )
        for suffix, auth_path, key, kind, readiness_code in cases:
            with self.subTest(case=suffix):
                gateway = self.start_external(
                    auth_path=auth_path,
                    key=key,
                    kind=kind,
                    suffix=suffix,
                )
                before = self.fake.request_count
                status, body = self.post(gateway, token=TOKEN_A)
                self.assertEqual(status, 503)
                self.assertEqual(
                    json.loads(body)["error"]["code"],
                    "CACHE_ISOLATION_UNAVAILABLE",
                )
                if key:
                    self.assertNotIn(key, body.decode("utf-8"))
                self.assertEqual(self.fake.request_count, before)
                cache_status = self.get_json(gateway, "/api/status")["cache_isolation"]
                self.assertFalse(cache_status["ready"])
                self.assertEqual(
                    cache_status["unavailable_reason_code"], readiness_code
                )

    def test_mock_backend_is_unavailable_for_enabled_policy(self) -> None:
        try:
            engine = MockEngine(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(engine.stop)
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(self.policy_path),
            auth_config_path=str(self.auth_path),
            data_dir=str(self.root / "data-mock"),
        )
        gateway = GatewayApp(
            config,
            load_policy(self.policy_path),
            engine.address,
            cache_salt_environ={CACHE_SALT_KEY_ENV: KEY},
        ).start()
        self.gateways.append(gateway)
        status, body = self.post(gateway, token=TOKEN_A)
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"]["code"], "CACHE_ISOLATION_UNAVAILABLE")
        self.assertEqual(engine.receipts, [])

    def test_status_records_and_query_disclose_only_mode_and_applied(self) -> None:
        gateway = self.start_external(auth_path=self.auth_path, suffix="secret")
        response_status, response_body = self.post(gateway, token=TOKEN_A)
        self.assertEqual(response_status, 200)
        salt = self.fake.requests[-1]["extra_values"]["cache_salt"]
        status = self.get_json(gateway, "/api/status")
        policy = self.get_json(gateway, "/api/policy")
        requests = self.get_json(gateway, "/api/requests")
        self.assertEqual(
            status["cache_isolation"],
            {"mode": "per_principal", "ready": True, "unavailable_reason_code": None},
        )
        self.assertEqual(policy["current"]["cache_isolation"], status["cache_isolation"])
        self.assertEqual(
            requests["requests"][0]["cache_isolation"],
            {"mode": "per_principal", "applied": True},
        )
        public_text = json.dumps([status, policy, requests], ensure_ascii=False)
        public_text += response_body.decode("utf-8")
        stored_text = "".join(
            path.read_text(encoding="utf-8")
            for path in (self.root / "data-secret").glob("*.jsonl")
        )
        for secret in (KEY, salt):
            self.assertNotIn(secret, public_text)
            self.assertNotIn(secret, stored_text)

    def test_off_mode_is_explicit_and_does_not_inject(self) -> None:
        off_path = self.write_json(
            "policy-off.json", policy_document(cache_isolation="off")
        )
        gateway = self.start_external(
            policy_path=off_path,
            auth_path=None,
            key=None,
            suffix="off",
        )
        status, _ = self.post(gateway, token=TOKEN_A)
        self.assertEqual(status, 200)
        self.assertNotIn("cache_salt", self.fake.requests[-1]["field_names"])
        cache_status = self.get_json(gateway, "/api/status")["cache_isolation"]
        self.assertEqual(
            cache_status,
            {"mode": "off", "ready": True, "unavailable_reason_code": None},
        )
        final = self.records("off")[-1]
        self.assertEqual(
            final["request"]["cache_isolation"],
            {"mode": "off", "applied": False},
        )


if __name__ == "__main__":
    unittest.main()
