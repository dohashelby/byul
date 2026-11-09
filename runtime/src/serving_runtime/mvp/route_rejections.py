"""Bounded, separate route audit over the existing retention-managed JSONL."""
from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

from .mock_engine import _utc_now
from .store import JsonlStore

ROUTE_REJECTION_LIMIT = 1000
QUERY_MAX_BYTES = 1024 * 1024
QUERY_MAX_LIMIT = 200
_PATH_LIMIT = 256
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class RouteRejections:
    """Route audits never enter inference request or runtime-event counts."""

    def __init__(self, data_dir: str | Path, *, limit: int = ROUTE_REJECTION_LIMIT, scope: dict | None = None) -> None:
        if type(limit) is not int or limit <= 0:
            raise ValueError("route audit limit must be a positive integer")
        self.store = JsonlStore(Path(data_dir) / "gateway_route_rejections.jsonl")
        self._scope = dict(scope or {})
        self._limit = limit
        self._lock = threading.Lock()
        self._window_start = time.monotonic()
        self._window_started_at = _utc_now()
        self._window_count = self._overflow_count = self._attempted_count = self._recorded_count = self._write_failure_count = 0
        self._last_write_error_class = self._last_write_failure_at = self._last_recorded_at = None

    @property
    def overflow_count(self) -> int:
        return self.status()["overflow_count"]

    def status(self) -> dict:
        with self._lock:
            return {"state": "error" if self._write_failure_count else "partial" if self._overflow_count else "ok",
                    "scope": "process_lifetime", "attempted_count": self._attempted_count,
                    "recorded_count": self._recorded_count, "overflow_count": self._overflow_count,
                    "write_failure_count": self._write_failure_count, "last_write_error_class": self._last_write_error_class,
                    "last_write_failure_at": self._last_write_failure_at, "last_recorded_at": self._last_recorded_at,
                    "window_count": self._window_count, "window_limit": self._limit,
                    "window_seconds": 3600, "window_started_at": self._window_started_at}

    def record(self, request_id: str, method: str, path: str, status: int, reason: str) -> None:
        with self._lock:
            self._attempted_count += 1
            now = time.monotonic()
            if now - self._window_start >= 3600:
                self._window_start, self._window_count = now, 0
                self._window_started_at = _utc_now()
            if self._window_count >= self._limit:
                self._overflow_count += 1
                return
            self._window_count += 1
            record = {"schema_version": "runtime-route-rejection/1.0", "event_id": uuid4().hex,
                      "request_id": request_id, "recorded_at": _utc_now(),
                      "method": method if re.fullmatch(r"[A-Z]{1,32}", method) else "OTHER",
                      "path": _CONTROL.sub("?", path.split("?", 1)[0].split("#", 1)[0])[:_PATH_LIMIT],
                      "response_code": status, "reason": reason, "forwarded_to_engine": False,
                      # Retention reads these established top-level fields;
                      # preserve the richer original scope without inferring identities.
                      "run_id": self._scope.get("run_id"), "environment": self._scope.get("environment"),
                      "scope": self._scope, "actor": {"state": "unattributed", "principal_id": None}}
            try:
                self.store.append(record)
            except OSError as error:
                self._write_failure_count += 1
                self._last_write_error_class, self._last_write_failure_at = type(error).__name__, _utc_now()
            else:
                self._recorded_count += 1
                self._last_recorded_at = record["recorded_at"]

    def query(self, *, limit: int = 100) -> dict:
        if type(limit) is not int or not 1 <= limit <= QUERY_MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {QUERY_MAX_LIMIT}")
        rows, corrupt = [], 0
        truncated, read_error, scanned_bytes = False, None, 0
        # Same existing per-path lock as retention and append: a rewrite cannot
        # mix two generations of the journal in one bounded read.
        with self.store._lock:
            try:
                with self.store.path.open("rb") as source:
                    source.seek(0, 2)
                    size = source.tell()
                    offset = max(0, size - QUERY_MAX_BYTES)
                    source.seek(offset)
                    raw = source.read(QUERY_MAX_BYTES)
                scanned_bytes = len(raw)
                truncated = offset > 0
                if offset:
                    raw = raw.partition(b"\n")[2]
                for line in raw.splitlines():
                    try:
                        row = json.loads(line.decode("utf-8"))
                        if not isinstance(row, dict) or row.get("schema_version") != "runtime-route-rejection/1.0":
                            raise ValueError("invalid route audit record")
                    except (ValueError, UnicodeError, RecursionError):
                        corrupt += 1
                        continue
                    rows.append(row)
            except FileNotFoundError:
                pass
            except OSError as error:
                read_error = type(error).__name__
        truncated = truncated or len(rows) > limit
        return {"items": list(reversed(rows[-limit:])), "status": self.status(), "generated_at": _utc_now(),
                "window": {"scope": "journal_tail_bytes", "total": None, "returned": min(len(rows), limit),
                           "truncated": truncated, "scanned_bytes": scanned_bytes, "max_bytes": QUERY_MAX_BYTES,
                           "limit": limit, "corrupt_lines": corrupt, "read_error_class": read_error}}
