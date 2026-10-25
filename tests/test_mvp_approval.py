"""Policy approval verifier frame and Gateway fail-closed behavior tests."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.approval import ApprovalVerifier, HashListApprovalVerifier
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.policy_state import evaluate_policy, load_revocations


def policy_document() -> dict[str, object]:
    return {
        "schema_version": "runtime-mvp/1.1",
        "policy_id": "approval-test",
        "policy_version": "1",
        "target_id": "mock-engine-001",
        "allowed_models": ["test-model"],
        "output_default_tokens": 16,
        "output_max_tokens": 32,
        "max_body_bytes": 65536,
        "approval_ref": "LAB-APPROVAL-TEST",
    }


def approval_document(policy_path: Path, **entry_updates: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "policy_id": "approval-test",
        "policy_version": "1",
        "sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        "approval_ref": "LAB-APPROVAL-TEST",
        "approved_at": "2026-01-01T00:00:00Z",
    }
    entry.update(entry_updates)
    return {"schema_version": "runtime-policy-approvals/1.0", "entries": [entry]}


class ApprovalVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.policy_path = self.directory / "policy.json"
        self.policy_path.write_text(json.dumps(policy_document()), encoding="utf-8")
        self.policy_state = load_policy(self.policy_path)
        self.approvals_path = self.directory / "approvals.json"

    def write(self, document: object) -> None:
        self.approvals_path.write_text(json.dumps(document), encoding="utf-8")

    def verify(self, *, now: datetime | None = None):
        verifier: ApprovalVerifier = HashListApprovalVerifier(self.approvals_path)
        policy = self.policy_state.policy
        assert policy is not None and self.policy_state.raw_bytes is not None
        return verifier.verify(
            self.policy_state.raw_bytes,
            policy.policy_id,
            policy.policy_version,
            policy.approval_ref,
            now=now,
        )

    def test_match_is_verified_and_unconfigured_evaluation_stays_active(self) -> None:
        self.write(approval_document(self.policy_path))
        result = self.verify(now=datetime(2026, 10, 25, tzinfo=timezone.utc))
        self.assertEqual(result.state, "verified")
        self.assertTrue(result.evidence["policy_sha256_match"])
        configured = evaluate_policy(
            self.policy_state,
            load_revocations(None),
            approval=result,
        )
        self.assertTrue(configured["gateway_active"])
        unconfigured = evaluate_policy(self.policy_state, load_revocations(None))
        self.assertTrue(unconfigured["gateway_active"])
        self.assertEqual(unconfigured["approval"]["state"], "not_configured")

    def test_mismatch_missing_malformed_and_expired_are_not_verified(self) -> None:
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        cases = {
            "hash": (approval_document(self.policy_path, sha256="0" * 64), "rejected"),
            "missing": ({"schema_version": "runtime-policy-approvals/1.0", "entries": []}, "unverified"),
            "malformed": (None, "error"),
            "expired": (
                approval_document(
                    self.policy_path,
                    valid_until=(now - timedelta(seconds=1)).isoformat(),
                ),
                "rejected",
            ),
        }
        for label, (document, expected) in cases.items():
            with self.subTest(case=label):
                if document is None:
                    self.approvals_path.write_text("{broken", encoding="utf-8")
                else:
                    self.write(document)
                result = self.verify(now=now)
                self.assertEqual(result.state, expected)
                evaluated = evaluate_policy(
                    self.policy_state,
                    load_revocations(None),
                    approval=result,
                )
                self.assertFalse(evaluated["gateway_active"])
                self.assertIn(
                    evaluated["reason_code"],
                    {
                        "POLICY_APPROVAL_UNVERIFIED",
                        "POLICY_APPROVAL_REJECTED",
                        "POLICY_APPROVAL_ERROR",
                    },
                )


class ApprovalGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.policy_path = self.directory / "policy.json"
        self.policy_path.write_text(json.dumps(policy_document()), encoding="utf-8")
        self.approvals_path = self.directory / "approvals.json"
        self.replace_approvals(approval_document(self.policy_path))
        try:
            self.engine = MockEngine(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.engine.stop)
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(self.policy_path),
            policy_approvals_path=str(self.approvals_path),
            data_dir=str(self.directory / "data"),
        )
        try:
            self.gateway = GatewayApp(
                config, load_policy(self.policy_path), self.engine.address
            ).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.gateway.stop)
        self.base_url = f"http://{self.gateway.address[0]}:{self.gateway.address[1]}"

    def replace_approvals(self, document: object, *, raw: bool = False) -> None:
        replacement = self.directory / "approvals.replacement"
        replacement.write_text(
            str(document) if raw else json.dumps(document),
            encoding="utf-8",
        )
        os.replace(replacement, self.approvals_path)

    def post(self) -> tuple[int, dict[str, object]]:
        request = Request(
            self.base_url + "/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "approval test"}],
                    "max_tokens": 8,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            with closing(error):
                return error.code, json.loads(error.read())

    def get(self, path: str) -> dict[str, object]:
        with urlopen(self.base_url + path, timeout=3) as response:
            return json.loads(response.read())

    def test_replacement_is_rechecked_and_failures_are_503_without_delivery(self) -> None:
        status = self.get("/api/status")
        self.assertEqual(status["policy"]["approval"]["state"], "verified")
        history = self.get("/api/policy")["history"][0]
        self.assertEqual(history["approval_state"], "verified")
        self.assertEqual(self.post()[0], 200)
        delivered = len(self.engine.receipts)

        now = datetime.now(timezone.utc)
        cases = {
            "hash": approval_document(self.policy_path, sha256="0" * 64),
            "missing": {"schema_version": "runtime-policy-approvals/1.0", "entries": []},
            "broken": "{broken",
            "expired": approval_document(
                self.policy_path,
                approved_at=(now - timedelta(days=2)).isoformat(),
                valid_until=(now - timedelta(days=1)).isoformat(),
            ),
        }
        for label, document in cases.items():
            with self.subTest(case=label):
                self.replace_approvals(document, raw=label == "broken")
                http_status, body = self.post()
                self.assertEqual(http_status, 503)
                self.assertTrue(body["error"]["code"].startswith("POLICY_APPROVAL_"))
                self.assertEqual(len(self.engine.receipts), delivered)

        self.replace_approvals(approval_document(self.policy_path))
        self.assertEqual(self.post()[0], 200)
        self.assertEqual(len(self.engine.receipts), delivered + 1)


if __name__ == "__main__":
    unittest.main()
