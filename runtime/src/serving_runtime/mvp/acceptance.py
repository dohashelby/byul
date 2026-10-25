"""Self-contained MVP acceptance run and human/machine-readable reports."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import http.client
import io
import json
import platform
import socket
import struct
import sys
import tempfile
import threading
import time
from typing import Any
from contextlib import redirect_stderr
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .app import GatewayApp
from .assets import snapshot as snapshot_assets
from .auth import CHAT_ROUTE, token_digest
from .backend import load_backend_config
from .config import MVPConfig
from .demo import DemoConnectionError, DemoResult, run_demo_cases
from .mock_engine import MockEngine
from .fake_backend import FakeBackend
from .policy import load_policy
from .store import JsonlStore


_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE_POLICY = _ROOT / "examples" / "mvp" / "policy.json"
_VLLM_METRICS_DOC = "https://docs.vllm.ai/en/latest/usage/metrics/"


@dataclass
class AcceptanceResult:
    case_id: str
    command: str
    expected: str
    actual: Any
    passed: bool
    evidence_request_ids: list[str]


def _demo_group(case_id: str, results: list[DemoResult]) -> AcceptanceResult:
    selected = [
        result for result in results
        if result.case_id == case_id or result.case_id.startswith(case_id)
    ]
    return AcceptanceResult(
        case_id,
        "POST /v1/chat/completions; GET /api/requests/{request_id}",
        "; ".join(result.expected for result in selected),
        [asdict(result) for result in selected],
        bool(selected) and all(result.passed for result in selected),
        [result.request_id for result in selected if result.request_id is not None],
    )


class _Runner:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.engine: MockEngine | None = None
        self.fake_backend: FakeBackend | None = None
        self.gateway: GatewayApp | None = None
        self._backend_config_index = 0

    def stop(self) -> None:
        if self.gateway is not None:
            self.gateway.stop()
            self.gateway = None
        if self.engine is not None:
            self.engine.stop()
            self.engine = None
        if self.fake_backend is not None:
            self.fake_backend.stop()
            self.fake_backend = None

    def _external_document(self, flavor: str, **updates: Any) -> dict[str, Any]:
        if self.fake_backend is None:
            raise RuntimeError("fake backend is not running")
        if flavor not in {"vllm", "sglang"}:
            raise ValueError("acceptance backend flavor must be vllm or sglang")
        metric = (
            "vllm:num_requests_running"
            if flavor == "vllm"
            else "sglang:num_running_reqs"
        )
        source_doc = (
            _VLLM_METRICS_DOC
            if flavor == "vllm"
            else "https://github.com/sgl-project/sglang/blob/v0.5.21/python/sglang/srt/observability/metrics_collector.py"
        )
        document: dict[str, Any] = {
            "schema_version": "runtime-backend/1.0",
            "kind": flavor,
            "base_url": self.fake_backend.base_url,
            "model_map": {"test-model": "backend-test-model"},
            "health_path": "/health",
            "models_path": "/v1/models" if flavor == "vllm" else "/get_model_info",
            "version_path": "/version" if flavor == "vllm" else None,
            "metrics_url": (
                f"http://{self.fake_backend.address[0]}:"
                f"{self.fake_backend.address[1]}/metrics"
            ),
            "timeouts": {
                "relay_timeout_s": 0.2,
                "stream_idle_timeout_s": 0.3,
                "request_deadline_s": 2,
            },
            "metrics_map": {
                "requests.running": {
                    "metric": metric,
                    "type": "gauge",
                    "unit": "requests",
                    "source_doc": source_doc,
                    "verified": False,
                },
                "requests.absent": {
                    "metric": f"{flavor}:not_in_fake_response",
                    "type": "gauge",
                    "unit": "requests",
                    "source_doc": source_doc,
                    "verified": False,
                },
            },
        }
        document.update(updates)
        return document

    def start_external(
        self,
        flavor: str,
        *,
        policy_path: Path = _EXAMPLE_POLICY,
        fake_options: dict[str, Any] | None = None,
        backend_updates: dict[str, Any] | None = None,
        credential_environ: dict[str, str] | None = None,
        data_dir: Path | None = None,
        collector_interval_s: float = 0.05,
        collector_stale_after_s: float = 1.0,
        baseline_path: Path | None = None,
        manifest_path: Path | None = None,
        asset_manifest_path: Path | None = None,
        asset_interval_s: float = 0.05,
    ) -> None:
        self.stop()
        active_data_dir = data_dir or self.data_dir
        options = dict(fake_options or {})
        options["flavor"] = flavor
        self.fake_backend = FakeBackend(port=0, **options).start()
        document = self._external_document(flavor, **(backend_updates or {}))
        self._backend_config_index += 1
        path = active_data_dir / f"backend-{flavor}-{self._backend_config_index}.json"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8")
        config = MVPConfig(
            gateway_port=0,
            backend=flavor,
            backend_config_path=str(path),
            policy_path=str(policy_path),
            data_dir=str(active_data_dir),
            collector_interval_s=collector_interval_s,
            collector_stale_after_s=collector_stale_after_s,
            baseline_path=str(baseline_path) if baseline_path else None,
            manifest_path=str(manifest_path) if manifest_path else None,
            asset_manifest_path=(str(asset_manifest_path) if asset_manifest_path else None),
            asset_interval_s=asset_interval_s,
        )
        backend = load_backend_config(path, environ=credential_environ or {})
        self.gateway = GatewayApp(
            config,
            load_policy(policy_path),
            backend_config=backend,
        ).start()

    def start_vllm(
        self,
        *,
        policy_path: Path = _EXAMPLE_POLICY,
        fake_options: dict[str, Any] | None = None,
        backend_updates: dict[str, Any] | None = None,
        credential_environ: dict[str, str] | None = None,
        data_dir: Path | None = None,
        collector_interval_s: float = 0.05,
        collector_stale_after_s: float = 1.0,
    ) -> None:
        self.start_external(
            "vllm",
            policy_path=policy_path,
            fake_options=fake_options,
            backend_updates=backend_updates,
            credential_environ=credential_environ,
            data_dir=data_dir,
            collector_interval_s=collector_interval_s,
            collector_stale_after_s=collector_stale_after_s,
        )

    def start(
        self,
        *,
        policy_path: Path = _EXAMPLE_POLICY,
        response_delay_s: float = 0,
        relay_timeout_s: float = 0.5,
        stream_idle_timeout_s: float = 1.0,
        request_deadline_s: float = 5.0,
        max_concurrent_requests: int = 2,
        data_dir: Path | None = None,
        engine_options: dict[str, Any] | None = None,
        revocations_path: Path | None = None,
        baseline_path: Path | None = None,
        manifest_path: Path | None = None,
        collector_interval_s: float = 5.0,
        collector_stale_after_s: float = 15.0,
        request_body_timeout_s: float = 10.0,
        auth_config_path: Path | None = None,
        target_path: Path | None = None,
        observation_feeds: tuple[tuple[str, str], ...] = (),
        observation_heartbeats: tuple[tuple[str, str], ...] = (),
        observation_origin: str = "real",
    ) -> None:
        self.stop()
        active_data_dir = data_dir or self.data_dir
        self.engine = MockEngine(
            port=0,
            response_delay_s=response_delay_s,
            receipts_path=active_data_dir / "engine_receipts.jsonl",
            **(engine_options or {}),
        ).start()
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(policy_path),
            relay_timeout_s=relay_timeout_s,
            stream_idle_timeout_s=stream_idle_timeout_s,
            request_deadline_s=request_deadline_s,
            max_concurrent_requests=max_concurrent_requests,
            data_dir=str(active_data_dir),
            revocations_path=str(revocations_path) if revocations_path else None,
            baseline_path=str(baseline_path) if baseline_path else None,
            manifest_path=str(manifest_path) if manifest_path else None,
            collector_interval_s=collector_interval_s,
            collector_stale_after_s=collector_stale_after_s,
            request_body_timeout_s=request_body_timeout_s,
            auth_config_path=str(auth_config_path) if auth_config_path else None,
            target_path=str(target_path) if target_path else None,
            observation_feeds=observation_feeds,
            observation_heartbeats=observation_heartbeats,
            observation_origin=observation_origin,
        )
        self.gateway = GatewayApp(config, load_policy(policy_path), self.engine.address).start()

    def start_gateway(
        self,
        policy_path: Path,
        *,
        data_dir: Path | None = None,
        relay_timeout_s: float = 0.5,
        stream_idle_timeout_s: float = 1.0,
        request_deadline_s: float = 5.0,
        max_concurrent_requests: int = 2,
        revocations_path: Path | None = None,
        baseline_path: Path | None = None,
        manifest_path: Path | None = None,
        collector_interval_s: float = 5.0,
        collector_stale_after_s: float = 15.0,
        auth_config_path: Path | None = None,
        target_path: Path | None = None,
    ) -> None:
        if self.engine is None:
            raise RuntimeError("mock engine is not running")
        if self.gateway is not None:
            self.gateway.stop()
        active_data_dir = data_dir or self.data_dir
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(policy_path),
            relay_timeout_s=relay_timeout_s,
            stream_idle_timeout_s=stream_idle_timeout_s,
            request_deadline_s=request_deadline_s,
            max_concurrent_requests=max_concurrent_requests,
            data_dir=str(active_data_dir),
            revocations_path=str(revocations_path) if revocations_path else None,
            baseline_path=str(baseline_path) if baseline_path else None,
            manifest_path=str(manifest_path) if manifest_path else None,
            collector_interval_s=collector_interval_s,
            collector_stale_after_s=collector_stale_after_s,
            auth_config_path=str(auth_config_path) if auth_config_path else None,
            target_path=str(target_path) if target_path else None,
        )
        self.gateway = GatewayApp(config, load_policy(policy_path), self.engine.address).start()

    @property
    def base_url(self) -> str:
        if self.gateway is None:
            raise RuntimeError("gateway is not running")
        host, port = self.gateway.address
        return f"http://{host}:{port}"

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str = "application/json",
        timeout: float = 3,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any], bytes]:
        request_headers = {"Content-Type": content_type} if body is not None else {}
        request_headers.update(headers or {})
        request = Request(
            self.base_url + path,
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as error:
            with error:
                return error.code, dict(error.headers), error.read()
        except (URLError, TimeoutError, OSError) as error:
            raise DemoConnectionError(str(error)) from error

    def json_request(self, method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any], dict[str, Any]]:
        status, headers, body = self.request(method, path, **kwargs)
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            document = {"_invalid_json": body.decode("utf-8", errors="replace")}
        if not isinstance(document, dict):
            document = {"_value": document}
        return status, headers, document

    def post_chat(
        self,
        max_tokens: int = 64,
        *,
        prompt: str = "acceptance",
        stream: bool = False,
        model: str = "test-model",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str | None, dict[str, Any]]:
        body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "stream": stream,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        status, headers, document = self.json_request(
            "POST", "/v1/chat/completions", body=body, headers=headers
        )
        return status, headers.get("X-Request-ID"), document

    def get_json(self, path: str) -> tuple[int, dict[str, Any]]:
        status, _, document = self.json_request("GET", path)
        return status, document


def _run_07(runner: _Runner) -> AcceptanceResult:
    if runner.engine is None:
        raise RuntimeError("mock engine is not running")
    runner.engine.stop()
    runner.engine = None
    stopped_status, stopped_id, stopped_body = runner.post_chat()
    detail_status, stopped_detail = runner.get_json(f"/api/requests/{stopped_id}")
    stopped_evidence = stopped_detail.get("evidence_state")

    runner.start(response_delay_s=0.2, relay_timeout_s=0.05)
    timeout_status, timeout_id, timeout_body = runner.post_chat()
    passed = (
        stopped_status == 502
        and stopped_body.get("error", {}).get("code") == "ENGINE_UNREACHABLE"
        and detail_status == 200
        and stopped_evidence == "unknown"
        and timeout_status == 504
        and timeout_body.get("error", {}).get("code") == "ENGINE_TIMEOUT"
    )
    return AcceptanceResult(
        "MVP-07",
        "모의 엔진 정지 후 POST; 응답 지연을 timeout보다 길게 설정 후 POST",
        "502 ENGINE_UNREACHABLE와 증거 unknown; 504 ENGINE_TIMEOUT",
        {
            "stopped": {
                "http": stopped_status,
                "error": stopped_body.get("error"),
                "detail_http": detail_status,
                "evidence_state": stopped_evidence,
            },
            "delayed": {"http": timeout_status, "error": timeout_body.get("error")},
        },
        passed,
        [item for item in (stopped_id, timeout_id) if item],
    )


def _run_08(runner: _Runner) -> AcceptanceResult:
    missing = runner.data_dir / "missing-policy.json"
    invalid = runner.data_dir / "invalid-policy.json"
    invalid.write_text("{}\n", encoding="utf-8")
    actual: dict[str, Any] = {}
    request_ids: list[str] = []
    passed = True
    for label, policy_path in (("missing", missing), ("invalid", invalid)):
        runner.start_gateway(policy_path)
        inference, request_id, body = runner.post_chat()
        status_code, _ = runner.get_json("/api/status")
        requests_code, _ = runner.get_json("/api/requests")
        actual[label] = {
            "inference_http": inference,
            "error": body.get("error"),
            "status_http": status_code,
            "requests_http": requests_code,
        }
        if request_id:
            request_ids.append(request_id)
        passed = passed and inference == 503 and status_code == 200 and requests_code == 200
    return AcceptanceResult(
        "MVP-08",
        "정책 없음/잘못된 정책으로 Gateway 시작 후 추론·현황·목록 조회",
        "각 추론 503; /api/status와 /api/requests 200",
        actual,
        passed,
        request_ids,
    )


def _run_09(runner: _Runner) -> AcceptanceResult:
    runner.start()
    first_session = runner.gateway.observation_session_id if runner.gateway else None
    _, denied_id, _ = runner.post_chat(129)
    _, allowed_id, _ = runner.post_chat(64)
    runner.stop()
    runner.start()
    second_session = runner.gateway.observation_session_id if runner.gateway else None
    list_status, listed = runner.get_json("/api/requests")
    detail_status, detail = runner.get_json(f"/api/requests/{denied_id}")
    listed_ids = [item.get("request_id") for item in listed.get("requests", [])]
    previous_item = next(
        (item for item in listed.get("requests", []) if item.get("request_id") == denied_id),
        {},
    )
    passed = (
        list_status == 200
        and detail_status == 200
        and first_session != second_session
        and denied_id in listed_ids
        and allowed_id in listed_ids
        and previous_item.get("current_session") is False
        and detail.get("evidence_state") == "unknown"
    )
    return AcceptanceResult(
        "MVP-09",
        "같은 data_dir에서 Gateway·모의 엔진 중지 후 재시작; 이전 목록·상세 조회",
        "이전 요청 유지; 세션 ID 변경; 이전 거부 요청 current_session=false, 증거 unknown",
        {
            "list_http": list_status,
            "detail_http": detail_status,
            "first_session": first_session,
            "second_session": second_session,
            "listed_request_ids": listed_ids,
            "previous_current_session": previous_item.get("current_session"),
            "evidence_state": detail.get("evidence_state"),
        },
        passed,
        [item for item in (denied_id, allowed_id) if item],
    )


def _run_10(runner: _Runner) -> AcceptanceResult:
    if runner.gateway is None or runner.engine is None:
        raise RuntimeError("servers are not running")
    gateway_store = JsonlStore(runner.data_dir / "gateway_records.jsonl")
    before_summary_status, before_summary = runner.get_json("/api/summary")
    with gateway_store.path.open("ab") as output:
        output.write(b"{broken acceptance line\n")
    incomplete_id = "f" * 32
    gateway_store.append(
        {
            "phase": "started",
            "request_id": incomplete_id,
            "observation_session_id": runner.gateway.observation_session_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "decision": {"result": "ALLOWED", "reason_code": "REQUEST_ALLOWED", "http_status": 200},
            "request": {"model": "test-model", "max_tokens": 64},
            "delivery": {"attempted": False, "backend_receipt": "not_queried"},
        }
    )
    summary_status, summary = runner.get_json("/api/summary")
    incomplete_status, incomplete_list = runner.get_json("/api/requests?result=INCOMPLETE")

    blocker = runner.data_dir / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    receipt_count = len(runner.engine.receipts)
    original_gateway = runner.gateway
    original_gateway.stop()
    runner.gateway = None
    runner.start_gateway(_EXAMPLE_POLICY, data_dir=blocker / "sub")
    store_status, unavailable_id, unavailable_body = runner.post_chat()
    receipt_count_after = len(runner.engine.receipts)
    corrupt_before = before_summary.get("corrupt_lines", {}).get("gateway", 0)
    corrupt_after = summary.get("corrupt_lines", {}).get("gateway", 0)
    detail_lines = summary.get("corrupt_details", {}).get("gateway", [])
    incomplete_ids = [item.get("request_id") for item in incomplete_list.get("requests", [])]
    passed = (
        before_summary_status == 200
        and summary_status == 200
        and corrupt_after == corrupt_before + 1
        and any(item.get("line") for item in detail_lines)
        and incomplete_status == 200
        and incomplete_id in incomplete_ids
        and store_status == 503
        and unavailable_body.get("error", {}).get("code") == "STORE_UNAVAILABLE"
        and receipt_count_after == receipt_count
    )
    return AcceptanceResult(
        "MVP-10",
        "gateway JSONL 손상 줄·started-only 추가 후 조회; 기록 경로를 파일 아래로 지정 후 POST",
        "손상 수·위치 증가, INCOMPLETE 조회, 저장 불가 503 STORE_UNAVAILABLE, 엔진 미호출",
        {
            "summary_http": summary_status,
            "corrupt_before": corrupt_before,
            "corrupt_after": corrupt_after,
            "corrupt_details": detail_lines,
            "incomplete_http": incomplete_status,
            "incomplete_request_ids": incomplete_ids,
            "store_http": store_status,
            "store_error": unavailable_body.get("error"),
            "engine_receipts_before_after": [receipt_count, receipt_count_after],
        },
        passed,
        [incomplete_id] + ([unavailable_id] if unavailable_id else []),
    )


def _run_11(runner: _Runner) -> AcceptanceResult:
    runner.start()
    prompt = '<script>alert("mvp")</script>'
    post_status, request_id, _ = runner.post_chat(prompt=prompt)
    list_status, listed = runner.get_json("/api/requests?result=ALLOWED")
    detail_status, detail = runner.get_json(f"/api/requests/{request_id}")
    evidence_status, _, evidence_body = runner.request(
        "GET", f"/api/requests/{request_id}/evidence"
    )
    try:
        evidence = json.loads(evidence_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        evidence = {}
    item = next(
        (entry for entry in listed.get("requests", []) if entry.get("request_id") == request_id),
        {},
    )
    final_record = next(
        (record for record in reversed(detail.get("gateway_records", []))
         if record.get("phase") == "final"),
        {},
    )
    raw_records = b""
    for name in ("gateway_records.jsonl", "engine_receipts.jsonl"):
        path = runner.data_dir / name
        if path.exists():
            raw_records += path.read_bytes()
    policy_id = final_record.get("policy", {}).get("policy_id")
    evidence_records = evidence.get("gateway_records", [])
    evidence_policy_id = (
        evidence_records[-1].get("policy", {}).get("policy_id")
        if evidence_records and isinstance(evidence_records[-1], dict)
        else None
    )
    passed = (
        post_status == 200
        and list_status == 200
        and detail_status == 200
        and evidence_status == 200
        and item.get("request_id") == request_id
        and item.get("result") == "ALLOWED"
        and detail.get("request_id") == request_id
        and detail.get("final_state", {}).get("result") == "ALLOWED"
        and evidence.get("request_id") == request_id
        and evidence.get("final_state", {}).get("result") == "ALLOWED"
        and evidence_policy_id == policy_id
        and prompt.encode("utf-8") not in raw_records
    )
    return AcceptanceResult(
        "MVP-11",
        "스크립트 문자열 포함 요청 후 ALLOWED 필터·상세·증거 JSON·기록 파일 대조",
        "request_id/정책/결과 일치; 프롬프트 원문 미기록",
        {
            "post_http": post_status,
            "list_http": list_status,
            "detail_http": detail_status,
            "evidence_http": evidence_status,
            "list_result": item.get("result"),
            "detail_result": detail.get("final_state", {}).get("result"),
            "evidence_result": evidence.get("final_state", {}).get("result"),
            "policy_id": policy_id,
            "prompt_found_in_records": prompt.encode("utf-8") in raw_records,
        },
        passed,
        [request_id] if request_id else [],
    )


def _stream_body(*, max_tokens: int = 64, model: str = "test-model") -> bytes:
    return json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "stream acceptance"}],
            "max_tokens": max_tokens,
            "stream": True,
        }
    ).encode("utf-8")


def _wait_for(get_value: Any, *, timeout: float = 3.0) -> Any:
    deadline = time.monotonic() + timeout
    wake = threading.Event()
    while time.monotonic() < deadline:
        value = get_value()
        if value:
            return value
        wake.wait(0.02)
    return None


def _final_record(runner: _Runner, request_id: str) -> dict[str, Any] | None:
    status, detail = runner.get_json(f"/api/requests/{request_id}")
    if status != 200:
        return None
    return next(
        (
            item
            for item in reversed(detail.get("gateway_records", []))
            if isinstance(item, dict) and item.get("phase") == "final"
        ),
        None,
    )


def _run_s1_01(runner: _Runner) -> AcceptanceResult:
    runner.start(
        engine_options={"stream_events": 3, "stream_interval_s": 0.12, "split_writes": True}
    )
    if runner.gateway is None or runner.engine is None:
        raise RuntimeError("servers are not running")
    connection = http.client.HTTPConnection(*runner.gateway.address, timeout=3)
    connection.request(
        "POST",
        "/v1/chat/completions",
        body=_stream_body(),
        headers={"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    request_id = response.getheader("X-Request-ID")
    first = bytearray()
    while b"\n\n" not in first:
        part = response.read1(7)
        if not part:
            break
        first.extend(part)
    first_at = time.monotonic()
    rest = response.read()
    done_at = time.monotonic()
    connection.close()
    relayed = bytes(first) + rest
    final = _wait_for(lambda: _final_record(runner, request_id)) if request_id else None
    backend = runner.engine.stream_transmissions.get(request_id or "")
    passed = (
        response.status == 200
        and response.getheader("Content-Type") == "text/event-stream"
        and response.getheader("Content-Length") is None
        and response.getheader("Transfer-Encoding") is None
        and b"data: [DONE]\n\n" not in bytes(first)
        and b"data: [DONE]\n\n" in relayed
        and "한글✓".encode("utf-8") in relayed
        and relayed == backend
        and first_at < done_at
        and final is not None
        and final.get("final_outcome") == "completed"
        and final.get("decision", {}).get("result") == "ALLOWED"
    )
    return AcceptanceResult(
        "S1-01",
        "분할 UTF-8·지연 SSE를 작은 read 단위로 수신",
        "[DONE] 전 첫 이벤트, backend 송신 바이트와 동일, final completed",
        {
            "http": response.status,
            "bytes_equal": relayed == backend,
            "first_before_done": b"data: [DONE]\n\n" not in bytes(first) and first_at < done_at,
            "done": b"data: [DONE]\n\n" in relayed,
            "unicode": "한글✓".encode("utf-8") in relayed,
            "final_outcome": final.get("final_outcome") if final else None,
        },
        passed,
        [request_id] if request_id else [],
    )


def _run_s1_02(runner: _Runner) -> AcceptanceResult:
    runner.start()
    if runner.engine is None:
        raise RuntimeError("mock engine is not running")
    before = len(runner.engine.receipts)
    model_status, model_id, model_body = runner.post_chat(stream=True, model="other-model")
    limit_status, limit_id, limit_body = runner.post_chat(stream=True, max_tokens=129)
    after = len(runner.engine.receipts)
    passed = (
        model_status == 403
        and model_body.get("error", {}).get("code") == "MODEL_NOT_ALLOWED"
        and limit_status == 403
        and limit_body.get("error", {}).get("code") == "MAX_TOKENS_EXCEEDED"
        and before == after == 0
    )
    return AcceptanceResult(
        "S1-02",
        "stream:true 금지 모델·max_tokens 129 요청",
        "각 403, 모의 엔진 수신 0",
        {
            "model_http": model_status,
            "model_error": model_body.get("error"),
            "limit_http": limit_status,
            "limit_error": limit_body.get("error"),
            "engine_receipts": after,
        },
        passed,
        [item for item in (model_id, limit_id) if item],
    )


def _run_s1_03(runner: _Runner) -> AcceptanceResult:
    runner.start(
        engine_options={"stream_events": 50, "stream_interval_s": 0.03, "split_writes": True}
    )
    if runner.gateway is None:
        raise RuntimeError("gateway is not running")
    client = socket.create_connection(runner.gateway.address, timeout=3)
    body = _stream_body()
    client.sendall(
        b"POST /v1/chat/completions HTTP/1.0\r\n"
        + b"Host: 127.0.0.1\r\nContent-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        + body
    )
    received = bytearray()
    while b"\r\n\r\n" not in received or b"\n\n" not in received.split(b"\r\n\r\n", 1)[1]:
        part = client.recv(256)
        if not part:
            break
        received.extend(part)
    header = received.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")
    request_id = next(
        (line.split(":", 1)[1].strip() for line in header.split("\r\n") if line.lower().startswith("x-request-id:")),
        None,
    )
    client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    client.close()
    final = _wait_for(lambda: _final_record(runner, request_id), timeout=4) if request_id else None
    status_code, status_body = runner.get_json("/api/status")
    next_status, next_id, _ = runner.post_chat()
    passed = (
        final is not None
        and final.get("final_outcome") == "client_disconnected"
        and final.get("decision", {}).get("reason_code") == "CLIENT_DISCONNECTED"
        and status_code == 200
        and status_body.get("concurrency", {}).get("in_flight") == 0
        and next_status == 200
    )
    return AcceptanceResult(
        "S1-03",
        "첫 SSE 이벤트 후 RST close; final·슬롯을 폴링하고 일반 요청 재시도",
        "client_disconnected, in_flight 0, 후속 일반 요청 200",
        {
            "final_outcome": final.get("final_outcome") if final else None,
            "backend_cancel": final.get("backend_cancel") if final else None,
            "in_flight": status_body.get("concurrency", {}).get("in_flight"),
            "next_http": next_status,
        },
        passed,
        [item for item in (request_id, next_id) if item],
    )


def _run_s1_04(runner: _Runner) -> AcceptanceResult:
    runner.start(relay_timeout_s=0.05, engine_options={"header_delay_s": 0.2})
    header_status, header_id, header_body = runner.post_chat(stream=True)
    _, header_runtime = runner.get_json("/api/status")

    runner.start(
        stream_idle_timeout_s=0.08,
        engine_options={"stall_after_events": 1, "stream_interval_s": 0},
    )
    idle_status, idle_headers, idle_body = runner.request(
        "POST", "/v1/chat/completions", body=_stream_body(), timeout=2
    )
    idle_id = idle_headers.get("X-Request-ID")
    idle_final = _wait_for(lambda: _final_record(runner, idle_id)) if idle_id else None
    _, idle_runtime = runner.get_json("/api/status")

    runner.start(engine_options={"truncate_after_events": 1, "stream_interval_s": 0})
    truncate_status, truncate_headers, truncate_body = runner.request(
        "POST", "/v1/chat/completions", body=_stream_body(), timeout=2
    )
    truncate_id = truncate_headers.get("X-Request-ID")
    truncate_final = _wait_for(lambda: _final_record(runner, truncate_id)) if truncate_id else None
    _, truncate_runtime = runner.get_json("/api/status")
    passed = (
        header_status == 504
        and header_body.get("error", {}).get("code") == "ENGINE_TIMEOUT"
        and header_runtime.get("concurrency", {}).get("in_flight") == 0
        and idle_status == 200
        and b"[DONE]" not in idle_body
        and idle_final is not None
        and idle_final.get("final_outcome") == "idle_timeout"
        and idle_runtime.get("concurrency", {}).get("in_flight") == 0
        and truncate_status == 200
        and b"[DONE]" not in truncate_body
        and truncate_final is not None
        and truncate_final.get("final_outcome") == "backend_truncated"
        and truncate_runtime.get("concurrency", {}).get("in_flight") == 0
    )
    return AcceptanceResult(
        "S1-04",
        "헤더 지연·이벤트 후 stall·[DONE] 전 절단",
        "504 / 200+idle_timeout / 200+backend_truncated, 각 슬롯 0",
        {
            "header": [header_status, header_body.get("error", {}).get("code")],
            "idle": [idle_status, idle_final.get("final_outcome") if idle_final else None],
            "truncate": [
                truncate_status,
                truncate_final.get("final_outcome") if truncate_final else None,
            ],
        },
        passed,
        [item for item in (header_id, idle_id, truncate_id) if item],
    )


def _run_s1_05(runner: _Runner) -> AcceptanceResult:
    runner.start(
        stream_idle_timeout_s=3,
        request_deadline_s=5,
        engine_options={"stall_after_events": 1, "stream_interval_s": 0.01},
    )
    if runner.gateway is None or runner.engine is None:
        raise RuntimeError("servers are not running")
    ready = [threading.Event(), threading.Event()]
    results: list[dict[str, Any]] = [{}, {}]

    def consume(index: int) -> None:
        connection = http.client.HTTPConnection(*runner.gateway.address, timeout=4)
        try:
            connection.request(
                "POST",
                "/v1/chat/completions",
                body=_stream_body(),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            results[index]["request_id"] = response.getheader("X-Request-ID")
            first = bytearray()
            while b"\n\n" not in first:
                first.extend(response.read1(64))
            ready[index].set()
            results[index]["body"] = bytes(first) + response.read()
            results[index]["status"] = response.status
        finally:
            connection.close()

    threads = [threading.Thread(target=consume, args=(index,), daemon=True) for index in range(2)]
    for thread in threads:
        thread.start()
    both_ready = all(event.wait(2) for event in ready)
    receipt_count = len(runner.engine.receipts)
    third_status, third_headers, third_body = runner.json_request(
        "POST", "/v1/chat/completions", body=_stream_body().replace(b'"stream": true', b'"stream": false')
    )
    third_id = third_headers.get("X-Request-ID")
    receipt_count_after = len(runner.engine.receipts)
    runner.engine.release_stalled_streams()
    for thread in threads:
        thread.join(timeout=4)
    finals = [
        _wait_for(lambda request_id=item.get("request_id"): _final_record(runner, request_id))
        for item in results
    ]
    _, runtime = runner.get_json("/api/status")
    passed = (
        both_ready
        and receipt_count == receipt_count_after == 2
        and third_status == 429
        and third_body.get("error", {}).get("code") == "CONCURRENCY_LIMIT"
        and third_headers.get("Retry-After") == "1"
        and all(not thread.is_alive() for thread in threads)
        and all(item.get("status") == 200 and b"[DONE]" in item.get("body", b"") for item in results)
        and all(final and final.get("final_outcome") == "completed" for final in finals)
        and runtime.get("concurrency", {}).get("in_flight") == 0
    )
    return AcceptanceResult(
        "S1-05",
        "stall 중 stream 2개 뒤 세 번째 일반 요청",
        "429 Retry-After 1·엔진 미수신, 앞 두 stream 이후 completed",
        {
            "both_ready": both_ready,
            "third_http": third_status,
            "third_error": third_body.get("error"),
            "retry_after": third_headers.get("Retry-After"),
            "engine_receipts_before_after": [receipt_count, receipt_count_after],
            "stream_outcomes": [item.get("final_outcome") if item else None for item in finals],
            "in_flight": runtime.get("concurrency", {}).get("in_flight"),
        },
        passed,
        [
            item
            for item in [*(result.get("request_id") for result in results), third_id]
            if item
        ],
    )


def _run_s1_06(runner: _Runner) -> AcceptanceResult:
    runner.start()
    first_status, first_headers, first_body = runner.request(
        "POST", "/v1/chat/completions", body=_stream_body(), timeout=2
    )
    first_id = first_headers.get("X-Request-ID")
    first_session = runner.gateway.observation_session_id if runner.gateway else None
    runner.stop()
    runner.start()
    second_session = runner.gateway.observation_session_id if runner.gateway else None
    detail_status, _ = runner.get_json(f"/api/requests/{first_id}")

    if runner.engine is None:
        raise RuntimeError("mock engine is not running")
    blocker = runner.data_dir / "s1-not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    before = len(runner.engine.receipts)
    runner.start_gateway(_EXAMPLE_POLICY, data_dir=blocker / "sub")
    store_status, store_id, store_body = runner.post_chat(stream=True)
    after = len(runner.engine.receipts)

    runner.start_gateway(_EXAMPLE_POLICY, data_dir=runner.data_dir)
    regular_status, regular_id, _ = runner.post_chat()
    records, _ = JsonlStore(runner.data_dir / "gateway_records.jsonl").read_all()
    final_counts: dict[str, int] = {}
    for record in records:
        if record.get("phase") == "final" and isinstance(record.get("request_id"), str):
            request_id = record["request_id"]
            final_counts[request_id] = final_counts.get(request_id, 0) + 1
    passed = (
        first_status == 200
        and b"data: [DONE]\n\n" in first_body
        and first_session != second_session
        and detail_status == 200
        and store_status == 503
        and store_body.get("error", {}).get("code") == "STORE_UNAVAILABLE"
        and before == after
        and regular_status == 200
        and final_counts.get(first_id) == 1
        and final_counts.get(regular_id) == 1
        and all(count == 1 for count in final_counts.values())
    )
    return AcceptanceResult(
        "S1-06",
        "stream 후 재시작 조회·저장 실패 stream·일반 요청 회귀·final 개수 확인",
        "이전 조회 200, 저장 실패 503·엔진 미호출, 일반 200, final 요청별 1개",
        {
            "stream_http_done": [first_status, b"[DONE]" in first_body],
            "sessions_differ": first_session != second_session,
            "detail_http": detail_status,
            "store_http_error": [store_status, store_body.get("error", {}).get("code")],
            "engine_receipts_before_after": [before, after],
            "regular_http": regular_status,
            "duplicate_final_ids": [key for key, count in final_counts.items() if count != 1],
        },
        passed,
        [item for item in (first_id, store_id, regular_id) if item],
    )


def _write_s2_policy(path: Path, policy_id: str, version: str, **updates: Any) -> None:
    document: dict[str, Any] = {
        "schema_version": "runtime-mvp/1.1",
        "policy_id": policy_id,
        "policy_version": version,
        "target_id": "mock-engine-001",
        "allowed_models": ["test-model"],
        "output_default_tokens": 128,
        "output_max_tokens": 128,
        "max_body_bytes": 65536,
    }
    document.update(updates)
    path.write_text(json.dumps(document), encoding="utf-8")


def _run_s2_01(runner: _Runner) -> AcceptanceResult:
    policy_a = runner.data_dir / "s2-policy-a.json"
    policy_b = runner.data_dir / "s2-policy-b.json"
    _write_s2_policy(policy_a, "policy-a", "1")
    _write_s2_policy(policy_b, "policy-b", "2")
    runner.start(policy_path=policy_a)
    a_status, a_id, _ = runner.post_chat()
    _, a_detail = runner.get_json(f"/api/requests/{a_id}")
    a_record = _final_record(runner, a_id) if a_id else None
    runner.start_gateway(policy_b)
    b_status, b_id, _ = runner.post_chat()
    b_record = _final_record(runner, b_id) if b_id else None
    _, policy = runner.get_json("/api/policy")
    history = policy.get("history", [])
    newest = history[0] if history else {}
    previous_sha = newest.get("previous", {}).get("sha256")
    passed = (
        a_status == b_status == 200
        and len(history) >= 2
        and newest.get("policy_id") == "policy-b"
        and previous_sha == (a_record or {}).get("policy", {}).get("sha256")
        and (a_record or {}).get("policy", {}).get("policy_id") == "policy-a"
        and (b_record or {}).get("policy", {}).get("policy_id") == "policy-b"
        and (a_record or {}).get("policy", {}).get("sha256")
        != (b_record or {}).get("policy", {}).get("sha256")
        and a_detail.get("final_state", {}).get("result") == "ALLOWED"
    )
    return AcceptanceResult(
        "S2-01",
        "정책 A 요청 후 같은 data_dir에서 정책 B로 Gateway 재시작·요청",
        "이력 previous=A, 요청 기록 SHA-256으로 A/B 각각 추적",
        {
            "http": [a_status, b_status],
            "history_count": len(history),
            "previous_sha": previous_sha,
            "request_policy_ids": [
                (a_record or {}).get("policy", {}).get("policy_id"),
                (b_record or {}).get("policy", {}).get("policy_id"),
            ],
        },
        passed,
        [item for item in (a_id, b_id) if item],
    )


def _run_s2_02(runner: _Runner) -> AcceptanceResult:
    missing = runner.data_dir / "s2-missing.json"
    damaged = runner.data_dir / "s2-damaged.json"
    damaged.write_text("{", encoding="utf-8")
    expired = runner.data_dir / "s2-expired.json"
    _write_s2_policy(
        expired,
        "expired-policy",
        "1",
        valid_until=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    active = runner.data_dir / "s2-active.json"
    _write_s2_policy(active, "revoked-policy", "1")
    active_state = load_policy(active)
    revocations = runner.data_dir / "s2-revocations.json"
    revocations.write_text(
        json.dumps(
            {
                "schema_version": "runtime-mvp-revocations/1.0",
                "revoked": [
                    {
                        "policy_id": "revoked-policy",
                        "policy_version": "1",
                        "sha256": active_state.sha256,
                        "revoked_at": datetime.now(timezone.utc).isoformat(),
                        "reason": "acceptance",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    broken_revocations = runner.data_dir / "s2-revocations-broken.json"
    broken_revocations.write_text("[]", encoding="utf-8")
    cases = (
        ("missing", missing, None, "POLICY_UNAVAILABLE"),
        ("damaged", damaged, None, "POLICY_UNAVAILABLE"),
        ("expired", expired, None, "POLICY_EXPIRED"),
        ("revoked", active, revocations, "POLICY_REVOKED"),
        ("revocation_unknown", active, broken_revocations, "POLICY_REVOCATION_UNKNOWN"),
    )
    actual: dict[str, Any] = {}
    request_ids: list[str] = []
    passed = True
    if runner.engine is None:
        runner.start()
    for label, policy_path, revocation_path, expected in cases:
        if runner.engine is None:
            raise RuntimeError("mock engine is not running")
        before = len(runner.engine.receipts)
        runner.start_gateway(policy_path, revocations_path=revocation_path)
        status, request_id, body = runner.post_chat()
        _, policy = runner.get_json("/api/policy")
        after = len(runner.engine.receipts)
        reason = body.get("error", {}).get("code")
        actual[label] = {
            "http": status,
            "reason_code": reason,
            "gateway_active": policy.get("gateway_active"),
            "engine_receipts_before_after": [before, after],
        }
        passed = passed and status == 503 and reason == expected and not policy.get(
            "gateway_active"
        ) and before == after
        if request_id:
            request_ids.append(request_id)
    return AcceptanceResult(
        "S2-02",
        "정책 부재·손상·만료·철회·철회 파일 손상 각각 신규 요청",
        "각 503 구분 코드, gateway_active=false, 엔진 미호출",
        actual,
        passed,
        request_ids,
    )


def _run_s2_03(runner: _Runner) -> AcceptanceResult:
    policy = runner.data_dir / "s2-same-version.json"
    _write_s2_policy(policy, "same-policy", "7", allowed_models=["test-model"])
    runner.start(policy_path=policy)
    first_sha = load_policy(policy).sha256
    _write_s2_policy(policy, "same-policy", "7", allowed_models=["test-model", "other"])
    second_sha = load_policy(policy).sha256
    runner.start_gateway(policy)
    _, current = runner.get_json("/api/policy")
    newest = current.get("history", [{}])[0]
    passed = (
        first_sha != second_sha
        and current.get("current", {}).get("sha256") == second_sha
        and newest.get("same_version_content_changed") is True
        and newest.get("previous", {}).get("sha256") == first_sha
    )
    return AcceptanceResult(
        "S2-03",
        "같은 policy_id·policy_version 파일 내용을 바꿔 재시작",
        "same_version_content_changed=true, 현재 SHA-256 표시",
        {
            "first_sha": first_sha,
            "second_sha": second_sha,
            "current_sha": current.get("current", {}).get("sha256"),
            "same_version_content_changed": newest.get("same_version_content_changed"),
        },
        passed,
        [],
    )


def _run_s2_04(runner: _Runner) -> AcceptanceResult:
    baseline = runner.data_dir / "s2-baseline.json"
    manifest = runner.data_dir / "s2-manifest.json"
    baseline.write_text(
        json.dumps(
            {
                "schema_version": "runtime-mvp-baseline/1.0",
                "fields": {
                    "engine.kind": "mock",
                    "engine.version": "expected-other-version",
                    "option.max_model_len": 2048,
                    "unsupported.field": "value",
                },
            }
        ),
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "runtime-mvp-baseline/1.0",
                "fields": {"engine.kind": "mock"},
            }
        ),
        encoding="utf-8",
    )
    runner.start(
        baseline_path=baseline,
        manifest_path=manifest,
        collector_interval_s=0.05,
        collector_stale_after_s=0.2,
    )
    _, compared = runner.get_json("/api/baseline")
    states = {
        f"{row.get('field')}:{row.get('source')}": row.get("state")
        for row in compared.get("rows", [])
    }
    passed = {
        "match",
        "drift",
        "missing",
        "not_collected",
    }.issubset(set(states.values())) and "weight" in compared.get("model_id_limit", "")
    return AcceptanceResult(
        "S2-04",
        "baseline·manifest와 모의 엔진 관측값 비교",
        "match/drift/missing/not_collected 분리",
        states,
        passed,
        [],
    )


def _collector_with_state(runner: _Runner, expected: str) -> dict[str, Any] | None:
    _, payload = runner.get_json("/api/collectors")
    return next(
        (
            item
            for item in payload.get("collectors", [])
            if item.get("source") == "engine_info" and item.get("state") == expected
        ),
        None,
    )


def _run_s2_05(runner: _Runner) -> AcceptanceResult:
    runner.start(collector_interval_s=0.04, collector_stale_after_s=0.15)
    if runner.engine is None:
        raise RuntimeError("mock engine is not running")
    engine_port = runner.engine.address[1]
    ok = _wait_for(lambda: _collector_with_state(runner, "ok"))
    runner.engine.stop()
    runner.engine = None
    error = _wait_for(lambda: _collector_with_state(runner, "error"))
    stale = _wait_for(lambda: _collector_with_state(runner, "stale"))
    restarted = MockEngine(
        port=engine_port,
        receipts_path=runner.data_dir / "engine_receipts.jsonl",
    ).start()
    runner.engine = restarted
    recovered = _wait_for(lambda: _collector_with_state(runner, "ok"))
    passed = (
        ok is not None
        and error is not None
        and stale is not None
        and recovered is not None
        and error.get("last_success_at") == ok.get("last_success_at")
        and error.get("last_value") == ok.get("last_value")
        and recovered.get("last_error") is None
    )
    return AcceptanceResult(
        "S2-05",
        "짧은 수집 주기에서 정상→엔진 중지→stale→같은 포트 재시작",
        "ok→error(마지막 성공 유지)→stale→ok",
        {
            "states": [
                item.get("state") if item else None
                for item in (ok, error, stale, recovered)
            ],
            "success_retained": bool(
                ok and error and error.get("last_success_at") == ok.get("last_success_at")
            ),
            "value_retained": bool(ok and error and error.get("last_value") == ok.get("last_value")),
        },
        passed,
        [],
    )


def _run_s2_06(runner: _Runner) -> AcceptanceResult:
    runner.start()
    old_id = "e" * 32
    JsonlStore(runner.data_dir / "gateway_records.jsonl").append(
        {
            "phase": "final",
            "request_id": old_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "policy": {"policy_id": "old", "policy_version": "1"},
            "decision": {"result": "ERROR", "reason_code": "OLD", "http_status": 503},
            "request": {},
            "delivery": {},
        }
    )
    _, old_detail = runner.get_json(f"/api/requests/{old_id}")
    old_schema = old_detail.get("gateway_records", [{}])[0].get("policy", {}).get(
        "schema_version"
    )
    blocker = runner.data_dir / "s2-history-blocker"
    blocker.write_text("x", encoding="utf-8")
    runner.start_gateway(_EXAMPLE_POLICY, data_dir=blocker / "sub")
    _, failed_policy = runner.get_json("/api/policy")
    history_state = failed_policy.get("history_storage", {}).get("state")
    runner.start_gateway(_EXAMPLE_POLICY, data_dir=runner.data_dir)
    regular_status, regular_id, _ = runner.post_chat()
    passed = old_schema is None and history_state == "error" and regular_status == 200
    return AcceptanceResult(
        "S2-06",
        "schema_version 없는 과거 기록 조회·이력 저장 실패·정상 data_dir 재시작",
        "과거 필드 None, 저장 상태 error, 기존 일반 요청 200",
        {
            "old_schema_version": old_schema,
            "history_storage_state": history_state,
            "regular_http": regular_status,
        },
        passed,
        [item for item in (old_id, regular_id) if item],
    )


def _s3a_final(runner: _Runner, request_id: str | None) -> dict[str, Any] | None:
    if request_id is None:
        return None
    records, _ = JsonlStore(runner.data_dir / "gateway_records.jsonl").read_all()
    return next(
        (
            record
            for record in reversed(records)
            if record.get("request_id") == request_id and record.get("phase") == "final"
        ),
        None,
    )


def _collector_source(
    runner: _Runner, source: str, expected: str
) -> dict[str, Any] | None:
    _, payload = runner.get_json("/api/collectors")
    return next(
        (
            item
            for item in payload.get("collectors", [])
            if item.get("source") == source and item.get("state") == expected
        ),
        None,
    )


def _run_s3a_01(runner: _Runner) -> AcceptanceResult:
    runner.start_vllm()
    if runner.fake_backend is None:
        raise RuntimeError("fake backend is not running")
    status, request_id, body = runner.post_chat()
    _, detail = runner.get_json(f"/api/requests/{request_id}")
    receipt = runner.fake_backend.requests[-1] if runner.fake_backend.requests else {}
    final = _s3a_final(runner, request_id) or {}
    passed = (
        status == 200
        and body.get("model") == "backend-test-model"
        and receipt.get("model") == "backend-test-model"
        and final.get("backend_kind") == "vllm"
        and final.get("delivery", {}).get("backend_receipt") == "not_available"
        and detail.get("evidence_summary")
        == "Gateway 전달·응답 수신, backend 측 수신 기록 미연결"
        and not detail.get("engine_receipts")
    )
    return AcceptanceResult(
        "S3A-01",
        "fake external backend 일반 요청·상세 조회",
        "200, model_map 치환, backend_kind=vllm, mock receipt 비사용",
        {
            "http": status,
            "backend_model": receipt.get("model"),
            "backend_kind": final.get("backend_kind"),
            "backend_receipt": final.get("delivery", {}).get("backend_receipt"),
            "evidence_summary": detail.get("evidence_summary"),
            "data_origin": "fake_backend_test",
            "real_vllm": False,
        },
        passed,
        [request_id] if request_id else [],
    )


def _run_s3a_02(runner: _Runner) -> AcceptanceResult:
    runner.start_vllm(fake_options={"stream_interval_s": 0.08})
    if runner.gateway is None:
        raise RuntimeError("gateway is not running")
    payload = json.dumps(
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "stream"}],
            "max_tokens": 64,
            "stream": True,
        }
    ).encode("utf-8")
    connection = http.client.HTTPConnection(*runner.gateway.address, timeout=3)
    started = time.monotonic()
    try:
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=payload,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        request_id = response.getheader("X-Request-ID")
        first = response.read1(1)
        first_elapsed = time.monotonic() - started
        body = first + response.read()
        total_elapsed = time.monotonic() - started
        http_status = response.status
    finally:
        connection.close()
    final = _wait_for(lambda: _s3a_final(runner, request_id))
    passed = (
        http_status == 200
        and bool(first)
        and b"[DONE]" not in first
        and body.endswith(b"data: [DONE]\n\n")
        and first_elapsed < total_elapsed
        and final is not None
        and final.get("final_outcome") == "completed"
    )
    return AcceptanceResult(
        "S3A-02",
        "fake external backend SSE를 read1로 증분 수신",
        "[DONE] 전 바이트 관측 후 [DONE] 종료·최종 completed",
        {
            "http": http_status,
            "first_before_done": b"[DONE]" not in first,
            "ends_done": body.endswith(b"data: [DONE]\n\n"),
            "first_elapsed_s": round(first_elapsed, 4),
            "total_elapsed_s": round(total_elapsed, 4),
            "final_outcome": final.get("final_outcome") if final else None,
            "data_origin": "fake_backend_test",
        },
        passed,
        [request_id] if request_id else [],
    )


def _run_s3a_03(runner: _Runner) -> AcceptanceResult:
    runner.start_vllm()
    if runner.fake_backend is None:
        raise RuntimeError("fake backend is not running")
    cases = [
        runner.post_chat(model="other-model"),
        runner.post_chat(max_tokens=129),
    ]
    details = [runner.get_json(f"/api/requests/{item[1]}")[1] for item in cases]
    codes = [item[2].get("error", {}).get("code") for item in cases]
    passed = (
        [item[0] for item in cases] == [403, 403]
        and codes == ["MODEL_NOT_ALLOWED", "MAX_TOKENS_EXCEEDED"]
        and runner.fake_backend.request_count == 0
        and all(
            "Gateway 미전달 확인, backend 미수신은 미확인(증거원 미연결)"
            == detail.get("evidence_summary")
            for detail in details
        )
    )
    return AcceptanceResult(
        "S3A-03",
        "금지 모델·max_tokens 129 전송",
        "둘 다 403 사전 거부, fake backend chat 수신 0, 미수신 미확인 분리",
        {
            "http": [item[0] for item in cases],
            "codes": codes,
            "backend_chat_count": runner.fake_backend.request_count,
            "evidence": [detail.get("evidence_summary") for detail in details],
        },
        passed,
        [item[1] for item in cases if item[1]],
    )


def _run_s3a_04(runner: _Runner) -> AcceptanceResult:
    runner.start_vllm(
        backend_updates={
            "timeouts": {
                "relay_timeout_s": 0.04,
                "stream_idle_timeout_s": 0.1,
                "request_deadline_s": 1,
            }
        }
    )
    if runner.fake_backend is None:
        raise RuntimeError("fake backend is not running")
    observed: dict[str, dict[str, Any]] = {}
    expected = {
        "http_400": (400, "ENGINE_REJECTED_REQUEST", "ENGINE_REJECTED", "engine"),
        "http_401": (502, "ENGINE_AUTHORIZATION_FAILED", "ERROR", "engine"),
        "http_403": (502, "ENGINE_AUTHORIZATION_FAILED", "ERROR", "engine"),
        "http_429": (429, "ENGINE_CAPACITY_REJECTED", "ENGINE_REJECTED", "engine"),
        "http_500": (502, "ENGINE_BAD_RESPONSE"),
        "invalid_json": (502, "ENGINE_BAD_RESPONSE"),
        "disconnect": (502, "ENGINE_UNREACHABLE"),
        "redirect": (502, "ENGINE_BAD_RESPONSE"),
        "header_timeout": (504, "ENGINE_TIMEOUT"),
    }
    request_ids: list[str] = []
    for mode, wanted in expected.items():
        wanted_status, wanted_code = wanted[:2]
        runner.fake_backend.set_mode(mode)
        status, request_id, body = runner.post_chat()
        if request_id:
            request_ids.append(request_id)
        observed[mode] = {
            "http": status,
            "code": body.get("error", {}).get("code"),
            "result": body.get("error", {}).get("result"),
            "source": body.get("error", {}).get("source"),
            "expected": [wanted_status, wanted_code],
        }
    redirect_not_followed = runner.fake_backend.redirect_follow_count == 0
    runner.fake_backend.set_mode("sse_truncated")
    truncated_status, truncated_id, _ = runner.post_chat(stream=True)
    if truncated_id:
        request_ids.append(truncated_id)
    truncated_final = _wait_for(lambda: _s3a_final(runner, truncated_id))
    passed = (
        all(
            item["http"] == item["expected"][0]
            and item["code"] == item["expected"][1]
            for item in observed.values()
        )
        and observed["http_400"]["result"] == "ENGINE_REJECTED"
        and observed["http_400"]["source"] == "engine"
        and observed["http_401"]["result"] == "ERROR"
        and observed["http_403"]["result"] == "ERROR"
        and observed["http_429"]["result"] == "ENGINE_REJECTED"
        and redirect_not_followed
        and truncated_status == 200
        and truncated_final is not None
        and truncated_final.get("final_outcome") == "backend_truncated"
        and truncated_final.get("decision", {}).get("reason_code") == "STREAM_TRUNCATED"
    )
    return AcceptanceResult(
        "S3A-04",
        "fake backend 400·401·403·429·500·invalid JSON·disconnect·302·header timeout·SSE 절단",
        "엔진 일반 4xx/429 출처·상태 보존, 엔진 인증 실패 502 오류 분리, 5xx/비정상 응답 502, timeout 504, redirect 미추적",
        {
            "nonstream": observed,
            "redirect_not_followed": redirect_not_followed,
            "truncated_http": truncated_status,
            "truncated_final": (
                truncated_final.get("final_outcome") if truncated_final else None
            ),
            "data_origin": "fake_backend_test",
        },
        passed,
        request_ids,
    )


def _run_s3a_05(runner: _Runner) -> AcceptanceResult:
    secret = "S3A-ACCEPTANCE-AUTH-VALUE"
    runner.start_vllm(
        fake_options={"required_authorization": f"Bearer {secret}"},
        backend_updates={"credential": {"env": "S3A_BACKEND_TOKEN"}},
        credential_environ={},
    )
    if runner.fake_backend is None:
        raise RuntimeError("fake backend is not running")
    missing_status, missing_id, missing_body = runner.post_chat()
    missing_count = runner.fake_backend.request_count
    runner.start_vllm(
        fake_options={"required_authorization": f"Bearer {secret}"},
        backend_updates={"credential": {"env": "S3A_BACKEND_TOKEN"}},
        credential_environ={"S3A_BACKEND_TOKEN": secret},
    )
    if runner.fake_backend is None:
        raise RuntimeError("fake backend is not running")
    configured_status, configured_id, _ = runner.post_chat()
    authorization_present = bool(
        runner.fake_backend.requests
        and runner.fake_backend.requests[-1].get("authorization_present")
    )
    passed = (
        missing_status == 503
        and missing_body.get("error", {}).get("code")
        == "BACKEND_CREDENTIAL_MISSING"
        and missing_count == 0
        and configured_status == 200
        and authorization_present
    )
    return AcceptanceResult(
        "S3A-05",
        "env 자격정보 누락 후 설정하여 재실행",
        "누락 503·backend 미호출, 설정 시 Gateway 생성 Authorization으로 200",
        {
            "missing_http": missing_status,
            "missing_code": missing_body.get("error", {}).get("code"),
            "missing_backend_chat_count": missing_count,
            "configured_http": configured_status,
            "authorization_present": authorization_present,
        },
        passed,
        [item for item in (missing_id, configured_id) if item],
    )


def _run_s3a_06(runner: _Runner) -> AcceptanceResult:
    runner.start_vllm(collector_interval_s=0.04)
    if runner.fake_backend is None:
        raise RuntimeError("fake backend is not running")
    health = _wait_for(lambda: _collector_source(runner, "engine_health", "ok"))
    info = _wait_for(lambda: _collector_source(runner, "engine_info", "ok"))
    metrics = _wait_for(lambda: _collector_source(runner, "metrics", "ok"))
    runner.fake_backend.set_metrics_failure("http_500")
    metrics_error = _wait_for(lambda: _collector_source(runner, "metrics", "error"))
    inference_status, request_id, _ = runner.post_chat()
    passed = (
        health is not None
        and health.get("data_origin") == "external_backend"
        and info is not None
        and info.get("last_value", {}).get("engine.kind") == "vllm"
        and info.get("last_value", {}).get("model.id") == "backend-test-model"
        and metrics is not None
        and metrics.get("last_value", {}).get("requests.absent", {}).get("state")
        == "unsupported_or_absent"
        and metrics_error is not None
        and inference_status == 200
    )
    return AcceptanceResult(
        "S3A-06",
        "health/models/metrics 수집 후 metrics만 500",
        "외부 출처 수집 성공→metrics error, 추론 200 유지",
        {
            "health": health.get("state") if health else None,
            "engine_kind": (
                info.get("last_value", {}).get("engine.kind") if info else None
            ),
            "model_id": info.get("last_value", {}).get("model.id") if info else None,
            "metrics_before": metrics.get("state") if metrics else None,
            "absent_metric": (
                metrics.get("last_value", {}).get("requests.absent", {}).get("state")
                if metrics
                else None
            ),
            "metrics_after": metrics_error.get("state") if metrics_error else None,
            "inference_http": inference_status,
            "data_origin": "fake_backend_test (collector reports external_backend)",
        },
        passed,
        [request_id] if request_id else [],
    )


def _run_s3a_07(runner: _Runner) -> AcceptanceResult:
    secret = "S3A-SECRET-MUST-NOT-APPEAR"
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        runner.start_vllm(
            fake_options={"required_authorization": f"Bearer {secret}"},
            backend_updates={"credential": {"env": "S3A_SECRET_ENV"}},
            credential_environ={"S3A_SECRET_ENV": secret},
        )
        status, request_id, _ = runner.post_chat()
    payloads: list[bytes] = []
    for path in (
        "/api/status",
        "/api/policy",
        "/api/collectors",
        f"/api/requests/{request_id}",
        f"/api/requests/{request_id}/evidence",
    ):
        _, _, body = runner.request("GET", path)
        payloads.append(body)
    probe = AcceptanceResult(
        "S3A-07-probe",
        "secret report probe",
        "secret absent",
        {"http": status, "credential_state_only": True},
        True,
        [request_id] if request_id else [],
    )
    report_dir = runner.data_dir / "s3a-secret-report"
    json_report, md_report = write_reports(report_dir, runner.data_dir, [probe])
    payloads.extend([json_report.read_bytes(), md_report.read_bytes()])
    for path in runner.data_dir.rglob("*"):
        if path.is_file():
            payloads.append(path.read_bytes())
    payloads.append(stderr.getvalue().encode("utf-8"))
    leaked = any(secret.encode("utf-8") in payload for payload in payloads)
    passed = status == 200 and not leaked
    return AcceptanceResult(
        "S3A-07",
        "env 시험 키 사용 후 기록·API·evidence·보고서·stderr 검색",
        "키 문자열 없음, 상태만 configured/missing/insecure_file",
        {
            "http": status,
            "secret_found": leaked,
            "scanned_payloads": len(payloads),
            "report_scanned": True,
            "stderr_scanned": True,
        },
        passed,
        [request_id] if request_id else [],
    )


def _run_s3a_08(runner: _Runner) -> AcceptanceResult:
    mixed_dir = runner.data_dir / "s3a-mixed"
    runner.start_vllm(data_dir=mixed_dir)
    if runner.fake_backend is None:
        raise RuntimeError("fake backend is not running")
    body = json.dumps(
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "fixed destination"}],
            "max_tokens": 64,
        }
    ).encode("utf-8")
    fixed_status, fixed_headers, _ = runner.request(
        "POST",
        "/v1/chat/completions",
        body=body,
        headers={
            "Host": "127.0.0.1:1",
            "X-Forwarded-Host": "127.0.0.1:1",
            "X-Arbitrary": "must-not-forward",
        },
    )
    vllm_id = fixed_headers.get("X-Request-ID")
    received = runner.fake_backend.requests[-1] if runner.fake_backend.requests else {}
    invalid_body = json.dumps(
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "bad field"}],
            "max_tokens": 64,
            "base_url": "http://127.0.0.1:1",
        }
    ).encode("utf-8")
    invalid_status, _, invalid_document = runner.json_request(
        "POST", "/v1/chat/completions", body=invalid_body
    )
    runner.start(data_dir=mixed_dir)
    mock_status, mock_id, _ = runner.post_chat()
    _, old_detail = runner.get_json(f"/api/requests/{vllm_id}")
    _, mock_detail = runner.get_json(f"/api/requests/{mock_id}")
    passed = (
        fixed_status == 200
        and received.get("host") != "127.0.0.1:1"
        and received.get("x_forwarded_host") is None
        and "x-arbitrary" not in received.get("header_names", [])
        and invalid_status == 400
        and invalid_document.get("error", {}).get("code") == "UNSUPPORTED_FIELD"
        and mock_status == 200
        and old_detail.get("backend_kind") == "vllm"
        and not old_detail.get("engine_receipts")
        and mock_detail.get("backend_kind") == "mock"
        and mock_detail.get("evidence_state") == "received"
    )
    return AcceptanceResult(
        "S3A-08",
        "악성 목적지 힌트·본문 base_url 후 같은 data_dir에서 mock 전환",
        "고정 목적지·미지원 필드 400·vllm/mock 증거 분리",
        {
            "fixed_http": fixed_status,
            "backend_host_is_client_value": received.get("host") == "127.0.0.1:1",
            "forwarded_host_received": received.get("x_forwarded_host"),
            "arbitrary_header_received": "x-arbitrary" in received.get("header_names", []),
            "body_override_http": invalid_status,
            "body_override_code": invalid_document.get("error", {}).get("code"),
            "old_backend_kind": old_detail.get("backend_kind"),
            "old_mock_receipts": len(old_detail.get("engine_receipts", [])),
            "new_backend_kind": mock_detail.get("backend_kind"),
            "new_evidence_state": mock_detail.get("evidence_state"),
        },
        passed,
        [item for item in (vllm_id, mock_id) if item],
    )


def _run_sgl_01(runner: _Runner) -> AcceptanceResult:
    """F4 common comparison path with a deliberately drifting fake SGLang."""

    baseline = runner.data_dir / "sgl-01-baseline.json"
    manifest = runner.data_dir / "sgl-01-manifest.json"
    baseline.write_text(
        json.dumps(
            {
                "schema_version": "runtime-mvp-baseline/1.0",
                "fields": {
                    "engine.kind": "vllm",
                    "option.max_model_len": 2048,
                },
            }
        ),
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "runtime-mvp-baseline/1.0",
                "fields": {
                    "engine.kind": "sglang",
                    "option.max_model_len": 2048,
                },
            }
        ),
        encoding="utf-8",
    )
    runner.start_external(
        "sglang",
        baseline_path=baseline,
        manifest_path=manifest,
        collector_interval_s=0.04,
    )
    info = _wait_for(lambda: _collector_source(runner, "engine_info", "ok"))

    def current_drift() -> dict[str, Any] | None:
        _, payload = runner.get_json("/api/revalidation")
        return next(
            (
                item
                for item in payload.get("requests", [])
                if item.get("trigger") == "config_drift"
                and item.get("current_evidence_present") is True
            ),
            None,
        )

    drift = _wait_for(current_drift)
    _, comparison = runner.get_json("/api/baseline")
    engine_row = next(
        (
            item
            for item in comparison.get("rows", [])
            if item.get("source") == "engine_api"
            and item.get("field") == "engine.kind"
        ),
        {},
    )
    unknown_row = next(
        (
            item
            for item in comparison.get("rows", [])
            if item.get("source") == "engine_api"
            and item.get("field") == "option.max_model_len"
        ),
        {},
    )
    passed = (
        info is not None
        and info.get("last_value", {}).get("engine.kind") == "sglang"
        and info.get("last_value", {}).get("engine.version") == "not_collected"
        and engine_row.get("state") == "drift"
        and engine_row.get("observed") == "sglang"
        and unknown_row.get("state") == "not_collected"
        and unknown_row.get("observed") == "unknown"
        and drift is not None
        and drift.get("data_origin") == "external_backend"
    )
    return AcceptanceResult(
        "SGL-01",
        "가짜 SGLang engine_info와 승인 baseline 비교",
        "engine.kind drift와 재검증 요청; version은 not_collected",
        {
            "engine_kind": info.get("last_value", {}).get("engine.kind") if info else None,
            "engine_version": info.get("last_value", {}).get("engine.version") if info else None,
            "comparison_state": engine_row.get("state"),
            "max_model_len_state": unknown_row.get("state"),
            "revalidation": drift is not None,
            "verification_level": "fake_sglang_test_only",
        },
        passed,
        [],
    )


def _run_sgl_02(runner: _Runner) -> AcceptanceResult:
    """SP1 hash collector/revalidation through the same fake SGLang app."""

    model = runner.data_dir / "sgl-02-model"
    model.mkdir(parents=True, exist_ok=True)
    asset = model / "weights.bin"
    asset.write_bytes(b"approved-test-fixture")
    manifest = runner.data_dir / "sgl-02-assets.json"
    manifest.write_text(json.dumps(snapshot_assets(model)), encoding="utf-8")
    runner.start_external(
        "sglang",
        asset_manifest_path=manifest,
        asset_interval_s=0.04,
        collector_interval_s=0.04,
    )
    intact = _wait_for(
        lambda: (
            item
            if (item := _collector_source(runner, "asset_integrity", "ok"))
            and item.get("last_value", {}).get("intact") is True
            else None
        )
    )
    asset.write_bytes(b"changed-test-fixture")
    changed = _wait_for(
        lambda: (
            item
            if (item := _collector_source(runner, "asset_integrity", "ok"))
            and item.get("last_value", {}).get("counts", {}).get("changed") == 1
            else None
        )
    )

    def asset_request() -> dict[str, Any] | None:
        _, payload = runner.get_json("/api/revalidation")
        return next(
            (
                item
                for item in payload.get("requests", [])
                if item.get("trigger") == "asset_changed"
                and item.get("current_evidence_present") is True
            ),
            None,
        )

    revalidation = _wait_for(asset_request)
    passed = (
        intact is not None
        and changed is not None
        and changed.get("data_origin") == "local_file"
        and revalidation is not None
        and revalidation.get("data_origin") == "local_file"
    )
    return AcceptanceResult(
        "SGL-02",
        "가짜 SGLang 실행 중 승인 자산 변경",
        "공통 해시 비교가 변경을 찾고 재검증 요청",
        {
            "initial_intact": intact is not None,
            "changed_count": (
                changed.get("last_value", {}).get("counts", {}).get("changed")
                if changed
                else None
            ),
            "revalidation": revalidation is not None,
            "verification_level": "fake_sglang_test_only",
        },
        passed,
        [],
    )


_S4_INFERENCE_TOKEN = "s4-inference-token-acceptance-0001"
_S4_VIEWER_TOKEN = "s4-viewer-token-acceptance-000001"
_S4_WRONG_TOKEN = "s4-wrong-token-acceptance-0000001"


def _write_s4_auth(path: Path, *, protect_reads: bool = False) -> None:
    route_roles: dict[str, list[str]] = {CHAT_ROUTE: ["inference"]}
    if protect_reads:
        route_roles["GET /api/*"] = ["viewer"]
    document = {
        "schema_version": "runtime-auth/1.0",
        "auth_ref": "S4-ACCEPTANCE",
        "principals": [
            {
                "principal_id": "s4-client",
                "roles": ["inference"],
                "token_sha256": token_digest(_S4_INFERENCE_TOKEN),
                "allowed_models": ["test-model"],
            },
            {
                "principal_id": "s4-viewer",
                "roles": ["viewer"],
                "token_sha256": token_digest(_S4_VIEWER_TOKEN),
            },
        ],
        "route_roles": route_roles,
    }
    path.write_text(json.dumps(document), encoding="utf-8")


def _records_in(data_dir: Path) -> list[dict[str, Any]]:
    records, corrupt = JsonlStore(data_dir / "gateway_records.jsonl").read_all()
    if corrupt:
        return []
    return records


def _finals_in(data_dir: Path) -> list[dict[str, Any]]:
    return [record for record in _records_in(data_dir) if record.get("phase") == "final"]


def _run_s4_01(runner: _Runner) -> AcceptanceResult:
    data_dir = runner.data_dir / "s4-01"
    auth_path = runner.data_dir / "s4-01-auth.json"
    policy_path = runner.data_dir / "s4-01-policy.json"
    _write_s4_auth(auth_path)
    policy_document = json.loads(_EXAMPLE_POLICY.read_text(encoding="utf-8"))
    policy_document["allowed_models"] = ["test-model", "other-model"]
    policy_path.write_text(json.dumps(policy_document), encoding="utf-8")
    runner.start(
        policy_path=policy_path,
        data_dir=data_dir,
        auth_config_path=auth_path,
    )
    if runner.engine is None:
        raise RuntimeError("backend fixture is not running")

    cases = [
        ("missing", None, "test-model", 401, "AUTH_REQUIRED"),
        ("invalid", _S4_WRONG_TOKEN, "test-model", 401, "AUTH_INVALID"),
        ("role", _S4_VIEWER_TOKEN, "test-model", 403, "ROLE_NOT_ALLOWED"),
        (
            "principal_model",
            _S4_INFERENCE_TOKEN,
            "other-model",
            403,
            "PRINCIPAL_MODEL_NOT_ALLOWED",
        ),
        ("allowed", _S4_INFERENCE_TOKEN, "test-model", 200, None),
    ]
    actual: dict[str, Any] = {}
    request_ids: list[str] = []
    passed = True
    expected_auth = {
        "missing": ("missing", None),
        "invalid": ("invalid", None),
        "role": ("authenticated", "s4-viewer"),
        "principal_model": ("authenticated", "s4-client"),
        "allowed": ("authenticated", "s4-client"),
    }
    for label, token, model, expected_status, expected_code in cases:
        headers = {"Authorization": f"Bearer {token}"} if token is not None else None
        request_body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": "acceptance"}],
                "max_tokens": 64,
                "stream": False,
            }
        ).encode("utf-8")
        status, response_headers, body = runner.json_request(
            "POST", "/v1/chat/completions", body=request_body, headers=headers
        )
        request_id = response_headers.get("X-Request-ID")
        if request_id:
            request_ids.append(request_id)
        code = body.get("error", {}).get("code")
        final = _final_record(runner, request_id) if request_id else None
        auth = (final or {}).get("auth", {})
        delivery_attempted = (final or {}).get("delivery", {}).get("attempted")
        actual[label] = {
            "http": status,
            "code": code,
            "www_authenticate": response_headers.get(
                "WWW-Authenticate", ""
            ).startswith("Bearer"),
            "auth_state": auth.get("state"),
            "principal_id": auth.get("principal_id"),
            "delivery_attempted": delivery_attempted,
        }
        passed = passed and status == expected_status and code == expected_code
        passed = passed and final is not None
        passed = passed and (auth.get("state"), auth.get("principal_id")) == expected_auth[label]
        if label != "allowed":
            passed = passed and delivery_attempted is False
        else:
            passed = passed and delivery_attempted is True

    finals = _finals_in(data_dir)
    allowed = next(
        (
            record
            for record in reversed(finals)
            if record.get("decision", {}).get("reason_code") == "REQUEST_ALLOWED"
        ),
        {},
    )
    stored = (data_dir / "gateway_records.jsonl").read_text(encoding="utf-8")
    status_text = json.dumps(runner.gateway.status()) if runner.gateway else ""
    sensitive = (
        _S4_INFERENCE_TOKEN,
        _S4_VIEWER_TOKEN,
        _S4_WRONG_TOKEN,
        token_digest(_S4_INFERENCE_TOKEN),
        token_digest(_S4_VIEWER_TOKEN),
        token_digest(_S4_WRONG_TOKEN),
    )
    sensitive_found = any(value in stored or value in status_text for value in sensitive)
    backend_receipts = len(runner.engine.receipts)
    non_delivery_count = sum(
        record.get("delivery", {}).get("attempted") is False for record in finals
    )
    passed = (
        passed
        and actual["missing"]["www_authenticate"]
        and backend_receipts == 1
        and non_delivery_count == 4
        and allowed.get("auth", {}).get("principal_id") == "s4-client"
        and allowed.get("auth", {}).get("method") == "bearer_sha256"
        and not sensitive_found
    )
    return AcceptanceResult(
        "S4-01",
        "인증 없음·불일치·역할 불허·주체 모델 불허·정상 요청과 기록 대조",
        "401/401/403/403/200, 401 challenge, 거부 요청 backend 미전달, 주체 ID만 기록",
        {
            "requests": actual,
            "backend_receipts": backend_receipts,
            "non_delivery_records": non_delivery_count,
            "allowed_principal_id": allowed.get("auth", {}).get("principal_id"),
            "token_or_token_digest_found": sensitive_found,
        },
        passed,
        request_ids,
    )


def _run_s4_02(runner: _Runner) -> AcceptanceResult:
    data_dir = runner.data_dir / "s4-02"
    auth_path = runner.data_dir / "s4-02-broken-auth.json"
    auth_path.write_text('{"schema_version":"runtime-auth/1.0"}\n', encoding="utf-8")
    runner.start(data_dir=data_dir, auth_config_path=auth_path)
    if runner.engine is None:
        raise RuntimeError("backend fixture is not running")
    inference_status, request_id, body = runner.post_chat(
        headers={"Authorization": f"Bearer {_S4_INFERENCE_TOKEN}"}
    )
    status_status, _, status_body = runner.json_request("GET", "/api/status")
    list_status, _, list_body = runner.json_request("GET", "/api/requests")
    final = next(
        (
            record
            for record in reversed(_finals_in(data_dir))
            if record.get("request_id") == request_id
        ),
        {},
    )
    backend_receipts = len(runner.engine.receipts)

    runner.start_gateway(_EXAMPLE_POLICY, data_dir=data_dir)
    summary_status, summary = runner.get_json("/api/summary")
    counts = summary.get("counts", {})
    passed = (
        inference_status == 503
        and body.get("error", {}).get("code") == "AUTH_UNAVAILABLE"
        and status_status == list_status == 503
        and status_body.get("error", {}).get("code") == "AUTH_UNAVAILABLE"
        and list_body.get("error", {}).get("code") == "AUTH_UNAVAILABLE"
        and final.get("decision", {}).get("result") == "ERROR"
        and final.get("auth", {}).get("state") == "unavailable"
        and final.get("delivery", {}).get("attempted") is False
        and backend_receipts == 0
        and summary_status == 200
        and counts.get("DENIED") == 0
        and counts.get("ERROR") == 1
    )
    return AcceptanceResult(
        "S4-02",
        "깨진 인증 설정으로 추론·상태·목록 조회 후 인증 없이 같은 기록 집계 조회",
        "추론·조회 503 AUTH_UNAVAILABLE, backend 미전달, DENIED 0·ERROR 1",
        {
            "inference": [inference_status, body.get("error", {}).get("code")],
            "reads": [
                [status_status, status_body.get("error", {}).get("code")],
                [list_status, list_body.get("error", {}).get("code")],
            ],
            "record": {
                "result": final.get("decision", {}).get("result"),
                "auth_state": final.get("auth", {}).get("state"),
                "delivery_attempted": final.get("delivery", {}).get("attempted"),
            },
            "backend_receipts": backend_receipts,
            "summary_counts": counts,
        },
        passed,
        [request_id] if request_id else [],
    )


def _duplicate_authorization_get(
    runner: _Runner, path: str, first: str, second: str
) -> tuple[int, dict[str, str], dict[str, Any]]:
    if runner.gateway is None:
        raise RuntimeError("gateway is not running")
    connection = http.client.HTTPConnection(*runner.gateway.address, timeout=3)
    try:
        connection.putrequest("GET", path)
        connection.putheader("Authorization", f"Bearer {first}")
        connection.putheader("Authorization", f"Bearer {second}")
        connection.endheaders()
        response = connection.getresponse()
        body = response.read()
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            document = {}
        return response.status, dict(response.headers), document
    finally:
        connection.close()


def _run_s4_03(runner: _Runner) -> AcceptanceResult:
    data_dir = runner.data_dir / "s4-03"
    auth_path = runner.data_dir / "s4-03-auth.json"
    _write_s4_auth(auth_path, protect_reads=True)
    runner.start(data_dir=data_dir, auth_config_path=auth_path)
    if runner.engine is None:
        raise RuntimeError("backend fixture is not running")
    missing_status, missing_headers, missing_body = runner.json_request(
        "GET", "/api/status"
    )
    viewer_status, _, viewer_body = runner.json_request(
        "GET",
        "/api/status",
        headers={"Authorization": f"Bearer {_S4_VIEWER_TOKEN}"},
    )
    duplicate_status, duplicate_headers, duplicate_body = _duplicate_authorization_get(
        runner, "/api/status", _S4_VIEWER_TOKEN, _S4_INFERENCE_TOKEN
    )
    backend_receipts = len(runner.engine.receipts)
    gateway_records = len(_records_in(data_dir))
    passed = (
        missing_status == 401
        and missing_body.get("error", {}).get("code") == "AUTH_REQUIRED"
        and missing_headers.get("WWW-Authenticate", "").startswith("Bearer")
        and viewer_status == 200
        and viewer_body.get("auth", {}).get("state") == "ok"
        and duplicate_status == 401
        and duplicate_body.get("error", {}).get("code") == "AUTH_INVALID"
        and duplicate_headers.get("WWW-Authenticate", "").startswith("Bearer")
        and backend_receipts == 0
        and gateway_records == 0
    )
    return AcceptanceResult(
        "S4-03",
        "GET /api/* 보호 설정에서 무토큰·조회 역할·중복 Authorization으로 상태 조회",
        "401 AUTH_REQUIRED, 200, 401 AUTH_INVALID; backend·추론 기록 변화 없음",
        {
            "missing": [missing_status, missing_body.get("error", {}).get("code")],
            "viewer_http": viewer_status,
            "duplicate": [
                duplicate_status,
                duplicate_body.get("error", {}).get("code"),
            ],
            "backend_receipts": backend_receipts,
            "gateway_request_records": gateway_records,
        },
        passed,
        [],
    )


def _headers_only_chat(
    runner: _Runner, *, authorization: str | None
) -> tuple[int, dict[str, str], dict[str, Any]]:
    if runner.gateway is None:
        raise RuntimeError("gateway is not running")
    connection = http.client.HTTPConnection(*runner.gateway.address, timeout=3)
    try:
        connection.putrequest("POST", "/v1/chat/completions")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", "4096")
        if authorization is not None:
            connection.putheader("Authorization", authorization)
        connection.endheaders()
        response = connection.getresponse()
        payload = response.read()
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            document = {}
        return response.status, dict(response.headers), document
    finally:
        connection.close()


def _run_s4_04(runner: _Runner) -> AcceptanceResult:
    data_dir = runner.data_dir / "s4-04"
    auth_path = runner.data_dir / "s4-04-auth.json"
    target_path = runner.data_dir / "s4-04-target.json"
    _write_s4_auth(auth_path)
    target_path.write_text(
        json.dumps(
            {
                "schema_version": "runtime-target/1.0",
                "target_id": "other-target",
                "engine": {"kind": "mock", "version": "mock-1.0"},
                "model": {"id": "test-model"},
            }
        ),
        encoding="utf-8",
    )
    runner.start(
        data_dir=data_dir,
        auth_config_path=auth_path,
        target_path=target_path,
        request_body_timeout_s=0.4,
    )
    if runner.engine is None:
        raise RuntimeError("backend fixture is not running")
    target_status, target_headers, target_body = _headers_only_chat(
        runner, authorization=f"Bearer {_S4_INFERENCE_TOKEN}"
    )
    auth_status, auth_headers, auth_body = _headers_only_chat(
        runner, authorization=None
    )
    target_id = target_headers.get("X-Request-ID")
    auth_id = auth_headers.get("X-Request-ID")
    finals = {record.get("request_id"): record for record in _finals_in(data_dir)}
    target_record = finals.get(target_id, {})
    auth_record = finals.get(auth_id, {})
    backend_receipts = len(runner.engine.receipts)
    passed = (
        target_status == 503
        and target_body.get("error", {}).get("code") == "TARGET_MISMATCH"
        and target_record.get("auth", {}).get("principal_id") == "s4-client"
        and target_record.get("request", {}).get("model") is None
        and target_record.get("delivery", {}).get("attempted") is False
        and auth_status == 401
        and auth_body.get("error", {}).get("code") == "AUTH_REQUIRED"
        and auth_record.get("decision", {}).get("reason_code") == "AUTH_REQUIRED"
        and auth_record.get("target", {}).get("registration") == "ok"
        and auth_record.get("delivery", {}).get("attempted") is False
        and backend_receipts == 0
    )
    return AcceptanceResult(
        "S4-04",
        "대상 불일치에서 본문 0바이트 전송 요청을 인증 토큰 유무로 두 번 실행",
        "인증 성공은 503 TARGET_MISMATCH·주체 기록, 인증 없음은 먼저 401; backend 미전달",
        {
            "authenticated": {
                "http": target_status,
                "code": target_body.get("error", {}).get("code"),
                "principal_id": target_record.get("auth", {}).get("principal_id"),
                "recorded_model": target_record.get("request", {}).get("model"),
                "body_bytes_sent": 0,
                "delivery_attempted": target_record.get("delivery", {}).get("attempted"),
            },
            "unauthenticated": {
                "http": auth_status,
                "code": auth_body.get("error", {}).get("code"),
                "body_bytes_sent": 0,
                "delivery_attempted": auth_record.get("delivery", {}).get("attempted"),
            },
            "backend_receipts": backend_receipts,
        },
        passed,
        [item for item in (target_id, auth_id) if item],
    )


class _AcceptanceFakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


def _run_s4_05(runner: _Runner) -> AcceptanceResult:
    from tools import k8s_observation_feed as helper

    data_dir = runner.data_dir / "s4-05"
    live_dir = data_dir / "live"
    live_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    feed_path = live_dir / "tetragon.jsonl"
    heartbeat_path = live_dir / "tetragon.heartbeat.json"
    gap_path = live_dir / "tetragon.gaps.jsonl"
    now = [1000.0]
    started: list[_AcceptanceFakeProcess] = []
    commands: list[list[str]] = []

    with feed_path.open("ab") as sink:
        group = helper._FeedGroup(
            "tetragon",
            feed_path=feed_path,
            heartbeat_path=heartbeat_path,
            gap_path=gap_path,
            children=[
                helper._Child(
                    "tetragon",
                    lambda since: helper.tetragon_command(since_time=since),
                    sink,
                    sink,
                )
            ],
        )
        supervisor = helper._Supervisor(
            [group],
            context=helper.EXPECTED_CONTEXT,
            stop_requested=threading.Event(),
            clock=lambda: now[0],
        )

        def fake_start(argv: Any, _output: Any, _error: Any) -> _AcceptanceFakeProcess:
            commands.append(list(argv))
            process = _AcceptanceFakeProcess()
            started.append(process)
            return process

        with (
            patch.object(helper, "_start", side_effect=fake_start),
            patch.object(helper, "assert_safe_context") as context_check,
        ):
            supervisor.start_group(group, since=None)
            supervisor.write()
            runner.start(
                data_dir=data_dir,
                collector_interval_s=0.04,
                # This case drives explicit ok -> error -> ok transitions.
                # Keep the success snapshot fresh during a loaded full-suite
                # run instead of letting scheduler delay turn it into stale.
                collector_stale_after_s=30.0,
                observation_feeds=(("tetragon", str(feed_path)),),
                observation_heartbeats=(("tetragon", str(heartbeat_path)),),
                observation_origin="synthetic",
            )
            running = _wait_for(
                lambda: _collector_source(
                    runner, "observation_feed_tetragon", "ok"
                ),
                timeout=10.0,
            )
            if running is None:
                raise RuntimeError(
                    "S4-05 initial observation collector did not reach ok"
                )
            started[0].returncode = 0
            supervisor.tick()
            supervisor.write()
            reconnecting = _wait_for(
                lambda: _collector_source(
                    runner, "observation_feed_tetragon", "error"
                ),
                timeout=10.0,
            )
            status_http, status_body = runner.get_json("/api/status")
            now[0] += 1.0
            supervisor.tick()
            supervisor.write()
            recovered = _wait_for(
                lambda: _collector_source(
                    runner, "observation_feed_tetragon", "ok"
                ),
                timeout=10.0,
            )
            context_calls = context_check.call_count
        helper._stop_children(group)

    gaps = [
        json.loads(line)
        for line in gap_path.read_text(encoding="utf-8").splitlines()
    ]
    backend_receipts = len(runner.engine.receipts) if runner.engine else -1
    gateway_records = len(_records_in(data_dir))
    writer = (recovered or {}).get("last_value", {}).get("writer", {})
    passed = (
        running is not None
        and reconnecting is not None
        and "reconnecting" in str(reconnecting.get("last_error"))
        and status_http == 200
        and status_body.get("collectors", {}).get("observation_feed_tetragon")
        == "error"
        and [item.get("schema") for item in gaps]
        == [helper.GAP_SCHEMA, helper.GAP_SCHEMA]
        and [item.get("event") for item in gaps] == ["gap_opened", "gap_closed"]
        and recovered is not None
        and writer.get("reconnect_count") == 1
        and writer.get("last_gap", {}).get("backfill") == "requested_unverified"
        and len(commands) == 2
        and any(part.startswith("--since-time=") for part in commands[1])
        and context_calls == 1
        and backend_receipts == 0
        and gateway_records == 0
    )
    return AcceptanceResult(
        "S4-05",
        "가짜 관측 자식 종료 후 heartbeat·gap 기록을 거쳐 재연결",
        "reconnecting 중 Gateway 수집원 error, gap 열림·닫힘, backfill 인자 재시작",
        {
            "initial_collector": (running or {}).get("state"),
            "reconnecting_collector": (reconnecting or {}).get("state"),
            "gateway_observation_state": status_body.get("collectors", {}).get(
                "observation_feed_tetragon"
            ),
            "gap_events": [item.get("event") for item in gaps],
            "gap_schemas": [item.get("schema") for item in gaps],
            "recovered_collector": (recovered or {}).get("state"),
            "reconnect_count": writer.get("reconnect_count"),
            "backfill_requested": any(
                part.startswith("--since-time=") for part in commands[1]
            ) if len(commands) > 1 else False,
            "backend_receipts": backend_receipts,
            "gateway_request_records": gateway_records,
            "real_kubectl_executed": False,
        },
        passed,
        [],
    )


def _run_s4_06(runner: _Runner) -> AcceptanceResult:
    data_dir = runner.data_dir / "s4-06"
    policy_path = runner.data_dir / "s4-06-policy.json"
    document = json.loads(
        (_ROOT / "examples" / "mvp" / "policy-v1.2-zeroday-lab.json").read_text(
            encoding="utf-8"
        )
    )
    document["max_n"] = 2
    policy_path.write_text(json.dumps(document), encoding="utf-8")
    runner.start_vllm(policy_path=policy_path, data_dir=data_dir)
    if runner.fake_backend is None:
        raise RuntimeError("fake backend is not running")
    marker = "s4-value-must-not-be-recorded"
    normal_body = json.dumps(
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "zeroday acceptance"}],
            "max_tokens": 16,
            "n": 2,
            "temperature": 0.7,
            "cache_salt": marker,
        }
    ).encode("utf-8")
    normal_status, normal_headers, _ = runner.json_request(
        "POST", "/v1/chat/completions", body=normal_body
    )
    normal_id = normal_headers.get("X-Request-ID")
    normal_received = next(
        (
            item
            for item in runner.fake_backend.requests
            if item.get("request_id") == normal_id
        ),
        {},
    )
    rejected_body = json.dumps(
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "zeroday acceptance"}],
            "max_tokens": 16,
            "n": 3,
            "temperature": 0.7,
            "cache_salt": marker,
        }
    ).encode("utf-8")
    rejected_status, rejected_headers, rejected_document = runner.json_request(
        "POST", "/v1/chat/completions", body=rejected_body
    )
    rejected_id = rejected_headers.get("X-Request-ID")
    normal_final = _final_record(runner, normal_id) if normal_id else {}
    rejected_final = _final_record(runner, rejected_id) if rejected_id else {}
    stored = (data_dir / "gateway_records.jsonl").read_text(encoding="utf-8")
    backend_count = runner.fake_backend.request_count
    expected_fields = [
        {"name": "cache_salt", "mode": "passthrough"},
        {"name": "temperature", "mode": "validate"},
    ]
    values = normal_received.get("extra_values", {})
    passed = (
        normal_status == 200
        and normal_received.get("n") == 2
        and values.get("temperature") == 0.7
        and values.get("cache_salt") == marker
        and normal_final.get("policy", {}).get("profile") == "zeroday-lab"
        and normal_final.get("request", {}).get("n") == 2
        and normal_final.get("request", {}).get("extra_fields") == expected_fields
        and normal_final.get("delivery", {}).get("attempted") is True
        and marker not in stored
        and rejected_status == 400
        and rejected_document.get("error", {}).get("code") == "N_UNSUPPORTED"
        and rejected_final.get("decision", {}).get("reason_code") == "N_UNSUPPORTED"
        and rejected_final.get("delivery", {}).get("attempted") is False
        and backend_count == 1
    )
    return AcceptanceResult(
        "S4-06",
        "정책 1.2 zeroday-lab에서 validate·passthrough 필드와 n=2 정상, n=3 거부 요청",
        "정상 요청만 fake backend 1회 전달, 이름·모드·n만 기록, n=3은 400 미전달",
        {
            "normal_http": normal_status,
            "backend_values_match": (
                values.get("temperature") == 0.7
                and values.get("cache_salt") == marker
                and normal_received.get("n") == 2
            ),
            "recorded_extra_fields": normal_final.get("request", {}).get(
                "extra_fields"
            ),
            "recorded_n": normal_final.get("request", {}).get("n"),
            "value_found_in_gateway_records": marker in stored,
            "rejected": [
                rejected_status,
                rejected_document.get("error", {}).get("code"),
            ],
            "rejected_delivery_attempted": rejected_final.get("delivery", {}).get(
                "attempted"
            ),
            "backend_chat_count": backend_count,
        },
        passed,
        [item for item in (normal_id, rejected_id) if item],
    )


def execute_acceptance(data_dir: Path) -> list[AcceptanceResult]:
    """Run MVP, S1/S2/S3A, fake-SGLang, and S4 acceptance cases."""

    runner = _Runner(data_dir)
    results: list[AcceptanceResult] = []
    try:
        runner.start()
        demo_results = run_demo_cases(runner.base_url)
        results.extend(_demo_group(case_id, demo_results) for case_id in (
            "MVP-01", "MVP-02", "MVP-03", "MVP-04", "MVP-05", "MVP-06"
        ))
        results.append(_run_07(runner))
        results.append(_run_08(runner))
        results.append(_run_09(runner))
        results.append(_run_10(runner))
        results.append(_run_11(runner))
        results.append(_run_s1_01(runner))
        results.append(_run_s1_02(runner))
        results.append(_run_s1_03(runner))
        results.append(_run_s1_04(runner))
        results.append(_run_s1_05(runner))
        results.append(_run_s1_06(runner))
        results.append(_run_s2_01(runner))
        results.append(_run_s2_02(runner))
        results.append(_run_s2_03(runner))
        results.append(_run_s2_04(runner))
        results.append(_run_s2_05(runner))
        results.append(_run_s2_06(runner))
        results.append(_run_s3a_01(runner))
        results.append(_run_s3a_02(runner))
        results.append(_run_s3a_03(runner))
        results.append(_run_s3a_04(runner))
        results.append(_run_s3a_05(runner))
        results.append(_run_s3a_06(runner))
        results.append(_run_s3a_07(runner))
        results.append(_run_s3a_08(runner))
        results.append(_run_sgl_01(runner))
        results.append(_run_sgl_02(runner))
        results.append(_run_s4_01(runner))
        results.append(_run_s4_02(runner))
        results.append(_run_s4_03(runner))
        results.append(_run_s4_04(runner))
        results.append(_run_s4_05(runner))
        results.append(_run_s4_06(runner))
        return results
    finally:
        runner.stop()


def _timestamp() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.isoformat(), now.strftime("%Y%m%dT%H%M%S.%fZ")


def _md(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text.replace("|", "\\|").replace("\n", "<br>")


def write_reports(
    report_dir: str | Path,
    data_dir: Path,
    results: list[AcceptanceResult],
) -> tuple[Path, Path]:
    executed_at, filename = _timestamp()
    directory = Path(report_dir).expanduser()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    common = {
        "executed_at": executed_at,
        "python_version": platform.python_version(),
        "os": platform.platform(),
        "data_dir": str(data_dir),
        "s3a_backend_fixture": (
            "fake_backend_test; this report does not contain a real vLLM execution"
        ),
        "s4_06_backend_fixture": (
            "fake_backend_test; this report does not contain a real vLLM execution"
        ),
        "sglang_backend_fixture": (
            "fake_sglang_test_only; this report does not contain a real SGLang execution"
        ),
        "passed": all(result.passed for result in results),
        "cases": [asdict(result) for result in results],
    }
    json_path = directory / f"{filename}.json"
    md_path = directory / f"{filename}.md"
    json_path.write_text(json.dumps(common, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 런타임 통제 MVP 수용 시험",
        "",
        f"- 실행 시각(UTC): {executed_at}",
        f"- Python: {platform.python_version()}",
        f"- OS: {platform.platform()}",
        f"- 임시 data_dir: `{data_dir}`",
        "- S3A backend: `fake_backend_test` (실제 vLLM 실행·검증 아님)",
        "- S4-06 backend: `fake_backend_test` (실제 vLLM 실행·검증 아님)",
        "- SGL-01~02 backend: `fake_sglang_test_only` (실제 SGLang 실행·검증 아님)",
        f"- 전체 결과: {'통과' if common['passed'] else '실패'}",
        "",
        "| 사례 | 명령 | 기대 | 실제 | 결과 | 증거 요청 ID |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        lines.append(
            "| " + " | ".join((
                result.case_id,
                _md(result.command),
                _md(result.expected),
                _md(result.actual),
                "통과" if result.passed else "실패",
                "<br>".join(result.evidence_request_ids) or "–",
            )) + " |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def run_cli(report_dir: str | None = None) -> int:
    target_report_dir = (
        Path(report_dir).expanduser()
        if report_dir is not None
        else Path(MVPConfig().data_dir).expanduser() / "acceptance"
    )
    try:
        with tempfile.TemporaryDirectory(prefix="serving-runtime-acceptance-") as temporary:
            data_dir = Path(temporary)
            results = execute_acceptance(data_dir)
            json_path, md_path = write_reports(target_report_dir, data_dir, results)
    except (OSError, RuntimeError, DemoConnectionError) as error:
        print(f"수용 시험 실행 실패: {error}", file=sys.stderr)
        return 1
    for result in results:
        print(f"{result.case_id}: {'통과' if result.passed else '실패'}")
    print(f"JSON 보고서: {json_path}")
    print(f"Markdown 보고서: {md_path}")
    return 0 if all(result.passed for result in results) else 1
