from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
import threading
import unittest

from serving_runtime.enforcement.events import ObservationQuery
from serving_runtime.mvp.gpu_state import read_gpu_state
from serving_runtime.mvp.http_handler import HandlerDependencies, create_handler
from tools import gpu_status

L4 = "0, NVIDIA L4, 37, 18120, 23034, 54, 71.52\n"


def runner(stdout="", returncode=0, error=None):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if error:
            raise error
        return subprocess.CompletedProcess(command, returncode, stdout, "secret stderr text")
    run.calls = calls
    return run


class ParseTests(unittest.TestCase):
    def test_parses_l4_row(self):
        self.assertEqual(gpu_status.parse(L4), [{
            "index": 0, "name": "NVIDIA L4", "utilization_percent": 37.0, "memory_used_mib": 18120.0,
            "memory_total_mib": 23034.0, "temperature_c": 54.0, "power_w": 71.52}])

    def test_not_supported_fields_are_null(self):
        row = gpu_status.parse("1, NVIDIA L4, [N/A], 0, 23034, 40, [Not Supported]\n")[0]
        self.assertIsNone(row["utilization_percent"])
        self.assertIsNone(row["power_w"])

    def test_malformed_rows_reject_whole_output(self):
        for output in ("", "0, NVIDIA L4, 37\n", "x, NVIDIA L4, 1, 1, 2, 3, 4\n",
                       "0, NVIDIA L4, 101, 1, 2, 3, 4\n", "0, NVIDIA L4, 1, 5, 2, 3, 4\n",
                       "0, NVIDIA L4, -1, 1, 2, 3, 4\n", "0, NVIDIA L4, nan, 1, 2, 3, 4\n",
                       L4 * 17, "x" * (gpu_status.MAX_OUTPUT + 1)):
            with self.subTest(output=output[:40]), self.assertRaises(ValueError):
                gpu_status.parse(output)


class CollectTests(unittest.TestCase):
    def test_query_uses_fixed_fields_and_timeout(self):
        run = runner(L4)
        result = gpu_status.collect(run, "/usr/bin/nvidia-smi")
        self.assertEqual(result["state"], "ok")
        command, kwargs = run.calls[0]
        self.assertEqual(command[1], "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw")
        self.assertEqual(kwargs["timeout"], gpu_status.TIMEOUT_S)
        self.assertNotIn("uuid", json.dumps(result))

    def test_errors_become_reason_codes_without_output(self):
        cases = {"NVIDIA_SMI_FAILED": runner("x", 9), "TIMEOUT": runner(error=subprocess.TimeoutExpired("n", 8)),
                 "EXEC_FAILED": runner(error=PermissionError("denied /dev/nvidia0")), "PARSE_ERROR": runner("junk\n")}
        for reason, run in cases.items():
            with self.subTest(reason=reason):
                result = gpu_status.collect(run, "/usr/bin/nvidia-smi")
                self.assertEqual((result["state"], result["reason"], result["gpus"]), ("error", reason, []))
                self.assertNotIn("secret", json.dumps(result))
                self.assertNotIn("denied", json.dumps(result))

    def test_missing_binary(self):
        original = gpu_status.shutil.which
        gpu_status.shutil.which = lambda _name: None
        try:
            self.assertEqual(gpu_status.collect(runner(L4))["reason"], "NVIDIA_SMI_MISSING")
        finally:
            gpu_status.shutil.which = original

    def test_write_is_atomic_0640(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "gpu-status" / "gpu-state.json"
            gpu_status.write(path, gpu_status.collect(runner(L4), "/x"))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            self.assertFalse(path.with_name(path.name + ".next").exists())
            self.assertEqual(read_gpu_state(path)["state"], "ok")


class ReadStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "gpu-state.json"

    def snapshot(self, **changes):
        value = gpu_status.collect(runner(L4), "/x")
        value.update(changes)
        self.path.write_text(json.dumps(value))
        return value

    def test_not_configured_missing_ok(self):
        self.assertEqual(read_gpu_state(None)["state"], "not_configured")
        self.assertEqual(read_gpu_state(self.path)["state"], "missing")
        self.snapshot()
        result = read_gpu_state(self.path)
        self.assertEqual((result["state"], result["fresh"], result["gpus"][0]["name"]), ("ok", True, "NVIDIA L4"))

    def test_stopped_collector_is_stale_not_zero(self):
        self.snapshot(collected_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat())
        result = read_gpu_state(self.path)
        self.assertEqual((result["state"], result["fresh"]), ("stale", False))
        self.assertEqual(result["gpus"][0]["utilization_percent"], 37.0)

    def test_collector_error_is_preserved(self):
        self.snapshot(state="error", reason="TIMEOUT", gpus=[])
        self.assertEqual((read_gpu_state(self.path)["state"], read_gpu_state(self.path)["reason"]), ("error", "TIMEOUT"))

    def test_nonfinite_snapshot_is_rejected_before_json_response(self):
        for number in (float("nan"), float("inf"), -float("inf")):
            row = gpu_status.parse(L4)[0]
            row["power_w"] = number
            self.snapshot(gpus=[row])
            result = read_gpu_state(self.path)
            self.assertEqual((result["state"], result["reason"], result["gpus"]), ("error", "INVALID_SNAPSHOT", []))

    def test_invalid_snapshots_are_errors(self):
        for change in ({"schema_version": "other"}, {"gpus": [{"index": 0, "name": "x", "power_w": -1}]},
                       {"gpus": [{"index": True, "name": "x"}]}, {"state": "error", "reason": "free text"},
                       {"collected_at": "2026-11-09T00:00:00"}):
            with self.subTest(change=change):
                self.snapshot(**change)
                result = read_gpu_state(self.path)
                self.assertEqual((result["state"], result["reason"], result["gpus"]), ("error", "INVALID_SNAPSHOT", []))
        self.path.write_text("{")
        self.assertEqual(read_gpu_state(self.path)["state"], "error")


class RouteTests(unittest.TestCase):
    def test_gateway_serves_gpu_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            dependencies = HandlerDependencies(
                chat=None, completions=None, status=lambda: {}, policy_status=lambda: {},
                baseline_status=lambda: {}, collectors_status=lambda: {}, revalidation_status=lambda: {},
                queries=None, observations=ObservationQuery(Path(temporary) / "o.jsonl"),
                receipt_lookup=lambda _r: {}, observation_session_id="s", lab_report=lambda: {},
                record_transport_error=lambda *_a: None, request_header_timeout_s=0.5, request_body_timeout_s=0.5,
                gpu_status=lambda: {"state": "ok", "gpus": [{"index": 0}]})
            client, server = socket.socketpair()
            self.addCleanup(client.close)
            thread = threading.Thread(target=lambda: (create_handler(dependencies)(server, ("local", 0), object()), server.close()))
            thread.start()
            try:
                client.sendall(b"GET /api/gpu-status HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            except PermissionError as error:
                client.close()
                thread.join(timeout=1)
                self.skipTest(f"socket I/O is unavailable: {error}")
            client.settimeout(2)
            data = b""
            while chunk := client.recv(4096):
                data += chunk
            thread.join(timeout=2)
            head, _, body = data.partition(b"\r\n\r\n")
            self.assertIn(b"200", head.split(b"\r\n")[0])
            self.assertEqual(json.loads(body)["state"], "ok")


if __name__ == "__main__":
    unittest.main()
