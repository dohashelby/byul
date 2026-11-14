"""Frozen admission order, records, delivery and exactly-once acquired-slot release."""

from contextlib import ExitStack
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
import itertools
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from serving_runtime.gateway import request_flow as module
from serving_runtime.gateway.auth import AuthResult, Principal, CHAT_ROUTE, COMPLETIONS_ROUTE
from serving_runtime.gateway.backend import BackendConfig
from serving_runtime.gateway.cache_isolation import CacheSaltKey
from serving_runtime.gateway.validator import Decision
from serving_runtime.policy.policy import Policy, PolicyState
from serving_runtime.policy.policy_state import RevocationState


def normalized(value):
    if is_dataclass(value):
        return normalized(asdict(value))
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [normalized(item) for item in value]
    return value


def scenarios():
    cases = {}
    for kind, stream, backend in itertools.product(["chat", "completions"], [False, True], ["mock", "vllm", "sglang"]):
        cases[f"allowed:{kind}:{stream}:{backend}"] = {"kind": kind, "stream": stream, "backend": backend}
    errors = [
        "transfer",
        "length-missing",
        "length-empty",
        "length-invalid",
        "length-overflow",
        "too-large",
        "inactive",
        "auth",
        "hold",
        "cache-unready",
        "short-body",
        "invalid-json",
        "principal-model",
        "credential-missing",
        "credential-invalid",
        "unmapped",
        "salt-no-principal",
        "salt-failure",
        "concurrency",
    ]
    for name in errors:
        cases["reject:" + name] = {"errors": [name]}
    # All adjacent feasible rejection priorities, plus every pair of independent conditions.
    for a, b in itertools.combinations(errors, 2):
        if (a.startswith("length-") and b.startswith("length-")) or (
            a.startswith("credential-") and b.startswith("credential-")
        ):
            continue
        if (a, b) == ("principal-model", "salt-no-principal"):
            continue  # A restricted principal and an absent principal cannot coexist.
        cases["priority:" + a + "+" + b] = {"errors": [a, b]}
    for auth in ["unavailable", "missing", "invalid", "role"]:
        cases["auth:" + auth] = {"auth": auth}
    for value in ["zero", "negative", "leading-zero", "non-ascii", "whitespace"]:
        cases["length:" + value] = {"length": value}
    for value in ["array", "invalid", "unicode", "deep", "null", "scalar", "zero-body"]:
        cases["json:" + value] = {"json": value}
    for value in ["array", "invalid", "unicode", "deep", "null", "scalar"]:
        cases["defensive-document:" + value] = {"json": value, "force_allowed": True}
    for value in ["missing", "wrong"]:
        cases["content-type:" + value] = {"content_type": value}
    for stream in [False, True]:
        for outcome in ["completed", "backend_truncated", "client_disconnected", "deadline_exceeded", "upstream_error"]:
            for started in [False, True]:
                for backend in ["mock", "vllm"]:
                    cases[f"stream:{outcome}:{started}:{backend}"] = {
                        "stream": True,
                        "stream_outcome": outcome,
                        "response_started": started,
                        "backend": backend,
                    }
        for backend in ["mock", "vllm"]:
            for reason in ["ENGINE_DEADLINE", "ENGINE_UNAVAILABLE", "ENGINE_RATE_LIMITED"]:
                cases[f"buffered:{reason}:{backend}"] = {"backend": backend, "buffered_error": reason}
        for store in ["started", "final", "response_started"]:
            cases[f"store-false:{stream}:{store}"] = {"stream": stream, "store_false": store}
    for delivery in [
        "ok",
        "disconnect-before",
        "deadline-before",
        "disconnect-after",
        "deadline-after",
        "raise-before",
        "raise-after",
    ]:
        cases["delivery:" + delivery] = {"delivery": delivery}
    for context in ["within", "exceeded", "deadline", "unavailable", "exception"]:
        cases["context:" + context] = {"context": context}
        cases["context-stream:" + context] = {"context": context, "stream": True}
    cases["salt:applied"] = {"salt": True}
    cases["salt:stream-applied"] = {"salt": True, "stream": True}
    for point in [
        "clear_context",
        "bind_context",
        "set_auth",
        "read_body",
        "context_check",
        "mark_cache",
        "build:started",
        "append:started",
        "build:final",
        "append:final",
        "relay",
        "relay_stream",
        "build:response_started",
        "append:response_started",
        "receipt",
        "cancel",
        "send_error",
        "send_body",
    ]:
        options = {"fault": point}
        if point in ["relay_stream", "build:response_started", "append:response_started", "cancel"]:
            options.update(stream=True, stream_outcome="client_disconnected", response_started=True)
        if point == "mark_cache":
            options["salt"] = True
        if point == "send_error":
            options["store_false"] = "started"
        cases["exception:" + point] = options
    return dict(sorted(cases.items()))


class EventSource:
    def __init__(self, events, options):
        self.events, self.options = events, options

    def event(self, name, *args, **kwargs):
        self.events.append([name, normalized(args), normalized(kwargs)])
        if self.options.get("fault") == name:
            raise RuntimeError("test fault: " + name)


class Records(EventSource):
    def clear_context(self, route):
        self.event("clear_context", route)

    def bind_context(self, snapshot):
        self.event("bind_context", snapshot)

    def set_auth(self, value):
        self.event("set_auth", value)

    def set_context(self, value):
        self.event("set_context", value)

    def mark_cache_isolation_applied(self):
        self.event("mark_cache")

    def build(self, event, request_id, decision, **kwargs):
        self.event("build:" + event, request_id, decision, **kwargs)
        return {"event": event, "request_id": request_id, "decision": asdict(decision), **kwargs}

    def append(self, record):
        self.event("append:" + record["event"], record)
        return self.options.get("store_false") != record["event"]


class IO(EventSource):
    def __init__(self, events, options, body, headers):
        super().__init__(events, options)
        self.body, self.headers = body, headers

    def request_header(self, name):
        self.event("header", name)
        return self.headers.get(name)

    def read_request_body(self, length):
        self.event("read_body", length)
        return self.body

    def send_error_decision(self, decision, request_id, **kwargs):
        self.event("send_error", decision, request_id, **kwargs)

    def send_response_body(self, status, content_type, body, **kwargs):
        self.event("send_body", status, content_type, body, **kwargs)


class Slots(module.ConcurrencyLimiter):
    def __init__(self, events, blocked):
        super().__init__(0 if blocked else 1)
        self.events, self.acquired, self.releases = events, 0, 0

    def try_acquire(self):
        value = super().try_acquire()
        self.events.append(["acquire", value])
        self.acquired += int(value)
        return value

    def release(self):
        self.releases += 1
        self.events.append(["release"])
        super().release()


class Client(EventSource):
    def receipt_not_queried(self):
        self.event("receipt_not_queried")
        return "not_queried"

    def backend_receipt(self, request_id):
        self.event("receipt", request_id)
        return "confirmed"

    def mock_cancel_evidence(self, request_id, **kwargs):
        self.event("cancel", request_id, **kwargs)
        return "test_cancel_evidence"

    def count_input_tokens(self, *args, **kwargs):
        self.event("count_tokens", *args, **kwargs)
        if self.options.get("context") == "deadline":
            raise module.RequestDeadlineExceeded()
        if self.options.get("context") == "unavailable":
            raise ValueError("test tokenizer unavailable")
        return 5


class Relay(EventSource):
    request_deadline_s = 20.0

    def response_id_receipt(self, request_id):
        self.event("response_receipt", request_id)
        return "external_receipt"

    def relay(self, request_id, document, decision, **kwargs):
        self.event("relay", request_id, document, decision, **kwargs)
        reason = self.options.get("buffered_error")
        if reason:
            return replace(
                decision,
                result="ERROR",
                http_status=504 if reason == "ENGINE_DEADLINE" else 503,
                reason_code=reason,
                retry_after="3" if reason == "ENGINE_RATE_LIMITED" else None,
            ), None
        return decision, b'{"answer":"test"}'

    def relay_stream(self, response, request_id, document, decision, acquired_at, callback, **kwargs):
        self.event("relay_stream", request_id, document, decision, acquired_at, **kwargs)
        started = self.options.get("response_started", True)
        if started:
            callback()
        outcome = self.options.get("stream_outcome", "completed")
        final = (
            decision
            if outcome == "completed"
            else replace(
                decision,
                result="ERROR",
                http_status=504,
                reason_code="ENGINE_DEADLINE" if outcome == "deadline_exceeded" else "STREAM_TEST_ERROR",
                retry_after="2" if outcome == "upstream_error" else None,
            )
        )
        return final, outcome, started


class Auth(EventSource):
    def __init__(self, events, options, principal):
        super().__init__(events, options)
        self.principal = principal
        self.state = "not_configured" if principal is None else "ok"

    def authenticate(self, authorization):
        self.event("authenticate", authorization)
        state = self.options.get("auth")
        if state == "role":
            return AuthResult("authenticated", replace(self.principal, roles=()))
        if state:
            return AuthResult(state)
        return AuthResult("not_configured" if self.state == "not_configured" else "authenticated", self.principal)

    def roles_for(self, route):
        self.event("roles_for", route)
        return ("inference",)


def observe(options):
    options = dict(options)
    errors = set(options.get("errors", []))
    if "auth" in errors:
        options["auth"] = "missing"
    if "salt-failure" in errors or "salt-no-principal" in errors:
        options["salt"] = True
    events = []
    policy = Policy(
        "runtime-mvp/1.0",
        "contract-policy",
        "1",
        "contract-target",
        ("contract-model",),
        16,
        32,
        4096,
        max_context_tokens=8 if options.get("context") == "exceeded" else 100 if options.get("context") else None,
        cache_isolation="per_principal" if options.get("salt") else "off",
    )
    snapshot = PolicyState(True, policy, "/srv/test/policy.json", "a" * 64, "2026-01-02T00:00:00+00:00", None, None)
    backend = replace(BackendConfig.mock(("127.0.0.1", 18088)), kind=options.get("backend", "mock"))
    if errors & {"credential-missing", "credential-invalid", "unmapped"} or options.get("salt"):
        backend = replace(backend, kind="vllm")
    backend = replace(
        backend,
        model_map={} if "unmapped" in errors else {"contract-model": "engine-model"},
        credential_status="missing"
        if "credential-missing" in errors
        else "invalid"
        if "credential-invalid" in errors
        else "configured",
    )
    principal = Principal(
        "contract-principal", ("inference",), "0" * 64, ("other-model",) if "principal-model" in errors else None
    )
    if "salt-no-principal" in errors:
        principal = None
    auth = Auth(events, options, principal)
    records, client, relay = Records(events, options), Client(events, options), Relay(events, options)
    slots = Slots(events, "concurrency" in errors)
    stream = options.get("stream", False)
    kind = options.get("kind", "chat")
    route = CHAT_ROUTE if kind == "chat" else COMPLETIONS_ROUTE
    document = {
        "model": "contract-model",
        "max_tokens": 16,
        "stream": stream,
        **({"messages": [{"role": "user", "content": "test"}]} if kind == "chat" else {"prompt": "test"}),
    }
    body = json.dumps(document).encode()
    variants = {
        "array": b"[]",
        "invalid": b"{",
        "unicode": b"\xff",
        "deep": b"[" * 1200 + b"]" * 1200,
        "null": b"null",
        "scalar": b"1",
        "zero-body": b"",
    }
    if options.get("json"):
        body = variants[options["json"]]
    if "invalid-json" in errors:
        body = b"{"
    length = str(len(body))
    if "short-body" in errors:
        length = str(len(body) + 1)
    if "too-large" in errors:
        length = "4097"
    length_variants = {
        "zero": "0",
        "negative": "-1",
        "leading-zero": "000" + length,
        "non-ascii": "１２",
        "whitespace": " ",
    }
    if options.get("length"):
        length = length_variants[options["length"]]
        if options["length"] == "zero":
            body = b""
    headers = {"Content-Length": length, "Content-Type": "application/json", "Authorization": "Bearer synthetic-token"}
    if "transfer" in errors:
        headers["Transfer-Encoding"] = "chunked"
    for label, value in [
        ("length-missing", None),
        ("length-empty", ""),
        ("length-invalid", "1a"),
        ("length-overflow", "9" * 5000),
    ]:
        if label in errors:
            headers["Content-Length"] = value
    if options.get("content_type"):
        headers["Content-Type"] = None if options["content_type"] == "missing" else "text/plain"
    response = IO(events, options, body, headers)
    key = CacheSaltKey("configured", b"x" * 32)
    flow = module.RequestFlow(
        backend,
        lambda: snapshot,
        lambda: RevocationState("not_configured", None, "test", ()),
        records,
        client,
        relay,
        slots,
        lambda: auth,
        lambda target: ("TARGET_TEST_HOLD", "test target conflict") if "hold" in errors else None,
        cache_salt_key=key,
    )
    real_evaluate, real_validate = module.evaluate_policy, module.validate_request

    def evaluate(*args, **kwargs):
        events.append(["evaluate_policy"])
        result = real_evaluate(*args, **kwargs, now=datetime(2026, 1, 2, tzinfo=timezone.utc))
        if "inactive" in errors:
            result.update(gateway_active=False, reason_code="POLICY_TEST_INACTIVE", reason_message="test inactive")
        return result

    def validate(*args, **kwargs):
        events.append(["validate_request"])
        if options.get("force_allowed"):
            return Decision("ALLOWED", 200, "ALLOWED", None, "ok", "contract-model", 16)
        return real_validate(*args, **kwargs)

    def isolation(*args):
        events.append(["isolation_status"])
        # State-transition probes deliberately make this gate ready, then fail the later principal/key gate.
        if "cache-unready" in errors:
            return {"ready": False, "unavailable_reason_code": "CACHE_ISOLATION_KEY_MISSING"}
        if errors & {"salt-no-principal", "salt-failure"}:
            return {"ready": True}
        return module_isolation(*args)

    module_isolation = module.isolation_status

    def clock():
        value = (
            200.0
            if options.get("delivery", "").startswith("deadline")
            and any(e[0] == "write_response_before" for e in events)
            else 100.0
        )
        events.append(["monotonic", value])
        return value

    def write(response, deadline, callback):
        events.append(["write_response_before", deadline])
        delivery = options.get("delivery", "ok")
        if delivery.endswith("before"):
            if delivery.startswith("raise"):
                raise RuntimeError("test delivery exception")
            raise OSError("test delivery failure")
        callback()
        if delivery.endswith("after"):
            if delivery.startswith("raise"):
                raise RuntimeError("test delivery exception")
            raise OSError("test delivery failure")

    with ExitStack() as stack:
        stack.enter_context(patch.object(module, "evaluate_policy", evaluate))
        stack.enter_context(patch.object(module, "validate_request", validate))
        stack.enter_context(patch.object(module, "isolation_status", isolation))
        stack.enter_context(patch.object(module.time, "monotonic", clock))
        stack.enter_context(patch.object(module, "write_response_before", write))
        if "salt-failure" in errors:
            stack.enter_context(patch.object(CacheSaltKey, "derive", side_effect=RuntimeError("test key failure")))
        if options.get("fault") == "context_check" or options.get("context") == "exception":
            stack.enter_context(
                patch.object(flow, "_context_decision", side_effect=RuntimeError("test context exception"))
            )
        try:
            result = flow._handle(response, "contract-request", route=route, request_kind=kind)
            failure = None
        except Exception as error:
            result = None
            failure = [
                type(error).__name__,
                str(error),
                None if error.__cause__ is None else [type(error.__cause__).__name__, str(error.__cause__)],
            ]
    return {
        "return": result,
        "exception": failure,
        "events": events,
        "acquired": slots.acquired,
        "released": slots.releases,
        "in_flight": slots.status()["in_flight"],
    }


CASES = scenarios()
GOLDEN = json.loads((Path(__file__).parent / "data/wp5-handle-golden.json").read_text())


@pytest.mark.parametrize("case", CASES)
def test_wp5_handle_contract(case):
    actual = observe(CASES[case])
    assert actual == GOLDEN[case]
    assert actual["released"] == actual["acquired"]
    assert actual["in_flight"] == 0
    if not actual["acquired"]:
        assert not any(event[0] in {"relay", "relay_stream"} for event in actual["events"])

    if case.startswith("priority:"):
        first = observe({"errors": [CASES[case]["errors"][0]]})
        actual_error = next(event[1][0] for event in actual["events"] if event[0] == "send_error")
        first_error = next(event[1][0] for event in first["events"] if event[0] == "send_error")
        assert actual_error == first_error
