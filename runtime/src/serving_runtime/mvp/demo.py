"""Run and judge the repeatable local MVP demonstration cases."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import sys
from typing import Any, TextIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import MVPConfig


@dataclass(frozen=True)
class DemoCase:
    case_id: str
    description: str
    expected_http: int
    expected_result: str
    body: bytes
    content_type: str = "application/json"
    expected_evidence_state: str | None = None
    expected_rule_id: str | None = None
    expected_max_tokens: int | None = None
    expected_done: bool = False

    @property
    def expected_text(self) -> str:
        parts = [f"HTTP {self.expected_http}", self.expected_result]
        if self.expected_evidence_state:
            parts.append(f"증거 {self.expected_evidence_state}")
        if self.expected_rule_id:
            parts.append(f"규칙 {self.expected_rule_id}")
        if self.expected_max_tokens is not None:
            parts.append(f"max_tokens {self.expected_max_tokens}")
        if self.expected_done:
            parts.append("SSE [DONE]")
        return ", ".join(parts)


@dataclass
class DemoResult:
    case_id: str
    description: str
    expected: str
    actual_http: int | None
    actual_result: str | None
    request_id: str | None
    passed: bool
    failures: list[str]
    evidence_state: str | None
    rule_id: str | None
    max_tokens: int | None
    saw_done: bool | None = None


class DemoConnectionError(ConnectionError):
    """The configured Gateway could not be reached."""


def _json_body(**overrides: Any) -> bytes:
    document: dict[str, Any] = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "demo"}],
        "max_tokens": 64,
    }
    document.update(overrides)
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def demo_cases() -> list[DemoCase]:
    """Return cases in the order required for the live demonstration."""

    without_max_tokens = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "demo"}],
    }
    return [
        DemoCase("MVP-01", "정상 요청", 200, "ALLOWED", _json_body(),
                 expected_evidence_state="received"),
        DemoCase("MVP-02a", "상한값 128 허용", 200, "ALLOWED", _json_body(max_tokens=128)),
        DemoCase("MVP-02b", "상한 초과 129 거부", 403, "DENIED", _json_body(max_tokens=129),
                 expected_evidence_state="not_received"),
        DemoCase("MVP-03", "거부 직후 정상 요청", 200, "ALLOWED", _json_body()),
        DemoCase("MVP-04", "미허용 모델 거부", 403, "DENIED",
                 _json_body(model="other-model"), expected_rule_id="allowed_models"),
        DemoCase("MVP-05a", "max_tokens 생략 시 기본값", 200, "ALLOWED",
                 json.dumps(without_max_tokens).encode("utf-8"), expected_max_tokens=128),
        DemoCase("MVP-05b", "max_tokens 0 거부", 400, "INVALID", _json_body(max_tokens=0)),
        DemoCase("MVP-05c", "max_tokens -1 거부", 400, "INVALID", _json_body(max_tokens=-1)),
        DemoCase("MVP-05d", "max_tokens 문자열 거부", 400, "INVALID", _json_body(max_tokens="64")),
        DemoCase("MVP-05e", "max_tokens bool 거부", 400, "INVALID", _json_body(max_tokens=True)),
        DemoCase("MVP-06a", "stream 타입 오류", 400, "INVALID", _json_body(stream="true")),
        DemoCase("MVP-06b", "미지원 필드 거부", 400, "INVALID", _json_body(unsupported="x")),
        DemoCase("MVP-06c", "70,000바이트 본문 거부", 413, "INVALID", b"x" * 70000),
        DemoCase("MVP-06d", "text/plain 거부", 415, "INVALID", _json_body(),
                 content_type="text/plain"),
        DemoCase("S1-DEMO", "stream 정상 완료", 200, "ALLOWED", _json_body(stream=True),
                 expected_done=True),
    ]


def evaluate_case(
    case: DemoCase,
    actual_http: int,
    request_id: str | None,
    detail: dict[str, Any] | None,
    saw_done: bool | None = None,
) -> DemoResult:
    """Judge one case without performing socket I/O."""

    final = detail.get("final_state") if isinstance(detail, dict) else None
    final = final if isinstance(final, dict) else {}
    actual_result = final.get("result") if isinstance(final.get("result"), str) else None
    evidence_state = (
        detail.get("evidence_state")
        if isinstance(detail, dict) and isinstance(detail.get("evidence_state"), str)
        else None
    )
    records = detail.get("gateway_records") if isinstance(detail, dict) else None
    final_record = None
    if isinstance(records, list):
        final_record = next(
            (item for item in reversed(records)
             if isinstance(item, dict) and item.get("phase") == "final"),
            None,
        )
    decision = final_record.get("decision") if isinstance(final_record, dict) else None
    decision = decision if isinstance(decision, dict) else {}
    rule_id = decision.get("rule_id") if isinstance(decision.get("rule_id"), str) else None
    max_tokens = final.get("max_tokens")
    failures: list[str] = []
    if actual_http != case.expected_http:
        failures.append(f"HTTP {case.expected_http} 기대, 실제 {actual_http}")
    if not request_id:
        failures.append("응답에 X-Request-ID 없음")
    if actual_result != case.expected_result:
        failures.append(f"결과 {case.expected_result} 기대, 실제 {actual_result or '없음'}")
    if case.expected_evidence_state and evidence_state != case.expected_evidence_state:
        failures.append(
            f"증거 {case.expected_evidence_state} 기대, 실제 {evidence_state or '없음'}"
        )
    if case.expected_rule_id and rule_id != case.expected_rule_id:
        failures.append(f"규칙 {case.expected_rule_id} 기대, 실제 {rule_id or '없음'}")
    if case.expected_max_tokens is not None and max_tokens != case.expected_max_tokens:
        failures.append(
            f"max_tokens {case.expected_max_tokens} 기대, 실제 {max_tokens!r}"
        )
    if case.expected_done and saw_done is not True:
        failures.append("SSE [DONE] 종료 표식 없음")
    return DemoResult(
        case.case_id,
        case.description,
        case.expected_text,
        actual_http,
        actual_result,
        request_id,
        not failures,
        failures,
        evidence_state,
        rule_id,
        max_tokens if isinstance(max_tokens, int) and not isinstance(max_tokens, bool) else None,
        saw_done,
    )


def _read_json(response: Any) -> dict[str, Any] | None:
    try:
        payload = json.loads(response.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _post_case(
    gateway: str, case: DemoCase, timeout: float
) -> tuple[int, str | None, bool | None]:
    try:
        request = Request(
            f"{gateway}/v1/chat/completions",
            data=case.body,
            headers={"Content-Type": case.content_type},
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            return (
                response.status,
                response.headers.get("X-Request-ID"),
                b"data: [DONE]\n\n" in body if case.expected_done else None,
            )
    except HTTPError as error:
        with error:
            error.read()
            return error.code, error.headers.get("X-Request-ID"), None
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise DemoConnectionError(str(error)) from error


def _get_detail(gateway: str, request_id: str, timeout: float) -> dict[str, Any] | None:
    try:
        with urlopen(f"{gateway}/api/requests/{request_id}", timeout=timeout) as response:
            return _read_json(response)
    except HTTPError as error:
        with error:
            error.read()
            return None
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise DemoConnectionError(str(error)) from error


def run_demo_cases(gateway: str, *, timeout: float = 5.0) -> list[DemoResult]:
    """Send all demo cases to an already-running Gateway."""

    base = gateway.rstrip("/")
    results: list[DemoResult] = []
    for case in demo_cases():
        status, request_id, saw_done = _post_case(base, case, timeout)
        detail = _get_detail(base, request_id, timeout) if request_id else None
        results.append(evaluate_case(case, status, request_id, detail, saw_done))
    return results


def _timestamp() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.isoformat(), now.strftime("%Y%m%dT%H%M%S.%fZ")


def write_demo_report(
    data_dir: str | Path, gateway: str, results: list[DemoResult]
) -> Path:
    executed_at, filename = _timestamp()
    output_dir = Path(data_dir).expanduser() / "demo-runs"
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = output_dir / f"{filename}.json"
    report = {
        "executed_at": executed_at,
        "gateway": gateway,
        "passed": all(result.passed for result in results),
        "cases": [asdict(result) for result in results],
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def print_results(results: list[DemoResult], output: TextIO = sys.stdout) -> None:
    headings = ("사례 ID", "설명", "기대", "실제 HTTP", "결과", "요청 ID", "통과")
    print(" | ".join(headings), file=output)
    print(" | ".join("---" for _ in headings), file=output)
    for result in results:
        print(
            " | ".join(
                (
                    result.case_id,
                    result.description,
                    result.expected,
                    str(result.actual_http) if result.actual_http is not None else "–",
                    result.actual_result or "–",
                    result.request_id[:8] if result.request_id else "–",
                    "통과" if result.passed else "실패",
                )
            ),
            file=output,
        )
        for failure in result.failures:
            print(f"  - {failure}", file=output)


def run_cli(gateway: str, data_dir: str | None = None) -> int:
    target_data_dir = data_dir if data_dir is not None else MVPConfig().data_dir
    try:
        results = run_demo_cases(gateway)
    except DemoConnectionError as error:
        print(
            f"Gateway에 연결할 수 없습니다: {gateway} ({error})\n"
            "먼저 다른 터미널에서 'serving-runtime mvp'를 실행했는지 확인하세요.",
            file=sys.stderr,
        )
        return 2
    print_results(results)
    try:
        report = write_demo_report(target_data_dir, gateway, results)
    except OSError as error:
        print(f"시연 결과 저장 실패: {error}", file=sys.stderr)
        return 1
    print(f"\n결과 JSON: {report}")
    print("대시보드에서 확인: http://127.0.0.1:8080")
    return 0 if all(result.passed for result in results) else 1
