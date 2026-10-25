"""Detect, persist, query, and export revalidation requests.

The records in this module only ask an existing validation tool or a person to
look again.  They do not approve, close, or transmit a request.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any
from uuid import uuid4

from .store import JsonlStore


SCHEMA_VERSION = "runtime-revalidation-request/1.0"
EXPORT_SCHEMA_VERSION = "runtime-revalidation-export/0.1"
EXPORT_FORMAT_STATUS = "검증 도구 연동 형식 초안(D-03 미합의)"
_EVENTS = frozenset(
    {"opened", "condition_cleared", "condition_reappeared", "superseded"}
)
_INACTIVE_EVENTS = frozenset({"condition_cleared", "superseded"})
_ASSET_STATES = ("changed", "missing", "unreadable", "symlink")
_MAX_PATHS = 50


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_hash(trigger: str, evidence: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"trigger": trigger, "evidence": evidence},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _string_paths(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({value for value in values if isinstance(value, str)})[:_MAX_PATHS]


@dataclass(frozen=True)
class Condition:
    """One currently observed reason to request revalidation."""

    trigger: str
    scope: str
    fingerprint: str
    evidence: dict[str, Any]
    data_origin: str | None


@dataclass(frozen=True)
class Evaluation:
    """Conditions and scopes for which the latest observation is conclusive."""

    trigger: str
    conditions: tuple[Condition, ...]
    observed_origins: dict[str, str | None]


def evaluate_asset_integrity(
    result: dict[str, Any], data_origin: str | None
) -> Evaluation:
    """Turn one complete asset check into zero or one condition."""

    if not isinstance(result, dict) or not isinstance(result.get("intact"), bool):
        raise ValueError("asset_integrity result must contain boolean intact")
    scope = "asset_integrity"
    observed = {scope: data_origin}
    if result["intact"]:
        return Evaluation("asset_changed", (), observed)

    items = result.get("items")
    if not isinstance(items, list):
        raise ValueError("asset_integrity result must contain items")
    paths: dict[str, list[str]] = {state: [] for state in _ASSET_STATES}
    for item in items:
        if not isinstance(item, dict):
            continue
        state = item.get("state")
        path = item.get("path")
        if state in paths and isinstance(path, str):
            paths[state].append(path)
    for state in paths:
        paths[state] = sorted(set(paths[state]))[:_MAX_PATHS]
    paths["unlisted_file"] = _string_paths(result.get("extra_files"))
    paths["unlisted_symlink"] = _string_paths(result.get("extra_symlinks"))
    walk_errors = result.get("walk_errors")
    paths["walk_error"] = (
        sorted(
            {
                item.get("path")
                for item in walk_errors
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            }
        )[:_MAX_PATHS]
        if isinstance(walk_errors, list)
        else []
    )

    raw_counts = result.get("counts")
    counts = {
        state: (
            raw_counts.get(state, 0)
            if isinstance(raw_counts, dict)
            and isinstance(raw_counts.get(state, 0), int)
            and not isinstance(raw_counts.get(state, 0), bool)
            else len(paths[state])
        )
        for state in _ASSET_STATES
    }
    counts.update(
        {
            "unlisted_file": result.get("extra_file_count", len(paths["unlisted_file"])),
            "unlisted_symlink": result.get(
                "extra_symlink_count", len(paths["unlisted_symlink"])
            ),
            "walk_error": result.get("walk_error_count", len(paths["walk_error"])),
        }
    )
    for name, value in tuple(counts.items()):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            counts[name] = len(paths[name])
    evidence = {
        "paths": paths,
        "counts": counts,
        "manifest_sha256": result.get("manifest_sha256"),
        "checked_at": result.get("checked_at"),
    }
    identity = {
        "paths": paths,
        "counts": counts,
        "manifest_sha256": result.get("manifest_sha256"),
    }
    condition = Condition(
        "asset_changed",
        scope,
        _canonical_hash("asset_changed", identity),
        evidence,
        data_origin,
    )
    return Evaluation("asset_changed", (condition,), observed)


def evaluate_config_drift(comparison: dict[str, Any]) -> Evaluation:
    """Select conclusive engine_api drift/match rows from a baseline comparison."""

    if not isinstance(comparison, dict) or not isinstance(comparison.get("rows"), list):
        raise ValueError("baseline comparison must contain rows")
    conditions: list[Condition] = []
    observed: dict[str, str | None] = {}
    for row in comparison["rows"]:
        if not isinstance(row, dict) or row.get("source") != "engine_api":
            continue
        field = row.get("field")
        if not isinstance(field, str) or not field:
            raise ValueError("engine_api baseline row must contain field")
        state = row.get("state")
        if state not in {"match", "drift"}:
            # A missing, failed, stale, or never-collected observation does not
            # prove that an earlier drift has cleared.
            continue
        scope = f"engine_api:{field}"
        origin = row.get("data_origin")
        origin = origin if isinstance(origin, str) else None
        observed[scope] = origin
        if state == "match":
            continue
        evidence = {
            "field": field,
            "expected": row.get("expected"),
            "observed": row.get("observed"),
            "source": "engine_api",
            "observed_at": row.get("observed_at"),
        }
        identity = {
            name: evidence[name]
            for name in ("field", "expected", "observed", "source")
        }
        conditions.append(
            Condition(
                "config_drift",
                scope,
                _canonical_hash("config_drift", identity),
                evidence,
                origin,
            )
        )
    return Evaluation("config_drift", tuple(conditions), observed)


def _policy_ref(value: object) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    return {
        "policy_id": value.get("policy_id"),
        "policy_version": value.get("policy_version"),
        "sha256": value.get("sha256"),
    }


def evaluate_policy_history(record: dict[str, Any]) -> Evaluation:
    """Evaluate the policy history record just written for this startup."""

    changed = record.get("same_version_content_changed")
    if not isinstance(changed, bool):
        raise ValueError("policy history record must contain change decision")
    scope = "policy_content"
    observed = {scope: "local_file"}
    if not changed:
        return Evaluation("policy_content_changed", (), observed)
    evidence = {
        "previous": _policy_ref(record.get("previous")),
        "current": _policy_ref(record),
    }
    condition = Condition(
        "policy_content_changed",
        scope,
        _canonical_hash("policy_content_changed", evidence),
        evidence,
        "local_file",
    )
    return Evaluation("policy_content_changed", (condition,), observed)


def _scope(record: dict[str, Any]) -> str | None:
    trigger = record.get("trigger")
    evidence = record.get("evidence")
    if trigger == "asset_changed":
        return "asset_integrity"
    if trigger == "policy_content_changed":
        return "policy_content"
    if trigger == "config_drift" and isinstance(evidence, dict):
        field = evidence.get("field")
        if isinstance(field, str):
            return f"engine_api:{field}"
    return None


def _valid_record(record: dict[str, Any]) -> bool:
    event = record.get("event")
    return (
        record.get("schema_version") == SCHEMA_VERSION
        and event in _EVENTS
        and isinstance(record.get("revalidation_id"), str)
        and isinstance(record.get("fingerprint"), str)
        and isinstance(record.get("evidence"), dict)
        and record.get("status") == "open"
        and (event != "superseded" or isinstance(record.get("superseded_by"), str))
    )


def _request_groups(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if _valid_record(record):
            groups.setdefault(record["revalidation_id"], []).append(record)
    return groups


def _evidence_summary(trigger: str, evidence: dict[str, Any]) -> str:
    if trigger == "asset_changed":
        counts = evidence.get("counts") if isinstance(evidence.get("counts"), dict) else {}
        labels = (
            ("changed", "변경"),
            ("missing", "누락"),
            ("unreadable", "읽기 불가"),
            ("symlink", "링크"),
            ("unlisted_file", "목록 밖 파일"),
            ("unlisted_symlink", "목록 밖 링크"),
            ("walk_error", "순회 오류"),
        )
        return " · ".join(f"{label} {counts.get(name, 0)}" for name, label in labels)
    if trigger == "config_drift":
        return (
            f"{evidence.get('field', '–')}: "
            f"{evidence.get('expected', '–')} → {evidence.get('observed', '–')}"
        )
    if trigger == "policy_content_changed":
        previous = _policy_ref(evidence.get("previous"))
        current = _policy_ref(evidence.get("current"))
        return (
            f"{current.get('policy_id') or '–'} {current.get('policy_version') or '–'}: "
            f"{(previous.get('sha256') or '–')[:12]} → {(current.get('sha256') or '–')[:12]}"
        )
    return trigger


class RevalidationRequests:
    """Persist append-only request events and reconcile repeated observations."""

    def __init__(self, data_dir: str | Path) -> None:
        self.store = JsonlStore(Path(data_dir) / "revalidation_requests.jsonl")
        self._lock = threading.RLock()
        self._storage_state = "ok"
        self._storage_error: str | None = None
        self._storage_error_at: str | None = None
        self._source_status: dict[str, dict[str, Any]] = {}
        self._errors: list[dict[str, Any]] = []
        try:
            self.store.read_all()
        except OSError as error:
            self._set_storage_error(error)

    def _set_storage_error(self, error: Exception) -> None:
        self._storage_state = "error"
        self._storage_error = str(error)
        self._storage_error_at = _utc_now()

    def _set_source_ok(self, source: str) -> None:
        self._source_status[source] = {
            "state": "ok",
            "last_evaluated_at": _utc_now(),
            "last_error": None,
            "last_error_at": None,
        }

    def record_evaluation_error(self, source: str, error: Exception) -> None:
        """Expose an evaluation failure without raising into the Gateway path."""

        at = _utc_now()
        item = {
            "source": source,
            "at": at,
            "error": type(error).__name__,
            "message": str(error),
        }
        with self._lock:
            self._errors.append(item)
            self._errors = self._errors[-20:]
            self._source_status[source] = {
                "state": "error",
                "last_evaluated_at": at,
                "last_error": item["message"],
                "last_error_at": at,
            }

    def observe_asset(
        self,
        result: dict[str, Any],
        *,
        data_origin: str | None,
        observation_session_id: str,
        target_id: str | None,
        policy: dict[str, Any],
    ) -> None:
        self._safe_observe(
            "asset_integrity",
            lambda: evaluate_asset_integrity(result, data_origin),
            observation_session_id,
            target_id,
            policy,
        )

    def observe_baseline(
        self,
        comparison: dict[str, Any],
        *,
        observation_session_id: str,
        target_id: str | None,
        policy: dict[str, Any],
    ) -> None:
        self._safe_observe(
            "baseline",
            lambda: evaluate_config_drift(comparison),
            observation_session_id,
            target_id,
            policy,
        )

    def observe_policy_history(
        self,
        record: dict[str, Any],
        *,
        observation_session_id: str,
        target_id: str | None,
        policy: dict[str, Any],
    ) -> None:
        self._safe_observe(
            "policy_history",
            lambda: evaluate_policy_history(record),
            observation_session_id,
            target_id,
            policy,
        )

    def _safe_observe(
        self,
        source: str,
        evaluator: Callable[[], Evaluation],
        observation_session_id: str,
        target_id: str | None,
        policy: dict[str, Any],
    ) -> None:
        try:
            evaluation = evaluator()
            self._reconcile(
                evaluation,
                observation_session_id=observation_session_id,
                target_id=target_id,
                policy=policy,
            )
        except Exception as error:  # Evaluation/storage is observational and fail-open.
            self.record_evaluation_error(source, error)
            return
        with self._lock:
            self._set_source_ok(source)

    def _append(self, record: dict[str, Any]) -> None:
        try:
            self.store.append(record)
        except OSError as error:
            self._set_storage_error(error)
            raise
        self._storage_state = "ok"
        self._storage_error = None

    @staticmethod
    def _record(
        event: str,
        condition: Condition,
        revalidation_id: str,
        observation_session_id: str,
        target_id: str | None,
        policy: dict[str, Any],
        *,
        superseded_by: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "schema_version": SCHEMA_VERSION,
            "revalidation_id": revalidation_id,
            "event": event,
            "created_at": _utc_now(),
            "trigger": condition.trigger,
            "fingerprint": condition.fingerprint,
            "observation_session_id": observation_session_id,
            "target_id": target_id,
            "policy": {
                "policy_id": policy.get("policy_id"),
                "policy_version": policy.get("policy_version"),
                "sha256": policy.get("sha256"),
            },
            "evidence": condition.evidence,
            "status": "open",
            "data_origin": condition.data_origin,
        }
        if event == "superseded":
            if superseded_by is None:
                raise ValueError("superseded event requires superseded_by")
            record["superseded_by"] = superseded_by
        return record

    def _reconcile(
        self,
        evaluation: Evaluation,
        *,
        observation_session_id: str,
        target_id: str | None,
        policy: dict[str, Any],
    ) -> None:
        with self._lock:
            try:
                records, _ = self.store.read_all()
            except OSError as error:
                self._set_storage_error(error)
                raise
            groups = _request_groups(records)
            latest = {request_id: events[-1] for request_id, events in groups.items()}
            by_fingerprint: dict[str, tuple[str, dict[str, Any]]] = {}
            for request_id, event in latest.items():
                if event.get("trigger") == evaluation.trigger:
                    by_fingerprint[event["fingerprint"]] = (request_id, event)

            current = {
                condition.fingerprint: condition for condition in evaluation.conditions
            }
            current_by_scope = {
                condition.scope: condition for condition in evaluation.conditions
            }
            current_request_ids = {
                fingerprint: (
                    by_fingerprint[fingerprint][0]
                    if fingerprint in by_fingerprint
                    else uuid4().hex
                )
                for fingerprint in current
            }
            observed_scopes = set(evaluation.observed_origins)
            for request_id, event in tuple(latest.items()):
                scope = _scope(event)
                if (
                    event.get("trigger") != evaluation.trigger
                    or scope not in observed_scopes
                    or event.get("fingerprint") in current
                    or event.get("event") in _INACTIVE_EVENTS
                ):
                    continue
                condition = Condition(
                    evaluation.trigger,
                    scope or "",
                    event["fingerprint"],
                    event["evidence"],
                    evaluation.observed_origins.get(scope or ""),
                )
                replacement = current_by_scope.get(scope or "")
                if replacement is None:
                    replacement_event = "condition_cleared"
                    superseded_by = None
                else:
                    replacement_event = "superseded"
                    superseded_by = current_request_ids[replacement.fingerprint]
                self._append(
                    self._record(
                        replacement_event,
                        condition,
                        request_id,
                        observation_session_id,
                        target_id,
                        policy,
                        superseded_by=superseded_by,
                    )
                )

            for condition in evaluation.conditions:
                existing = by_fingerprint.get(condition.fingerprint)
                if existing is None:
                    self._append(
                        self._record(
                            "opened",
                            condition,
                            current_request_ids[condition.fingerprint],
                            observation_session_id,
                            target_id,
                            policy,
                        )
                    )
                elif existing[1].get("event") in _INACTIVE_EVENTS:
                    self._append(
                        self._record(
                            "condition_reappeared",
                            condition,
                            existing[0],
                            observation_session_id,
                            target_id,
                            policy,
                        )
                    )

    def summary(self) -> dict[str, Any]:
        """Return latest state for every still-open request ID."""

        with self._lock:
            try:
                records, corrupt = self.store.read_all()
            except OSError as error:
                self._set_storage_error(error)
                records, corrupt = [], []
            groups = _request_groups(records)
            requests: list[dict[str, Any]] = []
            for request_id, events in groups.items():
                first = events[0]
                latest = events[-1]
                trigger = latest["trigger"]
                evidence = latest["evidence"]
                current_evidence_present = latest["event"] not in _INACTIVE_EVENTS
                if latest["event"] == "superseded":
                    condition_state = "superseded"
                    condition_state_label = "다른 근거로 대체됨, 해소 아님"
                elif latest["event"] == "condition_cleared":
                    condition_state = "cleared"
                    condition_state_label = "해소됨"
                else:
                    condition_state = "current"
                    condition_state_label = "있음"
                requests.append(
                    {
                        "revalidation_id": request_id,
                        "status": "open",
                        "trigger": trigger,
                        "fingerprint": latest["fingerprint"],
                        "opened_at": first["created_at"],
                        "updated_at": latest["created_at"],
                        "observation_session_id": latest.get("observation_session_id"),
                        "target_id": latest.get("target_id"),
                        "policy": latest.get("policy"),
                        "data_origin": latest.get("data_origin"),
                        "event_count": len(events),
                        "last_event": latest["event"],
                        "current_evidence_present": current_evidence_present,
                        "condition_state": condition_state,
                        "condition_state_label": condition_state_label,
                        "superseded_by": latest.get("superseded_by"),
                        "evidence": evidence,
                        "evidence_summary": _evidence_summary(trigger, evidence),
                    }
                )
            requests.sort(key=lambda item: item["opened_at"], reverse=True)
            source_status = {name: dict(value) for name, value in self._source_status.items()}
            evaluation_state = (
                "error"
                if any(item.get("state") == "error" for item in source_status.values())
                else "ok"
            )
            return {
                "schema_version": SCHEMA_VERSION,
                "requests": requests,
                "open_count": len(requests),
                "current_condition_count": sum(
                    1 for item in requests if item["current_evidence_present"]
                ),
                "corrupt_lines": corrupt[:20],
                "evaluation_errors": list(self._errors),
                "evaluation_status": {
                    "state": evaluation_state,
                    "ok": evaluation_state == "ok",
                    "sources": source_status,
                },
                "storage": {
                    "path": str(self.store.path),
                    "state": self._storage_state,
                    "ok": self._storage_state == "ok",
                    "last_error": self._storage_error,
                    "last_error_at": self._storage_error_at,
                },
                "generated_at": _utc_now(),
            }


def export_requests(data_dir: str | Path, out: str | Path) -> dict[str, Any]:
    """Write an exclusive, read-only integration draft containing open requests."""

    summary = RevalidationRequests(data_dir).summary()
    document = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "format_status": EXPORT_FORMAT_STATUS,
        "generated_at": _utc_now(),
        "source_schema_version": SCHEMA_VERSION,
        "open_count": summary["open_count"],
        "current_condition_count": summary["current_condition_count"],
        "requests": summary["requests"],
        "corrupt_lines": summary["corrupt_lines"],
    }
    encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor = os.open(
        Path(out),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return document
