"""runtime 실제 연결 어댑터: GET만 보내고, 캡처한 runtime 응답을 계약 형식으로 낸다."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from byul_platform.config import Settings
from byul_platform.main import create_app
from byul_platform.sources.runtime import LiveRuntimeSource, RuntimeClient, RuntimeUnavailable
from conftest import FIXTURES, load_json

RT = FIXTURES / "runtime"
TOKEN = "viewer-secret-token-value"


class FakeRuntime:
    """캡처한 runtime 응답을 돌려주고, 받은 요청을 모두 기록한다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.details = load_json(RT / "details.json")
        lines = (RT / "gateway_records.jsonl").read_text(encoding="utf-8").splitlines()
        self.journal = [json.loads(line) for line in lines if line.strip()]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.headers.get("authorization")))
        path = request.url.path
        m = re.fullmatch(r"/api/requests/([0-9a-f]{32})(/evidence)?", path)
        if m:
            key = m.group(1) + (m.group(2) or "")
            if key in self.details:
                return httpx.Response(200, json=self.details[key])
            # 상세를 캡처하지 않은 요청은 runtime 기록 원본(gateway_records.jsonl)으로 상세를 만든다.
            records = [r for r in self.journal if r["request_id"] == m.group(1)]
            if not records:
                return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
            return httpx.Response(200, json={"gateway_records": records, "evidence_state": "unknown"})
        name = path.removeprefix("/api/")
        file = RT / f"{name}.json"
        if file.is_file():
            return httpx.Response(200, json=load_json(file))
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})


@pytest.fixture
def fake() -> FakeRuntime:
    return FakeRuntime()


@pytest.fixture
def live_client(tmp_path: Path, fake: FakeRuntime, monkeypatch):
    real_init = RuntimeClient.__init__

    def init(self, base_url, token, **kw):
        real_init(self, base_url, token, transport=httpx.MockTransport(fake))

    monkeypatch.setattr(RuntimeClient, "__init__", init)
    app = create_app(Settings(runtime_base_url="http://runtime.test", runtime_viewer_token=TOKEN, web_dist=tmp_path / "x"))
    with TestClient(app) as client:
        yield client


def test_runtime_client_sends_only_get(live_client, fake):
    for path in (
        "/api/meta", "/api/overview", "/api/runtime/events", "/api/runtime/requests", "/api/runtime/summary",
        "/api/runtime/status", "/api/runtime/comparisons", "/api/update/revalidation",
        "/api/runtime/requests/edada386f9114c1da78338131d8fb737",
    ):
        assert live_client.get(path).status_code == 200, path
    assert fake.calls, "runtime을 한 번도 부르지 않음"
    assert {method for method, _, _ in fake.calls} == {"GET"}
    assert all(p.startswith("/api/") for _, p, _ in fake.calls)
    assert not any("/v1/" in p for _, p, _ in fake.calls)
    assert {auth for _, _, auth in fake.calls} == {f"Bearer {TOKEN}"}


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def test_runtime_client_blocks_other_methods_before_sending(fake, method):
    client = RuntimeClient("http://runtime.test", TOKEN, transport=httpx.MockTransport(fake))
    with pytest.raises(PermissionError):
        client._http.request(method, "/v1/chat/completions", json={"model": "x"})
    with pytest.raises(PermissionError):
        client._http.request(method, "/api/summary")
    assert fake.calls == []


def test_runtime_client_blocks_non_api_paths(fake):
    client = RuntimeClient("http://runtime.test", None, transport=httpx.MockTransport(fake))
    with pytest.raises(PermissionError):
        client._http.get("/v1/models")
    assert fake.calls == []


def test_live_events_and_status_are_valid_contracts(live_client):
    events = live_client.get("/api/runtime/events").json()
    assert events["source"]["mode"] == "live"
    assert events["invalid"] == []
    assert len(events["items"]) == len(load_json(RT / "requests.json")["requests"])

    status = live_client.get("/api/runtime/status").json()
    assert status["invalid"] is None
    item = status["item"]
    assert item["policy"]["policy_ref"]["policy_id"] == "local-policy-001"
    assert item["policy"]["bundle_id"] is None  # runtime이 모르는 값은 만들지 않는다
    assert item["policy"]["approval_state"] == "not_configured"
    assert {c["source"]: c["state"] for c in item["collectors"]}["metrics"] == "disconnected"

    summary = live_client.get("/api/runtime/summary").json()
    assert summary["counts"] == {"allowed": 5, "denied": 2, "invalid": 8, "engine_rejected": 0, "error": 0, "unknown": 0}
    assert summary["total"] == 15

    detail = live_client.get("/api/runtime/requests/edada386f9114c1da78338131d8fb737").json()
    assert detail["row"]["outcome"] == "denied"
    assert detail["row"]["rule_id"] == "allowed_models"
    assert detail["evidence_state"] == "not_received"


def test_token_never_leaves_the_api(live_client, caplog):
    bodies = [live_client.get(p).text for p in ("/api/meta", "/api/overview", "/api/runtime/status", "/openapi.json")]
    assert all(TOKEN not in b for b in bodies)
    assert TOKEN not in caplog.text
    assert TOKEN not in repr(Settings(runtime_viewer_token=TOKEN))


def test_runtime_unreachable_is_reported_not_hidden(tmp_path):
    def down(_request):
        raise httpx.ConnectError("refused")

    client = RuntimeClient("http://runtime.test", TOKEN, transport=httpx.MockTransport(down))
    source = LiveRuntimeSource(client, "http://runtime.test")
    with pytest.raises(RuntimeUnavailable) as info:
        source.summary()
    assert TOKEN not in str(info.value)
    assert source.ping() == "disconnected"


def test_unreachable_runtime_returns_503_with_code(tmp_path, monkeypatch):
    real_init = RuntimeClient.__init__

    def down(_request):
        raise httpx.ConnectError("refused")

    def init(self, base_url, token, **kw):
        real_init(self, base_url, token, transport=httpx.MockTransport(down))

    monkeypatch.setattr(RuntimeClient, "__init__", init)
    app = create_app(Settings(runtime_base_url="http://runtime.test", web_dist=tmp_path / "x"))
    with TestClient(app) as client:
        r = client.get("/api/runtime/events")
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "RUNTIME_UNREACHABLE"
        o = client.get("/api/overview").json()
        assert o["requests"] is None and o["requests_error"]["code"] == "RUNTIME_UNREACHABLE"
        assert client.get("/api/meta").json()["runtime_connection"] == "disconnected"
