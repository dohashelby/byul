"""Tests for strict MVP policy loading."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from serving_runtime.mvp.policy import load_policy


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_POLICY = ROOT / "examples/mvp/policy.json"


def policy_document() -> dict[str, object]:
    return {
        "schema_version": "runtime-mvp/1.0",
        "policy_id": "local-policy-001",
        "policy_version": "1",
        "target_id": "mock-engine-001",
        "allowed_models": ["test-model"],
        "output_default_tokens": 128,
        "output_max_tokens": 128,
        "max_body_bytes": 65536,
    }


class PolicyTests(unittest.TestCase):
    def test_example_loads_with_byte_sha256(self) -> None:
        state = load_policy(EXAMPLE_POLICY)
        self.assertTrue(state.ok, state.error_message)
        self.assertIsNotNone(state.policy)
        self.assertEqual(state.policy.allowed_models, ("test-model",))
        self.assertEqual(state.policy.output_default_tokens, 128)
        self.assertEqual(
            state.sha256, hashlib.sha256(EXAMPLE_POLICY.read_bytes()).hexdigest()
        )
        self.assertIn("+00:00", state.loaded_at)

    def test_missing_file_is_state_not_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = load_policy(Path(directory) / "missing.json")
        self.assertFalse(state.ok)
        self.assertIsNone(state.sha256)
        self.assertEqual(state.error_code, "POLICY_NOT_FOUND")

    def test_duplicate_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(
                json.dumps(policy_document())[:-1] + ', "policy_id": "other"}',
                encoding="utf-8",
            )
            state = load_policy(path)
        self.assertFalse(state.ok)
        self.assertEqual(state.error_code, "DUPLICATE_KEY")
        self.assertIsNotNone(state.sha256)

    def test_bool_is_not_an_integer(self) -> None:
        document = policy_document()
        document["output_max_tokens"] = True
        state = self._load_document(document)
        self.assertFalse(state.ok)
        self.assertEqual(state.error_code, "INVALID_POLICY")

    def test_default_must_not_exceed_maximum(self) -> None:
        document = policy_document()
        document["output_default_tokens"] = 129
        state = self._load_document(document)
        self.assertFalse(state.ok)
        self.assertIn("must not exceed", state.error_message)

    def test_unknown_field_is_rejected(self) -> None:
        document = policy_document()
        document["extra"] = "no"
        state = self._load_document(document)
        self.assertFalse(state.ok)
        self.assertEqual(state.error_code, "UNKNOWN_FIELD")

    def test_deeply_nested_json_is_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_bytes(b"[" * 32000 + b"]" * 32000)
            state = load_policy(path)
        self.assertFalse(state.ok)
        self.assertEqual(state.error_code, "MALFORMED_JSON")

    def _load_document(self, document: dict[str, object]):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            return load_policy(path)


if __name__ == "__main__":
    unittest.main()
