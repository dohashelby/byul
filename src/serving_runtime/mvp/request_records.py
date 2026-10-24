"""Request record construction and fail-closed JSONL store state."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any, Callable

from .mock_engine import _utc_now
from .policy import PolicyState
from .store import JsonlStore
from .validator import Decision


class RequestRecords:
    """Own request-local context and the gateway request record store."""

    def __init__(
        self,
        data_dir: str | Path,
        observation_session_id: str,
        policy_state: Callable[[], PolicyState],
        backend_kind: str,
        environment: str | None = None,
        run_id: str | None = None,
        target_context: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._target_context = target_context
        self.store = JsonlStore(Path(data_dir) / "gateway_records.jsonl")
        self._data_dir = str(data_dir)
        self._observation_session_id = observation_session_id
        self._policy_state = policy_state
        self._backend_kind = backend_kind
        self._environment = environment
        self._run_id = run_id
        self._state_lock = threading.Lock()
        self._store_state = "unknown"
        self._last_error: str | None = None
        self._last_error_at: str | None = None
        self._request_local = threading.local()
        self.check_store()

    def clear_context(self) -> None:
        self._request_local.context = None
        self._request_local.auth = None

    def set_auth(self, auth: dict[str, Any]) -> None:
        """Attach the authentication outcome (never the credential) to this request."""

        self._request_local.auth = auth

    def set_context(self, context: dict[str, Any]) -> None:
        self._request_local.context = context

    def check_store(self) -> None:
        try:
            self.store.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with self.store.path.open("ab"):
                pass
        except OSError as error:
            with self._state_lock:
                self._store_state = "error"
                self._last_error = str(error)
                self._last_error_at = _utc_now()
            return
        with self._state_lock:
            self._store_state = "ok"

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "data_dir": self._data_dir,
                "state": self._store_state,
                "ok": self._store_state == "ok",
                "last_error": self._last_error,
                "last_error_at": self._last_error_at,
            }

    def append(self, record: dict[str, Any]) -> bool:
        try:
            self.store.append(record)
        except OSError as error:
            with self._state_lock:
                self._store_state = "error"
                self._last_error = str(error)
                self._last_error_at = _utc_now()
            return False
        with self._state_lock:
            self._store_state = "ok"
        return True

    def build(
        self,
        phase: str,
        request_id: str,
        decision: Decision,
        *,
        attempted: bool,
        backend_receipt: str,
        response: str,
        error_code: str | None,
        stream: bool = False,
        http_start_status: int | None = None,
        final_outcome: str | None = None,
        response_started: bool = False,
        backend_cancel: str | None = None,
    ) -> dict[str, Any]:
        policy_state = self._policy_state()
        policy = policy_state.policy
        return {
            "schema_version": "runtime-mvp-record/1.0",
            "phase": phase,
            "request_id": request_id,
            "observation_session_id": self._observation_session_id,
            "environment": self._environment,
            "run_id": self._run_id,
            "target_id": policy.target_id if policy else None,
            "target": (
                self._target_context()
                if self._target_context is not None
                else {"registration": "not_configured", "target_id": None, "instance_id": None, "instance_state": "not_configured"}
            ),
            "recorded_at": _utc_now(),
            "policy": {
                "schema_version": policy.schema_version if policy else None,
                "profile": policy.profile if policy else None,
                "policy_id": policy.policy_id if policy else None,
                "policy_version": policy.policy_version if policy else None,
                "sha256": policy_state.sha256,
                "path": policy_state.path,
            },
            "auth": getattr(self._request_local, "auth", None)
            or {"state": "not_evaluated", "method": None, "principal_id": None, "roles": []},
            "decision": {
                "result": decision.result,
                "reason_code": decision.reason_code,
                "rule_id": decision.rule_id,
                "message": decision.message,
                "http_status": decision.http_status,
            },
            "request": {
                "model": decision.model,
                "max_tokens": decision.max_tokens,
                "n": decision.n,
                # Test-only extra fields: names and modes only, never values.
                "extra_fields": [
                    {"name": name, "mode": mode}
                    for name, mode in decision.extra_fields
                ],
                "context": getattr(self._request_local, "context", None),
            },
            "delivery": {
                "attempted": attempted,
                "backend_receipt": backend_receipt,
                "response": response,
                "error_code": error_code,
            },
            "stream": stream,
            "http_start_status": http_start_status,
            "final_outcome": final_outcome,
            "response_started": response_started,
            "backend_cancel": (
                backend_cancel
                if backend_cancel is not None
                else (
                    "not_applicable"
                    if self._backend_kind == "mock"
                    else "not_observed"
                )
            ),
            "backend_kind": self._backend_kind,
            "data_origin": (
                "actual_test"
                if self._backend_kind == "mock"
                else "external_backend"
            ),
        }
