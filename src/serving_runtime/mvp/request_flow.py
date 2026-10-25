"""Validated chat request flow and concurrent-request accounting."""

from __future__ import annotations

import json
import threading
import time
from typing import Callable, Protocol

from .approval import ApprovalResult
from .auth import CHAT_ROUTE, AuthState, not_configured
from .backend import BackendConfig
from .engine_client import EngineClient
from .policy import PolicyState
from .policy_state import RevocationState, evaluate_policy
from .relay import Relay, StreamResponse, engine_error
from .request_records import RequestRecords
from .validator import Decision, validate_request


_INVALID_POLICY_BODY_LIMIT = 1024 * 1024


class ChatIO(StreamResponse, Protocol):
    """HTTP request/response operations needed by the chat flow."""

    def request_header(self, name: str) -> str | None: ...

    def read_request_body(self, length: int) -> bytes: ...

    def send_error_decision(
        self,
        decision: Decision,
        request_id: str,
        *,
        close_connection: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None: ...

    def send_response_body(
        self,
        status: int,
        content_type: str,
        body: bytes,
        *,
        request_id: str | None = None,
    ) -> None: ...


def transport_decision(status: int, code: str, message: str) -> Decision:
    return Decision(
        result="INVALID",
        http_status=status,
        reason_code=code,
        rule_id=None,
        message=message,
        model=None,
        max_tokens=None,
    )


class ConcurrencyLimiter:
    """Non-blocking request slot accounting shared by buffered and stream calls."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._in_flight = 0

    def try_acquire(self) -> bool:
        with self._lock:
            if self._in_flight >= self._limit:
                return False
            self._in_flight += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._in_flight <= 0:
                raise RuntimeError("in-flight request counter underflow")
            self._in_flight -= 1

    def status(self) -> dict[str, int]:
        with self._lock:
            return {"in_flight": self._in_flight, "limit": self._limit}


class RequestFlow:
    """Run one chat request without depending on the application object."""

    def __init__(
        self,
        backend: BackendConfig,
        policy_state: Callable[[], PolicyState],
        revocation_state: Callable[[], RevocationState],
        records: RequestRecords,
        engine_client: EngineClient,
        relay: Relay,
        concurrency: ConcurrencyLimiter,
        auth_state: Callable[[], AuthState] | None = None,
        target_hold: Callable[[str | None], tuple[str, str] | None] | None = None,
        approval_result: Callable[[], ApprovalResult | None] | None = None,
    ) -> None:
        self._auth_state = auth_state or not_configured
        self._target_hold = target_hold or (lambda _policy_target: None)
        self._backend = backend
        self._policy_state = policy_state
        self._revocation_state = revocation_state
        self._approval_result = approval_result or (lambda: None)
        self._records = records
        self._engine_client = engine_client
        self._relay = relay
        self._concurrency = concurrency

    def finish_without_relay(
        self,
        response: ChatIO,
        request_id: str,
        decision: Decision,
        *,
        close_connection: bool = False,
        stream: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._records.append(
            self._records.build(
                "final",
                request_id,
                decision,
                attempted=False,
                backend_receipt=self._engine_client.receipt_not_queried(),
                response="none",
                error_code=None,
                stream=stream,
                http_start_status=decision.http_status,
                final_outcome=decision.reason_code,
            )
        )
        response.send_error_decision(
            decision,
            request_id,
            close_connection=close_connection,
            extra_headers=extra_headers,
        )

    def record_without_relay(self, request_id: str, decision: Decision) -> None:
        """Record a transport rejection produced outside the normal chat path."""

        self._records.append(
            self._records.build(
                "final",
                request_id,
                decision,
                attempted=False,
                backend_receipt=self._engine_client.receipt_not_queried(),
                response="none",
                error_code=decision.reason_code,
                http_start_status=decision.http_status,
                final_outcome=decision.reason_code,
            )
        )

    def _context_decision(
        self,
        request_id: str,
        document: dict[str, object],
        decision: Decision,
        policy_snapshot: PolicyState,
    ) -> Decision | None:
        context_limit = (
            policy_snapshot.policy.max_context_tokens
            if policy_snapshot.policy is not None
            else None
        )
        if context_limit is None:
            return None
        try:
            backend_model_id = self._backend.mapped_model(decision.model)
            if backend_model_id is None:
                raise ValueError("backend model is unavailable")
            messages = document["messages"]
            if not isinstance(messages, list):
                raise ValueError("validated messages are unavailable")
            input_tokens = self._engine_client.count_input_tokens(
                request_id,
                messages,
                backend_model_id,
            )
        except Exception as error:
            # Store only the exception class; backend messages may contain secrets.
            self._records.set_context(
                {
                    "limit": context_limit,
                    "input_tokens": None,
                    "result": "unavailable",
                    "error": type(error).__name__,
                }
            )
            return engine_error(
                503,
                "CONTEXT_CHECK_UNAVAILABLE",
                "input token count is unavailable",
                decision,
                source="gateway",
            )
        total = input_tokens + (decision.max_tokens or 0)
        exceeded = total > context_limit
        self._records.set_context(
            {
                "limit": context_limit,
                "input_tokens": input_tokens,
                "requested_total": total,
                "result": "exceeded" if exceeded else "within_limit",
            }
        )
        if not exceeded:
            return None
        return Decision(
            result="DENIED",
            http_status=403,
            reason_code="CONTEXT_LIMIT_EXCEEDED",
            rule_id="max_context_tokens",
            message="input tokens plus max_tokens exceed the context limit",
            model=decision.model,
            max_tokens=decision.max_tokens,
        )

    @staticmethod
    def _auth_rejection(
        auth_state: AuthState, auth
    ) -> tuple[Decision, dict[str, str] | None] | None:
        """Map an authentication outcome to a pre-body rejection, if any."""

        if auth.state == "not_configured":
            return None
        if auth.state == "unavailable":
            # A broken auth config is a Gateway error, not a successful block.
            return (
                Decision(
                    result="ERROR",
                    http_status=503,
                    reason_code="AUTH_UNAVAILABLE",
                    rule_id=None,
                    message="authentication configuration is unavailable",
                    model=None,
                    max_tokens=None,
                ),
                None,
            )
        if auth.state in {"missing", "invalid"}:
            code = "AUTH_REQUIRED" if auth.state == "missing" else "AUTH_INVALID"
            message = (
                "authentication is required"
                if auth.state == "missing"
                else "authentication failed"
            )
            return (
                Decision(
                    result="DENIED",
                    http_status=401,
                    reason_code=code,
                    rule_id="authentication",
                    message=message,
                    model=None,
                    max_tokens=None,
                ),
                {"WWW-Authenticate": 'Bearer realm="serving-runtime"'},
            )
        required = set(auth_state.roles_for(CHAT_ROUTE))
        if auth.principal is None or not required & set(auth.principal.roles):
            return (
                Decision(
                    result="DENIED",
                    http_status=403,
                    reason_code="ROLE_NOT_ALLOWED",
                    rule_id="route_roles",
                    message="principal role is not allowed for this route",
                    model=None,
                    max_tokens=None,
                ),
                None,
            )
        return None

    def chat(self, response: ChatIO, request_id: str) -> None:
        self._records.clear_context()
        policy_snapshot = self._policy_state()
        policy_runtime = evaluate_policy(
            policy_snapshot,
            self._revocation_state(),
            approval=self._approval_result(),
        )
        if response.request_header("Transfer-Encoding") is not None:
            self.finish_without_relay(
                response,
                request_id,
                transport_decision(
                    400,
                    "UNSUPPORTED_TRANSFER_ENCODING",
                    "Transfer-Encoding is not supported",
                ),
                close_connection=True,
            )
            return

        raw_length = response.request_header("Content-Length")
        if raw_length is None or not raw_length or any(
            character not in "0123456789" for character in raw_length
        ):
            self.finish_without_relay(
                response,
                request_id,
                transport_decision(
                    400,
                    "INVALID_CONTENT_LENGTH",
                    "Content-Length must be a non-negative integer",
                ),
                close_connection=True,
            )
            return
        try:
            content_length = int(raw_length)
        except ValueError:
            self.finish_without_relay(
                response,
                request_id,
                transport_decision(
                    400,
                    "INVALID_CONTENT_LENGTH",
                    "Content-Length must be a non-negative integer",
                ),
                close_connection=True,
            )
            return

        policy = policy_snapshot.policy
        body_limit = (
            policy.max_body_bytes
            if policy_snapshot.ok and policy is not None
            else _INVALID_POLICY_BODY_LIMIT
        )
        if content_length > body_limit:
            self.finish_without_relay(
                response,
                request_id,
                transport_decision(
                    413,
                    "BODY_TOO_LARGE",
                    "request body exceeds the policy limit",
                ),
                close_connection=True,
            )
            return

        if not policy_runtime["gateway_active"]:
            unavailable = Decision(
                result="ERROR",
                http_status=503,
                reason_code=str(policy_runtime["reason_code"]),
                rule_id=None,
                message=str(policy_runtime["reason_message"]),
                model=None,
                max_tokens=None,
            )
            self.finish_without_relay(
                response, request_id, unavailable, close_connection=True
            )
            return

        auth_state = self._auth_state()
        auth = auth_state.authenticate(response.request_header("Authorization"))
        self._records.set_auth(auth.record())
        auth_rejection = self._auth_rejection(auth_state, auth)
        if auth_rejection is not None:
            decision, headers = auth_rejection
            self.finish_without_relay(
                response,
                request_id,
                decision,
                close_connection=True,
                extra_headers=headers,
            )
            return

        hold = self._target_hold(policy.target_id if policy is not None else None)
        if hold is not None:
            # Declared target conflicts hold the request; this is not a block result.
            self.finish_without_relay(
                response,
                request_id,
                Decision(
                    result="ERROR",
                    http_status=503,
                    reason_code=hold[0],
                    rule_id="target_registration",
                    message=hold[1],
                    model=None,
                    max_tokens=None,
                ),
                close_connection=True,
            )
            return

        body = response.read_request_body(content_length)
        incomplete = len(body) != content_length
        if incomplete:
            self.finish_without_relay(
                response,
                request_id,
                transport_decision(
                    400,
                    "INVALID_CONTENT_LENGTH",
                    "request body is shorter than Content-Length",
                ),
                close_connection=True,
            )
            return
        decision = validate_request(
            body, response.request_header("Content-Type"), policy_snapshot
        )
        stream = False
        document: dict[str, object] | None = None
        try:
            parsed_document = json.loads(body.decode("utf-8"))
            if isinstance(parsed_document, dict):
                document = parsed_document
                stream = parsed_document.get("stream") is True
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            pass
        if decision.result != "ALLOWED":
            self.finish_without_relay(
                response,
                request_id,
                decision,
                close_connection=incomplete,
                stream=stream,
            )
            return
        if document is None:
            self.finish_without_relay(
                response,
                request_id,
                transport_decision(400, "MALFORMED_JSON", "invalid JSON"),
            )
            return
        principal = auth.principal
        if (
            principal is not None
            and principal.allowed_models is not None
            and decision.model not in principal.allowed_models
        ):
            self.finish_without_relay(
                response,
                request_id,
                Decision(
                    result="DENIED",
                    http_status=403,
                    reason_code="PRINCIPAL_MODEL_NOT_ALLOWED",
                    rule_id="principal_allowed_models",
                    message="model is not allowed for this principal",
                    model=decision.model,
                    max_tokens=decision.max_tokens,
                ),
                stream=stream,
            )
            return

        if self._backend.external:
            if self._backend.credential_status == "missing":
                self.finish_without_relay(
                    response,
                    request_id,
                    engine_error(
                        503,
                        "BACKEND_CREDENTIAL_MISSING",
                        "backend credential is configured but unavailable",
                        decision,
                        source="gateway",
                    ),
                    stream=stream,
                )
                return
            if self._backend.credential_status == "invalid":
                self.finish_without_relay(
                    response,
                    request_id,
                    engine_error(
                        503,
                        "BACKEND_CREDENTIAL_INVALID",
                        "backend credential cannot be used as an HTTP header value",
                        decision,
                        source="gateway",
                    ),
                    stream=stream,
                )
                return
            if self._backend.mapped_model(decision.model) is None:
                self.finish_without_relay(
                    response,
                    request_id,
                    engine_error(
                        503,
                        "BACKEND_MODEL_UNMAPPED",
                        "allowed policy model has no backend mapping",
                        decision,
                        source="gateway",
                    ),
                    stream=stream,
                )
                return

        if not self._concurrency.try_acquire():
            limited = Decision(
                result="DENIED",
                http_status=429,
                reason_code="CONCURRENCY_LIMIT",
                rule_id="max_concurrent_requests",
                message="maximum concurrent requests reached",
                model=decision.model,
                max_tokens=decision.max_tokens,
            )
            self.finish_without_relay(
                response,
                request_id,
                limited,
                stream=stream,
                extra_headers={"Retry-After": "1"},
            )
            return

        acquired_at = time.monotonic()
        try:
            context_decision = self._context_decision(
                request_id, document, decision, policy_snapshot
            )
            if context_decision is not None:
                self.finish_without_relay(
                    response,
                    request_id,
                    context_decision,
                    stream=stream,
                )
                return
            started = self._records.build(
                "started",
                request_id,
                decision,
                attempted=False,
                backend_receipt=self._engine_client.receipt_not_queried(),
                response="none",
                error_code=None,
                stream=stream,
            )
            if not self._records.append(started):
                unavailable = engine_error(
                    503,
                    "STORE_UNAVAILABLE",
                    "request record store is unavailable",
                    decision,
                    source="gateway",
                )
                self._records.append(
                    self._records.build(
                        "final",
                        request_id,
                        unavailable,
                        attempted=False,
                        backend_receipt=self._engine_client.receipt_not_queried(),
                        response="failed",
                        error_code="STORE_UNAVAILABLE",
                        stream=stream,
                        http_start_status=503,
                        final_outcome="STORE_UNAVAILABLE",
                    )
                )
                response.send_error_decision(unavailable, request_id)
                return

            if stream:
                def record_response_started() -> None:
                    self._records.append(
                        self._records.build(
                            "response_started",
                            request_id,
                            decision,
                            attempted=True,
                            backend_receipt=(
                                self._engine_client.receipt_not_queried()
                            ),
                            response="streaming",
                            error_code=None,
                            stream=True,
                            http_start_status=200,
                            response_started=True,
                        )
                    )

                final_decision, final_outcome, response_started = (
                    self._relay.relay_stream(
                        response,
                        request_id,
                        document,
                        decision,
                        acquired_at,
                        record_response_started,
                    )
                )
                backend_receipt = self._engine_client.backend_receipt(request_id)
                abnormal_started = response_started and final_outcome not in {
                    "completed",
                    "backend_truncated",
                }
                if self._backend.external:
                    backend_cancel = "not_observed"
                else:
                    backend_cancel = (
                        self._engine_client.mock_cancel_evidence(
                            request_id,
                            wait=final_outcome == "client_disconnected",
                        )
                        if abnormal_started
                        else "not_applicable"
                    )
                completed = final_outcome == "completed"
                self._records.append(
                    self._records.build(
                        "final",
                        request_id,
                        final_decision,
                        attempted=True,
                        backend_receipt=backend_receipt,
                        response="completed" if completed else "failed",
                        error_code=(
                            None if completed else final_decision.reason_code
                        ),
                        stream=True,
                        http_start_status=(
                            200 if response_started else final_decision.http_status
                        ),
                        final_outcome=final_outcome,
                        response_started=response_started,
                        backend_cancel=backend_cancel,
                    )
                )
                if not response_started:
                    response.send_error_decision(
                        final_decision,
                        request_id,
                        extra_headers=(
                            {"Retry-After": final_decision.retry_after}
                            if final_decision.retry_after is not None
                            else None
                        ),
                    )
                return

            final_decision, engine_body = self._relay.relay(
                request_id, document, decision
            )
            backend_receipt = self._engine_client.backend_receipt(request_id)
            completed = (
                final_decision.result == "ALLOWED" and engine_body is not None
            )
            self._records.append(
                self._records.build(
                    "final",
                    request_id,
                    final_decision,
                    attempted=True,
                    backend_receipt=backend_receipt,
                    response="completed" if completed else "failed",
                    error_code=None if completed else final_decision.reason_code,
                    stream=False,
                    http_start_status=final_decision.http_status,
                    final_outcome=(
                        "completed" if completed else final_decision.reason_code
                    ),
                )
            )
            if completed:
                response.send_response_body(
                    200,
                    "application/json; charset=utf-8",
                    engine_body,
                    request_id=request_id,
                )
            else:
                response.send_error_decision(
                    final_decision,
                    request_id,
                    extra_headers=(
                        {"Retry-After": final_decision.retry_after}
                        if final_decision.retry_after is not None
                        else None
                    ),
                )
        finally:
            self._concurrency.release()
