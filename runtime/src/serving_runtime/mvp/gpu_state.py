"""Read the host-produced GPU snapshot; collection stopped is shown as stale, not zero."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

SCHEMA = "runtime-gpu-status/1.0"
NUMERIC = ("utilization_percent", "memory_used_mib", "memory_total_mib", "temperature_c", "power_w")
REASONS = {"NVIDIA_SMI_MISSING", "NVIDIA_SMI_FAILED", "TIMEOUT", "EXEC_FAILED", "PARSE_ERROR",
           "NO_GPU", "TOO_MANY_GPUS", "OUTPUT_TOO_LARGE"}


def _gpu(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or type(value.get("index")) is not int or value["index"] < 0:
        raise ValueError("invalid gpu")
    name = value.get("name")
    if not isinstance(name, str) or not name or len(name) > 64 or not name.isprintable():
        raise ValueError("invalid gpu")
    row = {"index": value["index"], "name": name}
    for key in NUMERIC:
        number = value.get(key)
        if number is not None and (isinstance(number, bool) or not isinstance(number, (int, float))
                                   or (isinstance(number, float) and not math.isfinite(number)) or number < 0):
            raise ValueError("invalid gpu")
        row[key] = number
    return row


def read_gpu_state(path: str | Path | None, *, now: datetime | None = None,
                   stale_after_s: float = 45) -> dict[str, Any]:
    result = {"schema_version": SCHEMA, "state": "not_configured", "fresh": False,
              "collected_at": None, "reason": None, "gpus": []}
    if path is None:
        return result
    try:
        target = Path(path)
        if not target.exists():
            result["state"] = "missing"
            return result
        if target.stat().st_size > 256 * 1024:
            raise ValueError("too large")
        value = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != SCHEMA:
            raise ValueError("schema")
        written = datetime.fromisoformat(str(value["collected_at"]).replace("Z", "+00:00"))
        if written.tzinfo is None:
            raise ValueError("timezone")
        if value.get("state") not in {"ok", "error"} or not isinstance(value.get("gpus"), list):
            raise ValueError("state")
        reason = value.get("reason")
        if value["state"] == "error" and reason not in REASONS:
            raise ValueError("reason")
        gpus = [_gpu(item) for item in value["gpus"][:16]]
        age = ((now or datetime.now(timezone.utc)) - written).total_seconds()
        stale = age > stale_after_s or age < -5
        result.update(collected_at=written.isoformat().replace("+00:00", "Z"), gpus=gpus,
                      reason=reason if value["state"] == "error" else None,
                      state="stale" if stale else value["state"],
                      fresh=not stale and value["state"] == "ok")
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        result.update(state="error", reason="INVALID_SNAPSHOT", gpus=[], collected_at=None)
    return result
