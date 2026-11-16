"""Original streaming relay outcomes, ordered resource effects and partial headers."""

from contextlib import ExitStack
from dataclasses import asdict, replace
from http.client import HTTPException
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from serving_runtime.gateway import relay as module
from serving_runtime.gateway.backend import BackendConfig
from serving_runtime.gateway.auth import CHAT_ROUTE, COMPLETIONS_ROUTE
from serving_runtime.gateway.validator import Decision


def cases():
    result = {}
    common = [
        "normal",
        "early-deadline",
        "factory-error",
        "connect-oserror",
        "connect-timeout",
        "request-http-error",
        "response-timeout",
        "upstream-429",
        "upstream-500",
        "bad-media",
        "header-deadline",
        "error-body-deadline",
        "start-broken",
        "start-oserror",
        "start-timeout",
        "callback-broken",
        "read-idle",
        "read-deadline",
        "read-oserror",
        "read-oserror-deadline",
        "read-http-error",
        "read-post-deadline",
        "loop-deadline",
        "loop-idle",
        "write-broken",
        "write-deadline",
        "write-timeout",
        "empty",
        "truncated",
        "partial-event",
        "finish-deadline",
        "invalid-json",
        "invalid-utf8",
        "privacy-strip",
        "privacy-deny",
        "bom-first",
        "bom-second",
        "socket-fallback",
        "socket-absent",
        "closed-socket",
        "split-event",
    ]
    for route in ["chat", "completions"]:
        for scenario in common:
            result[route + ":" + scenario] = {"route": route, "scenario": scenario}
    for scenario in ["line-limit", "event-limit", "line-partial-limit"]:
        result["chat:" + scenario] = {"route": "chat", "scenario": scenario}
    for scenario in [
        "comments-before",
        "comments-after",
        "comment-broken",
        "comment-deadline",
        "done-first",
        "done-duplicate",
        "chunk-after-done",
        "invalid-choices",
        "total-limit",
        "emitted-limit",
        "comment-emitted-limit",
        "header-start-deadline",
        "multiple-chunks",
    ]:
        result["completions:" + scenario] = {"route": "completions", "scenario": scenario}
    result["chat:external"] = {"route": "chat", "scenario": "normal", "external": True}
    result["completions:metadata-requested"] = {"route": "completions", "scenario": "metadata-requested"}
    return dict(sorted(result.items()))


def normalize(value):
    if isinstance(value, bytes):
        return {"hex": value.hex()}
    if isinstance(value, Decision):
        return normalize(asdict(value))
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    return value


def observe(options):
    events = []
    scenario = options["scenario"]
    route = CHAT_ROUTE if options["route"] == "chat" else COMPLETIONS_ROUTE
    shared = {
        "phase": "setup",
        "clock": 0,
        "reads": 0,
        "writes": 0,
        "expired_checks": 0,
        "expired": False,
        "connection_created": False,
        "watchdogs": 0,
    }

    def log(name, *args, **kwargs):
        events.append([name, normalize(args), normalize(kwargs)])

    def fault(name):
        mapping = {
            "connect-oserror": ("connect", OSError),
            "connect-timeout": ("connect", TimeoutError),
            "request-http-error": ("request", HTTPException),
            "response-timeout": ("getresponse", TimeoutError),
        }
        if scenario in mapping and mapping[scenario][0] == name:
            raise mapping[scenario][1]("synthetic " + name)

    class Socket:
        def __init__(self, name):
            self.name = name

        def settimeout(self, value):
            log("socket.timeout", self.name, value)
            if scenario == "closed-socket" and self.name == "upstream":
                raise OSError(9, "closed synthetic socket")

    upstream_socket, downstream_socket = Socket("upstream"), Socket("downstream")
    chunk = {"id": "cmpl-contract-request", "choices": [{"text": "test"}]}
    if route == CHAT_ROUTE:
        chunk["choices"] = [{"delta": {"content": "test"}}]
    if scenario in {"privacy-strip", "metadata-requested"}:
        chunk.update(private_debug="synthetic-private", prompt_token_ids=[1, 2])
    if scenario == "privacy-deny":
        chunk["metadata"] = nested = {}
        for _ in range(67):
            nested["child"] = {}
            nested = nested["child"]
    if scenario == "invalid-choices":
        chunk["choices"] = []
    event = b"data: " + json.dumps(chunk, separators=(",", ":")).encode() + b"\n\n"
    done = b"data: [DONE]\n\n"
    comment = b": heartbeat\n\n"
    chunks = [event, done, b""]
    if scenario == "empty":
        chunks = [b""]
    if scenario == "truncated":
        chunks = [event, b""]
    if scenario == "partial-event":
        chunks = [event[:-2], b""]
    if scenario == "invalid-json":
        chunks = [b"data: {bad}\n\n", b""]
    if scenario == "invalid-utf8":
        chunks = [b"data: \xff\n\n", b""]
    if scenario == "bom-first":
        chunks[0] = b"\xef\xbb\xbf" + event
    if scenario == "bom-second":
        chunks = [event, b"\xef\xbb\xbf" + done, b""]
    if scenario == "split-event":
        chunks = [event[:9], event[9:], done, b""]
    if scenario == "line-limit":
        chunks = [b"data: " + b"x" * 40 + b"\n\n"]
    if scenario == "line-partial-limit":
        chunks = [b"data: " + b"x" * 40]
    if scenario == "event-limit":
        chunks = [b": " + b"x" * 20 + b"\n" + b": " + b"y" * 20 + b"\n\n"]
    if scenario == "comments-before":
        chunks = [comment, event, done, b""]
    if scenario in {"comments-after", "comment-broken", "comment-deadline", "comment-emitted-limit"}:
        chunks = [event, comment, done, b""]
    if scenario == "done-first":
        chunks = [done, b""]
    if scenario == "done-duplicate":
        chunks = [event, done, done, b""]
    if scenario == "chunk-after-done":
        chunks = [event, done, event, b""]
    if scenario == "multiple-chunks":
        chunks = [event, event, done, b""]
    iterator = iter(chunks)

    class EngineResponse:
        status = (
            429
            if scenario == "upstream-429" or scenario == "error-body-deadline"
            else 500
            if scenario == "upstream-500"
            else 200
        )
        fp = SimpleNamespace(raw=SimpleNamespace(_sock=None if scenario == "socket-absent" else upstream_socket))

        def getheader(self, name, default=None):
            log("response.header", name, default)
            if name == "Content-Type":
                return "application/json" if scenario == "bad-media" else "Text/Event-Stream; charset=utf-8"
            if name == "Retry-After":
                return "7"
            return default

        def read(self, size):
            log("response.read", size)
            shared["phase"] = "error-body"
            return b'{"error":"synthetic upstream"}'

        def read1(self, size):
            shared["reads"] += 1
            shared["phase"] = "read:" + str(shared["reads"])
            log("response.read1", size)
            if scenario in {"read-idle", "read-deadline"}:
                raise TimeoutError("synthetic read timeout")
            if scenario in {"read-oserror", "read-oserror-deadline"}:
                raise OSError("synthetic read failure")
            if scenario == "read-http-error":
                raise HTTPException("synthetic HTTP read failure")
            return next(iterator, b"")

    engine_response = EngineResponse()

    class Connection:
        sock = None if scenario in {"socket-fallback", "socket-absent", "closed-socket"} else upstream_socket

        def connect(self):
            log("connection.connect")
            fault("connect")

        def request(self, *args, **kwargs):
            log("connection.request", *args, **kwargs)
            fault("request")

        def getresponse(self):
            log("connection.getresponse")
            fault("getresponse")
            shared["phase"] = "headers"
            return engine_response

        def close(self):
            log("connection.close")

    connection = Connection()
    actual_backend = replace(
        BackendConfig.mock(("127.0.0.1", 18088)),
        kind="vllm" if options.get("external") else "mock",
        model_map={"contract-model": "engine-model"},
    )

    class Backend:
        def __getattr__(self, name):
            return getattr(actual_backend, name)

        def connection(self, path, timeout):
            log("backend.connection", path, timeout)
            if scenario == "factory-error":
                raise OSError("synthetic factory failure")
            shared["connection_created"] = True
            return connection, path

    class Watchdog:
        def __init__(self, conn, deadline):
            assert conn is connection
            shared["watchdogs"] += 1
            log("watchdog.create", deadline)

        def start(self):
            log("watchdog.start")

        def connect(self):
            log("watchdog.connect")
            connection.connect()

        def watch_response(self, value):
            assert value is engine_response
            log("watchdog.watch_response")

        def watch_socket(self, value):
            assert value is downstream_socket
            log("watchdog.watch_socket", "downstream")

        def expired(self):
            shared["expired_checks"] += 1
            phase = shared["phase"]
            trigger = (
                scenario == "header-deadline"
                and phase == "headers"
                or scenario == "error-body-deadline"
                and phase == "error-body"
                or scenario in {"read-deadline", "read-oserror-deadline", "read-post-deadline"}
                and phase.startswith("read:")
                or scenario in {"write-deadline", "comment-deadline"}
                and phase.startswith("write:")
                or scenario == "header-start-deadline"
                and shared["expired_checks"] >= 4
            )
            shared["expired"] = shared["expired"] or trigger
            log("watchdog.expired", shared["expired"])
            return shared["expired"]

        def finish(self):
            log("watchdog.finish", scenario == "finish-deadline")
            return scenario == "finish-deadline"

        def cancel(self):
            log("watchdog.cancel")

    class Downstream:
        connection = downstream_socket

        def start_stream_response(self, request_id):
            shared["phase"] = "partial-headers"
            log("downstream.start", request_id)
            if scenario == "start-broken":
                raise BrokenPipeError("synthetic partial headers")
            if scenario == "start-oserror":
                raise OSError("synthetic partial headers")
            if scenario == "start-timeout":
                raise TimeoutError("synthetic partial headers")

        def write_stream_chunk(self, value):
            shared["writes"] += 1
            shared["phase"] = "write:" + str(shared["writes"])
            log("downstream.write", value)
            if (
                scenario in {"write-broken", "write-deadline"}
                or scenario in {"comment-broken", "comment-deadline"}
                and value.startswith(b":")
            ):
                raise BrokenPipeError("synthetic client disconnect")
            if scenario == "write-timeout":
                raise TimeoutError("synthetic client timeout")

    def on_started():
        log("on_response_started")
        if scenario == "callback-broken":
            raise ConnectionResetError("synthetic callback disconnect")

    def clock():
        shared["clock"] += 1
        if scenario == "early-deadline":
            value = 120.0
        elif scenario == "loop-deadline" and shared["clock"] >= (4 if route == CHAT_ROUTE else 3):
            value = 120.0
        elif scenario == "loop-idle" and shared["clock"] >= (4 if route == CHAT_ROUTE else 3):
            value = 103.0
        else:
            value = 100.0
        log("monotonic", value)
        return value

    allowed = Decision(
        "ALLOWED",
        200,
        "REQUEST_ALLOWED",
        None,
        "allowed",
        "contract-model",
        8,
        extra_fields=(("return_token_ids", "forward"),) if scenario == "metadata-requested" else (),
    )
    document = {
        "model": "contract-model",
        **({"messages": [{"role": "user", "content": "test"}]} if route == CHAT_ROUTE else {"prompt": "test"}),
    }
    if scenario == "metadata-requested":
        document["return_token_ids"] = True
    real_payload, real_sanitize = module.relay_payload, module._sanitize_sse_event

    def payload(*args, **kwargs):
        log("relay_payload", kwargs)
        return real_payload(*args, **kwargs)

    def sanitize(*args, **kwargs):
        result = real_sanitize(*args, **kwargs)
        if scenario == "emitted-limit":
            return b":" + b"x" * 160 + b"\n\n", result[1]
        if scenario == "comment-emitted-limit":
            return b":" + b"x" * 117 + b"\n\n", result[1]
        return result

    relay = module.Relay(Backend(), 5.0, 2.0, 10.0)
    with ExitStack() as stack:
        stack.enter_context(patch.object(module, "DeadlineWatchdog", Watchdog))
        stack.enter_context(patch.object(module.time, "monotonic", clock))
        stack.enter_context(patch.object(module, "relay_payload", payload))
        if scenario in {"line-limit", "line-partial-limit"}:
            stack.enter_context(patch.object(module, "_CHAT_SSE_LINE_LIMIT", 24))
        if scenario == "event-limit":
            stack.enter_context(patch.object(module, "_CHAT_SSE_EVENT_LIMIT", 32))
        if scenario in {"total-limit", "emitted-limit", "comment-emitted-limit"}:
            stack.enter_context(
                patch.object(module, "_COMPLETION_STREAM_BODY_LIMIT", 32 if scenario == "total-limit" else 128)
            )
        if scenario in {"emitted-limit", "comment-emitted-limit"}:
            stack.enter_context(patch.object(module, "_sanitize_sse_event", sanitize))
        try:
            value = relay.relay_stream(
                Downstream(),
                "contract-request",
                document,
                allowed,
                100.0,
                on_started,
                route=route,
                cache_salt="synthetic-cache-salt",
            )
            result, exception = normalize(value), None
        except Exception as error:
            result, exception = (
                None,
                [
                    type(error).__name__,
                    str(error),
                    None if error.__cause__ is None else [type(error.__cause__).__name__, str(error.__cause__)],
                ],
            )
    receipt = relay.response_id_receipt("contract-request")
    return {
        "result": result,
        "exception": exception,
        "receipt": receipt,
        "events": events,
        "connection_created": shared["connection_created"],
        "watchdogs": shared["watchdogs"],
    }


CASES = cases()
GOLDEN = json.loads((Path(__file__).parent / "data/wp5-relay-stream-golden.json").read_text())


@pytest.mark.parametrize("case", CASES)
def test_wp5_relay_stream_contract(case):
    result = observe(CASES[case])
    assert result == GOLDEN[case]
    names = [event[0] for event in result["events"]]
    if result["connection_created"]:
        assert names.count("watchdog.start") == names.count("watchdog.cancel") == names.count("connection.close") == 1
        assert names.index("watchdog.start") < names.index("watchdog.cancel") < names.index("connection.close")
    else:
        assert result["watchdogs"] == 0
        assert not any(name in names for name in ["watchdog.start", "watchdog.cancel", "connection.close"])
    if CASES[case]["scenario"].startswith("start-"):
        assert result["result"][2] is True
    if CASES[case]["scenario"] == "privacy-strip":
        output = b"".join(
            bytes.fromhex(event[1][0]["hex"]) for event in result["events"] if event[0] == "downstream.write"
        )
        assert b"private_debug" not in output and b"prompt_token_ids" not in output
