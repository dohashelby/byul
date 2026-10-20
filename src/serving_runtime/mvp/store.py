"""Durable JSON Lines storage for MVP observations."""

import json
import os
from pathlib import Path
import threading
from typing import Any


class JsonlStore:
    """Append dictionaries as durable UTF-8 JSON Lines records."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

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
