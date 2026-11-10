from pathlib import Path
import httpx
from fastapi.testclient import TestClient

from byul_platform.config import Settings
from byul_platform.main import create_app
from byul_platform.sources.runtime import RuntimeClient
from serving_runtime.mvp.route_rejections import RouteRejections


def test_route_audit_reads_bounded_separate_journal_and_preserves_failures(tmp_path, monkeypatch):
    audit = RouteRejections(tmp_path, scope={"environment": "lab", "run_id": "r"}, limit=1)
    audit.record("a" * 32, "POST", "/unsupported", 404, "NOT_FOUND")
    audit.record("b" * 32, "GET", "/missing", 404, "NOT_FOUND")
    calls = []
    def transport(request):
        calls.append((request.method, request.url.path, str(request.url.params), request.headers.get("authorization")))
        if request.url.path == "/api/route-rejections":
            return httpx.Response(200, json=audit.query(limit=int(request.url.params["limit"])))
        return httpx.Response(404)
    original = RuntimeClient.__init__
    def init(self, base_url, token, **kwargs):
        original(self, base_url, token, transport=httpx.MockTransport(transport))
    monkeypatch.setattr(RuntimeClient, "__init__", init)
    app = create_app(Settings(runtime_base_url="http://runtime.invalid", runtime_viewer_token="private-viewer", platform_data_dir=tmp_path / "platform"))
    with TestClient(app) as client:
        document = client.get("/api/runtime/route-audit?limit=1").json()
        assert document["state"] == "ok"
        assert document["item"]["status"]["overflow_count"] == 1
        assert document["item"]["items"][0]["path"] == "/unsupported"
        assert document["item"]["items"][0]["actor"]["state"] == "unattributed"
        assert "private-viewer" not in str(document)
        assert client.get("/api/runtime/route-audit?limit=201").status_code == 422
    assert calls == [("GET", "/api/route-rejections", "limit=1", "Bearer private-viewer")]


def test_unconfigured_audit_is_explicit_not_empty_success(tmp_path):
    with TestClient(create_app(Settings(platform_data_dir=tmp_path))) as client:
        result = client.get("/api/runtime/route-audit").json()
    assert result["state"] == "not_configured" and result["item"] is None


def test_old_and_malformed_runtime_audit_are_explicit(tmp_path, monkeypatch):
    original = RuntimeClient.__init__
    result = {"mode": "old"}
    def transport(request):
        return httpx.Response(404) if result["mode"] == "old" else httpx.Response(200, json={"items": []})
    def init(self, base_url, token, **kwargs):
        original(self, base_url, token, transport=httpx.MockTransport(transport))
    monkeypatch.setattr(RuntimeClient, "__init__", init)
    with TestClient(create_app(Settings(runtime_base_url="http://runtime.invalid", platform_data_dir=tmp_path))) as client:
        assert client.get("/api/runtime/route-audit").json()["state"] == "unsupported"
        result["mode"] = "invalid"
        payload = client.get("/api/runtime/route-audit").json()
        assert payload["state"] == "invalid" and payload["item"] is None
