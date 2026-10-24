#!/usr/bin/env python3
"""Append live Hubble and Tetragon JSONL for the isolated runtime kind lab."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
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


def tetragon_command() -> list[str]:
    return [
        "kubectl",
        "--context",
        EXPECTED_CONTEXT,
        "--namespace",
        "kube-system",
        "logs",
        "--follow",
        "--tail=0",
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
        raise RuntimeError(
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


def write_heartbeats(
    specifications: dict[
        str,
        tuple[Path, Path, str, list[tuple[str, subprocess.Popen[bytes]]]],
    ],
    *,
    state: str,
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
        _write_json_atomic(
            heartbeat_path,
            {
                "schema": HEARTBEAT_SCHEMA,
                "kind": kind,
                "feed_path": str(feed_path),
                "started_at": started_at,
                "updated_at": updated_at,
                "state": actual_state,
                "processes": process_status,
            },
        )


def run(output_dir: Path, context: str, *, include_forwarded: bool = False) -> int:
    assert_safe_context(context)
    output_dir = output_dir.absolute()
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    hubble_path = output_dir / "hubble.jsonl"
    tetragon_path = output_dir / "tetragon.jsonl"
    hubble_error_path = output_dir / "hubble.stderr.log"
    tetragon_error_path = output_dir / "tetragon.stderr.log"
    port_forward_log_path = output_dir / "cilium-hubble-port-forward.log"
    processes: list[tuple[str, subprocess.Popen[bytes]]] = []
    heartbeat_specs: dict[
        str,
        tuple[Path, Path, str, list[tuple[str, subprocess.Popen[bytes]]]],
    ] = {}
    final_state = "error"
    stop_requested = threading.Event()
    with (
        hubble_path.open("ab") as hubble_output,
        tetragon_path.open("ab") as tetragon_output,
        hubble_error_path.open("ab") as hubble_error,
        tetragon_error_path.open("ab") as tetragon_error,
        port_forward_log_path.open("ab") as port_forward_log,
    ):
        previous_signal_handlers = _install_stop_signal_handlers(stop_requested)
        try:
            try:
                port_forward = _start(
                    hubble_port_forward_command(), port_forward_log, port_forward_log
                )
                processes.append(("cilium hubble port-forward", port_forward))
                if stop_requested.is_set():
                    raise _StopRequested
                _wait_for_hubble(
                    port_forward, stop_requested=stop_requested.is_set
                )
                hubble_started_at = _utc_now()
                hubble_process = _start(
                    hubble_command(
                        since=hubble_started_at,
                        include_forwarded=include_forwarded,
                    ),
                    hubble_output,
                    hubble_error,
                )
                processes.append(("hubble", hubble_process))
                if stop_requested.is_set():
                    raise _StopRequested
                heartbeat_specs["hubble"] = (
                    output_dir / "hubble.heartbeat.json",
                    hubble_path,
                    hubble_started_at,
                    [
                        ("cilium-hubble-port-forward", port_forward),
                        ("hubble", hubble_process),
                    ],
                )
                tetragon_started_at = _utc_now()
                tetragon_process = _start(
                    tetragon_command(), tetragon_output, tetragon_error
                )
                processes.append(("tetragon", tetragon_process))
                if stop_requested.is_set():
                    raise _StopRequested
                heartbeat_specs["tetragon"] = (
                    output_dir / "tetragon.heartbeat.json",
                    tetragon_path,
                    tetragon_started_at,
                    [("tetragon", tetragon_process)],
                )
                write_heartbeats(heartbeat_specs, state="running")
                print(f"Hubble feed: {hubble_path}", flush=True)
                print(f"Tetragon feed: {tetragon_path}", flush=True)
                print(
                    f"Heartbeat: {heartbeat_specs['hubble'][0]}, "
                    f"{heartbeat_specs['tetragon'][0]}",
                    flush=True,
                )
                print(
                    "Ctrl-C 또는 SIGTERM/SIGHUP으로 follow 프로세스를 함께 종료합니다.",
                    flush=True,
                )
                while True:
                    if stop_requested.is_set():
                        raise _StopRequested
                    write_heartbeats(heartbeat_specs, state="running")
                    for name, process in processes:
                        returncode = process.poll()
                        if returncode is not None:
                            raise RuntimeError(
                                f"{name} follow exited with status {returncode}"
                            )
                    try:
                        processes[0][1].wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        continue
            except (KeyboardInterrupt, _StopRequested):
                final_state = "stopped"
                return 0
        finally:
            for _name, process in processes:
                if process.poll() is None:
                    process.terminate()
            for _name, process in processes:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if heartbeat_specs:
                write_heartbeats(heartbeat_specs, state=final_state)
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
    except (OSError, RuntimeError, ValueError) as error:
        print(f"관측 feed 실행 실패: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
