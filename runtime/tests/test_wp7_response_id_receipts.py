"""Actual vLLM callback IDs remain correlated through every relay path."""

from dataclasses import replace
import json
import time
from unittest.mock import Mock, patch

import pytest

from serving_runtime.gateway.auth import CHAT_ROUTE, COMPLETIONS_ROUTE
from serving_runtime.gateway.backend import BackendConfig, upstream_request_id
from serving_runtime.gateway.relay import Relay, response_id_receipt
from serving_runtime.gateway.validator import Decision

RID = "0123456789abcdef0123456789abcdef"
OTHER = "f" * 32


@pytest.mark.parametrize("prefix", ["", "chatcmpl-", "cmpl-"])
def test_receipt_matches_exact_forwarded_vllm_callback_id(prefix):
    forwarded = upstream_request_id("vllm", RID)
    assert response_id_receipt(RID, prefix + forwarded) == "engine_response_id_matched"


@pytest.mark.parametrize("value", ["byul-cb-" + OTHER, "chatcmpl-byul-cb-" + RID + "-0", "fake-byul-cb-" + RID])
def test_callback_receipt_rejects_other_request_or_extra_suffix(value):
    assert response_id_receipt(RID, value) == "engine_response_id_mismatch"


@pytest.mark.parametrize("route", [CHAT_ROUTE, COMPLETIONS_ROUTE])
@pytest.mark.parametrize("stream", [False, True])
def test_real_backend_forwarded_id_matches_buffered_and_stream_receipts(route, stream):
    backend = replace(BackendConfig.mock(("localhost", 1)), kind="vllm")
    forwarded = backend.request_headers(request_id=RID)["X-Request-ID"]
    prefix = "chatcmpl-" if route == CHAT_ROUTE else "cmpl-"
    choices = (
        [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}]
        if stream and route == CHAT_ROUTE
        else [{"index": 0, "text": "ok", "finish_reason": "stop", "logprobs": None}]
    )
    body = json.dumps(
        {
            "id": prefix + forwarded,
            "object": "text_completion" if route == COMPLETIONS_ROUTE else "chat.completion",
            "choices": choices,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    ).encode()
    wire = b"data: " + body + b"\n\ndata: [DONE]\n\n"
    response = Mock(status=200)
    response.getheader.return_value = "text/event-stream" if stream else "application/json"
    response.read.side_effect = [body, b""]
    response.read1.side_effect = [wire if stream else body, b""]
    connection = Mock(sock=None)
    connection.getresponse.return_value = response
    relay = Relay(backend, relay_timeout_s=2, stream_idle_timeout_s=2, request_deadline_s=5)
    allowed = Decision("ALLOWED", 200, "REQUEST_ALLOWED", None, "allowed", "model", 10)
    with (
        patch.object(BackendConfig, "connection", return_value=(connection, route)),
        patch("serving_runtime.gateway.relay.DeadlineWatchdog") as watcher,
        patch("serving_runtime.gateway.relay.relay_payload", return_value=b"{}"),
    ):
        watcher.return_value.expired.return_value = False
        watcher.return_value.finish.return_value = False
        if stream:
            result, outcome, started = relay.relay_stream(
                Mock(), RID, {}, allowed, time.monotonic(), lambda: None, route=route
            )
            assert (result, outcome, started) == (allowed, "completed", True)
        else:
            result, returned = relay.relay(RID, {}, allowed, started_at=time.monotonic(), route=route)
            assert result == allowed
            assert json.loads(returned)["id"] == prefix + forwarded
    assert connection.request.call_args.kwargs["headers"]["X-Request-ID"] == forwarded
    assert relay.response_id_receipt(RID) == "engine_response_id_matched"
