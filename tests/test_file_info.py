"""Check byte-level fingerprints with temporary, harmless files."""

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serving_runtime.file_info import inspect_file


class FileInfoTests(unittest.TestCase):
    def test_known_bytes_and_source_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.bin"
            path.write_bytes(b"abc")
            info = inspect_file(path)
            self.assertEqual(info.size_bytes, 3)
            self.assertEqual(info.sha256, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
            self.assertEqual(path.read_bytes(), b"abc")

    def test_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty"
            path.touch()
            info = inspect_file(path)
            self.assertEqual(info.size_bytes, 0)
            self.assertEqual(info.sha256, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    def test_binary_file_larger_than_read_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.bin"
            content = bytes(range(256)) * 9000
            path.write_bytes(content)
            info = inspect_file(path)
            self.assertEqual(info.size_bytes, len(content))
            self.assertEqual(info.sha256, hashlib.sha256(content).hexdigest())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFO support")
    def test_fifo_is_rejected_without_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pipe"
            os.mkfifo(path)
            with self.assertRaises(ValueError):
                inspect_file(path)

    def test_change_during_read_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changing.bin"
            path.write_bytes(b"abc")
            real_read = os.read
            changed = False

            def read_and_change(descriptor: int, count: int) -> bytes:
                nonlocal changed
                chunk = real_read(descriptor, count)
                if not changed:
                    with path.open("ab") as output:
                        output.write(b"d")
                    changed = True
                return chunk

            with patch("serving_runtime.file_info.os.read", side_effect=read_and_change):
                with self.assertRaises(OSError):
                    inspect_file(path)
