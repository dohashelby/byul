"""Bounded collector/transport pipes and optional stdin, without a shell or queues.

Only the child created here is killed. No daemon, container, process group or
unrelated process is signalled. Each reader retains at most its byte budget;
counts describe bytes observed before termination, not the full source size.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import threading
import time


def capture_pipes(argv: list[str], *, timeout: float, env_extra: dict[str, str],
                  max_stdout_bytes: int, max_stderr_bytes: int, input_bytes: bytes | None = None) -> dict:
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
            or any(type(cap) is not int or not 1 <= cap <= 16 * 1024**2
                   for cap in (max_stdout_bytes, max_stderr_bytes))):
        raise ValueError("finite deadline and bounded positive pipe budgets required")
    if input_bytes is not None and (type(input_bytes) is not bytes or len(input_bytes) > 1024**2):
        raise ValueError("stdin must be bounded bytes")
    started = time.monotonic()
    env = {**os.environ, **env_extra} if env_extra else None
    child = subprocess.Popen(argv, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env, bufsize=0)
    stop = threading.Event()
    retained = {"stdout": bytearray(), "stderr": bytearray()}
    observed = {"stdout": 0, "stderr": 0}
    overflow = {"stdout": False, "stderr": False}
    read_errors: list[str] = []

    def consume(name, pipe, limit):
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                observed[name] += len(chunk)
                remaining = limit - len(retained[name])
                retained[name].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow[name] = True
                    stop.set()
                    break
        except (OSError, ValueError):
            read_errors.append(name)
            stop.set()

    threads = [threading.Thread(target=consume, args=(name, pipe, cap), daemon=True)
               for name, pipe, cap in (("stdout", child.stdout, max_stdout_bytes),
                                       ("stderr", child.stderr, max_stderr_bytes))]
    def feed():
        try:
            remaining = memoryview(input_bytes)
            while remaining:
                written = child.stdin.write(remaining)
                if not written:
                    raise OSError("stdin closed")
                remaining = remaining[written:]
        except (OSError, ValueError):
            read_errors.append("stdin")
            stop.set()
        finally:
            try:
                child.stdin.close()
            except OSError:
                pass

    if input_bytes is not None:
        threads.append(threading.Thread(target=feed, daemon=True))
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        # Pipe EOF and child completion are both required. Descendants holding
        # an inherited pipe cannot extend this call beyond its own deadline.
        while child.poll() is None or any(thread.is_alive() for thread in threads):
            if stop.is_set():
                break
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
                break
            stop.wait(min(0.01, remaining))
    finally:
        if child.poll() is None:
            child.kill()
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            # A platform failure is a collection gap; do not kill other PIDs.
            read_errors.append("child_reap")
        for thread in threads:
            thread.join(timeout=0.25)
        pipes_closed = not any(thread.is_alive() for thread in threads)
        # Unbuffered FileIO.close does not wait for a buffered reader lock.
        for pipe in (child.stdout, child.stderr, *([child.stdin] if input_bytes is not None else [])):
            try:
                pipe.close()
            except OSError:
                pass
    raw = {name: bytes(value) for name, value in retained.items()}
    return {"returncode": child.returncode if child.returncode is not None else -1,
            "stdout_bytes": raw["stdout"], "stderr_bytes": raw["stderr"],
            "capture": {"schema": "guard-pipe-capture/1", "bounded_at_source": True,
                        "timed_out": timed_out, "truncated": any(overflow.values()),
                        "pipes_closed": pipes_closed, "read_errors": sorted(set(read_errors)),
                        "complete": not timed_out and not any(overflow.values()) and pipes_closed and not read_errors,
                        "streams": {name: {"limit": cap, "retained_bytes": len(raw[name]),
                                           "observed_bytes": observed[name], "truncated": overflow[name],
                                           "retained_sha256": hashlib.sha256(raw[name]).hexdigest()}
                                    for name, cap in (("stdout", max_stdout_bytes), ("stderr", max_stderr_bytes))},
                        "hash_scope": "exact retained CLI pipe bytes, before UTF-8 decoding or redaction; not daemon-file bytes"}}


def strict_json(raw, *, limit=65536):
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise ValueError("CAPTURE_BYTE_BOUND")
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("CAPTURE_DUPLICATE_KEY")
            out[key] = value
        return out
    def constant(_):
        raise ValueError("CAPTURE_NONFINITE_NUMBER")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, RecursionError, json.JSONDecodeError):
        raise ValueError("CAPTURE_INVALID_JSON") from None
