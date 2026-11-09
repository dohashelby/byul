"""Request/response boundary regressions without an external serving engine."""

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from serving_runtime.mvp.backend import BackendConfig
from serving_runtime.mvp.engine_client import EngineClient
from serving_runtime.mvp.policy import ExtraFieldRule, load_policy
from serving_runtime.mvp.policy_state import load_revocations
from serving_runtime.mvp.relay import Relay, engine_rejection
from serving_runtime.mvp.request_flow import ConcurrencyLimiter, RequestFlow
from serving_runtime.mvp.request_records import RequestRecords
from serving_runtime.mvp.validator import validate_request


POLICY = Path(__file__).resolve().parents[1] / "examples/mvp/policy.json"


def body(**fields):
    document = {"model": "test-model", "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 8}
    document.update(fields)
    return json.dumps(document).encode("utf-8")


class BackendResponse:
    status = 200
    fp = None

    def __init__(self, chunks, *, stream=False):
        self.chunks = iter(chunks)
        self.stream = stream
        self.reads = 0

    def getheader(self, name, default=None):
        return "text/event-stream" if self.stream and name == "Content-Type" else default

    def getheaders(self):
        return [("Content-Type", "application/json")]

    def read1(self, _size):
        self.reads += 1
        return next(self.chunks, b"")


class BackendConnection:
    sock = None

    def __init__(self, response):
        self.response = response
        self.closed = False

    def request(self, *_args, **_kwargs):
        pass

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class RequestIO:
    def __init__(self, request_body):
        self.body = request_body
        self.sent = None
        self.chunks = []

    def request_header(self, name):
        return {"Content-Length": str(len(self.body)), "Content-Type": "application/json"}.get(name)

    def read_request_body(self, length):
        return self.body[:length]

    def send_error_decision(self, decision, _request_id, **_kwargs):
        self.sent = (decision.http_status, decision.reason_code)

    def send_response_body(self, status, _content_type, _body, **_kwargs):
        self.sent = (status, None)

    def start_stream_response(self, _request_id):
        self.sent = (200, None)

    def write_stream_chunk(self, chunk):
        self.chunks.append(chunk)


class RequestUnicodeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(POLICY)

    def test_all_request_strings_and_keys_must_be_utf8_encodable(self):
        for document in (
            {"model": "test-model", "messages": [{"role": "user", "content": "\ud800"}]},
            {"model": "\udfff", "messages": [{"role": "user", "content": "ok"}]},
            {"model": "test-model", "messages": [{"role": "user", "content": "ok"}], "\ud800": 1},
        ):
            with self.subTest(document=repr(document)):
                decision = validate_request(json.dumps(document).encode(), "application/json", self.policy)
                self.assertEqual((decision.http_status, decision.reason_code), (400, "INVALID_UNICODE"))
                decision.message.encode("utf-8")

    def test_duplicate_invalid_unicode_key_has_a_safe_error_message(self):
        raw = b'{"model":"test-model","messages":[],"\\ud800":1,"\\ud800":2}'
        decision = validate_request(raw, "application/json", self.policy)
        self.assertEqual((decision.http_status, decision.reason_code), (400, "INVALID_UNICODE"))
        decision.message.encode("utf-8")

    def test_normal_unicode_including_an_escaped_surrogate_pair_is_allowed(self):
        document = {"model": "test-model", "messages": [{"role": "user", "content": "한글😀"}]}
        for raw in (json.dumps(document).encode(), json.dumps(document, ensure_ascii=False).encode()):
            with self.subTest(raw=raw):
                self.assertEqual(validate_request(raw, "application/json", self.policy).result, "ALLOWED")

    def test_nested_extra_fields_are_checked_before_utf8_byte_constraints(self):
        policy = replace(self.policy, policy=replace(
            self.policy.policy, profile="zeroday-lab",
            extra_fields=(ExtraFieldRule("response_format", "validate", {"type": "object", "max_bytes": 128}),)))
        raw = body(response_format={"nested": ["\ud800"]})
        decision = validate_request(raw, "application/json", policy)
        self.assertEqual((decision.http_status, decision.reason_code), (400, "INVALID_UNICODE"))

    def test_rejected_unicode_is_final_not_delivered_and_does_not_poison_next_request(self):
        backend = BackendConfig.mock(("127.0.0.1", 1))
        allowed = validate_request(body(), "application/json", self.policy)
        relay = SimpleNamespace(request_deadline_s=1, relay=Mock(return_value=(allowed, b"{}")))
        engine = SimpleNamespace(receipt_not_queried=lambda: "not_queried",
                                 backend_receipt=lambda _id: "not_queried")
        with tempfile.TemporaryDirectory() as directory:
            records = RequestRecords(directory, "session", lambda: self.policy, "mock")
            limiter = ConcurrencyLimiter(1)
            flow = RequestFlow(backend, lambda: self.policy, lambda: load_revocations(None),
                               records, engine, relay, limiter)
            invalid = RequestIO(body(messages=[{"role": "user", "content": "\ud800"}]))
            flow.chat(invalid, "a" * 32)
            self.assertEqual(invalid.sent, (400, "INVALID_UNICODE"))
            relay.relay.assert_not_called()
            stored, corrupt = records.store.read_all()
            self.assertFalse(corrupt)
            self.assertEqual(stored[-1]["phase"], "final")
            self.assertFalse(stored[-1]["delivery"]["attempted"])
            normal = RequestIO(body())
            flow.chat(normal, "b" * 32)
            self.assertEqual(normal.sent, (200, None))
            self.assertEqual(limiter.status()["in_flight"], 0)


class UpstreamErrorPrivacyTests(unittest.TestCase):
    def test_rejection_never_echoes_upstream_detail_but_preserves_classification(self):
        allowed = validate_request(body(), "application/json", load_policy(POLICY))
        marker = "PRIVATE /workspace/x s3://bucket/key FAKE_CREDENTIAL_123"
        for status in (400, 404, 413, 422, 429):
            for payload in ({"error": {"message": marker}}, {"detail": marker}):
                with self.subTest(status=status, payload=payload):
                    decision = engine_rejection(status, json.dumps(payload).encode(), allowed,
                                                truncated=False, retry_after="003")
                    self.assertNotIn(marker, decision.message)
                    self.assertEqual(decision.http_status, status)
                    self.assertEqual(decision.engine_http_status, status)
                    self.assertEqual(decision.result, "ENGINE_REJECTED")
                    self.assertEqual(decision.retry_after, "3" if status == 429 else None)


class AuxiliaryResponseBoundTests(unittest.TestCase):
    def request(self, chunks, *, metrics=False):
        response = BackendResponse(chunks)
        connection = BackendConnection(response)
        backend = BackendConfig.mock(("127.0.0.1", 1))
        with patch.object(BackendConfig, "connection", return_value=(connection, "/tokenize")), \
             patch.object(BackendConfig, "metrics_connection", return_value=(connection, "/metrics")):
            try:
                return EngineClient(backend, 1).request("GET", "/tokenize", metrics=metrics)
            finally:
                self.assertTrue(connection.closed)

    def test_auxiliary_response_boundary_and_chunked_overflow(self):
        with patch("serving_runtime.mvp.engine_client._AUX_RESPONSE_BODY_LIMIT", 8, create=True):
            self.assertEqual(self.request([b"1234", b"5678"])[2], b"12345678")
            with self.assertRaisesRegex(OSError, "byte limit"):
                self.request([b"1234", b"5678", b"9", b"must-not-be-read"])

    def test_metrics_has_a_separate_larger_bound(self):
        with patch("serving_runtime.mvp.engine_client._AUX_RESPONSE_BODY_LIMIT", 4, create=True), \
             patch("serving_runtime.mvp.engine_client._METRICS_RESPONSE_BODY_LIMIT", 8, create=True):
            self.assertEqual(self.request([b"12345678"], metrics=True)[2], b"12345678")
            with self.assertRaisesRegex(OSError, "byte limit"):
                self.request([b"12345678", b"9"], metrics=True)

    def test_tokenize_overflow_holds_generation_and_records_a_body_free_error(self):
        policy = load_policy(POLICY)
        policy = replace(policy, policy=replace(policy.policy, max_context_tokens=256))
        backend = BackendConfig.mock(("127.0.0.1", 1))
        connection = BackendConnection(BackendResponse([b"12345678", b"PRIVATE"]))
        relay = SimpleNamespace(request_deadline_s=1, relay=Mock())
        limiter = ConcurrencyLimiter(1)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(BackendConfig, "connection", return_value=(connection, "/tokenize")), \
             patch("serving_runtime.mvp.engine_client._AUX_RESPONSE_BODY_LIMIT", 8):
            records = RequestRecords(directory, "session", lambda: policy, "mock")
            flow = RequestFlow(backend, lambda: policy, lambda: load_revocations(None),
                               records, EngineClient(backend, 1), relay, limiter)
            response = RequestIO(body())
            flow.chat(response, "a" * 32)
            self.assertEqual(response.sent, (503, "CONTEXT_CHECK_UNAVAILABLE"))
            relay.relay.assert_not_called()
            stored, corrupt = records.store.read_all()
            self.assertFalse(corrupt)
            self.assertEqual(stored[-1]["request"]["context"]["error"], "ResponseTooLargeError")
            self.assertFalse(stored[-1]["delivery"]["attempted"])
            self.assertNotIn("PRIVATE", json.dumps(stored))
            self.assertEqual(limiter.status()["in_flight"], 0)
        self.assertTrue(connection.closed)


class ChatStreamBoundTests(unittest.TestCase):
    def relay(self, chunks):
        backend = BackendConfig.mock(("127.0.0.1", 1))
        connection = BackendConnection(BackendResponse(chunks, stream=True))
        client = RequestIO(body(stream=True))
        allowed = validate_request(client.body, "application/json", load_policy(POLICY))
        with patch.object(BackendConfig, "connection", return_value=(connection, "/v1/chat/completions")):
            result = Relay(backend, 1, 1, 1).relay_stream(
                client, "a" * 32, json.loads(client.body), allowed, time.monotonic(), lambda: None)
        self.assertTrue(connection.closed)
        return result, client

    def test_unterminated_line_is_rejected_before_forwarding_the_overflow_chunk(self):
        with patch("serving_runtime.mvp.relay._CHAT_SSE_LINE_LIMIT", 8, create=True):
            (decision, outcome, started), client = self.relay([b"1234", b"5678", b"9"])
        self.assertEqual((decision.reason_code, outcome, started),
                         ("ENGINE_BAD_RESPONSE", "oversized_chat_stream", True))
        self.assertEqual(b"".join(client.chunks), b"")

    def test_event_size_is_bounded_even_when_individual_lines_are_small(self):
        with patch("serving_runtime.mvp.relay._CHAT_SSE_LINE_LIMIT", 8, create=True), \
             patch("serving_runtime.mvp.relay._CHAT_SSE_EVENT_LIMIT", 12, create=True):
            (decision, outcome, _), client = self.relay([b": aa\n", b": bb\n", b"xyz"])
        self.assertEqual((decision.reason_code, outcome), ("ENGINE_BAD_RESPONSE", "oversized_chat_stream"))
        self.assertEqual(b"".join(client.chunks), b"")

    def test_blank_lines_reset_event_budget_and_normal_chunks_remain_unchanged(self):
        chunks = [b": aa\r\n", b"\r\n", b": bb\n\n", b"data: [DO", b"NE]\n\n"]
        with patch("serving_runtime.mvp.relay._CHAT_SSE_LINE_LIMIT", 16, create=True), \
             patch("serving_runtime.mvp.relay._CHAT_SSE_EVENT_LIMIT", 16, create=True):
            (decision, outcome, started), client = self.relay(chunks)
        self.assertEqual((decision.result, outcome, started), ("ALLOWED", "completed", True))
        self.assertEqual(b"".join(client.chunks), b"".join(chunks))

    def test_oversized_stream_is_recorded_and_releases_the_gateway_slot(self):
        policy = load_policy(POLICY)
        backend = BackendConfig.mock(("127.0.0.1", 1))
        connection = BackendConnection(BackendResponse([b"12345678", b"9"], stream=True))
        engine = SimpleNamespace(receipt_not_queried=lambda: "not_queried",
                                 backend_receipt=lambda _id: "not_queried",
                                 mock_cancel_evidence=lambda _id, **_kwargs: "not_observed")
        limiter = ConcurrencyLimiter(1)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(BackendConfig, "connection", return_value=(connection, "/v1/chat/completions")), \
             patch("serving_runtime.mvp.relay._CHAT_SSE_LINE_LIMIT", 8):
            records = RequestRecords(directory, "session", lambda: policy, "mock")
            flow = RequestFlow(backend, lambda: policy, lambda: load_revocations(None),
                               records, engine, Relay(backend, 1, 1, 1), limiter)
            flow.chat(RequestIO(body(stream=True)), "a" * 32)
            stored, corrupt = records.store.read_all()
            self.assertFalse(corrupt)
            self.assertEqual(stored[-1]["phase"], "final")
            self.assertEqual(stored[-1]["decision"]["reason_code"], "ENGINE_BAD_RESPONSE")
            self.assertEqual(stored[-1]["final_outcome"], "oversized_chat_stream")
            self.assertEqual(limiter.status()["in_flight"], 0)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
