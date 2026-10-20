"""Self-contained MVP acceptance run and human/machine-readable reports."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import platform
import sys
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .app import GatewayApp
from .config import MVPConfig
from .demo import DemoConnectionError, DemoResult, run_demo_cases
from .mock_engine import MockEngine
from .policy import load_policy
from .store import JsonlStore


_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE_POLICY = _ROOT / "examples" / "mvp" / "policy.json"


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
        self.gateway: GatewayApp | None = None

    def stop(self) -> None:
        if self.gateway is not None:
            self.gateway.stop()
            self.gateway = None
        if self.engine is not None:
            self.engine.stop()
            self.engine = None

    def start(
        self,
        *,
        policy_path: Path = _EXAMPLE_POLICY,
        response_delay_s: float = 0,
        relay_timeout_s: float = 0.5,
        data_dir: Path | None = None,
    ) -> None:
        self.stop()
        active_data_dir = data_dir or self.data_dir
        self.engine = MockEngine(
            port=0,
            response_delay_s=response_delay_s,
            receipts_path=active_data_dir / "engine_receipts.jsonl",
        ).start()
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(policy_path),
            relay_timeout_s=relay_timeout_s,
            data_dir=str(active_data_dir),
        )
        self.gateway = GatewayApp(config, load_policy(policy_path), self.engine.address).start()

    def start_gateway(
        self,
        policy_path: Path,
        *,
        data_dir: Path | None = None,
        relay_timeout_s: float = 0.5,
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
            data_dir=str(active_data_dir),
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
    ) -> tuple[int, dict[str, Any], bytes]:
        headers = {"Content-Type": content_type} if body is not None else {}
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
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

    def post_chat(self, max_tokens: int = 64, *, prompt: str = "acceptance") -> tuple[int, str | None, dict[str, Any]]:
        body = json.dumps(
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        status, headers, document = self.json_request(
            "POST", "/v1/chat/completions", body=body
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


def execute_acceptance(data_dir: Path) -> list[AcceptanceResult]:
    """Run MVP-01 through MVP-11 against temporary local servers."""

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
