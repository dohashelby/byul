"""AWS GPU lab acceptance checks for the already-installed runtime stack.

This tool is deliberately separate from ``k8s_lab_check.py``.  It never
applies or deletes Kubernetes policy objects.  The only disruptive actions
are an explicitly enabled collector service stop/start and engine Deployment
rollout restart.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from ipaddress import ip_address
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from serving_runtime.mvp.backend import is_valid_credential_value
from serving_runtime.mvp.kube_context import validate_kube_context
from serving_runtime.mvp.observation_feed import HEARTBEAT_SCHEMA


CASE_IDS = tuple(f"AWS-{number:02d}" for number in range(1, 9))
CASE_TITLES = {
    "AWS-01": "정상",
    "AWS-02": "인증 거부",
    "AWS-03": "토큰 상한",
    "AWS-04": "직통 거부",
    "AWS-05": "금지 송신",
    "AWS-06": "RO 쓰기 거부",
    "AWS-07": "수집 중단",
    "AWS-08": "Pod 교체",
}
CASE_RT_IDS = {
    "AWS-01": ["RT-API-01", "RT-ENV-02"],
    "AWS-02": ["RT-API-02", "RT-OPT-04"],
    "AWS-03": ["RT-OPT-01", "RT-OPT-04"],
    "AWS-04": ["RT-API-03", "RT-NET-02", "RT-TASKNET-02"],
    "AWS-05": ["RT-NET-02", "RT-TASKNET-01"],
    "AWS-06": ["RT-SYS-03", "RT-ASSET-02", "RT-ASSET-05"],
    "AWS-07": ["RT-OBS-01", "RT-SYS-06"],
    "AWS-08": ["RT-NET-04", "RT-PHASE-01"],
}
RESULTS = frozenset({"passed", "failed", "not_run", "unknown"})
_NAMESPACE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,256}")
_OBSERVATION_SOURCES = ("observation_feed_hubble", "observation_feed_tetragon")
_EXTERNAL_TEST_IP = "192.0.2.1"
_IMDS_IP = "169.254.169.254"
_MODEL = "test-model"
TCP_PROBE_CODE = (
    "import errno,socket,sys; s=socket.socket(); s.settimeout(3); "
    "r=s.connect_ex((sys.argv[1],int(sys.argv[2]))); s.close(); "
    "name=errno.errorcode.get(r,'UNKNOWN'); "
    "print(('CONNECT_FAILED errno=%d name=%s' % (r,name)) if r else 'CONNECTED'); "
    "sys.exit(0 if r else 1)"
)
WRITE_DENY_CODE = (
    "import errno,os,sys; p=sys.argv[1]; "
    "\ntry:\n f=open(p,'xb'); f.close()\n"
    "except OSError as e:\n name=errno.errorcode.get(e.errno,'UNKNOWN'); marker='WRITE_DENIED' if e.errno in (errno.EPERM,errno.EACCES,errno.EROFS) else 'WRITE_ERROR'; print('%s errno=%d name=%s' % (marker,e.errno,name)); sys.exit(0 if marker == 'WRITE_DENIED' else 2)\n"
    "else:\n os.unlink(p); print('WRITE_SUCCEEDED'); sys.exit(1)"
)
CACHE_WRITE_CODE = (
    "import os,sys; p=sys.argv[1]; "
    "f=open(p,'xb'); f.write(b'x'); f.close(); os.unlink(p); print('CACHE_WRITE_REMOVED')"
)


class ConfigurationError(ValueError):
    """A configuration error whose message does not contain secret values."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class Runner:
    """Subprocess adapter with a secret-free command journal."""

    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []

    def run(self, argv: Sequence[str], *, timeout: float = 30) -> CommandResult:
        started = _now()
        try:
            completed = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
            result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
        except subprocess.TimeoutExpired:
            result = CommandResult(124)
        self.history.append(
            {
                "argv": list(argv),
                "started_at": started,
                "ended_at": _now(),
                "returncode": result.returncode,
            }
        )
        return result


class HTTPClient:
    """Small urllib adapter. Headers and bodies are never journaled."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 30,
    ) -> HTTPResponse:
        request = Request(url, data=body, headers=dict(headers or {}), method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return HTTPResponse(
                    response.status, dict(response.headers), response.read()
                )
        except HTTPError as error:
            with error:
                return HTTPResponse(error.code, dict(error.headers), error.read())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().replace("Z", "+00:00")
    # Hubble commonly emits nanoseconds; datetime accepts microseconds.
    match = re.match(r"^(.*\.)(\d{6})\d+(\+00:00)$", candidate)
    if match is not None:
        candidate = "".join(match.groups())
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _after(value: object, since: str) -> bool:
    observed = _parse_time(value)
    boundary = _parse_time(since)
    return observed is not None and boundary is not None and observed >= boundary


def _json_object(raw: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _safe_exception(error: BaseException) -> str:
    return type(error).__name__


def _record_fields(detail: object) -> dict[str, Any] | None:
    if not isinstance(detail, dict):
        return None
    final = detail.get("final_state")
    if not isinstance(final, dict):
        return None
    delivery = final.get("delivery")
    if not isinstance(delivery, dict):
        delivery = {}
    return {
        "result": final.get("result"),
        "reason_code": final.get("reason_code"),
        "http_status": final.get("http_status"),
        "final_outcome": final.get("final_outcome"),
        "delivery": {
            "attempted": delivery.get("attempted"),
            "backend_receipt": delivery.get("backend_receipt"),
            "response": delivery.get("response"),
            "error_code": delivery.get("error_code"),
        },
    }


def _extract_policy_limit(document: object) -> int | None:
    """Read the enforced output limit exposed by GET /api/policy."""

    if not isinstance(document, dict):
        return None
    for name in ("current", "policy"):
        value = document.get(name)
        if not isinstance(value, dict):
            continue
        limit = value.get("output_max_tokens")
        if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
            return limit
    return None


def _target_snapshot_stale(document: object) -> bool:
    if not isinstance(document, dict):
        return False
    kubernetes = document.get("kubernetes")
    if not isinstance(kubernetes, dict):
        return False
    return "stale" in " ".join(
        str(kubernetes.get(name, "")) for name in ("state", "error")
    ).lower()


def _overall(cases: Iterable[Mapping[str, Any]]) -> str:
    results = [case.get("result") for case in cases]
    if results and all(result == "passed" for result in results):
        return "passed"
    if any(result == "failed" for result in results):
        return "failed"
    return "unknown"


def evaluate_case(case_id: str, facts: Mapping[str, Any]) -> tuple[str, str]:
    """Pure result rules used by live execution and unit tests."""

    if case_id not in CASE_IDS:
        raise ValueError("unknown AWS acceptance case")
    if facts.get("not_run_reason"):
        return "not_run", str(facts["not_run_reason"])
    if facts.get("unknown_reason"):
        return "unknown", str(facts["unknown_reason"])
    if case_id == "AWS-05" and facts.get("receiver_control_connected") is False:
        return "unknown", "대조 실패: receiver에 닿지 않음"

    checks: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "AWS-01": (
            ("http_200", "valid_completion", "record_allowed", "delivery_attempted", "engine_log_match", "stream_ok"),
            ("http_200", "valid_completion", "record_allowed", "delivery_attempted", "stream_ok"),
        ),
        "AWS-02": (
            ("auth_codes", "records_denied", "not_delivered", "engine_log_absent", "engine_log_format_confirmed"),
            ("auth_codes", "records_denied", "not_delivered", "engine_log_absent"),
        ),
        "AWS-03": (
            (
                "policy_limit_read",
                "boundary_allowed",
                "http_403",
                "reason_code",
                "not_delivered",
                "engine_log_absent",
                "engine_log_format_confirmed",
            ),
            ("boundary_allowed", "http_403", "reason_code", "not_delivered", "engine_log_absent"),
        ),
        "AWS-04": (
            ("direct_failed", "gateway_tcp_control", "gateway_control_200", "hubble_dropped"),
            ("direct_failed", "gateway_control_200"),
        ),
        "AWS-05": (
            ("engine_receiver_failed", "receiver_control_connected", "receiver_hubble_dropped"),
            ("engine_receiver_failed", "receiver_control_connected"),
        ),
        "AWS-06": (
            ("model_write_denied", "cache_write_succeeded", "cache_harmless_removed", "cache_tetragon"),
            ("model_write_denied", "cache_write_succeeded", "cache_harmless_removed"),
        ),
        "AWS-07": (
            ("stop_ok", "heartbeat_stopped_seen", "collectors_unavailable", "start_ok", "collectors_recovered", "inference_recovered"),
            ("stop_ok", "heartbeat_stopped_seen", "collectors_unavailable", "start_ok", "collectors_recovered", "inference_recovered"),
        ),
        "AWS-08": (
            (
                "restart_ok",
                "pod_changed",
                "gateway_recovered",
                "engine_info_recovered",
                "gateway_instance_changed",
            ),
            (
                "restart_ok",
                "pod_changed",
                "gateway_recovered",
                "engine_info_recovered",
                "gateway_instance_changed",
            ),
        ),
    }
    required, decisive = checks[case_id]
    false_decisive = [name for name in decisive if facts.get(name) is False]
    if false_decisive:
        return "failed", "기대 결과 불일치: " + ", ".join(false_decisive)
    missing_decisive = [name for name in decisive if facts.get(name) is not True]
    if missing_decisive:
        return "unknown", "판정 근거 부족: " + ", ".join(missing_decisive)
    false_required = [name for name in required if facts.get(name) is False]
    if false_required:
        # A missing expected observation is unknown; an explicitly observed
        # contradictory action remains a failure through the decisive set.
        return "unknown", "필수 관측 미확인: " + ", ".join(false_required)
    missing_required = [name for name in required if facts.get(name) is not True]
    if missing_required:
        return "unknown", "판정 근거 부족: " + ", ".join(missing_required)
    return "passed", "모든 필수 결과와 연결 증거를 확인함"


def _parse_cases(values: Sequence[str] | None) -> tuple[str, ...]:
    if not values:
        return CASE_IDS
    requested: list[str] = []
    for value in values:
        requested.extend(part.strip().upper() for part in value.split(",") if part.strip())
    unknown = sorted(set(requested) - set(CASE_IDS))
    if unknown:
        raise argparse.ArgumentTypeError("unknown case(s): " + ", ".join(unknown))
    return tuple(case_id for case_id in CASE_IDS if case_id in set(requested))


def _read_tokens(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ConfigurationError("token env file could not be read") from error
    values: dict[str, str] = {}
    for line in lines:
        if not line or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            raise ConfigurationError("token env file contains a non-KEY=VALUE line")
        key, value = line.split("=", 1)
        if key not in {"RUNTIME_INFERENCE_TOKEN", "RUNTIME_VIEWER_TOKEN"}:
            raise ConfigurationError("token env file contains an unsupported key")
        if key in values:
            raise ConfigurationError("token env file contains a duplicate key")
        if not is_valid_credential_value(value):
            raise ConfigurationError("token env file contains an invalid token value")
        values[key] = value
    missing = sorted({"RUNTIME_INFERENCE_TOKEN", "RUNTIME_VIEWER_TOKEN"} - set(values))
    if missing:
        raise ConfigurationError("token env file is missing required keys")
    return values


def _redact(value: Any, secrets: Iterable[str]) -> Any:
    secret_values = tuple(secret for secret in secrets if secret)
    if isinstance(value, str):
        redacted = value
        for secret in secret_values:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    if isinstance(value, list):
        return [_redact(item, secret_values) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, secret_values) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact(item, secret_values) for key, item in value.items()}
    return value


class AWSAcceptance:
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
        try:
            self.context = validate_kube_context(args.kube_context)
        except ValueError as error:
            raise ConfigurationError(str(error)) from error
        if _NAMESPACE.fullmatch(args.namespace) is None:
            raise ConfigurationError("namespace must be one DNS label")
        self.namespace = args.namespace
        parsed = urlsplit(args.gateway_url)
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ConfigurationError("gateway URL must be an http origin without credentials")
        try:
            port = parsed.port or 80
        except ValueError as error:
            raise ConfigurationError("gateway URL has an invalid port") from error
        self.gateway = f"http://{parsed.hostname}:{port}"
        self.tokens = _read_tokens(Path(args.token_env_file).expanduser())
        self.wrong_token = "aws-acceptance-invalid-" + uuid4().hex
        while self.wrong_token in self.tokens.values():
            self.wrong_token = "aws-acceptance-invalid-" + uuid4().hex
        self.observation_dir = Path(args.observation_dir).expanduser()
        self.runner = runner or Runner()
        self.http = http or HTTPClient()
        self.sleep = sleeper
        self.monotonic = monotonic
        self.http_events: list[dict[str, Any]] = []
        self.cases: list[dict[str, Any]] = []
        self.engine_log_format_confirmed = False
        self.run_started_at = _now()
        base = Path(args.evidence_dir).expanduser()
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.evidence_dir = base / f"aws-acceptance-{stamp}-{os.getpid()}"
        self.evidence_dir.mkdir(mode=0o700)
        os.chmod(self.evidence_dir, 0o700)

    def _kubectl(self, *args: str, timeout: float = 30) -> CommandResult:
        return self.runner.run(
            ["kubectl", "--context", self.context, "-n", self.namespace, *args],
            timeout=timeout,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None,
        payload: Mapping[str, Any] | None = None,
        timeout: float = 30,
    ) -> HTTPResponse:
        headers: dict[str, str] = {}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            response = self.http.request(
                method,
                self.gateway + path,
                headers=headers,
                body=body,
                timeout=timeout,
            )
        except (OSError, URLError, TimeoutError) as error:
            self.http_events.append(
                {"method": method, "path": path, "status": None, "error": _safe_exception(error)}
            )
            raise
        request_id = response.headers.get("X-Request-ID") or response.headers.get("x-request-id")
        self.http_events.append(
            {"method": method, "path": path, "status": response.status, "request_id": request_id}
        )
        return response

    def _get_json(
        self, path: str, *, timeout: float = 30
    ) -> tuple[HTTPResponse, dict[str, Any] | None]:
        response = self._request(
            "GET", path, token=self.tokens["RUNTIME_VIEWER_TOKEN"], timeout=timeout
        )
        return response, _json_object(response.body)

    def _completion(
        self, *, token: str | None, max_tokens: int = 8, timeout: float = 300
    ) -> tuple[HTTPResponse, str | None, bool]:
        response = self._request(
            "POST",
            "/v1/chat/completions",
            token=token,
            payload={
                "model": _MODEL,
                "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
                "max_tokens": max_tokens,
            },
            timeout=timeout,
        )
        request_id = response.headers.get("X-Request-ID") or response.headers.get("x-request-id")
        valid = False
        if response.status == 200:
            document = _json_object(response.body)
            choices = document.get("choices") if document else None
            if isinstance(choices, list) and choices:
                first = choices[0]
                message = first.get("message") if isinstance(first, dict) else None
                content = message.get("content") if isinstance(message, dict) else None
                valid = isinstance(content, str) and bool(content.strip())
        return response, request_id if isinstance(request_id, str) else None, valid

    def _stream_completion(
        self, *, token: str
    ) -> tuple[HTTPResponse, str | None, bool, bool]:
        response = self._request(
            "POST",
            "/v1/chat/completions",
            token=token,
            payload={
                "model": _MODEL,
                "messages": [{"role": "user", "content": "Say hello in one short sentence."}],
                "max_tokens": 8,
                "stream": True,
            },
            timeout=300,
        )
        request_id = response.headers.get("X-Request-ID") or response.headers.get("x-request-id")
        data_seen = False
        done_seen = False
        if response.status == 200:
            for raw_line in response.body.splitlines():
                line = raw_line.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    done_seen = True
                    continue
                if _json_object(data) is not None:
                    data_seen = True
        return (
            response,
            request_id if isinstance(request_id, str) else None,
            data_seen,
            done_seen,
        )

    def _detail(
        self, request_id: str | None, *, timeout: float = 30
    ) -> dict[str, Any] | None:
        if request_id is None or _REQUEST_ID.fullmatch(request_id) is None:
            return None
        response, detail = self._get_json(f"/api/requests/{request_id}", timeout=timeout)
        return detail if response.status == 200 else None

    def _engine_logs(self, since: str) -> tuple[CommandResult, str]:
        result = self._kubectl(
            "logs",
            "deployment/serving-engine",
            "-c",
            "engine",
            f"--since-time={since}",
            timeout=60,
        )
        return result, result.stdout + result.stderr

    @staticmethod
    def _log_received(logs: str, request_id: str | None, route: str) -> bool:
        if request_id is None:
            return False
        if route == "/v1/chat/completions":
            engine_id = f"chatcmpl-{request_id}"
        elif route == "/v1/completions":
            engine_id = f"cmpl-{request_id}-0"
        else:
            return False
        return f"Received request {engine_id}" in logs

    def _engine_pod(self) -> tuple[dict[str, Any] | None, CommandResult]:
        result = self._kubectl("get", "pods", "-l", "app=serving-engine", "-o", "json")
        document = _json_object(result.stdout.encode("utf-8")) if result.returncode == 0 else None
        items = document.get("items") if document else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            return None, result
        pod = items[0]
        metadata = pod.get("metadata") if isinstance(pod.get("metadata"), dict) else {}
        status = pod.get("status") if isinstance(pod.get("status"), dict) else {}
        container_statuses = status.get("containerStatuses")
        engine = next(
            (
                item
                for item in container_statuses or []
                if isinstance(item, dict) and item.get("name") == "engine"
            ),
            None,
        )
        conditions = status.get("conditions") or []
        ready = any(
            isinstance(item, dict)
            and item.get("type") == "Ready"
            and item.get("status") == "True"
            for item in conditions
        )
        return {
            "name": metadata.get("name"),
            "uid": metadata.get("uid"),
            "container_id": engine.get("containerID") if isinstance(engine, dict) else None,
            "ready": ready,
        }, result

    def _test_receiver_pod(self) -> tuple[dict[str, Any] | None, CommandResult]:
        result = self._kubectl("get", "pod", "test-receiver", "-o", "json")
        document = _json_object(result.stdout.encode("utf-8")) if result.returncode == 0 else None
        if document is None:
            return None, result
        metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
        labels = metadata.get("labels") if isinstance(metadata.get("labels"), dict) else {}
        status = document.get("status") if isinstance(document.get("status"), dict) else {}
        conditions = status.get("conditions") or []
        ready = any(
            isinstance(item, dict)
            and item.get("type") == "Ready"
            and item.get("status") == "True"
            for item in conditions
        )
        pod_ip = status.get("podIP")
        try:
            parsed_ip = ip_address(pod_ip) if isinstance(pod_ip, str) else None
        except ValueError:
            parsed_ip = None
        if (
            metadata.get("name") != "test-receiver"
            or labels.get("app") != "test-receiver"
            or not ready
            or parsed_ip is None
        ):
            return None, result
        return {
            "name": metadata["name"],
            "ip": str(parsed_ip),
            "ready": ready,
        }, result

    def _tcp_probe(self, pod: str, container: str, host: str, port: int) -> dict[str, Any]:
        result = self._kubectl(
            "exec", pod, "-c", container, "--", "python3", "-c", TCP_PROBE_CODE, host, str(port), timeout=15
        )
        marker = "CONNECT_FAILED" if "CONNECT_FAILED" in result.stdout else (
            "CONNECTED" if "CONNECTED" in result.stdout else None
        )
        errno_match = re.search(r"CONNECT_FAILED errno=(\d+) name=([A-Z0-9_]+)", result.stdout)
        error_number = int(errno_match.group(1)) if errno_match else None
        error_name = errno_match.group(2) if errno_match else None
        timed_out = error_name in {"EAGAIN", "EWOULDBLOCK", "ETIMEDOUT"}
        missing_python = result.returncode != 0 and (
            "not found" in result.stderr.lower() or "executable file" in result.stderr.lower()
        )
        return {
            "returncode": result.returncode,
            "marker": marker,
            "errno": error_number,
            "errno_name": error_name,
            "python_available": not missing_python,
            "connection_failed": result.returncode == 0 and marker == "CONNECT_FAILED",
            "connection_timed_out": result.returncode == 0 and marker == "CONNECT_FAILED" and timed_out,
            "connected": result.returncode == 1 and marker == "CONNECTED",
        }

    def _retry_inference(
        self, timeout: float
    ) -> tuple[bool, dict[str, Any] | None, dict[str, Any]]:
        started = self.monotonic()
        deadline = started + timeout
        attempts = 0
        last_record: dict[str, Any] | None = None
        last_status: int | None = None
        last_reason: str | None = None
        while True:
            if attempts and self.monotonic() >= deadline:
                return False, last_record, {
                    "attempts": attempts,
                    "elapsed_s": round(self.monotonic() - started, 3),
                    "window_s": timeout,
                    "last_status": last_status,
                    "last_reason_code": last_reason,
                }
            attempts += 1
            last_status = None
            try:
                request_timeout = min(30.0, max(0.1, deadline - self.monotonic()))
                response, request_id, valid = self._completion(
                    token=self.tokens["RUNTIME_INFERENCE_TOKEN"],
                    timeout=request_timeout,
                )
                last_status = response.status
                detail_timeout = min(30.0, max(0.1, deadline - self.monotonic()))
                last_record = _record_fields(
                    self._detail(request_id, timeout=detail_timeout)
                )
                response_document = _json_object(response.body)
                response_error = (
                    response_document.get("error")
                    if response_document and isinstance(response_document.get("error"), dict)
                    else {}
                )
                last_reason = (
                    last_record.get("reason_code")
                    if last_record is not None
                    else response_error.get("code")
                )
                recovered = (
                    response.status == 200
                    and valid
                    and last_record is not None
                    and last_record["result"] == "ALLOWED"
                    and last_record["delivery"]["attempted"] is True
                )
                if recovered:
                    return True, last_record, {
                        "attempts": attempts,
                        "elapsed_s": round(self.monotonic() - started, 3),
                        "window_s": timeout,
                        "last_status": last_status,
                        "last_reason_code": last_reason,
                    }
            except (OSError, URLError, TimeoutError) as error:
                last_reason = _safe_exception(error)
                last_record = None
            if self.monotonic() >= deadline:
                return False, last_record, {
                    "attempts": attempts,
                    "elapsed_s": round(self.monotonic() - started, 3),
                    "window_s": timeout,
                    "last_status": last_status,
                    "last_reason_code": last_reason,
                }
            self.sleep(min(2.0, max(0.0, deadline - self.monotonic())))

    @staticmethod
    def _normalized_raw(source: str, document: object) -> dict[str, Any] | None:
        if not isinstance(document, dict):
            return None
        if source == "hubble":
            flow = document.get("flow")
            if not isinstance(flow, dict):
                return None
            src = flow.get("source") if isinstance(flow.get("source"), dict) else {}
            dst = flow.get("destination") if isinstance(flow.get("destination"), dict) else {}
            ip = flow.get("IP") if isinstance(flow.get("IP"), dict) else {}
            l4 = flow.get("l4") if isinstance(flow.get("l4"), dict) else {}
            tcp = l4.get("TCP") if isinstance(l4.get("TCP"), dict) else {}
            return {
                "observed_at": flow.get("time"),
                "verdict": str(flow.get("verdict", "")).lower(),
                "src": {"namespace": src.get("namespace"), "pod": src.get("pod_name")},
                "dst": {
                    "namespace": dst.get("namespace"),
                    "pod": dst.get("pod_name"),
                    "ip": ip.get("destination"),
                    "port": tcp.get("destination_port"),
                },
            }
        event = document.get("process_kprobe")
        if not isinstance(event, dict):
            return None
        path = None
        for argument in event.get("args") or []:
            if not isinstance(argument, dict):
                continue
            file_arg = argument.get("file_arg")
            if isinstance(file_arg, dict) and isinstance(file_arg.get("path"), str):
                path = file_arg["path"]
        policy = event.get("policy_name")
        if not isinstance(policy, str):
            return None
        match = re.search(r"TG-[A-Z]+-\d+", policy.upper())
        return {
            "observed_at": document.get("time"),
            "rule_id": match.group(0) if match else None,
            "policy_name": policy,
            "path": path,
        }

    def _observation_refs(
        self,
        source: str,
        since: str,
        predicate: Callable[[Mapping[str, Any]], bool],
    ) -> tuple[bool, list[dict[str, Any]]]:
        path = self.observation_dir / f"{source}.jsonl"
        refs: list[dict[str, Any]] = []
        local_read = False
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                local_read = True
                for number, line in enumerate(handle, 1):
                    try:
                        document = json.loads(line)
                    except (json.JSONDecodeError, RecursionError):
                        continue
                    item = self._normalized_raw(source, document)
                    if item and _after(item.get("observed_at"), since) and predicate(item):
                        refs.append(
                            {
                                "reference": f"{path}:{number}",
                                "observed_at": item.get("observed_at"),
                                "verdict": item.get("verdict"),
                                "rule_id": item.get("rule_id"),
                            }
                        )
        except OSError:
            pass
        if refs:
            return True, refs[:10]
        try:
            response, document = self._get_json(
                "/api/observations?" + urlencode({"source": source})
            )
        except (OSError, URLError, TimeoutError):
            return local_read, []
        observations = document.get("observations") if document else None
        if response.status == 200 and isinstance(observations, list):
            for item in observations:
                if (
                    isinstance(item, dict)
                    and _after(item.get("observed_at"), since)
                    and predicate(item)
                ):
                    refs.append(
                        {
                            "reference": f"GET /api/observations?source={source}",
                            "observed_at": item.get("observed_at"),
                            "verdict": item.get("verdict"),
                            "rule_id": item.get("rule_id"),
                        }
                    )
            return True, refs[:10]
        return local_read, []

    def _wait_observation_refs(
        self,
        source: str,
        since: str,
        predicate: Callable[[Mapping[str, Any]], bool],
    ) -> tuple[bool, list[dict[str, Any]]]:
        available = False
        for attempt in range(8):
            current_available, refs = self._observation_refs(source, since, predicate)
            available = available or current_available
            if refs:
                return True, refs
            if attempt < 7:
                self.sleep(2.0)
        return available, []

    def _case_result(
        self,
        case_id: str,
        started_at: str,
        command_start: int,
        http_start: int,
        facts: Mapping[str, Any],
        evidence: Mapping[str, Any],
    ) -> None:
        result, reason = evaluate_case(case_id, facts)
        case_http = self.http_events[http_start:]
        case_evidence: dict[str, Any] = {
            "request_ids": [
                event["request_id"]
                for event in case_http
                if isinstance(event.get("request_id"), str)
            ],
            "http_codes": [event.get("status") for event in case_http],
            "record_fields_used": None,
            "observation_references": [],
        }
        case_evidence.update(evidence)
        item = {
            "case_id": case_id,
            "title": CASE_TITLES[case_id],
            "rt_ids": CASE_RT_IDS[case_id],
            "started_at": started_at,
            "ended_at": _now(),
            "result": result,
            "reason": reason,
            "commands": self.runner.history[command_start:],
            "http": case_http,
            "facts": dict(facts),
            "evidence": case_evidence,
        }
        self.cases.append(item)
        labels = {"passed": "통과", "failed": "실패", "not_run": "미실행", "unknown": "확인 불가"}
        print(f"{case_id} {CASE_TITLES[case_id]}: {labels[result]} — {reason}")

    def _run_case(self, case_id: str, function: Callable[[], tuple[dict[str, Any], dict[str, Any]]]) -> None:
        started_at = _now()
        command_start = len(self.runner.history)
        http_start = len(self.http_events)
        try:
            facts, evidence = function()
        except Exception as error:
            facts = {"unknown_reason": f"사례 실행 오류({_safe_exception(error)})"}
            evidence = {}
        self._case_result(case_id, started_at, command_start, http_start, facts, evidence)

    def aws_01(self) -> tuple[dict[str, Any], dict[str, Any]]:
        since = _now()
        response, request_id, valid = self._completion(
            token=self.tokens["RUNTIME_INFERENCE_TOKEN"]
        )
        detail = self._detail(request_id)
        record = _record_fields(detail)
        stream_response, stream_request_id, stream_data, stream_done = (
            self._stream_completion(token=self.tokens["RUNTIME_INFERENCE_TOKEN"])
        )
        stream_record = _record_fields(self._detail(stream_request_id))
        stream_ok = None if stream_record is None else (
            stream_response.status == 200
            and stream_data
            and stream_done
            and stream_record["result"] == "ALLOWED"
            and stream_record["delivery"]["attempted"] is True
        )
        logs_result, logs = self._engine_logs(since)
        log_match = logs_result.returncode == 0 and self._log_received(
            logs, request_id, "/v1/chat/completions"
        )
        self.engine_log_format_confirmed = log_match
        facts = {
            "http_200": response.status == 200,
            "valid_completion": valid,
            "record_allowed": None if record is None else record["result"] == "ALLOWED",
            "delivery_attempted": None if record is None else record["delivery"]["attempted"] is True,
            "engine_log_match": log_match if logs_result.returncode == 0 else None,
            "stream_ok": stream_ok,
        }
        return facts, {
            "record_fields_used": record,
            "stream": {
                "http_status": stream_response.status,
                "sse_data_received": stream_data,
                "done_received": stream_done,
                "record_fields_used": stream_record,
            },
            "engine_evidence": {
                "state": "observed" if log_match else "unknown",
                "match": "Received request chatcmpl-<gateway request id>",
                "reference": "kubectl logs deployment/serving-engine -c engine --since-time",
            },
        }

    def aws_02(self) -> tuple[dict[str, Any], dict[str, Any]]:
        since = _now()
        attempts: list[dict[str, Any]] = []
        for label, token in (("missing", None), ("invalid", self.wrong_token)):
            response, request_id, _valid = self._completion(token=token)
            body = _json_object(response.body)
            error = body.get("error") if body and isinstance(body.get("error"), dict) else {}
            record = _record_fields(self._detail(request_id))
            attempts.append(
                {
                    "kind": label,
                    "status": response.status,
                    "request_id": request_id,
                    "error_code": error.get("code"),
                    "record_fields_used": record,
                }
            )
        logs_result, logs = self._engine_logs(since)
        expected = {"missing": "AUTH_REQUIRED", "invalid": "AUTH_INVALID"}
        auth_codes = all(
            item["status"] == 401 and item["error_code"] == expected[item["kind"]]
            for item in attempts
        )
        records_present = all(item["record_fields_used"] is not None for item in attempts)
        records_denied = (
            all(
                item["record_fields_used"]["result"] == "DENIED"
                and item["record_fields_used"]["reason_code"] == expected[item["kind"]]
                for item in attempts
            )
            if records_present
            else None
        )
        not_delivered = (
            all(item["record_fields_used"]["delivery"]["attempted"] is False for item in attempts)
            if records_present
            else None
        )
        ids = [item["request_id"] for item in attempts if item["request_id"]]
        log_absent = logs_result.returncode == 0 and all(
            not self._log_received(logs, request_id, "/v1/chat/completions")
            for request_id in ids
        )
        return {
            "auth_codes": auth_codes,
            "records_denied": records_denied,
            "not_delivered": not_delivered,
            "engine_log_absent": log_absent if logs_result.returncode == 0 else None,
            "engine_log_format_confirmed": self.engine_log_format_confirmed,
        }, {
            "attempts": attempts,
            "engine_evidence": {
                "state": "not_observed" if log_absent and self.engine_log_format_confirmed else "unknown",
                "reference": "kubectl logs deployment/serving-engine -c engine --since-time",
                "note": "positive log format must first be confirmed by AWS-01",
            },
        }

    def aws_03(self) -> tuple[dict[str, Any], dict[str, Any]]:
        policy_response, policy = self._get_json("/api/policy")
        limit = _extract_policy_limit(policy) if policy_response.status == 200 else None
        if limit is None:
            return {"unknown_reason": "GET /api/policy에서 output_max_tokens를 확인하지 못함"}, {
                "policy_http_code": policy_response.status,
                "policy_limit": None,
            }
        since = _now()
        boundary_response, boundary_request_id, boundary_valid = self._completion(
            token=self.tokens["RUNTIME_INFERENCE_TOKEN"], max_tokens=limit
        )
        boundary_record = _record_fields(self._detail(boundary_request_id))
        boundary_allowed = (
            boundary_response.status == 200
            and boundary_valid
            and boundary_record is not None
            and boundary_record["result"] == "ALLOWED"
            and boundary_record["delivery"]["attempted"] is True
        )
        response, request_id, _valid = self._completion(
            token=self.tokens["RUNTIME_INFERENCE_TOKEN"], max_tokens=limit + 1
        )
        body = _json_object(response.body)
        error = body.get("error") if body and isinstance(body.get("error"), dict) else {}
        record = _record_fields(self._detail(request_id))
        logs_result, logs = self._engine_logs(since)
        log_absent = (
            not self._log_received(logs, request_id, "/v1/chat/completions")
            if logs_result.returncode == 0 and request_id is not None
            else None
        )
        facts = {
            "policy_limit_read": True,
            "boundary_allowed": boundary_allowed,
            "http_403": response.status == 403,
            "reason_code": (
                None
                if record is None
                else error.get("code") == "MAX_TOKENS_EXCEEDED"
                and record["reason_code"] == "MAX_TOKENS_EXCEEDED"
            ),
            "not_delivered": None if record is None else record["delivery"]["attempted"] is False,
            "engine_log_absent": log_absent,
            "engine_log_format_confirmed": self.engine_log_format_confirmed,
        }
        return facts, {
            "policy_limit": limit,
            "record_fields_used": record,
            "boundary": {
                "http_status": boundary_response.status,
                "record_fields_used": boundary_record,
            },
            "engine_evidence": {
                "state": "not_observed" if log_absent is True and self.engine_log_format_confirmed else "unknown",
                "reference": "kubectl logs deployment/serving-engine -c engine --since-time",
                "note": "positive log format must first be confirmed by AWS-01",
            },
        }

    def aws_04(self) -> tuple[dict[str, Any], dict[str, Any]]:
        since = _now()
        gateway_before = self._tcp_probe(
            "deployment/serving-gateway", "gateway", "serving-engine", 8000
        )
        probe = self._tcp_probe("pod/other-client", "client", "serving-engine", 8000)
        gateway_after = self._tcp_probe(
            "deployment/serving-gateway", "gateway", "serving-engine", 8000
        )
        control, request_id, valid = self._completion(
            token=self.tokens["RUNTIME_INFERENCE_TOKEN"]
        )
        control_record = _record_fields(self._detail(request_id))
        available, refs = self._wait_observation_refs(
            "hubble",
            since,
            lambda item: (
                str((item.get("src") or {}).get("pod", "")).startswith("other-client")
                and str((item.get("dst") or {}).get("pod", "")).startswith("serving-engine")
                and (item.get("dst") or {}).get("port") == 8000
                and str(item.get("verdict", "")).lower() == "dropped"
            ),
        )
        gateway_tcp_control = gateway_before["connected"] and gateway_after["connected"]
        facts: dict[str, Any] = {
            "direct_failed": probe["connection_timed_out"] if probe["marker"] is not None else None,
            "gateway_tcp_control": (
                gateway_tcp_control
                if gateway_before["marker"] is not None and gateway_after["marker"] is not None
                else None
            ),
            "gateway_control_200": (
                None
                if control_record is None
                else control.status == 200
                and valid
                and control_record["result"] == "ALLOWED"
                and control_record["delivery"]["attempted"] is True
            ),
            "hubble_dropped": bool(refs) if available else None,
        }
        if not probe["python_available"]:
            facts = {"not_run_reason": "other-client에 python3가 없음"}
        elif not gateway_before["python_available"] or not gateway_after["python_available"]:
            facts = {"unknown_reason": "Gateway 컨테이너에서 TCP 대조 probe를 실행하지 못함"}
        elif probe["connection_failed"] and not probe["connection_timed_out"]:
            facts = {
                "unknown_reason": (
                    "other-client→engine 연결 실패가 시간 초과가 아님"
                    f"({probe['errno_name'] or 'errno 미확인'})"
                )
            }
        return facts, {
            "direct_probe": probe,
            "gateway_tcp_control": {"before": gateway_before, "after": gateway_after},
            "record_fields_used": control_record,
            "observation_references": refs,
            "observation": {
                "state": "observed" if refs else "not_observed",
                "references": refs,
                "note": "해당 other-client→engine:8000 흐름의 Hubble dropped만 필수 관측으로 인정함",
            },
        }

    def aws_05(self) -> tuple[dict[str, Any], dict[str, Any]]:
        pod, _result = self._engine_pod()
        if not pod or not isinstance(pod.get("name"), str) or not pod.get("ready"):
            return {"unknown_reason": "Ready 엔진 Pod 하나를 식별하지 못함"}, {}
        receiver, _receiver_result = self._test_receiver_pod()
        if receiver is None:
            return {"unknown_reason": "Ready test-receiver Pod IP를 식별하지 못함"}, {
                "engine_pod": pod,
            }
        since = _now()
        engine_receiver = self._tcp_probe(
            "pod/" + pod["name"], "engine", receiver["ip"], 8080
        )
        if not engine_receiver["python_available"]:
            return {"not_run_reason": "engine 컨테이너에 python3가 없음"}, {
                "engine_pod": pod,
                "receiver_pod": receiver,
                "engine_receiver_probe": engine_receiver,
            }
        receiver_control = self._tcp_probe(
            "pod/other-client", "client", receiver["ip"], 8080
        )
        if not receiver_control["python_available"]:
            return {"not_run_reason": "other-client에 python3가 없음"}, {
                "engine_pod": pod,
                "receiver_pod": receiver,
                "engine_receiver_probe": engine_receiver,
                "receiver_control_probe": receiver_control,
            }
        external = self._tcp_probe("pod/" + pod["name"], "engine", _EXTERNAL_TEST_IP, 80)
        imds = self._tcp_probe("pod/" + pod["name"], "engine", _IMDS_IP, 80)
        available_receiver, refs_receiver = self._wait_observation_refs(
            "hubble",
            since,
            lambda item: (
                (item.get("src") or {}).get("pod") == pod["name"]
                and (item.get("dst") or {}).get("ip") == receiver["ip"]
                and (item.get("dst") or {}).get("port") == 8080
                and str(item.get("verdict", "")).lower() == "dropped"
            ),
        )
        available_external, refs_external = self._wait_observation_refs(
            "hubble",
            since,
            lambda item: (
                str((item.get("src") or {}).get("pod", "")).startswith("serving-engine")
                and (item.get("dst") or {}).get("ip") == _EXTERNAL_TEST_IP
                and (item.get("dst") or {}).get("port") == 80
            ),
        )
        available_imds, refs_imds = self._wait_observation_refs(
            "hubble",
            since,
            lambda item: (
                str((item.get("src") or {}).get("pod", "")).startswith("serving-engine")
                and (item.get("dst") or {}).get("ip") == _IMDS_IP
                and (item.get("dst") or {}).get("port") == 80
            ),
        )
        facts: dict[str, Any] = {
            "engine_receiver_failed": (
                engine_receiver["connection_timed_out"]
                if engine_receiver["marker"] is not None
                else None
            ),
            "receiver_control_connected": (
                receiver_control["connected"]
                if receiver_control["marker"] is not None
                else None
            ),
            "receiver_hubble_dropped": bool(refs_receiver) if available_receiver else None,
            "supplementary_external_failed": (
                external["connection_failed"] if external["marker"] is not None else None
            ),
            "supplementary_imds_failed": (
                imds["connection_failed"] if imds["marker"] is not None else None
            ),
            "supplementary_external_hubble": (
                bool(refs_external) if available_external else None
            ),
            "supplementary_imds_hubble": bool(refs_imds) if available_imds else None,
        }
        if engine_receiver["connection_failed"] and not engine_receiver["connection_timed_out"]:
            facts = {
                "unknown_reason": (
                    "engine→receiver 연결 실패가 시간 초과가 아님"
                    f"({engine_receiver['errno_name'] or 'errno 미확인'})"
                )
            }
        return facts, {
            "engine_pod": pod,
            "receiver_pod": receiver,
            "engine_receiver_probe": engine_receiver,
            "receiver_control_probe": receiver_control,
            "external_probe": external,
            "imds_probe": imds,
            "observation_references": [*refs_receiver, *refs_external, *refs_imds],
            "observations": {
                "engine_receiver": refs_receiver,
                "external": refs_external,
                "imds": refs_imds,
                "note": (
                    "engine→receiver 해당 흐름의 Hubble dropped는 필수 연결 증거임. "
                    "TEST-NET-1·IMDS probe는 정책이 없어도 실패하므로 보조 증거일 뿐 단독으로 아무것도 입증하지 않음"
                ),
            },
        }

    def aws_06(self) -> tuple[dict[str, Any], dict[str, Any]]:
        pod, _result = self._engine_pod()
        if not pod or not isinstance(pod.get("name"), str) or not pod.get("ready"):
            return {"unknown_reason": "Ready 엔진 Pod 하나를 식별하지 못함"}, {}
        suffix = f"{os.getpid()}-{uuid4().hex[:8]}"
        model_path = f"/models/.byul-acceptance-{suffix}"
        cache_path = f"/cache/.byul-acceptance-{suffix}"
        since = _now()
        model_result = self._kubectl(
            "exec", "pod/" + pod["name"], "-c", "engine", "--", "python3", "-c", WRITE_DENY_CODE, model_path,
            timeout=15,
        )
        if model_result.returncode != 0 and (
            "not found" in model_result.stderr.lower() or "executable file" in model_result.stderr.lower()
        ):
            return {"not_run_reason": "engine 컨테이너에 python3가 없음"}, {}
        cache_result = self._kubectl(
            "exec", "pod/" + pod["name"], "-c", "engine", "--", "python3", "-c", CACHE_WRITE_CODE, cache_path,
            timeout=15,
        )
        model_available, model_refs = self._wait_observation_refs(
            "tetragon",
            since,
            lambda item: item.get("rule_id") == "TG-ASSET-01" and item.get("path") == model_path,
        )
        cache_available, cache_refs = self._wait_observation_refs(
            "tetragon",
            since,
            lambda item: item.get("rule_id") == "TG-CACHE-01" and item.get("path") == cache_path,
        )
        model_denied: bool | None = None
        model_marker = (
            "WRITE_DENIED" if "WRITE_DENIED" in model_result.stdout else
            "WRITE_SUCCEEDED" if "WRITE_SUCCEEDED" in model_result.stdout else
            "WRITE_ERROR" if "WRITE_ERROR" in model_result.stdout else None
        )
        errno_match = re.search(
            r"(?:WRITE_DENIED|WRITE_ERROR) errno=(\d+) name=([A-Z0-9_]+)",
            model_result.stdout,
        )
        model_errno = int(errno_match.group(1)) if errno_match else None
        model_errno_name = errno_match.group(2) if errno_match else None
        if model_marker == "WRITE_DENIED" and model_errno_name == "EROFS":
            model_denied = model_result.returncode == 0
        elif model_marker == "WRITE_DENIED" and model_errno_name in {"EPERM", "EACCES"}:
            model_denied = False
        elif "WRITE_SUCCEEDED" in model_result.stdout:
            model_denied = False
        cache_succeeded = (
            cache_result.returncode == 0
            if "CACHE_WRITE_REMOVED" in cache_result.stdout
            else None
        )
        facts: dict[str, Any] = {
            "model_write_denied": model_denied,
            "cache_write_succeeded": cache_succeeded,
            "cache_harmless_removed": cache_succeeded,
            "cache_tetragon": bool(cache_refs) if cache_available else None,
        }
        if model_marker == "WRITE_DENIED" and model_errno_name in {"EPERM", "EACCES"}:
            facts = {
                "unknown_reason": (
                    f"모델 쓰기 거부 errno {model_errno_name}는 read-only mount 증거가 아님"
                )
            }
        return facts, {
            "engine_pod": pod,
            "model_probe": {
                "returncode": model_result.returncode,
                "marker": model_marker,
                "errno": model_errno,
                "errno_name": model_errno_name,
            },
            "cache_probe": {"returncode": cache_result.returncode, "marker": "CACHE_WRITE_REMOVED" if "CACHE_WRITE_REMOVED" in cache_result.stdout else "other"},
            "observation_references": [*model_refs, *cache_refs],
            "observations": {
                "model": {"state": "observed" if model_refs else "not_observed", "references": model_refs},
                "cache": {"state": "observed" if cache_refs else "not_observed", "references": cache_refs},
                "note": "EROFS만 read-only mount 증거이며 EPERM·EACCES는 통과 근거가 아님",
                "sources_readable": {"model": model_available, "cache": cache_available},
            },
        }

    def _collectors(self) -> tuple[HTTPResponse, dict[str, dict[str, Any]]]:
        response, document = self._get_json("/api/collectors")
        items = document.get("collectors") if document else None
        mapped = {
            item["source"]: item
            for item in items or []
            if isinstance(item, dict) and isinstance(item.get("source"), str)
        }
        return response, mapped

    def _wait_collectors_ok(self, timeout: float) -> tuple[bool, dict[str, dict[str, Any]]]:
        deadline = self.monotonic() + timeout
        latest: dict[str, dict[str, Any]] = {}
        while True:
            try:
                response, latest = self._collectors()
            except (OSError, URLError, TimeoutError):
                response = HTTPResponse(0, {}, b"")
            if response.status == 200 and all(
                latest.get(source, {}).get("state") == "ok" for source in _OBSERVATION_SOURCES
            ):
                return True, latest
            if self.monotonic() >= deadline:
                return False, latest
            self.sleep(min(2.0, max(0.0, deadline - self.monotonic())))

    def _wait_collectors_not_ok(
        self, timeout: float
    ) -> tuple[bool, dict[str, dict[str, Any]], float]:
        started = self.monotonic()
        deadline = started + timeout
        latest: dict[str, dict[str, Any]] = {}
        while True:
            try:
                response, latest = self._collectors()
            except (OSError, URLError, TimeoutError):
                response = HTTPResponse(0, {}, b"")
            if response.status == 200 and all(
                source in latest and latest[source].get("state") != "ok"
                for source in _OBSERVATION_SOURCES
            ):
                return True, latest, self.monotonic() - started
            if self.monotonic() >= deadline:
                return False, latest, self.monotonic() - started
            self.sleep(min(2.0, max(0.0, deadline - self.monotonic())))

    def _heartbeat_snapshot(
        self, since: str
    ) -> tuple[bool, bool, dict[str, dict[str, Any]]]:
        readable = True
        snapshots: dict[str, dict[str, Any]] = {}
        for kind in ("hubble", "tetragon"):
            path = self.observation_dir / f"{kind}.heartbeat.json"
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
                readable = False
                snapshots[kind] = {
                    "reference": str(path),
                    "read_error": _safe_exception(error),
                }
                continue
            valid = (
                isinstance(document, dict)
                and document.get("schema") == HEARTBEAT_SCHEMA
                and document.get("kind") == kind
            )
            if not valid:
                readable = False
                snapshots[kind] = {
                    "reference": str(path),
                    "read_error": "invalid heartbeat schema or kind",
                }
                continue
            updated_at = document.get("updated_at")
            snapshots[kind] = {
                "reference": str(path),
                "state": document.get("state"),
                "updated_at": updated_at,
                "updated_after_stop_request": _after(updated_at, since),
            }
        stopped = readable and all(
            snapshots[kind].get("state") == "stopped"
            and snapshots[kind].get("updated_after_stop_request") is True
            for kind in ("hubble", "tetragon")
        )
        return readable, stopped, snapshots

    def _wait_heartbeats_stopped(
        self, since: str, timeout: float
    ) -> tuple[bool | None, dict[str, dict[str, Any]], float]:
        started = self.monotonic()
        deadline = started + timeout
        latest: dict[str, dict[str, Any]] = {}
        readable_seen = False
        while True:
            readable, stopped, latest = self._heartbeat_snapshot(since)
            readable_seen = readable_seen or readable
            if stopped:
                return True, latest, self.monotonic() - started
            if self.monotonic() >= deadline:
                return (False if readable_seen else None), latest, self.monotonic() - started
            self.sleep(min(0.25, max(0.0, deadline - self.monotonic())))

    def _gap_positions(self) -> tuple[bool, dict[Path, int]]:
        positions: dict[Path, int] = {}
        if not self.observation_dir.is_dir():
            return False, {}
        try:
            paths = set(self.observation_dir.glob("*.gaps.jsonl"))
        except OSError:
            return False, {}
        paths.update(
            {
                self.observation_dir / "hubble.gaps.jsonl",
                self.observation_dir / "tetragon.gaps.jsonl",
            }
        )
        for path in sorted(paths):
            if not path.exists():
                positions[path] = 0
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    positions[path] = sum(1 for _ in handle)
            except OSError:
                return False, positions
        return True, positions

    @staticmethod
    def _new_gap_refs(positions: Mapping[Path, int]) -> tuple[bool, list[dict[str, Any]]]:
        refs: list[dict[str, Any]] = []
        for path, old_lines in positions.items():
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    for number, line in enumerate(handle, 1):
                        if number <= old_lines:
                            continue
                        try:
                            document = json.loads(line)
                        except (json.JSONDecodeError, RecursionError):
                            continue
                        if isinstance(document, dict) and document.get("event") in {"gap_opened", "gap_closed"}:
                            refs.append(
                                {
                                    "reference": f"{path}:{number}",
                                    "event": document.get("event"),
                                    "kind": document.get("kind"),
                                    "gap_id": document.get("gap_id"),
                                }
                            )
            except OSError:
                return False, refs
        return True, refs

    def aws_07(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.args.allow_collector_stop:
            return {"not_run_reason": "--allow-collector-stop 없음"}, {}
        before_response, before = self._collectors()
        if before_response.status != 200 or not all(source in before for source in _OBSERVATION_SOURCES):
            return {"unknown_reason": "observation_feed collector 상태를 읽지 못함"}, {}
        stale_values = [before[source].get("stale_after_s") for source in _OBSERVATION_SOURCES]
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0
            for value in stale_values
        ):
            return {"unknown_reason": "collector stale_after_s를 확인하지 못함"}, {}
        stale = max(float(value) for value in stale_values)
        gaps_readable, positions = self._gap_positions()
        stop_requested_at = _now()
        stop_started = self.monotonic()
        stop = self.runner.run(
            ["systemctl", "--user", "stop", "byul-observation-feed"], timeout=30
        )
        stop_completed_at = _now()
        stop_elapsed = self.monotonic() - stop_started
        start: CommandResult | None = None
        after: dict[str, dict[str, Any]] = {}
        recovered: dict[str, dict[str, Any]] = {}
        recovery_ok = False
        heartbeat_seen: bool | None = None
        heartbeat_snapshot: dict[str, dict[str, Any]] = {}
        heartbeat_elapsed: float | None = None
        unavailable: bool | None = None
        unavailable_elapsed: float | None = None
        start_requested_at: str | None = None
        start_completed_at: str | None = None
        start_elapsed: float | None = None
        recovery_elapsed: float | None = None
        stopped_request: dict[str, Any] = {
            "http_status": None,
            "reason_code": None,
            "error": None,
        }
        inference_recovered = False
        inference_record: dict[str, Any] | None = None
        inference_retry: dict[str, Any] = {
            "attempts": 0,
            "elapsed_s": 0.0,
            "window_s": min(stale + 30.0, 90.0),
            "last_status": None,
            "last_reason_code": None,
        }
        try:
            if stop.returncode == 0:
                stopped_window_started = self.monotonic()
                heartbeat_seen, heartbeat_snapshot, _heartbeat_wait = (
                    self._wait_heartbeats_stopped(
                        stop_requested_at, min(stale, 5.0)
                    )
                )
                if heartbeat_seen is True:
                    heartbeat_elapsed = self.monotonic() - stopped_window_started
                remaining = max(
                    0.0,
                    stale - (self.monotonic() - stopped_window_started),
                )
                unavailable, after, _unavailable_wait = self._wait_collectors_not_ok(
                    remaining
                )
                if unavailable:
                    unavailable_elapsed = self.monotonic() - stopped_window_started
                try:
                    stopped_response, stopped_request_id, _stopped_valid = self._completion(
                        token=self.tokens["RUNTIME_INFERENCE_TOKEN"],
                        timeout=min(max(stale, 1.0), 30.0),
                    )
                    stopped_record = _record_fields(self._detail(stopped_request_id))
                    stopped_document = _json_object(stopped_response.body)
                    stopped_error = (
                        stopped_document.get("error")
                        if stopped_document and isinstance(stopped_document.get("error"), dict)
                        else {}
                    )
                    stopped_request = {
                        "http_status": stopped_response.status,
                        "reason_code": (
                            stopped_record.get("reason_code")
                            if stopped_record
                            else stopped_error.get("code")
                        ),
                        "error": None,
                    }
                except (OSError, URLError, TimeoutError) as error:
                    stopped_request = {
                        "http_status": None,
                        "reason_code": None,
                        "error": _safe_exception(error),
                    }
        finally:
            start_requested_at = _now()
            start_started = self.monotonic()
            start = self.runner.run(
                ["systemctl", "--user", "start", "byul-observation-feed"], timeout=30
            )
            start_completed_at = _now()
            start_elapsed = self.monotonic() - start_started
            if start.returncode == 0:
                recovery_started = self.monotonic()
                recovery_window = min(stale + 30.0, 90.0)
                recovery_ok, recovered = self._wait_collectors_ok(recovery_window)
                recovery_elapsed = self.monotonic() - recovery_started
                if recovery_ok:
                    inference_recovered, inference_record, inference_retry = (
                        self._retry_inference(recovery_window)
                    )
        gap_scan_ok, gap_refs = self._new_gap_refs(positions) if gaps_readable else (False, [])
        heartbeat_refs = list(heartbeat_snapshot.values())
        return {
            "stop_ok": stop.returncode == 0,
            "heartbeat_stopped_seen": heartbeat_seen,
            "collectors_unavailable": unavailable,
            "start_ok": bool(start and start.returncode == 0),
            "collectors_recovered": recovery_ok,
            "inference_recovered": inference_recovered,
            "supplementary_gap_recorded": bool(gap_refs) if gap_scan_ok else None,
        }, {
            "timings": {
                "stale_after_s": stale,
                "stop_requested_at": stop_requested_at,
                "stop_completed_at": stop_completed_at,
                "stop_command_elapsed_s": round(stop_elapsed, 3),
                "heartbeat_stopped_observed_after_stop_completion_s": (
                    None if heartbeat_elapsed is None else round(heartbeat_elapsed, 3)
                ),
                "collectors_unavailable_observed_after_stop_completion_s": (
                    None if unavailable_elapsed is None else round(unavailable_elapsed, 3)
                ),
                "start_requested_at": start_requested_at,
                "start_completed_at": start_completed_at,
                "start_command_elapsed_s": (
                    None if start_elapsed is None else round(start_elapsed, 3)
                ),
                "collectors_recovered_after_start_s": (
                    None if recovery_elapsed is None else round(recovery_elapsed, 3)
                ),
            },
            "collector_states": {
                "before": {source: before[source].get("state") for source in _OBSERVATION_SOURCES},
                "stopped": {source: after.get(source, {}).get("state") for source in _OBSERVATION_SOURCES},
                "recovered": {source: recovered.get(source, {}).get("state") for source in _OBSERVATION_SOURCES},
            },
            "heartbeat_stopped": heartbeat_snapshot,
            "stopped_interval_request": stopped_request,
            "recovery_request": {
                **inference_retry,
                "record_fields_used": inference_record,
            },
            "gap_references": gap_refs,
            "observation_references": [*heartbeat_refs, *gap_refs],
            "gap_note": "clean systemd stop은 gap 행을 만들지 않으며 gap은 보조 증거임",
        }

    def aws_08(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.args.allow_engine_restart:
            return {"not_run_reason": "--allow-engine-restart 없음(GPU 반납 및 cold-start 동반)"}, {}
        print("경고: AWS-08은 serving-engine GPU를 반납한 뒤 cold-start합니다.")
        _target_response, target_before = self._get_json("/api/target")
        before_tracker = target_before.get("instance") if target_before else None
        before_current = (
            before_tracker.get("current") if isinstance(before_tracker, dict) else None
        )
        before_instance_id = (
            before_current.get("instance_id")
            if isinstance(before_current, dict)
            else None
        )
        if not isinstance(before_instance_id, str) or not before_instance_id:
            return {"unknown_reason": "교체 전 Gateway target instance_id를 확인하지 못함"}, {
                "target_before": {
                    "state": before_tracker.get("state") if isinstance(before_tracker, dict) else None,
                    "instance_id": before_instance_id,
                },
                "snapshot_note": (
                    "Gateway target Pod snapshot이 stale인 상태를 관측함"
                    if _target_snapshot_stale(target_before)
                    else None
                ),
            }
        before, _before_result = self._engine_pod()
        if not before or not before.get("uid") or not before.get("container_id") or not before.get("ready"):
            return {"unknown_reason": "교체 전 엔진 Pod UID/containerID를 확인하지 못함"}, {}
        restarted_at = _now()
        restart = self._kubectl("rollout", "restart", "deployment/serving-engine", timeout=30)
        rollout = self._kubectl(
            "rollout", "status", "deployment/serving-engine", "--timeout=25m", timeout=1510
        ) if restart.returncode == 0 else CommandResult(1)
        after, _after_result = self._engine_pod()
        gateway_instance_changed = False
        target_after: dict[str, Any] | None = None
        matching_history: dict[str, Any] | None = None
        snapshot_stale_seen = _target_snapshot_stale(target_before)
        if rollout.returncode == 0:
            deadline = self.monotonic() + 90.0
            while True:
                try:
                    _target_response, target_after = self._get_json("/api/target")
                except (OSError, URLError, TimeoutError):
                    target_after = None
                if target_after:
                    snapshot_stale_seen = snapshot_stale_seen or _target_snapshot_stale(
                        target_after
                    )
                    tracker = target_after.get("instance")
                    current = tracker.get("current") if isinstance(tracker, dict) else None
                    current_id = current.get("instance_id") if isinstance(current, dict) else None
                    history = target_after.get("history")
                    if isinstance(history, list):
                        matching_history = next(
                            (
                                item
                                for item in history
                                if isinstance(item, dict)
                                and item.get("event") == "instance_changed"
                                and item.get("reason") == "pod_replaced"
                                and item.get("previous_instance_id") == before_instance_id
                                and isinstance(item.get("instance"), dict)
                                and item["instance"].get("instance_id") == current_id
                            ),
                            None,
                        )
                    gateway_instance_changed = (
                        isinstance(current_id, str)
                        and current_id != before_instance_id
                        and matching_history is not None
                    )
                    if gateway_instance_changed:
                        break
                if self.monotonic() >= deadline:
                    break
                self.sleep(min(2.0, max(0.0, deadline - self.monotonic())))
        engine_info_ok = False
        engine_info: dict[str, Any] | None = None
        if rollout.returncode == 0:
            deadline = self.monotonic() + 90.0
            while True:
                try:
                    _collector_response, collectors = self._collectors()
                except (OSError, URLError, TimeoutError):
                    collectors = {}
                engine_info = collectors.get("engine_info")
                if (
                    engine_info
                    and engine_info.get("state") == "ok"
                    and _after(engine_info.get("last_success_at"), restarted_at)
                ):
                    engine_info_ok = True
                    break
                if self.monotonic() >= deadline:
                    break
                self.sleep(min(2.0, max(0.0, deadline - self.monotonic())))
        gateway_recovered = False
        recovery_record: dict[str, Any] | None = None
        recovery_retry: dict[str, Any] = {
            "attempts": 0,
            "elapsed_s": 0.0,
            "window_s": 90.0,
            "last_status": None,
            "last_reason_code": None,
        }
        if engine_info_ok:
            gateway_recovered, recovery_record, recovery_retry = self._retry_inference(90.0)
        changed: bool | None = None
        if after and after.get("uid") and after.get("container_id") and after.get("ready"):
            # A rollout replacement must change the Pod identity. A container
            # restart alone is a different runtime event and does not satisfy
            # AWS-08's host-side replacement evidence.
            changed = before.get("uid") != after.get("uid")
        return {
            "restart_ok": restart.returncode == 0 and rollout.returncode == 0,
            "pod_changed": changed,
            "gateway_recovered": gateway_recovered,
            "engine_info_recovered": engine_info_ok,
            "gateway_instance_changed": gateway_instance_changed,
        }, {
            "engine_before": before,
            "engine_after": after,
            "record_fields_used": recovery_record,
            "recovery_request": recovery_retry,
            "engine_info": {
                "state": engine_info.get("state") if engine_info else None,
                "last_success_at": engine_info.get("last_success_at") if engine_info else None,
                "required_after": restarted_at,
            },
            "target_before": {"instance_id": before_instance_id},
            "target_after": {
                "instance_id": (
                    target_after["instance"]["current"].get("instance_id")
                    if isinstance(target_after, dict)
                    and isinstance(target_after.get("instance"), dict)
                    and isinstance(target_after["instance"].get("current"), dict)
                    else None
                ),
                "history_event": matching_history,
            },
            "snapshot_note": (
                "Gateway target Pod snapshot이 stale인 상태를 관측함"
                if snapshot_stale_seen
                else None
            ),
        }

    def write_report(self) -> tuple[Path, Path]:
        report = {
            "schema": "runtime-aws-acceptance/1.0",
            "run_id": self.evidence_dir.name,
            "started_at": self.run_started_at,
            "ended_at": _now(),
            "kube_context": self.context,
            "namespace": self.namespace,
            "gateway_url": self.gateway,
            "observation_dir": str(self.observation_dir),
            "requested_cases": list(self.args.cases),
            "allow_collector_stop": self.args.allow_collector_stop,
            "allow_engine_restart": self.args.allow_engine_restart,
            "overall": _overall(self.cases),
            "result_counts": {
                result: sum(case["result"] == result for case in self.cases)
                for result in sorted(RESULTS)
            },
            "decision_notes": [
                (
                    "AWS-04 and AWS-05 accept only connect_ex timeout errnos as a block candidate, "
                    "and require the exact Hubble flow to be dropped plus their allowed-path "
                    "controls. ECONNREFUSED and other failures remain unknown."
                ),
                (
                    "AWS-07 clean stop is decided by stopped heartbeats, Gateway collector "
                    "unavailability, collector recovery, and recovered inference. Its stopped-window "
                    "request and gap records are supplementary."
                ),
            ],
            "known_gaps": [
                "The first AWS run must confirm that this Cilium configuration manifests a blocked connect as EAGAIN/EWOULDBLOCK/ETIMEDOUT; another errno is not accepted as policy-block evidence.",
                "AWS-06 accepts only EROFS as read-only mount evidence. EPERM may be Tetragon enforcement and EACCES may come from ownership/mode before a read-only mount is tested.",
            ],
            "cases": self.cases,
        }
        token_values = (*self.tokens.values(), self.wrong_token)
        secrets = (
            *token_values,
            *(hashlib.sha256(token.encode("utf-8")).hexdigest() for token in token_values),
        )
        safe_report = _redact(report, secrets)
        encoded = json.dumps(safe_report, ensure_ascii=False, indent=2) + "\n"
        if any(secret and secret in encoded for secret in secrets):
            raise RuntimeError("secret redaction failed")
        json_path = self.evidence_dir / "report.json"
        json_path.write_text(encoded, encoding="utf-8")
        os.chmod(json_path, 0o600)
        lines = [
            "# AWS runtime acceptance",
            "",
            f"- run: `{safe_report['run_id']}`",
            f"- context/namespace: `{self.context}` / `{self.namespace}`",
            f"- overall: **{safe_report['overall']}**",
            "",
            "| 사례 | 결과 | RT | 이유 |",
            "| --- | --- | --- | --- |",
        ]
        for case in safe_report["cases"]:
            reason = str(case["reason"]).replace("|", "\\|")
            lines.append(
                f"| {case['case_id']} {case['title']} | {case['result']} | "
                f"{', '.join(case['rt_ids'])} | {reason} |"
            )
        lines.extend(
            [
                "",
                "상세 명령, HTTP 상태, 요청 ID, 사용한 record 필드와 관측 참조는 `report.json`에 있다.",
                "응답 본문, prompt, token 및 token hash는 보고서에 저장하지 않는다.",
                "",
                "## Decision notes",
                "",
                *[f"- {item}" for item in safe_report["decision_notes"]],
                "",
                "## Known gaps",
                "",
                *[f"- {item}" for item in safe_report["known_gaps"]],
                "",
            ]
        )
        markdown = "\n".join(lines)
        if any(secret and secret in markdown for secret in secrets):
            raise RuntimeError("secret redaction failed")
        md_path = self.evidence_dir / "report.md"
        md_path.write_text(markdown, encoding="utf-8")
        os.chmod(md_path, 0o600)
        return json_path, md_path

    def run(self) -> int:
        functions = {
            "AWS-01": self.aws_01,
            "AWS-02": self.aws_02,
            "AWS-03": self.aws_03,
            "AWS-04": self.aws_04,
            "AWS-05": self.aws_05,
            "AWS-06": self.aws_06,
            "AWS-07": self.aws_07,
            "AWS-08": self.aws_08,
        }
        for case_id in self.args.cases:
            self._run_case(case_id, functions[case_id])
        json_path, md_path = self.write_report()
        overall = _overall(self.cases)
        print(f"overall: {overall}")
        print(f"JSON: {json_path}")
        print(f"Markdown: {md_path}")
        return 0 if overall == "passed" else (1 if overall == "failed" else 3)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AWS GPU lab runtime acceptance (does not apply or delete policies)"
    )
    parser.add_argument(
        "--kube-context",
        required=True,
        help="explicit Kubernetes context; every kubectl command receives --context",
    )
    parser.add_argument("--namespace", default="serving-lab")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:18080")
    parser.add_argument(
        "--token-env-file",
        required=True,
        help="KEY=VALUE file containing inference/viewer tokens (values are never reported)",
    )
    parser.add_argument("--observation-dir", default="/srv/serving/observation")
    parser.add_argument(
        "--evidence-dir",
        default="evidence",
        help="parent for a new mode-0700 aws-acceptance-<UTC>-<pid> directory",
    )
    parser.add_argument(
        "--cases",
        action="append",
        metavar="AWS-01[,AWS-02...]",
        help="case subset; repeatable (default: AWS-01 through AWS-08)",
    )
    parser.add_argument(
        "--allow-collector-stop",
        action="store_true",
        help="allow AWS-07 to stop/start byul-observation-feed",
    )
    parser.add_argument(
        "--allow-engine-restart",
        action="store_true",
        help="allow AWS-08 rollout restart (releases GPU and cold-starts)",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.cases = _parse_cases(args.cases)
        validate_kube_context(args.kube_context)
    except (argparse.ArgumentTypeError, ValueError) as error:
        parser.error(str(error))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        return AWSAcceptance(args).run()
    except ConfigurationError as error:
        print(f"AWS acceptance configuration error: {error}", file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError) as error:
        print(f"AWS acceptance failed before report: {_safe_exception(error)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
