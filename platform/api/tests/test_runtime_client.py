"""runtime 실제 연결 어댑터: GET만 보내고, 캡처한 runtime 응답을 계약 형식으로 낸다."""

from __future__ import annotations

import json
from copy import deepcopy
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


CAPTURE = load_json(RT / "capture.json")  # 현재 runtime에서 받은 실제 GET 응답(합성 관측 ingest + mvp-demo)
DENIED_ID = next(v["request_id"] for v in CAPTURE["endpoints"]["requests"]["requests"] if v["result"] == "DENIED")
EVIDENCE_ID = next(iter(CAPTURE["evidence"]))


class FakeRuntime:
    """캡처한 runtime 응답을 돌려주고, 받은 요청을 모두 기록한다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.endpoints = CAPTURE["endpoints"]
        self.details = CAPTURE["details"]
        self.evidence = CAPTURE["evidence"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.headers.get("authorization")))
        path = request.url.path
        m = re.fullmatch(r"/api/requests/([0-9a-f]{32})(/evidence)?", path)
        if m:
            table = self.evidence if m.group(2) else self.details
            if m.group(1) in table:
                return httpx.Response(200, json=table[m.group(1)])
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
        name = path.removeprefix("/api/")
        source = request.url.params.get("source")
        key = f"{name}?source={source}" if source else name
        if key in self.endpoints:
            return httpx.Response(200, json=self.endpoints[key])
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
        "/api/runtime/status", "/api/runtime/lab-report", "/api/update/revalidation",
        f"/api/runtime/requests/{DENIED_ID}", f"/api/runtime/requests/{EVIDENCE_ID}/evidence",
    ):
        assert live_client.get(path).status_code == 200, path
    assert fake.calls, "runtime을 한 번도 부르지 않음"
    assert {method for method, _, _ in fake.calls} == {"GET"}
    assert all(p.startswith("/api/") for _, p, _ in fake.calls)
    assert not any("/v1/" in p for _, p, _ in fake.calls)
    assert {auth for _, _, auth in fake.calls} == {f"Bearer {TOKEN}"}


@pytest.mark.parametrize("value", ["not_connected", "error", "unknown", "monitor", "monitor_only", "enforce", "mixed"])
def test_external_enforcement_modes_pass_through_contract(live_client, fake, value):
    fake.endpoints = deepcopy(fake.endpoints)
    fake.endpoints["status"]["policy"]["external_enforcement"] = value
    response = live_client.get("/api/runtime/status")
    assert response.status_code == 200
    assert response.json()["item"]["policy"]["external_enforcement"] == value


def test_configured_empty_lab_directory_stays_missing(live_client, fake):
    fake.endpoints = deepcopy(fake.endpoints)
    fake.endpoints["lab-report"] = {"state": "missing", "report": None,
        "configured_path": "/srv/serving/observation/lab-reports", "path": None,
        "generated_at": "2026-11-08T00:00:00Z", "error": "lab report directory has no JSON reports"}
    response = live_client.get("/api/runtime/lab-report")
    assert response.status_code == 200
    assert response.json()["item"]["state"] == "missing"
    assert response.json()["item"]["report"] is None


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
    gateway = [e for e in events["items"] if e["source"] == "gateway"]
    observed = [e for e in events["items"] if e["source"] != "gateway"]
    assert len(gateway) == len(CAPTURE["endpoints"]["requests"]["requests"])
    # runtime의 실제 정규화 관측(network_flow·process_exec·file_access)이 계약으로 변환된다
    assert {e["observation"]["kind"] for e in observed} == {"network_flow", "process_exec", "file_access"}
    assert all(e["observation"] is None for e in gateway)
    flow = next(e for e in observed if e["observation"]["kind"] == "network_flow")
    assert flow["outcome"] == "observed" and flow["enforcer"] is None and flow["observation"]["dst_port"]

    status = live_client.get("/api/runtime/status").json()
    assert status["invalid"] is None, status["invalid"]
    item = status["item"]
    assert item["schema_version"] == "byul-runtime-status/0.2"
    assert item["policy"]["policy_ref"]["policy_id"] == "local-policy-001"
    assert item["policy"]["bundle_id"] is None  # runtime이 모르는 값은 만들지 않는다
    assert item["policy"]["history"] and item["policy"]["validity_items"]
    assert item["backend"]["kind"] == "mock" and item["backend"]["url"]
    assert item["observations"]["by_source"] == {"hubble": 2, "tetragon": 3}
    assert {c["source"]: c["state"] for c in item["collectors"]}["metrics"] == "disconnected"

    lab = live_client.get("/api/runtime/lab-report").json()
    assert lab["invalid"] is None and lab["item"]["state"] == "not_configured"

    summary = live_client.get("/api/runtime/summary").json()
    assert summary["counts"] == {"allowed": 5, "denied": 2, "invalid": 8, "engine_rejected": 0, "error": 0, "unknown": 0}

    detail = live_client.get(f"/api/runtime/requests/{DENIED_ID}").json()
    assert detail["detail_invalid"] is None, detail["detail_invalid"]
    assert detail["row"]["outcome"] == "denied"
    d = detail["detail"]
    assert d["decision"]["result"] == "DENIED" and d["delivery"]["attempted"] is False
    assert [t["phase"] for t in d["timeline"]][-1] == "final"  # 정책 거부는 시작 기록 없이 final 하나
    assert d["evidence_state"] in ("not_received", "unknown")

    ev = live_client.get(f"/api/runtime/requests/{EVIDENCE_ID}/evidence")
    assert ev.status_code == 200 and ev.headers["content-disposition"].startswith("attachment")
    assert ev.json()["request_id"] == EVIDENCE_ID


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


@pytest.mark.parametrize("case_id,valid", [
    *[(f"AWS-{i:02d}", True) for i in range(1, 21)],
    *[(f"L-{i:02d}", True) for i in range(1, 9)],
    *[(case_id, False) for case_id in (
        "AWS-00", "AWS-21", "AWS-1", "AWS-001", "aws-01", "AWS-01\n", "xAWS-01",
        "L-00", "L-09", "L-99", "L-1",
    )],
])
def test_lab_report_contract_preserves_ids_and_run(live_client, fake, case_id, valid):
    report = {"run_id": "aws-acceptance-20200101T000000Z-1", "context": "byul-aws-lab", "namespace": "serving-lab",
              "started_at": "2020-01-01T00:00:00Z", "finished_at": "2020-01-01T00:01:00Z", "overall": "unknown",
              "cases": [{"id": case_id, "title": "AWS 시험", "status": "unknown", "summary": "미확인", "evidence": [], "limitation": None, "note": "해당 회차 선택 사례"}]}
    fake.endpoints = {**fake.endpoints, "lab-report": {"state": "ok", "report": report, "generated_at": "2020-01-01T00:01:01Z"}}
    body = live_client.get("/api/runtime/lab-report").json()
    if valid:
        assert body["invalid"] is None
        assert body["item"]["report"] == report
        assert live_client.app.state.byul.contracts.errors("runtime-lab-report", body["item"]) == []
    else:
        assert body["item"] is None and body["invalid"]


@pytest.mark.parametrize("report_schema,valid", [
    ("serving-runtime-k8s-lab-report/1.0", True),
    ("serving-runtime-aws-lab-report/1.0", True),
    ("serving-runtime-aws-lab-report/2.0", False),
    (None, False),
    (1, False),
])
def test_lab_report_optional_schema(contracts, report_schema, valid):
    from conftest import EXAMPLES

    document = load_json(EXAMPLES / "runtime-lab-report.json")
    document["report"]["schema"] = report_schema
    if report_schema == "serving-runtime-aws-lab-report/1.0":
        document["report"]["cases"][0]["id"] = "AWS-01"
    assert contracts.is_valid("runtime-lab-report", document) == valid


def test_live_events_fetch_details_in_parallel_and_keep_order():
    """원격 연결에서 첫 화면이 느려지지 않게 상세를 병렬로 묻되, 목록 순서는 그대로 둔다."""
    import threading
    import time as _time

    capture = load_json(FIXTURES / "runtime" / "capture.json")
    details = capture["details"]
    listed = capture["endpoints"]["requests"]
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/requests":
            return httpx.Response(200, json=listed)
        if path.startswith("/api/requests/"):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            _time.sleep(0.05)
            with lock:
                state["now"] -= 1
            rid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json=details[rid]) if rid in details else httpx.Response(404, json={})
        if path == "/api/observations":
            return httpx.Response(200, json={"observations": []})
        return httpx.Response(404, json={})

    source = LiveRuntimeSource(RuntimeClient("http://runtime.test", None, transport=httpx.MockTransport(fake)), "http://runtime.test")
    started = _time.monotonic()
    batch = source.events()
    elapsed = _time.monotonic() - started
    expected = [v["request_id"] for v in listed["requests"] if v["request_id"] in details]
    assert state["max"] > 1
    assert elapsed < 0.05 * len(expected)  # not one-by-one
    sequential = LiveRuntimeSource(RuntimeClient("http://runtime.test", None, transport=httpx.MockTransport(fake)), "http://runtime.test")
    sequential.DETAIL_WORKERS = 1
    assert [repr(c) for c in sequential.events().candidates] == [repr(c) for c in batch.candidates]
