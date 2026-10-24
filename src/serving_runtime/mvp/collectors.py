"""Background collectors and baseline/current comparison for the local MVP."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Any

from .policy import DuplicateKeyError, _object_without_duplicates, _reject_json_constant


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NoCollectionUpdate(RuntimeError):
    """A source is reachable but has no new observation to count as success."""

    def __init__(
        self,
        message: str,
        *,
        last_value: Any = None,
        data_origin: str | None = None,
    ) -> None:
        super().__init__(message)
        self.last_value = last_value
        self.data_origin = data_origin


class Collector:
    """One polling source that retains its last successful observation."""

    def __init__(
        self,
        source: str,
        fetch: Callable[[], Any] | None,
        interval_s: float = 5,
        stale_after_s: float = 15,
        dedicated_thread: bool = False,
        on_collected: Callable[[Any, str | None], None] | None = None,
    ) -> None:
        if interval_s <= 0 or stale_after_s <= 0:
            raise ValueError("collector intervals must be positive")
        self.source = source
        self.fetch = fetch
        self.interval_s = float(interval_s)
        self.stale_after_s = float(stale_after_s)
        self.dedicated_thread = dedicated_thread
        self.on_collected = on_collected
        self.last_attempt_at: str | None = None
        self.last_success_at: str | None = None
        self.last_error: str | None = None
        self.last_value: Any = None
        self.data_origin: str | None = None
        self._last_attempt_monotonic: float | None = None
        self._last_success_monotonic: float | None = None
        self._lock = threading.Lock()

    def collect(self) -> None:
        if self.fetch is None:
            return
        attempted_at = _utc_now()
        attempted_monotonic = time.monotonic()
        try:
            fetched = self.fetch()
            if isinstance(fetched, tuple) and len(fetched) == 2:
                value, data_origin = fetched
            else:
                value = fetched
                data_origin = fetched.get("data_origin") if isinstance(fetched, dict) else None
        except NoCollectionUpdate as idle:
            # Do not move last_success_at or clear an earlier real error. A
            # feed without independent writer evidence therefore becomes
            # stale instead of being refreshed as healthy by an empty read.
            with self._lock:
                self.last_attempt_at = attempted_at
                self._last_attempt_monotonic = attempted_monotonic
                if idle.last_value is not None:
                    self.last_value = idle.last_value
                if idle.data_origin is not None:
                    self.data_origin = idle.data_origin
            return
        except Exception as error:  # Polling failures are state, not thread failures.
            with self._lock:
                self.last_attempt_at = attempted_at
                self._last_attempt_monotonic = attempted_monotonic
                self.last_error = str(error)
            return
        completed_at = _utc_now()
        completed_monotonic = time.monotonic()
        with self._lock:
            self.last_attempt_at = attempted_at
            self.last_success_at = completed_at
            self.last_error = None
            self.last_value = value
            self.data_origin = data_origin if isinstance(data_origin, str) else None
            self._last_attempt_monotonic = attempted_monotonic
            self._last_success_monotonic = completed_monotonic
        if self.on_collected is not None:
            try:
                self.on_collected(value, self.data_origin)
            except Exception:
                # Observation consumers must not turn a successful collection
                # into a collector failure or stop its polling thread.
                pass

    def due(self, now: float | None = None) -> bool:
        with self._lock:
            last = self._last_attempt_monotonic
        return last is None or (now or time.monotonic()) - last >= self.interval_s

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            last_attempt_at = self.last_attempt_at
            last_success_at = self.last_success_at
            last_error = self.last_error
            last_value = self.last_value
            data_origin = self.data_origin
            success_monotonic = self._last_success_monotonic
        if self.fetch is None:
            state = "disconnected"
        elif success_monotonic is not None and time.monotonic() - success_monotonic > self.stale_after_s:
            state = "stale"
        elif last_error is not None:
            state = "error"
        elif success_monotonic is not None:
            state = "ok"
        else:
            state = "never"
        return {
            "source": self.source,
            "state": state,
            "interval_s": self.interval_s,
            "stale_after_s": self.stale_after_s,
            "last_attempt_at": last_attempt_at,
            "last_success_at": last_success_at,
            "last_error": last_error,
            "last_value": last_value,
            "data_origin": data_origin,
        }


class CollectorManager:
    """Poll ordinary sources together and slow sources on dedicated threads."""

    def __init__(self, collectors: list[Collector]) -> None:
        self.collectors = collectors
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._dedicated_threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        for collector in self.collectors:
            if collector.dedicated_thread:
                continue
            collector.collect()
        self._thread = threading.Thread(
            target=self._run, name="mvp-collectors", daemon=True
        )
        self._thread.start()
        self._dedicated_threads = [
            threading.Thread(
                target=self._run_dedicated,
                args=(collector,),
                name=f"mvp-collector-{collector.source}",
                daemon=True,
            )
            for collector in self.collectors
            if collector.dedicated_thread and collector.fetch is not None
        ]
        for thread in self._dedicated_threads:
            thread.start()

    def _run(self) -> None:
        while not self._stop.wait(0.05):
            now = time.monotonic()
            for collector in self.collectors:
                if not collector.dedicated_thread and collector.due(now):
                    collector.collect()

    def _run_dedicated(self, collector: Collector) -> None:
        while not self._stop.is_set():
            collector.collect()
            # The interval starts when the preceding collection has finished.
            if self._stop.wait(collector.interval_s):
                return

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        thread.join(timeout=5)
        for dedicated in self._dedicated_threads:
            dedicated.join(timeout=5)
        self._thread = None
        self._dedicated_threads = []

    def snapshots(self) -> list[dict[str, Any]]:
        return [collector.snapshot() for collector in self.collectors]

    def get(self, source: str) -> dict[str, Any] | None:
        return next(
            (collector.snapshot() for collector in self.collectors if collector.source == source),
            None,
        )


@dataclass(frozen=True)
class DeclarationState:
    state: str
    path: str | None
    fields: dict[str, Any]
    loaded_at: str
    sha256: str | None = None
    error_code: str | None = None
    error_message: str | None = None


def load_declaration(path: str | Path | None, *, kind: str) -> DeclarationState:
    """Load a strict flat baseline or declared manifest."""

    loaded_at = _utc_now()
    if path is None:
        return DeclarationState("not_configured", None, {}, loaded_at)
    path_text = str(path)
    try:
        raw = Path(path).read_bytes()
    except (OSError, ValueError) as error:
        return DeclarationState(
            "error", path_text, {}, loaded_at, error_code=f"{kind.upper()}_READ_ERROR",
            error_message=str(error),
        )
    digest = hashlib.sha256(raw).hexdigest()
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, DuplicateKeyError, json.JSONDecodeError, ValueError, RecursionError) as error:
        return DeclarationState(
            "error", path_text, {}, loaded_at, digest,
            f"{kind.upper()}_MALFORMED", str(error),
        )
    schemas = {"runtime-mvp-baseline/1.0"}
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "fields"}
        or document.get("schema_version") not in schemas
        or not isinstance(document.get("fields"), dict)
    ):
        return DeclarationState(
            "error", path_text, {}, loaded_at, digest,
            f"INVALID_{kind.upper()}", f"invalid {kind} document",
        )
    fields = document["fields"]
    if any(
        not isinstance(name, str)
        or not name
        or value is None
        or isinstance(value, (dict, list))
        for name, value in fields.items()
    ):
        return DeclarationState(
            "error", path_text, {}, loaded_at, digest,
            f"INVALID_{kind.upper()}", f"{kind} fields must be flat scalar values",
        )
    return DeclarationState("ok", path_text, dict(fields), loaded_at, digest)


_ENGINE_API_FIELDS = frozenset(
    {"engine.kind", "engine.version", "model.id", "option.max_model_len"}
)
_MODEL_ID_NOTE = (
    "model.id 일치는 weight 해시·revision·메모리 로딩을 증명하지 않음"
)


def compare_baseline(
    baseline: DeclarationState,
    manifest: DeclarationState,
    collectors: CollectorManager,
) -> dict[str, Any]:
    """Compare approved expectations to declared and collected current values."""

    rows: list[dict[str, Any]] = []
    engine_info = collectors.get("engine_info")
    for field, expected in baseline.fields.items():
        declared = manifest.fields.get(field)
        if manifest.state == "not_configured":
            declared_state = "not_collected"
        elif manifest.state == "error":
            declared_state = "error"
        elif field not in manifest.fields:
            declared_state = "missing"
        else:
            declared_state = "match" if declared == expected else "drift"
        rows.append(
            {
                "field": field,
                "expected": expected,
                "observed": declared,
                "source": "declared_manifest",
                "observed_at": manifest.loaded_at if field in manifest.fields else None,
                "state": declared_state,
                "data_origin": "declared" if field in manifest.fields else None,
            }
        )
        if field not in _ENGINE_API_FIELDS:
            rows.append(
                {
                    "field": field,
                    "expected": expected,
                    "observed": None,
                    "source": "engine_api",
                    "observed_at": None,
                    "state": "not_collected",
                    "data_origin": None,
                }
            )
            continue
        collector_state = engine_info.get("state") if engine_info else "disconnected"
        value = engine_info.get("last_value") if engine_info else None
        observed = value.get(field) if isinstance(value, dict) else None
        if collector_state in {"error", "stale"}:
            observed_state = collector_state
        elif collector_state in {"never", "disconnected"}:
            observed_state = "not_collected"
        elif not isinstance(value, dict) or field not in value:
            observed_state = "missing"
        else:
            observed_state = "match" if observed == expected else "drift"
        rows.append(
            {
                "field": field,
                "expected": expected,
                "observed": observed,
                "source": "engine_api",
                "observed_at": engine_info.get("last_success_at") if engine_info else None,
                "state": observed_state,
                "data_origin": engine_info.get("data_origin") if engine_info else None,
            }
        )
    return {
        "baseline": declaration_view(baseline),
        "manifest": declaration_view(manifest),
        "rows": rows,
        "model_id_limit": _MODEL_ID_NOTE,
        "generated_at": _utc_now(),
    }


def declaration_view(state: DeclarationState) -> dict[str, Any]:
    return {
        "state": state.state,
        "path": state.path,
        "sha256": state.sha256,
        "loaded_at": state.loaded_at,
        "error_code": state.error_code,
        "error_message": state.error_message,
    }


MODEL_ID_LIMIT_NOTE = _MODEL_ID_NOTE
