"""Gateway-side lookup of request IDs observed in engine logs (optional evidence source).

"not_observed" is reported only when a fresh, healthy collection window fully
covers the request; it means "not seen in engine logs", never "not received".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import threading
from typing import Any

SCHEMA = "runtime-engine-receipts/1.0"
REQUEST_ID = re.compile(r"[0-9a-f]{32}")
MAX_BYTES = 4 * 1024 * 1024


def _time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone")
    return parsed


class EngineReceipts:
    def __init__(self, path: str | Path | None, *, context: str | None = None,
                 stale_after_s: float = 60, settle_s: float = 5) -> None:
        self.path = Path(path) if path else None
        self.context = context
        self.stale_after_s = stale_after_s
        self.settle_s = settle_s
        self._cache: tuple[tuple[int, int], dict[str, Any]] | None = None
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        info = self.path.stat()
        key = (info.st_mtime_ns, info.st_size)
        with self._lock:
            if self._cache and self._cache[0] == key:
                return self._cache[1]
        if info.st_size > MAX_BYTES:
            raise ValueError("too large")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != SCHEMA:
            raise ValueError("schema")
        if self.context is not None and value.get("context") != self.context:
            raise ValueError("context")
        if value.get("state") not in {"ok", "error"} or not isinstance(value.get("receipts"), list):
            raise ValueError("state")
        receipts = {}
        for item in value["receipts"]:
            if not isinstance(item, dict) or not REQUEST_ID.fullmatch(str(item.get("request_id"))):
                raise ValueError("receipt")
            receipts[item["request_id"]] = _time(item["observed_at"])
        loaded = {"state": value["state"], "collected_at": _time(value["collected_at"]),
                  "window_started_at": _time(value["window_started_at"]) if value.get("window_started_at") else None,
                  "receipts": receipts}
        with self._lock:
            self._cache = (key, loaded)
        return loaded

    def lookup(self, request_id: str, recorded_at: str | None, *, now: datetime | None = None) -> dict[str, Any]:
        """Return {status, observed_at, collected_at}; status is observed/not_observed/unavailable/not_configured."""
        if self.path is None:
            return {"status": "not_configured", "observed_at": None, "collected_at": None}
        try:
            if not self.path.exists():
                return {"status": "unavailable", "observed_at": None, "collected_at": None}
            data = self._load()
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            return {"status": "unavailable", "observed_at": None, "collected_at": None}
        collected = data["collected_at"]
        observed = data["receipts"].get(request_id)
        result = {"observed_at": observed.isoformat().replace("+00:00", "Z") if observed else None,
                  "collected_at": collected.isoformat().replace("+00:00", "Z")}
        if observed:
            return {"status": "observed", **result}
        current = now or datetime.now(timezone.utc)
        try:
            at = _time(recorded_at)
        except (ValueError, TypeError):
            return {"status": "unavailable", **result}
        fresh = data["state"] == "ok" and -5 <= (current - collected).total_seconds() <= self.stale_after_s
        covered = (data["window_started_at"] is not None and data["window_started_at"] <= at
                   and collected >= at + timedelta(seconds=self.settle_s))
        return {"status": "not_observed" if fresh and covered else "unavailable", **result}
