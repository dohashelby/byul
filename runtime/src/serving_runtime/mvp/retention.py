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

from .store import JsonlStore, RewriteCommittedError


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
    lines: dict[str, list[int]]
    size: int
    held: bool
    classified: bool


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
        self._last_deletion_cache: dict[str, Any] | None = None

    def _scan_file(
        self,
        managed: ManagedFile,
        hold_runs: set[str],
        hold_requests: set[str],
        units: dict[str, _Unit],
    ) -> tuple[int, int, set[str]]:
        """Add one file to shared units and return its unclassified/size/unit keys."""

        unclassified = 0
        file_units: set[str] = set()
        path = self.data_dir / managed.name
        try:
            source = path.open("rb")
        except FileNotFoundError:
            return 0, 0, file_units
        with source:
            for line_number, raw_line in enumerate(source, start=1):
                try:
                    record = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError, RecursionError):
                    record = None
                timestamp = _parse_time(record.get(managed.time_field)) if isinstance(record, dict) else None
                request_id = record.get("request_id") if isinstance(record, dict) else None
                run_id = record.get("run_id") if isinstance(record, dict) else None
                group = record.get(managed.group_field) if managed.group_field and isinstance(record, dict) else None
                if isinstance(group, str) and group:
                    # Gateway records and engine receipts with the same request
                    # ID form one unit even though they live in different files.
                    key = f"request:{group}"
                else:
                    key = f"{managed.name}:line:{line_number}"
                held = (
                    isinstance(run_id, str) and run_id in hold_runs
                ) or (
                    isinstance(request_id, str) and request_id in hold_requests
                )
                if timestamp is None:
                    # An undated request line still joins its cross-file unit:
                    # it prevents deletion and can propagate a run hold to the
                    # request's receipts. Other undated lines remain untouched.
                    unclassified += 1
                    if not (managed.group_field and isinstance(group, str) and group):
                        continue
                unit = units.get(key)
                if unit is None:
                    units[key] = _Unit(
                        timestamp,
                        {managed.name: [line_number]},
                        len(raw_line),
                        held,
                        timestamp is not None,
                    )
                else:
                    unit.lines.setdefault(managed.name, []).append(line_number)
                    unit.size += len(raw_line)
                    unit.held = unit.held or held
                    unit.classified = unit.classified and timestamp is not None
                    if timestamp is not None and (
                        unit.newest is None or timestamp > unit.newest
                    ):
                        unit.newest = timestamp
                file_units.add(key)
        return unclassified, path.stat().st_size, file_units

    def run(self, *, now: datetime | None = None) -> dict[str, Any]:
        with self._run_lock:
            return self._run(now or datetime.now(timezone.utc))

    def _run(self, now: datetime) -> dict[str, Any]:
        hold_runs, hold_requests, hold_state = load_holds(self.hold_path)
        cutoff = now - self.max_age
        units: dict[str, _Unit] = {}
        scanned: dict[str, tuple[int, int, set[str]]] = {}
        for managed in MANAGED_FILES:
            scanned[managed.name] = self._scan_file(
                managed, hold_runs, hold_requests, units
            )

        removal: dict[str, str] = {}
        for key, unit in units.items():
            if (
                not unit.held
                and unit.classified
                and unit.newest is not None
                and unit.newest < cutoff
            ):
                removal[key] = "age"

        total_bytes = sum(size for _unclassified, size, _keys in scanned.values())
        remaining = total_bytes - sum(units[key].size for key in removal)
        if remaining > self.max_bytes:
            candidates = sorted(
                (
                    (unit.newest, key)
                    for key, unit in units.items()
                    if not unit.held
                    and unit.classified
                    and unit.newest is not None
                    and key not in removal
                ),
                key=lambda item: item[0],
            )
            for _newest, key in candidates:
                if remaining <= self.max_bytes:
                    break
                removal[key] = "size"
                remaining -= units[key].size

        # A broken hold file must not lead to deleting what it meant to protect.
        can_delete = self.enforce and hold_state["state"] != "error"
        files: dict[str, Any] = {}
        for name, (unclassified, size, file_units) in scanned.items():
            chosen = {key: removal[key] for key in file_units if key in removal}
            removed_times = [
                units[key].newest
                for key in chosen
                if units[key].newest is not None
            ]
            lines = sorted(
                line
                for key in chosen
                for line in units[key].lines.get(name, [])
            )
            entry: dict[str, Any] = {
                "bytes": size,
                "units": len(file_units),
                "held_units": sum(1 for key in file_units if units[key].held),
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
                "durability": "not_applicable",
            }
            if can_delete and lines:
                try:
                    _kept, removed = JsonlStore(self.data_dir / name).remove_lines(lines)
                    entry["removed_lines"] = removed
                    entry["durability"] = "confirmed"
                except RewriteCommittedError as error:
                    entry["removed_lines"] = error.removed
                    entry["durability"] = "uncertain"
                    entry["error"] = f"durability_uncertain:{error.cause_type}"
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
            "retention_log": {"state": "not_written"},
        }
        if self.enforce and hold_state["state"] == "error":
            summary["blocked_reason"] = "hold file unreadable; nothing deleted"
        if removed_total:
            self._last_deletion_cache = self._deletion_view(summary)
        if removed_total or summary["errors"]:
            try:
                summary["retention_log"] = {"state": "ok"}
                self.log.append(summary)
            except OSError as error:
                summary["retention_log"] = {
                    "state": "error",
                    "error": type(error).__name__,
                }
                summary["errors"].append("retention_log.jsonl")
        summary["last_deletion"] = self.last_deletion()
        return summary

    @staticmethod
    def _deletion_view(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "ran_at": record.get("ran_at"),
            "files": {
                name: {
                    "removed_lines": entry.get("removed_lines"),
                    "removable_range": entry.get("removable_range"),
                    "durability": entry.get("durability"),
                    "error": entry.get("error"),
                }
                for name, entry in (record.get("files") or {}).items()
                if isinstance(entry, dict) and entry.get("removed_lines")
            },
        }

    def last_deletion(self) -> dict[str, Any] | None:
        """The newest logged deletion, so the removed range stays visible."""

        if self._last_deletion_cache is not None:
            return self._last_deletion_cache
        try:
            records, _corrupt = self.log.read_all()
        except OSError:
            return None
        for record in reversed(records):
            if record.get("deleted"):
                return self._deletion_view(record)
        return None

    def collect(self) -> tuple[dict[str, Any], str]:
        return self.run(), "gateway_local"
