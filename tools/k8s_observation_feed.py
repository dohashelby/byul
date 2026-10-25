#!/usr/bin/env python3
"""Append live Hubble and Tetragon JSONL for the isolated runtime kind lab."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
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

from serving_runtime.mvp.observation_feed import HEARTBEAT_SCHEMA


EXPECTED_CONTEXT = "kind-runtime-lab"
LAB_NAMESPACE = "serving-lab"


class _StopRequested(Exception):
    """Leave the follow loop through its normal child-process cleanup path."""


class _ContextMismatchError(RuntimeError):
    """The active kubectl context is not the isolated runtime lab."""


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


def context_command() -> list[str]:
    return ["kubectl", "config", "current-context"]


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


def hubble_port_forward_command() -> list[str]:
    return ["cilium", "hubble", "port-forward"]


def tetragon_command(*, since_time: str | None = None) -> list[str]:
    # First start skips old log lines; a reconnect asks for lines since the gap
    # opened so events during the gap can be recovered (duplicates are dropped
    # by the Gateway's event key).
    window = ["--tail=0"] if since_time is None else [f"--since-time={since_time}"]
    return [
        "kubectl",
        "--context",
        EXPECTED_CONTEXT,
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
    if requested != EXPECTED_CONTEXT:
        raise ValueError(f"--context must be {EXPECTED_CONTEXT}")
    result = runner(
        context_command(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError("kubectl current context query failed")
    actual = result.stdout.strip()
    if actual != EXPECTED_CONTEXT:
        raise _ContextMismatchError(
            f"current kubectl context must be {EXPECTED_CONTEXT}; got {actual or 'empty'}"
        )


def _start(
    argv: Sequence[str], output: BinaryIO, error: BinaryIO
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=error,
        shell=False,
    )


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
    ) -> None:
        self.name = name
        self.argv = argv
        self.output = output
        self.error = error
        self.ready = ready
        self.process: subprocess.Popen[bytes] | None = None

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
    ) -> None:
        self.kind = kind
        self.feed_path = feed_path
        self.heartbeat_path = heartbeat_path
        self.gap_path = gap_path
        self.children = children
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

    def processes(self) -> list[tuple[str, object]]:
        return [
            (child.name, child.process if child.process is not None else _NotStarted())
            for child in self.children
        ]

    def heartbeat_extra(self) -> dict[str, object]:
        return {
            "reconnect_count": self.reconnect_count,
            "open_gap": self.open_gap,
            "last_gap": self.last_gap,
        }


class _NotStarted:
    @staticmethod
    def poll() -> int:
        return -1


def _append_gap(path: Path, record: dict[str, object]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "ab") as output:
        output.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
        output.flush()
        os.fsync(output.fileno())


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
        closed["backfill"] = "requested_unverified"
        _append_gap(group.gap_path, {"schema": GAP_SCHEMA, "event": "gap_closed", "kind": group.kind, **closed})
        group.last_gap = closed
        group.open_gap = None
        group.reconnect_count += 1

    def start_group(self, group: _FeedGroup, *, since: str | None) -> None:
        started: list[_Child] = []
        try:
            if since is not None:
                # Re-check the context before talking to a cluster again.
                assert_safe_context(self.context)
            for child in group.children:
                child.process = _start(child.argv(since), child.output, child.error)
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
        except _ContextMismatchError:
            _stop_children(group)
            raise
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
            _stop_children(group)
            raise RuntimeError(f"{group.kind} start failed: {error}") from error
        group.state = "running"
        group.backoff_s = BACKOFF_INITIAL_S
        group.last_healthy_at = datetime.now(timezone.utc)

    def tick(self) -> None:
        for group in self.groups:
            if self.stop_requested.is_set():
                raise _StopRequested
            if group.state == "running":
                exited = [
                    {"name": child.name, "returncode": child.returncode()}
                    for child in group.children
                    if child.returncode() is not None
                ]
                if exited:
                    _stop_children(group)
                    self._open_gap(group, exited)
                else:
                    group.last_healthy_at = datetime.now(timezone.utc)
                continue
            if group.state == "reconnecting" and self.clock() >= group.next_attempt:
                assert group.open_gap is not None
                group.open_gap["attempts"] = int(group.open_gap["attempts"]) + 1
                try:
                    self.start_group(group, since=str(group.open_gap["backfill_since"]))
                except _ContextMismatchError as error:
                    group.open_gap["last_error"] = str(error)
                    raise
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
                extra={group.kind: group.heartbeat_extra()},
            )


def run(
    output_dir: Path,
    context: str,
    *,
    include_forwarded: bool = False,
    max_ticks: int | None = None,
) -> int:
    assert_safe_context(context)
    output_dir = output_dir.absolute()
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    hubble_path = output_dir / "hubble.jsonl"
    tetragon_path = output_dir / "tetragon.jsonl"
    stop_requested = threading.Event()
    final_state = "error"
    with (
        hubble_path.open("ab") as hubble_output,
        tetragon_path.open("ab") as tetragon_output,
        (output_dir / "hubble.stderr.log").open("ab") as hubble_error,
        (output_dir / "tetragon.stderr.log").open("ab") as tetragon_error,
        (output_dir / "cilium-hubble-port-forward.log").open("ab") as port_forward_log,
    ):
        hubble_group = _FeedGroup(
            "hubble",
            feed_path=hubble_path,
            heartbeat_path=output_dir / "hubble.heartbeat.json",
            gap_path=output_dir / "hubble.gaps.jsonl",
            children=[
                _Child(
                    "cilium-hubble-port-forward",
                    lambda _since: hubble_port_forward_command(),
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
                    lambda since: tetragon_command(since_time=since),
                    tetragon_output,
                    tetragon_error,
                )
            ],
        )
        groups = [hubble_group, tetragon_group]
        supervisor = _Supervisor(groups, context=context, stop_requested=stop_requested)
        previous_signal_handlers = _install_stop_signal_handlers(stop_requested)
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
            for group in groups:
                _stop_children(group)
            supervisor.write(final_state=final_state)
            _restore_signal_handlers(previous_signal_handlers)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="kind-runtime-lab의 Hubble·Tetragon JSONL follow 출력을 저장합니다."
    )
    parser.add_argument("--context", default=EXPECTED_CONTEXT)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--include-forwarded",
        action="store_true",
        help="DROPPED 기본 필터를 해제해 FORWARDED를 포함한 모든 verdict를 저장",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(
            args.output_dir.expanduser(),
            args.context,
            include_forwarded=args.include_forwarded,
        )
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
        print(f"관측 feed 실행 실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
