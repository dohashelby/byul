#!/usr/bin/env python3
"""Run the serving-runtime Kubernetes lab checks and write secret-free reports."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time
from typing import Any, BinaryIO, Callable, Sequence

from serving_runtime.enforcement.events import transform_lines


EXPECTED_CONTEXT = "kind-runtime-lab"
REPORT_SCHEMA = "serving-runtime-k8s-lab-report/1.0"
PROBE_ATTEMPTS = 5
DEFAULT_TETRAGON_WAIT = 90.0
TETRAGON_POLL_INTERVAL = 5.0
L05_MEASUREMENT_NOTE = (
    "적용 직후 측정; 이전 관측에서 시간 경과 후 불일치 사례 있음"
)
TETRAGON_EXPORT_REMEDIATION = (
    "helm upgrade tetragon cilium/tetragon --version 1.7.1 -n kube-system "
    "--reuse-values -f deploy/lab/tetragon-values.yaml"
)
CASE_TITLES = {
    "L-01": "정책 적용 전 기준선",
    "L-02": "Cilium 통신 정책",
    "L-03": "Hubble DROPPED 증거",
    "L-04": "Tetragon 파일·실행 관측",
    "L-05": "Tetragon probe 차단",
    "L-06": "적용 정책 정리",
}
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(authorization|password|api[_-]?key|access[_-]?token|refresh[_-]?token|secret)"
    r"([\"']?\s*[:=]\s*[\"']?)([^\"'\s,;}]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")

HTTP_GET_SCRIPT = """\
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
        status = response.status
    print(json.dumps({"status": status}))
    raise SystemExit(0 if status == 200 else 8)
except Exception as error:
    print(json.dumps({"connected": False, "error": type(error).__name__}))
    raise SystemExit(7)
"""
GATEWAY_POST_SCRIPT = """\
import json, sys, urllib.error, urllib.request
body = json.dumps({"model": "test-model", "messages": [{"role": "user", "content": "lab check"}], "max_tokens": 8}).encode()
request = urllib.request.Request(sys.argv[1], data=body, headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        status = response.status
    print(json.dumps({"status": status}))
    raise SystemExit(0 if status == 200 else 8)
except urllib.error.HTTPError as error:
    print(json.dumps({"status": error.code}))
    raise SystemExit(8)
except Exception as error:
    print(json.dumps({"connected": False, "error": type(error).__name__}))
    raise SystemExit(7)
"""
EXTERNAL_CONNECT_SCRIPT = """\
import json, socket
try:
    connection = socket.create_connection(("1.1.1.1", 443), timeout=3)
    connection.close()
    print(json.dumps({"connected": True}))
    raise SystemExit(0)
except OSError as error:
    print(json.dumps({"connected": False, "error": type(error).__name__}))
    raise SystemExit(7)
"""
DNS_SCRIPT = """\
import json, socket
try:
    addresses = socket.getaddrinfo("kubernetes.default.svc.cluster.local", 443)
    print(json.dumps({"resolved": bool(addresses)}))
    raise SystemExit(0 if addresses else 8)
except OSError as error:
    print(json.dumps({"resolved": False, "error": type(error).__name__}))
    raise SystemExit(7)
"""
MODEL_WRITE_SCRIPT = """\
from pathlib import Path
import sys
Path(sys.argv[1]).write_text("test-only write probe\\n", encoding="utf-8")
print("WRITE_OK")
"""
SECRET_READ_SCRIPT = """\
from pathlib import Path
Path("/opt/serving/secret/read-probe.txt").read_bytes()
print("READ_OK")
"""
EXEC_PREP_SCRIPT = """\
from pathlib import Path
import shutil, sys
target = Path(sys.argv[1])
shutil.copyfile("/bin/true", target)
target.chmod(0o700)
print("EXEC_READY")
"""
PROBE_WRITE_SCRIPT = """\
import errno
from pathlib import Path
try:
    Path("/opt/serving/probe/blocked-probe.txt").write_text("test-only deny probe\\n", encoding="utf-8")
except PermissionError as error:
    if error.errno == errno.EPERM:
        print("EPERM")
        raise SystemExit(13)
    print("PERMISSION_ERROR")
    raise SystemExit(9)
except OSError as error:
    print(type(error).__name__)
    raise SystemExit(9)
print("WRITE_SUCCEEDED")
"""
NORMAL_WRITE_SCRIPT = """\
from pathlib import Path
Path("/opt/serving/cache/normal-write.txt").write_text("test-only normal write\\n", encoding="utf-8")
print("WRITE_OK")
"""


class ContextSafetyError(RuntimeError):
    """The requested/current kubectl context is not the dedicated lab context."""


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class SubprocessRunner:
    """Small injectable subprocess boundary used by cluster-free unit tests."""

    def run(self, argv: Sequence[str], *, timeout: float) -> ProcessResult:
        command = tuple(str(item) for item in argv)
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            return ProcessResult(
                command,
                124,
                _as_text(error.stdout),
                _as_text(error.stderr) or f"timed out after {timeout:g}s",
                True,
            )
        except OSError as error:
            return ProcessResult(command, 127, "", str(error))
        return ProcessResult(command, result.returncode, result.stdout, result.stderr)

    def start(self, argv: Sequence[str], output: BinaryIO) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            tuple(str(item) for item in argv),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
        )


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_text(value: str, *, limit: int = 2000) -> str:
    """Redact common credential shapes and bound report output size."""

    cleaned = _BEARER.sub("Bearer [REDACTED]", value)
    cleaned = _SENSITIVE_VALUE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", cleaned
    )
    if len(cleaned) > limit:
        return cleaned[:limit] + f"\n...[{len(cleaned) - limit} characters omitted]"
    return cleaned


def sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value, limit=10_000)
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize_value(item) for key, item in value.items()}
    return value


def kubectl_command(
    context: str, namespace: str | None, *arguments: str
) -> list[str]:
    command = ["kubectl", "--context", context]
    if namespace is not None:
        command.extend(["--namespace", namespace])
    command.extend(arguments)
    return command


def hubble_observe_command(namespace: str) -> list[str]:
    return [
        "hubble",
        "observe",
        "--server",
        "localhost:4245",
        "--namespace",
        namespace,
        "--verdict",
        "DROPPED",
        "-o",
        "json",
        "--last",
        "50",
    ]


def parse_running_pod(output: str) -> str:
    """Return the first non-terminating Running pod from a kubectl JSON list."""

    try:
        document = json.loads(output)
    except json.JSONDecodeError as error:
        raise ValueError("kubectl pod output is not JSON") from error
    items = document.get("items") if isinstance(document, dict) else None
    if not isinstance(items, list):
        raise ValueError("kubectl pod output has no items array")
    names: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        status = item.get("status")
        if not isinstance(metadata, dict) or not isinstance(status, dict):
            continue
        name = metadata.get("name")
        if (
            isinstance(name, str)
            and status.get("phase") == "Running"
            and metadata.get("deletionTimestamp") is None
        ):
            names.append(name)
    if not names:
        raise ValueError("no Running pod matched the requested label")
    return sorted(names)[0]


def parse_last_json(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("command output has no JSON object")


def load_observations(path: Path) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    if not path.is_file():
        return observations
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            observations.append(item)
    return observations


def export_denylist_blocks_empty_namespace(output: str) -> bool:
    """Return whether tetragon-config drops events without a namespace."""

    try:
        document = json.loads(output)
    except json.JSONDecodeError as error:
        raise ValueError("tetragon-config output is not JSON") from error
    data = document.get("data") if isinstance(document, dict) else None
    raw_denylist = data.get("export-denylist") if isinstance(data, dict) else None
    if raw_denylist is None:
        return False
    if not isinstance(raw_denylist, str):
        raise ValueError("tetragon-config export-denylist is not a string")
    for line_number, line in enumerate(raw_denylist.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rule = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"tetragon-config export-denylist line {line_number} is not JSON"
            ) from error
        namespaces = rule.get("namespace") if isinstance(rule, dict) else None
        if isinstance(namespaces, list) and "" in namespaces:
            return True
    return False


def parse_tracingpolicy_nenforce(output: str, policy_name: str) -> int:
    """Parse one policy's NENFORCE value from ``tetra tracingpolicy list``."""

    lines = [line for line in output.splitlines() if line.strip()]
    for header_index, header_line in enumerate(lines):
        headers = header_line.split()
        if "NAME" not in headers or "NENFORCE" not in headers:
            continue
        offset_from_end = len(headers) - headers.index("NENFORCE")
        for row in lines[header_index + 1 :]:
            fields = row.split()
            if policy_name not in fields or len(fields) < offset_from_end:
                continue
            raw_value = fields[-offset_from_end]
            try:
                return int(raw_value)
            except ValueError as error:
                raise ValueError(
                    f"NENFORCE for {policy_name} is not an integer"
                ) from error
        break
    raise ValueError(f"NENFORCE for {policy_name} was not found")


def classify_probe_results(results: Sequence[ProcessResult]) -> str:
    """Classify repeated override probes without treating mixed results as failure."""

    outcomes: list[str] = []
    for result in results:
        if result.returncode == 13 and "EPERM" in result.stdout:
            outcomes.append("blocked")
        elif result.returncode == 0 and "WRITE_SUCCEEDED" in result.stdout:
            outcomes.append("write_succeeded")
        else:
            outcomes.append("unknown")
    if outcomes and all(outcome == "blocked" for outcome in outcomes):
        return "passed"
    if outcomes and all(outcome == "write_succeeded" for outcome in outcomes):
        return "unsupported"
    if set(outcomes) == {"blocked", "write_succeeded"}:
        return "inconsistent"
    return "failed"


def calculate_overall(statuses: Sequence[str]) -> str:
    """Collapse case statuses into the report's three overall states."""

    if "failed" in statuses:
        return "failed"
    if statuses and all(status == "passed" for status in statuses):
        return "passed"
    return "partial"


def _parse_observed_at(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def matches_tetragon_file_observation(
    item: dict[str, Any],
    *,
    policy_name: str,
    path: str,
    access: str,
    since: str,
) -> bool:
    """Match a file event without relying on Tetragon pod attribution."""

    observed_at = _parse_observed_at(item.get("observed_at"))
    started_at = _parse_observed_at(since)
    return (
        observed_at is not None
        and started_at is not None
        and observed_at >= started_at
        and item.get("source") == "tetragon"
        and item.get("kind") == "file_access"
        and item.get("data_origin") == "real"
        and item.get("policy_name") == policy_name
        and item.get("path") == path
        and item.get("access") == access
    )


def matches_tetragon_exec_observation(
    item: dict[str, Any],
    *,
    binary: str,
    namespace: str,
    since: str,
) -> bool:
    """Match a uniquely named exec only when its pod namespace is attributable."""

    observed_at = _parse_observed_at(item.get("observed_at"))
    started_at = _parse_observed_at(since)
    pod = item.get("pod")
    return (
        observed_at is not None
        and started_at is not None
        and observed_at >= started_at
        and item.get("source") == "tetragon"
        and item.get("kind") == "process_exec"
        and item.get("data_origin") == "real"
        and item.get("binary") == binary
        and isinstance(pod, dict)
        and pod.get("namespace") == namespace
    )


def evaluate_l04_observations(
    observations: Sequence[dict[str, Any]],
    *,
    asset_path: str,
    secret_path: str,
    exec_path: str,
    namespace: str,
    since: str,
) -> dict[str, Any]:
    """Evaluate L-04 without requiring kprobe pod or process attribution."""

    asset_matches = [
        item
        for item in observations
        if matches_tetragon_file_observation(
            item,
            policy_name="runtime-tg-asset-01",
            path=asset_path,
            access="write",
            since=since,
        )
    ]
    secret_matches = [
        item
        for item in observations
        if matches_tetragon_file_observation(
            item,
            policy_name="runtime-tg-secret-01",
            path=secret_path,
            access="read",
            since=since,
        )
    ]
    exec_matches = [
        item
        for item in observations
        if matches_tetragon_exec_observation(
            item,
            binary=exec_path,
            namespace=namespace,
            since=since,
        )
    ]
    file_matches = asset_matches + secret_matches
    return {
        "asset": bool(asset_matches),
        "secret": bool(secret_matches),
        "exec": bool(exec_matches),
        "pod_attribution_unavailable": any(
            item.get("pod_attribution") == "unavailable" for item in file_matches
        ),
        "process_attribution_unavailable": any(
            item.get("process_attribution") == "unavailable"
            for item in file_matches
        ),
    }


def wait_for_local_port(
    host: str, port: int, process: subprocess.Popen[bytes], timeout: float
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _new_case(case_id: str) -> dict[str, Any]:
    return {
        "id": case_id,
        "title": CASE_TITLES[case_id],
        "status": "not_run",
        "summary": "아직 실행하지 않음",
        "commands": [],
        "evidence": [],
        "limitation": None,
        "note": L05_MEASUREMENT_NOTE if case_id == "L-05" else None,
        "remediation": None,
    }


class LabChecker:
    def __init__(
        self,
        *,
        context: str,
        namespace: str,
        state_dir: Path,
        cleanup: bool,
        tetragon_wait: float = DEFAULT_TETRAGON_WAIT,
        runner: SubprocessRunner | None = None,
        repo_root: Path | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        port_waiter: Callable[[str, int, subprocess.Popen[bytes], float], bool] = wait_for_local_port,
    ) -> None:
        self.context = context
        self.namespace = namespace
        self.state_dir = state_dir.resolve()
        self.cleanup_requested = cleanup
        if tetragon_wait < 0:
            raise ValueError("tetragon_wait must be non-negative")
        self.tetragon_wait = tetragon_wait
        self.runner = runner or SubprocessRunner()
        self.repo_root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
        self.sleep = sleeper
        self.port_waiter = port_waiter
        self.intent = self.repo_root / "deploy/lab/intent.lab.json"
        self.rendered_dir = self.state_dir / "rendered"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = f"k8s-lab-{timestamp}-{os.getpid()}"
        self.raw_dir = self.state_dir / "raw" / self.run_id
        self.data_dir = self.state_dir / "data" / self.run_id
        self.observations_path = self.data_dir / "observations.jsonl"
        self.cases = {case_id: _new_case(case_id) for case_id in CASE_TITLES}
        self.pods: dict[str, str] = {}
        self.applied: list[Path] = []
        self.started_at = utc_now()
        self.current_context: str | None = None

    def assert_safe_context(self) -> None:
        if self.context != EXPECTED_CONTEXT:
            raise ContextSafetyError(
                f"--context must be {EXPECTED_CONTEXT!r}; got {self.context!r}"
            )
        result = self.runner.run(
            ["kubectl", "config", "current-context"], timeout=10
        )
        actual = result.stdout.strip()
        if result.returncode != 0:
            raise ContextSafetyError("kubectl current-context could not be read")
        if actual != EXPECTED_CONTEXT:
            raise ContextSafetyError(
                f"current kubectl context must be {EXPECTED_CONTEXT!r}; got {actual!r}"
            )
        self.current_context = actual

    def _record_run(
        self,
        case: dict[str, Any],
        argv: Sequence[str],
        *,
        timeout: float = 30,
        summarize_output: bool = True,
    ) -> ProcessResult:
        result = self.runner.run(argv, timeout=timeout)
        if summarize_output:
            stdout = sanitize_text(result.stdout)
            stderr = sanitize_text(result.stderr)
        else:
            stdout = f"captured separately ({len(result.stdout.encode('utf-8'))} bytes)"
            stderr = sanitize_text(result.stderr)
        case["commands"].append(
            {
                "argv": [str(item) for item in result.argv],
                "returncode": result.returncode,
                "timed_out": result.timed_out,
                "stdout": stdout,
                "stderr": stderr,
            }
        )
        return result

    def _kubectl(
        self,
        case: dict[str, Any],
        *arguments: str,
        namespace: str | None = None,
        timeout: float = 30,
        summarize_output: bool = True,
    ) -> ProcessResult:
        active_namespace = self.namespace if namespace is None else namespace
        return self._record_run(
            case,
            kubectl_command(self.context, active_namespace, *arguments),
            timeout=timeout,
            summarize_output=summarize_output,
        )

    def _runtime(
        self, case: dict[str, Any], *arguments: str, timeout: float = 30
    ) -> ProcessResult:
        return self._record_run(
            case,
            [sys.executable, "-m", "serving_runtime", *arguments],
            timeout=timeout,
        )

    def _pod(self, case: dict[str, Any], label: str) -> str:
        result = self._kubectl(
            case, "get", "pods", "--selector", f"app={label}", "-o", "json"
        )
        if result.returncode != 0:
            raise RuntimeError(f"could not list pod for app={label}")
        return parse_running_pod(result.stdout)

    def _exec(
        self,
        case: dict[str, Any],
        pod: str,
        *arguments: str,
        timeout: float = 15,
    ) -> ProcessResult:
        return self._kubectl(
            case, "exec", pod, "--", *arguments, timeout=timeout
        )

    def _apply(self, case: dict[str, Any], path: Path) -> ProcessResult:
        result = self._kubectl(case, "apply", "-f", str(path), timeout=30)
        if result.returncode == 0 and path not in self.applied:
            self.applied.append(path)
        return result

    def _set(
        self,
        case: dict[str, Any],
        status: str,
        summary: str,
        evidence: Sequence[str] = (),
    ) -> None:
        case["status"] = status
        case["summary"] = summary
        case["evidence"] = [str(item) for item in evidence]

    def _retry(
        self,
        operation: Callable[[], ProcessResult],
        acceptable: Callable[[ProcessResult], bool],
        *,
        attempts: int = 5,
        delay: float = 1,
    ) -> ProcessResult:
        result = operation()
        for _attempt in range(1, attempts):
            if acceptable(result):
                return result
            self.sleep(delay)
            result = operation()
        return result

    @staticmethod
    def _is_http_200(result: ProcessResult) -> bool:
        if result.returncode != 0:
            return False
        try:
            return parse_last_json(result.stdout).get("status") == 200
        except ValueError:
            return False

    def _run_case(self, case_id: str, operation: Callable[[dict[str, Any]], None]) -> bool:
        case = self.cases[case_id]
        try:
            operation(case)
        except Exception as error:  # keep remaining independent cases reportable
            self._set(case, "failed", sanitize_text(str(error)))
        return case["status"] in {"passed", "unsupported"}

    def _check_tetragon_export_denylist(self) -> bool:
        case = self.cases["L-04"]
        result = self._kubectl(
            case,
            "get",
            "configmap",
            "tetragon-config",
            "-o",
            "json",
            namespace="kube-system",
        )
        if result.returncode != 0:
            case["limitation"] = (
                "tetragon-config export-denylist를 읽지 못해 빈 namespace 제외 여부를 "
                "사전 확인하지 못함"
            )
            return False
        try:
            blocked = export_denylist_blocks_empty_namespace(result.stdout)
        except ValueError as error:
            case["limitation"] = str(error)
            return False
        if blocked:
            self._set(
                case,
                "blocked_by_environment",
                "Tetragon pod_info 연결 실패 시 namespace가 빈 이벤트가 현재 "
                "export-denylist에 의해 내보내지지 않아 L-04를 실행할 수 없음",
            )
            case["limitation"] = (
                "tetragon-config export-denylist가 빈 namespace(\"\")를 제외함"
            )
            case["remediation"] = TETRAGON_EXPORT_REMEDIATION
        return blocked

    def _ensure_rendered(self, case: dict[str, Any]) -> bool:
        expected = self.rendered_dir / "tetragon-tg-asset-01.json"
        if expected.is_file():
            return True
        result = self._runtime(
            case,
            "enforce",
            "render",
            "--intent",
            str(self.intent),
            "--out",
            str(self.rendered_dir),
        )
        return result.returncode == 0 and expected.is_file()

    def _gateway_request(self, case: dict[str, Any]) -> ProcessResult:
        return self._exec(
            case,
            self.pods["gateway"],
            "python",
            "-c",
            GATEWAY_POST_SCRIPT,
            "http://127.0.0.1:8080/v1/chat/completions",
        )

    def _direct_engine_request(self, case: dict[str, Any]) -> ProcessResult:
        return self._exec(
            case,
            self.pods["other"],
            "python",
            "-c",
            HTTP_GET_SCRIPT,
            "http://serving-engine.serving-lab.svc.cluster.local:8000/health",
        )

    def _l01(self, case: dict[str, Any]) -> None:
        wait = self._kubectl(
            case,
            "wait",
            "--for=condition=available",
            "deployment/serving-engine",
            "deployment/serving-gateway",
            "deployment/other-client",
            "deployment/metrics-collector",
            "--timeout=120s",
            timeout=130,
        )
        if wait.returncode != 0:
            raise RuntimeError("lab deployments did not become available")
        self.pods = {
            "engine": self._pod(case, "serving-engine"),
            "gateway": self._pod(case, "serving-gateway"),
            "other": self._pod(case, "other-client"),
        }
        direct = self._retry(
            lambda: self._direct_engine_request(case), self._is_http_200
        )
        gateway = self._retry(lambda: self._gateway_request(case), self._is_http_200)
        if self._is_http_200(direct) and self._is_http_200(gateway):
            self._set(case, "passed", "정책 전 엔진 직통과 Gateway 요청이 모두 HTTP 200")
        else:
            self._set(case, "failed", "정책 전 기준선 요청 중 하나 이상이 실패")

    def _l02(self, case: dict[str, Any]) -> None:
        render = self._runtime(
            case,
            "enforce",
            "render",
            "--intent",
            str(self.intent),
            "--out",
            str(self.rendered_dir),
        )
        if render.returncode != 0:
            raise RuntimeError("enforcement manifest rendering failed")
        for name in (
            "cilium-engine-ingress.json",
            "cilium-engine-egress.json",
            "cilium-gateway-egress.json",
        ):
            path = self.rendered_dir / name
            if not path.is_file() or self._apply(case, path).returncode != 0:
                raise RuntimeError(f"could not apply {name}")
        self.sleep(2)
        gateway = self._retry(lambda: self._gateway_request(case), self._is_http_200)
        direct = self._retry(
            lambda: self._direct_engine_request(case),
            lambda result: result.returncode != 0,
        )
        external = self._retry(
            lambda: self._exec(
                case,
                self.pods["engine"],
                "python",
                "-c",
                EXTERNAL_CONNECT_SCRIPT,
            ),
            lambda result: result.returncode != 0,
        )
        dns = self._retry(
            lambda: self._exec(
                case, self.pods["engine"], "python", "-c", DNS_SCRIPT
            ),
            lambda result: result.returncode == 0,
        )
        gateway_ok = self._is_http_200(gateway)
        if (
            gateway_ok
            and direct.returncode != 0
            and external.returncode != 0
            and dns.returncode == 0
        ):
            self._set(
                case,
                "passed",
                "Gateway 경유·DNS는 허용되고 other-client 직통·engine 외부 연결은 차단",
            )
        else:
            self._set(case, "failed", "Cilium 적용 후 허용/차단 결과가 기대와 다름")

    def _l03(self, case: dict[str, Any]) -> None:
        trigger = self._direct_engine_request(case)
        if trigger.returncode == 0:
            raise RuntimeError("DROPPED 흐름 생성용 엔진 직통 요청이 차단되지 않음")
        port_forward_log = self.raw_dir / "cilium-hubble-port-forward.log"
        hubble_path = self.raw_dir / "hubble-dropped.jsonl"
        process: subprocess.Popen[bytes] | None = None
        self.raw_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with port_forward_log.open("wb") as output:
            try:
                command = ["cilium", "hubble", "port-forward"]
                process = self.runner.start(command, output)
                command_record = {
                    "argv": command,
                    "returncode": None,
                    "timed_out": False,
                    "stdout": f"captured separately: {port_forward_log}",
                    "stderr": "",
                }
                case["commands"].append(command_record)
                if not self.port_waiter("127.0.0.1", 4245, process, 15):
                    raise RuntimeError("cilium hubble port-forward did not become ready")
                observed = self._record_run(
                    case,
                    hubble_observe_command(self.namespace),
                    timeout=30,
                    summarize_output=False,
                )
                hubble_path.write_text(observed.stdout, encoding="utf-8")
                if observed.returncode != 0:
                    raise RuntimeError("hubble observe failed")
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                if process is not None:
                    command_record["returncode"] = process.poll()
        ingest = self._runtime(
            case,
            "enforce",
            "ingest",
            "--kind",
            "hubble",
            "--input",
            str(hubble_path),
            "--origin",
            "real",
            "--data-dir",
            str(self.data_dir),
        )
        dropped = [
            item
            for item in load_observations(self.observations_path)
            if item.get("source") == "hubble"
            and item.get("verdict") == "dropped"
            and item.get("data_origin") == "real"
        ]
        if ingest.returncode == 0 and dropped:
            self._set(
                case,
                "passed",
                f"실제 Hubble DROPPED 관측 {len(dropped)}건을 변환",
                [hubble_path, port_forward_log, self.observations_path],
            )
        else:
            self._set(
                case,
                "failed",
                "실제 Hubble DROPPED 관측을 확인하지 못함",
                [hubble_path],
            )

    def _l04(self, case: dict[str, Any]) -> None:
        if not self._ensure_rendered(case):
            raise RuntimeError("Tetragon manifests are unavailable")
        for name in (
            "tetragon-tg-asset-01.json",
            "tetragon-tg-secret-01.json",
            "tetragon-tg-cache-01.json",
        ):
            path = self.rendered_dir / name
            if not path.is_file() or self._apply(case, path).returncode != 0:
                raise RuntimeError(f"could not apply {name}")
        self.sleep(3)
        test_started_at = utc_now()
        asset_path = f"/opt/serving/assets/model/lab-write-{self.run_id}.txt"
        secret_path = "/opt/serving/secret/read-probe.txt"
        exec_path = f"/opt/serving/cache/lab-exec-{self.run_id}"
        write = self._exec(
            case,
            self.pods["engine"],
            "python",
            "-c",
            MODEL_WRITE_SCRIPT,
            asset_path,
        )
        read = self._exec(
            case, self.pods["engine"], "python", "-c", SECRET_READ_SCRIPT
        )
        exec_prep = self._exec(
            case,
            self.pods["engine"],
            "python",
            "-c",
            EXEC_PREP_SCRIPT,
            exec_path,
        )
        outside_exec = self._exec(case, self.pods["engine"], exec_path)
        if any(
            item.returncode != 0 for item in (write, read, exec_prep, outside_exec)
        ):
            raise RuntimeError("Tetragon monitor-mode probe operation failed")
        tetragon_path = self.raw_dir / "tetragon-monitor.jsonl"

        def expected_events(
            observations: Sequence[dict[str, Any]],
        ) -> dict[str, bool]:
            result = evaluate_l04_observations(
                observations,
                asset_path=asset_path,
                secret_path=secret_path,
                exec_path=exec_path,
                namespace=self.namespace,
                since=test_started_at,
            )
            return {
                "asset_write": result["asset"],
                "secret_read": result["secret"],
                "process_exec": result["exec"],
            }

        observations, wait_result = self._collect_tetragon_logs(
            case,
            path=tetragon_path,
            since=test_started_at,
            expected_events=expected_events,
        )
        case["tetragon_wait"] = wait_result
        ingest = self._runtime(
            case,
            "enforce",
            "ingest",
            "--kind",
            "tetragon",
            "--input",
            str(tetragon_path),
            "--origin",
            "real",
            "--data-dir",
            str(self.data_dir),
            "--intent",
            str(self.intent),
            timeout=60,
        )
        result = evaluate_l04_observations(
            observations,
            asset_path=asset_path,
            secret_path=secret_path,
            exec_path=exec_path,
            namespace=self.namespace,
            since=test_started_at,
        )
        unavailable = []
        if result["pod_attribution_unavailable"]:
            unavailable.append("pod")
        if result["process_attribution_unavailable"]:
            unavailable.append("process")
        if unavailable:
            case["limitation"] = (
                f"Tetragon {'·'.join(unavailable)} 귀속 불가 — "
                "kprobe는 policy_name·경로·접근 종류·시각으로 판정"
            )
        asset = result["asset"]
        secret = result["secret"]
        outside = result["exec"]
        if ingest.returncode == 0 and asset and secret and outside:
            self._set(
                case,
                "passed",
                "TG-ASSET-01 write, TG-SECRET-01 read, 허용 목록 밖 exec를 실제 로그에서 확인",
                [tetragon_path, self.observations_path],
            )
        else:
            missing = [
                name
                for name, present in (
                    ("TG-ASSET-01 write", asset),
                    ("TG-SECRET-01 read", secret),
                    ("serving-lab process_exec(고유 실행 파일 경로)", outside),
                )
                if not present
            ]
            self._set(
                case,
                "failed",
                f"Tetragon 관측 누락: {', '.join(missing) or 'ingest failure'}",
                [tetragon_path, self.observations_path],
            )

    def _collect_tetragon_logs(
        self,
        case: dict[str, Any],
        *,
        path: Path,
        since: str,
        expected_events: Callable[
            [Sequence[dict[str, Any]]], dict[str, bool]
        ] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Collect deduplicated export logs until expected events appear or timeout."""

        unique_lines: list[str] = []
        seen_lines: set[str] = set()
        elapsed = 0.0
        first_seen: dict[str, float | None] = {
            name: None for name in (expected_events([]) if expected_events else {})
        }
        observations: list[dict[str, Any]] = []
        collected = False
        while True:
            logs = self._kubectl(
                case,
                "logs",
                "daemonset/tetragon",
                "--container",
                "export-stdout",
                "--since-time",
                since,
                namespace="kube-system",
                timeout=45,
                summarize_output=False,
            )
            if logs.returncode == 0:
                collected = True
                for line in logs.stdout.splitlines():
                    if line and line not in seen_lines:
                        seen_lines.add(line)
                        unique_lines.append(line)
                observations, _rejected = transform_lines(
                    unique_lines,
                    kind="tetragon",
                    data_origin="real",
                )
                present = expected_events(observations) if expected_events else {}
                for name, is_present in present.items():
                    if is_present and first_seen[name] is None:
                        first_seen[name] = elapsed
                if not present or all(present.values()):
                    break
            if elapsed >= self.tetragon_wait:
                break
            delay = min(TETRAGON_POLL_INTERVAL, self.tetragon_wait - elapsed)
            if delay <= 0:
                break
            self.sleep(delay)
            elapsed += delay

        self.raw_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        content = "\n".join(unique_lines)
        path.write_text(content + ("\n" if content else ""), encoding="utf-8")
        if not collected:
            raise RuntimeError("could not collect Tetragon export-stdout logs")
        return observations, {
            "configured_seconds": self.tetragon_wait,
            "elapsed_seconds": elapsed,
            "first_seen_seconds": first_seen,
        }

    def _l05(self, case: dict[str, Any]) -> None:
        if not self._ensure_rendered(case):
            raise RuntimeError("Tetragon deny manifest is unavailable")
        deny_path = self.rendered_dir / "tetragon-tg-deny-01.json"
        if not deny_path.is_file() or self._apply(case, deny_path).returncode != 0:
            raise RuntimeError("could not apply tetragon-tg-deny-01.json")
        self.sleep(3)
        test_started_at = utc_now()
        policy_name = "runtime-tg-deny-01"
        probes: list[ProcessResult] = []
        attempts: list[dict[str, Any]] = []
        for attempt_number in range(1, PROBE_ATTEMPTS + 1):
            before = self._tracingpolicy_nenforce(case, policy_name)
            probe = self._exec(
                case, self.pods["engine"], "python", "-c", PROBE_WRITE_SCRIPT
            )
            probes.append(probe)
            self.sleep(1)
            after = self._tracingpolicy_nenforce(case, policy_name)
            outcome = (
                "blocked"
                if probe.returncode == 13 and "EPERM" in probe.stdout
                else "write_succeeded"
                if probe.returncode == 0 and "WRITE_SUCCEEDED" in probe.stdout
                else "unknown"
            )
            attempts.append(
                {
                    "attempt": attempt_number,
                    "result": outcome,
                    "returncode": probe.returncode,
                    "nenforce_before": before,
                    "nenforce_after": after,
                }
            )
        case["attempts"] = attempts
        normal = self._exec(
            case, self.pods["engine"], "python", "-c", NORMAL_WRITE_SCRIPT
        )
        self.sleep(1)
        probe_log = self.raw_dir / "tetragon-deny-probe.jsonl"
        logs = self._kubectl(
            case,
            "logs",
            "daemonset/tetragon",
            "--container",
            "export-stdout",
            "--since-time",
            test_started_at,
            namespace="kube-system",
            timeout=45,
            summarize_output=False,
        )
        self.raw_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        probe_log.write_text(logs.stdout, encoding="utf-8")
        evidence = [probe_log]
        classification = classify_probe_results(probes)
        counts = ", ".join(
            f"#{item['attempt']} {item['result']} "
            f"NENFORCE {item['nenforce_before']}→{item['nenforce_after']}"
            for item in attempts
        )
        if any(
            item["nenforce_before"] is None or item["nenforce_after"] is None
            for item in attempts
        ):
            case["limitation"] = (
                "일부 시도의 tetra tracingpolicy list NENFORCE 값을 읽지 못함"
            )
        if logs.returncode != 0:
            self._set(
                case,
                "failed",
                "probe 결과의 Tetragon 근거 로그를 수집하지 못함",
                evidence,
            )
        elif normal.returncode != 0 or "WRITE_OK" not in normal.stdout:
            self._set(case, "failed", "probe 이외 정상 파일 쓰기에도 영향이 발생", evidence)
        elif classification == "passed":
            self._set(
                case,
                "passed",
                f"probe 쓰기 {PROBE_ATTEMPTS}회 모두 EPERM, 정상 쓰기는 성공; {counts}",
                evidence,
            )
        elif classification == "unsupported":
            self._set(
                case,
                "unsupported",
                f"probe 쓰기 {PROBE_ATTEMPTS}회 모두 성공: 현재 커널/Tetragon에서 "
                f"Override 미지원 또는 미동작; {counts}",
                evidence,
            )
        elif classification == "inconsistent":
            self._set(
                case,
                "inconsistent",
                f"probe 차단 결과가 시도마다 다름; 정상 쓰기는 성공; {counts}",
                evidence,
            )
        else:
            self._set(
                case,
                "failed",
                f"probe 결과 중 EPERM 또는 성공으로 판정되지 않은 시도가 있음; {counts}",
                evidence,
            )

    def _tracingpolicy_nenforce(
        self, case: dict[str, Any], policy_name: str
    ) -> int | None:
        result = self._kubectl(
            case,
            "exec",
            "daemonset/tetragon",
            "--container",
            "tetragon",
            "--",
            "tetra",
            "tracingpolicy",
            "list",
            namespace="kube-system",
        )
        if result.returncode != 0:
            return None
        try:
            return parse_tracingpolicy_nenforce(result.stdout, policy_name)
        except ValueError:
            return None

    def _l06(self, case: dict[str, Any]) -> None:
        if not self.cleanup_requested:
            self._set(case, "not_run", "--cleanup 미지정: 적용한 정책을 클러스터에 유지")
            return
        failures: list[str] = []
        for path in reversed(self.applied):
            result = self._kubectl(
                case,
                "delete",
                "-f",
                str(path),
                "--ignore-not-found=true",
                timeout=30,
            )
            if result.returncode != 0:
                failures.append(path.name)
        if failures:
            self._set(case, "failed", f"정리 실패: {', '.join(failures)}")
        else:
            self._set(case, "passed", f"이번 실행에서 적용한 정책 {len(self.applied)}개 정리")

    def run(self) -> tuple[dict[str, Any], tuple[Path, Path]]:
        self.assert_safe_context()
        self.rendered_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.raw_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        l04_blocked = self._check_tetragon_export_denylist()
        baseline_ok = self._run_case("L-01", self._l01)
        if baseline_ok:
            cilium_ok = self._run_case("L-02", self._l02)
            if cilium_ok:
                self._run_case("L-03", self._l03)
            else:
                self._set(self.cases["L-03"], "not_run", "L-02 실패로 Hubble 증거 확인 생략")
            if not l04_blocked:
                self._run_case("L-04", self._l04)
            self._run_case("L-05", self._l05)
        else:
            for case_id in ("L-02", "L-03", "L-05"):
                self._set(self.cases[case_id], "not_run", "L-01 기준선 실패로 후속 정책 적용 생략")
            if not l04_blocked:
                self._set(
                    self.cases["L-04"],
                    "not_run",
                    "L-01 기준선 실패로 후속 정책 적용 생략",
                )
        self._run_case("L-06", self._l06)
        statuses = [item["status"] for item in self.cases.values()]
        overall = calculate_overall(statuses)
        report = {
            "schema": REPORT_SCHEMA,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": utc_now(),
            "context": self.current_context,
            "namespace": self.namespace,
            "cleanup_requested": self.cleanup_requested,
            "overall": overall,
            "cases": list(self.cases.values()),
        }
        paths = write_reports(report, self.state_dir)
        return report, paths


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# serving-runtime Kubernetes lab 점검",
        "",
        f"- 실행 ID: `{report['run_id']}`",
        f"- context: `{report['context']}`",
        f"- namespace: `{report['namespace']}`",
        f"- 전체 결과: **{report['overall']}**",
        "",
    ]
    for case in report["cases"]:
        lines.extend(
            [
                f"## {case['id']} — {case['title']}",
                "",
                f"- 상태: **{case['status']}**",
                f"- 요약: {case['summary']}",
            ]
        )
        if case["evidence"]:
            lines.append("- 증거 파일:")
            lines.extend(f"  - `{item}`" for item in case["evidence"])
        if case.get("limitation"):
            lines.append(f"- 제한: {case['limitation']}")
        if case.get("note"):
            lines.append(f"- 참고: {case['note']}")
        if case.get("remediation"):
            lines.append(f"- 해결: `{case['remediation']}`")
        if case.get("attempts"):
            lines.append("- 반복 시도:")
            for attempt in case["attempts"]:
                lines.append(
                    f"  - #{attempt['attempt']}: {attempt['result']}, "
                    f"NENFORCE {attempt['nenforce_before']}→{attempt['nenforce_after']}"
                )
        if case.get("tetragon_wait"):
            wait = case["tetragon_wait"]
            lines.append(
                f"- Tetragon 최종 대기: {wait['elapsed_seconds']:g}초 "
                f"(최대 {wait['configured_seconds']:g}초)"
            )
            first_seen = wait.get("first_seen_seconds", {})
            for name, seconds in first_seen.items():
                rendered = "미관측" if seconds is None else f"{seconds:g}초"
                lines.append(f"  - {name} 최초 관측: {rendered}")
        if case["commands"]:
            lines.extend(["", "명령 요약:", ""])
            for command in case["commands"]:
                rendered = shlex.join(str(item) for item in command["argv"])
                lines.append(f"- `{sanitize_text(rendered, limit=500)}` → rc={command['returncode']}")
                if command.get("stdout"):
                    lines.append(f"  - stdout: `{sanitize_text(str(command['stdout']), limit=300)}`")
                if command.get("stderr"):
                    lines.append(f"  - stderr: `{sanitize_text(str(command['stderr']), limit=300)}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_reports(report: dict[str, Any], state_dir: Path) -> tuple[Path, Path]:
    safe_report = sanitize_value(report)
    reports_dir = state_dir.resolve() / "reports"
    reports_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stem = str(safe_report["run_id"])
    json_path = reports_dir / f"{stem}.json"
    markdown_path = reports_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(safe_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(report_markdown(safe_report), encoding="utf-8")
    json_path.chmod(0o600)
    markdown_path.chmod(0o600)
    return json_path, markdown_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="kind runtime-lab에서 Cilium·Hubble·Tetragon lab 사례를 점검합니다."
    )
    parser.add_argument("--context", default=EXPECTED_CONTEXT)
    parser.add_argument("--namespace", default="serving-lab")
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument(
        "--tetragon-wait",
        type=float,
        default=DEFAULT_TETRAGON_WAIT,
        help="Tetragon 기대 이벤트를 기다릴 최대 초 (기본: 90)",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="이번 실행에서 적용한 Cilium/Tetragon 정책을 보고서 작성 전에 삭제",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    checker = LabChecker(
        context=args.context,
        namespace=args.namespace,
        state_dir=args.state_dir,
        cleanup=args.cleanup,
        tetragon_wait=args.tetragon_wait,
    )
    try:
        report, paths = checker.run()
    except ContextSafetyError as error:
        print(f"안전 중단: {error}", file=sys.stderr)
        return 2
    print(f"결과: {report['overall']}")
    print(f"JSON 보고서: {paths[0]}")
    print(f"Markdown 보고서: {paths[1]}")
    return 0 if report["overall"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
