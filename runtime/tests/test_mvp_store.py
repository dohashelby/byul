"""Tests for durable JSON Lines MVP storage."""

import json
from pathlib import Path
import tempfile
import unittest

from serving_runtime.mvp.store import JsonlStore


class JsonlStoreTests(unittest.TestCase):
    def test_append_and_read_creates_private_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "records.jsonl"
            store = JsonlStore(path)
            expected = [{"number": 1}, {"text": "한글"}]
            for record in expected:
                store.append(record)

            records, corrupt = store.read_all()

            self.assertEqual(records, expected)
            self.assertEqual(corrupt, [])
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(
                [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()],
                expected,
            )

    def test_corrupt_lines_are_reported_and_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            path.write_bytes(
                b'{"ok": 1}\n'
                b'{not-json}\n'
                b'[1, 2]\n'
                b'\xff\n'
                b'{"ok": 2}\n'
            )

            records, corrupt = JsonlStore(path).read_all()

            self.assertEqual(records, [{"ok": 1}, {"ok": 2}])
            self.assertEqual([item["line"] for item in corrupt], [2, 3, 4])
            self.assertTrue(all(item["reason"] for item in corrupt))

    def test_missing_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            records, corrupt = JsonlStore(Path(directory) / "missing.jsonl").read_all()
        self.assertEqual((records, corrupt), ([], []))


if __name__ == "__main__":
    unittest.main()
