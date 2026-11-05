"""Read-only, redacted projection of the phase timeline used by ExecPolicy."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Iterable

from serving_runtime.enforcement.execpolicy import parse_timeline


def read_phase_timeline(path: str | None, *, demo: bool = False, observed_times: Iterable[str] = ()) -> dict[str, Any]:
    file = Path(path) if path else None
    if file is None or not file.is_file():
        if demo:
            now = datetime.now(timezone.utc)
            timestamps: list[datetime] = []
            for raw in observed_times:
                try:
                    parsed = datetime.fromisoformat(raw)
                    if parsed.tzinfo is not None:
                        timestamps.append(parsed.astimezone(timezone.utc))
                except (TypeError, ValueError):
                    continue
            first = min(timestamps) if timestamps else now
            last = max(timestamps) if timestamps else now
            def at(base: datetime, minutes: int) -> str:
                return (base + timedelta(minutes=minutes)).isoformat()
            return {
                "entries": [
                    {"phase": "loading", "from": at(first, -10), "until": at(first, -6), "basis": "engine_signal"},
                    {"phase": "compiling", "from": at(first, -6), "until": at(first, -2), "basis": "engine_signal"},
                    {"phase": "inferring", "from": at(first, -2), "until": at(last, 2), "basis": "engine_signal"},
                ],
                "collector": {"state": "example", "written_at": now.isoformat(), "fresh": True},
                "extraction": {"engine": "sglang", "support_level": "example", "limitations": ["합성 시간표이며 실제 엔진 로그 근거가 아닙니다."]},
                "data_origin": "synthetic",
            }
        return {"entries": [], "collector": {"state": "missing", "written_at": None, "fresh": False},
                "extraction": {"engine": None, "support_level": None, "limitations": []}, "data_origin": "real"}
    try:
        doc = json.loads(file.read_text(encoding="utf-8"))
        parse_timeline(doc)
        collector = doc.get("collector") or {}
        written = collector.get("written_at")
        timestamp = datetime.fromisoformat(written) if written else datetime.fromtimestamp(file.stat().st_mtime, timezone.utc)
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        fresh = -5 <= age < 15
        state = collector.get("state", "running")
        extraction = doc.get("extraction") or {}
        identities = [value for value in (collector.get("pod_name"), collector.get("pod_uid")) if value]
        limitations = [value for value in extraction.get("limitations", []) if not any(identity in value for identity in identities)]
        return {
            "entries": [{key: entry.get(key) for key in ("phase", "from", "until", "basis")} for entry in doc["entries"]],
            "collector": {"state": "stale" if not fresh else state, "written_at": timestamp.isoformat(), "fresh": fresh},
            "extraction": {"engine": extraction.get("engine"), "support_level": extraction.get("support_level"), "limitations": limitations},
            "data_origin": "real",
        }
    except (OSError, ValueError, TypeError, KeyError) as error:
        return {"entries": [], "collector": {"state": "error", "written_at": None, "fresh": False},
                "extraction": {"engine": None, "support_level": None, "limitations": [type(error).__name__]}, "data_origin": "real"}
