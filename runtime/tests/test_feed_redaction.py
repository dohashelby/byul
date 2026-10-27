"""The live feed helper must not persist Tetragon process arguments (plan §6.5)."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
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
        errors = [None]
        feed._pump(source, output, feed.redact_tetragon_line, dropped, errors)
        written = output.getvalue()
        self.assertNotIn(b"synthetic-do-not-store", written)
        self.assertNotIn(b"garbage", written)
        self.assertEqual(dropped[0], 1)
        self.assertIsNone(errors[0])
        self.assertEqual(len(written.splitlines()), len(FIXTURE.read_bytes().splitlines()))

    def test_pump_records_write_failure_class(self) -> None:
        class BrokenOutput(io.BytesIO):
            def write(self, value):
                raise OSError("disk full")

        errors = [None]
        feed._pump(
            io.BytesIO(b'{"time":"t"}\n'),
            BrokenOutput(),
            feed.redact_tetragon_line,
            [0],
            errors,
        )
        self.assertEqual(errors[0], "OSError")

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
        feed._pump(
            child.process.stdout,
            output,
            child.transform,
            child.dropped_lines,
            child.pump_error,
        )
        child.process.wait(timeout=10)
        self.assertNotIn(b"do-not-store", output.getvalue())
        self.assertIn(b"/bin/x", output.getvalue())


class ReaderGroupSharingTests(unittest.TestCase):
    """--reader-group lets one group (the Gateway Pod) read feed files only."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.addCleanup(setattr, feed, "_reader_gid", None)

    def mode(self, path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_files_are_owner_only_by_default(self) -> None:
        feed._reader_gid = None
        heartbeat = self.directory / "hubble.heartbeat.json"
        feed._write_json_atomic(heartbeat, {"state": "running"})
        gaps = self.directory / "hubble.gaps.jsonl"
        feed._append_gap(gaps, {"event": "gap_opened"})
        self.assertEqual(self.mode(heartbeat), 0o600)
        self.assertEqual(self.mode(gaps), 0o600)

    def test_reader_group_gets_read_only_access(self) -> None:
        gid = os.getgid()
        feed._reader_gid = gid
        heartbeat = self.directory / "hubble.heartbeat.json"
        feed._write_json_atomic(heartbeat, {"state": "running"})
        gaps = self.directory / "hubble.gaps.jsonl"
        feed._append_gap(gaps, {"event": "gap_opened"})
        feed._share(self.directory, directory=True)
        for path in (heartbeat, gaps):
            with self.subTest(path=path.name):
                self.assertEqual(self.mode(path), 0o640)
                self.assertEqual(path.stat().st_gid, gid)
        self.assertEqual(self.mode(self.directory), 0o750)
        # Rewriting the heartbeat keeps the sharing (atomic replace).
        feed._write_json_atomic(heartbeat, {"state": "reconnecting"})
        self.assertEqual(self.mode(heartbeat), 0o640)

    def test_group_not_held_fails_clearly_with_exit_2(self) -> None:
        held = {os.getegid(), *os.getgroups()}
        missing = next(gid for gid in range(60000, 65000) if gid not in held)
        with self.assertRaises(feed.ReaderGroupError):
            feed.check_reader_group(missing)
        feed.check_reader_group(os.getgid())  # a held group is accepted
        feed.check_reader_group(None)
        code = feed.main(
            ["--output-dir", str(self.directory / "out"), "--reader-group", str(missing)]
        )
        self.assertEqual(code, 2)
        self.assertFalse((self.directory / "out").exists())

    def test_cli_accepts_reader_group(self) -> None:
        args = feed.build_parser().parse_args(
            ["--output-dir", str(self.directory), "--reader-group", "10010", "--kube-context", "byul-aws-lab"]
        )
        self.assertEqual((args.reader_group, args.kube_context), (10010, "byul-aws-lab"))

    def test_raw_append_files_are_nofollow_and_mode_is_repaired(self) -> None:
        raw = self.directory / "tetragon.jsonl"
        raw.write_bytes(b"existing\n")
        os.chmod(raw, 0o666)
        with feed._open_append(raw) as output:
            output.write(b"next\n")
        self.assertEqual(self.mode(raw), 0o600)
        self.assertEqual(raw.read_bytes(), b"existing\nnext\n")

        target = self.directory / "target.log"
        target.write_bytes(b"")
        link = self.directory / "hubble.stderr.log"
        link.symlink_to(target)
        with self.assertRaisesRegex(OSError, "symbolic link"):
            feed._open_append(link)

    def test_raw_append_reader_group_mode_is_0640(self) -> None:
        feed._reader_gid = os.getgid()
        raw = self.directory / "hubble.jsonl"
        with feed._open_append(raw):
            pass
        self.assertEqual(self.mode(raw), 0o640)
        self.assertEqual(raw.stat().st_gid, os.getgid())


if __name__ == "__main__":
    unittest.main()
