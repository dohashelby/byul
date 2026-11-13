"""Keep the management heartbeat writer's text and error boundaries."""

import json
import os
from pathlib import Path
import re
import stat
import tempfile
import unittest
from unittest.mock import patch

from serving_runtime.gateway import management_listener as module


class ManagementAtomicContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "heartbeat.json"
        self.recorder = module.AccessRecorder(self.root / "입력.jsonl", heartbeat_path=self.path)
        self.path.write_text("old")

    def test_name_mode_text_payload_and_file_only_sync(self):
        events = []
        replace, fsync = os.replace, os.fsync

        def synced(fd):
            self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
            events.append("file")
            return fsync(fd)

        def swapped(source, target):
            source = Path(source)
            self.assertIsNotNone(re.fullmatch(r"\.heartbeat\.json\.[0-9a-f]{32}\.tmp", source.name))
            self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o600)
            raw = source.read_text()
            self.assertTrue(raw.endswith("\n"))
            document = json.loads(raw)
            self.assertEqual(document["state"], "running")
            self.assertEqual(document["feed_path"], str((self.root / "입력.jsonl").absolute()))
            self.assertEqual(self.path.read_text(), "old")
            events.append("replace")
            return replace(source, target)

        with (
            patch.object(module.os, "replace", side_effect=swapped),
            patch.object(module.os, "fsync", side_effect=synced),
        ):
            self.recorder._write_heartbeat("running")
        self.assertEqual(events, ["file", "replace"])
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_replace_error_is_swallowed_and_temporary_is_removed(self):
        with patch.object(module.os, "replace", side_effect=OSError("blocked")):
            self.assertIsNone(self.recorder._write_heartbeat("running"))
        self.assertEqual(self.path.read_text(), "old")
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_cleanup_error_is_outside_the_swallowed_write_error(self):
        unlink = Path.unlink
        cleanups = []

        def blocked(path, *args, **kwargs):
            if path.suffix == ".tmp":
                cleanups.append(path)
                raise PermissionError("cleanup blocked")
            return unlink(path, *args, **kwargs)

        with (
            patch.object(module.os, "replace", side_effect=OSError("write blocked")),
            patch.object(Path, "unlink", new=blocked),
        ):
            with self.assertRaisesRegex(PermissionError, "cleanup blocked"):
                self.recorder._write_heartbeat("running")
        self.assertEqual(len(cleanups), 1)

    def test_fdopen_failure_keeps_the_legacy_descriptor_ownership(self):
        opened = []
        original = os.open

        def tracked(*args, **kwargs):
            descriptor = original(*args, **kwargs)
            opened.append(descriptor)
            return descriptor

        with (
            patch.object(module.os, "open", side_effect=tracked),
            patch.object(module.os, "fdopen", side_effect=OSError("fdopen blocked")),
        ):
            self.recorder._write_heartbeat("running")
        self.assertEqual(len(opened), 1)
        try:
            self.assertTrue(stat.S_ISREG(os.fstat(opened[0]).st_mode))
        finally:
            os.close(opened[0])
        self.assertEqual(list(self.root.glob(".*.tmp")), [])
