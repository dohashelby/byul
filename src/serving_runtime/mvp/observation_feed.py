"""Incremental JSONL observation feeds with durable restart checkpoints."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from serving_runtime.enforcement.events import transform_lines

from .collectors import NoCollectionUpdate
from .store import JsonlStore


HEARTBEAT_SCHEMA = "runtime-observation-feed-heartbeat/1.0"


class ObservationFeedIdle(NoCollectionUpdate):
    """The source file is readable but has not produced a complete new line."""


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
    ) -> None:
        if kind not in {"hubble", "tetragon"}:
            raise ValueError("observation feed kind must be hubble or tetragon")
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
        self._out_of_window_total = 0
        self._checkpoint_error: str | None = None
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
            out_of_window = document.get("out_of_window_total", 0)
            line_number = document.get("line_number", 0)
            keys = document.get("recent_event_keys", [])
            if (
                not isinstance(offset, int)
                or isinstance(offset, bool)
                or offset < 0
                or not isinstance(device, int)
                or not isinstance(inode, int)
                or not isinstance(corrupt, int)
                or corrupt < 0
                or not isinstance(out_of_window, int)
                or out_of_window < 0
                or not isinstance(line_number, int)
                or line_number < 0
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
        self._out_of_window_total = out_of_window
        self._line_number = line_number
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
            "out_of_window_total": self._out_of_window_total,
            "recent_event_keys": list(self._seen),
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
        if state != "running" or not all(item["running"] for item in processes):
            raise OSError(f"observation writer is not running (state={state})")
        return (
            {
                "configured": True,
                "state": "running",
                "updated_at": document["updated_at"],
                "started_at": document["started_at"],
                "age_s": max(0.0, round(age_s, 3)),
                "processes": processes,
            },
            started_at,
        )

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
        }

    def collect(self) -> tuple[dict[str, Any], str]:
        heartbeat, writer_started_at = self._read_heartbeat()
        mode = "append"
        with self.path.open("rb") as source:
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
            if mode != "append":
                self._write_checkpoint()
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
        corrupt_count = 0
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
            accepted, line_rejected = transform_lines(
                [line],
                kind=self.kind,
                data_origin=self.data_origin,
                allowed_engine_binaries=self.allowed_engine_binaries,
                environment=self.environment,
                run_id=self.run_id,
            )
            if line_rejected:
                rejected.append(
                    {"line": line_number, "reason": line_rejected[0]["reason"]}
                )
                corrupt_count += 1
                continue
            item = accepted[0]
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
            item["event_key"] = event_key
            self.store.append(item)
            self._remember(event_key)
            accepted_count += 1

        self._offset += complete_length
        self._line_number += len(raw_lines)
        self._corrupt_lines_total += corrupt_count
        self._out_of_window_total += out_of_window_count
        checkpoint_error = self._checkpoint_error
        self._write_checkpoint()
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
