"""Checkpoint and health publication keep their original distinct contracts."""

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from serving_runtime.observation import observation_feed as module


class ObservationAtomicContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.feed = module.ObservationFeed(
            kind="tetragon",
            path=self.root / "feed.jsonl",
            data_dir=self.root / "data",
            data_origin="synthetic",
            environment="test",
            run_id="run",
            health_receipt_mode=0o640,
        )
        self.feed.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.item = {"identity": {"node": "node-a"}, "observed_at": "2026-11-13T00:00:00+00:00"}

    def check_publication(self, health, mode):
        path = self.feed.state_path.parent / "tetragon-health-receipts.json" if health else self.feed.state_path
        path.write_text("old")
        self.feed.health_receipt_mode = mode
        events = []
        fdopen, fsync, fchmod, replace = os.fdopen, os.fsync, os.fchmod, os.replace

        class Stream:
            def __init__(self, raw):
                self.raw = raw

            def __enter__(self):
                self.raw.__enter__()
                return self

            def __exit__(self, *args):
                return self.raw.__exit__(*args)

            def write(self, data):
                events.append("write")
                return self.raw.write(data)

            def flush(self):
                events.append("flush")
                return self.raw.flush()

            def fileno(self):
                return self.raw.fileno()

        def synced(fd):
            self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
            events.append("sync")
            return fsync(fd)

        def chmod(fd, value):
            events.append("chmod")
            return fchmod(fd, value)

        def swapped(source, target):
            source = Path(source)
            self.assertTrue(source.name.startswith("." + path.name + "."))
            self.assertTrue(source.name.endswith(".tmp"))
            self.assertEqual(stat.S_IMODE(source.stat().st_mode), mode)
            self.assertEqual(path.read_text(), "old")
            document = json.loads(source.read_text())
            if health:
                self.assertEqual(document["nodes"]["node-a"], self.item)
            else:
                self.assertEqual(document["kind"], "tetragon")
            events.append("replace")
            return replace(source, target)

        with (
            patch.object(module.os, "fdopen", side_effect=lambda *a, **kw: Stream(fdopen(*a, **kw))),
            patch.object(module.os, "fsync", side_effect=synced),
            patch.object(module.os, "fchmod", side_effect=chmod),
            patch.object(module.os, "replace", side_effect=swapped),
        ):
            if health:
                self.feed._save_health_receipt(self.item)
            else:
                self.feed._write_checkpoint()
        self.assertEqual(
            events, ["write", "chmod", "flush", "sync", "replace"] if health else ["write", "flush", "sync", "replace"]
        )
        self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_checkpoint_mode_and_no_directory_sync(self):
        self.check_publication(False, 0o600)

    def test_health_chmod_occurs_after_write_and_before_flush(self):
        for mode in (0o600, 0o640):
            with self.subTest(mode=mode):
                self.check_publication(True, mode)

    def test_replace_failure_keeps_original_and_removes_temporary(self):
        for health in (False, True):
            with self.subTest(health=health):
                path = self.feed.state_path.parent / "tetragon-health-receipts.json" if health else self.feed.state_path
                path.write_text("old")
                with patch.object(module.os, "replace", side_effect=OSError("blocked")):
                    with self.assertRaisesRegex(OSError, "blocked"):
                        self.feed._save_health_receipt(self.item) if health else self.feed._write_checkpoint()
                self.assertEqual(path.read_text(), "old")
                self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_fdopen_failure_closes_unowned_descriptor(self):
        opened = []
        original_open = os.open

        def tracked(*args, **kwargs):
            fd = original_open(*args, **kwargs)
            opened.append(fd)
            return fd

        with (
            patch.object(module.os, "open", side_effect=tracked),
            patch.object(module.os, "fdopen", side_effect=OSError("fdopen blocked")),
        ):
            with self.assertRaisesRegex(OSError, "fdopen blocked"):
                self.feed._write_checkpoint()
        self.assertEqual(len(opened), 1)
        with self.assertRaises(OSError):
            os.fstat(opened[0])
        self.assertEqual(list(self.feed.state_path.parent.glob(".*.tmp")), [])
