"""Tests for ordered, transport-independent MVP request decisions."""

import json
from pathlib import Path
import tempfile
import unittest

from serving_runtime.mvp.policy import PolicyState, load_policy
from serving_runtime.mvp.selected_profile import UNSUPPORTED_ENTRY_GROUPS
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

    def test_completion_prompt_and_fields_are_route_specific(self) -> None:
        def completion(**updates: object):
            document: dict[str, object] = {
                "model": "test-model",
                "prompt": "hello",
                "max_tokens": 8,
            }
            document.update(updates)
            return validate_request(
                json.dumps(document).encode("utf-8"),
                "application/json",
                self.policy_state,
                request_kind="completions",
            )

        self.assertEqual(completion().result, "ALLOWED")
        for prompt in (["hello"], [1, 2], 3, None):
            with self.subTest(prompt=prompt):
                decision = completion(prompt=prompt)
                self.assertEqual(
                    (decision.result, decision.reason_code),
                    ("INVALID", "INVALID_PROMPT_TYPE"),
                )
        for field in ("echo", "suffix", "best_of", "logprobs"):
            decision = completion(**{field: True})
            self.assertEqual(decision.reason_code, "UNSUPPORTED_FIELD")

    def test_deeply_nested_json_is_malformed(self) -> None:
        body = b'{"model":"test-model","messages":' + b"[" * 32000 + b"]" * 32000 + b"}"
        self.assertLessEqual(len(body), 65536)
        decision = self.decide(body)
        self.assertEqual(
            (decision.result, decision.http_status, decision.reason_code),
            ("INVALID", 400, "MALFORMED_JSON"),
        )

    def test_code_selection_groups_reject_every_forbidden_top_level_key(self) -> None:
        for group, reason in (
            ("code_selection", "CODE_SELECTION_FIELD"),
            ("input_processors", "INPUT_PROCESSOR_FIELD"),
        ):
            for name in UNSUPPORTED_ENTRY_GROUPS[group]:
                with self.subTest(group=group, name=name):
                    decision = self.decide(encoded(**{name: "PRIVATE_SELECTOR_VALUE"}))
                    self.assertEqual((decision.result, decision.http_status), ("INVALID", 400))
                    self.assertEqual((decision.reason_code, decision.rule_id),
                                     (reason, "request_code_selection"))
                    self.assertEqual(decision.message, name)
                    self.assertNotIn("PRIVATE_SELECTOR_VALUE", repr(decision))

    def test_nested_selectors_precede_unsupported_alias_and_message_errors(self) -> None:
        cases = (
            ({"mm_processor_kwargs": {"code_revision": "private"}},
             "mm_processor_kwargs.code_revision", "CODE_SELECTION_FIELD"),
            ({"messages": [{"role": "user", "content": "hello", "revision": "private"}]},
             "messages[0].revision", "CODE_SELECTION_FIELD"),
            ({"unknown": [[{}, {"nested": {"tokenizer_revision": "private"}}]]},
             "unknown[0][1].nested.tokenizer_revision", "CODE_SELECTION_FIELD"),
            ({"unknown": [{"processor_kwargs": {}}]},
             "unknown[0].processor_kwargs", "INPUT_PROCESSOR_FIELD"),
            ({"mm_processor_kwargs": [{"chat_template_kwargs": {}}]},
             "mm_processor_kwargs[0].chat_template_kwargs", "INPUT_PROCESSOR_FIELD"),
            ({"max_completion_tokens": 1, "code_revision": "private"},
             "code_revision", "CODE_SELECTION_FIELD"),
        )
        for fields, path, reason in cases:
            with self.subTest(path=path):
                decision = self.decide(encoded(**fields))
                self.assertEqual((decision.reason_code, decision.message), (reason, path))
                self.assertEqual(decision.rule_id, "request_code_selection")

    def test_completions_reject_selectors_at_every_depth(self) -> None:
        for fields, path in (
            ({"code_revision": "private"}, "code_revision"),
            ({"mm_processor_kwargs": {"code_revision": "private"}},
             "mm_processor_kwargs.code_revision"),
            ({"prompt": [{"revision": "private"}]}, "prompt[0].revision"),
        ):
            with self.subTest(path=path):
                document = {"model": "test-model", "prompt": "hello", **fields}
                decision = validate_request(json.dumps(document).encode(), "application/json",
                                            self.policy_state, request_kind="completions")
                self.assertEqual((decision.reason_code, decision.rule_id, decision.message),
                                 ("CODE_SELECTION_FIELD", "request_code_selection", path))

    def test_opening_default_policy_fields_cannot_bypass_selector_rule(self) -> None:
        base = json.loads((EXAMPLE_POLICY.parent / "policy-v1.2-default-sampling.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            for group in ("code_selection", "input_processors"):
                for name in UNSUPPORTED_ENTRY_GROUPS[group]:
                    with self.subTest(name=name):
                        base["extra_fields"] = {
                            name: {"mode": "validate", "type": "string", "max_length": 128}
                        }
                        path.write_text(json.dumps(base))
                        state = load_policy(path)
                        self.assertTrue(state.ok, state.error_message)
                        decision = validate_request(encoded(**{name: "private"}),
                                                    "application/json", state)
                        self.assertEqual((decision.result, decision.rule_id),
                                         ("INVALID", "request_code_selection"))

    def test_selector_paths_are_bounded_and_controls_are_removed(self) -> None:
        for ancestor in ("before\n\r\t\x00\x7f\x85\u202e\u2028after", "x" * 2000):
            decision = self.decide(encoded(**{ancestor: {"code_revision": "private"}}))
            self.assertEqual(decision.reason_code, "CODE_SELECTION_FIELD")
            self.assertLessEqual(len(decision.message), 256)
            self.assertTrue(decision.message.isprintable())
            self.assertTrue(decision.message.endswith(".code_revision"))
        nested = {"revision": "private"}
        for _ in range(300):
            nested = {"level": [nested]}
        decision = self.decide(encoded(unknown=nested))
        self.assertEqual(decision.reason_code, "CODE_SELECTION_FIELD")
        self.assertLessEqual(len(decision.message), 256)
        self.assertTrue(decision.message.endswith(".revision"))

    def test_selector_names_in_values_are_allowed_and_other_errors_are_unchanged(self) -> None:
        text = " ".join(name for group in ("code_selection", "input_processors")
                        for name in UNSUPPORTED_ENTRY_GROUPS[group])
        self.assertEqual(self.decide(encoded(messages=[{"role": "user", "content": text}])).result,
                         "ALLOWED")
        decision = self.decide(encoded(typo_field={"text": "code_revision"}))
        self.assertEqual((decision.reason_code, decision.rule_id), ("UNSUPPORTED_FIELD", None))
        self.assertEqual(self.decide(encoded(Code_revision="private")).reason_code,
                         "UNSUPPORTED_FIELD")
        self.assertEqual(self.decide(encoded(code_revision="\ud800")).reason_code,
                         "INVALID_UNICODE")
        self.assertEqual(self.decide(b'[{"code_revision":"private"}]').reason_code,
                         "INVALID_REQUEST")

    def test_zeroday_explicitly_opened_chat_values_keep_experimental_behavior(self) -> None:
        base = json.loads((EXAMPLE_POLICY.parent / "policy-v1.2-zeroday-lab.json").read_text())
        base["extra_fields"]["code_revision"] = {"mode": "passthrough"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(base))
            state = load_policy(path)
            self.assertTrue(state.ok, state.error_message)
            decision = validate_request(
                encoded(code_revision="lab", chat_template_kwargs={"revision": "lab"}),
                "application/json", state)
            self.assertEqual(decision.result, "ALLOWED")
            # Unopened fields and messages retain protection in the lab too.
            self.assertEqual(validate_request(encoded(processor="private"), "application/json",
                                             state).reason_code, "INPUT_PROCESSOR_FIELD")
            self.assertEqual(validate_request(encoded(messages=[{"code_revision": "private"}]),
                                             "application/json", state).reason_code,
                             "CODE_SELECTION_FIELD")
            decision = validate_request(
                b'{"model":"test-model","prompt":"hello","code_revision":"lab"}',
                "application/json", state, request_kind="completions")
            self.assertEqual(decision.reason_code, "CODE_SELECTION_FIELD")


if __name__ == "__main__":
    unittest.main()
