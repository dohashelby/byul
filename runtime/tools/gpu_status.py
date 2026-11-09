"""Read-only nvidia-smi query -> small public GPU snapshot for the dashboard.

Only fixed numeric fields and the product name are kept. GPU UUIDs, serial
numbers, bus IDs, processes and raw command output are never written.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Callable

SCHEMA = "runtime-gpu-status/1.0"
FIELDS = ("index", "name", "utilization.gpu", "memory.used", "memory.total", "temperature.gpu", "power.draw")
MAX_OUTPUT = 64 * 1024
MAX_GPUS = 16
TIMEOUT_S = 8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(raw: str) -> float | None:
    value = raw.strip()
    if value in {"", "[N/A]", "N/A", "[Not Supported]", "Not Supported"}:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("invalid number")
    return number


def parse(output: str) -> list[dict[str, Any]]:
    """Parse `--format=csv,noheader,nounits` rows; any malformed row rejects all."""
    if len(output) > MAX_OUTPUT:
        raise ValueError("OUTPUT_TOO_LARGE")
    rows = [line for line in output.splitlines() if line.strip()]
    if not rows:
        raise ValueError("NO_GPU")
    if len(rows) > MAX_GPUS:
        raise ValueError("TOO_MANY_GPUS")
    gpus = []
    for line in rows:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != len(FIELDS):
            raise ValueError("PARSE_ERROR")
        index, name = parts[0], parts[1]
        if not index.isdigit() or not name or len(name) > 64 or not name.isprintable():
            raise ValueError("PARSE_ERROR")
        try:
            util, used, total, temp, power = (_number(p) for p in parts[2:])
        except ValueError:
            raise ValueError("PARSE_ERROR") from None
        if util is not None and util > 100:
            raise ValueError("PARSE_ERROR")
        if used is not None and total is not None and used > total:
            raise ValueError("PARSE_ERROR")
        gpus.append({"index": int(index), "name": name, "utilization_percent": util,
                     "memory_used_mib": used, "memory_total_mib": total,
                     "temperature_c": temp, "power_w": power})
    return gpus


def query(runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
          binary: str | None = None) -> list[dict[str, Any]]:
    path = binary or shutil.which("nvidia-smi")
    if not path:
        raise ValueError("NVIDIA_SMI_MISSING")
    try:
        result = runner([path, "--query-gpu=" + ",".join(FIELDS), "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise ValueError("TIMEOUT") from None
    except OSError:
        raise ValueError("EXEC_FAILED") from None
    if result.returncode:
        raise ValueError("NVIDIA_SMI_FAILED")
    return parse(result.stdout or "")


def collect(runner=subprocess.run, binary=None) -> dict[str, Any]:
    try:
        gpus = query(runner, binary)
        return {"schema_version": SCHEMA, "collected_at": _now(), "state": "ok", "reason": None, "gpus": gpus}
    except ValueError as error:
        reason = str(error) if str(error).isupper() else "PARSE_ERROR"
        return {"schema_version": SCHEMA, "collected_at": _now(), "state": "error", "reason": reason, "gpus": []}


def write(path: Path, value: dict[str, Any], mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".next")
    temporary.unlink(missing_ok=True)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as target:
        os.fchmod(target.fileno(), mode)
        target.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="공개 GPU 상태 파일(0640)")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=10)
    args = parser.parse_args(argv)
    if args.interval < 2:
        parser.error("interval must be at least two seconds")
    while True:
        result = collect()
        write(args.output, result)
        print(json.dumps({"state": result["state"], "reason": result["reason"], "gpus": len(result["gpus"])}), flush=True)
        if not args.watch:
            return 0 if result["state"] == "ok" else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
