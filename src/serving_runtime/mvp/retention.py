"""LAB record retention (plan v1.4 §6.5, D-08: 7 days or 5 GiB, whichever first).

By default this only reports what the limits would remove. Records are deleted
only when retention is explicitly enforced, records held for test evidence are
never deleted, and every deletion is logged with the removed time range so the
gap stays visible instead of looking like normal operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
from typing import Any

from .store import JsonlStore


HOLD_SCHEMA = "runtime-retention-hold/1.0"
RUN_SCHEMA = "runtime-retention-run/1.0"
DEFAULT_MAX_AGE_DAYS = 7
DEFAULT_MAX_BYTES = 5 * 1024**3


@dataclass(frozen=True)
class ManagedFile:
    name: str
    time_field: str
    # Records sharing this field are kept or removed together, so a request's
    # started/final pair never splits into a misleading "incomplete" request.
    group_field: str | None


# Raw LAB records only. Policy history and re-validation requests rebuild state
# on restart and are small, so they are never pruned here.
MANAGED_FILES = (
    ManagedFile("gateway_records.jsonl", "recorded_at", "request_id"),
    ManagedFile("engine_receipts.jsonl", "received_at", "request_id"),
    ManagedFile("observations.jsonl", "observed_at", None),
)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def load_holds(path: str | Path | None) -> tuple[set[str], set[str], dict[str, Any]]:
    """Return (run_ids, request_ids, state). An unreadable hold file is an error."""

    if path is None:
        return set(), set(), {"state": "not_configured", "path": None}
    path_text = str(path)
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set(), set(), {"state": "error", "path": path_text, "error": "hold file not found"}
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        return set(), set(), {"state": "error", "path": path_text, "error": type(error).__name__}
    if (
        not isinstance(document, dict)
        or document.get("schema") != HOLD_SCHEMA
        or set(document) - {"schema", "run_ids", "request_ids", "note"}
    ):
        return set(), set(), {"state": "error", "path": path_text, "error": "invalid hold document"}
    values: dict[str, set[str]] = {}
    for key in ("run_ids", "request_ids"):
        items = document.get(key, [])
        if not isinstance(items, list) or any(not isinstance(item, str) or not item for item in items):
            return set(), set(), {"state": "error", "path": path_text, "error": f"{key} must be strings"}
        values[key] = set(items)
    return (
        values["run_ids"],
        values["request_ids"],
        {
            "state": "ok",
            "path": path_text,
            "run_ids": len(values["run_ids"]),
            "request_ids": len(values["request_ids"]),
        },
    )


@dataclass
class _Unit:
    newest: datetime | None
    lines: list[int]
    size: int
    held: bool


class RecordRetention:
    """Evaluate and optionally apply the LAB retention limits to one data dir."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        max_age_days: float = DEFAULT_MAX_AGE_DAYS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        enforce: bool = False,
        hold_path: str | Path | None = None,
    ) -> None:
        if max_age_days <= 0:
            raise ValueError("retention max age must be positive")
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            raise ValueError("retention max bytes must be a positive integer")
        self.data_dir = Path(data_dir)
        self.max_age = timedelta(days=max_age_days)
        self.max_bytes = max_bytes
        self.enforce = enforce
        self.hold_path = hold_path
        self.log = JsonlStore(self.data_dir / "retention_log.jsonl")
        self._run_lock = threading.Lock()

    def _scan(
        self,
        managed: ManagedFile,
        hold_runs: set[str],
        hold_requests: set[str],
    ) -> tuple[dict[str, _Unit], int, int]:
        """Return (units, unclassified_lines, file_bytes) without changing the file."""

        units: dict[str, _Unit] = {}
        unclassified = 0
        path = self.data_dir / managed.name
        try:
            source = path.open("rb")
        except FileNotFoundError:
            return units, 0, 0
        with source:
            for line_number, raw_line in enumerate(source, start=1):
                try:
                    record = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError, RecursionError):
                    record = None
                timestamp = _parse_time(record.get(managed.time_field)) if isinstance(record, dict) else None
                if timestamp is None:
                    # Lines we cannot date are never removed by age or size.
                    unclassified += 1
                    continue
                group = record.get(managed.group_field) if managed.group_field else None
                key = f"g:{group}" if isinstance(group, str) and group else f"l:{line_number}"
                held = record.get("run_id") in hold_runs or record.get("request_id") in hold_requests
                unit = units.get(key)
                if unit is None:
                    units[key] = _Unit(timestamp, [line_number], len(raw_line), held)
                else:
                    unit.lines.append(line_number)
                    unit.size += len(raw_line)
                    unit.held = unit.held or held
                    if unit.newest is None or timestamp > unit.newest:
                        unit.newest = timestamp
        return units, unclassified, path.stat().st_size

    def run(self, *, now: datetime | None = None) -> dict[str, Any]:
        with self._run_lock:
            return self._run(now or datetime.now(timezone.utc))

    def _run(self, now: datetime) -> dict[str, Any]:
        hold_runs, hold_requests, hold_state = load_holds(self.hold_path)
        cutoff = now - self.max_age
        scanned: dict[str, tuple[dict[str, _Unit], int, int]] = {}
        for managed in MANAGED_FILES:
            units, unclassified, size = self._scan(managed, hold_runs, hold_requests)
            if managed.name == "gateway_records.jsonl":
                # A held run also holds the engine receipts of its requests.
                hold_requests = hold_requests | {
                    key[2:] for key, unit in units.items() if unit.held and key.startswith("g:")
                }
            scanned[managed.name] = (units, unclassified, size)

        removal: dict[str, dict[str, str]] = {name: {} for name in scanned}
        for name, (units, _unclassified, _size) in scanned.items():
            for key, unit in units.items():
                if not unit.held and unit.newest is not None and unit.newest < cutoff:
                    removal[name][key] = "age"

        total_bytes = sum(size for _units, _unclassified, size in scanned.values())
        remaining = total_bytes - sum(
            scanned[name][0][key].size for name, keys in removal.items() for key in keys
        )
        if remaining > self.max_bytes:
            candidates = sorted(
                (
                    (unit.newest, name, key)
                    for name, (units, _u, _s) in scanned.items()
                    for key, unit in units.items()
                    if not unit.held and key not in removal[name]
                ),
                key=lambda item: item[0],
            )
            for _newest, name, key in candidates:
                if remaining <= self.max_bytes:
                    break
                removal[name][key] = "size"
                remaining -= scanned[name][0][key].size

        # A broken hold file must not lead to deleting what it meant to protect.
        can_delete = self.enforce and hold_state["state"] != "error"
        files: dict[str, Any] = {}
        for name, (units, unclassified, size) in scanned.items():
            chosen = removal[name]
            removed_times = [units[key].newest for key in chosen if units[key].newest is not None]
            lines = sorted(line for key in chosen for line in units[key].lines)
            entry: dict[str, Any] = {
                "bytes": size,
                "units": len(units),
                "held_units": sum(1 for unit in units.values() if unit.held),
                "unclassified_lines": unclassified,
                "removable_by_age": sum(1 for reason in chosen.values() if reason == "age"),
                "removable_by_size": sum(1 for reason in chosen.values() if reason == "size"),
                "removable_lines": len(lines),
                "removable_range": (
                    {"from": min(removed_times).isoformat(), "to": max(removed_times).isoformat()}
                    if removed_times
                    else None
                ),
                "removed_lines": 0,
            }
            if can_delete and lines:
                try:
                    _kept, removed = JsonlStore(self.data_dir / name).remove_lines(lines)
                    entry["removed_lines"] = removed
                except OSError as error:
                    entry["error"] = type(error).__name__
            files[name] = entry

        removed_total = sum(entry["removed_lines"] for entry in files.values())
        summary = {
            "schema": RUN_SCHEMA,
            "ran_at": now.isoformat(),
            "mode": "enforce" if self.enforce else "report_only",
            "deleted": removed_total > 0,
            "limits": {"max_age_days": self.max_age / timedelta(days=1), "max_bytes": self.max_bytes},
            "cutoff": cutoff.isoformat(),
            "total_bytes": total_bytes,
            "over_size_limit": total_bytes > self.max_bytes,
            "over_size_limit_after": remaining > self.max_bytes,
            "holds": hold_state,
            "files": files,
            "errors": [name for name, entry in files.items() if "error" in entry],
        }
        if self.enforce and hold_state["state"] == "error":
            summary["blocked_reason"] = "hold file unreadable; nothing deleted"
        if removed_total or summary["errors"]:
            self.log.append(summary)
        summary["last_deletion"] = self.last_deletion()
        return summary

    def last_deletion(self) -> dict[str, Any] | None:
        """The newest logged deletion, so the removed range stays visible."""

        records, _corrupt = self.log.read_all()
        for record in reversed(records):
            if record.get("deleted"):
                return {
                    "ran_at": record.get("ran_at"),
                    "files": {
                        name: {
                            "removed_lines": entry.get("removed_lines"),
                            "removable_range": entry.get("removable_range"),
                        }
                        for name, entry in (record.get("files") or {}).items()
                        if isinstance(entry, dict) and entry.get("removed_lines")
                    },
                }
        return None

    def collect(self) -> tuple[dict[str, Any], str]:
        return self.run(), "gateway_local"
