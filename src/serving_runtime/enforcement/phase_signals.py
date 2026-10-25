"""Extract phase timeline entries from versioned serving-engine log signals.

The pattern table is deliberately data-oriented and lives in this module so a
new engine/version can replace it without changing the timeline state machine.
Only directly observed signal timestamps become boundaries.  In particular,
an untimestamped marker is reported but is not assigned a guessed time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
import re
from typing import Any, BinaryIO


TIMELINE_SCHEMA = "runtime-phase-timeline/1.0"


@dataclass(frozen=True)
class SignalPattern:
    signal: str
    expression: re.Pattern[str]


@dataclass(frozen=True)
class EnginePatternSet:
    engine: str
    name: str
    support_level: str
    basis: str
    patterns: tuple[SignalPattern, ...]
    limitations: tuple[str, ...]


def _pattern(signal: str, text: str) -> SignalPattern:
    return SignalPattern(signal, re.compile(re.escape(text)))


# Grounded in the supplied vLLM CPU v0.29.0 startup log.  GPU-only
# torch.compile and CUDA graph markers are intentionally absent: that variant
# has not been observed for this project.
VLLM_CPU_029 = EnginePatternSet(
    engine="vllm",
    name="vllm-cpu-0.29.0-observed",
    support_level="observed_cpu_log_excerpt",
    basis="engine_signal",
    patterns=(
        _pattern("eager_compile_disabled", "Enforce eager set, disabling torch.compile"),
        _pattern("loading_start", "Loading model from scratch..."),
        _pattern("weights_loaded", "Loading weights took"),
        SignalPattern(
            "loading_end",
            re.compile(r"Model loading took .* seconds"),
        ),
        _pattern("compiling_start", "Warming up model for the compilation..."),
        _pattern("compiling_end", "Warming up done."),
        _pattern(
            "engine_init_end",
            "init engine (profile, create kv cache, warmup model) took",
        ),
        _pattern("inferring_start", "Application startup complete."),
        _pattern("shutdown_start", "[shutdown] API server: shutdown triggered"),
    ),
    limitations=(
        "CPU vLLM v0.29.0 로그 발췌에서 관찰한 표지만 지원",
        "torch.compile·CUDA graph capture GPU 표지는 미확인이라 패턴 없음",
        "타임스탬프 없는 표지는 경계 시각을 추정하지 않고 생략",
    ),
)


SGLANG_UNSUPPORTED = EnginePatternSet(
    engine="sglang",
    name="sglang-unverified",
    support_level="unsupported_no_source_log",
    basis="engine_signal",
    patterns=(),
    limitations=("SGLang 단계 표지는 근거 로그가 없어 미지원",),
)


ENGINE_PATTERN_SETS: dict[str, EnginePatternSet] = {
    "vllm": VLLM_CPU_029,
    "sglang": SGLANG_UNSUPPORTED,
}


_LOG_TIME = re.compile(
    r"(?<!\d)(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12]\d|3[01]) "
    r"(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d):(?P<second>[0-5]\d)(?!\d)"
)


def parse_timezone(value: str) -> tzinfo:
    """Parse an explicit fixed UTC offset; named/local zones are not guessed."""

    if value == "Z":
        return timezone.utc
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", value)
    if match is None:
        raise ValueError("timezone must be Z or an explicit offset such as +00:00")
    hours, minutes = int(match.group(2)), int(match.group(3))
    if hours > 23 or minutes > 59:
        raise ValueError("timezone offset is out of range")
    offset = timedelta(hours=hours, minutes=minutes)
    if match.group(1) == "-":
        offset = -offset
    try:
        return timezone(offset)
    except ValueError as error:
        raise ValueError("timezone offset is out of range") from error


def _line_time(line: str, *, year: int, zone: tzinfo) -> datetime | None:
    match = _LOG_TIME.search(line)
    if match is None:
        return None
    values = {name: int(match.group(name)) for name in (
        "month", "day", "hour", "minute", "second"
    )}
    try:
        return datetime(year=year, tzinfo=zone, **values)
    except ValueError as error:
        raise ValueError(f"invalid log timestamp: {match.group(0)}") from error


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def extract_phase_timeline(
    source: str | Path | BinaryIO,
    *,
    engine: str,
    year: int,
    timezone_offset: str,
) -> dict[str, Any]:
    """Return a timeline plus auditable extraction progress and omissions."""

    if isinstance(year, bool) or not isinstance(year, int) or not 1 <= year <= 9999:
        raise ValueError("year must be an integer from 1 through 9999")
    try:
        pattern_set = ENGINE_PATTERN_SETS[engine]
    except KeyError as error:
        raise ValueError(f"unsupported engine: {engine}") from error
    if not pattern_set.patterns:
        raise ValueError(
            f"{engine} phase signals are unsupported: no source-backed patterns"
        )
    zone = parse_timezone(timezone_offset)

    opened_here = not hasattr(source, "read")
    stream = open(source, "rb") if opened_here else source
    if not hasattr(stream, "readline"):
        raise ValueError("source must be a binary log stream or path")

    entries: list[dict[str, Any]] = []
    open_phases: dict[str, tuple[datetime, int]] = {}
    omissions: list[dict[str, Any]] = []
    matched = {pattern.signal: 0 for pattern in pattern_set.patterns}
    lines_read = 0
    unparsed_lines = 0
    byte_offset = 0
    run_number = 0
    run_active = False
    run_init_ended = False
    compile_seen = False
    inferring_seen = False

    def omit(line_number: int, signal: str, reason: str) -> None:
        item = {"line": line_number, "signal": signal, "reason": reason}
        if item not in omissions:
            omissions.append(item)

    def close_phase(phase: str, at: datetime, line_number: int) -> None:
        opened = open_phases.pop(phase, None)
        if opened is None:
            return
        started, _start_line = opened
        if at <= started:
            omit(line_number, f"{phase}_end", "non_positive_interval")
            return
        entries.append(
            {
                "phase": phase,
                "from": _iso(started),
                "until": _iso(at),
                "basis": pattern_set.basis,
            }
        )

    def finish_run(line_number: int) -> None:
        nonlocal run_active
        if not run_active:
            return
        if run_init_ended and not compile_seen:
            omit(line_number, "compiling", "compile_markers_not_observed")
        if run_init_ended and not inferring_seen:
            omit(line_number, "inferring_start", "startup_marker_not_observed_with_timestamp")
        run_active = False

    def start_run(line_number: int, at: datetime | None, signal: str) -> None:
        nonlocal run_number, run_active, run_init_ended, compile_seen, inferring_seen
        if run_active:
            if at is not None:
                for phase in tuple(open_phases):
                    close_phase(phase, at, line_number)
            finish_run(line_number)
        run_number += 1
        run_active = True
        run_init_ended = False
        compile_seen = False
        inferring_seen = False
        if signal != "loading_start":
            omit(line_number, "loading_start", "restart_observed_after_loading_start")

    try:
        while True:
            raw = stream.readline()
            if raw in (b"", ""):
                break
            if not isinstance(raw, bytes):
                raise ValueError("source stream must return bytes")
            lines_read += 1
            byte_offset += len(raw)
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise UnicodeError(f"log line {lines_read} is not UTF-8") from error
            pattern = next(
                (item for item in pattern_set.patterns if item.expression.search(line)),
                None,
            )
            if pattern is None:
                unparsed_lines += 1
                continue
            signal = pattern.signal
            matched[signal] += 1
            at = _line_time(line, year=year, zone=zone)
            if at is None:
                omit(lines_read, signal, "timestamp_missing")
                continue

            if signal == "loading_start":
                start_run(lines_read, at, signal)
                open_phases["loading"] = (at, lines_read)
            elif signal == "weights_loaded":
                if not run_active or run_init_ended or inferring_seen:
                    if "inferring" in open_phases:
                        close_phase("inferring", at, lines_read)
                    start_run(lines_read, at, signal)
            elif signal == "loading_end":
                if not run_active or run_init_ended or inferring_seen:
                    if "inferring" in open_phases:
                        close_phase("inferring", at, lines_read)
                    start_run(lines_read, at, signal)
                close_phase("loading", at, lines_read)
                if "loading" not in open_phases and not any(
                    item["signal"] == "loading_start" and item["line"] == lines_read
                    for item in omissions
                ) and matched["loading_start"] == 0:
                    omit(lines_read, "loading", "loading_start_not_observed")
            elif signal == "compiling_start":
                if not run_active:
                    start_run(lines_read, at, signal)
                compile_seen = True
                open_phases["compiling"] = (at, lines_read)
            elif signal == "compiling_end":
                compile_seen = True
                if "compiling" not in open_phases:
                    omit(lines_read, signal, "compiling_start_not_observed")
                close_phase("compiling", at, lines_read)
            elif signal == "engine_init_end":
                if not run_active:
                    start_run(lines_read, at, signal)
                run_init_ended = True
                if not compile_seen:
                    omit(lines_read, "compiling", "compile_markers_not_observed")
            elif signal == "inferring_start":
                if not run_active:
                    start_run(lines_read, at, signal)
                inferring_seen = True
                open_phases["inferring"] = (at, lines_read)
            elif signal == "shutdown_start":
                close_phase("inferring", at, lines_read)
                for phase in ("loading", "compiling"):
                    close_phase(phase, at, lines_read)
                finish_run(lines_read)
    finally:
        if opened_here:
            stream.close()

    finish_run(lines_read)
    for phase, (started, _line_number) in open_phases.items():
        entries.append(
            {
                "phase": phase,
                "from": _iso(started),
                "until": None,
                "basis": pattern_set.basis,
            }
        )
    entries.sort(key=lambda item: (item["from"], item["phase"]))
    return {
        "schema_version": TIMELINE_SCHEMA,
        "entries": entries,
        "extraction": {
            "engine": engine,
            "pattern_set": pattern_set.name,
            "support_level": pattern_set.support_level,
            "year": year,
            "timezone": timezone_offset,
            "runs_observed": run_number,
            "lines_read": lines_read,
            "unparsed_lines": unparsed_lines,
            "last_position": {"line": lines_read, "byte_offset": byte_offset},
            "matched_signals": {name: count for name, count in matched.items() if count},
            "omitted_boundaries": omissions,
            "open_phases": sorted(open_phases),
            "limitations": list(pattern_set.limitations),
        },
    }
