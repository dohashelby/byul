"""SP3 engine-log phase signal extraction and ExecPolicy connection."""

from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from serving_runtime.cli import main
from serving_runtime.enforcement.execpolicy import ExecPolicy, parse_timeline
from serving_runtime.enforcement.intent import parse_intent
from serving_runtime.enforcement.phase_signals import (
    ENGINE_PATTERN_SETS,
    extract_phase_timeline,
    follow_phase_timeline,
    parse_timezone,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/enforcement/vllm_cpu_029_startup_excerpt.log"
INTENT = ROOT / "examples/enforcement/intent-v1.1.example.json"


class _HealthyHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()

    def log_message(self, _format: str, *args: object) -> None:
        pass


class _Response:
    status = 200

    def close(self) -> None:
        pass


def _wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition was not reached before timeout")


class PhaseSignalExtractionTests(unittest.TestCase):
    def test_observed_cpu_excerpt_uses_only_certain_boundaries(self) -> None:
        timeline = extract_phase_timeline(
            FIXTURE, engine="vllm", year=2026, timezone_offset="+00:00"
        )
        self.assertEqual(
            timeline["entries"],
            [
                {
                    "phase": "loading",
                    "from": "2026-10-23T05:47:12+00:00",
                    "until": "2026-10-23T05:47:24+00:00",
                    "basis": "engine_signal",
                },
                {
                    "phase": "compiling",
                    "from": "2026-10-23T05:47:24+00:00",
                    "until": "2026-10-23T05:47:58+00:00",
                    "basis": "engine_signal",
                },
            ],
        )
        extraction = timeline["extraction"]
        self.assertEqual(extraction["engine"], "vllm")
        self.assertEqual(extraction["runs_observed"], 2)
        self.assertEqual(extraction["lines_read"], 12)
        self.assertEqual(extraction["last_position"]["line"], 12)
        self.assertGreater(extraction["last_position"]["byte_offset"], 0)
        self.assertIn(
            {"line": 8, "signal": "inferring_start", "reason": "timestamp_missing"},
            extraction["omitted_boundaries"],
        )
        self.assertIn(
            {"line": 12, "signal": "compiling", "reason": "compile_markers_not_observed"},
            extraction["omitted_boundaries"],
        )
        self.assertNotIn("inferring", [item["phase"] for item in timeline["entries"]])
        self.assertEqual(len(parse_timeline(timeline)), 2)

    def test_two_timestamped_restarts_close_each_inferring_interval(self) -> None:
        log = b"\n".join(
            (
                b"INFO 10-23 01:00:00 Loading model from scratch...",
                b"INFO 10-23 01:00:05 Model loading took 1 GiB memory and 5 seconds",
                b"INFO 10-23 01:00:06 Application startup complete.",
                b"INFO 10-23 02:00:00 Loading model from scratch...",
                b"INFO 10-23 02:00:05 Model loading took 1 GiB memory and 5 seconds",
                b"INFO 10-23 02:00:06 Application startup complete.",
            )
        ) + b"\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "restart.log"
            path.write_bytes(log)
            timeline = extract_phase_timeline(
                path, engine="vllm", year=2026, timezone_offset="+09:00"
            )
        inferring = [item for item in timeline["entries"] if item["phase"] == "inferring"]
        self.assertEqual(len(inferring), 2)
        self.assertEqual(timeline["extraction"]["runs_observed"], 2)
        self.assertEqual(inferring[0]["until"], "2026-10-23T02:00:00+09:00")
        self.assertIsNone(inferring[1]["until"])
        self.assertTrue(all(item["basis"] == "engine_signal" for item in inferring))

    def test_sglang_and_invalid_time_inputs_fail_without_guessing(self) -> None:
        self.assertEqual(ENGINE_PATTERN_SETS["sglang"].patterns, ())
        with self.assertRaisesRegex(ValueError, "no source-backed patterns"):
            extract_phase_timeline(
                FIXTURE, engine="sglang", year=2026, timezone_offset="+00:00"
            )
        for value in ("UTC", "local", "+24:00", "+00:60"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_timezone(value)

    def test_cli_output_is_consumed_by_exec_policy_as_engine_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "timeline.json"
            code = main(
                [
                    "enforce", "phase-signals", "--engine", "vllm",
                    "--log", str(FIXTURE), "--year", "2026", "--tz", "+00:00",
                    "--out", str(output),
                ]
            )
            self.assertEqual(code, 0)
            document = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], "runtime-phase-timeline/1.0")

            intent_document = json.loads(INTENT.read_text(encoding="utf-8"))
            # The engine kind affects the cache generation path, not phase parsing.
            intent_document["assets"]["cache_generation"]["engine"] = "sglang"
            intent_document["protected_paths"]["cache_dir"] = (
                "/var/cache/serving/sglang/image-example/revision-example/default"
            )
            policy = ExecPolicy(parse_intent(intent_document), timeline_path=output)
            result = policy.classify_exec(
                binary="/usr/bin/python3",
                parent=None,
                image=None,
                observed_at="2026-10-23T05:47:30+00:00",
            )
            self.assertEqual(result["phase"], ["compiling"])
            self.assertEqual(result["phase_basis"], ["engine_signal"])
            self.assertEqual(result["exec_verdict"], "allowed_in_phase")

    def test_follow_stdin_health_clock_order_and_no_request_content(self) -> None:
        log = io.BytesIO(
            b"INFO 10-23 01:00:00 Loading model from scratch...\n"
            b"INFO 10-23 01:00:05 Model loading took 1 GiB memory and 5 seconds\n"
            b"INFO 10-23 01:00:06 init engine (profile, create kv cache, warmup model) took 6 seconds\n"
            b"INFO 10-23 01:30:00 request_logger prompt=Loading model from scratch... "
            b"SamplingParams(secret-token)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "timeline.json"
            timeline = follow_phase_timeline(
                "-",
                output,
                engine="vllm",
                year=2026,
                timezone_offset="+00:00",
                existing="overwrite",
                health_url="http://127.0.0.1:9/health?not-persisted",
                health_interval=0.01,
                stdin=log,
                clock=lambda: datetime(2026, 10, 23, 2, 0, tzinfo=timezone.utc),
                opener=lambda _request, timeout: _Response(),
            )
            self.assertEqual(timeline["extraction"]["follow"]["state"], "input_ended")
            inferring = [item for item in timeline["entries"] if item["phase"] == "inferring"]
            self.assertEqual(inferring[0]["from"], "2026-10-23T02:00:00+00:00")
            self.assertIsNone(inferring[0]["until"])
            self.assertEqual(timeline["extraction"]["health_probe"]["last_result"], "healthy_boundary_recorded")
            self.assertEqual(
                timeline["extraction"]["clock_sources"]["health_boundaries"],
                "gateway_side_collector_clock_utc",
            )
            payload = output.read_text(encoding="utf-8")
            for secret in ("DO-NOT-STORE", "request_logger", "prompt=", "SamplingParams", "not-persisted"):
                self.assertNotIn(secret, payload)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(len(parse_timeline(timeline)), 2)
            policy = ExecPolicy(
                parse_intent(json.loads(INTENT.read_text(encoding="utf-8"))),
                timeline_path=output,
            )
            result = policy.classify_exec(
                binary="/usr/bin/python3",
                parent=None,
                image=None,
                observed_at="2026-10-23T02:00:01+00:00",
            )
            self.assertEqual(result["phase"], ["inferring"])
            self.assertEqual(result["exec_verdict"], "allowed_in_phase")
            with self.assertRaisesRegex(ValueError, "choose --overwrite or --resume"):
                follow_phase_timeline(
                    "-", output, engine="vllm", year=2026, timezone_offset="+00:00",
                    stdin=io.BytesIO(),
                )
            with self.assertRaisesRegex(ValueError, "must match"):
                follow_phase_timeline(
                    "-", output, engine="vllm", year=2025, timezone_offset="+00:00",
                    existing="resume", stdin=io.BytesIO(),
                )
            follow_phase_timeline(
                "-", output, engine="vllm", year=2026, timezone_offset="+00:00",
                existing="resume",
                stdin=io.BytesIO(
                    b"INFO 10-23 03:00:00 Loading model from scratch...\n"
                ),
                clock=lambda: datetime(2026, 10, 23, 3, 1, tzinfo=timezone.utc),
            )
            reloaded = policy.classify_exec(
                binary="/usr/bin/python3",
                parent=None,
                image=None,
                observed_at="2026-10-23T03:00:01+00:00",
            )
            self.assertEqual(reloaded["phase"], ["loading"])
            self.assertEqual(reloaded["exec_verdict"], "allowed_in_phase")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "timeline.json"
            timeline = follow_phase_timeline(
                "-",
                output,
                engine="vllm",
                year=2026,
                timezone_offset="+09:00",
                existing="overwrite",
                health_url="http://127.0.0.1:9/health",
                stdin=io.BytesIO(
                    b"INFO 10-23 12:00:00 Loading model from scratch...\n"
                    b"INFO 10-23 12:00:10 init engine (profile, create kv cache, warmup model) took 10 seconds\n"
                ),
                clock=lambda: datetime(2026, 10, 23, 2, 0, tzinfo=timezone.utc),
                opener=lambda _request, timeout: _Response(),
            )
            self.assertNotIn("inferring", [item["phase"] for item in timeline["entries"]])
            self.assertIn(
                {
                    "line": 2,
                    "signal": "inferring_start",
                    "reason": "health_and_log_clock_order_invalid",
                },
                timeline["extraction"]["omitted_boundaries"],
            )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "timeline.json"

            def timeout(_request, timeout):
                raise TimeoutError

            timeline = follow_phase_timeline(
                "-",
                output,
                engine="vllm",
                year=2026,
                timezone_offset="+00:00",
                existing="overwrite",
                health_url="http://127.0.0.1:9/health",
                stdin=io.BytesIO(
                    b"INFO 10-23 01:00:00 Loading model from scratch...\n"
                    b"INFO 10-23 01:00:10 init engine (profile, create kv cache, warmup model) took 10 seconds\n"
                ),
                clock=lambda: datetime(2026, 10, 23, 2, 0, tzinfo=timezone.utc),
                opener=timeout,
            )
            self.assertNotIn("inferring", [item["phase"] for item in timeline["entries"]])
            self.assertEqual(timeline["extraction"]["health_probe"]["last_result"], "TimeoutError")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "timeline.json"
            timeline = follow_phase_timeline(
                "-", output, engine="vllm", year=2026, timezone_offset="+00:00",
                existing="overwrite", stdin=io.BytesIO(b"\xff\n"),
                clock=lambda: datetime(2026, 10, 23, 2, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(timeline["extraction"]["follow"]["state"], "read_error")
            self.assertEqual(
                timeline["extraction"]["follow"]["events"],
                [
                    {"kind": "read_error", "at": "2026-10-23T02:00:00+00:00", "error": "UnicodeError"},
                    {"kind": "input_ended", "at": "2026-10-23T02:00:00+00:00"},
                ],
            )

    def test_cli_follow_reads_binary_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "timeline.json"
            fake_stdin = mock.Mock()
            fake_stdin.buffer = io.BytesIO(
                b"INFO 10-23 01:00:00 Loading model from scratch...\n"
            )
            with mock.patch("sys.stdin", fake_stdin):
                code = main(
                    [
                        "enforce", "phase-signals", "--engine", "vllm", "--log", "-",
                        "--year", "2026", "--tz", "+00:00", "--out", str(output),
                        "--follow", "--overwrite",
                    ]
                )
            self.assertEqual(code, 0)
            timeline = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(timeline["extraction"]["follow"]["state"], "input_ended")
            self.assertIsNone(timeline["entries"][0]["until"])

    def test_follow_file_detects_replacement_and_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "engine.log"
            output = Path(directory) / "timeline.json"
            log.write_bytes(b"INFO 10-23 01:00:00 Loading model from scratch...\n")
            stop = threading.Event()
            errors: list[BaseException] = []

            def run() -> None:
                try:
                    follow_phase_timeline(
                        log,
                        output,
                        engine="vllm",
                        year=2026,
                        timezone_offset="+00:00",
                        existing="overwrite",
                        stop_event=stop,
                        poll_interval=0.01,
                    )
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=run)
            thread.start()

            def document() -> dict:
                try:
                    return json.loads(output.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError):
                    return {}

            _wait_for(lambda: document().get("extraction", {}).get("lines_read") == 1)
            with log.open("ab") as stream:
                stream.write(b"INFO 10-23 01:00:05 Model loading took 1 GiB memory and 5 seconds\n")
            _wait_for(lambda: document().get("extraction", {}).get("lines_read") == 2)

            rotated = log.with_suffix(".old")
            os.replace(log, rotated)
            log.write_bytes(b"INFO 10-23 02:00:00 Loading model from scratch...\n")
            _wait_for(
                lambda: "file_replaced"
                in [item["kind"] for item in document().get("extraction", {}).get("follow", {}).get("events", [])]
                and document().get("extraction", {}).get("lines_read", 0) >= 3
            )
            with log.open("ab") as stream:
                stream.write(b"unparsed padding " + b"x" * 300 + b"\n")
            _wait_for(lambda: document().get("extraction", {}).get("lines_read", 0) >= 4)
            log.write_bytes(b"INFO 10-23 03:00:00 Loading model from scratch...\n")
            _wait_for(
                lambda: "file_truncated"
                in [item["kind"] for item in document().get("extraction", {}).get("follow", {}).get("events", [])]
                and document().get("extraction", {}).get("lines_read", 0) >= 5
            )
            stop.set()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            timeline = document()
            loading = [item for item in timeline["entries"] if item["phase"] == "loading"]
            self.assertEqual(loading[0]["until"], "2026-10-23T01:00:05+00:00")
            self.assertEqual(loading[1]["until"], "2026-10-23T03:00:00+00:00")
            self.assertIsNone(loading[2]["until"])

    def test_health_follow_reload_exec_policy_and_restart(self) -> None:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthyHandler)
        except OSError as error:
            self.skipTest(f"local sockets unavailable: {error}")
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "timeline.json"
                first = io.BytesIO(
                    b"INFO 10-23 01:00:00 Loading model from scratch...\n"
                    b"INFO 10-23 01:00:05 Model loading took 1 GiB memory and 5 seconds\n"
                    b"INFO 10-23 01:00:06 init engine (profile, create kv cache, warmup model) took 6 seconds\n"
                )
                follow_phase_timeline(
                    "-", output, engine="vllm", year=2026, timezone_offset="+00:00",
                    existing="overwrite", stdin=first,
                    clock=lambda: datetime(2026, 10, 23, 1, 30, tzinfo=timezone.utc),
                )
                intent = parse_intent(json.loads(INTENT.read_text(encoding="utf-8")))
                policy = ExecPolicy(intent, timeline_path=output)
                before = policy.classify_exec(
                    binary="/usr/bin/python3", parent=None, image=None,
                    observed_at="2026-10-23T01:30:01+00:00",
                )
                self.assertEqual(before["exec_verdict"], "phase_unknown")

                follow_phase_timeline(
                    "-", output, engine="vllm", year=2026, timezone_offset="+00:00",
                    existing="resume", stdin=io.BytesIO(b"request prompt=NEVER-SAVE\n"),
                    health_url=f"http://127.0.0.1:{server.server_port}/health",
                    health_interval=0.01,
                    clock=lambda: datetime(2026, 10, 23, 2, 0, tzinfo=timezone.utc),
                )
                after = policy.classify_exec(
                    binary="/usr/bin/python3", parent=None, image=None,
                    observed_at="2026-10-23T02:00:01+00:00",
                )
                self.assertEqual(after["phase"], ["inferring"])
                self.assertEqual(after["exec_verdict"], "allowed_in_phase")
                self.assertNotIn("NEVER-SAVE", output.read_text(encoding="utf-8"))

                timeline = follow_phase_timeline(
                    "-", output, engine="vllm", year=2026, timezone_offset="+00:00",
                    existing="resume",
                    stdin=io.BytesIO(b"INFO 10-23 03:00:00 Loading model from scratch...\n"),
                    clock=lambda: datetime(2026, 10, 23, 3, 1, tzinfo=timezone.utc),
                )
                inferring = [item for item in timeline["entries"] if item["phase"] == "inferring"]
                self.assertEqual(inferring[0]["until"], "2026-10-23T03:00:00+00:00")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
