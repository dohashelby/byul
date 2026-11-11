"""Incremental JSONL observation feeds with durable restart checkpoints."""

from __future__ import annotations

from collections import Counter, deque
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from serving_runtime.enforcement.events import transform_lines_with_loss

from .collectors import CollectionFailure, NoCollectionUpdate
from .store import JsonlStore


HEARTBEAT_SCHEMA = "runtime-observation-feed-heartbeat/1.0"


class ObservationFeedIdle(NoCollectionUpdate):
    """The source file is readable but has not produced a complete new line."""


class ObservationFeedFailure(CollectionFailure, OSError):
    """Current feed metadata accompanies an unavailable writer or failed save."""


class ObservationFeed:
    """Read only newly appended complete lines and persist a safe byte offset."""

    _SEEN_LIMIT = 10_000
    _MAX_BATCH_BYTES = 4 * 1024 * 1024
    _MAX_LINE_BYTES = 1024 * 1024

    def __init__(
        self,
        *,
        kind: str,
        path: str | Path,
        data_dir: str | Path,
        data_origin: str,
        environment: str | None,
        run_id: str | None,
        allowed_engine_binaries: Iterable[str] | None = None,
        heartbeat_path: str | Path | None = None,
        heartbeat_stale_after_s: float = 15.0,
        exec_policy: Any = None,
        identity_path: str | Path | None = None,
        kube_context: str = "kind-runtime-lab",
        health_receipt_mode: int = 0o600,
    ) -> None:
        if type(health_receipt_mode) is not int or health_receipt_mode not in {0o600, 0o640}:
            raise ValueError("health_receipt_mode must be 0600 or 0640")
        self.health_receipt_mode = health_receipt_mode
        self.exec_policy = exec_policy
        self.identity_path = identity_path
        self.kube_context = kube_context
        if kind not in {"hubble", "tetragon", "management_listener"}:
            raise ValueError("observation feed kind must be hubble, tetragon or management_listener")
        self.kind = kind
        self.path = Path(path).expanduser()
        self.data_origin = data_origin
        self.environment = environment
        self.run_id = run_id
        self.allowed_engine_binaries = tuple(allowed_engine_binaries or ())
        self.heartbeat_path = (
            None if heartbeat_path is None else Path(heartbeat_path).expanduser()
        )
        if heartbeat_stale_after_s <= 0:
            raise ValueError("heartbeat_stale_after_s must be positive")
        self.heartbeat_stale_after_s = float(heartbeat_stale_after_s)
        root = Path(data_dir)
        self.store = JsonlStore(root / "observations.jsonl")
        self.state_path = root / f"observation_feed_{kind}.json"
        self._offset = 0
        self._device: int | None = None
        self._inode: int | None = None
        self._line_number = 0
        self._corrupt_lines_total = 0
        self._skipped_unsupported_total = 0
        self._out_of_window_total = 0
        self._hubble_lines_seen_total = 0
        self._lost_events_total = 0
        self._lost_events_by_source: Counter[str] = Counter()
        self._last_lost_at: str | None = None
        self._writer_started_at: str | None = None
        self._redaction_dropped_lines_total = 0
        self._writer_dropped_lines_total: int | None = None
        self._last_writer_drop_at: str | None = None
        self._last_redaction_drop_reason: str | None = None
        self._checkpoint_error: str | None = None
        self._checkpoint_pending = False
        self._storage_errors_total = 0
        self._last_storage_error_class: str | None = None
        self._last_storage_error_at: str | None = None
        self._last_storage_operation: str | None = None
        self._seen: deque[str] = deque(maxlen=self._SEEN_LIMIT)
        self._seen_set: set[str] = set()
        self._load_existing_keys()
        self._load_checkpoint()

    def _remember(self, key: str) -> None:
        if key in self._seen_set:
            return
        if len(self._seen) == self._SEEN_LIMIT:
            removed = self._seen.popleft()
            self._seen_set.discard(removed)
        self._seen.append(key)
        self._seen_set.add(key)

    def _load_existing_keys(self) -> None:
        records, _corrupt = self.store.read_all()
        for record in records[-self._SEEN_LIMIT :]:
            key = record.get("event_key")
            if isinstance(key, str) and key:
                self._remember(key)

    def _load_checkpoint(self) -> None:
        try:
            document = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
            self._checkpoint_error = type(error).__name__
            return
        try:
            if (
                not isinstance(document, dict)
                or document.get("schema") != "runtime-observation-feed-state/1.0"
                or document.get("kind") != self.kind
                or document.get("path") != str(self.path)
            ):
                raise ValueError("checkpoint does not match this feed")
            offset = document.get("offset")
            device = document.get("device")
            inode = document.get("inode")
            corrupt = document.get("corrupt_lines_total", 0)
            skipped = document.get("skipped_unsupported_total", 0)
            out_of_window = document.get("out_of_window_total", 0)
            line_number = document.get("line_number", 0)
            hubble_lines_seen = document.get("hubble_lines_seen_total", 0)
            lost_total = document.get("lost_events_total", 0)
            lost_by_source = document.get("lost_events_by_source", {})
            last_lost_at = document.get("last_lost_at")
            writer_dropped = document.get("writer_dropped_lines_total")
            last_writer_drop_at = document.get("last_writer_drop_at")
            keys = document.get("recent_event_keys", [])
            if (
                not isinstance(offset, int)
                or isinstance(offset, bool)
                or offset < 0
                or not isinstance(device, int)
                or not isinstance(inode, int)
                or not isinstance(corrupt, int)
                or corrupt < 0
                or not isinstance(skipped, int)
                or isinstance(skipped, bool)
                or skipped < 0
                or not isinstance(out_of_window, int)
                or out_of_window < 0
                or not isinstance(line_number, int)
                or line_number < 0
                or not isinstance(hubble_lines_seen, int)
                or hubble_lines_seen < 0
                or not isinstance(lost_total, int)
                or lost_total < 0
                or not isinstance(lost_by_source, dict)
                or any(
                    not isinstance(source, str)
                    or not source
                    or not isinstance(count, int)
                    or isinstance(count, bool)
                    or count < 0
                    for source, count in lost_by_source.items()
                )
                or (last_lost_at is not None and not isinstance(last_lost_at, str))
                or (
                    writer_dropped is not None
                    and (
                        not isinstance(writer_dropped, int)
                        or isinstance(writer_dropped, bool)
                        or writer_dropped < 0
                    )
                )
                or (
                    last_writer_drop_at is not None
                    and not isinstance(last_writer_drop_at, str)
                )
                or not isinstance(keys, list)
                or any(not isinstance(key, str) or not key for key in keys)
            ):
                raise ValueError("checkpoint fields are invalid")
        except ValueError as error:
            self._checkpoint_error = str(error)
            return
        self._offset = offset
        self._device = device
        self._inode = inode
        self._corrupt_lines_total = corrupt
        self._skipped_unsupported_total = skipped
        self._out_of_window_total = out_of_window
        self._line_number = line_number
        self._hubble_lines_seen_total = hubble_lines_seen
        self._lost_events_total = lost_total
        self._lost_events_by_source.update(lost_by_source)
        self._last_lost_at = last_lost_at
        self._writer_dropped_lines_total = writer_dropped
        started = document.get("writer_started_at")
        if isinstance(started, str):
            self._writer_started_at = started
        total = document.get("redaction_dropped_lines_total", writer_dropped or 0)
        if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
            self._redaction_dropped_lines_total = total
        self._last_writer_drop_at = last_writer_drop_at
        reason = document.get("last_redaction_drop_reason")
        if reason in {"INVALID_UTF8", "INVALID_JSON", "JSON_TOO_DEEP", "OBJECT_REQUIRED", "FILTER_REJECTED"}:
            self._last_redaction_drop_reason = reason
        storage_errors = document.get("storage_errors_total", 0)
        if isinstance(storage_errors, int) and not isinstance(storage_errors, bool) and storage_errors >= 0:
            self._storage_errors_total = storage_errors
        for name in ("last_storage_error_class", "last_storage_error_at", "last_storage_operation"):
            value = document.get(name)
            if isinstance(value, str):
                setattr(self, "_" + name, value)
        for key in keys[-self._SEEN_LIMIT :]:
            self._remember(key)

    def _write_checkpoint(self) -> None:
        document = {
            "schema": "runtime-observation-feed-state/1.0",
            "kind": self.kind,
            "path": str(self.path),
            "device": self._device,
            "inode": self._inode,
            "offset": self._offset,
            "line_number": self._line_number,
            "corrupt_lines_total": self._corrupt_lines_total,
            "skipped_unsupported_total": self._skipped_unsupported_total,
            "out_of_window_total": self._out_of_window_total,
            "hubble_lines_seen_total": self._hubble_lines_seen_total,
            "lost_events_total": self._lost_events_total,
            "lost_events_by_source": dict(self._lost_events_by_source),
            "last_lost_at": self._last_lost_at,
            "writer_dropped_lines_total": self._writer_dropped_lines_total,
            "redaction_dropped_lines_total": self._redaction_dropped_lines_total,
            "writer_started_at": self._writer_started_at,
            "last_writer_drop_at": self._last_writer_drop_at,
            "last_redaction_drop_reason": self._last_redaction_drop_reason,
            "recent_event_keys": list(self._seen),
            "storage_errors_total": self._storage_errors_total,
            "last_storage_error_class": self._last_storage_error_class,
            "last_storage_error_at": self._last_storage_error_at,
            "last_storage_operation": self._last_storage_operation,
        }
        encoded = (json.dumps(document, ensure_ascii=False) + "\n").encode("utf-8")
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.{os.getpid()}.{uuid4().hex}.tmp"
        )
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = -1
                    output.write(encoded)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.state_path)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _parse_time(value: Any, field: str) -> datetime:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty timestamp")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{field} must be an RFC3339 timestamp") from error
        if parsed.tzinfo is None:
            raise ValueError(f"{field} must include a timezone")
        return parsed.astimezone(timezone.utc)

    def _read_heartbeat(self) -> tuple[dict[str, Any], datetime | None]:
        if self.heartbeat_path is None:
            return (
                {
                    "configured": False,
                    "state": "not_configured",
                    "reason": "writer heartbeat is not configured",
                },
                None,
            )
        try:
            if self.kind == "management_listener":
                descriptor = os.open(self.heartbeat_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as source:
                    info = os.fstat(source.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022 or info.st_size > self._MAX_LINE_BYTES:
                        raise PermissionError("management heartbeat requires an owned regular file")
                    raw = source.read(self._MAX_LINE_BYTES + 1)
                if len(raw) > self._MAX_LINE_BYTES:
                    raise ValueError("management heartbeat exceeds size limit")
                document = json.loads(raw)
            else:
                document = json.loads(self.heartbeat_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise OSError("observation writer heartbeat is missing") from error
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise OSError("observation writer heartbeat cannot be read") from error
        if (
            not isinstance(document, dict)
            or document.get("schema") != HEARTBEAT_SCHEMA
            or document.get("kind") != self.kind
        ):
            raise ValueError("observation writer heartbeat schema or kind is invalid")
        if document.get("kube_context") not in (None, self.kube_context):
            raise ValueError("observation writer belongs to a different Kubernetes context")
        feed_path = document.get("feed_path")
        if (
            not isinstance(feed_path, str)
            or not feed_path
            or Path(feed_path).expanduser().absolute() != self.path.absolute()
        ):
            raise ValueError("observation writer heartbeat does not match the feed path")
        updated_at = self._parse_time(document.get("updated_at"), "heartbeat.updated_at")
        started_at = self._parse_time(document.get("started_at"), "heartbeat.started_at")
        age_s = (datetime.now(timezone.utc) - updated_at).total_seconds()
        if age_s < -60:
            raise ValueError("observation writer heartbeat is too far in the future")
        if age_s > self.heartbeat_stale_after_s:
            raise OSError(
                f"observation writer heartbeat expired ({age_s:.1f}s old)"
            )
        state = document.get("state")
        raw_processes = document.get("processes")
        if not isinstance(raw_processes, list) or not raw_processes:
            raise ValueError("observation writer heartbeat has no process status")
        processes: list[dict[str, Any]] = []
        for raw_process in raw_processes:
            if not isinstance(raw_process, dict):
                raise ValueError("observation writer process status is invalid")
            name = raw_process.get("name")
            running = raw_process.get("running")
            returncode = raw_process.get("returncode")
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(running, bool)
                or (
                    returncode is not None
                    and (not isinstance(returncode, int) or isinstance(returncode, bool))
                )
            ):
                raise ValueError("observation writer process fields are invalid")
            processes.append(
                {"name": name, "running": running, "returncode": returncode}
            )
        open_gap = document.get("open_gap") if isinstance(document.get("open_gap"), dict) else None
        reconnect_count = document.get("reconnect_count")
        last_gap = document.get("last_gap") if isinstance(document.get("last_gap"), dict) else None
        dropped_lines_total = document.get("dropped_lines_total")
        if dropped_lines_total is not None and (
            not isinstance(dropped_lines_total, int)
            or isinstance(dropped_lines_total, bool)
            or dropped_lines_total < 0
        ):
            raise ValueError("observation writer dropped_lines_total is invalid")
        redaction_error = document.get("last_redaction_error_class")
        if redaction_error is not None and (
            not isinstance(redaction_error, str) or not redaction_error
        ):
            raise ValueError(
                "observation writer last_redaction_error_class is invalid"
            )
        recording = document.get("recording") if self.kind == "management_listener" else None
        if self.kind == "management_listener" and (not isinstance(recording, dict) or type(recording.get("write_failure_count")) is not int or recording["write_failure_count"] < 0 or type(recording.get("write_failed")) is not bool):
            raise ValueError("management writer recording status is invalid")
        if recording is not None:
            error_class = recording.get("last_write_error_class")
            error_at = recording.get("last_write_error_at")
            if recording["write_failure_count"] == 0:
                if recording["write_failed"] or error_class is not None or error_at is not None:
                    raise ValueError("management writer failure receipt is inconsistent")
            else:
                if not isinstance(error_class, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", error_class) is None:
                    raise ValueError("management writer error class is invalid")
                when = self._parse_time(error_at, "writer.last_write_error_at")
                if when < started_at or when > updated_at:
                    raise ValueError("management writer failure timestamp is outside its generation")
        drop_reason = document.get("last_redaction_drop_reason")
        drop_at = document.get("last_redaction_drop_at")
        if drop_reason is not None and drop_reason not in {"INVALID_UTF8", "INVALID_JSON", "JSON_TOO_DEEP", "OBJECT_REQUIRED", "FILTER_REJECTED"}:
            raise ValueError("observation writer drop reason is invalid")
        if drop_at is not None:
            parsed_drop = self._parse_time(drop_at, "writer.last_redaction_drop_at")
            if parsed_drop > updated_at or parsed_drop < started_at:
                raise ValueError("observation writer drop timestamp is outside its generation")
        if (drop_reason is None) != (drop_at is None):
            raise ValueError("observation writer drop reason and timestamp must be paired")
        heartbeat = {
            "last_redaction_drop_reason": drop_reason,
            "last_redaction_drop_at": drop_at,
            "recording": recording,
            "configured": True,
            "state": state,
            "updated_at": document["updated_at"],
            "started_at": document["started_at"],
            "age_s": max(0.0, round(age_s, 3)),
            "processes": processes,
            "reconnect_count": (
                reconnect_count
                if isinstance(reconnect_count, int) and not isinstance(reconnect_count, bool)
                else None
            ),
            "last_gap": (
                {
                    key: last_gap.get(key)
                    for key in ("gap_id", "opened_at", "closed_at", "backfill", "attempts")
                }
                if last_gap
                else None
            ),
            "open_gap": (
                {key: open_gap.get(key) for key in ("gap_id", "opened_at", "closed_at", "backfill", "attempts")}
                if open_gap else None
            ),
            "dropped_lines_total": dropped_lines_total,
            "last_redaction_error_class": redaction_error,
        }
        if state != "running" or not all(item["running"] for item in processes) or (recording is not None and recording["write_failed"]):
            loss_changed = self._update_writer_loss(heartbeat)
            if loss_changed or self._checkpoint_pending:
                if self._device is None or self._inode is None:
                    identity = self.path.stat()
                    self._device, self._inode = identity.st_dev, identity.st_ino
                self._save_checkpoint(heartbeat)
            since = open_gap.get("opened_at") if open_gap else None
            message = (f"observation writer is reconnecting (gap open since {since or 'unknown'})"
                       if state == "reconnecting" else f"observation writer is not running (state={state})")
            raise ObservationFeedFailure(message, last_value=self._status(
                mode="unavailable", processed=0, accepted=0, duplicates=0, rejected=[],
                out_of_window=0, first_line=None, heartbeat=heartbeat, idle=False), data_origin=self.data_origin)
        return heartbeat, started_at

    def _update_writer_loss(self, heartbeat: dict[str, Any]) -> bool:
        if self.kind != "tetragon":
            return False
        current = heartbeat.get("dropped_lines_total")
        if not isinstance(current, int) or isinstance(current, bool):
            return False
        previous = self._writer_dropped_lines_total
        started = heartbeat.get("started_at")
        restarted = isinstance(started, str) and self._writer_started_at is not None and started != self._writer_started_at
        changed = current != previous or restarted
        reset = previous is None or restarted or current < previous
        added = current if reset else current - previous
        self._redaction_dropped_lines_total += added
        if added > 0:
            self._last_writer_drop_at = heartbeat.get("last_redaction_drop_at") or datetime.now(timezone.utc).isoformat()
            self._last_redaction_drop_reason = heartbeat.get("last_redaction_drop_reason")
        self._writer_dropped_lines_total = current
        if isinstance(started, str):
            self._writer_started_at = started
        return changed

    def _status(
        self,
        *,
        mode: str,
        processed: int,
        accepted: int,
        duplicates: int,
        rejected: list[dict[str, Any]],
        out_of_window: int,
        first_line: int | None,
        heartbeat: dict[str, Any],
        idle: bool,
    ) -> dict[str, Any]:
        loss_detected: bool | None = None
        lost_total: int | None = None
        lost_by_source: dict[str, int] | None = None
        if self.kind == "hubble" and self._hubble_lines_seen_total > 0:
            lost_total = self._lost_events_total
            lost_by_source = dict(sorted(self._lost_events_by_source.items()))
            loss_detected = False
            if self._last_lost_at is not None:
                try:
                    last_lost = self._parse_time(self._last_lost_at, "last_lost_at")
                    loss_detected = (
                        datetime.now(timezone.utc) - last_lost
                    ).total_seconds() <= self.heartbeat_stale_after_s
                except ValueError:
                    loss_detected = None
        redaction_loss_detected: bool | None = None
        if self.kind == "tetragon" and self._writer_dropped_lines_total is not None:
            redaction_loss_detected = False
            if self._last_writer_drop_at is not None:
                try:
                    last_drop = self._parse_time(
                        self._last_writer_drop_at, "last_writer_drop_at"
                    )
                    redaction_loss_detected = (
                        datetime.now(timezone.utc) - last_drop
                    ).total_seconds() <= self.heartbeat_stale_after_s
                except ValueError:
                    redaction_loss_detected = None
        return {
            "path": str(self.path),
            "kind": self.kind,
            "mode": mode,
            "processed": processed,
            "accepted": accepted,
            "duplicates": duplicates,
            "rejected": len(rejected),
            "rejected_details": rejected[:20],
            "corrupt_lines_total": self._corrupt_lines_total,
            "skipped_unsupported_total": self._skipped_unsupported_total,
            "out_of_window": out_of_window,
            "out_of_window_total": self._out_of_window_total,
            "offset": self._offset,
            "first_line": first_line,
            "last_line": self._line_number,
            "checkpoint_reset_reason": self._checkpoint_error,
            "data_origin": self.data_origin,
            "environment": self.environment,
            "run_id": self.run_id,
            "idle": idle,
            "writer": heartbeat,
            "storage_errors_total": self._storage_errors_total,
            "last_storage_error_class": self._last_storage_error_class,
            "last_storage_error_at": self._last_storage_error_at,
            "last_storage_operation": self._last_storage_operation,
            "checkpoint_pending": self._checkpoint_pending,
            "lost_events_total": lost_total,
            "lost_events_by_source": lost_by_source,
            "last_lost_at": self._last_lost_at if self.kind == "hubble" else None,
            "loss_detected": loss_detected,
            "dropped_lines_total": (
                self._writer_dropped_lines_total
                if self.kind == "tetragon"
                else None
            ),
            "last_redaction_error_class": (
                heartbeat.get("last_redaction_error_class")
                if self.kind == "tetragon"
                else None
            ),
            "last_redaction_drop_reason": self._last_redaction_drop_reason if self.kind == "tetragon" else None,
            "last_redaction_drop_at": (
                self._last_writer_drop_at if self.kind == "tetragon" else None
            ),
            "redaction_loss_detected": redaction_loss_detected,
            "redaction_dropped_lines_total": self._redaction_dropped_lines_total if self.kind == "tetragon" else None,
        }

    def _storage_failure(self, error: OSError, operation: str, heartbeat: dict[str, Any]) -> ObservationFeedFailure:
        self._storage_errors_total += 1
        self._last_storage_error_class = type(error).__name__
        self._last_storage_error_at = datetime.now(timezone.utc).isoformat()
        self._last_storage_operation = operation
        value = self._status(mode="save_failed", processed=0, accepted=0, duplicates=0,
                             rejected=[], out_of_window=0, first_line=None, heartbeat=heartbeat, idle=False)
        return ObservationFeedFailure(f"observation {operation} failed ({type(error).__name__})",
                                      last_value=value, data_origin=self.data_origin)

    def _save_checkpoint(self, heartbeat: dict[str, Any]) -> None:
        self._checkpoint_pending = True
        try:
            self._write_checkpoint()
        except OSError as error:
            raise self._storage_failure(error, "checkpoint", heartbeat) from error
        self._checkpoint_pending = False

    def _save_health_receipt(self, item):
        path = self.state_path.parent / "tetragon-health-receipts.json"
        nodes = {}
        try:
            document = json.loads(path.read_text())
            if document.get("context") == self.kube_context and isinstance(document.get("nodes"), dict):
                nodes = document["nodes"]
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        node = item["identity"]["node"]
        if node not in nodes or nodes[node].get("observed_at", "") <= item["observed_at"]:
            nodes[node] = item
        encoded = (json.dumps({"schema_version":"runtime-tetragon-health-receipts/1.0",
            "context":self.kube_context,"nodes":nodes}, ensure_ascii=False)+"\n").encode("utf-8")
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = -1
                    output.write(encoded)
                    os.fchmod(output.fileno(), self.health_receipt_mode)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def collect(self) -> tuple[dict[str, Any], str]:
        heartbeat, writer_started_at = self._read_heartbeat()
        identity_index = None
        if self.kind == "tetragon" and self.identity_path is not None:
            from serving_runtime.enforcement.attribution import read_identities
            identity_index = read_identities(self.identity_path, context=self.kube_context)
        writer_loss_changed = self._update_writer_loss(heartbeat)
        mode = "append"
        if self.kind == "management_listener":
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
                os.close(fd)
                raise PermissionError("management feed requires an owned, non-writable regular journal")
            opened = os.fdopen(fd, "rb")
        else:
            opened = self.path.open("rb")
        with opened as source:
            stat_result = os.fstat(source.fileno())
            identity = (stat_result.st_dev, stat_result.st_ino)
            if self._device is None or self._inode is None:
                self._device, self._inode = identity
            elif identity != (self._device, self._inode):
                self._device, self._inode = identity
                self._offset = 0
                self._line_number = 0
                mode = "rotated"
            elif stat_result.st_size < self._offset:
                self._offset = 0
                self._line_number = 0
                mode = "truncated"
            source.seek(self._offset)
            payload = source.read(self._MAX_BATCH_BYTES + 1)

        complete_length = payload.rfind(b"\n") + 1
        if complete_length == 0:
            if len(payload) > self._MAX_LINE_BYTES:
                raise ValueError("observation feed line exceeds 1 MiB")
            if mode != "append" or writer_loss_changed or self._checkpoint_pending:
                self._save_checkpoint(heartbeat)
            value = self._status(
                mode=mode,
                processed=0,
                accepted=0,
                duplicates=0,
                rejected=[],
                out_of_window=0,
                first_line=None,
                heartbeat=heartbeat,
                idle=True,
            )
            if heartbeat["configured"]:
                self._checkpoint_error = None
                return value, self.data_origin
            raise ObservationFeedIdle(
                "observation feed has no new line and writer heartbeat is not configured",
                last_value=value,
                data_origin=self.data_origin,
            )

        raw_lines = payload[:complete_length].splitlines()
        accepted_count = 0
        duplicate_count = 0
        out_of_window_count = 0
        valid_hubble_count = 0
        corrupt_count = 0
        skipped_count = 0
        rejected: list[dict[str, Any]] = []
        first_line_number = self._line_number + 1
        for index, raw_line in enumerate(raw_lines, start=1):
            line_number = self._line_number + index
            if len(raw_line) > self._MAX_LINE_BYTES:
                rejected.append(
                    {
                        "line": line_number,
                        "reason": "observation feed line exceeds 1 MiB",
                    }
                )
                corrupt_count += 1
                continue
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError as error:
                rejected.append({"line": line_number, "reason": str(error)})
                corrupt_count += 1
                continue
            accepted, line_rejected, losses = transform_lines_with_loss(
                [line],
                kind=self.kind,
                data_origin=self.data_origin,
                allowed_engine_binaries=self.allowed_engine_binaries,
                environment=self.environment,
                run_id=self.run_id,
                exec_policy=self.exec_policy,
                identity_index=identity_index,
            )
            if line_rejected:
                rejected.append(
                    {"line": line_number, "reason": line_rejected[0]["reason"]}
                )
                corrupt_count += 1
                continue
            if not accepted and not losses:
                skipped_count += 1
                continue
            item = accepted[0] if accepted else losses[0]
            if self.kind == "tetragon":
                from serving_runtime.enforcement.codeload import apply_collection_evidence
                apply_collection_evidence(item, heartbeat)
            if writer_started_at is not None:
                try:
                    observed_at = self._parse_time(
                        item.get("observed_at"), "event.observed_at"
                    )
                except ValueError as error:
                    rejected.append({"line": line_number, "reason": str(error)})
                    corrupt_count += 1
                    continue
                if observed_at < writer_started_at:
                    rejected.append(
                        {
                            "line": line_number,
                            "reason": "event observed_at precedes writer started_at",
                        }
                    )
                    out_of_window_count += 1
                    continue
            if self.kind == "hubble":
                valid_hubble_count += 1
            event_key = hashlib.sha256(
                self.kind.encode("ascii")
                + b"\0"
                + self.data_origin.encode("ascii")
                + b"\0"
                + (self.environment or "").encode("utf-8")
                + b"\0"
                + (self.run_id or "").encode("utf-8")
                + b"\0"
                + raw_line
            ).hexdigest()
            if event_key in self._seen_set:
                duplicate_count += 1
                continue
            if losses:
                loss = losses[0]
                self._lost_events_total += loss["num_events_lost"]
                self._lost_events_by_source[loss["source"]] += loss[
                    "num_events_lost"
                ]
                self._last_lost_at = loss["observed_at"]
                self._remember(event_key)
                continue
            item["event_key"] = event_key
            try:
                if item.get("observation_purpose") == "health_check":
                    self._save_health_receipt(item)
                else:
                    self.store.append(item)
            except OSError as error:
                raise self._storage_failure(error, "event_record", heartbeat) from error
            self._remember(event_key)
            accepted_count += 1

        self._offset += complete_length
        self._line_number += len(raw_lines)
        self._corrupt_lines_total += corrupt_count
        self._skipped_unsupported_total += skipped_count
        self._out_of_window_total += out_of_window_count
        self._hubble_lines_seen_total += valid_hubble_count
        checkpoint_error = self._checkpoint_error
        self._save_checkpoint(heartbeat)
        value = self._status(
            mode=mode,
            processed=len(raw_lines),
            accepted=accepted_count,
            duplicates=duplicate_count,
            rejected=rejected,
            out_of_window=out_of_window_count,
            first_line=first_line_number,
            heartbeat=heartbeat,
            idle=False,
        )
        value["checkpoint_reset_reason"] = checkpoint_error
        self._checkpoint_error = None
        return (
            value,
            self.data_origin,
        )
