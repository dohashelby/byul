"""The live feed helper must not persist Tetragon process arguments (plan §6.5)."""

from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys
import unittest

from tools import k8s_observation_feed as feed

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = ROOT / "tests" / "fixtures" / "enforcement" / "tetragon_events.synthetic.jsonl"


class RedactTetragonLineTests(unittest.TestCase):
    def test_arguments_are_removed_and_other_fields_kept(self) -> None:
        for raw in FIXTURE.read_bytes().splitlines(keepends=True):
            cleaned = feed.redact_tetragon_line(raw)
            self.assertIsNotNone(cleaned)
            self.assertNotIn(b"synthetic-secret-argument", cleaned)
            self.assertNotIn(b"synthetic-do-not-store", cleaned)
            self.assertTrue(cleaned.endswith(b"\n"))
            original = json.loads(raw)
            document = json.loads(cleaned)
            self.assertEqual(document["time"], original["time"])
            event_name = next(key for key in original if key != "time")
            self.assertEqual(
                document[event_name]["process"]["binary"],
                original[event_name]["process"]["binary"],
            )
            self.assertNotIn("arguments", document[event_name]["process"])

    def test_parent_and_ancestor_arguments_are_removed(self) -> None:
        raw = json.dumps(
            {
                "time": "2026-10-26T00:00:00Z",
                "process_exec": {
                    "process": {"binary": "/bin/a", "arguments": "secret-1"},
                    "parent": {"binary": "/bin/b", "arguments": "secret-2"},
                    "ancestors": [{"binary": "/bin/c", "arguments": "secret-3"}],
                },
            }
        ).encode()
        cleaned = feed.redact_tetragon_line(raw + b"\n")
        for secret in (b"secret-1", b"secret-2", b"secret-3"):
            self.assertNotIn(secret, cleaned)

    def test_non_json_lines_are_dropped_not_stored(self) -> None:
        self.assertIsNone(feed.redact_tetragon_line(b"not json --password hunter2\n"))
        self.assertIsNone(feed.redact_tetragon_line(b"[1, 2]\n"))

    def test_pump_writes_only_redacted_lines_and_counts_drops(self) -> None:
        source = io.BytesIO(FIXTURE.read_bytes() + b"garbage line\n")
        output = io.BytesIO()
        dropped = [0]
        feed._pump(source, output, feed.redact_tetragon_line, dropped)
        written = output.getvalue()
        self.assertNotIn(b"synthetic-do-not-store", written)
        self.assertNotIn(b"garbage", written)
        self.assertEqual(dropped[0], 1)
        self.assertEqual(len(written.splitlines()), len(FIXTURE.read_bytes().splitlines()))

    def test_child_with_transform_reads_through_a_pipe(self) -> None:
        output = io.BytesIO()
        script = (
            "import sys;"
            "sys.stdout.write('{\"time\":\"t\",\"process_exec\":{\"process\":"
            "{\"binary\":\"/bin/x\",\"arguments\":\"do-not-store\"}}}\\n')"
        )
        child = feed._Child(
            "tetragon",
            lambda _since: [sys.executable, "-c", script],
            output,
            io.BytesIO(),
            transform=feed.redact_tetragon_line,
        )
        child.process = feed._start(child.argv(None), subprocess.PIPE, subprocess.DEVNULL)
        feed._pump(child.process.stdout, output, child.transform, child.dropped_lines)
        child.process.wait(timeout=10)
        self.assertNotIn(b"do-not-store", output.getvalue())
        self.assertIn(b"/bin/x", output.getvalue())


if __name__ == "__main__":
    unittest.main()
