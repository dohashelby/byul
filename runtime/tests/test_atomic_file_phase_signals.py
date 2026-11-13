"""Preserve the phase timeline writer's observable filesystem contract."""

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from serving_runtime.enforcement import phase_signals as module


class PhaseAtomicContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "state.json"
        self.path.write_text("old")

    def test_random_name_mode_payload_and_sync_order(self):
        events = []
        original_replace, original_fsync = os.replace, os.fsync

        def synced(fd):
            events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
            return original_fsync(fd)

        def replaced(source, target):
            source = Path(source)
            self.assertTrue(source.name.startswith(".state.json."))
            self.assertTrue(source.name.endswith(".tmp"))
            self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o600)
            self.assertEqual(json.loads(source.read_text()), {"value": "검증"})
            self.assertEqual(self.path.read_text(), "old")
            events.append("replace")
            return original_replace(source, target)

        with (
            patch.object(module.os, "replace", side_effect=replaced),
            patch.object(module.os, "fsync", side_effect=synced),
        ):
            module._atomic_write(self.path, {"value": "검증"})
        self.assertEqual(events, ["file", "replace", "directory"])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_replace_error_preserves_destination_and_removes_temporary(self):
        with patch.object(module.os, "replace", side_effect=OSError("replace blocked")):
            with self.assertRaisesRegex(OSError, "replace blocked"):
                module._atomic_write(self.path, {"new": True})
        self.assertEqual(self.path.read_text(), "old")
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_destination_symlink_is_replaced_without_following_it(self):
        victim = self.root / "victim"
        victim.write_text("keep")
        self.path.unlink()
        self.path.symlink_to(victim)
        module._atomic_write(self.path, {"new": True})
        self.assertFalse(self.path.is_symlink())
        self.assertEqual(victim.read_text(), "keep")
        self.assertEqual(json.loads(self.path.read_text()), {"new": True})
