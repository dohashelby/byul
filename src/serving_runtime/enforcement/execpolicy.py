"""SP3 phase-aware execution classification and SP1 cache-generation tagging.

Classifies observed Tetragon events against the 1.1 intent: which phase was
active (from a declared phase timeline), whether the binary is approved for
that phase, whether an approved, time-bounded exception covers it, and whether
it ran from the cache. This is observation and alerting only; nothing here
blocks a process, and no binary (Python, workers, compilers) is ignored
wholesale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import threading
from typing import Any

from .intent import PHASES, EnforcementIntent, IntentError, _reject_constant, _unique_object


EXCEPTIONS_SCHEMA = "runtime-exec-exceptions/1.0"
TIMELINE_SCHEMA = "runtime-phase-timeline/1.0"
TIMELINE_PHASES = (*PHASES, "maintenance")
BASES = frozenset({"declared", "engine_signal", "test_controller"})
MAINTENANCE_MAX = timedelta(minutes=30)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _time(value: Any, path: str) -> datetime:
    if not isinstance(value, str):
        raise IntentError(f"{path} must be an ISO8601 string with a timezone")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise IntentError(f"{path} must be an ISO8601 string with a timezone") from error
    if parsed.tzinfo is None:
        raise IntentError(f"{path} must include a timezone")
    return parsed


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise IntentError(f"{path} must be a non-empty string")
    return value


def _abs(value: Any, path: str) -> str:
    text = _text(value, path)
    if not Path(text).is_absolute() or ".." in Path(text).parts:
        raise IntentError(f"{path} must be an absolute path without parent traversal")
    return text


def _read(path: str | Path) -> Any:
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise IntentError(f"could not read {path}: {type(error).__name__}") from error


@dataclass(frozen=True)
class ExecException:
    exception_id: str
    rule_ref: str
    kind: str  # maintenance | standing
    executable: str
    parent: str | None
    phase: str
    image_digest: str | None
    valid_from: datetime
    valid_until: datetime
    approval_ref: str

    def covers(self, *, binary: str, parent: str | None, image: str | None, phases: set[str], at: datetime | None) -> bool:
        if binary != self.executable or self.phase not in phases:
            return False
        if at is None or not self.valid_from <= at < self.valid_until:
            return False
        # Missing evidence never satisfies a condition the exception requires.
        if self.parent is not None and parent != self.parent:
            return False
        if self.image_digest is not None and image != self.image_digest:
            return False
        return True


@dataclass(frozen=True)
class PhaseEntry:
    phase: str
    start: datetime
    end: datetime | None
    basis: str

    def active(self, at: datetime) -> bool:
        return self.start <= at and (self.end is None or at < self.end)


def parse_exceptions(document: Any) -> tuple[ExecException, ...]:
    if not isinstance(document, dict) or set(document) - {"schema_version", "exceptions"}:
        raise IntentError("exceptions document must contain only schema_version and exceptions")
    if document.get("schema_version") != EXCEPTIONS_SCHEMA:
        raise IntentError(f"schema_version must be {EXCEPTIONS_SCHEMA}")
    raw = document.get("exceptions")
    if not isinstance(raw, list):
        raise IntentError("exceptions must be an array")
    allowed = {
        "exception_id", "rule_ref", "kind", "executable", "parent", "phase", "image_digest",
        "reason", "approved_by", "approval_ref", "valid_from", "valid_until",
    }
    result: list[ExecException] = []
    for index, item in enumerate(raw):
        path = f"exceptions[{index}]"
        if not isinstance(item, dict):
            raise IntentError(f"{path} must be an object")
        unknown = sorted(set(item) - allowed)
        if unknown:
            raise IntentError(f"{path} has unknown fields: {', '.join(unknown)}")
        exception_id = item.get("exception_id")
        if not isinstance(exception_id, str) or _ID.fullmatch(exception_id) is None:
            raise IntentError(f"{path}.exception_id is invalid")
        kind = item.get("kind")
        if kind not in {"maintenance", "standing"}:
            raise IntentError(f"{path}.kind must be maintenance or standing")
        phase = item.get("phase")
        if phase not in TIMELINE_PHASES:
            raise IntentError(f"{path}.phase must be one of {', '.join(TIMELINE_PHASES)}")
        image = item.get("image_digest")
        if image is not None and (not isinstance(image, str) or _DIGEST.fullmatch(image) is None):
            raise IntentError(f"{path}.image_digest must be sha256:<64 hex>")
        if kind == "standing" and image is None:
            raise IntentError(f"{path} standing exceptions must be bound to an image_digest")
        start = _time(item.get("valid_from"), f"{path}.valid_from")
        end = _time(item.get("valid_until"), f"{path}.valid_until")
        if end <= start:
            raise IntentError(f"{path}.valid_until must be after valid_from")
        if kind == "maintenance" and end - start > MAINTENANCE_MAX:
            raise IntentError(f"{path} maintenance exceptions may last at most 30 minutes")
        for name in ("reason", "approved_by", "approval_ref"):
            _text(item.get(name), f"{path}.{name}")
        result.append(
            ExecException(
                exception_id,
                _text(item.get("rule_ref"), f"{path}.rule_ref"),
                kind,
                _abs(item.get("executable"), f"{path}.executable"),
                _abs(item["parent"], f"{path}.parent") if item.get("parent") is not None else None,
                phase,
                image,
                start,
                end,
                item["approval_ref"],
            )
        )
    ids = [item.exception_id for item in result]
    if len(ids) != len(set(ids)):
        raise IntentError("exception_id values must be unique")
    return tuple(result)


def parse_timeline(document: Any) -> tuple[PhaseEntry, ...]:
    if not isinstance(document, dict) or set(document) - {"schema_version", "target_ref", "entries"}:
        raise IntentError("timeline must contain only schema_version, target_ref, entries")
    if document.get("schema_version") != TIMELINE_SCHEMA:
        raise IntentError(f"schema_version must be {TIMELINE_SCHEMA}")
    raw = document.get("entries")
    if not isinstance(raw, list):
        raise IntentError("entries must be an array")
    entries: list[PhaseEntry] = []
    for index, item in enumerate(raw):
        path = f"entries[{index}]"
        if not isinstance(item, dict) or set(item) - {"phase", "from", "until", "basis"}:
            raise IntentError(f"{path} must contain only phase, from, until, basis")
        phase = item.get("phase")
        if phase not in TIMELINE_PHASES:
            raise IntentError(f"{path}.phase must be one of {', '.join(TIMELINE_PHASES)}")
        basis = item.get("basis")
        if basis not in BASES:
            raise IntentError(f"{path}.basis must be one of {', '.join(sorted(BASES))}")
        start = _time(item.get("from"), f"{path}.from")
        end = _time(item["until"], f"{path}.until") if item.get("until") is not None else None
        if end is not None and end <= start:
            raise IntentError(f"{path}.until must be after from")
        entries.append(PhaseEntry(phase, start, end, basis))
    return tuple(entries)


class _ReloadingFile:
    """Re-read a small JSON file when its size or mtime changes."""

    def __init__(self, path: str | Path | None, parser) -> None:
        self.path = Path(path) if path is not None else None
        self.parser = parser
        self._signature: tuple[int, int] | None = None
        self.value: tuple = ()
        self.error: str | None = None

    def get(self) -> tuple:
        if self.path is None:
            return ()
        try:
            info = os.stat(self.path)
            signature = (info.st_mtime_ns, info.st_size)
        except OSError as error:
            self.value, self.error, self._signature = (), f"{type(error).__name__}", None
            return ()
        if signature != self._signature:
            try:
                self.value, self.error = self.parser(_read(self.path)), None
            except IntentError as error:
                # A broken file yields no exceptions and no phases: fail toward alerts.
                self.value, self.error = (), str(error)
            self._signature = signature
        return self.value


class ExecPolicy:
    def __init__(
        self,
        intent: EnforcementIntent,
        *,
        exceptions_path: str | Path | None = None,
        timeline_path: str | Path | None = None,
    ) -> None:
        self.intent = intent
        self._exceptions = _ReloadingFile(exceptions_path, parse_exceptions)
        self._timeline = _ReloadingFile(timeline_path, parse_timeline)
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        with self._lock:
            exceptions = self._exceptions.get()
            timeline = self._timeline.get()
            return {
                "exceptions": {
                    "path": str(self._exceptions.path) if self._exceptions.path else None,
                    "count": len(exceptions),
                    "error": self._exceptions.error,
                },
                "timeline": {
                    "path": str(self._timeline.path) if self._timeline.path else None,
                    "entries": len(timeline),
                    "error": self._timeline.error,
                },
            }

    def classify_exec(
        self,
        *,
        binary: str,
        parent: str | None,
        image: str | None,
        observed_at: str,
    ) -> dict[str, Any]:
        try:
            at: datetime | None = datetime.fromisoformat(observed_at)
        except ValueError:
            at = None
        with self._lock:
            timeline = self._timeline.get()
            exceptions = self._exceptions.get()
            timeline_error = self._timeline.error
        active = sorted({entry.phase for entry in timeline if at is not None and entry.active(at)})
        bases = sorted({entry.basis for entry in timeline if at is not None and entry.active(at)})
        intent = self.intent
        approved = set(intent.allowed_engine_binaries)
        for phase in active:
            approved |= set(intent.binaries_for(phase))
        cache_root = intent.assets.cache_root if intent.assets is not None else None
        from_cache = cache_root is not None and Path(binary).is_relative_to(cache_root)
        matched = next(
            (
                item
                for item in exceptions
                if item.covers(binary=binary, parent=parent, image=image, phases=set(active), at=at)
            ),
            None,
        )
        if matched is not None:
            verdict = "exception"
        elif from_cache:
            verdict = "exec_from_cache"
        elif binary in intent.suspect_binaries:
            verdict = "suspect_binary"
        elif not active:
            verdict = "phase_unknown"
        elif active == ["maintenance"]:
            # Maintenance work is allowed only through an approved exception.
            verdict = "outside_allowlist"
        elif binary in approved:
            verdict = "allowed_in_phase"
        else:
            verdict = "outside_allowlist"
        rule_refs = ["SP3-EXEC"]
        if from_cache:
            rule_refs.append("CACHE-03")
        return {
            "phase": active or ["unknown"],
            "phase_basis": bases,
            "phase_source_error": timeline_error,
            "exec_verdict": verdict,
            "exception_id": matched.exception_id if matched else None,
            "exception_kind": matched.kind if matched else None,
            "binary_location": "cache" if from_cache else "other",
            "parent_attribution": "available" if parent else "unavailable",
            "image_digest": image,
            "rule_refs": rule_refs,
        }

    def classify_cache_write(self, path: str) -> dict[str, Any]:
        assets = self.intent.assets
        if assets is None:
            return {}
        if Path(path).is_relative_to(assets.generation_path):
            generation = "current"
        elif Path(path).is_relative_to(assets.cache_root):
            generation = "other"
        else:
            generation = "outside"
        return {
            "cache_generation": generation,
            "rule_refs": ["CACHE-01", "CACHE-02"] if generation == "other" else ["CACHE-01"],
        }
