from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from serving_runtime.mvp.engine_receipts import EngineReceipts
from serving_runtime.mvp.query import QueryService
from serving_runtime.mvp.relay import Relay, _ResponseIds, _StreamResponseId, response_id_receipt
from serving_runtime.mvp.validator import Decision
from serving_runtime.mvp.store import JsonlStore
from tools import engine_receipts as collector

RID = "0123456789abcdef0123456789abcdef"
OTHER = "f" * 32
NOW = datetime(2026, 11, 9, 3, 0, 0, tzinfo=timezone.utc)
PROMPT = "my secret prompt: the launch code is 7777"


class ResponseIdTests(unittest.TestCase):
    def test_chat_relay_records_id_preserving_sse_bytes_and_event_boundaries(self):
        chunks = [b'data: {"id":"chat', f'cmpl-{RID}","choices":[]}}\n\n'.encode(), b'data: [DONE]\n\n']
        upstream = Mock(status=200)
        upstream.getheader.return_value = "text/event-stream"
        upstream.read1.side_effect = chunks + [b""]
        connection = Mock(sock=None)
        connection.getresponse.return_value = upstream
        backend = Mock(kind="vllm")
        backend.connection.return_value = (connection, "/v1/chat/completions")
        relay = Relay(backend, relay_timeout_s=2, stream_idle_timeout_s=2, request_deadline_s=5)
        downstream = Mock()
        allowed = Decision("ALLOWED", 200, "REQUEST_ALLOWED", None, "allowed", "model", 10)
        with patch("serving_runtime.mvp.relay.DeadlineWatchdog") as watcher, \
                patch("serving_runtime.mvp.relay.relay_payload", return_value=b"{}"):
            watcher.return_value.expired.return_value = False
            watcher.return_value.finish.return_value = False
            result, outcome, started = relay.relay_stream(downstream, RID, {}, allowed, time.monotonic(), lambda: None)
        self.assertEqual((result, outcome, started), (allowed, "completed", True))
        writes = [c.args[0] for c in downstream.write_stream_chunk.call_args_list]
        self.assertEqual(b"".join(writes), b"".join(chunks))
        self.assertTrue(all(event.endswith(b"\n\n") for event in writes))
        self.assertEqual(len(writes), 2)
        self.assertEqual(relay.response_id_receipt(RID), "engine_response_id_matched")

    def test_chat_stream_id_across_chunks_and_first_event_only(self):
        stream = _StreamResponseId()
        ids = _ResponseIds()
        chunks = [b": keepalive\n\n", b'data: {"i', f'd":"chatcmpl-{RID}","choices":[]}}\n\n'.encode(),
                  b'data: {"id":"other"}\n\ndata: [DONE]\n\n']
        for chunk in chunks:
            found, value = stream.feed(chunk)
            if found:
                ids.note(RID, value)
        self.assertEqual(ids.pop(RID), "engine_response_id_matched")
        self.assertEqual(stream.pending, b"")

    def test_chat_stream_without_id_and_oversized_line_are_bounded(self):
        for chunk, expected in ((b'data: {"choices":[]}\n', (True, None)),
                                (b"data: " + b"x" * _StreamResponseId.LIMIT, (False, None))):
            stream = _StreamResponseId()
            self.assertEqual(stream.feed(chunk), expected)
            self.assertEqual(stream.pending, b"")

    def test_only_exact_engine_echo_forms_match(self):
        for value, expected in ((f"chatcmpl-{RID}", "matched"), (f"cmpl-{RID}", "matched"), (RID, "matched"),
                                (f"chatcmpl-{OTHER}", "mismatch"), (f"chatcmpl-{RID}-0", "mismatch"),
                                (f"fake-{RID}", "mismatch"), (None, "absent"), ("", "absent"), (7, "absent")):
            with self.subTest(value=value):
                self.assertEqual(response_id_receipt(RID, value), f"engine_response_id_{expected}")

    def test_first_stream_chunk_decides_and_is_read_once(self):
        ids = _ResponseIds(limit=2)
        ids.note(RID, f"chatcmpl-{RID}")
        ids.note(RID, "other")
        self.assertEqual(ids.pop(RID), "engine_response_id_matched")
        self.assertEqual(ids.pop(RID), "not_available")
        for index in range(3):
            ids.note(f"{index}" * 32, None)
        self.assertEqual(ids.pop("0" * 32), "not_available")  # bounded


class ExtractTests(unittest.TestCase):
    def test_keeps_ids_only_never_log_text(self):
        output = "\n".join([
            f"2026-11-09T03:00:01.123456789Z INFO Received request chatcmpl-{RID}: prompt: '{PROMPT}'",
            f"2026-11-09T03:00:02Z INFO Finished request chatcmpl-{RID}.",
            f"2026-11-09T03:00:03+00:00 [sglang] rid={OTHER} {PROMPT}",
            f"no timestamp chatcmpl-{'a' * 32}",
            f"2026-11-09T03:00:04Z chatcmpl-{'b' * 33}",
        ]).encode()
        found = collector.extract(output)
        self.assertEqual(found, {RID: "2026-11-09T03:00:01.123456Z", OTHER: "2026-11-09T03:00:03Z"})
        self.assertNotIn("secret", json.dumps(found))


class Run:
    def __init__(self, *results):
        self.results = list(results)
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        item = self.results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return subprocess.CompletedProcess(command, item[0], item[1], b"stderr with " + PROMPT.encode())


class CollectorTests(unittest.TestCase):
    def test_window_resets_after_failure_and_output_has_no_text(self):
        clock = iter([NOW, NOW + timedelta(seconds=10), NOW + timedelta(seconds=20), NOW + timedelta(seconds=30)])
        line = f"2026-11-09T02:59:59Z Received request chatcmpl-{RID}: prompt: {PROMPT}\n".encode()
        run = Run((0, line), (1, b""), subprocess.TimeoutExpired("kubectl", 20), (0, b""))
        c = collector.Collector("ctx", "serving", "app=serving-engine", Path("unused"), since_s=60,
                                runner=run, clock=lambda: next(clock))
        first = c.collect()
        self.assertEqual((first["state"], first["window_started_at"]), ("ok", "2026-11-09T02:59:00Z"))
        self.assertEqual(first["receipts"], [{"request_id": RID, "observed_at": "2026-11-09T02:59:59Z"}])
        self.assertEqual((c.collect()["reason"], c.window_started_at), ("KUBECTL_FAILED", None))
        self.assertEqual(c.collect()["reason"], "TIMEOUT")
        last = c.collect()
        self.assertEqual(last["window_started_at"], "2026-11-09T02:59:30Z")  # restarted after the gap
        self.assertEqual(len(last["receipts"]), 1)  # positive evidence is kept
        self.assertNotIn("secret", json.dumps(last))
        self.assertIn("--timestamps=true", run.commands[0])
        self.assertEqual(run.commands[0][run.commands[0].index("-l") + 1], "app=serving-engine")

    def test_rejects_unsafe_selector(self):
        with self.assertRaises(SystemExit):
            collector.main(["--context", "c", "--namespace", "serving", "--selector", "app=x;rm", "--output", "/tmp/x"])


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "engine-receipts.json"

    def publish(self, **changes):
        value = {"schema_version": "runtime-engine-receipts/1.0", "context": "ctx", "collected_at": "2026-11-09T03:00:00Z",
                 "state": "ok", "reason": None, "window_started_at": "2026-11-09T02:50:00Z",
                 "receipts": [{"request_id": RID, "observed_at": "2026-11-09T02:55:01Z"}]}
        value.update(changes)
        self.path.write_text(json.dumps(value))

    def test_statuses(self):
        self.assertEqual(EngineReceipts(None).lookup(RID, None)["status"], "not_configured")
        receipts = EngineReceipts(self.path)
        self.assertEqual(receipts.lookup(RID, "2026-11-09T02:55:00Z", now=NOW)["status"], "unavailable")  # no file
        self.publish()
        self.assertEqual(receipts.lookup(RID, "2026-11-09T02:55:00Z", now=NOW)["observed_at"], "2026-11-09T02:55:01Z")
        self.assertEqual(receipts.lookup(OTHER, "2026-11-09T02:55:00Z", now=NOW)["status"], "not_observed")
        # Before the window, too recent to have been collected, or stale: cannot say.
        self.assertEqual(receipts.lookup(OTHER, "2026-11-09T02:40:00Z", now=NOW)["status"], "unavailable")
        self.assertEqual(receipts.lookup(OTHER, "2026-11-09T02:59:58Z", now=NOW)["status"], "unavailable")
        self.assertEqual(receipts.lookup(OTHER, "2026-11-09T02:55:00Z", now=NOW + timedelta(minutes=5))["status"], "unavailable")

    def test_error_state_and_invalid_files_never_claim_absence(self):
        receipts = EngineReceipts(self.path)
        self.publish(state="error", reason="TIMEOUT")
        self.assertEqual(receipts.lookup(OTHER, "2026-11-09T02:55:00Z", now=NOW)["status"], "unavailable")
        self.assertEqual(receipts.lookup(RID, "2026-11-09T02:55:00Z", now=NOW)["status"], "observed")
        for change in ({"schema_version": "x"}, {"receipts": [{"request_id": "not-an-id", "observed_at": "2026-11-09T02:55:01Z"}]}):
            self.publish(**change)
            self.assertEqual(EngineReceipts(self.path).lookup(RID, "2026-11-09T02:55:00Z", now=NOW)["status"], "unavailable")


def final(at="2026-11-09T02:55:00+00:00", receipt="engine_response_id_matched"):
    return {"phase": "final", "request_id": RID, "observation_session_id": "s", "recorded_at": at,
            "backend_kind": "vllm", "decision": {"result": "ALLOWED", "reason_code": "OK", "http_status": 200},
            "request": {}, "delivery": {"attempted": True, "backend_receipt": receipt, "response": "completed"}}


class QueryDetailTests(unittest.TestCase):
    def service(self, lookup):
        directory = Path(self.temporary.name)
        service = QueryService(JsonlStore(directory / "g.jsonl"), JsonlStore(directory / "e.jsonl"),
                               lambda: {"ok": True}, engine_log_lookup=lookup)
        service._gateway_store.append(final())
        return service

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def test_engine_log_observed_is_received(self):
        detail = self.service(lambda _r, _a: {"status": "observed", "observed_at": "2026-11-09T02:55:01Z",
                                              "collected_at": "2026-11-09T03:00:00Z"}).detail(RID, lambda _r: {}, "s")
        self.assertEqual(detail["evidence_state"], "received")
        self.assertEqual(detail["engine_receipts"][0]["received_at"], "2026-11-09T02:55:01Z")
        self.assertEqual(detail["engine_live"]["status"], "engine_log_observed")

    def test_not_observed_is_never_not_received(self):
        detail = self.service(lambda _r, _a: {"status": "not_observed", "observed_at": None,
                                              "collected_at": "2026-11-09T03:00:00Z"}).detail(RID, lambda _r: {}, "s")
        self.assertEqual(detail["evidence_state"], "gateway_only")
        self.assertIn("미수신 단정 불가", detail["evidence_summary"])
        self.assertEqual(detail["engine_receipts"], [])

    def test_without_logs_response_id_match_is_described(self):
        detail = self.service(lambda _r, _a: {"status": "not_configured"}).detail(RID, lambda _r: {}, "s")
        self.assertEqual(detail["evidence_state"], "gateway_only")
        self.assertIn("엔진 응답 ID", detail["evidence_summary"])
        self.assertEqual(detail["engine_live"]["status"], "not_available")


if __name__ == "__main__":
    unittest.main()
