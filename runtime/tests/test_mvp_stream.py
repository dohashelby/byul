"""S1 streaming relay, timeout, framing, and concurrency tests."""

from contextlib import closing
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.relay import sse_done
from serving_runtime.mvp.store import JsonlStore


class _StreamDeadlineClock:
    """실제 준비/저장 지연과 무관하게 같은60ms 요청 기한을 통과시킨다."""
    def __init__(self):
        self.value = time.monotonic()
        self.elapsed = 0.0
        self.timers = []
        self.lock = threading.RLock()

    def monotonic(self):
        with self.lock:
            return self.value

    def advance(self, seconds):
        with self.lock:
            self.value += seconds
            self.elapsed += seconds
            due = [timer for timer in self.timers if not timer.cancelled and timer.deadline <= self.value]
            for timer in due:
                timer.cancelled = True
        for timer in due:
            timer.callback()

    def timer(self, interval, callback):
        clock = self

        class Timer:
            def __init__(self):
                self.deadline = clock.monotonic() + interval
                self.callback = callback
                self.cancelled = False

            def start(self):
                with clock.lock:
                    clock.timers.append(self)

            def cancel(self):
                self.cancelled = True

        return Timer()


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "examples/mvp/policy.json"


def body(*, stream: bool = True, max_tokens: int = 64, model: str = "test-model") -> bytes:
    return json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "stream test"}],
            "max_tokens": max_tokens,
            "stream": stream,
        }
    ).encode("utf-8")


class SSEParserUnitTests(unittest.TestCase):
    def test_done_marker_is_found_across_arbitrary_reads(self) -> None:
        pending = b""
        found = False
        for chunk in (b"data: {\"x\":1}\n", b"\ndata: [DO", b"NE]\r", b"\n\r\n"):
            pending, current = sse_done(pending, chunk)
            found = found or current
        self.assertTrue(found)

    def test_partial_marker_is_not_treated_as_done(self) -> None:
        pending, found = sse_done(b"", b"data: [DONE]")
        self.assertFalse(found)
        self.assertEqual(pending, b"data: [DONE]")


class StreamingHttpTests(unittest.TestCase):
    def start_pair(
        self,
        *,
        engine_options: dict[str, object] | None = None,
        config_options: dict[str, object] | None = None,
    ) -> tuple[MockEngine, GatewayApp, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        data_dir = Path(temporary.name)
        try:
            engine = MockEngine(
                port=0,
                receipts_path=data_dir / "engine_receipts.jsonl",
                **(engine_options or {}),
            ).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(engine.stop)
        options: dict[str, object] = {
            "gateway_port": 0,
            "engine_port": 0,
            "policy_path": str(POLICY),
            "data_dir": str(data_dir),
            "relay_timeout_s": 0.5,
            "stream_idle_timeout_s": 1.0,
            "request_deadline_s": 3.0,
            "max_concurrent_requests": 2,
        }
        options.update(config_options or {})
        gateway = GatewayApp(MVPConfig(**options), load_policy(POLICY), engine.address).start()
        self.addCleanup(gateway.stop)
        return engine, gateway, data_dir

    @staticmethod
    def final(data_dir: Path, request_id: str) -> dict[str, object] | None:
        deadline = time.monotonic() + 2
        wake = threading.Event()
        while time.monotonic() < deadline:
            records, _ = JsonlStore(data_dir / "gateway_records.jsonl").read_all()
            match = next(
                (
                    record
                    for record in reversed(records)
                    if record.get("request_id") == request_id and record.get("phase") == "final"
                ),
                None,
            )
            if match is not None:
                return match
            wake.wait(0.02)
        return None

    def test_split_utf8_stream_is_forwarded_byte_for_byte(self) -> None:
        engine, gateway, data_dir = self.start_pair(
            engine_options={"split_writes": True, "stream_interval_s": 0.03}
        )
        connection = http.client.HTTPConnection(*gateway.address, timeout=2)
        self.addCleanup(connection.close)
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=body(),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        request_id = response.getheader("X-Request-ID")
        payload = response.read()

        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        self.assertEqual(response.getheader("Connection"), "close")
        self.assertIsNone(response.getheader("Content-Length"))
        self.assertIsNone(response.getheader("Transfer-Encoding"))
        self.assertIn("한글✓".encode("utf-8"), payload)
        self.assertTrue(payload.endswith(b"data: [DONE]\n\n"))
        self.assertEqual(payload, engine.stream_transmissions[request_id])
        receipt = next(item for item in engine.receipts if item["request_id"] == request_id)
        self.assertEqual(receipt["stream_end"], "completed")
        self.assertEqual(receipt["evidence_kind"], "mock")

        final = self.final(data_dir, request_id)
        self.assertIsNotNone(final)
        self.assertEqual(final["final_outcome"], "completed")
        self.assertEqual(final["decision"]["result"], "ALLOWED")
        self.assertTrue(final["response_started"])
        records, _ = JsonlStore(data_dir / "gateway_records.jsonl").read_all()
        phases = [record["phase"] for record in records if record["request_id"] == request_id]
        self.assertEqual(phases, ["started", "response_started", "final"])

    def test_stream_policy_denials_do_not_reach_engine(self) -> None:
        engine, gateway, _ = self.start_pair()
        base = f"http://{gateway.address[0]}:{gateway.address[1]}"
        for request_body, expected in (
            (body(model="other-model"), "MODEL_NOT_ALLOWED"),
            (body(max_tokens=129), "MAX_TOKENS_EXCEEDED"),
        ):
            request = Request(
                base + "/v1/chat/completions",
                data=request_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.subTest(expected=expected), self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=2)
            with closing(caught.exception):
                self.assertEqual(caught.exception.code, 403)
                self.assertEqual(json.loads(caught.exception.read())["error"]["code"], expected)
        self.assertEqual(engine.receipts, [])

    def test_header_timeout_idle_timeout_and_truncation_are_distinct(self) -> None:
        def stream_request(gateway: GatewayApp) -> Request:
            return Request(
                f"http://{gateway.address[0]}:{gateway.address[1]}/v1/chat/completions",
                data=body(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

        engine, gateway, data_dir = self.start_pair(
            engine_options={"header_delay_s": 0.15},
            config_options={"relay_timeout_s": 0.03},
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(stream_request(gateway), timeout=2)
        with closing(caught.exception):
            self.assertEqual(caught.exception.code, 504)
            self.assertEqual(json.loads(caught.exception.read())["error"]["code"], "ENGINE_TIMEOUT")
        gateway.stop()
        engine.stop()

        engine, gateway, data_dir = self.start_pair(
            engine_options={"stall_after_events": 1, "stream_interval_s": 0},
            config_options={"stream_idle_timeout_s": 0.06},
        )
        with closing(urlopen(stream_request(gateway), timeout=2)) as response:
            request_id = response.headers["X-Request-ID"]
            payload = response.read()
        self.assertNotIn(b"[DONE]", payload)
        self.assertEqual(self.final(data_dir, request_id)["final_outcome"], "idle_timeout")
        engine.release_stalled_streams()
        gateway.stop()
        engine.stop()

        engine, gateway, data_dir = self.start_pair(
            engine_options={"truncate_after_events": 1, "stream_interval_s": 0}
        )
        with closing(urlopen(stream_request(gateway), timeout=2)) as response:
            request_id = response.headers["X-Request-ID"]
            response.read()
        self.assertEqual(self.final(data_dir, request_id)["final_outcome"], "backend_truncated")

    def test_nonblocking_concurrency_limit_has_retry_after(self) -> None:
        engine, gateway, data_dir = self.start_pair(
            engine_options={"stall_after_events": 1, "stream_interval_s": 0},
            config_options={"stream_idle_timeout_s": 2},
        )
        ready = [threading.Event(), threading.Event()]
        completed = [threading.Event(), threading.Event()]

        def consume(index: int) -> None:
            connection = http.client.HTTPConnection(*gateway.address, timeout=3)
            try:
                connection.request(
                    "POST",
                    "/v1/chat/completions",
                    body=body(),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                first = bytearray()
                while b"\n\n" not in first:
                    first.extend(response.read1(64))
                ready[index].set()
                response.read()
            finally:
                connection.close()
                completed[index].set()

        threads = [threading.Thread(target=consume, args=(index,), daemon=True) for index in range(2)]
        for thread in threads:
            thread.start()
        self.assertTrue(all(event.wait(2) for event in ready))
        before = len(engine.receipts)
        request = Request(
            f"http://{gateway.address[0]}:{gateway.address[1]}/v1/chat/completions",
            data=body(stream=False),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        with closing(caught.exception):
            self.assertEqual(caught.exception.code, 429)
            self.assertEqual(caught.exception.headers["Retry-After"], "1")
            request_id = caught.exception.headers["X-Request-ID"]
        self.assertEqual(len(engine.receipts), before)
        denied = self.final(data_dir, request_id)
        self.assertEqual(denied["decision"]["rule_id"], "max_concurrent_requests")

        engine.release_stalled_streams()
        self.assertTrue(all(event.wait(3) for event in completed))
        for thread in threads:
            thread.join(timeout=1)
        with closing(urlopen(f"http://{gateway.address[0]}:{gateway.address[1]}/api/status")) as response:
            status = json.loads(response.read())
        self.assertEqual(status["concurrency"], {"in_flight": 0, "limit": 2})

    def test_request_deadline_is_separate_from_idle_timeout(self) -> None:
        _, gateway, data_dir = self.start_pair(
            engine_options={"stream_events": 3, "stream_interval_s": 0.2},
            config_options={"stream_idle_timeout_s": 1.0, "request_deadline_s": 0.06},
        )
        request = Request(
            f"http://{gateway.address[0]}:{gateway.address[1]}/v1/chat/completions",
            data=body(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        from serving_runtime.mvp import relay, request_flow, response_timeout
        from serving_runtime.mvp.backend import BackendConfig
        from test_mvp_request_boundaries import BackendConnection, BackendResponse

        clock = _StreamDeadlineClock()

        class TimedResponse(BackendResponse):
            def read1(self, size):
                # 첫 stream chunk는59ms, 다음 읽기는61ms다. idle1초는 지나지 않는다.
                clock.advance(0.059 if self.reads == 0 else 0.002)
                return super().read1(size)

        upstream = BackendConnection(TimedResponse([
            b'data: {"id":"stream-test","object":"chat.completion.chunk","model":"test-model",'
            b'"choices":[{"index":0,"delta":{"content":"hello"},"finish_reason":null}]}\n\n',
            b'data: [DONE]\n\n',
        ], stream=True))
        controlled_time = SimpleNamespace(**{**vars(time), "monotonic": clock.monotonic})
        controlled_threads = SimpleNamespace(**{**vars(threading), "Timer": clock.timer})
        # 제품의 deadline 검사·watchdog callback은 그대로 실행한다. 시계와 유계 상류만 제어한다.
        with patch.object(relay, "time", controlled_time), patch.object(request_flow, "time", controlled_time), \
                patch.object(response_timeout, "time", controlled_time), \
                patch.object(response_timeout, "threading", controlled_threads), \
                patch.object(BackendConfig, "connection", return_value=(upstream, "/engine")):
            with closing(urlopen(request, timeout=2)) as response:
                request_id = response.headers["X-Request-ID"]
                response.read()
            final = self.final(data_dir, request_id)
        self.assertEqual(upstream.response.reads, 2)
        self.assertTrue(upstream.closed)
        self.assertLess(clock.elapsed, 1.0)
        self.assertEqual(final["final_outcome"], "deadline_exceeded")
        self.assertEqual(final["decision"]["reason_code"], "STREAM_DEADLINE")


if __name__ == "__main__":
    unittest.main()
