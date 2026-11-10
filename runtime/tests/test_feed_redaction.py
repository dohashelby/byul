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
from unittest.mock import patch
from datetime import datetime

from tools import k8s_observation_feed as feed

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = ROOT / "tests" / "fixtures" / "enforcement" / "tetragon_events.synthetic.jsonl"


class RedactTetragonLineTests(unittest.TestCase):
    def test_only_single_synthetic_probe_token_is_preserved(self):
        tag = "byul-read-probe-" + "a" * 32
        for argv in (tag + "-secret", "prefix" + tag, tag + " " + tag, "'" + tag,
                     "ordinary-private-command", None):
            raw = {"process_kprobe": {"policy_name": "runtime-tg-secret-01", "runtime_probe_id": tag,
                                      "process": {"arguments": argv}}}
            clean = json.loads(feed.redact_tetragon_line(json.dumps(raw).encode()))
            self.assertNotIn("runtime_probe_id", clean["process_kprobe"])
        raw = {"process_kprobe": {"policy_name": "unrelated", "process": {"arguments": tag}}}
        self.assertNotIn("runtime_probe_id", json.loads(feed.redact_tetragon_line(json.dumps(raw).encode()))["process_kprobe"])

    def test_environment_variables_are_removed_from_every_actor(self) -> None:
        private={"arguments":"example-private-args","environment_variables":[{"key":"EXAMPLE","value":"example-private-env"}]}
        raw={"process_exec":{"process":{"binary":"/bin/a",**private},"parent":dict(private),"ancestors":[dict(private)]}}
        cleaned=feed.redact_tetragon_line(json.dumps(raw).encode())
        self.assertNotIn(b"example-private",cleaned)
        self.assertIn(b"/bin/a",cleaned)

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


class RedactionDropReceiptTests(unittest.TestCase):
    def child_group(self):
        child = feed._Child("tetragon", lambda _since: [], io.BytesIO(), io.BytesIO(),
                            transform=feed.redact_tetragon_line)
        group = feed._FeedGroup("tetragon", feed_path=Path("unused"),
                                heartbeat_path=Path("unused"), gap_path=Path("unused"),
                                children=[child])
        return child, group

    def pump(self, child, raw):
        feed._pump(io.BytesIO(raw), child.output, child.transform,
                   child.dropped_lines, child.pump_error, child.redaction_drops)

    def test_drop_codes_and_actual_pump_time_without_input(self):
        samples = [(b"\xff\n", "INVALID_UTF8"),
                   (b"EXAMPLE_PRIVATE_NOT_JSON\n", "INVALID_JSON"),
                   (b"[1,2]\n", "OBJECT_REQUIRED")]
        for raw, reason in samples:
            with self.subTest(reason=reason):
                child, group = self.child_group()
                stamp = "2026-11-08T07:00:00.123456+00:00"
                with patch.object(feed, "_utc_now", return_value=stamp):
                    self.pump(child, raw)
                extra = group.heartbeat_extra()
                self.assertEqual(extra["dropped_lines_total"], 1)
                self.assertEqual(extra["last_redaction_drop_reason"], reason)
                self.assertEqual(extra["last_redaction_drop_at"], stamp)
                self.assertIsNotNone(datetime.fromisoformat(stamp).tzinfo)
                self.assertIsNone(extra["last_redaction_error_class"])
                self.assertEqual(child.output.getvalue(), b"")
                self.assertNotIn("EXAMPLE_PRIVATE", json.dumps(extra))

    def test_decoder_recursion_failure_uses_fixed_code(self):
        child, group = self.child_group()
        with patch.object(feed.json, "loads", side_effect=RecursionError("EXAMPLE_PRIVATE")):
            self.pump(child, b"EXAMPLE_DEEP_LINE\n")
        extra = group.heartbeat_extra()
        self.assertEqual(extra["last_redaction_drop_reason"], "JSON_TOO_DEEP")
        self.assertEqual(extra["dropped_lines_total"], 1)
        self.assertNotIn("EXAMPLE", json.dumps(extra))

    def test_recovery_reconnect_and_idle_keep_original_receipt(self):
        child, group = self.child_group()
        stamp = "2026-11-08T07:00:00+00:00"
        with patch.object(feed, "_utc_now", return_value=stamp):
            self.pump(child, b"EXAMPLE_BAD\n")
        original = group.heartbeat_extra()
        # Same child/state is reused when the follow subprocess reconnects.
        group.state = "reconnecting"
        group.reconnect_count += 1
        self.pump(child, b'{"process_exec":{"process":{"arguments":"EXAMPLE_SECRET"}}}\n')
        group.state = "running"
        for _ in range(3):
            current = group.heartbeat_extra()
            for field in ("dropped_lines_total", "last_redaction_drop_reason", "last_redaction_drop_at"):
                self.assertEqual(current[field], original[field])
        self.assertNotIn(b"EXAMPLE_SECRET", child.output.getvalue())
        with patch.object(feed, "_utc_now", return_value="2026-11-08T07:01:00+00:00"):
            self.pump(child, b"[]\n")
        self.assertEqual(group.heartbeat_extra()["dropped_lines_total"], 2)
        self.assertEqual(group.heartbeat_extra()["last_redaction_drop_reason"], "OBJECT_REQUIRED")
        self.assertEqual(group.heartbeat_extra()["last_redaction_drop_at"], "2026-11-08T07:01:00+00:00")

    def test_unrecognized_reason_is_bounded_without_raw_value(self):
        child, group = self.child_group()
        child.redaction_drops.record("EXAMPLE_PRIVATE_REASON")
        extra = group.heartbeat_extra()
        self.assertEqual(extra["last_redaction_drop_reason"], "FILTER_REJECTED")
        self.assertNotIn("EXAMPLE_PRIVATE", json.dumps(extra))

    def test_new_writer_resets_count_and_receipt_without_fabricated_time(self):
        child, first = self.child_group()
        self.pump(child, b"EXAMPLE_BAD\n")
        _, second = self.child_group()
        self.assertEqual(first.heartbeat_extra()["dropped_lines_total"], 1)
        self.assertEqual(second.heartbeat_extra()["dropped_lines_total"], 0)
        self.assertIsNone(second.heartbeat_extra()["last_redaction_drop_reason"])
        self.assertIsNone(second.heartbeat_extra()["last_redaction_drop_at"])

    def test_heartbeat_write_preserves_event_time_separate_from_emission(self):
        child, group = self.child_group()
        with patch.object(feed, "_utc_now", return_value="2026-11-08T07:00:00+00:00"):
            self.pump(child, b"EXAMPLE_BAD\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tetragon.heartbeat.json"
            with patch.object(feed, "_utc_now", return_value="2026-11-08T07:05:00+00:00"):
                feed.write_heartbeats({"tetragon": (path, Path(directory)/"raw.jsonl", group.started_at, [])},
                                      state="running", extra={"tetragon": group.heartbeat_extra()})
            value = json.loads(path.read_text())
        self.assertEqual(value["last_redaction_drop_at"], "2026-11-08T07:00:00+00:00")
        self.assertEqual(value["updated_at"], "2026-11-08T07:05:00+00:00")
        self.assertEqual(value["last_redaction_drop_reason"], "INVALID_JSON")
        self.assertEqual(value["dropped_lines_total"], 1)
        self.assertNotIn("EXAMPLE_BAD", json.dumps(value))

    def test_pump_failure_does_not_invent_filter_drop(self):
        child, group = self.child_group()
        class BrokenOutput(io.BytesIO):
            def write(self, value):
                raise OSError("EXAMPLE_PRIVATE_ERROR")
        child.output = BrokenOutput()
        self.pump(child, b"{}\n")
        self.assertEqual(child.pump_error[0], "OSError")
        extra = group.heartbeat_extra()
        self.assertEqual(extra["dropped_lines_total"], 0)
        self.assertIsNone(extra["last_redaction_drop_reason"])
        self.assertIsNone(extra["last_redaction_drop_at"])
        self.assertNotIn("EXAMPLE_PRIVATE", json.dumps(extra))


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

    def test_reader_group_preserves_directory_setgid_and_child_group(self) -> None:
        gid = os.getgid()
        feed._reader_gid = gid
        for mode in (0o2750, 0o2777):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.directory, mode)
                feed._share(self.directory, directory=True)
                self.assertEqual(self.mode(self.directory), 0o2750)
                self.assertEqual(self.directory.stat().st_gid, gid)
                child = self.directory / f"identity-{mode}.json"
                child.touch(mode=0o640)
                self.assertEqual(child.stat().st_gid, gid)
                self.assertEqual(self.mode(child) & 0o022, 0)

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
