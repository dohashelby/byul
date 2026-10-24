"""Tests for the replaceable Gateway authentication frame (runtime-auth/1.0)."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.auth import (
    CHAT_ROUTE,
    load_auth,
    not_configured,
    token_digest,
)
from serving_runtime.mvp.backend import load_backend_config
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.fake_backend import FakeBackend
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.store import JsonlStore


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_AUTH = ROOT / "examples/mvp/auth.example.json"
INFERENCE_TOKEN = "lab-test-inference-token-0001"
VIEWER_TOKEN = "lab-test-viewer-token-000001"
DISABLED_TOKEN = "lab-test-disabled-token-0001"


def auth_document(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "runtime-auth/1.0",
        "auth_ref": "LAB-AUTH-TEST",
        "principals": [
            {
                "principal_id": "client-a",
                "roles": ["inference"],
                "token_sha256": token_digest(INFERENCE_TOKEN),
                "allowed_models": ["test-model"],
            },
            {
                "principal_id": "viewer-a",
                "roles": ["viewer"],
                "token_sha256": token_digest(VIEWER_TOKEN),
            },
            {
                "principal_id": "client-off",
                "roles": ["inference"],
                "token_sha256": token_digest(DISABLED_TOKEN),
                "disabled": True,
            },
        ],
        "route_roles": {CHAT_ROUTE: ["inference"]},
    }
    document.update(updates)
    return document


def policy_document() -> dict[str, object]:
    return {
        "schema_version": "runtime-mvp/1.0",
        "policy_id": "auth-test",
        "policy_version": "1",
        "target_id": "mock-engine-001",
        "allowed_models": ["test-model", "other-model"],
        "output_default_tokens": 16,
        "output_max_tokens": 32,
        "max_body_bytes": 65536,
    }


def chat(model: str = "test-model") -> bytes:
    return json.dumps(
        {"model": model, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 8}
    ).encode("utf-8")


class TempFiles:
    def write_json(self, name: str, document: object) -> Path:
        path = Path(self.temporary.name) / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path


class AuthConfigTests(TempFiles, unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def load(self, document: object):
        return load_auth(self.write_json("auth.json", document))

    def test_example_and_valid_config_load(self) -> None:
        example = load_auth(EXAMPLE_AUTH)
        self.assertEqual(example.state, "ok", example.error_message)
        state = self.load(auth_document())
        self.assertEqual(state.state, "ok", state.error_message)
        self.assertEqual(state.roles_for(CHAT_ROUTE), ("inference",))
        status = state.status()
        self.assertEqual(status["principal_count"], 2)
        self.assertEqual(status["disabled_principal_count"], 1)
        self.assertNotIn(token_digest(INFERENCE_TOKEN), json.dumps(status))

    def test_route_roles_default_to_inference(self) -> None:
        document = auth_document()
        document.pop("route_roles")
        self.assertEqual(self.load(document).roles_for(CHAT_ROUTE), ("inference",))

    def test_not_configured_and_missing_file(self) -> None:
        self.assertEqual(load_auth(None).state, "not_configured")
        missing = load_auth(Path(self.temporary.name) / "absent.json")
        self.assertEqual((missing.state, missing.error_code), ("error", "AUTH_CONFIG_NOT_FOUND"))

    def test_invalid_configs_become_error_state(self) -> None:
        good = auth_document()["principals"][0]
        cases = {
            "schema": auth_document(schema_version="runtime-auth/2.0"),
            "unknown-top": auth_document(extra=True),
            "empty-principals": auth_document(principals=[]),
            "unknown-principal-field": auth_document(principals=[dict(good, token="x")]),
            "bad-id": auth_document(principals=[dict(good, principal_id="bad id")]),
            "bad-digest": auth_document(principals=[dict(good, token_sha256="ABC")]),
            "empty-roles": auth_document(principals=[dict(good, roles=[])]),
            "bad-role": auth_document(principals=[dict(good, roles=["Bad Role"])]),
            "dup-role": auth_document(principals=[dict(good, roles=["a", "a"])]),
            "bool-disabled": auth_document(principals=[dict(good, disabled="no")]),
            "empty-models": auth_document(principals=[dict(good, allowed_models=[])]),
            "dup-id": auth_document(principals=[good, dict(good, token_sha256="0" * 64)]),
            "dup-digest": auth_document(principals=[good, dict(good, principal_id="other")]),
            "unprotectable-route": auth_document(route_roles={CHAT_ROUTE: ["inference"], "GET /api/status": ["viewer"]}),
            "empty-route-roles": auth_document(route_roles={}),
            "not-object": [],
        }
        for label, document in cases.items():
            with self.subTest(case=label):
                state = self.load(document)
                self.assertEqual(state.state, "error")
                self.assertIn(state.error_code, {"INVALID_AUTH_CONFIG"})
        duplicate = self.write_json("dup.json", {})
        duplicate.write_text('{"schema_version":"a","schema_version":"b"}', encoding="utf-8")
        self.assertEqual(load_auth(duplicate).error_code, "DUPLICATE_KEY")

    def test_authenticate_outcomes(self) -> None:
        state = self.load(auth_document())
        self.assertEqual(state.authenticate(None).state, "missing")
        self.assertEqual(state.authenticate("  ").state, "missing")
        self.assertEqual(state.authenticate(f"Basic {INFERENCE_TOKEN}").state, "invalid")
        self.assertEqual(state.authenticate("Bearer short").state, "invalid")
        self.assertEqual(state.authenticate("Bearer wrong-token-but-long-enough").state, "invalid")
        self.assertEqual(state.authenticate(f"Bearer {DISABLED_TOKEN}").state, "invalid")
        ok = state.authenticate(f"bearer {INFERENCE_TOKEN}")
        self.assertEqual((ok.state, ok.principal.principal_id), ("authenticated", "client-a"))
        record = json.dumps(ok.record())
        self.assertNotIn(INFERENCE_TOKEN, record)
        self.assertNotIn(token_digest(INFERENCE_TOKEN), record)
        self.assertEqual(not_configured().authenticate(None).state, "not_configured")
        broken = self.load({"schema_version": "x"})
        self.assertEqual(broken.authenticate(f"Bearer {INFERENCE_TOKEN}").state, "unavailable")

    def test_token_digest_rejects_unusable_tokens(self) -> None:
        for token in ("short", "has space in it here", "é" * 20, "x" * 513):
            with self.subTest(token=token[:10]):
                with self.assertRaises(ValueError):
                    token_digest(token)


class AuthHttpBase(TempFiles, unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name) / "data"
        self.policy_path = self.write_json("policy.json", policy_document())
        try:
            self.engine = MockEngine(
                port=0, receipts_path=self.data_dir / "engine_receipts.jsonl"
            ).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.engine.stop)

    def start(self, auth_path: Path | None) -> GatewayApp:
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(self.policy_path),
            data_dir=str(self.data_dir),
            auth_config_path=str(auth_path) if auth_path else None,
        )
        try:
            gateway = GatewayApp(config, load_policy(self.policy_path), self.engine.address).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(gateway.stop)
        self.gateway = gateway
        return gateway

    def url(self, path: str) -> str:
        return f"http://{self.gateway.address[0]}:{self.gateway.address[1]}{path}"

    def post(self, body: bytes, token: str | None = None, scheme: str = "Bearer"):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"{scheme} {token}"
        request = Request(self.url("/v1/chat/completions"), data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as error:
            with closing(error):
                return error.code, dict(error.headers), error.read()

    def status(self) -> dict[str, object]:
        with urlopen(self.url("/api/status"), timeout=3) as response:
            return json.loads(response.read())

    def final_record(self, request_id: str) -> dict[str, object]:
        records, corrupt = JsonlStore(self.data_dir / "gateway_records.jsonl").read_all()
        self.assertEqual(corrupt, [])
        return [r for r in records if r["request_id"] == request_id and r["phase"] == "final"][-1]

    def receipts(self) -> int:
        records, _ = JsonlStore(self.data_dir / "engine_receipts.jsonl").read_all()
        return len(records)


class AuthGatewayHttpTests(AuthHttpBase):
    def test_without_auth_config_requests_work_as_before(self) -> None:
        self.start(None)
        status, headers, _ = self.post(chat())
        self.assertEqual(status, 200)
        self.assertEqual(self.final_record(headers["X-Request-ID"])["auth"]["state"], "not_configured")
        self.assertEqual(self.status()["auth"]["state"], "not_configured")

    def test_auth_rejections_are_not_relayed(self) -> None:
        self.start(self.write_json("auth.json", auth_document()))
        cases = [
            (None, "Bearer", 401, "AUTH_REQUIRED"),
            ("wrong-token-but-long-enough", "Bearer", 401, "AUTH_INVALID"),
            (INFERENCE_TOKEN, "Basic", 401, "AUTH_INVALID"),
            (DISABLED_TOKEN, "Bearer", 401, "AUTH_INVALID"),
            (VIEWER_TOKEN, "Bearer", 403, "ROLE_NOT_ALLOWED"),
        ]
        for token, scheme, code, reason in cases:
            with self.subTest(reason=reason, token=(token or "none")[:8]):
                before = self.receipts()
                status, headers, body = self.post(chat(), token, scheme)
                self.assertEqual(status, code)
                self.assertEqual(json.loads(body)["error"]["code"], reason)
                if code == 401:
                    self.assertTrue(headers.get("WWW-Authenticate", "").startswith("Bearer"))
                self.assertEqual(self.receipts(), before)
                record = self.final_record(headers["X-Request-ID"])
                self.assertFalse(record["delivery"]["attempted"])
                self.assertEqual(record["decision"]["reason_code"], reason)

    def test_principal_model_restriction_and_success(self) -> None:
        self.start(self.write_json("auth.json", auth_document()))
        status, headers, body = self.post(chat("other-model"), INFERENCE_TOKEN)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "PRINCIPAL_MODEL_NOT_ALLOWED")
        denied = self.final_record(headers["X-Request-ID"])
        self.assertEqual(denied["auth"]["principal_id"], "client-a")
        self.assertFalse(denied["delivery"]["attempted"])

        status, headers, _ = self.post(chat(), INFERENCE_TOKEN)
        self.assertEqual(status, 200)
        record = self.final_record(headers["X-Request-ID"])
        self.assertEqual(
            record["auth"],
            {"state": "authenticated", "method": "bearer_sha256", "principal_id": "client-a", "roles": ["inference"]},
        )
        stored = (self.data_dir / "gateway_records.jsonl").read_text(encoding="utf-8")
        for secret in (INFERENCE_TOKEN, VIEWER_TOKEN, token_digest(INFERENCE_TOKEN)):
            self.assertNotIn(secret, stored)
        status_text = json.dumps(self.status()["auth"])
        self.assertNotIn(token_digest(INFERENCE_TOKEN), status_text)

    def test_broken_auth_config_fails_closed_as_error(self) -> None:
        self.start(self.write_json("auth.json", {"schema_version": "runtime-auth/1.0"}))
        status, headers, body = self.post(chat(), INFERENCE_TOKEN)
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"]["code"], "AUTH_UNAVAILABLE")
        record = self.final_record(headers["X-Request-ID"])
        self.assertEqual(record["decision"]["result"], "ERROR")
        self.assertEqual(self.receipts(), 0)
        # Whether reads were protected is unknown, so read APIs fail closed too.
        with self.assertRaises(HTTPError) as refused:
            self.status()
        with closing(refused.exception):
            self.assertEqual(refused.exception.code, 503)
            self.assertEqual(json.loads(refused.exception.read())["error"]["code"], "AUTH_UNAVAILABLE")
        overall = self.gateway.status()
        self.assertEqual(overall["status"], "degraded")
        self.assertEqual(overall["auth"]["state"], "error")


class ReadApiAuthTests(AuthHttpBase):
    def get(self, path: str, token: str | None = None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        request = Request(self.url(path), headers=headers)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as error:
            with closing(error):
                return error.code, dict(error.headers), error.read()

    def test_read_apis_open_unless_configured(self) -> None:
        self.start(self.write_json("auth.json", auth_document()))
        status, _, body = self.get("/api/status")
        self.assertEqual(status, 200)
        self.assertIn("GET /api/*", json.loads(body)["auth"]["unprotected"])

    def test_configured_read_protection(self) -> None:
        document = auth_document(route_roles={CHAT_ROUTE: ["inference"], "GET /api/*": ["viewer"]})
        self.start(self.write_json("auth.json", document))
        status, inference_headers, _ = self.post(chat(), INFERENCE_TOKEN)
        self.assertEqual(status, 200)
        request_id = inference_headers["X-Request-ID"]
        paths = ["/api/status", "/api/requests", f"/api/requests/{request_id}", f"/api/requests/{request_id}/evidence", "/api/observations", "/api/target"]
        for path in paths:
            with self.subTest(path=path):
                code, headers, body = self.get(path)
                self.assertEqual(code, 401)
                self.assertTrue(headers.get("WWW-Authenticate", "").startswith("Bearer"))
                self.assertEqual(json.loads(body)["error"]["code"], "AUTH_REQUIRED")
                self.assertEqual(self.get(path, "wrong-token-but-long-enough")[0], 401)
                self.assertEqual(self.get(path, INFERENCE_TOKEN)[0], 403)
                self.assertEqual(self.get(path, VIEWER_TOKEN)[0], 200)
        # Static dashboard files carry no data and stay public.
        for path in ("/", "/static/app.js"):
            self.assertEqual(self.get(path)[0], 200)
        status_body = json.loads(self.get("/api/status", VIEWER_TOKEN)[2])
        self.assertNotIn("GET /api/*", status_body["auth"]["unprotected"])
        self.assertIn("GET /api/*", status_body["auth"]["protected_routes"])

    def test_unprotectable_route_still_rejected(self) -> None:
        state = load_auth(self.write_json("bad.json", auth_document(route_roles={CHAT_ROUTE: ["inference"], "GET /static/*": ["viewer"]})))
        self.assertEqual(state.state, "error")
        missing_chat = load_auth(self.write_json("bad2.json", auth_document(route_roles={"GET /api/*": ["viewer"]})))
        self.assertEqual(missing_chat.state, "error")


class AuthHeaderNotForwardedTests(TempFiles, unittest.TestCase):
    def test_client_token_is_not_sent_to_backend(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        try:
            fake = FakeBackend(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(fake.stop)
        backend_path = self.write_json(
            "backend.json",
            {
                "schema_version": "runtime-backend/1.0",
                "kind": "vllm",
                "base_url": fake.base_url,
                "model_map": {"test-model": "backend-test-model"},
                "health_path": "/health",
                "models_path": "/v1/models",
                "timeouts": {"relay_timeout_s": 0.5, "stream_idle_timeout_s": 0.5, "request_deadline_s": 2},
            },
        )
        policy_path = self.write_json("policy.json", policy_document())
        config = MVPConfig(
            gateway_port=0,
            backend="vllm",
            backend_config_path=str(backend_path),
            policy_path=str(policy_path),
            data_dir=str(Path(self.temporary.name) / "data"),
            auth_config_path=str(self.write_json("auth.json", auth_document())),
        )
        try:
            gateway = GatewayApp(
                config,
                load_policy(policy_path),
                backend_config=load_backend_config(backend_path, environ={}),
            ).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(gateway.stop)
        request = Request(
            f"http://{gateway.address[0]}:{gateway.address[1]}/v1/chat/completions",
            data=chat(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {INFERENCE_TOKEN}"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 200)
            request_id = response.headers["X-Request-ID"]
        received = [item for item in fake.requests if item["request_id"] == request_id]
        self.assertEqual(len(received), 1)
        self.assertFalse(received[0]["authorization_present"])


if __name__ == "__main__":
    unittest.main()
