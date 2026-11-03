"""Bounded audit journal for HTTP routes outside the Gateway's supported list."""

from __future__ import annotations

from pathlib import Path
import re
import threading
import time

from .mock_engine import _utc_now
from .store import JsonlStore


ROUTE_REJECTION_LIMIT = 1000  # Per Gateway process, per rolling one-hour window.
_PATH_LIMIT = 256
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class RouteRejections:
    """Keep route probes visible without adding them to inference request counts."""

    def __init__(self, data_dir: str | Path, *, limit: int = ROUTE_REJECTION_LIMIT) -> None:
        self.store = JsonlStore(Path(data_dir) / "gateway_route_rejections.jsonl")
        self._limit = limit
        self._lock = threading.Lock()
        self._window_start = time.monotonic()
        self._window_count = 0
        self._overflow_count = 0

    @property
    def overflow_count(self) -> int:
        with self._lock:
            return self._overflow_count

    def record(self, request_id: str, method: str, path: str, status: int, reason: str) -> None:
        with self._lock:
            now = time.monotonic()
            if now - self._window_start >= 3600:
                self._window_start = now
                self._window_count = 0
            if self._window_count >= self._limit:
                self._overflow_count += 1
                return
            self._window_count += 1
            record = {
                "schema_version": "runtime-route-rejection/1.0",
                "request_id": request_id,
                "recorded_at": _utc_now(),
                "method": method,
                "path": _CONTROL.sub("?", path)[:_PATH_LIMIT],
                "response_code": status,
                "reason": reason,
                "forwarded_to_engine": False,
            }
            try:
                self.store.append(record)
            except OSError:
                # Audit storage failure must not change an HTTP rejection.
                pass
