"""Retained request details must follow upstream deletion and availability."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from pathlib import Path
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from byul_platform.config import Settings
from byul_platform.main import create_app
from byul_platform.sources.runtime import LiveRuntimeSource, RuntimeClient, RuntimeNotFound
from conftest import FIXTURES, load_json


CAPTURE = load_json(FIXTURES / "runtime" / "capture.json")
REQUEST_ID = next(iter(CAPTURE["details"]))
DETAIL_PATH = f"/api/requests/{REQUEST_ID}"
PUBLIC_PATH = f"/api/runtime/requests/{REQUEST_ID}"


class ChangingRuntime:
    def __init__(self) -> None:
        self.detail = copy.deepcopy(CAPTURE["details"][REQUEST_ID])
        self.status = 200
        self.unreachable = False
        self.list_status = 200
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path in (DETAIL_PATH, f"{DETAIL_PATH}/evidence"):
            if self.unreachable:
                raise httpx.ConnectError("fixture offline")
            return httpx.Response(self.status, json=self.detail if self.status == 200 else {})
        if path == "/api/requests":
            return httpx.Response(self.list_status, json={"requests": (
                [{"request_id": REQUEST_ID}] if self.status != 404 else []
            )})
        if path == "/api/observations":
            return httpx.Response(200, json={"observations": []})
        return httpx.Response(404, json={})


@pytest.fixture
def changing_client(tmp_path: Path, monkeypatch):
    runtime = ChangingRuntime()
    real_init = RuntimeClient.__init__

    def init(self, base_url, token, **kw):
        real_init(self, base_url, token, transport=httpx.MockTransport(runtime))

    monkeypatch.setattr(RuntimeClient, "__init__", init)
    app = create_app(Settings(runtime_base_url="http://runtime.test", web_dist=tmp_path / "no-dist"))
    with TestClient(app) as client:
        yield client, runtime


def test_detail_and_evidence_reflect_retention_immediately(changing_client):
    client, runtime = changing_client
    first = client.get(PUBLIC_PATH)
    assert first.status_code == 200
    assert first.json()["detail_invalid"] is None
    assert first.json()["detail"]["timeline"][-1]["phase"] == "final"
    assert client.get(f"{PUBLIC_PATH}/evidence").status_code == 200

    runtime.status = 404
    for _ in range(2):
        assert client.get(PUBLIC_PATH).status_code == 404
        assert client.get(f"{PUBLIC_PATH}/evidence").status_code == 404
    assert runtime.calls.count(DETAIL_PATH) == 3


def test_events_check_fresh_list_after_retention(changing_client):
    client, runtime = changing_client
    first = client.get("/api/runtime/events")
    assert first.status_code == 200
    assert len(first.json()["items"]) == 1

    runtime.status = 404
    second = client.get("/api/runtime/events")
    assert second.status_code == 200
    assert second.json()["items"] == []


@pytest.mark.parametrize(
    ("status", "unreachable", "expected_status", "expected_code"),
    [
        (500, False, 503, "RUNTIME_UNREACHABLE"),
        (200, True, 503, "RUNTIME_UNREACHABLE"),
        (401, False, 502, "RUNTIME_UNAUTHORIZED"),
        (403, False, 502, "RUNTIME_UNAUTHORIZED"),
    ],
)
def test_cached_final_is_not_returned_when_runtime_cannot_be_read(
    changing_client, status, unreachable, expected_status, expected_code
):
    client, runtime = changing_client
    assert client.get(PUBLIC_PATH).status_code == 200
    runtime.status = status
    runtime.unreachable = unreachable

    response = client.get(PUBLIC_PATH)
    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code

    runtime.status = 200
    runtime.unreachable = False
    assert client.get(PUBLIC_PATH).status_code == 200


def test_late_success_does_not_repopulate_deleted_detail_cache():
    started = threading.Event()
    release = threading.Event()
    runtime = ChangingRuntime()
    lock = threading.Lock()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        with lock:
            calls += 1
            sequence = calls
        if sequence == 1:
            # This request read before deletion, but completes after a later 404.
            started.set()
            assert release.wait(timeout=5), "fixture did not release the in-flight read"
            return httpx.Response(200, json=runtime.detail)
        return httpx.Response(404, json={})

    client = RuntimeClient("http://runtime.test", None, transport=httpx.MockTransport(handler))
    source = LiveRuntimeSource(client, "http://runtime.test", cache_ttl=60)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(source.request_detail, REQUEST_ID)
            try:
                assert started.wait(timeout=5), "fixture request never started"
                with pytest.raises(RuntimeNotFound):
                    source.request_detail(REQUEST_ID)
            finally:
                release.set()
            assert pending.result(timeout=5).detail["request_id"] == REQUEST_ID
        with pytest.raises(RuntimeNotFound):
            source.request_detail(REQUEST_ID)
        assert calls == 3
    finally:
        client.close()


def make_source(runtime, ttl=60):
    client = RuntimeClient("http://runtime.test", None, transport=httpx.MockTransport(runtime))
    return client, LiveRuntimeSource(client, "http://runtime.test", cache_ttl=ttl)


def test_warm_final_list_does_not_repeat_detail_reads():
    runtime = ChangingRuntime()
    client, source = make_source(runtime)
    try:
        for _ in range(5):
            assert len(source._gateway_events().candidates) == 1
        assert runtime.calls.count("/api/requests") == 5
        assert runtime.calls.count(DETAIL_PATH) == 1
        assert len(source._final_events) == 1
        runtime.status = 404
        assert source._gateway_events().candidates == []
        assert source._final_events == {}
    finally:
        client.close()


@pytest.mark.parametrize("status", [401, 403, 500])
def test_list_failure_discards_warm_events_and_recovers_fresh(status):
    runtime = ChangingRuntime()
    client, source = make_source(runtime)
    try:
        source._gateway_events()
        runtime.list_status = status
        with pytest.raises((RuntimeError, LookupError)):
            source._gateway_events()
        assert source._final_events == {}
        runtime.list_status = 200
        source._gateway_events()
        assert runtime.calls.count(DETAIL_PATH) == 2
    finally:
        client.close()


def test_final_event_cache_expires_and_incomplete_events_are_not_cached(monkeypatch):
    runtime = ChangingRuntime()
    client, source = make_source(runtime)
    clock = [100.0]
    monkeypatch.setattr("byul_platform.sources.runtime.time.monotonic", lambda: clock[0])
    try:
        source._gateway_events()
        clock[0] += source.FINAL_EVENT_TTL + 1
        source._gateway_events()
        assert runtime.calls.count(DETAIL_PATH) == 2
        source._invalidate_final_events()
        runtime.detail["gateway_records"] = [r for r in runtime.detail["gateway_records"] if r["phase"] != "final"]
        source._gateway_events()
        source._gateway_events()
        assert runtime.calls.count(DETAIL_PATH) == 4
        assert source._final_events == {}
    finally:
        client.close()


def test_concurrent_warm_polls_share_final_event_hydration():
    runtime = ChangingRuntime()
    client, source = make_source(runtime)
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            batches = list(executor.map(lambda _: source._gateway_events(), range(20)))
        assert all(len(b.candidates) == 1 for b in batches)
        assert runtime.calls.count("/api/requests") == 20
        assert runtime.calls.count(DETAIL_PATH) == 1
    finally:
        client.close()


def test_late_list_detail_cannot_repopulate_after_newer_detail_rejection():
    started, release = threading.Event(), threading.Event()
    runtime = ChangingRuntime()
    calls = 0
    lock = threading.Lock()

    def handler(request):
        nonlocal calls
        if request.url.path == DETAIL_PATH:
            with lock:
                calls += 1
                sequence = calls
            if sequence == 1:
                started.set()
                assert release.wait(5)
                return httpx.Response(200, json=runtime.detail)
            return httpx.Response(404, json={})
        return runtime(request)

    client, source = make_source(handler)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(source._gateway_events)
            try:
                assert started.wait(5)
                with pytest.raises(RuntimeNotFound):
                    source.request_detail(REQUEST_ID)
            finally:
                release.set()
            pending.result(5)
        assert source._final_events == {}
        assert source._gateway_events().candidates == []
        assert calls == 3
    finally:
        client.close()


def test_final_event_cache_is_bounded_to_list_limit():
    runtime = ChangingRuntime()
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/requests":
            return httpx.Response(200, json={"requests": [{"request_id": f"{i:032x}"} for i in range(250)]})
        detail = copy.deepcopy(runtime.detail)
        rid = request.url.path.rsplit("/", 1)[-1]
        for record in detail["gateway_records"]:
            record["request_id"] = rid
        return httpx.Response(200, json=detail)
    client, source = make_source(handler)
    try:
        batch = source._gateway_events()
        assert batch.truncated
        assert len(batch.candidates) == source.REQUEST_LIMIT
        assert len(source._final_events) == source.REQUEST_LIMIT
        source._gateway_events()
        assert len(calls) == source.REQUEST_LIMIT + 2
    finally:
        client.close()


@pytest.mark.parametrize("evidence", [False, True])
@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_fresh_detail_or_evidence_failure_invalidates_warm_list(evidence, status):
    runtime = ChangingRuntime()
    client, source = make_source(runtime)
    try:
        source._gateway_events()
        runtime.status = status
        with pytest.raises((RuntimeError, LookupError)):
            (source.request_evidence if evidence else source.request_detail)(REQUEST_ID)
        assert source._final_events == {}
    finally:
        client.close()


def test_late_list_is_followed_by_newer_refresh_and_eviction():
    started, release = threading.Event(), threading.Event()
    runtime = ChangingRuntime()
    calls = 0
    def handler(request):
        nonlocal calls
        if request.url.path == "/api/requests":
            calls += 1
            if calls == 1:
                started.set()
                assert release.wait(5)
                return httpx.Response(200, json={"requests": [{"request_id": REQUEST_ID}]})
            return httpx.Response(200, json={"requests": []})
        return runtime(request)
    client, source = make_source(handler)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(source._gateway_events)
            try:
                assert started.wait(5)
                second = executor.submit(source._gateway_events)
            finally:
                release.set()
            assert len(first.result(5).candidates) == 1
            assert second.result(5).candidates == []
        assert source._final_events == {}
    finally:
        client.close()
