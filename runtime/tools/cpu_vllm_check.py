"""One-shot local Gateway check against the real CPU vLLM container.

The vLLM container and Gateway must already be running.  This tool does not
start, stop, or reconfigure either server.  It stores only result metadata,
request IDs, selected Gateway record fields, and boolean engine-log matches;
tokens, prompts, response text, and raw engine logs are never written.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit


CASE_TITLES = {
    "CPU-01": "정상 chat",
    "CPU-02": "chat 스트리밍 [DONE]",
    "CPU-03": "클라이언트 취소와 엔진 abort 증거",
    "CPU-04": "인증 없음·오류 거부",
    "CPU-05": "출력 상한 거부",
    "CPU-06": "context 상한 거부",
    "CPU-07": "context 한도 내 허용",
    "CPU-08": "허용되지 않은 필드 거부",
    "CPU-09": "completions 정상",
    "CPU-10": "completions 스트리밍 [DONE]",
    "CPU-11": "completions prompt 배열 거부",
    "CPU-12": "대상 등록 보류 없음",
    "CPU-13": "정책 묶음 적용",
    "CPU-14": "조회 API와 엔진 수집기",
    "CPU-15": "logprobs 선택 시험",
}
BASE_CASE_IDS = tuple(f"CPU-{number:02d}" for number in range(1, 15))
ALL_CASE_IDS = (*BASE_CASE_IDS, "CPU-15")
RESULTS = ("passed", "failed", "not_run", "unknown")
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,256}")
_TOKEN = re.compile(r"[\x21-\x7e]{16,512}")

# A false fact is a contradictory observation.  A missing/None fact is an
# evidence gap and therefore unknown, never a pass.
CASE_RULES: dict[str, tuple[str, ...]] = {
    "CPU-01": ("http_200", "valid_response", "record_allowed", "delivery_attempted", "engine_log_match"),
    "CPU-02": ("http_200", "done", "record_completed", "engine_log_match"),
    "CPU-03": ("http_200", "client_disconnected", "slot_released", "engine_received", "engine_aborted"),
    "CPU-04": ("both_401", "error_codes", "records_denied", "not_delivered"),
    "CPU-05": ("limit_read", "http_403", "reason_code", "not_delivered"),
    "CPU-06": ("http_403", "reason_code", "not_delivered", "input_count_recorded"),
    "CPU-07": ("http_200", "record_allowed", "input_count_recorded", "within_limit_recorded"),
    "CPU-08": ("http_400", "invalid_field_code", "not_delivered"),
    "CPU-09": ("http_200", "valid_response", "record_allowed", "engine_log_match"),
    "CPU-10": ("http_200", "done", "record_completed", "engine_log_match"),
    "CPU-11": ("http_400", "invalid_prompt_type", "not_delivered"),
    "CPU-12": ("registration_ok", "no_gateway_hold", "normal_request_not_held"),
    "CPU-13": ("bundle_present", "required_roles_present", "all_files_applied"),
    "CPU-14": ("requests_ok", "detail_ok", "status_ok", "engine_collectors_ok"),
    "CPU-15": ("policy_allows_logprobs", "http_200", "valid_response", "engine_log_match", "engine_healthy_after"),
}


class ConfigurationError(ValueError):
    """A safe-to-display setup error."""


class HardTimeout(TimeoutError):
    """The client-side total deadline expired."""


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class StreamResponse:
    status: int
    headers: Mapping[str, str]
    event_count: int
    done: bool
    aborted: bool


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner:
    """Subprocess adapter whose journal never contains command output."""

    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []

    def run(self, argv: Sequence[str], *, timeout: float = 30) -> CommandResult:
        started_at = _now()
        try:
            completed = subprocess.run(
                list(argv), capture_output=True, text=True, check=False,
                timeout=timeout, shell=False,
            )
            result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
        except subprocess.TimeoutExpired:
            result = CommandResult(124)
        self.history.append({
            "argv": list(argv),
            "started_at": started_at,
            "ended_at": _now(),
            "returncode": result.returncode,
        })
        return result


class HTTPClient:
    """Small stdlib HTTP client with a total, not per-read, deadline."""

    @staticmethod
    def _connection(url: str, timeout: float) -> tuple[http.client.HTTPConnection, str]:
        parsed = urlsplit(url)
        if parsed.scheme != "http" or not parsed.hostname:
            raise ValueError("only an http URL is supported")
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        return connection, path

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HardTimeout("HTTP total deadline expired")
        return remaining

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 30,
    ) -> HTTPResponse:
        deadline = time.monotonic() + timeout
        connection, path = self._connection(url, timeout)
        try:
            connection.request(method, path, body=body, headers=dict(headers or {}))
            if connection.sock is not None:
                connection.sock.settimeout(self._remaining(deadline))
            response = connection.getresponse()
            chunks: list[bytes] = []
            while True:
                if connection.sock is not None:
                    connection.sock.settimeout(self._remaining(deadline))
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            return HTTPResponse(response.status, dict(response.getheaders()), b"".join(chunks))
        except socket.timeout as error:
            raise HardTimeout("HTTP total deadline expired") from error
        finally:
            connection.close()

    def stream(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
        abort_after_first: bool = False,
    ) -> StreamResponse:
        deadline = time.monotonic() + timeout
        connection, path = self._connection(url, timeout)
        event_count = 0
        done = False
        try:
            connection.request("POST", path, body=body, headers=dict(headers))
            if connection.sock is not None:
                connection.sock.settimeout(self._remaining(deadline))
            response = connection.getresponse()
            response_headers = dict(response.getheaders())
            if response.status != 200:
                if connection.sock is not None:
                    connection.sock.settimeout(self._remaining(deadline))
                response.read()
                return StreamResponse(response.status, response_headers, 0, False, False)
            buffer = b""
            while True:
                if connection.sock is not None:
                    connection.sock.settimeout(self._remaining(deadline))
                chunk = response.read1(4096)
                if not chunk:
                    break
                buffer += chunk
                event_count += chunk.count(b"data:")
                if b"data: [DONE]" in buffer:
                    done = True
                if abort_after_first:
                    if connection.sock is not None:
                        connection.sock.close()
                    return StreamResponse(200, response_headers, event_count, done, True)
                buffer = buffer[-64:]
            return StreamResponse(200, response_headers, event_count, done, False)
        except socket.timeout as error:
            raise HardTimeout("HTTP stream total deadline expired") from error
        finally:
            connection.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    return next((value for key, value in headers.items() if key.lower() == lowered), None)


def _json_object(raw: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _error_code(response: HTTPResponse) -> str | None:
    document = _json_object(response.body)
    error = document.get("error") if document else None
    return error.get("code") if isinstance(error, dict) and isinstance(error.get("code"), str) else None


def _request_id(headers: Mapping[str, str]) -> str | None:
    value = _header(headers, "X-Request-ID")
    return value if isinstance(value, str) and _REQUEST_ID.fullmatch(value) else None


def _record_fields(detail: object) -> dict[str, Any] | None:
    """Select metadata-only fields; never copy request or response content."""

    if not isinstance(detail, dict):
        return None
    records = detail.get("gateway_records")
    final = None
    if isinstance(records, list):
        final = next(
            (item for item in reversed(records) if isinstance(item, dict) and item.get("phase") == "final"),
            None,
        )
    if not isinstance(final, dict):
        return None
    decision = final.get("decision") if isinstance(final.get("decision"), dict) else {}
    delivery = final.get("delivery") if isinstance(final.get("delivery"), dict) else {}
    request = final.get("request") if isinstance(final.get("request"), dict) else {}
    context = request.get("context") if isinstance(request.get("context"), dict) else None
    target = final.get("target") if isinstance(final.get("target"), dict) else {}
    auth = final.get("auth") if isinstance(final.get("auth"), dict) else {}
    return {
        "result": decision.get("result"),
        "reason_code": decision.get("reason_code"),
        "rule_id": decision.get("rule_id"),
        "http_status": decision.get("http_status"),
        "final_outcome": final.get("final_outcome"),
        "stream": final.get("stream"),
        "backend_kind": final.get("backend_kind"),
        "backend_cancel": final.get("backend_cancel"),
        "delivery": {
            "attempted": delivery.get("attempted"),
            "response": delivery.get("response"),
            "error_code": delivery.get("error_code"),
        },
        "context": ({
            "limit": context.get("limit"),
            "input_tokens": context.get("input_tokens"),
            "requested_total": context.get("requested_total"),
            "result": context.get("result"),
        } if context is not None else None),
        "target": {
            "registration": target.get("registration"),
            "target_id": target.get("target_id"),
            "instance_state": target.get("instance_state"),
        },
        "auth": {
            "state": auth.get("state"),
            "method": auth.get("method"),
            "principal_id": auth.get("principal_id"),
            "roles": auth.get("roles") if isinstance(auth.get("roles"), list) else [],
        },
    }


def _redact(value: Any, secrets_to_remove: Iterable[str]) -> Any:
    secrets_tuple = tuple(secret for secret in secrets_to_remove if secret)
    if isinstance(value, str):
        result = value
        for secret in secrets_tuple:
            result = result.replace(secret, "[REDACTED]")
        return result
    if isinstance(value, dict):
        return {str(key): _redact(item, secrets_tuple) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, secrets_tuple) for item in value]
    return value


def _read_tokens(path: Path) -> dict[str, str]:
    try:
        information = path.stat()
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigurationError("token env file could not be read") from error
    if information.st_mode & 0o077:
        raise ConfigurationError("token env file must not be accessible by group or others")
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator != "=" or key not in {"RUNTIME_INFERENCE_TOKEN", "RUNTIME_VIEWER_TOKEN"}:
            raise ConfigurationError("token env file has an unsupported line")
        if key in values or _TOKEN.fullmatch(value) is None:
            raise ConfigurationError("token env file has a duplicate key or invalid token")
        values[key] = value
    if set(values) != {"RUNTIME_INFERENCE_TOKEN", "RUNTIME_VIEWER_TOKEN"}:
        raise ConfigurationError("token env file must contain inference and viewer tokens")
    return values


def target_hold_reason(document: object) -> str | None:
    """Return the explicit hold code, or None when the target is not held."""

    if not isinstance(document, dict):
        return "TARGET_STATUS_UNAVAILABLE"
    hold = document.get("gateway_hold")
    if hold is None:
        return None
    if not isinstance(hold, dict):
        return "TARGET_STATUS_UNAVAILABLE"
    code = hold.get("reason_code")
    return code if isinstance(code, str) and code else "TARGET_STATUS_UNAVAILABLE"


def evaluate_case(case_id: str, facts: Mapping[str, Any]) -> tuple[str, str]:
    if case_id not in CASE_RULES:
        raise ValueError("unknown CPU case")
    if facts.get("not_run_reason"):
        return "not_run", str(facts["not_run_reason"])
    if facts.get("unknown_reason"):
        return "unknown", str(facts["unknown_reason"])
    required = CASE_RULES[case_id]
    contradicted = [name for name in required if facts.get(name) is False]
    if contradicted:
        return "failed", "기대 결과 불일치: " + ", ".join(contradicted)
    missing = [name for name in required if facts.get(name) is not True]
    if missing:
        return "unknown", "판정 근거 부족: " + ", ".join(missing)
    return "passed", "모든 필수 결과를 확인함"


def requested_case_ids(include_logprobs: bool) -> tuple[str, ...]:
    return ALL_CASE_IDS if include_logprobs else BASE_CASE_IDS


def _overall(cases: Sequence[Mapping[str, Any]], requested: Sequence[str]) -> str:
    by_id = {case.get("case_id"): case.get("result") for case in cases}
    return "passed" if requested and all(by_id.get(case_id) == "passed" for case_id in requested) else "failed"


class CPUVLLMCheck:
    def __init__(
        self,
        args: argparse.Namespace,
        *,
        runner: Runner | None = None,
        http: HTTPClient | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.args = args
        self.gateway = self._origin(args.gateway_url, "Gateway")
        self.vllm = self._origin(args.vllm_url, "vLLM")
        self.tokens = _read_tokens(Path(args.token_env_file).expanduser())
        self.wrong_token = "cpu-vllm-invalid-" + secrets.token_hex(16)
        self.runner = runner or Runner()
        self.http = http or HTTPClient()
        self.sleep = sleeper
        self.monotonic = monotonic
        self.started_at = _now()
        self.http_events: list[dict[str, Any]] = []
        self.cases: list[dict[str, Any]] = []
        self.normal_request_id: str | None = None
        self.normal_status: int | None = None
        self.normal_error_code: str | None = None
        self.normal_detail: dict[str, Any] | None = None
        self.initial_target: dict[str, Any] | None = None

        base = Path(args.evidence_dir).expanduser()
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(base, 0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_dir = base / f"cpu-vllm-{stamp}-{os.getpid()}"
        self.run_dir.mkdir(mode=0o700)
        os.chmod(self.run_dir, 0o700)

    @staticmethod
    def _origin(value: str, label: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ConfigurationError(f"{label} URL must be an http origin without credentials")
        try:
            port = parsed.port or 80
        except ValueError as error:
            raise ConfigurationError(f"{label} URL has an invalid port") from error
        return f"http://{parsed.hostname}:{port}"

    @property
    def inference_token(self) -> str:
        return self.tokens["RUNTIME_INFERENCE_TOKEN"]

    @property
    def viewer_token(self) -> str:
        return self.tokens["RUNTIME_VIEWER_TOKEN"]

    def _headers(self, token: str | None, *, json_body: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None,
        payload: Mapping[str, Any] | None = None,
        timeout: float = 30,
        base: str | None = None,
    ) -> HTTPResponse:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            response = self.http.request(
                method, (base or self.gateway) + path,
                headers=self._headers(token, json_body=payload is not None),
                body=body, timeout=timeout,
            )
        except Exception as error:
            self.http_events.append({
                "method": method, "path": path, "status": None,
                "error": type(error).__name__,
            })
            raise
        self.http_events.append({
            "method": method, "path": path, "status": response.status,
            "request_id": _request_id(response.headers),
        })
        return response

    def _stream(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        timeout: float = 300,
        abort_after_first: bool = False,
    ) -> StreamResponse:
        try:
            response = self.http.stream(
                self.gateway + path,
                headers=self._headers(self.inference_token, json_body=True),
                body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=timeout, abort_after_first=abort_after_first,
            )
        except Exception as error:
            self.http_events.append({
                "method": "POST", "path": path, "status": None,
                "error": type(error).__name__,
            })
            raise
        self.http_events.append({
            "method": "POST", "path": path, "status": response.status,
            "request_id": _request_id(response.headers), "stream": True,
        })
        return response

    def _get_json(self, path: str) -> tuple[HTTPResponse, dict[str, Any] | None]:
        response = self._request("GET", path, token=self.viewer_token, timeout=30)
        return response, _json_object(response.body)

    def _detail(self, request_id: str | None) -> dict[str, Any] | None:
        if request_id is None or _REQUEST_ID.fullmatch(request_id) is None:
            return None
        response, document = self._get_json(f"/api/requests/{request_id}")
        return document if response.status == 200 else None

    def _wait_detail(
        self, request_id: str | None, predicate: Callable[[dict[str, Any]], bool], timeout: float = 30,
    ) -> dict[str, Any] | None:
        deadline = self.monotonic() + timeout
        latest = None
        while self.monotonic() < deadline:
            latest = self._detail(request_id)
            if latest is not None and predicate(latest):
                return latest
            self.sleep(0.25)
        return latest

    @staticmethod
    def _chat(*, max_tokens: int = 8, stream: bool = False, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "Reply with one short greeting."}],
            "max_tokens": max_tokens,
        }
        if stream:
            payload["stream"] = True
        payload.update(extra)
        return payload

    @staticmethod
    def _completion(*, max_tokens: int = 8, stream: bool = False, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": "test-model", "prompt": "A short greeting:", "max_tokens": max_tokens,
        }
        if stream:
            payload["stream"] = True
        payload.update(extra)
        return payload

    @staticmethod
    def _valid_chat(response: HTTPResponse) -> bool:
        document = _json_object(response.body)
        choices = document.get("choices") if document else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return False
        message = choices[0].get("message")
        return isinstance(message, dict) and isinstance(message.get("content"), str) and bool(message["content"])

    @staticmethod
    def _valid_completion(response: HTTPResponse) -> bool:
        document = _json_object(response.body)
        choices = document.get("choices") if document else None
        return (
            isinstance(choices, list) and bool(choices) and isinstance(choices[0], dict)
            and isinstance(choices[0].get("text"), str) and bool(choices[0]["text"])
        )

    def _engine_logs(self, since: str) -> tuple[bool, str]:
        result = self.runner.run(
            ["docker", "logs", "--since", since, self.args.docker_container], timeout=60,
        )
        return result.returncode == 0, result.stdout + result.stderr

    def _wait_engine_log_matches(
        self,
        since: str,
        request_id: str | None,
        *,
        route: str,
        require_abort: bool = False,
        timeout: float = 10,
    ) -> tuple[bool | None, bool | None]:
        """Poll Docker logs, returning only confirmed format matches.

        False is returned only when logs were readable.  Callers still map an
        unmatched request-ID format to unknown because absence is not proof.
        """

        deadline = self.monotonic() + timeout
        readable = False
        received = False
        aborted = False
        while self.monotonic() < deadline:
            logs_ok, logs = self._engine_logs(since)
            readable = readable or logs_ok
            if logs_ok:
                received = received or self._received(logs, request_id, route)
                aborted = aborted or self._aborted(logs, request_id, route)
            if received and (aborted or not require_abort):
                break
            self.sleep(0.5)
        if not readable:
            return None, None
        return received, aborted if require_abort else None

    @staticmethod
    def _received(logs: str, request_id: str | None, route: str) -> bool:
        if request_id is None:
            return False
        if route == "/v1/chat/completions":
            engine_id = f"chatcmpl-{request_id}"
        elif route == "/v1/completions":
            engine_id = f"cmpl-{request_id}-0"
        else:
            return False
        return re.search(
            rf"Received request {re.escape(engine_id)}(?:\b|\.)", logs
        ) is not None

    @staticmethod
    def _aborted(logs: str, request_id: str | None, route: str) -> bool:
        if request_id is None:
            return False
        if route == "/v1/chat/completions":
            engine_id = f"chatcmpl-{request_id}"
        elif route == "/v1/completions":
            engine_id = f"cmpl-{request_id}-0"
        else:
            return False
        return f"Request {engine_id} aborted." in logs

    def _vllm_health(self, timeout: float = 10) -> bool:
        try:
            return self._request("GET", "/health", token=None, timeout=timeout, base=self.vllm).status == 200
        except Exception:
            return False

    def _case(
        self,
        case_id: str,
        started_at: str,
        http_start: int,
        command_start: int,
        facts: Mapping[str, Any],
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        result, reason = evaluate_case(case_id, facts)
        events = self.http_events[http_start:]
        item = {
            "case_id": case_id,
            "title": CASE_TITLES[case_id],
            "started_at": started_at,
            "ended_at": _now(),
            "result": result,
            "reason": reason,
            "facts": dict(facts),
            "evidence": {
                "request_ids": [event["request_id"] for event in events if event.get("request_id")],
                "http_codes": [event.get("status") for event in events],
                "record_fields": None,
                **dict(evidence or {}),
            },
            "http": events,
            "commands": self.runner.history[command_start:],
        }
        self.cases.append(item)
        labels = {"passed": "통과", "failed": "실패", "not_run": "미실행", "unknown": "확인 불가"}
        print(f"{case_id} {CASE_TITLES[case_id]}: {labels[result]} — {reason}")

    def _run_case(self, case_id: str, function: Callable[[], tuple[dict[str, Any], dict[str, Any]]]) -> None:
        started = _now()
        http_start = len(self.http_events)
        command_start = len(self.runner.history)
        try:
            facts, evidence = function()
        except Exception as error:
            facts = {"unknown_reason": f"사례 실행 오류({type(error).__name__})"}
            evidence = {}
        self._case(case_id, started, http_start, command_start, facts, evidence)

    def _wait_target_ready(self, timeout: float = 30) -> dict[str, Any] | None:
        deadline = self.monotonic() + timeout
        latest = None
        while self.monotonic() < deadline:
            try:
                response, latest = self._get_json("/api/target")
                if response.status == 200 and target_hold_reason(latest) is None:
                    return latest
            except Exception:
                pass
            self.sleep(0.5)
        return latest

    def cpu_01(self) -> tuple[dict[str, Any], dict[str, Any]]:
        since = _now()
        response = self._request(
            "POST", "/v1/chat/completions", token=self.inference_token,
            payload=self._chat(), timeout=300,
        )
        request_id = _request_id(response.headers)
        detail = self._detail(request_id)
        record = _record_fields(detail)
        log_match, _ = self._wait_engine_log_matches(
            since, request_id, route="/v1/chat/completions"
        )
        self.normal_request_id = request_id
        self.normal_status = response.status
        self.normal_error_code = _error_code(response)
        self.normal_detail = detail
        facts: dict[str, Any] = {
            "http_200": response.status == 200,
            "valid_response": self._valid_chat(response),
            "record_allowed": None if record is None else record["result"] == "ALLOWED",
            "delivery_attempted": None if record is None else record["delivery"]["attempted"] is True,
            "engine_log_match": log_match,
        }
        if log_match is not True:
            facts = {"unknown_reason": "요청 ID가 포함된 vLLM 수신 로그 형식을 확인하지 못함", **facts}
        return facts, {
            "record_fields": record,
            "engine_evidence": {
                "state": "observed" if log_match else "unknown",
                "match_format": "Received request chatcmpl-<gateway request id>",
                "raw_logs_stored": False,
            },
        }

    def cpu_02(self) -> tuple[dict[str, Any], dict[str, Any]]:
        since = _now()
        response = self._stream("/v1/chat/completions", self._chat(stream=True), timeout=300)
        request_id = _request_id(response.headers)
        detail = self._detail(request_id)
        record = _record_fields(detail)
        log_match, _ = self._wait_engine_log_matches(
            since, request_id, route="/v1/chat/completions"
        )
        facts: dict[str, Any] = {
            "http_200": response.status == 200,
            "done": response.done,
            "record_completed": None if record is None else record["final_outcome"] == "completed",
            "engine_log_match": log_match,
        }
        if log_match is not True:
            facts = {"unknown_reason": "요청 ID가 포함된 vLLM 수신 로그 형식을 확인하지 못함", **facts}
        return facts, {
            "stream": {"event_count": response.event_count, "done": response.done},
            "record_fields": record,
            "engine_evidence": {"state": "observed" if log_match else "unknown", "raw_logs_stored": False},
        }

    def cpu_03(self) -> tuple[dict[str, Any], dict[str, Any]]:
        since = _now()
        response = self._stream(
            "/v1/chat/completions",
            self._chat(max_tokens=64, stream=True),
            timeout=300, abort_after_first=True,
        )
        request_id = _request_id(response.headers)
        detail = self._wait_detail(
            request_id,
            lambda item: (_record_fields(item) or {}).get("final_outcome") == "client_disconnected",
            timeout=30,
        )
        record = _record_fields(detail)
        status_response, status = self._get_json("/api/status")
        concurrency = status.get("concurrency") if status and isinstance(status.get("concurrency"), dict) else {}
        received, aborted = self._wait_engine_log_matches(
            since,
            request_id,
            route="/v1/chat/completions",
            require_abort=True,
            timeout=30,
        )
        facts: dict[str, Any] = {
            "http_200": response.status == 200,
            "client_disconnected": None if record is None else record["final_outcome"] == "client_disconnected",
            "slot_released": status_response.status == 200 and concurrency.get("in_flight") == 0,
            "engine_received": received,
            "engine_aborted": aborted,
        }
        if received is not True or aborted is not True:
            facts = {"unknown_reason": "취소 요청 ID의 vLLM 수신·abort 로그 형식을 확인하지 못함", **facts}
        return facts, {
            "record_fields": record,
            "engine_evidence": {
                "state": "observed" if received and aborted else "unknown",
                "received_match": received,
                "abort_match": aborted,
                "raw_logs_stored": False,
            },
        }

    def cpu_04(self) -> tuple[dict[str, Any], dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        records: list[dict[str, Any] | None] = []
        for label, token in (("missing", None), ("invalid", self.wrong_token)):
            response = self._request(
                "POST", "/v1/chat/completions", token=token,
                payload=self._chat(), timeout=30,
            )
            request_id = _request_id(response.headers)
            record = _record_fields(self._detail(request_id))
            records.append(record)
            attempts.append({
                "kind": label, "status": response.status,
                "error_code": _error_code(response), "request_id": request_id,
                "record_fields": record,
            })
        expected = {"missing": "AUTH_REQUIRED", "invalid": "AUTH_INVALID"}
        complete = all(record is not None for record in records)
        return {
            "both_401": all(item["status"] == 401 for item in attempts),
            "error_codes": all(item["error_code"] == expected[item["kind"]] for item in attempts),
            "records_denied": (all(record["result"] == "DENIED" for record in records if record) if complete else None),
            "not_delivered": (all(record["delivery"]["attempted"] is False for record in records if record) if complete else None),
        }, {"attempts": attempts, "record_fields": records}

    def cpu_05(self) -> tuple[dict[str, Any], dict[str, Any]]:
        policy_response, policy = self._get_json("/api/policy")
        current = policy.get("current") if policy and isinstance(policy.get("current"), dict) else {}
        limit = current.get("output_max_tokens")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            return {"unknown_reason": "GET /api/policy에서 output_max_tokens를 읽지 못함"}, {}
        response = self._request(
            "POST", "/v1/chat/completions", token=self.inference_token,
            payload=self._chat(max_tokens=limit + 1), timeout=30,
        )
        record = _record_fields(self._detail(_request_id(response.headers)))
        return {
            "limit_read": policy_response.status == 200,
            "http_403": response.status == 403,
            "reason_code": _error_code(response) == "MAX_TOKENS_EXCEEDED" and record is not None and record["reason_code"] == "MAX_TOKENS_EXCEEDED",
            "not_delivered": None if record is None else record["delivery"]["attempted"] is False,
        }, {"policy_output_max_tokens": limit, "record_fields": record}

    def cpu_06(self) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = self._chat(max_tokens=1)
        payload["messages"] = [{"role": "user", "content": "token " * 3000}]
        response = self._request(
            "POST", "/v1/chat/completions", token=self.inference_token,
            payload=payload, timeout=300,
        )
        record = _record_fields(self._detail(_request_id(response.headers)))
        context = record.get("context") if record else None
        return {
            "http_403": response.status == 403,
            "reason_code": _error_code(response) == "CONTEXT_LIMIT_EXCEEDED" and record is not None and record["reason_code"] == "CONTEXT_LIMIT_EXCEEDED",
            "not_delivered": None if record is None else record["delivery"]["attempted"] is False,
            "input_count_recorded": isinstance(context, dict) and isinstance(context.get("input_tokens"), int),
        }, {
            "record_fields": record,
            "input_token_count_source": "GET /api/requests/<request_id> gateway final record request.context.input_tokens",
        }

    def cpu_07(self) -> tuple[dict[str, Any], dict[str, Any]]:
        response = self._request(
            "POST", "/v1/chat/completions", token=self.inference_token,
            payload=self._chat(max_tokens=1), timeout=300,
        )
        record = _record_fields(self._detail(_request_id(response.headers)))
        context = record.get("context") if record else None
        return {
            "http_200": response.status == 200,
            "record_allowed": None if record is None else record["result"] == "ALLOWED",
            "input_count_recorded": isinstance(context, dict) and isinstance(context.get("input_tokens"), int),
            "within_limit_recorded": isinstance(context, dict) and context.get("result") == "within_limit",
        }, {
            "record_fields": record,
            "input_token_count_source": "GET /api/requests/<request_id> gateway final record request.context.input_tokens",
        }

    def cpu_08(self) -> tuple[dict[str, Any], dict[str, Any]]:
        response = self._request(
            "POST", "/v1/chat/completions", token=self.inference_token,
            payload=self._chat(suffix="not-allowed"), timeout=30,
        )
        record = _record_fields(self._detail(_request_id(response.headers)))
        return {
            "http_400": response.status == 400,
            "invalid_field_code": _error_code(response) == "UNSUPPORTED_FIELD",
            "not_delivered": None if record is None else record["delivery"]["attempted"] is False,
        }, {"record_fields": record, "field_name": "suffix"}

    def cpu_09(self) -> tuple[dict[str, Any], dict[str, Any]]:
        since = _now()
        response = self._request(
            "POST", "/v1/completions", token=self.inference_token,
            payload=self._completion(), timeout=300,
        )
        request_id = _request_id(response.headers)
        record = _record_fields(self._detail(request_id))
        log_match, _ = self._wait_engine_log_matches(
            since, request_id, route="/v1/completions"
        )
        facts: dict[str, Any] = {
            "http_200": response.status == 200,
            "valid_response": self._valid_completion(response),
            "record_allowed": None if record is None else record["result"] == "ALLOWED",
            "engine_log_match": log_match,
        }
        if log_match is not True:
            facts = {
                "unknown_reason": "completions 요청 ID가 포함된 vLLM 수신 로그 형식을 확인하지 못함",
                **facts,
            }
        return facts, {
            "record_fields": record,
            "engine_evidence": {
                "state": "observed" if log_match else "unknown",
                "match_format": "Received request cmpl-<gateway request id>-0",
                "raw_logs_stored": False,
            },
        }

    def cpu_10(self) -> tuple[dict[str, Any], dict[str, Any]]:
        since = _now()
        response = self._stream("/v1/completions", self._completion(stream=True), timeout=300)
        request_id = _request_id(response.headers)
        record = _record_fields(self._detail(request_id))
        log_match, _ = self._wait_engine_log_matches(
            since, request_id, route="/v1/completions"
        )
        facts: dict[str, Any] = {
            "http_200": response.status == 200,
            "done": response.done,
            "record_completed": None if record is None else record["final_outcome"] == "completed",
            "engine_log_match": log_match,
        }
        if log_match is not True:
            facts = {
                "unknown_reason": "completions 요청 ID가 포함된 vLLM 수신 로그 형식을 확인하지 못함",
                **facts,
            }
        return facts, {
            "stream": {"event_count": response.event_count, "done": response.done},
            "record_fields": record,
            "engine_evidence": {
                "state": "observed" if log_match else "unknown",
                "match_format": "Received request cmpl-<gateway request id>-0",
                "raw_logs_stored": False,
            },
        }

    def cpu_11(self) -> tuple[dict[str, Any], dict[str, Any]]:
        response = self._request(
            "POST", "/v1/completions", token=self.inference_token,
            payload=self._completion(prompt=["first", "second"]), timeout=30,
        )
        record = _record_fields(self._detail(_request_id(response.headers)))
        return {
            "http_400": response.status == 400,
            "invalid_prompt_type": _error_code(response) == "INVALID_PROMPT_TYPE" and record is not None and record["reason_code"] == "INVALID_PROMPT_TYPE",
            "not_delivered": None if record is None else record["delivery"]["attempted"] is False,
        }, {"record_fields": record}

    def cpu_12(self) -> tuple[dict[str, Any], dict[str, Any]]:
        response, target = self._get_json("/api/target")
        registration = target.get("registration") if target and isinstance(target.get("registration"), dict) else {}
        hold = target_hold_reason(target)
        initial_hold = target_hold_reason(self.initial_target)
        target_error_codes = {"TARGET_MISMATCH", "TARGET_UNVERIFIED", "TARGET_UNAVAILABLE"}
        reported_hold = hold
        if reported_hold is None and initial_hold in target_error_codes:
            reported_hold = initial_hold
        if reported_hold is None and self.normal_error_code in target_error_codes:
            reported_hold = self.normal_error_code
        return {
            "registration_ok": response.status == 200 and registration.get("state") == "ok",
            "no_gateway_hold": hold is None,
            "normal_request_not_held": self.normal_status == 200 and self.normal_error_code not in target_error_codes,
        }, {
            "request_ids": [self.normal_request_id] if self.normal_request_id else [],
            "record_fields": _record_fields(self.normal_detail),
            "gateway_hold_reason": reported_hold,
            "current_gateway_hold_reason": hold,
            "initial_gateway_hold_reason": initial_hold,
            "normal_request_id": self.normal_request_id,
            "normal_http_status": self.normal_status,
            "registration": {
                "state": registration.get("state"),
                "target_id": registration.get("target_id"),
                "engine": registration.get("engine"),
                "model": registration.get("model"),
            },
        }

    def cpu_13(self) -> tuple[dict[str, Any], dict[str, Any]]:
        response, status = self._get_json("/api/status")
        bundle = status.get("policy_bundle") if status and isinstance(status.get("policy_bundle"), dict) else None
        files = bundle.get("files") if bundle and isinstance(bundle.get("files"), list) else []
        roles = {item.get("role") for item in files if isinstance(item, dict)}
        applied = all(item.get("applied") is True for item in files if isinstance(item, dict))
        required = {"gateway_policy", "policy_approvals", "target"}
        return {
            "bundle_present": response.status == 200 and bundle is not None,
            "required_roles_present": required <= roles,
            "all_files_applied": bool(files) and applied,
        }, {
            "policy_bundle": ({
                "bundle_id": bundle.get("bundle_id"),
                "sha256": bundle.get("sha256"),
                "files": [
                    {"role": item.get("role"), "sha256": item.get("sha256"), "applied": item.get("applied")}
                    for item in files if isinstance(item, dict)
                ],
            } if bundle else None),
        }

    def cpu_14(self) -> tuple[dict[str, Any], dict[str, Any]]:
        requests_response, requests = self._get_json("/api/requests?limit=1000")
        detail_response, detail = self._get_json(f"/api/requests/{self.normal_request_id}")
        status_response, _status = self._get_json("/api/status")
        collectors_response, collectors = self._get_json("/api/collectors")
        items = collectors.get("collectors") if collectors and isinstance(collectors.get("collectors"), list) else []
        engine_items = [item for item in items if isinstance(item, dict) and str(item.get("source", "")).startswith("engine_")]
        request_items = requests.get("requests") if requests and isinstance(requests.get("requests"), list) else []
        return {
            "requests_ok": requests_response.status == 200 and any(isinstance(item, dict) and item.get("request_id") == self.normal_request_id for item in request_items),
            "detail_ok": detail_response.status == 200 and isinstance(detail, dict),
            "status_ok": status_response.status == 200,
            "engine_collectors_ok": collectors_response.status == 200 and bool(engine_items) and all(item.get("state") == "ok" for item in engine_items),
        }, {
            "request_ids": [self.normal_request_id] if self.normal_request_id else [],
            "record_fields": _record_fields(detail),
            "engine_collectors": [
                {"source": item.get("source"), "state": item.get("state"), "data_origin": item.get("data_origin"), "last_success_at": item.get("last_success_at")}
                for item in engine_items
            ],
        }

    def cpu_15(self) -> tuple[dict[str, Any], dict[str, Any]]:
        policy_response, policy = self._get_json("/api/policy")
        current = policy.get("current") if policy and isinstance(policy.get("current"), dict) else {}
        extra_fields = current.get("extra_fields") if isinstance(current.get("extra_fields"), list) else []
        allowed = any(isinstance(item, dict) and item.get("name") == "logprobs" for item in extra_fields)
        if policy_response.status != 200 or not allowed:
            return {"not_run_reason": "현재 정책이 logprobs 필드를 허용하지 않음"}, {
                "policy_allows_logprobs": allowed,
            }
        since = _now()
        response: HTTPResponse | None = None
        execution_error: str | None = None
        try:
            response = self._request(
                "POST", "/v1/chat/completions", token=self.inference_token,
                payload=self._chat(logprobs=True), timeout=self.args.logprobs_timeout,
            )
        except Exception as error:
            execution_error = type(error).__name__
        healthy = self._vllm_health(timeout=10)
        request_id = _request_id(response.headers) if response is not None else None
        log_match, _ = (
            self._wait_engine_log_matches(
                since, request_id, route="/v1/chat/completions"
            )
            if request_id
            else (None, None)
        )
        if response is None:
            return {"unknown_reason": f"logprobs 요청 완료 전 종료({execution_error})", "engine_healthy_after": healthy}, {
                "hard_timeout_s": self.args.logprobs_timeout,
                "engine_health_after": healthy,
                "engine_evidence": {"state": "unknown", "raw_logs_stored": False},
            }
        record = _record_fields(self._detail(request_id))
        facts = {
            "policy_allows_logprobs": True,
            "http_200": response.status == 200,
            "valid_response": self._valid_chat(response),
            "engine_log_match": log_match,
            "engine_healthy_after": healthy,
        }
        if log_match is not True:
            facts = {"unknown_reason": "요청 ID가 포함된 vLLM 수신 로그 형식을 확인하지 못함", **facts}
        return facts, {
            "hard_timeout_s": self.args.logprobs_timeout,
            "record_fields": record,
            "engine_health_after": healthy,
            "engine_evidence": {"state": "observed" if log_match else "unknown", "raw_logs_stored": False},
        }

    def _add_logprobs_not_run(self) -> None:
        self._case(
            "CPU-15", _now(), len(self.http_events), len(self.runner.history),
            {"not_run_reason": "--include-logprobs를 지정하지 않음"},
            {"opt_in": False},
        )

    def write_report(self) -> tuple[Path, Path]:
        requested = requested_case_ids(self.args.include_logprobs)
        report = {
            "schema": "runtime-cpu-vllm-check/1.0",
            "run_id": self.run_dir.name,
            "started_at": self.started_at,
            "ended_at": _now(),
            "gateway_url": self.gateway,
            "vllm_url": self.vllm,
            "docker_container": self.args.docker_container,
            "requested_cases": list(requested),
            "include_logprobs": self.args.include_logprobs,
            "overall": _overall(self.cases, requested),
            "result_counts": {result: sum(case["result"] == result for case in self.cases) for result in RESULTS},
            "data_minimization": {
                "tokens_stored": False,
                "prompts_stored": False,
                "responses_stored": False,
                "raw_engine_logs_stored": False,
            },
            "cases": self.cases,
        }
        safe_report = _redact(report, self.tokens.values())
        raw = json.dumps(safe_report, ensure_ascii=False, indent=2) + "\n"
        if any(secret in raw for secret in self.tokens.values()):
            raise RuntimeError("secret redaction verification failed")
        json_path = self.run_dir / "report.json"
        json_path.write_text(raw, encoding="utf-8")
        os.chmod(json_path, 0o600)

        lines = [
            "# CPU vLLM 로컬 API 점검", "",
            f"- 실행 ID: `{safe_report['run_id']}`",
            f"- 전체 결과: **{safe_report['overall']}**",
            f"- Gateway: `{self.gateway}`",
            f"- vLLM: `{self.vllm}`",
            "- 토큰·프롬프트·응답 본문·원시 엔진 로그: 저장하지 않음", "",
            "| ID | 사례 | 결과 | 이유 |", "| --- | --- | --- | --- |",
        ]
        for case in safe_report["cases"]:
            lines.append(f"| {case['case_id']} | {case['title']} | {case['result']} | {case['reason']} |")
        lines.extend(["", "## 사례별 최소 증거", ""])
        for case in safe_report["cases"]:
            evidence = case["evidence"]
            lines.extend([
                f"### {case['case_id']} {case['title']}", "",
                f"- 결과: `{case['result']}` — {case['reason']}",
                f"- request IDs: `{json.dumps(evidence.get('request_ids', []), ensure_ascii=False)}`",
                f"- HTTP codes: `{json.dumps(evidence.get('http_codes', []), ensure_ascii=False)}`",
                f"- record fields: `{json.dumps(evidence.get('record_fields'), ensure_ascii=False, sort_keys=True)}`",
                "",
            ])
        markdown_path = self.run_dir / "report.md"
        markdown_path.write_text("\n".join(lines), encoding="utf-8")
        os.chmod(markdown_path, 0o600)
        return json_path, markdown_path

    def run(self) -> int:
        # Give the collectors a short startup window before the key normal request.
        self.initial_target = self._wait_target_ready(timeout=30)
        for case_id, function in (
            ("CPU-01", self.cpu_01), ("CPU-02", self.cpu_02),
            ("CPU-03", self.cpu_03), ("CPU-04", self.cpu_04),
            ("CPU-05", self.cpu_05), ("CPU-06", self.cpu_06),
            ("CPU-07", self.cpu_07), ("CPU-08", self.cpu_08),
            ("CPU-09", self.cpu_09), ("CPU-10", self.cpu_10),
            ("CPU-11", self.cpu_11), ("CPU-12", self.cpu_12),
            ("CPU-13", self.cpu_13), ("CPU-14", self.cpu_14),
        ):
            self._run_case(case_id, function)
        if self.args.include_logprobs:
            self._run_case("CPU-15", self.cpu_15)
        else:
            self._add_logprobs_not_run()
        json_path, markdown_path = self.write_report()
        overall = _overall(self.cases, requested_case_ids(self.args.include_logprobs))
        print(f"overall: {overall}")
        print(f"JSON: {json_path}")
        print(f"Markdown: {markdown_path}")
        return 0 if overall == "passed" else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="running Gateway를 실제 CPU vLLM에 대해 일회 점검",
    )
    parser.add_argument("--gateway-url", default="http://127.0.0.1:18180")
    parser.add_argument("--token-env-file", required=True)
    parser.add_argument("--vllm-url", default="http://127.0.0.1:8000")
    parser.add_argument("--docker-container", default="byul-vllm-cpu-031")
    parser.add_argument(
        "--evidence-dir",
        default=str(Path.home() / ".local/state/serving-runtime/cpu-vllm/evidence"),
    )
    parser.add_argument(
        "--include-logprobs", action="store_true",
        help="정책이 허용할 때만 CPU 정지 위험이 있는 logprobs 시험을 포함",
    )
    parser.add_argument("--logprobs-timeout", type=float, default=45.0)
    args = parser.parse_args(argv)
    if not 1 <= args.logprobs_timeout <= 300:
        parser.error("--logprobs-timeout must be from 1 through 300 seconds")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return CPUVLLMCheck(parse_args(argv)).run()
    except ConfigurationError as error:
        print(f"설정 오류: {error}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
