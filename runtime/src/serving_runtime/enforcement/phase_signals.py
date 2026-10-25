"""Extract phase timeline entries from versioned serving-engine log signals.

The pattern table is deliberately data-oriented and lives in this module so a
new engine/version can replace it without changing the timeline state machine.
Only directly observed signal timestamps, or an explicitly configured health
200 observed by the follower clock, become boundaries. An untimestamped log
marker is reported but is not assigned a guessed time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
import json
import os
from pathlib import Path
import re
import select
import sys
import tempfile
import threading
import time
from typing import Any, BinaryIO, Callable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


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
_REQUEST_LINE = re.compile(r"request_logger|SamplingParams|(?:^|[\s,])prompt\s*[:=]", re.IGNORECASE)


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


def _configuration(engine: str, year: int, timezone_offset: str) -> tuple[EnginePatternSet, tzinfo]:
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
    return pattern_set, parse_timezone(timezone_offset)


class _TimelineState:
    """Incremental signal state; it never retains a source log line."""

    def __init__(
        self,
        *,
        engine: str,
        year: int,
        timezone_offset: str,
        health_enabled: bool = False,
        resume_document: dict[str, Any] | None = None,
    ) -> None:
        self.pattern_set, self.zone = _configuration(engine, year, timezone_offset)
        self.engine = engine
        self.year = year
        self.timezone_offset = timezone_offset
        self.health_enabled = health_enabled
        self.entries: list[dict[str, Any]] = []
        self.open_phases: dict[str, tuple[datetime, int, str]] = {}
        self.omissions: list[dict[str, Any]] = []
        self.matched = {pattern.signal: 0 for pattern in self.pattern_set.patterns}
        self.lines_read = 0
        self.unparsed_lines = 0
        self.byte_offset = 0
        self.run_number = 0
        self.run_active = False
        self.run_init_ended = False
        self.compile_seen = False
        self.inferring_seen = False
        self.health_ready_at: datetime | None = None
        self.health = {
            "configured": health_enabled,
            "clock_source": "gateway_side_collector_clock_utc",
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "last_checked_at": None,
            "last_result": "not_ready",
        }
        if resume_document is not None:
            self._resume(resume_document)

    def _resume(self, document: dict[str, Any]) -> None:
        extraction = document.get("extraction", {})
        for item in document.get("entries", []):
            copied = dict(item)
            if copied.get("until") is None:
                started = datetime.fromisoformat(copied["from"])
                self.open_phases[copied["phase"]] = (started, 0, "resumed")
            else:
                self.entries.append(copied)
        self.lines_read = extraction.get("lines_read", 0)
        self.unparsed_lines = extraction.get("unparsed_lines", 0)
        self.byte_offset = extraction.get("last_position", {}).get("byte_offset", 0)
        self.run_number = extraction.get("runs_observed", 0)
        self.omissions = [dict(item) for item in extraction.get("omitted_boundaries", [])]
        for name, count in extraction.get("matched_signals", {}).items():
            if name in self.matched:
                self.matched[name] = count
        saved = extraction.get("collector_state", {})
        self.run_active = saved.get("run_active", bool(self.open_phases))
        self.run_init_ended = saved.get("run_init_ended", False)
        self.compile_seen = saved.get(
            "compile_seen",
            any(item["phase"] == "compiling" for item in document.get("entries", [])),
        )
        self.inferring_seen = saved.get("inferring_seen", "inferring" in self.open_phases)
        ready = saved.get("health_ready_at")
        if ready is not None:
            self.health_ready_at = datetime.fromisoformat(ready)
        previous_health = extraction.get("health_probe")
        if isinstance(previous_health, dict):
            for name in ("attempts", "successes", "failures", "last_checked_at", "last_result"):
                if name in previous_health:
                    self.health[name] = previous_health[name]

    def omit(self, line_number: int, signal: str, reason: str) -> bool:
        item = {"line": line_number, "signal": signal, "reason": reason}
        if item in self.omissions:
            return False
        self.omissions.append(item)
        return True

    def close_phase(self, phase: str, at: datetime, line_number: int) -> bool:
        opened = self.open_phases.pop(phase, None)
        if opened is None:
            return False
        started, _start_line, source = opened
        if at <= started:
            reason = "health_and_log_clock_order_invalid" if source == "health_probe" else "non_positive_interval"
            self.omit(line_number, f"{phase}_end", reason)
            return True
        self.entries.append(
            {
                "phase": phase,
                "from": _iso(started),
                "until": _iso(at),
                "basis": self.pattern_set.basis,
            }
        )
        return True

    def finish_run(self, line_number: int) -> bool:
        if not self.run_active:
            return False
        changed = False
        if self.run_init_ended and not self.compile_seen:
            changed |= self.omit(line_number, "compiling", "compile_markers_not_observed")
        if self.run_init_ended and not self.inferring_seen:
            reason = (
                "health_probe_not_successful"
                if self.health_enabled
                else "startup_marker_not_observed_with_timestamp"
            )
            changed |= self.omit(line_number, "inferring_start", reason)
        self.run_active = False
        return changed

    def start_run(self, line_number: int, at: datetime | None, signal: str) -> bool:
        changed = False
        if self.run_active:
            if at is not None:
                for phase in tuple(self.open_phases):
                    changed |= self.close_phase(phase, at, line_number)
            changed |= self.finish_run(line_number)
        self.run_number += 1
        self.run_active = True
        self.run_init_ended = False
        self.compile_seen = False
        self.inferring_seen = False
        self.health_ready_at = None
        self.health["last_result"] = "not_ready"
        if signal != "loading_start":
            changed |= self.omit(line_number, "loading_start", "restart_observed_after_loading_start")
        return True

    def process_line(self, raw: bytes, *, byte_offset: int | None = None) -> bool:
        if not isinstance(raw, bytes):
            raise ValueError("source stream must return bytes")
        self.lines_read += 1
        self.byte_offset = self.byte_offset + len(raw) if byte_offset is None else byte_offset
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise UnicodeError(f"log line {self.lines_read} is not UTF-8") from error
        # Request logging may contain arbitrary prompt text that resembles an
        # engine marker. It is neither a trusted boundary nor retained output.
        if _REQUEST_LINE.search(line):
            self.unparsed_lines += 1
            return False
        pattern = next(
            (item for item in self.pattern_set.patterns if item.expression.search(line)),
            None,
        )
        if pattern is None:
            self.unparsed_lines += 1
            return False
        signal = pattern.signal
        self.matched[signal] += 1
        at = _line_time(line, year=self.year, zone=self.zone)
        if at is None:
            return self.omit(self.lines_read, signal, "timestamp_missing")

        changed = False
        if signal == "loading_start":
            changed |= self.start_run(self.lines_read, at, signal)
            self.open_phases["loading"] = (at, self.lines_read, "log_timestamp")
            changed = True
        elif signal == "weights_loaded":
            if not self.run_active or self.run_init_ended or self.inferring_seen:
                if "inferring" in self.open_phases:
                    changed |= self.close_phase("inferring", at, self.lines_read)
                changed |= self.start_run(self.lines_read, at, signal)
        elif signal == "loading_end":
            if not self.run_active or self.run_init_ended or self.inferring_seen:
                if "inferring" in self.open_phases:
                    changed |= self.close_phase("inferring", at, self.lines_read)
                changed |= self.start_run(self.lines_read, at, signal)
            changed |= self.close_phase("loading", at, self.lines_read)
            if "loading" not in self.open_phases and not any(
                item["signal"] == "loading_start" and item["line"] == self.lines_read
                for item in self.omissions
            ) and self.matched["loading_start"] == 0:
                changed |= self.omit(self.lines_read, "loading", "loading_start_not_observed")
        elif signal == "compiling_start":
            if not self.run_active:
                changed |= self.start_run(self.lines_read, at, signal)
            self.compile_seen = True
            self.open_phases["compiling"] = (at, self.lines_read, "log_timestamp")
            changed = True
        elif signal == "compiling_end":
            self.compile_seen = True
            if "compiling" not in self.open_phases:
                changed |= self.omit(self.lines_read, signal, "compiling_start_not_observed")
            changed |= self.close_phase("compiling", at, self.lines_read)
            if self.health_ready_at is None:
                self.health_ready_at = at
            changed = True
        elif signal == "engine_init_end":
            if not self.run_active:
                changed |= self.start_run(self.lines_read, at, signal)
            self.run_init_ended = True
            if self.health_ready_at is None:
                self.health_ready_at = at
            if not self.compile_seen:
                changed |= self.omit(self.lines_read, "compiling", "compile_markers_not_observed")
            changed = True
        elif signal == "inferring_start":
            if not self.run_active:
                changed |= self.start_run(self.lines_read, at, signal)
            self.inferring_seen = True
            self.open_phases["inferring"] = (at, self.lines_read, "log_timestamp")
            changed = True
        elif signal == "shutdown_start":
            changed |= self.close_phase("inferring", at, self.lines_read)
            for phase in ("loading", "compiling"):
                changed |= self.close_phase(phase, at, self.lines_read)
            changed |= self.finish_run(self.lines_read)
        return changed

    def health_due(self) -> bool:
        return (
            self.health_enabled
            and self.run_active
            and self.health_ready_at is not None
            and not self.inferring_seen
        )

    def health_result(self, *, checked_at: datetime, status: int | None, error: str | None) -> bool:
        if checked_at.tzinfo is None:
            raise ValueError("collector clock must return a timezone-aware datetime")
        checked_at = checked_at.astimezone(timezone.utc)
        self.health["attempts"] += 1
        self.health["last_checked_at"] = _iso(checked_at)
        if status != 200:
            self.health["failures"] += 1
            self.health["last_result"] = f"http_{status}" if status is not None else (error or "error")
            return True
        self.health["successes"] += 1
        if self.health_ready_at is None or checked_at <= self.health_ready_at:
            self.health["last_result"] = "clock_order_invalid"
            return self.omit(
                max(1, self.lines_read), "inferring_start", "health_and_log_clock_order_invalid"
            )
        self.health["last_result"] = "healthy_boundary_recorded"
        self.inferring_seen = True
        self.open_phases["inferring"] = (
            checked_at,
            self.lines_read,
            "health_probe",
        )
        return True

    def snapshot(self, *, follow: dict[str, Any] | None = None) -> dict[str, Any]:
        entries = [dict(item) for item in self.entries]
        for phase, (started, _line_number, _source) in self.open_phases.items():
            entries.append(
                {
                    "phase": phase,
                    "from": _iso(started),
                    "until": None,
                    "basis": self.pattern_set.basis,
                }
            )
        entries.sort(key=lambda item: (item["from"], item["phase"]))
        extraction: dict[str, Any] = {
            "engine": self.engine,
            "pattern_set": self.pattern_set.name,
            "support_level": self.pattern_set.support_level,
            "year": self.year,
            "timezone": self.timezone_offset,
            "runs_observed": self.run_number,
            "lines_read": self.lines_read,
            "unparsed_lines": self.unparsed_lines,
            "last_position": {"line": self.lines_read, "byte_offset": self.byte_offset},
            "matched_signals": {name: count for name, count in self.matched.items() if count},
            "omitted_boundaries": list(self.omissions),
            "open_phases": sorted(self.open_phases),
            "limitations": list(self.pattern_set.limitations),
        }
        if follow is not None:
            extraction["follow"] = follow
            extraction["collector_state"] = {
                "run_active": self.run_active,
                "run_init_ended": self.run_init_ended,
                "compile_seen": self.compile_seen,
                "inferring_seen": self.inferring_seen,
                "health_ready_at": _iso(self.health_ready_at) if self.health_ready_at else None,
            }
            extraction["clock_sources"] = {
                "log_boundaries": "log_timestamp_with_explicit_year_and_timezone",
                "health_boundaries": "gateway_side_collector_clock_utc",
                "ordering": "timezone_aware_absolute_time",
            }
            extraction["health_probe"] = dict(self.health)
        return {"schema_version": TIMELINE_SCHEMA, "entries": entries, "extraction": extraction}


def extract_phase_timeline(
    source: str | Path | BinaryIO,
    *,
    engine: str,
    year: int,
    timezone_offset: str,
) -> dict[str, Any]:
    """Return a timeline plus auditable extraction progress and omissions."""

    state = _TimelineState(engine=engine, year=year, timezone_offset=timezone_offset)
    opened_here = not hasattr(source, "read")
    stream = open(source, "rb") if opened_here else source
    if not hasattr(stream, "readline"):
        raise ValueError("source must be a binary log stream or path")
    try:
        while True:
            raw = stream.readline()
            if raw in (b"", ""):
                break
            state.process_line(raw)
    finally:
        if opened_here:
            stream.close()
    state.finish_run(state.lines_read)
    return state.snapshot()


def _atomic_write(path: Path, document: dict[str, Any]) -> None:
    encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=False, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_resume(path: Path) -> dict[str, Any]:
    from .execpolicy import parse_timeline

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot resume timeline: {type(error).__name__}") from error
    parse_timeline(document)
    return document


def _health_status(opener: Callable[..., Any], url: str, timeout: float) -> tuple[int | None, str | None]:
    try:
        response = opener(Request(url, method="GET"), timeout=timeout)
        try:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            return int(status), None
        finally:
            response.close()
    except Exception as error:  # urllib exposes several transport exception classes.
        status = getattr(error, "code", None)
        return (int(status) if isinstance(status, int) else None), type(error).__name__


def follow_phase_timeline(
    source: str | Path,
    output: str | Path,
    *,
    engine: str,
    year: int,
    timezone_offset: str,
    existing: str = "fail",
    health_url: str | None = None,
    health_interval: float = 2.0,
    stop_event: threading.Event | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    opener: Callable[..., Any] = urlopen,
    stdin: BinaryIO | None = None,
    poll_interval: float = 0.1,
) -> dict[str, Any]:
    """Follow a file (including replacement/truncation) or binary stdin."""

    if existing not in {"fail", "overwrite", "resume"}:
        raise ValueError("existing must be fail, overwrite, or resume")
    if health_interval <= 0:
        raise ValueError("health interval must be greater than zero")
    if health_url is not None:
        parsed = urlsplit(health_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("health URL must be HTTP(S) without userinfo")
    output_path = Path(output)
    resume_document: dict[str, Any] | None = None
    if output_path.exists():
        if existing == "fail":
            raise ValueError("timeline exists; choose --overwrite or --resume")
        if existing == "resume":
            resume_document = _load_resume(output_path)
            previous = resume_document.get("extraction", {})
            if (
                previous.get("engine") != engine
                or previous.get("year") != year
                or previous.get("timezone") != timezone_offset
            ):
                raise ValueError("resume timeline engine, year, and timezone must match")
    state = _TimelineState(
        engine=engine,
        year=year,
        timezone_offset=timezone_offset,
        health_enabled=health_url is not None,
        resume_document=resume_document,
    )
    stop = stop_event or threading.Event()
    previous_follow = (resume_document or {}).get("extraction", {}).get("follow", {})
    previous_events = previous_follow.get("events", [])
    follow: dict[str, Any] = {
        "mode": "stdin" if str(source) == "-" else "file",
        "existing": existing,
        "state": previous_follow.get("state", "running")
        if previous_follow.get("state") == "read_error"
        else "running",
        "last_updated_at": None,
        "input_identity": None,
        "events": [dict(item) for item in previous_events],
    }

    def now() -> datetime:
        value = clock()
        if value.tzinfo is None:
            raise ValueError("collector clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def event(kind: str, error: str | None = None) -> None:
        item: dict[str, Any] = {"kind": kind, "at": _iso(now())}
        if error is not None:
            item["error"] = error
        last = follow["events"][-1] if follow["events"] else None
        if last is None or last.get("kind") != kind or last.get("error") != error:
            follow["events"].append(item)
        if kind in {"read_error", "input_ended"} and follow["state"] == "running":
            follow["state"] = kind

    def write() -> None:
        follow["last_updated_at"] = _iso(now())
        _atomic_write(output_path, state.snapshot(follow=follow))

    next_health = monotonic()
    last_write = monotonic()

    def maybe_health() -> bool:
        nonlocal next_health
        current = monotonic()
        if health_url is None or not state.health_due() or current < next_health:
            return False
        checked_at = now()
        status, error = _health_status(opener, health_url, min(health_interval, 5.0))
        next_health = current + health_interval
        return state.health_result(checked_at=checked_at, status=status, error=error)

    write()
    try:
        if str(source) == "-":
            stream = stdin if stdin is not None else sys.stdin.buffer
            while not stop.is_set():
                ready = True
                try:
                    descriptor = stream.fileno()
                except (AttributeError, OSError):
                    descriptor = None
                if descriptor is not None:
                    try:
                        ready = bool(select.select([descriptor], [], [], poll_interval)[0])
                    except InterruptedError:
                        continue
                if ready:
                    try:
                        raw = stream.readline()
                    except OSError as error:
                        event("read_error", type(error).__name__)
                        break
                    if raw in (b"", ""):
                        event("input_ended")
                        break
                    try:
                        changed = state.process_line(raw)
                    except (UnicodeError, ValueError) as error:
                        event("read_error", type(error).__name__)
                        changed = False
                    if changed:
                        write()
                        last_write = monotonic()
                if maybe_health():
                    write()
                    last_write = monotonic()
                if monotonic() - last_write >= 1.0:
                    write()
                    last_write = monotonic()
                if descriptor is None and not ready:
                    sleeper(poll_interval)
        else:
            source_path = Path(source)
            stream: BinaryIO | None = None
            identity: tuple[int, int] | None = None
            resume_offset = state.byte_offset
            while not stop.is_set():
                if stream is None:
                    try:
                        info = source_path.stat()
                        stream = open(source_path, "rb")
                        new_identity = (info.st_dev, info.st_ino)
                        prior_identity = identity
                        if prior_identity is not None and new_identity != prior_identity:
                            event("file_replaced")
                        identity = new_identity
                        follow["input_identity"] = {"device": info.st_dev, "inode": info.st_ino}
                        old_identity = previous_follow.get("input_identity")
                        if (
                            prior_identity == new_identity
                            and info.st_size >= state.byte_offset
                        ):
                            stream.seek(state.byte_offset)
                        elif (
                            prior_identity is None
                            and resume_document is not None
                            and old_identity == follow["input_identity"]
                            and info.st_size >= resume_offset
                        ):
                            stream.seek(resume_offset)
                        else:
                            if prior_identity is None and resume_document is not None:
                                event("resume_source_changed")
                            state.byte_offset = 0
                    except OSError as error:
                        event("read_error", type(error).__name__)
                        if monotonic() - last_write >= 1.0:
                            write()
                            last_write = monotonic()
                        sleeper(poll_interval)
                        continue
                while not stop.is_set():
                    position = stream.tell()
                    try:
                        raw = stream.readline()
                    except OSError as error:
                        event("read_error", type(error).__name__)
                        stream.close()
                        stream = None
                        break
                    if not raw:
                        break
                    if not raw.endswith(b"\n"):
                        stream.seek(position)
                        break
                    try:
                        changed = state.process_line(raw, byte_offset=stream.tell())
                    except (UnicodeError, ValueError) as error:
                        event("read_error", type(error).__name__)
                        changed = False
                    if changed:
                        write()
                        last_write = monotonic()
                    if maybe_health():
                        write()
                        last_write = monotonic()
                if stream is not None:
                    try:
                        info = source_path.stat()
                        current_identity = (info.st_dev, info.st_ino)
                        if current_identity != identity:
                            event("file_replaced")
                            stream.close()
                            stream = None
                            state.byte_offset = 0
                        elif info.st_size < stream.tell():
                            event("file_truncated")
                            stream.seek(0)
                            state.byte_offset = 0
                    except OSError as error:
                        event("read_error", type(error).__name__)
                        stream.close()
                        stream = None
                if maybe_health():
                    write()
                    last_write = monotonic()
                if monotonic() - last_write >= 1.0:
                    write()
                    last_write = monotonic()
                sleeper(poll_interval)
            if stream is not None:
                stream.close()
    except KeyboardInterrupt:
        stop.set()
    if follow["state"] == "running":
        follow["state"] = "stopped"
    write()
    return state.snapshot(follow=follow)
