#!/usr/bin/env python3
"""Append live Hubble and Tetragon JSONL (Tetragon without process arguments)."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import BinaryIO, Callable, Sequence
from uuid import uuid4

from serving_runtime.mvp.config import (
    DEFAULT_COLLECTOR_INTERVAL_S,
    DEFAULT_COLLECTOR_STALE_AFTER_S,
)
from serving_runtime.mvp.kube_context import (
    DEFAULT_KUBE_CONTEXT,
    validate_kube_context,
)
from serving_runtime.mvp.observation_feed import HEARTBEAT_SCHEMA
from serving_runtime.mvp.target import (
    POD_SNAPSHOT_SCHEMA,
    KubernetesSelector,
    parse_label_selector,
    pods_command,
    validate_kubernetes_name,
)
from serving_runtime.enforcement.phase_signals import (
    _KUBECTL_TIME, _REQUEST_LINE, _TimelineState, ENGINE_PATTERN_SETS,
)


EXPECTED_CONTEXT = DEFAULT_KUBE_CONTEXT
LAB_NAMESPACE = "serving-lab"
PHASE_LOG_LIMIT_BYTES = 8 * 1024 * 1024


class _StopRequested(Exception):
    """Leave the follow loop through its normal child-process cleanup path."""


def _install_stop_signal_handlers(
    stop_requested: threading.Event,
) -> dict[int, object]:
    previous: dict[int, object] = {}

    def request_stop(_signum: int, _frame: object) -> None:
        stop_requested.set()

    for signum in (signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.signal(signum, request_stop)
    return previous


def _restore_signal_handlers(previous: dict[int, object]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def hubble_command(
    *, since: str | None = None, include_forwarded: bool = False
) -> list[str]:
    command = [
        "hubble",
        "observe",
        "--follow",
        "-o",
        "jsonpb",
        "--server",
        "127.0.0.1:4245",
        "--namespace",
        LAB_NAMESPACE,
    ]
    if since is not None:
        command.extend(["--since", since])
    if not include_forwarded:
        command.extend(["--verdict", "DROPPED"])
    return command


def hubble_port_forward_command(
    context: str = EXPECTED_CONTEXT,
) -> list[str]:
    return [
        "cilium",
        "hubble",
        "port-forward",
        "--context",
        validate_kube_context(context),
    ]


def tetragon_command(
    *, context: str = EXPECTED_CONTEXT, since_time: str | None = None
) -> list[str]:
    # A fresh start skips old lines; a restart with an open gap asks for older
    # lines so events during the gap can be recovered (duplicates are dropped
    # by the Gateway's event key).
    window = ["--tail=0"] if since_time is None else [f"--since-time={since_time}"]
    return [
        "kubectl",
        "--context",
        validate_kube_context(context),
        "--namespace",
        "kube-system",
        "logs",
        "--follow",
        *window,
        "daemonset/tetragon",
        "--container",
        "export-stdout",
    ]


def assert_safe_context(
    requested: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    # Every cluster command carries this validated context explicitly. The
    # kubeconfig current-context is intentionally irrelevant.
    validate_kube_context(requested)


def _start(
    argv: Sequence[str], output: BinaryIO | int, error: BinaryIO
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=error,
        shell=False,
    )


# Plan §6.5: process argument text is excluded from default collection. The
# raw Tetragon file is persisted, so arguments are removed before it is written.
_TETRAGON_PROCESS_KEYS = ("process", "parent")


def redact_tetragon_line(line: bytes) -> bytes | None:
    """Return the event line without process arguments, or None to drop it.

    Lines that are not a JSON object are dropped instead of being stored
    verbatim, because their content cannot be checked for arguments.
    """

    try:
        document = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(document, dict):
        return None
    for event in document.values():
        if not isinstance(event, dict):
            continue
        for key in _TETRAGON_PROCESS_KEYS:
            process = event.get(key)
            if isinstance(process, dict):
                for private in ("arguments","environment_variables","environment","env","envs"):
                    process.pop(private, None)
        ancestors = event.get("ancestors")
        if isinstance(ancestors, list):
            for ancestor in ancestors:
                if isinstance(ancestor, dict):
                    for private in ("arguments","environment_variables","environment","env","envs"):
                        ancestor.pop(private, None)
    return (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _pump(
    source: BinaryIO,
    output: BinaryIO,
    transform: Callable[[bytes], bytes | None],
    dropped: list[int],
    error_class: list[str | None],
) -> None:
    try:
        for line in iter(source.readline, b""):
            cleaned = transform(line)
            if cleaned is None:
                dropped[0] += 1
                continue
            output.write(cleaned)
            output.flush()
    except Exception as error:
        error_class[0] = type(error).__name__
    finally:
        try:
            source.close()
        except Exception as error:
            if error_class[0] is None:
                error_class[0] = type(error).__name__


def _wait_for_hubble(
    process: subprocess.Popen[bytes],
    timeout_s: float = 15,
    stop_requested: Callable[[], bool] = lambda: False,
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if stop_requested():
            raise _StopRequested
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(
                f"cilium hubble port-forward exited with status {returncode}"
            )
        try:
            with socket.create_connection(("127.0.0.1", 4245), timeout=0.25):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("cilium hubble port-forward did not become ready")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Optional read-only sharing with one group (for example the Gateway Pod's
# supplemental group). Files stay owner-writable only; the group may read.
_reader_gid: int | None = None


def _share(path: Path, *, directory: bool = False) -> None:
    if _reader_gid is None:
        return
    os.chown(path, -1, _reader_gid, follow_symlinks=False)
    os.chmod(path, 0o750 if directory else 0o640)


def _share_fd(descriptor: int) -> None:
    """Share a file through its open descriptor (no path lookup race)."""

    if _reader_gid is None:
        return
    os.fchown(descriptor, -1, _reader_gid)
    os.fchmod(descriptor, 0o640)


def _open_append(path: Path) -> BinaryIO:
    """Open one raw feed/log append target without following a symlink."""

    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
            0o600,
        )
    except OSError as error:
        if path.is_symlink():
            raise OSError(f"refusing symbolic link append target: {path}") from error
        raise
    try:
        os.fchmod(descriptor, 0o600)
        _share_fd(descriptor)
        return os.fdopen(descriptor, "ab")
    except Exception:
        os.close(descriptor)
        raise


class ReaderGroupError(ValueError):
    """The collector process is not a member of the requested reader group."""


def check_reader_group(gid: int | None) -> None:
    # A non-root process can only chgrp to a group it belongs to. After
    # `usermod -aG`, an already running systemd user manager keeps the old
    # group list, so fail clearly instead of crash-looping on EPERM.
    if gid is None:
        return
    if gid != os.getegid() and gid not in os.getgroups():
        raise ReaderGroupError(
            f"이 프로세스는 그룹 {gid}에 속하지 않아 --reader-group으로 공유할 수 없습니다. "
            "그룹 추가 뒤 모든 세션을 끝내거나 사용자 매니저를 다시 시작하고 `id -G`로 확인하세요"
        )


def _write_json_atomic(path: Path, document: dict[str, object]) -> None:
    encoded = (json.dumps(document, ensure_ascii=False) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            _share_fd(output.fileno())
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


GAP_SCHEMA = "runtime-observation-feed-gap/1.0"
BACKOFF_INITIAL_S = 1.0
BACKOFF_MAX_S = 30.0
BACKFILL_MARGIN = timedelta(seconds=1)
# With the Gateway defaults, the worst observed age is bounded by writer
# interval + kubectl timeout + Gateway poll interval. Keep that sum strictly
# below the Gateway stale window: 5 + 4 + 5 = 14 < 15 seconds.
DEFAULT_TARGET_INTERVAL_S = 5.0
MAX_TARGET_INTERVAL_S = 5.0
MAX_TARGET_QUERY_TIMEOUT_S = 5.0
TARGET_QUERY_TIMEOUT_S = 4.0


def _validate_target_timing(interval_s: float, timeout_s: float) -> None:
    if not math.isfinite(interval_s) or interval_s <= 0:
        raise ValueError("target interval must be a positive finite number")
    if interval_s > MAX_TARGET_INTERVAL_S:
        raise ValueError(
            f"target interval must be at most {MAX_TARGET_INTERVAL_S:g} seconds"
        )
    if (
        not math.isfinite(timeout_s)
        or timeout_s <= 0
        or timeout_s > MAX_TARGET_QUERY_TIMEOUT_S
    ):
        raise ValueError(
            "target query timeout must be a positive finite number no greater "
            f"than {MAX_TARGET_QUERY_TIMEOUT_S:g} seconds"
        )
    worst_case_age_s = interval_s + timeout_s + DEFAULT_COLLECTOR_INTERVAL_S
    if worst_case_age_s >= DEFAULT_COLLECTOR_STALE_AFTER_S:
        raise ValueError(
            "target interval + query timeout + Gateway poll interval must be "
            "below the Gateway stale window"
        )


def _target_interval_argument(value: str) -> float:
    try:
        interval_s = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    try:
        _validate_target_timing(interval_s, TARGET_QUERY_TIMEOUT_S)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    return interval_s


def _rfc3339_seconds(value: datetime) -> str:
    """Whole-second UTC timestamp accepted by both kubectl and hubble."""

    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_heartbeats(
    specifications: dict[
        str,
        tuple[Path, Path, str, list[tuple[str, subprocess.Popen[bytes]]]],
    ],
    *,
    state: str,
    extra: dict[str, dict[str, object]] | None = None,
) -> None:
    updated_at = _utc_now()
    for kind, (heartbeat_path, feed_path, started_at, processes) in specifications.items():
        process_status = []
        for name, process in processes:
            returncode = process.poll()
            process_status.append(
                {
                    "name": name,
                    "running": returncode is None,
                    "returncode": returncode,
                }
            )
        actual_state = state
        if state == "running" and not all(
            item["running"] for item in process_status
        ):
            actual_state = "error"
        document: dict[str, object] = {
            "schema": HEARTBEAT_SCHEMA,
            "kind": kind,
            "feed_path": str(feed_path),
            "started_at": started_at,
            "updated_at": updated_at,
            "state": actual_state,
            "processes": process_status,
        }
        document.update((extra or {}).get(kind, {}))
        _write_json_atomic(heartbeat_path, document)


class _Child:
    """One follow process. ``argv`` receives the backfill start, if any."""

    def __init__(
        self,
        name: str,
        argv: Callable[[str | None], list[str]],
        output: BinaryIO,
        error: BinaryIO,
        ready: Callable[[subprocess.Popen[bytes]], None] | None = None,
        transform: Callable[[bytes], bytes | None] | None = None,
    ) -> None:
        self.name = name
        self.argv = argv
        self.output = output
        self.error = error
        self.ready = ready
        # When set, stdout goes through this filter before reaching ``output``.
        self.transform = transform
        self.process: subprocess.Popen[bytes] | None = None
        self.pump: threading.Thread | None = None
        self.dropped_lines = [0]
        self.pump_error: list[str | None] = [None]

    def returncode(self) -> int | None:
        return None if self.process is None else self.process.poll()


class _FeedGroup:
    """Children that feed one JSONL file, restarted together after a gap."""

    def __init__(
        self,
        kind: str,
        *,
        feed_path: Path,
        heartbeat_path: Path,
        gap_path: Path,
        children: list[_Child],
        health_check: Callable[[], list[dict[str, object]]] | None = None,
        supports_backfill: bool = True,
    ) -> None:
        self.kind = kind
        self.feed_path = feed_path
        self.heartbeat_path = heartbeat_path
        self.gap_path = gap_path
        self.children = children
        self.health_check = health_check
        self.supports_backfill = supports_backfill
        # The writer start time stays fixed so backfilled events remain in window.
        self.started_at = _utc_now()
        self.state = "starting"
        self.open_gap: dict[str, object] | None = None
        self.last_gap: dict[str, object] | None = None
        self.reconnect_count = 0
        self.backoff_s = BACKOFF_INITIAL_S
        self.next_attempt = 0.0
        # Last moment every child was seen running; a gap's backfill starts
        # before it because an exit is only noticed on the next tick.
        self.last_healthy_at: datetime | None = None
        self.last_redaction_error_class: str | None = None

    def processes(self) -> list[tuple[str, object]]:
        return [
            (child.name, child.process if child.process is not None else _NotStarted())
            for child in self.children
        ]

    def heartbeat_extra(self) -> dict[str, object]:
        extra: dict[str, object] = {
            "reconnect_count": self.reconnect_count,
            "open_gap": self.open_gap,
            "last_gap": self.last_gap,
        }
        redacting = [child for child in self.children if child.transform is not None]
        if redacting:
            extra["dropped_lines_total"] = sum(
                child.dropped_lines[0] for child in redacting
            )
            extra["last_redaction_error_class"] = (
                self.last_redaction_error_class
            )
        return extra


class _NotStarted:
    @staticmethod
    def poll() -> int:
        return -1


def _append_gap(path: Path, record: dict[str, object]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "ab") as output:
        _share_fd(output.fileno())
        output.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
        output.flush()
        os.fsync(output.fileno())


def _unclosed_gaps(path: Path, kind: str) -> list[dict[str, object]] | None:
    """Read prior gaps; an unreadable or damaged journal cannot be reconciled."""
    def valid_time(value: object) -> bool:
        if not isinstance(value, str):
            return False
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
        except ValueError:
            return False

    try:
        pending: dict[str, dict[str, object]] = {}
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                item = json.loads(line)
                if not isinstance(item, dict) or item.get("schema") != GAP_SCHEMA or item.get("kind") != kind:
                    return None
                gap_id = item.get("gap_id")
                if not isinstance(gap_id, str) or not gap_id:
                    return None
                if item.get("event") == "gap_opened":
                    if gap_id in pending or not valid_time(item.get("opened_at")):
                        return None
                    pending[gap_id] = item
                elif item.get("event") == "gap_closed":
                    if gap_id not in pending or not valid_time(item.get("closed_at")):
                        return None
                    opened_at = datetime.fromisoformat(str(pending[gap_id]["opened_at"]).replace("Z", "+00:00"))
                    closed_at = datetime.fromisoformat(str(item["closed_at"]).replace("Z", "+00:00"))
                    if closed_at < opened_at:
                        return None
                    del pending[gap_id]
                else:
                    return None
        return list(pending.values())
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return None


def _strings(document: object) -> dict[str, str]:
    if not isinstance(document, dict):
        return {}
    return {
        key: value
        for key, value in document.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _reduce_pod(item: object) -> dict[str, object] | None:
    """Keep only the non-secret Pod fields used by ``summarize_pods``."""

    if not isinstance(item, dict):
        return None
    source_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    metadata: dict[str, object] = {}
    for name in ("name", "uid", "deletionTimestamp"):
        value = source_metadata.get(name)
        if isinstance(value, str):
            metadata[name] = value
    labels = _strings(source_metadata.get("labels"))
    if labels:
        metadata["labels"] = labels

    source_spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
    spec: dict[str, object] = {}
    if isinstance(source_spec.get("nodeName"), str):
        spec["nodeName"] = source_spec["nodeName"]

    source_status = item.get("status") if isinstance(item.get("status"), dict) else {}
    status: dict[str, object] = {}
    for name in ("phase", "startTime"):
        value = source_status.get(name)
        if isinstance(value, str):
            status[name] = value
    conditions: list[dict[str, str]] = []
    raw_conditions = source_status.get("conditions")
    for condition in raw_conditions if isinstance(raw_conditions, list) else []:
        reduced = {
            name: condition[name]
            for name in ("type", "status")
            if isinstance(condition, dict) and isinstance(condition.get(name), str)
        }
        if reduced:
            conditions.append(reduced)
    if conditions:
        status["conditions"] = conditions
    container_statuses: list[dict[str, object]] = []
    raw_container_statuses = source_status.get("containerStatuses")
    for container in raw_container_statuses if isinstance(raw_container_statuses, list) else []:
        if not isinstance(container, dict):
            continue
        reduced_container: dict[str, object] = {}
        for name in ("name", "containerID", "imageID", "image"):
            value = container.get(name)
            if isinstance(value, str):
                reduced_container[name] = value
        if isinstance(container.get("ready"), bool):
            reduced_container["ready"] = container["ready"]
        restart_count = container.get("restartCount")
        if isinstance(restart_count, int) and not isinstance(restart_count, bool):
            reduced_container["restartCount"] = restart_count
        source_state = container.get("state")
        if isinstance(source_state, dict):
            state = {
                name: {}
                for name in ("running", "waiting", "terminated")
                if name in source_state
            }
            if state:
                reduced_container["state"] = state
        container_statuses.append(reduced_container)
    status["containerStatuses"] = container_statuses
    return {"metadata": metadata, "spec": spec, "status": status}


class TargetPodSnapshotWriter:
    """Periodically materialize a minimal Pod list for a credential-free Gateway."""

    def __init__(
        self,
        path: Path,
        selector: KubernetesSelector,
        *,
        interval_s: float,
        timeout_s: float = TARGET_QUERY_TIMEOUT_S,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        _validate_target_timing(interval_s, timeout_s)
        self.path = path
        self.selector = selector
        self.interval_s = float(interval_s)
        self.timeout_s = float(timeout_s)
        self.runner = runner

    def _base(self) -> dict[str, object]:
        return {
            "schema": POD_SNAPSHOT_SCHEMA,
            "context": self.selector.context,
            "namespace": self.selector.namespace,
            "selector": dict(self.selector.selector),
        }

    def collect_once(self) -> dict[str, object]:
        document = self._base()
        try:
            result = self.runner(
                pods_command(self.selector),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
                shell=False,
            )
            if result.returncode != 0:
                error = {"class": "KubectlExit", "code": "kubectl_exit"}
            else:
                try:
                    raw = json.loads(result.stdout)
                except (json.JSONDecodeError, RecursionError):
                    error = {"class": "JSONDecodeError", "code": "invalid_json"}
                else:
                    items = raw.get("items") if isinstance(raw, dict) else None
                    if not isinstance(items, list):
                        error = {"class": "ValueError", "code": "invalid_pod_list"}
                    else:
                        document.update(
                            {
                                "collected_at": _utc_now(),
                                "state": "ok",
                                "error": None,
                                "items": [
                                    reduced
                                    for item in items
                                    if (reduced := _reduce_pod(item)) is not None
                                ],
                            }
                        )
                        _write_json_atomic(self.path, document)
                        return document
        except subprocess.TimeoutExpired:
            error = {"class": "TimeoutExpired", "code": "kubectl_timeout"}
        except (OSError, subprocess.SubprocessError) as failure:
            error = {"class": type(failure).__name__, "code": "kubectl_error"}
        document.update(
            {"collected_at": _utc_now(), "state": "error", "error": error}
        )
        _write_json_atomic(self.path, document)
        return document

    def follow(self, stop_requested: threading.Event) -> None:
        while not stop_requested.is_set():
            try:
                self.collect_once()
            except OSError:
                # Feed supervision remains independent even if this optional
                # snapshot cannot be written. The next interval retries it.
                pass
            stop_requested.wait(self.interval_s)


class EnginePhaseTimelineWriter:
    """Keep one Pod's extraction state and fetch only its new timestamped logs."""

    def __init__(self, path: Path, selector: KubernetesSelector, *, engine: str = "vllm",
                 interval_s: float = 2.0, timeout_s: float = 4.0,
                 runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run) -> None:
        self.path, self.selector = path, selector
        self.engine = engine
        self.patterns = ENGINE_PATTERN_SETS[engine].patterns
        self.loading_start = next(item.expression for item in self.patterns if item.signal == "loading_start")
        self.interval_s, self.timeout_s, self.runner = interval_s, timeout_s, runner
        self._uid: str | None = None
        self._phase: _TimelineState | None = None
        self._cursor: datetime | None = None
        self._boundary: set[bytes] = set()
        self._second_seen: set[bytes] = set()
        self._skip_boundary = False
        self._incomplete = False
        self._order_unknown = False
        self._skipped: Counter[str] = Counter()

    def _reset(self, uid: str | None, year: int) -> None:
        self._uid = uid
        self._phase = _TimelineState(engine=self.engine, year=year, timezone_offset="Z") if uid else None
        self._cursor = None
        self._boundary.clear()
        self._second_seen.clear()
        self._skip_boundary = False
        self._incomplete = False
        self._order_unknown = False
        self._skipped.clear()

    def _ingest(self, data: bytes, *, limited: bool, year: int) -> None:
        assert self._phase is not None
        # kubectl may cut the last line at any byte, including inside UTF-8 or
        # its RFC3339 prefix. It is fetched again from the boundary timestamp.
        if data and not data.endswith(b"\n"):
            self._skipped["partial_tail"] += 1
            data = data[:data.rfind(b"\n") + 1]
        if limited:
            self._incomplete = True
        previous = self._cursor
        progressed = False
        for raw in data.splitlines(keepends=True):
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                self._skipped["invalid_utf8"] += 1
                continue
            match = _KUBECTL_TIME.match(line)
            if match is None:
                self._skipped["missing_timestamp"] += 1
                continue
            try:
                at = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
            except ValueError:
                self._skipped["invalid_timestamp"] += 1
                continue
            key = hashlib.sha256(raw).digest()
            if (self._cursor is not None
                    and at.replace(microsecond=0) == self._cursor.replace(microsecond=0)
                    and key in self._second_seen):
                continue
            if previous is not None and at < previous:
                if at.replace(microsecond=0) == self._cursor.replace(microsecond=0):
                    self._second_seen.add(key)
                self._skipped["out_of_order"] += 1
                for phase in tuple(self._phase.open_phases):
                    self._phase.close_phase(phase, previous, max(1, self._phase.lines_read))
                self._phase.finish_run(max(1, self._phase.lines_read))
                self._order_unknown = True
                continue
            if self._cursor is None or at.replace(microsecond=0) != self._cursor.replace(microsecond=0):
                self._second_seen.clear()
            self._second_seen.add(key)
            if at == self._cursor:
                if key in self._boundary:
                    continue
                self._boundary.add(key)
            else:
                self._cursor = at
                self._boundary = {key}
            previous = at
            progressed = True
            if self._order_unknown:
                start_signal = any(pattern.signal in {"loading_start", "compiling_start", "inferring_start"}
                                   and pattern.expression.search(line) for pattern in self.patterns)
                if _REQUEST_LINE.search(line) or not start_signal:
                    continue
                self._order_unknown = False
            # A capped replay cannot establish the current run's opening
            # boundary. Wait for a new, directly observed loading start.
            if self._incomplete:
                if limited or _REQUEST_LINE.search(line) or not self.loading_start.search(line):
                    continue
                self._phase = _TimelineState(engine=self.engine, year=year, timezone_offset="Z")
                self._incomplete = False
            try:
                self._phase.process_line(raw)
            except ValueError:
                self._skipped["invalid_engine_timestamp"] += 1
        # Re-read a capped page's last timestamp once. If the same capped
        # page yields no new complete lines, advance to avoid a retry loop.
        if progressed:
            self._skip_boundary = False
        elif limited:
            self._skip_boundary = True

    def _snapshot(self) -> tuple[list[dict[str, object]], dict[str, object]]:
        assert self._phase is not None
        timeline = self._phase.snapshot()
        extraction = timeline["extraction"]
        extraction["skipped_lines"] = dict(self._skipped)
        extraction["history_incomplete"] = self._incomplete
        return ([] if self._incomplete else timeline["entries"]), extraction

    def collect_once(self) -> dict[str, object]:
        now = datetime.now(timezone.utc)
        state, uid, name, entries, extraction = "pod_error", None, None, [], None
        last_log_at = None
        try:
            pods = self.runner(pods_command(self.selector), stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=self.timeout_s, check=False, shell=False)
            if pods.returncode == 0:
                raw = json.loads(pods.stdout)
                items = raw.get("items") if isinstance(raw, dict) else None
                if not isinstance(items, list):
                    raise ValueError("invalid Pod list")
                current = [item for item in items if isinstance(item, dict)
                           and isinstance(item.get("status"), dict)
                           and item["status"].get("phase") == "Running"
                           and isinstance(item.get("metadata"), dict)
                           and not item["metadata"].get("deletionTimestamp")]
                state = "no_target" if not current else "multiple_targets" if len(current) > 1 else "running"
                if state == "running":
                    metadata = current[0].get("metadata", {})
                    uid, name = metadata.get("uid"), metadata.get("name")
                    if not isinstance(uid, str) or not uid or not isinstance(name, str) or not name:
                        state = "pod_error"
                    else:
                        if uid != self._uid:
                            self._reset(uid, now.year)
                        command = ["kubectl", "--context", self.selector.context,
                                   "--namespace", self.selector.namespace, "logs", "pod/" + name,
                                   "-c", self.selector.container, "--timestamps=true",
                                   f"--limit-bytes={PHASE_LOG_LIMIT_BYTES}"]
                        if self._cursor is not None:
                            # Kubernetes serializes this parameter at second
                            # precision. Replay the cursor's whole second;
                            # a stuck capped page must move to the next one.
                            boundary = self._cursor.replace(microsecond=0)
                            if self._skip_boundary:
                                boundary += timedelta(seconds=1)
                            command.append(f"--since-time={boundary.isoformat()}")
                        result = self.runner(command, stdin=subprocess.DEVNULL,
                                             capture_output=True, timeout=self.timeout_s,
                                             check=False, shell=False)
                        if result.returncode != 0:
                            state = "log_error"
                        else:
                            confirmed = self.runner(pods_command(self.selector), stdin=subprocess.DEVNULL,
                                                    capture_output=True, timeout=self.timeout_s,
                                                    check=False, shell=False)
                            if confirmed.returncode != 0:
                                raise ValueError("Pod identity recheck failed")
                            confirmed_doc = json.loads(confirmed.stdout)
                            confirmed_items = confirmed_doc.get("items") if isinstance(confirmed_doc, dict) else None
                            current_after = [item for item in confirmed_items if isinstance(item, dict)
                                             and isinstance(item.get("metadata"), dict)
                                             and isinstance(item.get("status"), dict)
                                             and item["status"].get("phase") == "Running"
                                             and not item["metadata"].get("deletionTimestamp")] if isinstance(confirmed_items, list) else []
                            if len(current_after) != 1 or current_after[0]["metadata"].get("uid") != uid:
                                self._reset(None, now.year)
                                raise ValueError("Pod identity changed during log fetch")
                            self._ingest(result.stdout, limited=len(result.stdout) >= PHASE_LOG_LIMIT_BYTES,
                                         year=now.year)
                            last_log_at = self._cursor.isoformat() if self._cursor else None
                            entries, extraction = self._snapshot()
                            if self._incomplete:
                                state = "incomplete_history"
                else:
                    self._reset(None, now.year)
            # A failed Pod query leaves state pod_error and no entries.
        except (OSError, subprocess.SubprocessError, ValueError, UnicodeError,
                json.JSONDecodeError):
            state = "log_error" if uid else "pod_error"
        written = datetime.now(timezone.utc)
        lag = (written - datetime.fromisoformat(last_log_at)).total_seconds() if last_log_at else None
        document: dict[str, object] = {
            "schema_version": "runtime-phase-timeline/1.0", "entries": entries,
            "collector": {"state": state, "pod_uid": uid, "pod_name": name,
                          "written_at": written.isoformat(),
                          "last_log_at": last_log_at, "log_delivery_lag_seconds": lag},
        }
        if extraction is not None:
            document["extraction"] = extraction
        _write_json_atomic(self.path, document)
        return document

    def follow(self, stop_requested: threading.Event) -> None:
        while not stop_requested.is_set():
            try:
                self.collect_once()
            except OSError:
                # Optional phase extraction never stops the other feeds.
                pass
            stop_requested.wait(self.interval_s)


def _stop_children(group: _FeedGroup) -> None:
    for child in group.children:
        if child.process is not None and child.process.poll() is None:
            child.process.terminate()
    for child in group.children:
        if child.process is None:
            continue
        try:
            child.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.process.kill()
            child.process.wait(timeout=5)
        if child.pump is not None:
            child.pump.join(timeout=5)


class _Supervisor:
    def __init__(
        self,
        groups: list[_FeedGroup],
        *,
        context: str,
        stop_requested: threading.Event,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.groups = groups
        self.context = context
        self.stop_requested = stop_requested
        self.clock = clock
        self.prior_gaps = {group.kind: _unclosed_gaps(group.gap_path, group.kind) for group in groups}

    def _prior_backfill_since(self, group: _FeedGroup, since: str | None) -> str | None:
        pending = self.prior_gaps[group.kind]
        if not pending:
            return since
        candidates = []
        if since is not None:
            candidates.append(datetime.fromisoformat(since.replace("Z", "+00:00")))
        for opened in pending:
            opened_at = datetime.fromisoformat(str(opened["opened_at"]).replace("Z", "+00:00"))
            backfill = opened.get("backfill_since")
            try:
                candidate = datetime.fromisoformat(backfill.replace("Z", "+00:00")) if isinstance(backfill, str) else opened_at - BACKFILL_MARGIN
                if candidate.tzinfo is None:
                    candidate = opened_at - BACKFILL_MARGIN
            except ValueError:
                candidate = opened_at - BACKFILL_MARGIN
            candidates.append(min(candidate, opened_at - BACKFILL_MARGIN))
        return _rfc3339_seconds(min(candidates))

    def _close_prior_gaps(self, group: _FeedGroup) -> None:
        pending = self.prior_gaps[group.kind]
        if pending is None or not pending:
            return
        assert group.last_healthy_at is not None
        for opened in pending:
            closed = dict(opened)
            closed.update({
                "event": "gap_closed",
                "closed_at": group.last_healthy_at.isoformat(),
                "closed_by": "collector_restart",
                "backfill": "requested_unverified" if group.supports_backfill else "not_available",
            })
            _append_gap(group.gap_path, closed)
            group.last_gap = {key: value for key, value in closed.items() if key not in ("schema", "event", "kind")}
            group.reconnect_count += 1
        self.prior_gaps[group.kind] = []

    def _open_gap(self, group: _FeedGroup, cause: list[dict[str, object]]) -> None:
        detected = datetime.now(timezone.utc)
        healthy = group.last_healthy_at
        # Start the backfill a margin before the last healthy check (or the
        # writer start), not at detection time, so nothing in between is lost.
        since = (healthy or datetime.fromisoformat(group.started_at)) - BACKFILL_MARGIN
        group.open_gap = {
            "gap_id": uuid4().hex,
            "opened_at": detected.isoformat(),
            "last_healthy_at": healthy.isoformat() if healthy else None,
            "backfill_since": _rfc3339_seconds(since),
            "cause": cause,
            "attempts": 0,
            "last_error": None,
        }
        group.state = "reconnecting"
        group.next_attempt = self.clock() + group.backoff_s
        _append_gap(group.gap_path, {"schema": GAP_SCHEMA, "event": "gap_opened", "kind": group.kind, **group.open_gap})

    def _close_gap(self, group: _FeedGroup) -> None:
        assert group.open_gap is not None
        closed = dict(group.open_gap)
        closed["closed_at"] = _utc_now()
        # Backfill was requested; whether every event came back is not knowable
        # (logs may have rotated or the pod may have restarted).
        closed["backfill"] = "requested_unverified" if group.supports_backfill else "not_available"
        _append_gap(group.gap_path, {"schema": GAP_SCHEMA, "event": "gap_closed", "kind": group.kind, **closed})
        group.last_gap = closed
        group.open_gap = None
        group.reconnect_count += 1

    def start_group(self, group: _FeedGroup, *, since: str | None) -> None:
        since = self._prior_backfill_since(group, since)
        started: list[_Child] = []
        try:
            if since is not None:
                # Re-check the context before talking to a cluster again.
                assert_safe_context(self.context)
            for child in group.children:
                if child.transform is None:
                    child.process = _start(child.argv(since), child.output, child.error)
                else:
                    child.process = _start(child.argv(since), subprocess.PIPE, child.error)
                    assert child.process.stdout is not None
                    child.pump_error[0] = None
                    child.pump = threading.Thread(
                        target=_pump,
                        args=(
                            child.process.stdout,
                            child.output,
                            child.transform,
                            child.dropped_lines,
                            child.pump_error,
                        ),
                        name=f"{child.name}-redact",
                        daemon=True,
                    )
                    child.pump.start()
                started.append(child)
                if self.stop_requested.is_set():
                    raise _StopRequested
                if child.ready is not None:
                    child.ready(child.process)
            exited = [
                {"name": child.name, "returncode": child.returncode()}
                for child in group.children
                if child.returncode() is not None
            ]
            if exited:
                raise RuntimeError(f"child exited during start: {exited}")
        except _StopRequested:
            _stop_children(group)
            raise
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
            _stop_children(group)
            raise RuntimeError(f"{group.kind} start failed: {error}") from error
        # A prior gap remains unavailable until the first healthy check.
        group.state = "verifying" if self.prior_gaps[group.kind] else "running"
        group.backoff_s = BACKOFF_INITIAL_S
        group.last_healthy_at = datetime.now(timezone.utc)

    def tick(self) -> None:
        for group in self.groups:
            if self.stop_requested.is_set():
                raise _StopRequested
            if group.state in ("running", "verifying"):
                exited = [
                    {"name": child.name, "returncode": child.returncode()}
                    for child in group.children
                    if child.returncode() is not None
                ]
                dead_pumps = []
                for child in group.children:
                    if (
                        child.transform is not None
                        and child.pump is not None
                        and not child.pump.is_alive()
                        and child.returncode() is None
                    ):
                        error_class = child.pump_error[0]
                        if error_class is not None:
                            group.last_redaction_error_class = error_class
                        dead_pumps.append(
                            {
                                "name": child.name,
                                "pump_error": error_class or "PumpStopped",
                            }
                        )
                failures = exited + dead_pumps
                if group.health_check is not None:
                    try:
                        failures += group.health_check()
                    except Exception:
                        failures.append({"name":"source_health","error":"HealthCheckFailed"})
                if failures:
                    _stop_children(group)
                    self._open_gap(group, failures)
                else:
                    group.last_healthy_at = datetime.now(timezone.utc)
                    self._close_prior_gaps(group)
                    group.state = "running"
                continue
            if group.state == "reconnecting" and self.clock() >= group.next_attempt:
                assert group.open_gap is not None
                group.open_gap["attempts"] = int(group.open_gap["attempts"]) + 1
                try:
                    self.start_group(group, since=str(group.open_gap["backfill_since"]))
                except RuntimeError as error:
                    group.open_gap["last_error"] = str(error)
                    group.backoff_s = min(group.backoff_s * 2, BACKOFF_MAX_S)
                    group.next_attempt = self.clock() + group.backoff_s
                    continue
                self._close_gap(group)

    def write(self, *, final_state: str | None = None) -> None:
        for group in self.groups:
            state = final_state or ("running" if group.state == "running" else group.state)
            write_heartbeats(
                {group.kind: (group.heartbeat_path, group.feed_path, group.started_at, group.processes())},
                state=state,
                extra={group.kind: {**group.heartbeat_extra(), "kube_context": self.context}},
            )


def run(
    output_dir: Path,
    context: str,
    *,
    include_forwarded: bool = False,
    max_ticks: int | None = None,
    reader_gid: int | None = None,
    target_selector: tuple[tuple[str, str], ...] | None = None,
    target_namespace: str = LAB_NAMESPACE,
    target_interval_s: float = DEFAULT_TARGET_INTERVAL_S,
    phase_timeline: bool = False,
    phase_engine: str = "vllm",
) -> int:
    global _reader_gid
    if phase_timeline and target_selector is None:
        raise ValueError("--phase-timeline requires --target-selector")
    assert_safe_context(context)
    check_reader_group(reader_gid)
    _reader_gid = reader_gid
    output_dir = output_dir.absolute()
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _share(output_dir, directory=True)
    target_writer: TargetPodSnapshotWriter | None = None
    phase_writer: EnginePhaseTimelineWriter | None = None
    if target_selector is not None:
        validate_kubernetes_name(target_namespace, "target namespace")
        target_writer = TargetPodSnapshotWriter(
            output_dir / "target-pods.json",
            KubernetesSelector(context, target_namespace, target_selector, ""),
            interval_s=target_interval_s,
        )
        if phase_timeline:
            phase_writer = EnginePhaseTimelineWriter(
                output_dir / "phase-timeline.json",
                KubernetesSelector(context, target_namespace, target_selector, "engine"),
                engine=phase_engine,
            )
    hubble_path = output_dir / "hubble.jsonl"
    tetragon_path = output_dir / "tetragon.jsonl"
    stop_requested = threading.Event()
    final_state = "error"
    with ExitStack() as stack:
        hubble_output = stack.enter_context(_open_append(hubble_path))
        tetragon_output = stack.enter_context(_open_append(tetragon_path))
        hubble_error = stack.enter_context(
            _open_append(output_dir / "hubble.stderr.log")
        )
        tetragon_error = stack.enter_context(
            _open_append(output_dir / "tetragon.stderr.log")
        )
        port_forward_log = stack.enter_context(
            _open_append(output_dir / "cilium-hubble-port-forward.log")
        )
        hubble_group = _FeedGroup(
            "hubble",
            feed_path=hubble_path,
            heartbeat_path=output_dir / "hubble.heartbeat.json",
            gap_path=output_dir / "hubble.gaps.jsonl",
            children=[
                _Child(
                    "cilium-hubble-port-forward",
                    lambda _since: hubble_port_forward_command(context),
                    port_forward_log,
                    port_forward_log,
                    ready=lambda process: _wait_for_hubble(
                        process, stop_requested=stop_requested.is_set
                    ),
                ),
                _Child(
                    "hubble",
                    lambda since: hubble_command(
                        since=since or hubble_group.started_at,
                        include_forwarded=include_forwarded,
                    ),
                    hubble_output,
                    hubble_error,
                ),
            ],
        )
        tetragon_group = _FeedGroup(
            "tetragon",
            feed_path=tetragon_path,
            heartbeat_path=output_dir / "tetragon.heartbeat.json",
            gap_path=output_dir / "tetragon.gaps.jsonl",
            children=[
                _Child(
                    "tetragon",
                    lambda since: tetragon_command(
                        context=context, since_time=since
                    ),
                    tetragon_output,
                    tetragon_error,
                    transform=redact_tetragon_line,
                )
            ],
        )
        groups = [hubble_group, tetragon_group]
        supervisor = _Supervisor(groups, context=context, stop_requested=stop_requested)
        previous_signal_handlers = _install_stop_signal_handlers(stop_requested)
        target_thread: threading.Thread | None = None
        phase_thread: threading.Thread | None = None
        if target_writer is not None:
            target_thread = threading.Thread(
                target=target_writer.follow,
                args=(stop_requested,),
                name="target-pod-snapshot",
                daemon=True,
            )
            target_thread.start()
        if phase_writer is not None:
            phase_thread = threading.Thread(target=phase_writer.follow,
                                            args=(stop_requested,), name="engine-phase-timeline", daemon=True)
            phase_thread.start()
        try:
            try:
                for group in groups:
                    try:
                        supervisor.start_group(group, since=None)
                    except RuntimeError as error:
                        # A failed first start is a gap from the beginning; keep retrying.
                        supervisor._open_gap(group, [{"name": "initial_start", "returncode": None, "error": str(error)}])
                supervisor.write()
                print(f"Hubble feed: {hubble_path}", flush=True)
                print(f"Tetragon feed: {tetragon_path}", flush=True)
                print(
                    f"Heartbeat: {hubble_group.heartbeat_path}, {tetragon_group.heartbeat_path}",
                    flush=True,
                )
                print(
                    "끊기면 끊긴 구간을 *.gaps.jsonl에 남기고 자동으로 다시 연결합니다. "
                    "Ctrl-C 또는 SIGTERM/SIGHUP으로 follow 프로세스를 함께 종료합니다.",
                    flush=True,
                )
                ticks = 0
                while max_ticks is None or ticks < max_ticks:
                    if stop_requested.is_set():
                        raise _StopRequested
                    supervisor.tick()
                    supervisor.write()
                    ticks += 1
                    stop_requested.wait(1)
                final_state = "stopped"
                return 0
            except (KeyboardInterrupt, _StopRequested):
                final_state = "stopped"
                return 0
        finally:
            stop_requested.set()
            if target_thread is not None:
                target_thread.join(timeout=TARGET_QUERY_TIMEOUT_S + 1)
            if phase_thread is not None:
                phase_thread.join(timeout=2 * TARGET_QUERY_TIMEOUT_S + 1)
            for group in groups:
                _stop_children(group)
            supervisor.write(final_state=final_state)
            _restore_signal_handlers(previous_signal_handlers)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="명시한 Kubernetes context의 Hubble·Tetragon JSONL follow 출력을 저장합니다."
    )
    parser.add_argument(
        "--kube-context",
        "--context",
        dest="kube_context",
        default=EXPECTED_CONTEXT,
        metavar="NAME",
        help="명시적으로 사용할 Kubernetes context (기본: kind-runtime-lab)",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--reader-group",
        type=int,
        metavar="GID",
        help=(
            "feed·heartbeat·gap 파일을 이 그룹에 읽기 전용(0640, 폴더 0750)으로 공유 "
            "(예: Gateway Pod의 supplementalGroups). 생략하면 소유자만 읽음"
        ),
    )
    parser.add_argument(
        "--include-forwarded",
        action="store_true",
        help="DROPPED 기본 필터를 해제해 FORWARDED를 포함한 모든 verdict를 저장",
    )
    parser.add_argument(
        "--target-selector",
        metavar="KEY=VALUE[,KEY=VALUE]",
        help="이 selector의 Pod 최소 스냅샷을 target-pods.json에 주기적으로 기록",
    )
    parser.add_argument("--phase-timeline", action="store_true",
                        help="현재 엔진 Pod 로그에서 단계 시간표를 별도 수집")
    parser.add_argument("--phase-engine", choices=("vllm", "sglang"), default="vllm",
                        help="단계 시간표의 엔진 종류")
    parser.add_argument(
        "--target-namespace",
        default=LAB_NAMESPACE,
        metavar="NAME",
        help="대상 Pod namespace (기본: serving-lab)",
    )
    parser.add_argument(
        "--target-interval",
        type=_target_interval_argument,
        default=DEFAULT_TARGET_INTERVAL_S,
        metavar="SECONDS",
        help=(
            "대상 Pod 조회 간격 (기본: 5, 최대: 5). 조회 제한 4초와 Gateway "
            "poll 5초를 더한 최악 age를 stale 15초보다 작게 유지"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        target_selector = (
            parse_label_selector(args.target_selector)
            if args.target_selector is not None
            else None
        )
        return run(
            args.output_dir.expanduser(),
            args.kube_context,
            include_forwarded=args.include_forwarded,
            reader_gid=args.reader_group,
            target_selector=target_selector,
            target_namespace=args.target_namespace,
            target_interval_s=args.target_interval,
            phase_timeline=args.phase_timeline,
            phase_engine=args.phase_engine,
        )
    except ReaderGroupError as error:
        # Configuration error: exit 2 so a supervisor does not restart in a loop.
        print(f"관측 feed 실행 실패: {error}", file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
        print(f"관측 feed 실행 실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
