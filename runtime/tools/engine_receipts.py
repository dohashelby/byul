"""Engine Pod logs -> request IDs only (optional, off by default).

Each log line is scanned in memory for a Gateway request ID (32 lowercase hex)
in a known engine form and then discarded. Prompts, responses, log text,
Pod names and node names are never written. Only {request_id, observed_at}
pairs and the coverage window are published (0640) for the Gateway.

Known forms (confirm per engine version on the server before relying on them):
  vLLM   chatcmpl-<id> / cmpl-<id>  (when X-Request-Id is honored and requests are logged)
  SGLang rid=<id> / "rid": "<id>"   (only if the engine adopts the forwarded ID)
A missing ID therefore means "not observed in logs", never "not received".
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable

SCHEMA = "runtime-engine-receipts/1.0"
PATTERN = re.compile(rb"(?:\b(?:chatcmpl|cmpl)-|\brid=['\"]?|\"rid\":\s*\")([0-9a-f]{32})(?![0-9a-f])")
STAMP = re.compile(rb"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})) ")
NAME = re.compile(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?")
SELECTOR = re.compile(r"[A-Za-z0-9./_-]+=[A-Za-z0-9._-]+(,[A-Za-z0-9./_-]+=[A-Za-z0-9._-]+)*")
MAX_LOG_BYTES = 32 * 1024 * 1024
MAX_IDS = 20000
TIMEOUT_S = 20


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _stamp(raw: bytes) -> datetime | None:
    try:
        text = raw.decode("ascii").replace("Z", "+00:00")
        if "." in text:  # kubectl prints nanoseconds; Python accepts at most microseconds.
            head, _, tail = text.partition(".")
            digits = re.match(r"\d+", tail).group()
            text = f"{head}.{digits[:6]}{tail[len(digits):]}"
        value = datetime.fromisoformat(text)
        return value if value.tzinfo else None
    except (ValueError, AttributeError):
        return None


def extract(output: bytes) -> dict[str, str]:
    """Return {request_id: first observed_at}; lines without a timestamp are skipped."""
    found: dict[str, str] = {}
    for line in output.splitlines():
        match = STAMP.match(line)
        if not match:
            continue
        when = _stamp(match.group(1))
        if when is None:
            continue
        for item in PATTERN.finditer(line, match.end()):
            request_id = item.group(1).decode("ascii")
            stamp = _iso(when)
            if request_id not in found or stamp < found[request_id]:
                found[request_id] = stamp
    return found


class Collector:
    def __init__(self, context: str, namespace: str, selector: str, output: Path, *, container: str | None = None,
                 since_s: int = 60, runner: Callable[..., Any] = subprocess.run,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.context, self.namespace, self.selector, self.container = context, namespace, selector, container
        self.output, self.since_s, self.run, self.clock = output, since_s, runner, clock
        self.receipts: dict[str, str] = {}
        self.window_started_at: datetime | None = None

    def command(self) -> list[str]:
        command = ["kubectl", "--context", self.context, "-n", self.namespace, "logs", "-l", self.selector,
                   "--since", f"{self.since_s}s", "--timestamps=true", "--prefix=false", "--tail=-1",
                   "--max-log-requests=8", "--ignore-errors=false"]
        if self.container:
            command += ["-c", self.container]
        return command

    def collect(self) -> dict[str, Any]:
        now = self.clock()
        reason = None
        try:
            result = self.run(self.command(), capture_output=True, timeout=TIMEOUT_S, check=False)
            if result.returncode:
                reason = "KUBECTL_FAILED"
            elif len(result.stdout) > MAX_LOG_BYTES:
                reason = "OUTPUT_TOO_LARGE"
            else:
                found = extract(result.stdout)
        except subprocess.TimeoutExpired:
            reason = "TIMEOUT"
        except OSError:
            reason = "EXEC_FAILED"
        if reason:
            # A failed read is a coverage gap: the next success restarts the window.
            self.window_started_at = None
        else:
            if self.window_started_at is None:
                self.window_started_at = now - timedelta(seconds=self.since_s)
            for request_id, stamp in found.items():
                if request_id not in self.receipts or stamp < self.receipts[request_id]:
                    self.receipts[request_id] = stamp
            if len(self.receipts) > MAX_IDS:
                keep = sorted(self.receipts.items(), key=lambda item: item[1])[-MAX_IDS:]
                self.receipts = dict(keep)
                # Older IDs were dropped, so absence before the oldest kept is not covered.
                self.window_started_at = max(self.window_started_at, _stamp(keep[0][1].encode()))
        return {"schema_version": SCHEMA, "context": self.context, "collected_at": _iso(now),
                "state": "error" if reason else "ok", "reason": reason,
                "window_started_at": _iso(self.window_started_at) if self.window_started_at else None,
                "receipts": [{"request_id": k, "observed_at": v} for k, v in sorted(self.receipts.items(), key=lambda i: i[1])]}


def write(path: Path, value: dict[str, Any], mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".next")
    temporary.unlink(missing_ok=True)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as target:
        os.fchmod(target.fileno(), mode)
        target.write(json.dumps(value, separators=(",", ":")) + "\n")
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--selector", required=True, help="엔진 Pod 라벨 선택자(예: app=serving-engine)")
    parser.add_argument("--container")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--since-seconds", type=int, default=60)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args(argv)
    if not NAME.fullmatch(args.namespace) or not SELECTOR.fullmatch(args.selector) or (
            args.container and not NAME.fullmatch(args.container)):
        parser.error("invalid namespace, selector or container")
    if args.interval < 2 or args.since_seconds < args.interval * 2:
        parser.error("since-seconds must cover at least two intervals")
    collector = Collector(args.context, args.namespace, args.selector, args.output,
                          container=args.container, since_s=args.since_seconds)
    while True:
        result = collector.collect()
        write(args.output, result)
        print(json.dumps({"state": result["state"], "reason": result["reason"], "ids": len(result["receipts"])}), flush=True)
        if not args.watch:
            return 0 if result["state"] == "ok" else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
