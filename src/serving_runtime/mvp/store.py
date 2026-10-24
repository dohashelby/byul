"""Durable JSON Lines storage for MVP observations."""

import json
import os
from pathlib import Path
import threading
from typing import Any, Collection
from uuid import uuid4


_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    # Several modules open their own JsonlStore on the same file. One lock per
    # path keeps a retention rewrite from racing their appends in this process.
    key = os.path.abspath(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = _PATH_LOCKS[key] = threading.Lock()
        return lock


class JsonlStore:
    """Append dictionaries as durable UTF-8 JSON Lines records."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = _lock_for(self.path)

    def append(self, record: dict[str, Any]) -> None:
        encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with self.path.open("ab") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())

    def read_all(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        records: list[dict[str, Any]] = []
        corrupt: list[dict[str, Any]] = []
        with self._lock:
            try:
                with self.path.open("rb") as source:
                    for line_number, raw_line in enumerate(source, start=1):
                        try:
                            document = json.loads(raw_line.decode("utf-8"))
                            if not isinstance(document, dict):
                                raise ValueError("record is not a JSON object")
                        except (
                            UnicodeDecodeError,
                            json.JSONDecodeError,
                            ValueError,
                            RecursionError,
                        ) as error:
                            corrupt.append({"line": line_number, "reason": str(error)})
                            continue
                        records.append(document)
            except FileNotFoundError:
                return [], []
        return records, corrupt

    def remove_lines(self, line_numbers: Collection[int]) -> tuple[int, int]:
        """Rewrite the file without the given 1-based lines; return (kept, removed).

        Lines appended after the caller chose ``line_numbers`` are kept, because
        existing line numbers do not move when new lines are appended.
        """

        drop = set(line_numbers)
        with self._lock:
            if not drop:
                return -1, 0
            temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.{uuid4().hex}.tmp")
            kept = removed = 0
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            try:
                with os.fdopen(descriptor, "wb") as output, self.path.open("rb") as source:
                    descriptor = -1
                    for line_number, raw_line in enumerate(source, start=1):
                        if line_number in drop:
                            removed += 1
                            continue
                        output.write(raw_line)
                        kept += 1
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
            return kept, removed
