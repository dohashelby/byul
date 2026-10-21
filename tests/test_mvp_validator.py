"""Tests for ordered, transport-independent MVP request decisions."""

import json
from pathlib import Path
import unittest

from serving_runtime.mvp.policy import PolicyState, load_policy
from serving_runtime.mvp.validator import validate_request


EXAMPLE_POLICY = Path(__file__).resolve().parents[1] / "examples/mvp/policy.json"


def request_document(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hello"}],
    }
    document.update(updates)
    return document


def encoded(**updates: object) -> bytes:
    return json.dumps(request_document(**updates)).encode("utf-8")


class ValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy_state = load_policy(EXAMPLE_POLICY)

    def decide(self, body: bytes, content_type: str | None = "application/json"):
        return validate_request(body, content_type, self.policy_state)

    def test_mvp_02_limit_is_allowed_without_silent_clamping(self) -> None:
        allowed = self.decide(encoded(max_tokens=128))
        denied = self.decide(encoded(max_tokens=129))
        self.assertEqual((allowed.result, allowed.http_status, allowed.max_tokens), ("ALLOWED", 200, 128))
        self.assertEqual((denied.result, denied.http_status, denied.max_tokens), ("DENIED", 403, 129))
        self.assertEqual(denied.reason_code, "MAX_TOKENS_EXCEEDED")
        self.assertEqual(denied.rule_id, "output_max_tokens")

    def test_mvp_04_disallowed_model(self) -> None:
        decision = self.decide(encoded(model="other-model", max_tokens=64))
        self.assertEqual((decision.result, decision.http_status), ("DENIED", 403))
        self.assertEqual(decision.reason_code, "MODEL_NOT_ALLOWED")
        self.assertEqual(decision.rule_id, "allowed_models")

    def test_mvp_05_default_and_invalid_max_tokens(self) -> None:
        defaulted = self.decide(encoded())
        self.assertEqual((defaulted.result, defaulted.max_tokens), ("ALLOWED", 128))
        for value in (0, -1, "128", True):
            with self.subTest(value=value):
                decision = self.decide(encoded(max_tokens=value))
                self.assertEqual((decision.result, decision.http_status), ("INVALID", 400))
                self.assertEqual(decision.reason_code, "INVALID_MAX_TOKENS")
        duplicate = self.decide(
            b'{"model":"test-model","model":"other-model","messages":[{"role":"user","content":"x"}]}'
        )
        self.assertEqual((duplicate.http_status, duplicate.reason_code), (400, "DUPLICATE_KEY"))

    def test_mvp_06_unsupported_inputs_and_transport_limits(self) -> None:
        cases = [
            (encoded(stream_options={}), "application/json", 400, "UNSUPPORTED_FIELD"),
            (encoded(tools=[]), "application/json", 400, "UNSUPPORTED_FIELD"),
            (encoded(max_completion_tokens=10), "application/json", 400, "UNSUPPORTED_ALIAS"),
            (b" " * 65537, "application/json", 413, "BODY_TOO_LARGE"),
            (encoded(), "text/plain", 415, "UNSUPPORTED_MEDIA_TYPE"),
        ]
        for body, content_type, status, reason in cases:
            with self.subTest(reason=reason):
                decision = self.decide(body, content_type)
                self.assertEqual((decision.result, decision.http_status), ("INVALID", status))
                self.assertEqual(decision.reason_code, reason)

    def test_stream_true_and_false_are_allowed_before_policy_decision(self) -> None:
        for value in (True, False):
            with self.subTest(stream=value):
                self.assertEqual(self.decide(encoded(stream=value)).result, "ALLOWED")
        denied = self.decide(encoded(stream=True, max_tokens=129))
        self.assertEqual((denied.http_status, denied.reason_code), (403, "MAX_TOKENS_EXCEEDED"))

    def test_content_type_parameters_and_case_are_accepted(self) -> None:
        decision = self.decide(encoded(max_tokens=64), "Application/JSON; charset=utf-8")
        self.assertEqual(decision.result, "ALLOWED")

    def test_policy_unavailable_precedes_request_errors(self) -> None:
        unavailable = PolicyState(
            False, None, "missing", None, "2026-01-01T00:00:00+00:00", "POLICY_NOT_FOUND", "missing"
        )
        decision = validate_request(b"not json", "text/plain", unavailable)
        self.assertEqual((decision.result, decision.http_status, decision.reason_code), (
            "ERROR", 503, "POLICY_UNAVAILABLE"
        ))

    def test_input_error_precedes_policy_denial(self) -> None:
        decision = self.decide(
            json.dumps({"model": "forbidden", "messages": []}).encode("utf-8")
        )
        self.assertEqual((decision.result, decision.reason_code), ("INVALID", "INVALID_MESSAGES"))

    def test_message_stream_and_n_shapes_are_strict(self) -> None:
        cases = [
            (encoded(messages=[]), "INVALID_MESSAGES"),
            (encoded(messages=[{"role": "tool", "content": "x"}]), "INVALID_MESSAGES"),
            (encoded(messages=[{"role": "user", "content": "x", "name": "n"}]), "INVALID_MESSAGES"),
            (encoded(stream="false"), "INVALID_STREAM"),
            (encoded(n=True), "N_UNSUPPORTED"),
            (encoded(n=2), "N_UNSUPPORTED"),
        ]
        for body, reason in cases:
            with self.subTest(reason=reason):
                decision = self.decide(body)
                self.assertEqual((decision.http_status, decision.reason_code), (400, reason))

    def test_unhashable_message_role_is_invalid(self) -> None:
        decision = self.decide(encoded(messages=[{"role": ["user"], "content": "x"}]))
        self.assertEqual(
            (decision.result, decision.http_status, decision.reason_code),
            ("INVALID", 400, "INVALID_MESSAGES"),
        )

    def test_deeply_nested_json_is_malformed(self) -> None:
        body = b'{"model":"test-model","messages":' + b"[" * 32000 + b"]" * 32000 + b"}"
        self.assertLessEqual(len(body), 65536)
        decision = self.decide(body)
        self.assertEqual(
            (decision.result, decision.http_status, decision.reason_code),
            ("INVALID", 400, "MALFORMED_JSON"),
        )


if __name__ == "__main__":
    unittest.main()
