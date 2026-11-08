"""AWS GPU lab acceptance checks for the already-installed runtime stack.

This tool is deliberately separate from ``k8s_lab_check.py``. Disruptive
comparison and recovery cases require explicit selection and opt-in flags.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from ipaddress import ip_address
import json
import shlex
import math
import os
from pathlib import Path
import re
import shutil
import socket
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
from serving_runtime.enforcement.execpolicy import ExecPolicy
try:
    from tools.tetragon_mode import switch_mode
except ModuleNotFoundError:  # direct script execution
    from tetragon_mode import switch_mode


CASE_IDS = tuple(f"AWS-{number:02d}" for number in range(1, 21))
FIXED_REQUESTS = (
    ("/v1/chat/completions", {"model": "test-model", "messages": [{"role": "user", "content": "Say hello in one short sentence."}], "max_tokens": 8}),
    ("/v1/completions", {"model": "test-model", "prompt": "Say hello in one short sentence.", "max_tokens": 8}),
)
COUNT_TOKENS_CODE = (
    "import json,sys,urllib.request; "
    "req=urllib.request.Request('http://127.0.0.1:8000/tokenize', "
    "data=sys.stdin.buffer.read(), headers={'Content-Type':'application/json'}, method='POST'); "
    "print(json.load(urllib.request.urlopen(req,timeout=20)).get('count'))"
)


def _fixed_request_hash() -> str:
    encoded = json.dumps(FIXED_REQUESTS, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _completion_usage(response: HTTPResponse) -> int | None:
    document = _json_object(response.body)
    usage = document.get("usage") if document else None
    value = usage.get("completion_tokens") if isinstance(usage, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
CASE_TITLES = {
    "AWS-01": "정상",
    "AWS-02": "인증 거부",
    "AWS-03": "토큰 상한",
    "AWS-04": "직통 거부",
    "AWS-05": "금지 송신",
    "AWS-06": "RO 쓰기 거부",
    "AWS-07": "수집 중단",
    "AWS-08": "Pod 교체",
    "AWS-09": "실행 탐지",
    "AWS-10": "보호 파일 읽기",
    "AWS-11": "정상 동작 대조",
    "AWS-12": "규칙 적용 전후 비교",
    "AWS-13": "수동 적용 실패와 되돌리기",
    "AWS-14": "Gateway 재시작 직후 503",
    "AWS-15": "수집 중단 중 Gateway 재시작",
    "AWS-16": "수집 중단 시 단계 만료",
    "AWS-17": "Pod 교체 시 단계 초기화",
    "AWS-18": "동일 요청 목록",
    "AWS-19": "미지원 입구와 필드 거부",
    "AWS-20": "보호 파일 읽기 차단",
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
    "AWS-09": ["RT-SYS-01", "RT-SYS-02"],
    "AWS-10": ["RT-SYS-05"],
    "AWS-11": ["RT-API-01", "RT-SYS-02", "RT-ASSET-05"],
    "AWS-12": ["RT-NET-02"],
    "AWS-13": ["RT-OPT-04"],
    "AWS-14": ["RT-OBS-01"],
    "AWS-15": ["RT-OBS-01"],
    "AWS-16": ["RT-PHASE-01"],
    "AWS-17": ["RT-PHASE-01"],
    "AWS-18": ["RT-API-01", "RT-OPT-01"],
    "AWS-19": ["RT-API-03", "RT-OPT-04"],
    "AWS-20": ["RT-SYS-05"],
}
RESULTS = frozenset({"passed", "failed", "not_run", "unknown"})
_NAMESPACE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,256}")
_OBSERVATION_SOURCES = ("observation_feed_hubble", "observation_feed_tetragon")
_EXTERNAL_TEST_IP = "192.0.2.1"
_IMDS_IP = "169.254.169.254"
_MODEL = "test-model"
_SECRET_PROBE_PATH = "/run/byul-probe/secret.txt"
_DEFAULT_CASES = CASE_IDS[:11]
_COMPARE_PODS = ("runtime-compare-client", "runtime-compare-receiver")
_COMPARE_POLICY = "runtime-compare-ingress"
_DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "gpu-lab"
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
PROBE_READ_CODE = (
    "import pathlib,sys; pathlib.Path(sys.argv[1]).read_bytes(); print('READ_OK')"
)
PROBE_READ_ERRNO_CODE = (
    "import errno,json,pathlib,sys\n"
    "try: pathlib.Path(sys.argv[1]).read_bytes()\n"
    "except OSError as error:\n"
    " print(json.dumps({'errno':error.errno,'name':errno.errorcode.get(error.errno)})); sys.exit(13 if error.errno in (errno.EPERM,errno.EACCES) else 9)\n"
    "print(json.dumps({'read':'ok'}))"
)
ROUTE_RECORD_PROBE_CODE = (
    "import json,sys; p=sys.argv[1]; rid=sys.argv[2]; match=None\n"
    "for line in open(p):\n"
    " try:\n  row=json.loads(line)\n except ValueError:\n  continue\n"
    " if row.get('request_id')==rid: match=row\n"
    "print(json.dumps({'found':bool(match),'reason':match.get('reason') if match else None,'response_code':match.get('response_code') if match else None,'forwarded_to_engine':match.get('forwarded_to_engine') if match else None}))"
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

    def run(self, argv: Sequence[str], *, timeout: float = 30, input: str | None = None) -> CommandResult:
        started = _now()
        try:
            completed = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                input=input,
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

    def start_port_forward(self, argv: Sequence[str]) -> subprocess.Popen[bytes]:
        return subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, shell=False)


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


def _timeline_reset(before: dict[str, Any], probe: dict[str, Any] | None, started_at: object) -> bool:
    started = _parse_time(started_at)
    previous = before.get("entries")
    current = probe.get("entries") if isinstance(probe, dict) else None
    if started is None or not isinstance(previous, list) or not previous or not isinstance(current, list) or not current:
        return False
    old_times = [_parse_time(row.get("from")) if isinstance(row, dict) else None for row in previous]
    new_times = [_parse_time(row.get("from")) if isinstance(row, dict) else None for row in current]
    if any(value is None for value in old_times + new_times):
        return False
    old_last = max(old_times)
    return all(value >= started and value > old_last for value in new_times)


def _after(value: object, since: str) -> bool:
    observed = _parse_time(value)
    boundary = _parse_time(since)
    return observed is not None and boundary is not None and observed >= boundary


def _aws20_method(document: object, *, namespace: str) -> str | None:
    """Recognize only the reviewed 1.7.1 monitor-mode action candidates."""
    if not isinstance(document, dict) or document.get("kind") != "TracingPolicyNamespaced":
        return None
    metadata = document.get("metadata") or {}
    spec = document.get("spec") or {}
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        return None
    if metadata.get("name") != "runtime-tg-secret-01" or metadata.get("namespace") != namespace or spec.get("options") != [{"name": "policy-mode", "value": "monitor"}]:
        return None
    pod_selector = spec.get("podSelector")
    if not isinstance(pod_selector, dict) or not isinstance(pod_selector.get("matchLabels"), dict) or not pod_selector["matchLabels"]:
        return None
    annotations = metadata.get("annotations") or {}
    method = annotations.get("serving-runtime.local/enforce-method") if isinstance(annotations, dict) else None
    probes = spec.get("lsmhooks") if method == "lsm_file_open" else spec.get("kprobes")
    if (method == "lsm_file_open" and spec.get("kprobes")) or (method != "lsm_file_open" and spec.get("lsmhooks")):
        return None
    if method not in {"lsm_file_open", "override_lsm", "sigkill_fd_install", "override_syscall"} or not isinstance(probes, list) or len(probes) != 1:
        return None
    probe = probes[0]
    selectors = probe.get("selectors") if isinstance(probe, dict) else None
    if not isinstance(selectors, list) or len(selectors) != 1:
        return None
    selector = selectors[0]
    actions = selector.get("matchActions") if isinstance(selector, dict) else None
    expected = ([{"action": "Sigkill"}] if method == "sigkill_fd_install" else
                [{"action": "Post"}, {"action": "Override", "argError": -1}, {"action": "Sigkill"}])
    override_only = [{"action": "Post"}, {"action": "Override", "argError": -13}]
    if actions != expected and not (method in {"lsm_file_open", "override_syscall"} and actions == override_only):
        return None
    if method == "lsm_file_open" and actions != override_only:
        return None
    call = probe.get("hook") if method == "lsm_file_open" else probe.get("call")
    if call != {"override_lsm": "security_file_permission", "sigkill_fd_install": "fd_install",
                "override_syscall": "sys_openat", "lsm_file_open": "file_open"}[method]:
        return None
    args = selector.get("matchArgs")
    if not isinstance(args, list) or not args:
        return None
    file_arg = args[0]
    path = _SECRET_PROBE_PATH + ("\x00" if method == "override_syscall" else "")
    if not isinstance(file_arg, dict) or file_arg.get("operator") != "Equal" or file_arg.get("values") != [path]:
        return None
    if method == "override_lsm" and (len(args) != 2 or args[1] != {"index": 1, "operator": "Equal", "values": ["4"]}):
        return None
    if method == "lsm_file_open" and (len(args) != 1 or file_arg.get("index") != 0
                                      or probe.get("args") != [{"index": 0, "type": "file"}]):
        return None
    return method


def _aws20_raw_action(path: Path, *, since: str, pod_uid: str, namespace: str,
                      action: str, method: str, probe_tag: str | None = None) -> bool:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    root = json.loads(line)
                    event = root.get("process_lsm" if method == "lsm_file_open" else "process_kprobe") or {}
                    pod = (event.get("process") or {}).get("pod") or {}
                    arguments = (event.get("process") or {}).get("arguments", "")
                    args = event.get("args") or []
                    if (_after(root.get("time"), since)
                        and event.get("policy_name") == "runtime-tg-secret-01"
                        and event.get("action") == action
                        and pod.get("uid") == pod_uid and pod.get("namespace") == namespace
                        and (probe_tag is None or probe_tag in shlex.split(arguments))
                        and any(isinstance(arg, dict) and ((arg.get("file_arg") or {}).get("path") == _SECRET_PROBE_PATH
                                                           or str(arg.get("string_arg", "")).rstrip("\x00") == _SECRET_PROBE_PATH) for arg in args)
                        and (method != "override_lsm" or any(isinstance(arg, dict) and str(arg.get("int_arg")) == "4" for arg in args))):
                        return True
                except (ValueError, TypeError, AttributeError):
                    continue
    except (OSError, UnicodeError):
        return False
    return False


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


def _stream_final_success(record: dict[str, Any] | None) -> bool | None:
    if record is None:
        return None
    delivery = record["delivery"]
    return (record["result"] == "ALLOWED"
            and record["final_outcome"] == "completed"
            and delivery["attempted"] is True
            and delivery["response"] == "completed"
            and delivery["error_code"] is None)


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
    if case_id in {"AWS-12", "AWS-13", "AWS-15", "AWS-16", "AWS-20"} and facts.get("cleanup_ok") is False:
        return "failed", "시험 뒤 정리 또는 복구 실패"
    if facts.get("not_run_reason"):
        return "not_run", str(facts["not_run_reason"])
    if facts.get("unknown_reason"):
        return "unknown", str(facts["unknown_reason"])
    if case_id == "AWS-20":
        method = facts.get("enforce_method")
        if method not in {"lsm_file_open", "override_lsm", "override_syscall", "sigkill_fd_install"}:
            return "unknown", "차단 방식을 확인하지 못함"
        if method == "sigkill_fd_install":
            required = ["enforce_active", "monitor_read_ok", "exit_sigkill", "raw_sigkill", "cleanup_ok", "restore_read_ok"]
            if facts.get("engine_target") is True:
                required += ["gateway_503", "recovered_200", "engine_restarted"]
            else:
                required += ["normal_200"]
        else:
            required = ["enforce_active", "monitor_read_ok", "read_eperm", "raw_override", "normal_200", "cleanup_ok", "restore_read_ok"]
        if any(facts.get(name) is False for name in required):
            return "failed", "차단·정상 요청 또는 monitor 복귀 결과 불일치"
        missing = [name for name in required if facts.get(name) is not True]
        if missing:
            return "unknown", "판정 근거 부족: " + ", ".join(missing)
        return "passed", ("Sigkill 종료·동일 Pod UID 이벤트·monitor 복귀 확인" if method == "sigkill_fd_install"
                          else "가짜 파일 EPERM/EACCES·동일 Pod UID Override·정상 200·monitor 복귀 확인")
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
            ("stop_ok", "heartbeat_stopped_seen", "collectors_unavailable", "stopped_request_200_unverified", "start_ok", "collectors_recovered", "target_recovered", "inference_recovered"),
            ("stop_ok", "heartbeat_stopped_seen", "collectors_unavailable", "stopped_request_200_unverified", "start_ok", "collectors_recovered", "target_recovered", "inference_recovered"),
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
        "AWS-09": (
            ("absolute_exec_ok", "path_exec_ok", "path_observed", "engine_pod_match", "suspect_verdict"),
            ("absolute_exec_ok", "engine_pod_match", "suspect_verdict"),
        ),
        "AWS-10": (
            ("read_ok", "path_match", "engine_pod_match", "read_access", "monitor_detected"),
            ("read_ok", "path_match", "engine_pod_match", "read_access", "monitor_detected"),
        ),
        "AWS-11": (
            ("normal_before", "normal_after", "stream_before", "stream_after", "cache_current_before", "cache_current_after", "false_warnings_zero"),
            ("normal_before", "normal_after", "stream_before", "stream_after", "cache_current_before", "cache_current_after", "false_warnings_zero"),
        ),
        "AWS-12": (("baseline_connected", "policy_timeout", "hubble_dropped", "restored_connected", "cleanup_ok"),
                   ("restored_connected", "cleanup_ok")),
        "AWS-13": (("bad_bundle_rejected", "recovered_200", "recovered_denied", "cleanup_ok"),
                   ("bad_bundle_rejected", "recovered_200", "recovered_denied", "cleanup_ok")),
        "AWS-14": (("restart_ok", "initial_503", "recovered_200", "within_10s"),
                   ("restart_ok", "recovered_200", "within_10s")),
        "AWS-15": (("stop_ok", "restart_ok", "observed_503", "recovered_200", "cleanup_ok"),
                   ("restart_ok", "observed_503", "recovered_200", "cleanup_ok")),
        "AWS-16": (("stop_ok", "expired_unknown", "request_200", "cleanup_ok"),
                   ("expired_unknown", "request_200", "cleanup_ok")),
        "AWS-17": (("pod_changed", "timeline_new_uid", "timeline_reset", "old_phase_absent"),
                   ("pod_changed", "timeline_new_uid", "timeline_reset", "old_phase_absent")),
        "AWS-18": (("normal", "stream", "usage", "log_match", "context_boundary", "context_exceeded"),
                   ("normal", "stream", "usage", "context_boundary", "context_exceeded")),
        "AWS-19": (("codes", "reasons", "records", "not_delivered", "engine_log_absent", "positive_control"),
                   ("codes", "reasons", "records", "not_delivered", "positive_control")),
    }
    required, decisive = checks[case_id]
    if case_id == "AWS-12" and facts.get("baseline_connected") is False:
        return "unknown", "규칙 적용 전 이미 연결이 막혀 비교 불가"
    if case_id == "AWS-12" and facts.get("policy_other_error"):
        return "unknown", "규칙 적용 중 시간 초과 아닌 연결 오류"
    if case_id == "AWS-12" and facts.get("policy_connected"):
        return "failed", "비교 정책 적용 중 연결이 성공함"
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
        return _DEFAULT_CASES
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


def _report_at(value: object, *path: str) -> object:
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _report_value(value: object, *, allowed: set[str] | None = None) -> str:
    """Render only known, short result values; never copy arbitrary evidence text."""
    if value is True:
        return "예"
    if value is False:
        return "아니요"
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 599:
        return str(value)
    if isinstance(value, str) and allowed is not None and value in allowed:
        return value
    return "확인 못 함"


def _report_code(value: object) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value):
        return value
    return "확인 못 함"


def _report_probe_value(value: object) -> str:
    if isinstance(value, str) and (value in {"/bin/sh", "/usr/bin/sh", "/bin/dash", "/usr/bin/dash", "suspect_binary"} or _parse_time(value) is not None):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return str(value)
    return "확인 못 함"


def _report_binary_path(value: object) -> str:
    if isinstance(value, str) and len(value) <= 256 and re.fullmatch(r"/[A-Za-z0-9_./-]+", value):
        return value
    return "확인 못 함"


def _report_summary(case: Mapping[str, Any]) -> str:
    case_id = case.get("case_id")
    facts = case.get("facts") or {}
    evidence = case.get("evidence") or {}
    at = lambda *path: _report_at(evidence, *path)
    fact = lambda name: _report_value(_report_at(facts, name))
    status = lambda value: _report_value(value) if isinstance(value, int) else "확인 못 함"
    state = lambda value, choices: _report_value(value, allowed=set(choices))
    if case_id == "AWS-01":
        codes = at("http_codes")
        chat_status = next((code for code in codes if isinstance(code, int)), None) if isinstance(codes, list) else None
        return (f"일반 HTTP {status(chat_status)}, stream HTTP {status(at('stream', 'http_status'))}; "
                f"SSE data {state(at('stream', 'sse_data_received'), ())}, [DONE] {state(at('stream', 'done_received'), ())}; "
                f"record ALLOWED {fact('record_allowed')}, 엔진 로그 일치 {fact('engine_log_match')}")
    if case_id == "AWS-02":
        attempts = at("attempts")
        parts = []
        for kind in ("missing", "invalid"):
            item = next((entry for entry in attempts if isinstance(entry, Mapping) and entry.get("kind") == kind), {}) if isinstance(attempts, list) else {}
            parts.append(f"{kind} HTTP {status(item.get('status'))}/{_report_code(item.get('error_code'))}")
        return ", ".join(parts) + f"; 미전달 {fact('not_delivered')}, 엔진 로그 부재 {fact('engine_log_absent')}"
    if case_id == "AWS-03":
        limit = at("policy_limit")
        limit_text = str(limit) if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0 else "확인 못 함"
        return (f"상한 {limit_text}토큰, 상한 요청 HTTP {status(at('boundary', 'http_status'))}, "
                f"초과 요청 403 {fact('http_403')}; reason_code {_report_code(at('record_fields_used', 'reason_code'))}, "
                f"MAX_TOKENS_EXCEEDED 일치 {fact('reason_code')}, 미전달 {fact('not_delivered')}")
    if case_id == "AWS-04":
        return (f"직통 연결 {_report_code(at('direct_probe', 'errno_name'))}, "
                f"Gateway 앞/뒤 연결 {fact('gateway_tcp_control')}, 요청 200 {fact('gateway_control_200')}, "
                f"Hubble dropped {fact('hubble_dropped')}")
    if case_id == "AWS-05":
        return (f"engine→receiver 연결 {_report_code(at('engine_receiver_probe', 'errno_name'))}, "
                f"other-client 대조 연결 {fact('receiver_control_connected')}, Hubble dropped {fact('receiver_hubble_dropped')}")
    if case_id == "AWS-06":
        return (f"/models 쓰기 {_report_code(at('model_probe', 'errno_name'))}, "
                f"/cache 쓰기·삭제 {state(at('cache_probe', 'marker'), ('CACHE_WRITE_REMOVED',))}, "
                f"TG-CACHE-01 기록 {fact('cache_tetragon')}")
    if case_id == "AWS-07":
        return (f"중단 중 요청 HTTP {status(at('stopped_interval_request', 'http_status'))}, "
                f"대상 상태 {state(at('stopped_target_verification', 'state'), ('ok', 'unavailable', 'blocked'))}; "
                f"복구 대상 {state(at('recovered_target_verification', 'state'), ('ok', 'unavailable', 'blocked'))}, "
                f"요청 HTTP {status(at('recovery_request', 'last_status'))}, 추론 회복 {fact('inference_recovered')}")
    if case_id == "AWS-08":
        return (f"Pod 교체 {fact('pod_changed')}, Gateway instance 변경 {fact('gateway_instance_changed')}, "
                f"engine_info {state(at('engine_info', 'state'), ('ok', 'error', 'stale'))}, "
                f"복구 요청 HTTP {status(at('recovery_request', 'last_status'))}, 추론 회복 {fact('gateway_recovered')}")
    if case_id == "AWS-09":
        return (f"절대 경로 /bin/sh 실행 {fact('absolute_exec_ok')}, PATH 탐색 실행 {fact('path_exec_ok')}; "
                f"관측 경로 {_report_probe_value(at('absolute', 'binary'))}, Pod 귀속 {fact('engine_pod_match')}, "
                f"판정 {_report_probe_value(at('absolute', 'exec_verdict'))}, 시각 {_report_probe_value(at('absolute', 'observed_at'))}, "
                f"탐지 시간 {_report_probe_value(at('absolute', 'detection_delay_s'))}초; "
                f"PATH 실행 기록 경로 {_report_probe_value(at('path_lookup', 'binary'))}, 판정 {_report_probe_value(at('path_lookup', 'exec_verdict'))}")
    if case_id == "AWS-10":
        return (f"가짜 파일 읽기 {fact('read_ok')}, 경로 {fact('path_match')}, Pod 귀속 {fact('engine_pod_match')}, "
                f"기록 경로 {_report_binary_path(at('observation', 'path'))}, 시각 {_report_probe_value(at('observation', 'observed_at'))}, "
                f"read {fact('read_access')}, monitor 탐지 {fact('monitor_detected')}, "
                f"탐지 시간 {_report_probe_value(at('observation', 'detection_delay_s'))}초")
    if case_id == "AWS-20":
        return (f"enforce 적용 {fact('enforce_active')}, 가짜 파일 EPERM {fact('read_eperm')}, "
                f"동일 Pod UID Override {fact('raw_override')}, 정상 요청 200 {fact('normal_200')}, "
                f"monitor 복원 {fact('cleanup_ok')}, 복원 뒤 읽기 {fact('restore_read_ok')}")
    if case_id == "AWS-11":
        return (f"전후 정상 요청 {fact('normal_before')}/{fact('normal_after')}, stream {fact('stream_before')}/{fact('stream_after')}, "
                f"현재 cache {fact('cache_current_before')}/{fact('cache_current_after')}, 잘못된 경고 {_report_probe_value(at('false_warning_count'))}건, "
                f"일반 응답 시간 전/후 {_report_probe_value(at('before', 'response_time_ms', 'normal'))}/{_report_probe_value(at('after', 'response_time_ms', 'normal'))}ms, "
                f"stream 전/후 {_report_probe_value(at('before', 'response_time_ms', 'stream'))}/{_report_probe_value(at('after', 'response_time_ms', 'stream'))}ms (참고값)")
    return "확인 못 함"


@dataclass(frozen=True)
class _ReportCase:
    criteria: str
    gap: str
    required: tuple[str, ...]


REPORT_CASES: dict[str, _ReportCase] = {
    "AWS-01": _ReportCase("일반·stream 200과 유효 응답, ALLOWED·전달 시도, SSE 완료와 엔진 로그 일치를 확인한다.", "엔진 로그 형식이 다르면 수신 여부를 확인할 수 없다.", ("http_200", "valid_completion", "record_allowed", "delivery_attempted", "engine_log_match", "stream_ok")),
    "AWS-02": _ReportCase("인증 없음·오류가 각각 401과 AUTH_REQUIRED·AUTH_INVALID이고, DENIED·미전달·엔진 로그 부재를 확인한다.", "엔진 로그 부재는 AWS-01에서 양성 로그 형식을 확인해야 해석할 수 있다.", ("auth_codes", "records_denied", "not_delivered", "engine_log_absent", "engine_log_format_confirmed")),
    "AWS-03": _ReportCase("상한 요청은 허용되고 상한+1은 403 MAX_TOKENS_EXCEEDED·미전달·엔진 로그 부재여야 한다.", "엔진 로그 부재는 AWS-01에서 양성 로그 형식을 확인해야 해석할 수 있다.", ("policy_limit_read", "boundary_allowed", "http_403", "reason_code", "not_delivered", "engine_log_absent", "engine_log_format_confirmed")),
    "AWS-04": _ReportCase("직통 연결은 시간 초과 errno, Gateway 경로는 앞뒤 연결과 요청이 성공하고 해당 Hubble 흐름은 dropped여야 한다.", "ECONNREFUSED 등 다른 errno나 Hubble 단독 기록은 정책 차단 증거가 아니다.", ("direct_failed", "gateway_tcp_control", "gateway_control_200", "hubble_dropped")),
    "AWS-05": _ReportCase("engine→receiver는 시간 초과 errno와 Hubble dropped, other-client→receiver는 연결 성공이어야 한다.", "다른 errno나 허용 경로 대조 실패는 정책 차단 증거가 아니다.", ("engine_receiver_failed", "receiver_control_connected", "receiver_hubble_dropped")),
    "AWS-06": _ReportCase("/models 쓰기는 EROFS, /cache 쓰기·삭제는 성공하고 TG-CACHE-01에 기록돼야 한다.", "EPERM·EACCES는 읽기 전용 mount 증거가 아니며 TG-ASSET-01은 선행 거부 시 안 보일 수 있다.", ("model_write_denied", "cache_write_succeeded", "cache_harmless_removed", "cache_tetragon")),
    "AWS-07": _ReportCase("중단 heartbeat·collector 비정상, stale 이후 요청 200·대상 unavailable, 재시작 뒤 collector·대상·추론 회복을 확인한다.", "정상 종료의 gap 기록은 보조 증거이며 없어도 판정할 수 있다.", ("stop_ok", "heartbeat_stopped_seen", "collectors_unavailable", "stopped_request_200_unverified", "start_ok", "collectors_recovered", "target_recovered", "inference_recovered")),
    "AWS-08": _ReportCase("재시작 뒤 Pod·Gateway instance 변경, engine_info ok와 유한 창 내 추론 회복을 확인한다.", "Pod snapshot stale은 별도 메모이며 통과 근거가 아니다.", ("restart_ok", "pod_changed", "gateway_recovered", "engine_info_recovered", "gateway_instance_changed")),
    "AWS-09": _ReportCase("엔진 Pod의 /bin/sh -c true 실행이 해당 Pod·시각·경로의 관측 기록과 연결되고 suspect_binary여야 한다. PATH 탐색 sh -c true도 실행해 실제 기록 경로와 판정을 남긴다.", "인자를 수집하지 않으므로 동일 Pod·경로·시각은 강한 대조이나 단일 프로세스 ID 증명은 아니다.", ("absolute_exec_ok", "path_exec_ok", "path_observed", "engine_pod_match", "suspect_verdict")),
    "AWS-10": _ReportCase("가짜 파일 읽기 성공과 TG-SECRET-01의 동일 경로·엔진 Pod·read·monitor 탐지를 확인한다.", "읽기 차단은 요구하지 않으며 파일 내용은 보고하지 않는다.", ("read_ok", "path_match", "engine_pod_match", "read_access", "monitor_detected")),
    "AWS-11": _ReportCase("AWS-09·10 앞뒤의 일반 요청·stream·현재 cache 쓰기가 정상이고, 구간 실행에 유효한 단계 근거가 있으며, 잘못된 경고가 0건이어야 한다. 응답 시간은 참고값이다.", "탐지 시간은 측정만 하며 기준 없이 판정하지 않는다. 실제 GPU 단계 패턴과 정상 허용 목록은 미확인이다.", ("normal_before", "normal_after", "stream_before", "stream_after", "cache_current_before", "cache_current_after", "false_warnings_zero")),
    "AWS-12": _ReportCase("비교 Pod 연결이 규칙 전 성공, 적용 중 timeout과 Hubble dropped, 제거 뒤 재성공해야 한다.", "기본 통제가 이미 막으면 비교 불가다. 정리 실패는 failed다.", ("baseline_connected", "policy_timeout", "hubble_dropped", "restored_connected", "cleanup_ok")),
    "AWS-13": _ReportCase("해시 불일치 묶음 시작 거부 뒤 이전 묶음으로 복구하고 정상 200·거부 1건을 확인한다.", "SHA-256 바이트 대조이며 서명 검증이 아니다. 복구 실패는 failed다.", ("bad_bundle_rejected", "recovered_200", "recovered_denied", "cleanup_ok")),
    "AWS-14": _ReportCase("Gateway 재시작 직후 503을 관측하고 준비 뒤 10초 안에 200으로 복귀한다.", "503은 스냅샷 not_before가 원인인지 상태와 함께 대조한다.", ("restart_ok", "initial_503", "recovered_200", "within_10s")),
    "AWS-15": _ReportCase("수집기 정지 중 Gateway 재시작 후 503, 수집 재개 뒤 200을 확인한다.", "수집기 복구 실패는 failed다.", ("stop_ok", "restart_ok", "observed_503", "recovered_200", "cleanup_ok")),
    "AWS-16": _ReportCase("수집기 정지 15초 초과 뒤 단계가 unknown이고 일반 요청은 200이어야 한다.", "단계 수집 비활성 시 unknown이다. 수집기 복구 실패는 failed다.", ("stop_ok", "expired_unknown", "request_200", "cleanup_ok")),
    "AWS-17": _ReportCase("교체 후 시간표 collector.pod_uid가 새 Pod이고 새 실행에 이전 단계가 붙지 않아야 한다.", "Tetragon 원시 Pod UID는 process.pod.uid다.", ("pod_changed", "timeline_new_uid", "old_phase_absent")),
    "AWS-18": _ReportCase("동일 고정 chat·completions 요청의 일반·stream 종료, 응답 usage의 답변 길이, 토큰화 경계·초과, 엔진 로그 수신을 대조한다.", "토큰 실패 주입은 서버 자동 사례에 포함되지 않아 별도 가짜 엔진 시험으로만 확인한다. 토큰화 결과를 못 얻으면 unknown이다.", ("normal", "stream", "usage", "log_match", "context_boundary", "context_exceeded")),
    "AWS-19": _ReportCase("미지원 경로·필드는 거부되고 사유가 기록되며 엔진 로그에는 정상 대조 ID만 남아야 한다.", "정상 ID가 같은 구간의 로그에 없으면 거부 미전달은 unknown이다.", ("codes", "reasons", "records", "not_delivered", "engine_log_absent", "positive_control")),
    "AWS-20": _ReportCase("가짜 보호 파일의 방식별 Override errno 또는 Sigkill 종료, 같은 Pod UID 원시 이벤트, monitor 복귀를 확인한다.", "시험용 Pod 먼저; 엔진 대상 Sigkill은 별도 opt-in. 근거 부족은 unknown, 복귀 실패는 failed다.", ("enforce_active", "cleanup_ok", "restore_read_ok")),
}


def _report_reason(case: Mapping[str, Any]) -> str:
    spec = REPORT_CASES[case["case_id"]]
    facts = case.get("facts") or {}
    if case.get("result") == "passed":
        return "기준 충족: " + spec.criteria
    values = ", ".join(
        f"{name}={_report_value(_report_at(facts, name))}"
        for name in spec.required if _report_at(facts, name) is not True
    )
    return f"{case.get('reason') or '확인 못 함'}; 필수 항목: {values or '확인 못 함'}"


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
        self.gateway = f"http://{parsed.hostname if ':' not in parsed.hostname else '[' + parsed.hostname + ']'}:{port}"
        self.gateway_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        self.gateway_host = parsed.hostname
        self.gateway_port = port
        self.tokens = _read_tokens(Path(args.token_env_file).expanduser())
        self.wrong_token = "aws-acceptance-invalid-" + uuid4().hex
        while self.wrong_token in self.tokens.values():
            self.wrong_token = "aws-acceptance-invalid-" + uuid4().hex
        self.observation_dir = Path(args.observation_dir).expanduser()
        self.runner = runner or Runner()
        self.engine = getattr(args, "engine", "vllm")
        self.http = http or HTTPClient()
        self.sleep = sleeper
        self.monotonic = monotonic
        self.http_events: list[dict[str, Any]] = []
        self._forward: Any = None
        self._restart_pending = False
        self.reconnect_at: str | None = None
        self.cases: list[dict[str, Any]] = []
        self.engine_log_format_confirmed = False
        self.normal_before: dict[str, Any] | None = None
        self.detection_started_at: str | None = None
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

    def _close_forward(self) -> None:
        forward, self._forward = self._forward, None
        if forward is not None and forward.poll() is None:
            forward.terminate()
            try:
                forward.wait(timeout=3)
            except subprocess.TimeoutExpired:
                forward.kill()
                forward.wait(timeout=3)

    def _gateway_restart_started(self) -> None:
        self._close_forward()
        self._restart_pending = self.gateway_loopback
        self.reconnect_at = None

    def _reopen_forward(self) -> None:
        if self._forward is not None and self._forward.poll() is None:
            return
        self._close_forward()
        self._forward = self.runner.start_port_forward([
            "kubectl", "--context", self.context, "-n", self.namespace,
            "port-forward", "deploy/serving-gateway", f"{self.gateway_port}:8080",
            "--address=::1" if self.gateway_host == "::1" else "--address=127.0.0.1",
        ])
        self.reconnect_at = _now()

    def _port_accepting(self) -> bool:
        try:
            with socket.create_connection((self.gateway_host, self.gateway_port), timeout=0.1):
                return True
        except OSError:
            return False

    def _wait_forward_ready(self, timeout: float) -> None:
        deadline = self.monotonic() + min(max(timeout, 0), 2.0)
        while self._forward is not None and self._forward.poll() is None:
            if self._port_accepting() or self.monotonic() >= deadline:
                return
            self.sleep(min(0.1, deadline - self.monotonic()))

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
            try:
                response = self.http.request(
                    method, self.gateway + path, headers=headers, body=body, timeout=timeout,
                )
            except (OSError, URLError, TimeoutError):
                if not self._restart_pending:
                    raise
                self._reopen_forward()
                self._wait_forward_ready(timeout)
                response = self.http.request(
                    method, self.gateway + path, headers=headers, body=body, timeout=timeout,
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

    def _log_received(self, logs: str, request_id: str | None, route: str) -> bool:
        if request_id is None:
            return False
        if self.engine == "sglang":
            for line in logs.splitlines():
                start = line.find("{")
                if start < 0:
                    continue
                try:
                    record = json.loads(line[start:])
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or record.get("event") not in {"request.received", "request.finished"}:
                    continue
                headers = record.get("headers")
                if isinstance(headers, dict) and headers.get("x-request-id") == request_id:
                    return True
            return False
        if route == "/v1/chat/completions":
            engine_id = f"chatcmpl-{request_id}"
        elif route == "/v1/completions":
            engine_id = f"cmpl-{request_id}-0"
        else:
            return False
        return f"Received request {engine_id}" in logs

    @staticmethod
    def _sglang_prompt_fields(logs: str) -> list[str]:
        sensitive = {"text", "prompt", "input_ids", "input_embeds", "image_data", "audio_data", "video_data"}
        found: set[str] = set()
        for line in logs.splitlines():
            start = line.find("{")
            if start < 0:
                continue
            try:
                record = json.loads(line[start:])
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("event") not in {"request.received", "request.finished"}:
                continue
            obj = record.get("obj")
            if isinstance(obj, dict):
                found.update(key for key in obj if key in sensitive and obj[key] is not None)
        return sorted(found)

    def _engine_pod(self) -> tuple[dict[str, Any] | None, CommandResult]:
        result = self._kubectl("get", "pods", "-l", "app=serving-engine", "-o", "json")
        document = _json_object(result.stdout.encode("utf-8")) if result.returncode == 0 else None
        items = document.get("items") if document else None
        if not isinstance(items, list):
            return None, result
        active = []
        for item in items:
            if not isinstance(item, dict):
                continue
            status = item.get("status") if isinstance(item.get("status"), dict) else {}
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if status.get("phase") not in {"Failed", "Succeeded"} and metadata.get("deletionTimestamp") is None:
                active.append(item)
        if len(active) != 1:
            return None, result
        pod = active[0]
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
            "started_at": ((engine.get("state") or {}).get("running") or {}).get("startedAt") if isinstance(engine, dict) else None,
            "ready": ready,
        }, result

    def _pod_configuration(self) -> dict[str, Any]:
        result = self._kubectl("get", "pods", "-l", "app=serving-engine", "-o", "json")
        document = _json_object(result.stdout.encode()) if result.returncode == 0 else None
        items = document.get("items") if document else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            return {"state": "unknown"}
        pod = items[0]
        spec = pod.get("spec") if isinstance(pod.get("spec"), dict) else {}
        status = pod.get("status") if isinstance(pod.get("status"), dict) else {}
        containers = spec.get("containers") if isinstance(spec.get("containers"), list) else []
        engine = next((item for item in containers if isinstance(item, dict) and item.get("name") == "engine"), None)
        statuses = status.get("containerStatuses") if isinstance(status.get("containerStatuses"), list) else []
        engine_status = next((item for item in statuses if isinstance(item, dict) and item.get("name") == "engine"), {})
        if engine is None:
            return {"state": "unknown"}
        secret_arg = re.compile(r'(--(?:admin-)?api-key)\s+(?:"[^"]*"|\x27[^\x27]*\x27|\S+)')
        def redact_arg(value: Any) -> Any:
            return secret_arg.sub(r'\1 [REDACTED]', value) if isinstance(value, str) else value
        args = [redact_arg(item) for item in (engine.get("args") or [])]
        for index, value in enumerate(args[:-1]):
            if value in {"--admin-api-key", "--api-key"}:
                args[index + 1] = "[REDACTED]"
        return {"state": "observed", "command": [redact_arg(item) for item in (engine.get("command") or [])], "args": args,
                "env_names": [item.get("name") for item in engine.get("env", []) if isinstance(item, dict)],
                "image": engine.get("image"), "image_id": engine_status.get("imageID")}

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
        response_count = 0
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
                    "response_count": response_count,
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
                response_count += 1
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
                        "response_count": response_count,
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
                    "response_count": response_count,
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

    def _tetragon_observations(self, since: str) -> tuple[bool, list[dict[str, Any]]]:
        """Use Gateway-normalized observations for verdict and cache generation."""
        try:
            response, document = self._get_json(
                "/api/observations?" + urlencode({"source": "tetragon"})
            )
        except (OSError, URLError, TimeoutError):
            return False, []
        rows = document.get("observations") if document else None
        if response.status != 200 or not isinstance(rows, list):
            return False, []
        recent = [item for item in rows if isinstance(item, dict) and _after(item.get("observed_at"), since)]
        counts = document.get("counts") if isinstance(document.get("counts"), dict) else {}
        total = counts.get("total")
        # The query returns only the latest 50 rows. Never claim zero warnings
        # if the requested interval may extend beyond that page.
        complete = not (isinstance(total, int) and total > len(rows) and len(rows) >= 50 and len(recent) == len(rows))
        return complete, recent

    def _tetragon_gaps_clear(self, since: str) -> bool:
        if not self.observation_dir.is_dir():
            return False
        path = self.observation_dir / "tetragon.gaps.jsonl"
        if not path.exists():
            return True
        try:
            gaps: dict[str, tuple[str | None, str | None]] = {}
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    item = json.loads(line)
                    if not isinstance(item, dict) or item.get("kind") != "tetragon":
                        return False
                    gap_id = item.get("gap_id")
                    if not isinstance(gap_id, str):
                        return False
                    if item.get("event") == "gap_opened":
                        gaps[gap_id] = (item.get("opened_at"), None)
                    elif item.get("event") == "gap_closed":
                        opened, _ = gaps.get(gap_id, (item.get("opened_at"), None))
                        gaps[gap_id] = (opened, item.get("closed_at"))
            boundary = _parse_time(since)
            if boundary is None:
                return False
            return all(
                _parse_time(opened) is not None
                and _parse_time(closed) is not None
                and _parse_time(closed) < boundary
                for opened, closed in gaps.values()
            )
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
            return False

    def _tetragon_collector_ok(self) -> bool:
        try:
            response, collectors = self._collectors()
        except (OSError, URLError, TimeoutError, ValueError):
            return False
        return response.status == 200 and collectors.get("observation_feed_tetragon", {}).get("state") == "ok"

    def _wait_tetragon(self, since: str, predicate: Callable[[Mapping[str, Any]], bool]) -> tuple[bool, dict[str, Any] | None]:
        available = False
        for attempt in range(8):
            readable, rows = self._tetragon_observations(since)
            available = available or readable
            match = next((item for item in rows if predicate(item)), None)
            if match is not None:
                return True, match
            if attempt < 7:
                self.sleep(2.0)
        return available, None

    def _engine_event(self, item: Mapping[str, Any], pod_name: str) -> bool:
        pod = item.get("pod")
        return isinstance(pod, Mapping) and pod.get("namespace") == self.namespace and pod.get("pod") == pod_name

    @staticmethod
    def _event_summary(item: Mapping[str, Any] | None, started_at: str) -> dict[str, Any] | None:
        if item is None:
            return None
        start, observed = _parse_time(started_at), _parse_time(item.get("observed_at"))
        return {
            "observed_at": item.get("observed_at"),
            "binary": item.get("binary"),
            "path": item.get("path"),
            "pod": item.get("pod"),
            "rule_id": item.get("rule_id"),
            "access": item.get("access"),
            "action_mode": item.get("action_mode"),
            "exec_verdict": item.get("exec_verdict"),
            "cache_generation": item.get("cache_generation"),
            "phase": item.get("phase"),
            "detection_delay_s": round((observed - start).total_seconds(), 3) if start and observed else None,
        }

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
        stream_final = _stream_final_success(stream_record)
        stream_ok = None if stream_final is None else (
            stream_response.status == 200
            and stream_data
            and stream_done
            and stream_final
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
                "truncated": not stream_done,
                "record_fields_used": stream_record,
            },
            "engine_evidence": {
                "state": "observed" if log_match else "unknown",
                "match": ("JSON request.received/request.finished headers.x-request-id"
                          if self.engine == "sglang" else "Received request chatcmpl-<gateway request id>"),
                "reference": "kubectl logs deployment/serving-engine -c engine --since-time",
                "prompt_fields": self._sglang_prompt_fields(logs) if self.engine == "sglang" else None,
            },
        }

    def _context_limits(self) -> tuple[int, int] | None:
        response, document = self._get_json("/api/policy")
        if response.status != 200 or not isinstance(document, dict):
            return None
        for name in ("current", "policy"):
            policy = document.get(name)
            if not isinstance(policy, dict):
                continue
            context = policy.get("max_context_tokens")
            output = policy.get("output_max_tokens")
            if (isinstance(context, int) and not isinstance(context, bool)
                    and isinstance(output, int) and not isinstance(output, bool)
                    and context > output > 1):
                return context, output
        return None

    def _engine_token_count(self, route: str, text: str) -> int | None:
        pod, _ = self._engine_pod()
        if not pod or not isinstance(pod.get("name"), str):
            return None
        payload: dict[str, Any] = {"model": _MODEL}
        if route == "/v1/chat/completions":
            payload["messages"] = [{"role": "user", "content": text}]
            if self.engine == "vllm":
                payload["add_generation_prompt"] = True
        else:
            payload["prompt"] = text
        result = self.runner.run(
            ["kubectl", "--context", self.context, "-n", self.namespace,
             "exec", "-i", "pod/" + pod["name"], "-c", "engine", "--",
             "python3", "-c", COUNT_TOKENS_CODE],
            timeout=30, input=json.dumps(payload, ensure_ascii=False),
        )
        if result.returncode != 0:
            return None
        try:
            count = int(result.stdout.strip())
        except ValueError:
            return None
        return count if count >= 0 else None

    def _context_probe(self, route: str, token: str, limit: int, output_max: int) -> tuple[bool | None, bool | None, dict[str, Any]]:
        low, high = 1, 4096
        selected: tuple[int, int] | None = None
        # A short output (1..8 tokens) keeps the exact-boundary generation cheap.
        lower = limit - min(output_max - 1, 8)
        while low <= high:
            middle = (low + high) // 2
            count = self._engine_token_count(route, ("hello " * middle).strip())
            if count is None:
                return None, None, {"state": "token_count_unavailable"}
            if lower <= count < limit:
                selected = middle, count
                break
            if count < lower:
                low = middle + 1
            else:
                high = middle - 1
        if selected is None:
            return None, None, {"state": "exact_boundary_prompt_unavailable"}
        repeats, input_tokens = selected
        output_tokens = limit - input_tokens
        prompt = ("hello " * repeats).strip()
        payload: dict[str, Any] = {"model": _MODEL, "max_tokens": output_tokens}
        if route == "/v1/chat/completions":
            payload["messages"] = [{"role": "user", "content": prompt}]
        else:
            payload["prompt"] = prompt
        boundary = self._request("POST", route, token=token, payload=payload, timeout=300)
        usage = _completion_usage(boundary)
        boundary_document = _json_object(boundary.body)
        choices = boundary_document.get("choices") if boundary_document else None
        finish_reason = choices[0].get("finish_reason") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        engine_truncated = finish_reason == "length" and usage is not None and usage < output_tokens
        boundary_ok = False if boundary.status != 200 or engine_truncated else (usage <= output_tokens if usage is not None else None)
        exceeded = self._request("POST", route, token=token,
                                 payload={**payload, "max_tokens": output_tokens + 1}, timeout=300)
        body = _json_object(exceeded.body)
        error = body.get("error") if body else None
        request_id = exceeded.headers.get("X-Request-ID") or exceeded.headers.get("x-request-id")
        record = _record_fields(self._detail(request_id))
        reason_ok = isinstance(error, dict) and error.get("code") == "CONTEXT_LIMIT_EXCEEDED"
        if exceeded.status != 403 or not reason_ok:
            exceeded_ok = False
        elif record is None:
            exceeded_ok = None
        else:
            exceeded_ok = record["delivery"]["attempted"] is False
        return boundary_ok, exceeded_ok, {
            "state": "tested", "input_tokens": input_tokens, "output_tokens": output_tokens,
            "sum": input_tokens + output_tokens, "limit": limit,
            "boundary_status": boundary.status, "boundary_usage_completion_tokens": usage,
            "boundary_finish_reason": finish_reason, "engine_truncated": engine_truncated,
            "boundary_result": "엔진 잘림" if engine_truncated else None,
            "exceeded_status": exceeded.status,
            "exceeded_reason": error.get("code") if isinstance(error, dict) else None,
            "exceeded_record": record,
        }

    def aws_18(self) -> tuple[dict[str, Any], dict[str, Any]]:
        since = _now()
        rows: list[dict[str, Any]] = []
        token = self.tokens["RUNTIME_INFERENCE_TOKEN"]
        for route, payload in FIXED_REQUESTS:
            for stream in (False, True):
                response = self._request("POST", route, token=token,
                                         payload={**payload, "stream": stream}, timeout=300)
                request_id = response.headers.get("X-Request-ID") or response.headers.get("x-request-id")
                document = _json_object(response.body) if not stream else None
                choices = document.get("choices") if document else None
                valid = isinstance(choices, list) and bool(choices)
                done = b"data: [DONE]" in response.body if stream else None
                data_seen = any(line.strip().startswith(b"data: {") for line in response.body.splitlines()) if stream else None
                record = _record_fields(self._detail(request_id)) if stream else None
                stream_final = _stream_final_success(record) if stream else None
                usage = _completion_usage(response) if not stream else None
                rows.append({"route": route, "stream": stream, "status": response.status,
                             "request_id": request_id, "valid": valid if not stream else data_seen,
                             "done": done, "truncated": stream and not done,
                             "record": record, "stream_final": stream_final,
                             "completion_tokens": usage,
                             "max_tokens": payload["max_tokens"]})
        logs_result, logs = self._engine_logs(since)
        normal = all(row["status"] == 200 and row["valid"] for row in rows if not row["stream"])
        stream_rows = [row for row in rows if row["stream"]]
        stream_ok = (None if any(row["stream_final"] is None for row in stream_rows)
                     else all(row["status"] == 200 and row["valid"] and row["done"]
                              and row["stream_final"] for row in stream_rows))
        usages = [row["completion_tokens"] for row in rows if not row["stream"]]
        usage_ok = None if any(value is None for value in usages) else all(value <= 8 for value in usages)
        log_match = (all(self._log_received(logs, row["request_id"], row["route"])
                         for row in rows if row["status"] == 200)
                     if logs_result.returncode == 0 else None)
        limits = self._context_limits()
        probes = [self._context_probe(route, token, *limits) for route, _ in FIXED_REQUESTS] if limits else []
        boundary_ok = all(item[0] is True for item in probes) if probes and all(item[0] is not None for item in probes) else None
        exceeded_ok = all(item[1] is True for item in probes) if probes and all(item[1] is not None for item in probes) else None
        return {"normal": normal, "stream": stream_ok, "usage": usage_ok,
                "log_match": log_match, "context_boundary": boundary_ok,
                "context_exceeded": exceeded_ok}, {
            "requests": rows, "fixed_request_sha256": _fixed_request_hash(),
            "usage_rule": "response.usage.completion_tokens <= requested max_tokens; missing usage is unknown",
            "context_limits": limits,
            "context_probes": [item[2] for item in probes],
            "log_prompt_fields": self._sglang_prompt_fields(logs) if self.engine == "sglang" else None,
        }

    def _route_rejection_record(self, request_id: str | None) -> dict[str, Any] | None:
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            return None
        try:
            result = self._kubectl("exec", "deployment/serving-gateway", "-c", "gateway", "--",
                                   "python3", "-c", ROUTE_RECORD_PROBE_CODE,
                                   "/srv/serving/runtime/data/gateway_route_rejections.jsonl", request_id)
        except (OSError, TimeoutError):
            return None
        record = _json_object(result.stdout.encode()) if result.returncode == 0 else None
        return record if isinstance(record, dict) and isinstance(record.get("found"), bool) else None

    def aws_19(self) -> tuple[dict[str, Any], dict[str, Any]]:
        since = _now()
        token = self.tokens["RUNTIME_INFERENCE_TOKEN"]
        normal, normal_id, valid = self._completion(token=token)
        paths = ("/generate", "/v1/responses", "/v1/messages", "/api/chat", "/api/generate",
                 "/invocations", "/vertex_generate", "/v1/realtime", "/v1/score", "/encode",
                 "/classify", "/v1/audio/transcriptions")
        fields = ("input_ids", "custom_logit_processor", "cache_salt", "session")
        attempts: list[dict[str, Any]] = []
        for path in paths:
            response = self._request("POST", path, token=token, payload={"model": _MODEL})
            request_id = response.headers.get("X-Request-ID") or response.headers.get("x-request-id")
            attempts.append({"path": path, "field": None, "status": response.status,
                             "request_id": request_id, "record": self._route_rejection_record(request_id)})
        for field in fields:
            payload = dict(FIXED_REQUESTS[0][1])
            payload[field] = "test-only"
            response = self._request("POST", "/v1/chat/completions", token=token, payload=payload)
            request_id = response.headers.get("X-Request-ID") or response.headers.get("x-request-id")
            body = _json_object(response.body)
            error = body.get("error") if body else None
            record = _record_fields(self._detail(request_id))
            attempts.append({"path": "/v1/chat/completions", "field": field,
                             "status": response.status, "request_id": request_id,
                             "reason": error.get("code") if isinstance(error, dict) else None,
                             "record": record})
        logs_result, logs = self._engine_logs(since)
        positive = (self._log_received(logs, normal_id, "/v1/chat/completions")
                    if logs_result.returncode == 0 and normal.status == 200 and valid else None)
        absent = (all(not self._log_received(logs, row["request_id"], row["path"])
                      for row in attempts) if positive else None)
        records_available = all(isinstance(row["record"], dict) for row in attempts)
        route_records = [row for row in attempts if row["field"] is None]
        missing_routes = [row["request_id"] for row in route_records
                          if isinstance(row["record"], dict) and row["record"].get("found") is False]
        return {
            "codes": all(row["status"] in {404, 405} for row in attempts if row["field"] is None)
                     and all(row["status"] == 400 for row in attempts if row["field"] is not None),
            "reasons": all(row["record"].get("found") is True
                           and row["record"].get("reason") == "NOT_FOUND"
                           and row["record"].get("response_code") == row["status"]
                           for row in route_records)
                       and all(row.get("reason") == "UNSUPPORTED_FIELD"
                               and row["record"].get("reason_code") == "UNSUPPORTED_FIELD"
                               for row in attempts if row["field"] is not None)
                       if records_available else None,
            "records": (all(row["record"].get("found") is True for row in route_records)
                        if records_available else None),
            "not_delivered": all((row["record"].get("found") is True
                                  and row["record"].get("forwarded_to_engine") is False
                                  if row["field"] is None else
                                  isinstance(row["record"].get("delivery"), dict)
                                  and row["record"]["delivery"].get("attempted") is False)
                                 for row in attempts)
                             if records_available else None,
            "engine_log_absent": absent,
            "positive_control": positive if positive else None,
        }, {"normal_control": {"status": normal.status, "request_id": normal_id,
                                "log_seen": positive}, "attempts": attempts,
            "missing_route_record_ids": missing_routes,
            "route_record_limit_note": ("Gateway가 한도 초과 수를 노출하지 않아 found:false를 기록 누락과 한도 초과로 구분할 수 없음"
                                        if missing_routes else None),
            "log_prompt_fields": self._sglang_prompt_fields(logs) if self.engine == "sglang" else None}

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
        phase_before = self._timeline()
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
        target_stale = before.get("target_pods", {}).get("stale_after_s")
        if isinstance(target_stale, (int, float)) and not isinstance(target_stale, bool) and math.isfinite(float(target_stale)):
            stale = max(stale, float(target_stale))
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
        stopped_target: dict[str, Any] | None = None
        stopped_target_status: int | None = None
        request_target_state: str | None = None
        request_target_status: int | None = None
        request_target_error: str | None = None
        stopped_valid = False
        target_recovered = False
        recovered_target: dict[str, Any] | None = None
        recovered_target_status: int | None = None
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
                # The Pod snapshot has its own freshness window. Exercise the
                # stopped window only after it has expired, with polling slack.
                self.sleep(max(0.0, stale + 2.0 - (self.monotonic() - stopped_window_started)))
                # The Gateway re-reads the snapshot on its own poll (5 s by
                # default), so expiry is visible up to one poll after `stale`.
                # Wait for the unavailable state before sending the request.
                unavailable_deadline = stopped_window_started + stale + 12.0
                while True:
                    try:
                        probe_response, probe_target = self._get_json("/api/target")
                        request_target_status = probe_response.status
                        request_target_error = None
                    except (OSError, URLError, TimeoutError) as error:
                        probe_target = None
                        request_target_status = None
                        request_target_error = _safe_exception(error)
                    request_target_state = ((probe_target or {}).get("verification") or {}).get("state") if request_target_status == 200 and isinstance(probe_target, dict) else None
                    if request_target_state in {"unavailable", "blocked"} or self.monotonic() >= unavailable_deadline:
                        break
                    self.sleep(1.0)
                if request_target_state == "unavailable":
                    try:
                        stopped_response, stopped_request_id, stopped_valid = self._completion(
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
                        _target_response, stopped_target = self._get_json("/api/target")
                        stopped_target_status = _target_response.status
                    except (OSError, URLError, TimeoutError) as error:
                        stopped_request = {
                            "http_status": None,
                            "reason_code": None,
                            "error": _safe_exception(error),
                        }
                else:
                    stopped_request["error"] = ("target blocked" if request_target_state == "blocked" else
                                                 "target 조회 실패" if request_target_error else "target unavailable 기한 초과")
        finally:
            phase_stopped = self._timeline()
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
                    target_deadline = self.monotonic() + recovery_window
                    while True:
                        try:
                            _target_response, recovered_target = self._get_json("/api/target")
                            recovered_target_status = _target_response.status
                        except (OSError, URLError, TimeoutError):
                            recovered_target = None
                        verification = recovered_target.get("verification") if recovered_target else None
                        if recovered_target_status == 200 and isinstance(verification, dict) and verification.get("state") == "ok" and recovered_target.get("gateway_hold") is None:
                            target_recovered = True
                            break
                        if self.monotonic() >= target_deadline:
                            break
                        self.sleep(min(2.0, max(0.0, target_deadline - self.monotonic())))
                    inference_recovered, inference_record, inference_retry = (
                        self._retry_inference(recovery_window)
                    )
        gap_scan_ok, gap_refs = self._new_gap_refs(positions) if gaps_readable else (False, [])
        heartbeat_refs = list(heartbeat_snapshot.values())
        return {
            **({"unknown_reason": "중단 중 /api/target 조회 실패 또는 unavailable 확인 기한 초과: " + (request_target_error or str(request_target_status))}
               if stop.returncode == 0 and request_target_state not in {"unavailable", "blocked"} else {}),
            "stop_ok": stop.returncode == 0,
            "heartbeat_stopped_seen": heartbeat_seen,
            "collectors_unavailable": unavailable,
            "stopped_request_200_unverified": (False if request_target_state == "blocked" else None if request_target_state != "unavailable" else (
                stopped_request["http_status"] == 200
                and stopped_valid
                and stopped_target_status == 200
                and isinstance(stopped_target, dict)
                and isinstance(stopped_target.get("verification"), dict)
                and stopped_target["verification"].get("state") == "unavailable"
                and stopped_target.get("gateway_hold") is None
            )),
            "start_ok": bool(start and start.returncode == 0),
            "collectors_recovered": recovery_ok,
            "target_recovered": target_recovered,
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
            "request_target_verification": {"http_status": request_target_status, "state": request_target_state, "error": request_target_error},
            "stopped_target_verification": stopped_target.get("verification") if stopped_target else None,
            "recovered_target_verification": recovered_target.get("verification") if recovered_target else None,
            "recovery_request": {
                **inference_retry,
                "record_fields_used": inference_record,
            },
            "gap_references": gap_refs,
            "observation_references": [*heartbeat_refs, *gap_refs],
            "gap_note": "clean systemd stop은 gap 행을 만들지 않으며 gap은 보조 증거임",
            "phase_state_supplementary": {
                "before": (phase_before or {}).get("collector"),
                "stopped": (phase_stopped or {}).get("collector"),
                "recovered": (self._timeline() or {}).get("collector"),
                "note": "단계 수집 비활성(SGLang 등)이면 기존 AWS-07 판정에 영향 없음",
            },
        }

    def aws_08(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.args.allow_engine_restart:
            return {"not_run_reason": "--allow-engine-restart 없음(GPU 반납 및 cold-start 동반)"}, {}
        phase_before = self._timeline()
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
            "phase_state_supplementary": {
                "before": (phase_before or {}).get("collector"),
                "after": (self._timeline() or {}).get("collector"),
                "note": "단계 수집 비활성(SGLang 등)이면 기존 AWS-08 판정에 영향 없음",
            },
        }

    def aws_09(self) -> tuple[dict[str, Any], dict[str, Any]]:
        pod, _ = self._engine_pod()
        if not pod or not pod.get("ready") or not isinstance(pod.get("name"), str):
            return {"unknown_reason": "Ready 엔진 Pod 하나를 식별하지 못함"}, {}
        probes: dict[str, dict[str, Any]] = {}
        for label, executable in (("absolute", "/bin/sh"), ("path", "sh")):
            started = _now()
            result = self._kubectl(
                "exec", "pod/" + pod["name"], "-c", "engine", "--", executable, "-c", "true", timeout=15
            )
            available, match = self._wait_tetragon(
                started,
                lambda item: item.get("kind") == "process_exec"
                and self._engine_event(item, pod["name"])
                and item.get("binary") in ({"/bin/sh"} if label == "absolute" else {"/bin/sh", "/usr/bin/sh", "/bin/dash", "/usr/bin/dash"}),
            )
            probes[label] = {
                "started_at": started,
                "returncode": result.returncode,
                "observation_available": available,
                "observation": self._event_summary(match, started),
            }
        absolute = probes["absolute"]["observation"]
        path = probes["path"]["observation"]
        return {
            "absolute_exec_ok": probes["absolute"]["returncode"] == 0,
            "path_exec_ok": probes["path"]["returncode"] == 0,
            "path_observed": None if not probes["path"]["observation_available"] else path is not None and path["exec_verdict"] is not None,
            "engine_pod_match": None if absolute is None else absolute["pod"] == {"namespace": self.namespace, "pod": pod["name"]} and absolute["binary"] == "/bin/sh",
            "suspect_verdict": None if absolute is None else absolute["exec_verdict"] == "suspect_binary" and absolute["binary"] == "/bin/sh",
        }, {
            "engine_pod": {"name": pod["name"], "uid": pod.get("uid")},
            "absolute": absolute,
            "path_lookup": path,
            "probes": probes,
            "detection_reason": "intent suspect_binaries에 있는 실행 경로",
        }

    def aws_10(self) -> tuple[dict[str, Any], dict[str, Any]]:
        pod, _ = self._engine_pod()
        if not pod or not pod.get("ready") or not isinstance(pod.get("name"), str):
            return {"unknown_reason": "Ready 엔진 Pod 하나를 식별하지 못함"}, {}
        path = "/run/byul-probe/secret.txt"
        started = _now()
        result = self._kubectl(
            "exec", "pod/" + pod["name"], "-c", "engine", "--", "python3", "-c", PROBE_READ_CODE, path, timeout=15
        )
        available, match = self._wait_tetragon(
            started,
            lambda item: item.get("rule_id") == "TG-SECRET-01"
            and item.get("path") == path
            and self._engine_event(item, pod["name"]),
        )
        observation = self._event_summary(match, started)
        return {
            "read_ok": result.returncode == 0 and "READ_OK" in result.stdout,
            "path_match": None if observation is None else observation["path"] == path,
            "engine_pod_match": None if observation is None else observation["pod"] == {"namespace": self.namespace, "pod": pod["name"]},
            "read_access": None if observation is None else observation["access"] == "read",
            "monitor_detected": None if observation is None else observation["rule_id"] == "TG-SECRET-01" and observation["action_mode"] == "monitor",
        }, {
            "engine_pod": {"name": pod["name"], "uid": pod.get("uid")},
            "started_at": started,
            "read_returncode": result.returncode,
            "observation_available": available,
            "observation": observation,
        }

    def aws_20(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Switch one loaded candidate through enforce and back to monitor."""
        if not getattr(self.args, "allow_rule_switch", False):
            return {"not_run_reason": "--allow-rule-switch 없음"}, {}
        monitor_path = Path(self.args.aws20_monitor_manifest)
        try:
            monitor = json.loads(monitor_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return {"unknown_reason": "monitor manifest를 읽지 못함"}, {}
        method = _aws20_method(monitor, namespace=self.namespace)
        if method is None:
            return {"unknown_reason": "검토한 1.7.1 monitor-mode manifest의 방식·가짜 경로 불일치"}, {}
        probe_name = getattr(self.args, "aws20_probe_pod", None)
        engine_target = not bool(probe_name)
        probes = monitor["spec"].get("kprobes", []) + monitor["spec"].get("lsmhooks", [])
        contains_sigkill = any(a.get("action") == "Sigkill" for p in probes
                              for s in p.get("selectors", []) for a in s.get("matchActions", []))
        if engine_target and contains_sigkill and not getattr(self.args, "allow_engine_sigkill", False):
            return {"not_run_reason": "Sigkill 포함 정책은 --aws20-probe-pod 우선; 엔진 대상은 --allow-engine-sigkill 필요"}, {}
        if probe_name:
            pod_result = self._kubectl("get", "pod", probe_name, "-o", "json")
            pod_doc = _json_object(pod_result.stdout.encode()) if pod_result.returncode == 0 else None
            metadata = (pod_doc or {}).get("metadata") or {}
            pod = {"name": probe_name, "uid": metadata.get("uid"), "ready": (pod_doc or {}).get("status", {}).get("phase") == "Running"}
            container = "probe"
        else:
            pod, _ = self._engine_pod()
            pod_result = self._kubectl("get", "pod", pod["name"], "-o", "json") if pod else CommandResult(1)
            pod_doc = _json_object(pod_result.stdout.encode()) if pod_result.returncode == 0 else None
            container = "engine"
        if not pod or not pod.get("ready") or not pod.get("uid") or not isinstance(pod.get("name"), str):
            return {"unknown_reason": "Ready 대상 Pod UID를 확인하지 못함"}, {}
        current_result = self._kubectl("get", "tracingpolicynamespaced", "runtime-tg-secret-01", "-o", "json")
        current = _json_object(current_result.stdout.encode()) if current_result.returncode == 0 else None
        from serving_runtime.enforcement.tetragon import comparable_spec
        if (_aws20_method(current, namespace=self.namespace) != method or comparable_spec(current["spec"]) != comparable_spec(monitor["spec"])):
            return {"unknown_reason": "적용 규칙과 검토한 monitor manifest 불일치"}, {}
        labels = (pod_doc or {}).get("metadata", {}).get("labels", {})
        selector = current["spec"]["podSelector"]["matchLabels"]
        if (not isinstance(labels, dict) or any(labels.get(key) != value for key, value in selector.items())
            or (pod_doc or {}).get("metadata", {}).get("uid") != pod["uid"]):
            return {"unknown_reason": "현재 대상 Pod UID 또는 규칙 selector 불일치"}, {}

        def run_mode(command: list[str]) -> CommandResult:
            return self.runner.run(command, timeout=70)
        probe_tag = "byul-read-probe-" + uuid4().hex
        monitor_read = self._kubectl("exec", "pod/" + pod["name"], "-c", container, "--",
                                     "python3", "-c", PROBE_READ_ERRNO_CODE, _SECRET_PROBE_PATH,
                                     probe_tag + "-monitor", timeout=15)
        if monitor_read.returncode != 0 or _json_object(monitor_read.stdout.encode()) != {"read": "ok"}:
            return {"unknown_reason": "monitor 구간의 가짜 파일 읽기 대조 실패; 정책 모드 유지"}, {}
        enabled = switch_mode(self.context, self.namespace, "runtime-tg-secret-01", "enforce", run=run_mode, history_path=self.evidence_dir / "transitions.jsonl")
        if not enabled["ok"] or enabled["before"]["mode"] != "monitor":
            if enabled["ok"] and enabled["before"]["mode"] != "monitor":
                switch_mode(self.context, self.namespace, "runtime-tg-secret-01", "monitor", run=run_mode, history_path=self.evidence_dir / "transitions.jsonl")
            return {"unknown_reason": "정책 monitor→enforce 모드 전환 또는 동일 ID 확인 실패"}, {"mode_switch": enabled}

        facts: dict[str, Any] = {"enforce_method": method, "engine_target": engine_target,
                                  "monitor_read_ok": True,
                                  "enforce_active": True, "read_eperm": None, "raw_override": None,
                                  "exit_sigkill": None, "raw_sigkill": None,
                                  "normal_200": None, "gateway_503": None, "engine_restarted": None,
                                  "recovered_200": None, "cleanup_ok": False, "restore_read_ok": None}
        evidence: dict[str, Any] = {"engine_pod_uid": pod["uid"], "rule_id": "TG-SECRET-01",
                                    "path": _SECRET_PROBE_PATH, "reviewed_manifest_sha256": hashlib.sha256(monitor_path.read_bytes()).hexdigest(),
                                    "enforce_method": method, "policy_id": enabled["after"]["id"]}
        evidence["probe_scope"] = "kubectl_exec_process"
        try:
            started = _now()
            read = self._kubectl("exec", "pod/" + pod["name"], "-c", container, "--",
                                 "python3", "-c", PROBE_READ_ERRNO_CODE, _SECRET_PROBE_PATH, probe_tag, timeout=15)
            result = _json_object(read.stdout.encode())
            facts["exit_sigkill"] = (True if read.returncode in {137, -9}
                                     else False if read.returncode in {0, 13} and result else None)
            if method != "sigkill_fd_install":
                # Keep the legacy fact key for stored AWS-20 reports.
                facts["read_eperm"] = (True if read.returncode == 13 and result in ({"errno": 1, "name": "EPERM"}, {"errno": 13, "name": "EACCES"})
                                       else False if read.returncode == 0 and result == {"read": "ok"} else None)
            evidence["read_returncode"] = read.returncode
            action = "KPROBE_ACTION_SIGKILL" if method == "sigkill_fd_install" else "KPROBE_ACTION_OVERRIDE"
            for attempt in range(8):
                if _aws20_raw_action(self.observation_dir / "tetragon.jsonl", since=started,
                                     pod_uid=pod["uid"], namespace=self.namespace, action=action, method=method,
                                     probe_tag=probe_tag):
                    facts["raw_sigkill" if method == "sigkill_fd_install" else "raw_override"] = True
                    break
                if attempt < 7:
                    self.sleep(2.0)
            key = "raw_sigkill" if method == "sigkill_fd_install" else "raw_override"
            facts[key] = facts[key] or None
            if method != "sigkill_fd_install":
                facts["raw_sigkill"] = _aws20_raw_action(self.observation_dir / "tetragon.jsonl", since=started,
                    pod_uid=pod["uid"], namespace=self.namespace, action="KPROBE_ACTION_SIGKILL", method=method,
                    probe_tag=probe_tag) or None
            response, request_id, valid = self._completion(token=self.tokens["RUNTIME_INFERENCE_TOKEN"])
            record = _record_fields(self._detail(request_id))
            facts["normal_200"] = (response.status == 200 and valid and record is not None
                                   and record["result"] == "ALLOWED" and record["delivery"]["attempted"] is True)
            if engine_target:
                evidence["engine_lifecycle_note"] = "exec 시험 프로세스의 종료는 엔진 주 프로세스 종료·503·재시작 근거가 아님"
            evidence["normal_request_id"] = request_id
            evidence["normal_http_status"] = response.status
            evidence["collector_clear"] = self._tetragon_collector_ok() and self._tetragon_gaps_clear(started)
            if not evidence["collector_clear"]:
                facts[key] = None
                facts["raw_sigkill"] = None
        finally:
            try:
                restored = switch_mode(self.context, self.namespace, "runtime-tg-secret-01", "monitor", run=run_mode, history_path=self.evidence_dir / "transitions.jsonl")
                facts["cleanup_ok"] = (restored["ok"] and restored["after"]["id"] == enabled["after"]["id"])
                evidence["mode_restore"] = restored
                if facts["cleanup_ok"]:
                    self.sleep(3.0)
                    read_after = self._kubectl("exec", "pod/" + pod["name"], "-c", container, "--",
                                               "python3", "-c", PROBE_READ_ERRNO_CODE, _SECRET_PROBE_PATH, timeout=15)
                    after = _json_object(read_after.stdout.encode())
                    facts["restore_read_ok"] = (True if read_after.returncode == 0 and after == {"read": "ok"}
                                                else False if after and "errno" in after else None)
                    evidence["restore_read_returncode"] = read_after.returncode
            except Exception as error:
                facts["cleanup_ok"] = False
                evidence["restore_error"] = _safe_exception(error)
        from serving_runtime.enforcement.verification import assess_effects
        evidence["verified_effects"] = assess_effects(method, {
            "enforce_active": facts["enforce_active"], "event_bound": bool(facts.get("raw_override") or facts.get("raw_sigkill")),
            "collector_complete": evidence.get("collector_clear"), "override_event": facts.get("raw_override"),
            "sigkill_event": facts.get("raw_sigkill"), "read_denied": facts.get("read_eperm"),
            "process_terminated": facts.get("exit_sigkill"), "monitor_read_ok": facts["monitor_read_ok"],
            "restore_read_ok": facts["restore_read_ok"], "same_policy_id": facts["cleanup_ok"], "cleanup_ok": facts["cleanup_ok"],
        })
        return facts, evidence

    def _normal_control(self) -> dict[str, Any]:
        pod, _ = self._engine_pod()
        if not pod or not pod.get("ready") or not isinstance(pod.get("name"), str):
            return {"error": "Ready 엔진 Pod 하나를 식별하지 못함"}
        request_start = self.monotonic()
        response, request_id, valid = self._completion(token=self.tokens["RUNTIME_INFERENCE_TOKEN"])
        request_ms = round((self.monotonic() - request_start) * 1000, 3)
        record = _record_fields(self._detail(request_id))
        stream_start = self.monotonic()
        streamed, stream_id, data_seen, done_seen = self._stream_completion(token=self.tokens["RUNTIME_INFERENCE_TOKEN"])
        stream_ms = round((self.monotonic() - stream_start) * 1000, 3)
        stream_record = _record_fields(self._detail(stream_id))
        cache_path = "/cache/.byul-acceptance-" + uuid4().hex[:12]
        cache_started = _now()
        cache = self._kubectl(
            "exec", "pod/" + pod["name"], "-c", "engine", "--", "python3", "-c", CACHE_WRITE_CODE, cache_path, timeout=15
        )
        available, match = self._wait_tetragon(
            cache_started,
            lambda item: item.get("rule_id") == "TG-CACHE-01"
            and item.get("path") == cache_path
            and self._engine_event(item, pod["name"]),
        )
        return {
            "pod": pod["name"],
            "normal_ok": response.status == 200 and valid and record is not None and record["result"] == "ALLOWED" and record["delivery"]["attempted"] is True,
            "stream_ok": streamed.status == 200 and data_seen and done_seen and stream_record is not None and stream_record["result"] == "ALLOWED" and stream_record["delivery"]["attempted"] is True,
            "cache_ok": None if not available else cache.returncode == 0 and "CACHE_WRITE_REMOVED" in cache.stdout and match is not None and match.get("cache_generation") == "current" and match.get("exec_verdict") is None,
            "cache_observation_available": available,
            "cache_observation": self._event_summary(match, cache_started),
            "response_time_ms": {"normal": request_ms, "stream": stream_ms},
        }

    def aws_11(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.normal_before is None or self.detection_started_at is None:
            return {"not_run_reason": "AWS-09·10을 함께 선택해야 전후 대조 가능"}, {}
        after = self._normal_control()
        available, rows = self._tetragon_observations(self.detection_started_at)
        available = (available and self._tetragon_gaps_clear(self.detection_started_at)
                     and self._tetragon_collector_ok())
        suspect_paths = {"/bin/sh", "/usr/bin/sh", "/bin/dash", "/usr/bin/dash"}
        false_warnings = [
            self._event_summary(item, self.detection_started_at)
            for item in rows
            if item.get("exec_verdict") in {"outside_allowlist", "phase_signal_unverified", "exec_from_cache"}
            and item.get("binary") not in suspect_paths
        ]
        phase_rows = [item for item in rows if item.get("kind") == "process_exec"
                      and isinstance(item.get("phase"), list)
                      and any(phase != "unknown" for phase in item["phase"])
                      and item.get("phase_timeline_fresh") is not False]
        if self.engine == "sglang":
            timeline = self._timeline()
            pod, _ = self._engine_pod()
            collector = timeline.get("collector") if isinstance(timeline, dict) else None
            extraction = timeline.get("extraction") if isinstance(timeline, dict) else None
            try:
                current_phase_source = (
                    isinstance(extraction, dict) and extraction.get("engine") == "sglang"
                    and isinstance(collector, dict) and collector.get("state") == "running"
                    and isinstance(pod, dict) and bool(pod.get("uid"))
                    and collector.get("pod_uid") == pod["uid"]
                    and not ExecPolicy._collector_stale(collector)
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                current_phase_source = False
            if not current_phase_source:
                phase_rows = []
            else:
                phase_rows = [item for item in phase_rows
                              if isinstance(item.get("pod"), dict)
                              and item["pod"].get("pod") == pod.get("name")]
        phase_evidence = {
            "valid_exec_count": len(phase_rows),
            "unknown_exec_count": sum(item.get("exec_verdict") == "phase_unknown" for item in rows),
            "bases": sorted({basis for item in phase_rows for basis in item.get("phase_basis", [])
                             if isinstance(basis, str)}),
            "timeline_written_at": sorted({item["phase_timeline_written_at"] for item in phase_rows
                                           if isinstance(item.get("phase_timeline_written_at"), str)}),
            "freshness": "fresh" if any(item.get("phase_timeline_fresh") is True for item in phase_rows)
                         else "legacy_unbounded" if phase_rows else "unconfirmed",
        }
        return {
            "normal_before": self.normal_before.get("normal_ok"),
            "normal_after": after.get("normal_ok"),
            "stream_before": self.normal_before.get("stream_ok"),
            "stream_after": after.get("stream_ok"),
            "cache_current_before": self.normal_before.get("cache_ok"),
            "cache_current_after": after.get("cache_ok"),
            "false_warnings_zero": len(false_warnings) == 0 if available and phase_rows else None,
        }, {
            "before": self.normal_before,
            "after": after,
            "false_warning_count": len(false_warnings) if available else None,
            "false_warning_observed_partial_count": len(false_warnings),
            "warning_list_complete": available,
            "false_warnings": false_warnings,
            "phase_evidence": phase_evidence,
            "warning_criterion": "구간 내 outside_allowlist·phase_signal_unverified·exec_from_cache; 의도한 shell probe 제외",
            "response_time_note": "응답 시간은 참고값이며 판정에 쓰지 않음",
            "detection_time_note": "실행 시각→관측 시각을 측정하며 기준 없이 판정하지 않음",
        }

    def _timeline(self) -> dict[str, Any] | None:
        try:
            document = json.loads((self.observation_dir / "phase-timeline.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
            return None
        return document if isinstance(document, dict) and document.get("schema_version") == "runtime-phase-timeline/1.0" else None

    def _tetragon_counts(self) -> dict[str, int] | None:
        try:
            response, collectors = self._collectors()
        except (OSError, URLError, TimeoutError):
            return None
        if response.status != 200:
            return None
        counts = collectors.get("observation_feed_tetragon", {}).get("last_value")
        if not isinstance(counts, dict):
            return None
        result = {key: counts.get(key) for key in ("skipped_unsupported_total", "corrupt_lines_total")}
        return result if all(isinstance(value, int) and not isinstance(value, bool) for value in result.values()) else None

    def _raw_process_exit_count(self, since: str) -> int | None:
        count = 0
        try:
            with (self.observation_dir / "tetragon.jsonl").open("r", encoding="utf-8") as handle:
                for line in handle:
                    item = json.loads(line)
                    if isinstance(item, dict) and isinstance(item.get("process_exit"), dict) and _after(item.get("time"), since):
                        count += 1
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
            return None
        return count

    def _restart(self, deployment: str, timeout_s: int = 90) -> tuple[bool, dict[str, Any]]:
        restart = self._kubectl("rollout", "restart", f"deployment/{deployment}")
        if deployment == "serving-gateway" and restart.returncode == 0:
            self._gateway_restart_started()
        status = self._kubectl("rollout", "status", f"deployment/{deployment}", f"--timeout={timeout_s}s", timeout=timeout_s + 10) if restart.returncode == 0 else CommandResult(1)
        return restart.returncode == 0 and status.returncode == 0, {"restart_rc": restart.returncode, "rollout_rc": status.returncode}

    def _gateway_pods(self) -> list[dict[str, Any]] | None:
        result = self._kubectl("get", "pods", "-l", "app=serving-gateway", "-o", "json")
        document = _json_object(result.stdout.encode()) if result.returncode == 0 else None
        items = document.get("items") if isinstance(document, dict) else None
        return [item for item in items if isinstance(item, dict) and not (item.get("metadata") or {}).get("deletionTimestamp")] if isinstance(items, list) else None

    def _gateway_crashed(self, window_s: float, previous_uids: set[str]) -> tuple[bool | None, dict[str, Any]]:
        """True if the newest Gateway container exited after the restart.

        False only when it stayed running for the whole window; None when the
        Pod state could not be read.
        """
        deadline = self.monotonic() + window_s
        last: dict[str, Any] = {}
        seen = False
        while True:
            items = self._gateway_pods()
            pods = [item for item in items if isinstance((item.get("metadata") or {}).get("uid"), str) and (item.get("metadata") or {}).get("uid") not in previous_uids] if items is not None else []
            if pods:
                pod = max(pods, key=lambda item: str((item.get("metadata") or {}).get("creationTimestamp", "")))
                metadata = pod.get("metadata") or {}
                last.update({"pod_uid": metadata.get("uid"), "pod_name": metadata.get("name")})
                statuses = (pod.get("status") or {}).get("containerStatuses") or []
                status = next((item for item in statuses if isinstance(item, dict) and item.get("name") == "gateway"), None)
                if isinstance(status, dict):
                    seen = True
                    state = status.get("state") or {}
                    last_state = status.get("lastState") or {}
                    waiting = (state.get("waiting") or {}).get("reason")
                    last.update({"restart_count": status.get("restartCount"), "waiting_reason": waiting,
                            "terminated": bool(state.get("terminated") or last_state.get("terminated"))})
                    if (isinstance(status.get("restartCount"), int) and status["restartCount"] > 0) or last["terminated"] or waiting in {"CrashLoopBackOff", "Error"}:
                        return True, last
            if self.monotonic() >= deadline:
                return (False if seen else None), last
            self.sleep(2.0)

    def aws_12(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if not getattr(self.args, "allow_compare_policy", False):
            return {"not_run_reason": "--allow-compare-policy 없음"}, {}
        evidence: dict[str, Any] = {"manifests": [str(_DEPLOY / "policy-compare-pods.yaml"), str(_DEPLOY / "policy-compare-cilium.yaml")]}
        source = self._kubectl("get", "pod", "other-client", "-o", "json")
        source_doc = _json_object(source.stdout.encode()) if source.returncode == 0 else None
        source_spec = source_doc.get("spec") if isinstance(source_doc, dict) else None
        source_status = source_doc.get("status") if isinstance(source_doc, dict) else None
        containers = source_spec.get("containers") if isinstance(source_spec, dict) else None
        client = next((item for item in containers if isinstance(item, dict) and item.get("name") == "client"), None) if isinstance(containers, list) else None
        image = client.get("image") if isinstance(client, dict) else None
        pull_policy = client.get("imagePullPolicy") if isinstance(client, dict) else None
        statuses = source_status.get("containerStatuses") if isinstance(source_status, dict) else None
        client_status = next((item for item in statuses if isinstance(item, dict) and item.get("name") == "client"), None) if isinstance(statuses, list) else None
        if (not isinstance(image, str) or not image or not isinstance(pull_policy, str)
                or pull_policy not in {"Never", "IfNotPresent", "Always"}
                or not isinstance(source_status, dict) or source_status.get("phase") != "Running"):
            return {"unknown_reason": "실행 중인 other-client 이미지 또는 pull 정책 미확인"}, evidence
        if not isinstance(client_status, dict) or client_status.get("ready") is not True:
            return {"unknown_reason": "실행 중인 other-client 이미지 또는 pull 정책 미확인"}, evidence
        evidence["compare_image"] = image
        evidence["compare_image_pull_policy"] = pull_policy
        manifest = (_DEPLOY / "policy-compare-pods.yaml").read_text(encoding="utf-8")
        if manifest.count("__COMPARE_IMAGE__") != 2 or manifest.count("__COMPARE_PULL_POLICY__") != 2 or re.fullmatch(r"[A-Za-z0-9._:/@-]+", image) is None:
            return {"unknown_reason": "비교 Pod 이미지 치환 불가"}, evidence
        manifest = manifest.replace("__COMPARE_IMAGE__", image).replace("__COMPARE_PULL_POLICY__", pull_policy)
        for kind, name in (("pod", *_COMPARE_PODS[:1]), ("pod", *_COMPARE_PODS[1:]), ("ciliumnetworkpolicy", _COMPARE_POLICY)):
            check = self._kubectl("get", kind, name, "--ignore-not-found", "-o", "name")
            if check.returncode != 0 or check.stdout.strip():
                return {"unknown_reason": "비교 객체 존재 또는 사전 조회 실패"}, {**evidence, "preflight": {"kind": kind, "name": name, "rc": check.returncode, "existing": bool(check.stdout.strip())}}
        facts: dict[str, Any] = {}
        pods_attempted = False
        policy_attempted = False
        try:
            pods_attempted = True
            pods = self.runner.run(["kubectl", "--context", self.context, "-n", self.namespace, "apply", "-f", "-"], input=manifest)
            if pods.returncode != 0:
                facts["unknown_reason"] = "비교 Pod 적용 실패"
            else:
                ready = all(self._kubectl("wait", "--for=condition=Ready", f"pod/{name}", "--timeout=90s", timeout=100).returncode == 0 for name in _COMPARE_PODS)
                if not ready:
                    facts["unknown_reason"] = "비교 Pod Ready 미확인"
                else:
                    receiver_raw = self._kubectl("get", "pod", _COMPARE_PODS[1], "-o", "json")
                    receiver = _json_object(receiver_raw.stdout.encode()) if receiver_raw.returncode == 0 else None
                    address = (receiver.get("status") or {}).get("podIP") if receiver else None
                    try:
                        address = str(ip_address(address))
                    except (ValueError, TypeError):
                        address = None
                    if address is None:
                        facts["unknown_reason"] = "비교 receiver IP 미확인"
                    else:
                        baseline = self._tcp_probe("pod/" + _COMPARE_PODS[0], "client", address, 8080)
                        evidence["baseline_probe"] = baseline
                        facts["baseline_connected"] = baseline["connected"] if baseline["marker"] else None
                        if facts["baseline_connected"] is True:
                            policy_attempted = True
                            applied = self._kubectl("apply", "-f", str(_DEPLOY / "policy-compare-cilium.yaml"))
                            if applied.returncode != 0:
                                facts["unknown_reason"] = "비교 정책 적용 실패"
                            else:
                                self.sleep(2.0)
                                since = _now()
                                blocked = self._tcp_probe("pod/" + _COMPARE_PODS[0], "client", address, 8080)
                                available, refs = self._wait_observation_refs("hubble", since, lambda item:
                                    (item.get("src") or {}).get("pod") == _COMPARE_PODS[0]
                                    and (item.get("dst") or {}).get("ip") == address
                                    and (item.get("dst") or {}).get("port") == 8080
                                    and str(item.get("verdict", "")).lower() == "dropped")
                                facts["policy_timeout"] = blocked["connection_timed_out"] if blocked["marker"] else None
                                facts["policy_other_error"] = blocked["connection_failed"] and not blocked["connection_timed_out"]
                                facts["policy_connected"] = blocked["connected"]
                                facts["hubble_dropped"] = bool(refs) if available else None
                                evidence.update({"blocked_probe": blocked, "observation_references": refs})
                        if policy_attempted:
                            removed = self._kubectl("delete", "-f", str(_DEPLOY / "policy-compare-cilium.yaml"), "--ignore-not-found")
                            evidence["policy_remove_rc"] = removed.returncode
                            policy_attempted = removed.returncode != 0
                            if removed.returncode == 0:
                                self.sleep(2.0)
                                restored = self._tcp_probe("pod/" + _COMPARE_PODS[0], "client", address, 8080)
                                evidence["restored_probe"] = restored
                                facts["restored_connected"] = restored["connected"] if restored["marker"] else None
        except Exception as error:
            facts["unknown_reason"] = "비교 실행 오류(" + _safe_exception(error) + ")"
        finally:
            cleanup: list[int | None] = []
            if policy_attempted:
                try:
                    cleanup.append(self._kubectl("delete", "-f", str(_DEPLOY / "policy-compare-cilium.yaml"), "--ignore-not-found").returncode)
                except Exception:
                    cleanup.append(None)
            if pods_attempted:
                try:
                    cleanup.append(self._kubectl("delete", "pod", *_COMPARE_PODS, "--ignore-not-found").returncode)
                except Exception:
                    cleanup.append(None)
            remaining = []
            for kind, name in (("pod", _COMPARE_PODS[0]), ("pod", _COMPARE_PODS[1]), ("ciliumnetworkpolicy", _COMPARE_POLICY)):
                try:
                    check = self._kubectl("get", kind, name, "--ignore-not-found", "-o", "name")
                    remaining.append({"kind": kind, "name": name, "rc": check.returncode, "present": bool(check.stdout.strip())})
                except Exception:
                    remaining.append({"kind": kind, "name": name, "rc": None, "present": None})
            facts["cleanup_ok"] = all(code == 0 for code in cleanup) and all(item["rc"] == 0 and not item["present"] for item in remaining)
            evidence["cleanup_returncodes"] = cleanup
            evidence["cleanup_remaining"] = remaining
        return facts, evidence

    def aws_13(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if not getattr(self.args, "allow_bundle_switch", False):
            return {"not_run_reason": "--allow-bundle-switch 없음"}, {}
        root = Path(getattr(self.args, "bundle_root", "/srv/serving/bundles"))
        current = root / "current"
        evidence: dict[str, Any] = {"bundle_root": str(root), "integrity_check": "SHA-256 byte comparison; not a signature"}
        if not current.is_symlink():
            return {"unknown_reason": "current가 상대 symlink가 아님"}, evidence
        previous = os.readlink(current)
        if Path(previous).name != previous or not (root / previous).is_dir():
            return {"unknown_reason": "이전 묶음 링크 대상이 안전한 상대 폴더가 아님"}, evidence
        bad = root / ("aws-acceptance-bad-" + uuid4().hex)
        swap = root / ("aws-acceptance-swap-" + uuid4().hex)
        evidence["previous_bundle"] = previous
        facts: dict[str, Any] = {}
        switched = False
        try:
            shutil.copytree(root / previous, bad, symlinks=False)
            # Keep the source group (Gateway reads via group 10000) so the bad
            # bundle is rejected for its hash, not for a permission error.
            source_gid = (root / previous).stat().st_gid
            for path in (bad, *bad.rglob("*")):
                try:
                    if path.stat().st_gid != source_gid:
                        os.chown(path, -1, source_gid)
                except PermissionError:
                    evidence["group_copy"] = "not_permitted"
                    break
            manifest = json.loads((bad / "bundle.json").read_text(encoding="utf-8"))
            role = next(item for item in manifest["files"] if item["role"] == "gateway_policy")
            target = bad / role["path"]
            if not target.resolve().is_relative_to(bad.resolve()) or not target.is_file():
                raise ValueError("bundle role path unsafe")
            data = target.read_bytes()
            if not data:
                raise ValueError("bundle role empty")
            target.write_bytes(bytes([data[0] ^ 1]) + data[1:])
            evidence["same_byte_length"] = target.stat().st_size == len(data)
            gateway_pods_before = self._gateway_pods()
            if not gateway_pods_before or any(not (item.get("metadata") or {}).get("uid") for item in gateway_pods_before):
                facts["unknown_reason"] = "재시작 전 Gateway Pod UID 조회 실패"
                return facts, evidence
            previous_uids = {(item.get("metadata") or {})["uid"] for item in gateway_pods_before}
            evidence["gateway_pod_uids_before"] = sorted(previous_uids)
            os.symlink(bad.name, swap)
            os.replace(swap, current)
            switched = True
            # The Gateway has no readiness probe: a refused bundle makes the new
            # Pod start, exit, and crash-loop, while `rollout status` can still
            # succeed. Judge by the container's exit and its log, not rollout.
            _bad_rollout, evidence["bad_restart"] = self._restart("serving-gateway", 45)
            crashed, crash_state = self._gateway_crashed(45.0, previous_uids)
            evidence["bad_gateway_state"] = crash_state
            hash_error = False
            if crashed and crash_state.get("pod_name"):
                reads: list[dict[str, Any]] = []
                deadline = self.monotonic() + 8.0
                for attempt in range(4):
                    for extra in (("--previous",), ()):
                        remaining = deadline - self.monotonic()
                        if reads and remaining <= 0:
                            break
                        logs = self._kubectl("logs", "pod/" + crash_state["pod_name"], "-c", "gateway", "--tail=100", *extra,
                                             timeout=min(3.0, max(remaining, 0.0)))
                        first_line_source = "stdout" if logs.stdout else "stderr"
                        first_line_text = logs.stdout if logs.stdout else logs.stderr
                        first_line = first_line_text.splitlines()[0] if first_line_text else ""
                        first_line = _redact(first_line, (*self.tokens.values(), self.wrong_token))
                        first_line = re.sub(
                            r'''(?i)(?<![\w.-])(["']?[a-z_][\w.-]*(?:key|token|secret|password|credential|auth)[\w.-]*["']?\s*[:=]\s*)(?:Bearer\s+\S+|"[^"]*"|'[^']*'|[^\s,;}\]]+)''',
                            r"\1[REDACTED]", first_line)
                        first_line = re.sub(
                            r'''(?i)(?<![\w.-])(--[a-z0-9_.-]*key[a-z0-9_.-]*\s+)(?:"[^"]*"|'[^']*'|\S+)''',
                            r"\1[REDACTED]", first_line)
                        first_line = re.sub(r"(?i)\b(bearer|token|secret|password|credential|auth)\s+\S+", r"\1 [REDACTED]", first_line)
                        first_line = first_line[:120]
                        reads.append({"attempt": attempt + 1, "source": "previous" if extra else "current",
                                      "returncode": logs.returncode, "stdout_length": len(logs.stdout),
                                      "stderr_length": len(logs.stderr), "first_line_source": first_line_source,
                                      "first_line_prefix": first_line})
                        if logs.returncode == 0 and ("sha256" in logs.stdout.lower() or "SHA-256" in logs.stdout):
                            hash_error = True
                            break
                    if hash_error or attempt == 3 or self.monotonic() >= deadline:
                        break
                    self.sleep(min(2.0, max(0.0, deadline - self.monotonic())))
                evidence["bad_gateway_log_reads"] = reads
            facts["bad_bundle_rejected"] = (True if hash_error else None) if crashed else (False if crashed is False else None)
            evidence["hash_error_seen"] = hash_error
        except (OSError, ValueError, KeyError, StopIteration, TypeError, json.JSONDecodeError) as error:
            facts["unknown_reason"] = "잘못된 묶음 준비 실패: " + _safe_exception(error)
        finally:
            cleanup_ok = True
            try:
                if switched:
                    os.symlink(previous, swap)
                    os.replace(swap, current)
                    cleanup_ok, evidence["recovery_restart"] = self._restart("serving-gateway", 120)
                    if cleanup_ok:
                        recovered, record, retry = self._retry_inference(30.0)
                        facts["recovered_200"] = recovered if retry.get("response_count", 1 if retry.get("last_status") is not None else 0) else None
                        evidence["recovered_request"] = {"record": record, "retry": retry}
                        if recovered:
                            try:
                                denied, request_id, _valid = self._completion(token=None, timeout=30)
                                denial = _record_fields(self._detail(request_id))
                                facts["recovered_denied"] = (denied.status == 401 and denial.get("result") == "DENIED" and denial.get("reason_code") == "AUTH_REQUIRED" and denial["delivery"]["attempted"] is False) if denial is not None else None
                                evidence["denied_request"] = {"http_status": denied.status, "record": denial}
                            except (OSError, URLError, TimeoutError) as error:
                                evidence["denied_request_error"] = _safe_exception(error)
                if bad.exists():
                    shutil.rmtree(bad)
            except (OSError, ValueError, URLError, TimeoutError) as error:
                cleanup_ok = False
                evidence["recovery_error"] = _safe_exception(error)
            facts["cleanup_ok"] = cleanup_ok and current.is_symlink() and os.readlink(current) == previous
            evidence["reconnect_at"] = self.reconnect_at
            evidence["recovery_forward"] = "tool_owned" if self.reconnect_at else "none"
        return facts, evidence

    def aws_14(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if not getattr(self.args, "allow_gateway_restart", False):
            return {"not_run_reason": "--allow-gateway-restart 없음"}, {}
        before_pods = self._kubectl("get", "pods", "-l", "app=serving-gateway", "-o", "json")
        before_doc = _json_object(before_pods.stdout.encode()) if before_pods.returncode == 0 else None
        before_items = before_doc.get("items") if before_doc else None
        before_uid = (before_items[0].get("metadata") or {}).get("uid") if isinstance(before_items, list) and len(before_items) == 1 else None
        if not isinstance(before_uid, str):
            return {"unknown_reason": "재시작 전 Gateway Pod UID 미확인"}, {}
        restart = self._kubectl("rollout", "restart", "deployment/serving-gateway")
        evidence: dict[str, Any] = {"restart_rc": restart.returncode, "pod_uid_before": before_uid}
        facts: dict[str, Any] = {"restart_ok": restart.returncode == 0}
        if restart.returncode != 0:
            return facts, evidence
        self._gateway_restart_started()
        startup_deadline = self.monotonic() + 120.0
        new_uid = None
        while self.monotonic() < startup_deadline:
            observed_pods = self._kubectl("get", "pods", "-l", "app=serving-gateway", "-o", "json")
            observed_doc = _json_object(observed_pods.stdout.encode()) if observed_pods.returncode == 0 else None
            items = observed_doc.get("items") if observed_doc else None
            active = [item for item in items or [] if isinstance(item, dict) and not (item.get("metadata") or {}).get("deletionTimestamp")]
            if len(active) == 1:
                new_uid = (active[0].get("metadata") or {}).get("uid")
                if isinstance(new_uid, str) and new_uid != before_uid:
                    break
            self.sleep(0.5)
        evidence["pod_uid_after"] = new_uid
        if not new_uid or new_uid == before_uid:
            return {"restart_ok": False}, evidence
        statuses: list[int | None] = []
        recovered = False
        first_503_at: float | None = None
        recovered_at: float | None = None
        reasons: list[str | None] = []
        while True:
            try:
                response, _request_id, valid = self._completion(token=self.tokens["RUNTIME_INFERENCE_TOKEN"], timeout=2.0)
                statuses.append(response.status)
                body = _json_object(response.body)
                error = body.get("error") if isinstance(body, dict) else None
                reasons.append(error.get("code") if isinstance(error, dict) else None)
                if response.status == 503 and first_503_at is None:
                    first_503_at = self.monotonic()
                if response.status == 200 and valid:
                    recovered = True
                    recovered_at = self.monotonic()
                    break
            except (OSError, URLError, TimeoutError):
                pass
            if self.monotonic() >= startup_deadline or (first_503_at is not None and self.monotonic() - first_503_at > 10.0):
                break
            self.sleep(0.5)
        elapsed = recovered_at - first_503_at if first_503_at is not None and recovered_at is not None else None
        rollout = self._kubectl("rollout", "status", "deployment/serving-gateway", "--timeout=120s", timeout=130)
        facts.update({"restart_ok": rollout.returncode == 0, "initial_503": (first_503_at is not None and "TARGET_UNAVAILABLE" in reasons) if statuses else None, "recovered_200": recovered if statuses else None, "within_10s": elapsed <= 10.0 if elapsed is not None else (False if first_503_at is not None and self.monotonic() - first_503_at > 10.0 else None)})
        pod_result = self._kubectl("get", "pods", "-l", "app=serving-gateway", "-o", "json")
        pod_doc = _json_object(pod_result.stdout.encode()) if pod_result.returncode == 0 else None
        pods = pod_doc.get("items") if pod_doc else None
        started_at = None
        if isinstance(pods, list) and len(pods) == 1:
            statuses_pod = (pods[0].get("status") or {}).get("containerStatuses") or []
            if statuses_pod:
                started_at = ((statuses_pod[0].get("state") or {}).get("running") or {}).get("startedAt")
        snapshot = None
        try:
            snapshot = json.loads((self.observation_dir / "target-pods.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        evidence.update({"statuses": statuses, "reason_codes": reasons, "elapsed_from_first_503_s": round(elapsed, 3) if elapsed is not None else None, "gateway_started_at": started_at, "target_snapshot_collected_at": snapshot.get("collected_at") if isinstance(snapshot, dict) else None, "not_before_comparison": "target collected_at must be at or after Gateway startup", "rollout_rc": rollout.returncode, "reconnect_at": self.reconnect_at, "recovery_forward": "tool_owned" if self.reconnect_at else "none"})
        return facts, evidence

    def _collector_interruption(self, *, restart_gateway: bool) -> tuple[dict[str, Any], dict[str, Any]]:
        if not getattr(self.args, "allow_collector_stop", False):
            return {"not_run_reason": "--allow-collector-stop 없음"}, {}
        if restart_gateway and not getattr(self.args, "allow_gateway_restart", False):
            return {"not_run_reason": "--allow-gateway-restart 없음"}, {}
        before = self._timeline()
        if not restart_gateway:
            if (before is None or not isinstance(before.get("extraction"), dict)
                    or before["extraction"].get("engine") != self.engine
                    or not isinstance(before.get("collector"), dict)
                    or before["collector"].get("state") != "running" or not before.get("entries")):
                return {"unknown_reason": "단계 시간표 비활성 또는 읽기 실패"}, {}
            pod, _pod_result = self._engine_pod()
            collector = before["collector"]
            try:
                fresh = not ExecPolicy._collector_stale(collector)
            except (KeyError, TypeError, ValueError, OverflowError):
                fresh = False
            if (not fresh or not pod or not pod.get("uid")
                    or collector.get("pod_uid") != pod["uid"]):
                return {"unknown_reason": "현재 엔진 Pod의 신선한 단계 시간표 미확인"}, {
                    "timeline_before": collector, "pod_before": pod, "timeline_fresh": fresh}
        evidence: dict[str, Any] = {"timeline_before": (before or {}).get("collector")}
        facts: dict[str, Any] = {}
        stop = self.runner.run(["systemctl", "--user", "stop", "byul-observation-feed"], timeout=30)
        facts["stop_ok"] = stop.returncode == 0
        try:
            if stop.returncode == 0:
                if restart_gateway:
                    restart = self._kubectl("rollout", "restart", "deployment/serving-gateway")
                    facts["restart_ok"] = restart.returncode == 0
                    if restart.returncode == 0:
                        self._gateway_restart_started()
                        rollout = self._kubectl("rollout", "status", "deployment/serving-gateway", "--timeout=120s", timeout=130)
                        facts["restart_ok"] = rollout.returncode == 0
                    statuses: list[int | None] = []
                    reasons: list[str | None] = []
                    for _ in range(30):
                        try:
                            response, _id, _valid = self._completion(token=self.tokens["RUNTIME_INFERENCE_TOKEN"], timeout=3)
                            statuses.append(response.status)
                            body = _json_object(response.body)
                            error = body.get("error") if isinstance(body, dict) else None
                            reasons.append(error.get("code") if isinstance(error, dict) else None)
                        except (OSError, URLError, TimeoutError):
                            pass
                        if statuses and statuses[-1] == 503:
                            break
                        self.sleep(1.0)
                    facts["observed_503"] = (503 in statuses and "TARGET_UNAVAILABLE" in reasons) if any(status is not None for status in statuses) else None
                    evidence["stopped_request_statuses"] = statuses
                    evidence["stopped_request_reasons"] = reasons
                else:
                    written = _parse_time(before["collector"].get("written_at"))
                    if written is None:
                        facts["unknown_reason"] = "시간표 쓰기 시각 미확인"
                    else:
                        deadline = written + timedelta(seconds=15, milliseconds=100)
                        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
                        self.sleep(max(0.0, remaining))
                        timeline = self._timeline()
                        evidence["timeline_stopped"] = (timeline or {}).get("collector")
                        same_snapshot = timeline is not None and timeline.get("collector", {}).get("written_at") == before["collector"].get("written_at")
                        stale_by_code = ExecPolicy._collector_stale(timeline["collector"]) if same_snapshot else None
                        facts["expired_unknown"] = stale_by_code if same_snapshot else None
                        evidence["phase_expiry"] = {"same_snapshot": same_snapshot, "stale_by_runtime_code": stale_by_code,
                                                    "inference": "classify_exec clears timeline and returns phase unknown when _collector_stale is true; no fresh Tetragon event is available while feed is stopped"}
                        try:
                            response, _id, valid = self._completion(token=self.tokens["RUNTIME_INFERENCE_TOKEN"])
                            facts["request_200"] = response.status == 200 and valid
                            evidence["stopped_request_status"] = response.status
                        except (OSError, URLError, TimeoutError) as error:
                            evidence["stopped_request_error"] = _safe_exception(error)
                            facts["request_200"] = False
        except Exception as error:
            facts["unknown_reason"] = "수집 중단 시험 오류(" + _safe_exception(error) + ")"
        finally:
            try:
                start = self.runner.run(["systemctl", "--user", "start", "byul-observation-feed"], timeout=30)
            except Exception as error:
                start = CommandResult(1)
                evidence["collector_start_error"] = _safe_exception(error)
            recovered = False
            if start.returncode == 0:
                recovered, states = self._wait_collectors_ok(45.0)
                evidence["collector_recovered_states"] = {key: value.get("state") for key, value in states.items() if key in _OBSERVATION_SOURCES}
            facts["cleanup_ok"] = start.returncode == 0 and recovered
            evidence["collector_start_rc"] = start.returncode
            if restart_gateway and facts["cleanup_ok"]:
                facts["recovered_200"], record, retry = self._retry_inference(45.0)
                if not retry.get("response_count", 1 if retry.get("last_status") is not None else 0):
                    facts["recovered_200"] = None
                evidence["recovery_request"] = {"record": record, "retry": retry}
            if restart_gateway:
                evidence["reconnect_at"] = self.reconnect_at
                evidence["recovery_forward"] = "tool_owned" if self.reconnect_at else "none"
        return facts, evidence

    def aws_15(self) -> tuple[dict[str, Any], dict[str, Any]]:
        return self._collector_interruption(restart_gateway=True)

    def aws_16(self) -> tuple[dict[str, Any], dict[str, Any]]:
        return self._collector_interruption(restart_gateway=False)

    def aws_17(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if not getattr(self.args, "allow_engine_restart", False):
            return {"not_run_reason": "--allow-engine-restart 없음"}, {}
        before, _result = self._engine_pod()
        timeline_before = self._timeline()
        if not before or not before.get("uid") or timeline_before is None:
            return {"unknown_reason": "이전 Pod 또는 단계 시간표 미확인"}, {}
        if (not isinstance(timeline_before.get("extraction"), dict)
                or timeline_before["extraction"].get("engine") != self.engine):
            return {"unknown_reason": "현재 엔진의 단계 시간표 미확인"}, {}
        collector_before = timeline_before.get("collector") or {}
        try:
            fresh = not ExecPolicy._collector_stale(collector_before)
        except (KeyError, TypeError, ValueError, OverflowError):
            fresh = False
        if (collector_before.get("pod_uid") != before["uid"] or collector_before.get("state") != "running"
                or not timeline_before.get("entries") or not fresh):
            return {"unknown_reason": "이전 Pod의 활성 단계 수집 미확인"}, {"timeline_before": collector_before}
        evidence: dict[str, Any] = {"pod_before": before, "timeline_before": collector_before, "tetragon_counts_before": self._tetragon_counts()}
        accounting_since = _now()
        restart = self._kubectl("rollout", "restart", "deployment/serving-engine")
        rollout = self._kubectl("rollout", "status", "deployment/serving-engine", "--timeout=25m", timeout=1510) if restart.returncode == 0 else CommandResult(1)
        after, _result = self._engine_pod()
        facts: dict[str, Any] = {"pod_changed": bool(after and after.get("uid") and after["uid"] != before["uid"] and rollout.returncode == 0)}
        evidence["pod_after"] = after
        if not facts["pod_changed"]:
            return facts, evidence
        # The raw Tetragon identity is process_exec.process.pod.uid.
        started = _now()
        executed = self._kubectl("exec", "pod/" + after["name"], "-c", "engine", "--", "/bin/sh", "-c", "true", timeout=15)
        evidence["probe_exec_rc"] = executed.returncode
        raw_uid = None
        raw_time = None
        path = self.observation_dir / "tetragon.jsonl"
        for attempt in range(8):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        item = json.loads(line)
                        process = (item.get("process_exec") or {}).get("process") or {}
                        pod = process.get("pod") or {}
                        if _after(item.get("time"), started) and process.get("binary") in {"/bin/sh", "/usr/bin/sh", "/bin/dash", "/usr/bin/dash"} and pod.get("uid") == after["uid"]:
                            raw_uid, raw_time = pod["uid"], item["time"]
                            break
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, AttributeError):
                pass
            if raw_uid or attempt == 7:
                break
            self.sleep(2.0)
        timeline_at_probe = self._timeline()
        if (not isinstance(timeline_at_probe, dict)
                or not isinstance(timeline_at_probe.get("extraction"), dict)
                or timeline_at_probe["extraction"].get("engine") != self.engine):
            return {"unknown_reason": "새 Pod의 엔진별 단계 시간표 미확인"}, evidence
        # The Gateway ingests the feed a few seconds after the raw event.
        available, rows, matched = False, [], []
        for attempt in range(10):
            available, rows = self._tetragon_observations(started)
            matched = [row for row in rows if row.get("kind") == "process_exec" and row.get("binary") in {"/bin/sh", "/usr/bin/sh", "/bin/dash", "/usr/bin/dash"} and self._engine_event(row, after["name"]) and _parse_time(row.get("observed_at")) == _parse_time(raw_time)]
            if matched or not raw_uid or attempt == 9:
                break
            self.sleep(2.0)
        probe_collector = (timeline_at_probe or {}).get("collector") or {}
        old_stale = (probe_collector.get("pod_uid") == before["uid"] and all(row.get("phase") in (None, [], ["unknown"]) for row in matched))
        # A collector UID alone cannot prove that entries were reset. Compare
        # every entry start with the new container start and old last entry.
        old_written = _parse_time((timeline_before.get("collector") or {}).get("written_at")) if isinstance(timeline_before, dict) else None
        previous_entries = timeline_before.get("entries") or []
        previous_starts = [_parse_time(row.get("from")) for row in previous_entries if isinstance(row, dict)]
        old_last_start = max((item for item in previous_starts if item is not None), default=None)
        probe_entries = (timeline_at_probe or {}).get("entries") or []
        fresh_entries = _timeline_reset(timeline_before, timeline_at_probe, after.get("started_at"))
        fresh_phases = {row.get("phase") for row in probe_entries if isinstance(row, dict)}
        matched_phases = {phase for row in matched for phase in (row.get("phase") if isinstance(row.get("phase"), list) else [row.get("phase")]) if phase not in (None, "unknown")}
        new_fresh = (probe_collector.get("pod_uid") == after["uid"] and old_written is not None and fresh_entries
                     and matched_phases <= fresh_phases
                     and all(row.get("phase_timeline_fresh") is True
                             and (_parse_time(row.get("phase_timeline_written_at")) or old_written) > old_written
                             for row in matched))
        facts["old_phase_absent"] = (old_stale or new_fresh) if raw_uid and available and matched else None
        evidence.update({"raw_process_pod_uid": raw_uid, "raw_event_time": raw_time, "timeline_at_probe": probe_collector, "probe_phase": [row.get("phase") for row in matched], "phase_source": "old_uid_rejected" if old_stale else "new_uid_timeline" if new_fresh else None,
                         "timeline_reset": {"new_container_started_at": after.get("started_at"), "old_last_entry_start": old_last_start.isoformat() if old_last_start else None, "entries_after_new_start": fresh_entries, "matched_phases_in_new_timeline": matched_phases <= fresh_phases}})
        timeline_new = None
        for attempt in range(20):
            timeline_new = self._timeline()
            if (timeline_new or {}).get("collector", {}).get("pod_uid") == after["uid"]:
                break
            if attempt < 19:
                self.sleep(1.0)
        if (not isinstance(timeline_new, dict)
                or not isinstance(timeline_new.get("extraction"), dict)
                or timeline_new["extraction"].get("engine") != self.engine):
            return {"unknown_reason": "새 Pod의 엔진별 단계 시간표 미확인"}, evidence
        facts["timeline_new_uid"] = (timeline_new or {}).get("collector", {}).get("pod_uid") == after["uid"]
        facts["timeline_reset"] = (_timeline_reset(timeline_before, timeline_new, after.get("started_at"))
                                   if facts["timeline_new_uid"] and (timeline_new or {}).get("entries") else None)
        evidence["timeline_after"] = (timeline_new or {}).get("collector")
        evidence["tetragon_counts_after"] = self._tetragon_counts()
        counts_before, counts_after = evidence["tetragon_counts_before"], evidence["tetragon_counts_after"]
        if counts_before is not None and counts_after is not None:
            raw_exit_count = self._raw_process_exit_count(accounting_since)
            evidence["process_exit_accounting"] = {
                "raw_process_exit_count": raw_exit_count,
                "skipped_unsupported_delta": counts_after["skipped_unsupported_total"] - counts_before["skipped_unsupported_total"],
                "corrupt_lines_delta": counts_after["corrupt_lines_total"] - counts_before["corrupt_lines_total"],
                "expected_pattern_seen": (raw_exit_count is not None and raw_exit_count > 0
                                          and counts_after["skipped_unsupported_total"] > counts_before["skipped_unsupported_total"]
                                          and counts_after["corrupt_lines_total"] == counts_before["corrupt_lines_total"]),
                "note": "process_exit 원시 행과 같은 구간에서 비교; 건너뜀 증가·손상 증가 없음은 보조 근거. 화면 확인은 사람 수행",
            }
        return facts, evidence

    def write_report(self) -> tuple[Path, Path]:
        report = {
            "schema": "runtime-aws-acceptance/1.0",
            "run_id": self.evidence_dir.name,
            "started_at": self.run_started_at,
            "ended_at": _now(),
            "kube_context": self.context,
            "namespace": self.namespace,
            "engine": self.engine,
            "fixed_request_sha256": _fixed_request_hash(),
            "fixed_requests": [{"route": route, "max_tokens": payload["max_tokens"],
                                "payload_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True,
                                  separators=(",", ":")).encode()).hexdigest()}
                               for route, payload in FIXED_REQUESTS],
            "engine_pod_configuration": self._pod_configuration(),
            "gateway_url": self.gateway,
            "port_forward": {"recovery": "tool_owned_kubectl_port_forward" if self.reconnect_at else "external_url_untouched" if not self.gateway_loopback else "none_opened",
                             "reconnect_at": self.reconnect_at,
                             "after_exit": "tool_owned_forward_closed; user_forward_must_be_restarted_if_its_pod_died" if self.gateway_loopback else "unchanged"},
            "observation_dir": str(self.observation_dir),
            "requested_cases": list(self.args.cases),
            "allow_collector_stop": self.args.allow_collector_stop,
            "allow_engine_restart": self.args.allow_engine_restart,
            "allow_compare_policy": getattr(self.args, "allow_compare_policy", False),
            "allow_bundle_switch": getattr(self.args, "allow_bundle_switch", False),
            "allow_gateway_restart": getattr(self.args, "allow_gateway_restart", False),
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
                    "unavailability, a 200 request with target marked unverified after the stale window, "
                    "collector and target recovery, and recovered inference. Gap records are supplementary."
                ),
                "AWS-13 compares bundle file bytes to SHA-256 in bundle.json; this is not a signature or approval verification.",
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
            "# AWS 런타임 인수 시험 보고서",
            "",
            "이 보고서는 해당 환경에서 한 번 실행한 결과다. 통과가 기능 완성을 뜻하지 않는다.",
            f"- 실행: `{safe_report['run_id']}`",
            f"- 환경/네임스페이스: `{self.context}` / `{self.namespace}`",
            f"- 전체 결과: **{safe_report['overall']}**",
            "",
            "## 시험 결과",
            "",
            "| 시험 | 결과 | RT | 확인한 값 | 판정 이유 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for case in safe_report["cases"]:
            reason = _report_reason(case).replace("|", "\\|")
            summary = _report_summary(case).replace("|", "\\|")
            lines.append(
                f"| {case['case_id']} {case['title']} | {case['result']} | "
                f"{', '.join(case['rt_ids'])} | {summary} | {reason} |"
            )
        lines.extend(
            [
                "",
                "상세 명령, HTTP 상태, 요청 ID, 사용한 record 필드와 관측 참조는 `report.json`에 있다.",
                "응답 본문, prompt, token 및 token hash는 보고서에 저장하지 않는다.",
                "AWS-13 무결성 검사는 SHA-256 바이트 대조이며 서명 검증이 아니다.",
                "관측 화면 확인은 사람이 수행한다. 도구 결과만으로 화면 표시를 통과 처리하지 않는다.",
                "",
                "## 판정 기준",
                "",
                *[f"- {case['case_id']}: {REPORT_CASES[case['case_id']].criteria}" for case in safe_report["cases"]],
                "",
                "## 아는 한계",
                "",
                *[f"- {case['case_id']}: {REPORT_CASES[case['case_id']].gap}" for case in safe_report["cases"]],
                "",
            ]
        )
        for case in safe_report["cases"]:
            if case["case_id"] != "AWS-11":
                continue
            evidence = case.get("evidence") or {}
            warnings = evidence.get("false_warnings")
            lines.extend(["## AWS-11 잘못된 경고 목록", ""])
            phase_evidence = evidence.get("phase_evidence")
            if isinstance(phase_evidence, Mapping):
                lines.extend([
                    f"단계 근거: 유효 실행 {_report_probe_value(phase_evidence.get('valid_exec_count'))}건, "
                    f"미확인 실행 {_report_probe_value(phase_evidence.get('unknown_exec_count'))}건, "
                    f"신선도 {_report_value(phase_evidence.get('freshness'), allowed={'fresh', 'legacy_unbounded', 'unconfirmed'})}, "
                    f"출처 {', '.join(_report_value(item, allowed={'declared', 'engine_signal', 'test_controller'}) for item in phase_evidence.get('bases', [])) or '확인 못 함'}, "
                    f"시간표 쓰기 시각 {_report_probe_value(phase_evidence.get('timeline_written_at', [None])[-1] if phase_evidence.get('timeline_written_at') else None)}", "",
                ])
            if evidence.get("warning_list_complete") is not True:
                lines.extend(["관측 조회 실패·수집 공백·수집기 불가 또는 최근 50행 제한으로 전체 목록·건수 확인 못 함. 아래는 조회된 일부다.", ""])
            if not isinstance(warnings, list):
                lines.append("관측 목록 확인 못 함")
            elif not warnings:
                lines.append("관측된 잘못된 경고 0건 (단계 근거와 목록 완전성은 별도 판정)")
            else:
                for item in warnings:
                    if not isinstance(item, Mapping):
                        continue
                    lines.append(
                        f"- {_report_probe_value(item.get('observed_at'))} · "
                        f"{_report_binary_path(item.get('binary'))} · "
                        f"{_report_value(item.get('exec_verdict'), allowed={'outside_allowlist', 'phase_signal_unverified', 'exec_from_cache'})}"
                    )
            lines.extend(["", "응답 시간은 참고값이며 판정에 쓰지 않는다. 탐지 시간은 기준 없이 판정에 쓰지 않는다.", ""])
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
            "AWS-09": self.aws_09,
            "AWS-10": self.aws_10,
            "AWS-11": self.aws_11,
            "AWS-12": self.aws_12,
            "AWS-13": self.aws_13,
            "AWS-14": self.aws_14,
            "AWS-15": self.aws_15,
            "AWS-16": self.aws_16,
            "AWS-17": self.aws_17,
            "AWS-18": self.aws_18,
            "AWS-19": self.aws_19,
            "AWS-20": self.aws_20,
        }
        try:
            for case_id in self.args.cases:
                if case_id == "AWS-09" and "AWS-11" in self.args.cases and "AWS-10" in self.args.cases:
                    self.detection_started_at = _now()
                    try:
                        self.normal_before = self._normal_control()
                    except Exception as error:
                        self.normal_before = {"error": _safe_exception(error)}
                self._run_case(case_id, functions[case_id])
            json_path, md_path = self.write_report()
            overall = _overall(self.cases)
            print(f"overall: {overall}")
            print(f"JSON: {json_path}")
            print(f"Markdown: {md_path}")
            return 0 if overall == "passed" else (1 if overall == "failed" else 3)
        finally:
            self._close_forward()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AWS GPU lab runtime acceptance; mutating cases require explicit flags"
    )
    parser.add_argument(
        "--kube-context",
        required=True,
        help="explicit Kubernetes context; every kubectl command receives --context",
    )
    parser.add_argument("--namespace", default="serving-lab")
    parser.add_argument("--engine", choices=("vllm", "sglang"), default="vllm")
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
        help="case subset; repeatable (default: AWS-01 through AWS-11); AWS-12 through AWS-20 require --cases",
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
    parser.add_argument("--allow-compare-policy", action="store_true", help="allow AWS-12 temporary comparison Pods and Cilium policy")
    parser.add_argument("--allow-bundle-switch", action="store_true", help="allow AWS-13 temporary bad bundle switch and recovery")
    parser.add_argument("--bundle-root", default="/srv/serving/bundles", help="host bundle root mounted by Gateway")
    parser.add_argument("--allow-gateway-restart", action="store_true", help="allow AWS-14/15 Gateway rollout restart")
    parser.add_argument("--allow-rule-switch", action="store_true", help="AWS-20에서 동일 Tetragon 정책의 monitor↔enforce 모드 전환 허용")
    parser.add_argument("--aws20-monitor-manifest", help="현재 정책과 대조할 1.7.1 monitor-mode 후보 manifest")
    parser.add_argument("--aws20-probe-pod", help="Sigkill 계열을 먼저 시험할 별도 Ready Pod 이름")
    parser.add_argument("--allow-engine-sigkill", action="store_true", help="AWS-20 엔진 대상 Sigkill 계열 시험 명시 허용")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.cases = _parse_cases(args.cases)
        validate_kube_context(args.kube_context)
        if "AWS-20" in args.cases and (not args.allow_rule_switch or not args.aws20_monitor_manifest):
            parser.error("AWS-20 requires --allow-rule-switch and --aws20-monitor-manifest")
    except (argparse.ArgumentTypeError, ValueError) as error:
        parser.error(str(error))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "inventory":
        return phase_exec_inventory(argv[1:])
    try:
        args = parse_args(argv)
        return AWSAcceptance(args).run()
    except ConfigurationError as error:
        print(f"AWS acceptance configuration error: {error}", file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError) as error:
        print(f"AWS acceptance failed before report: {_safe_exception(error)}", file=sys.stderr)
        return 2


def phase_exec_inventory(argv: Sequence[str]) -> int:
    """Read-only first-pass stage/Pod/path counts; never classify verdicts."""
    from collections import Counter
    from serving_runtime.enforcement.events import parse_tetragon, UnsupportedTetragonEvent
    from serving_runtime.enforcement.execpolicy import parse_timeline

    parser = argparse.ArgumentParser(prog="aws_lab_acceptance.py inventory")
    parser.add_argument("--tetragon", required=True, type=Path)
    parser.add_argument("--timeline", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        timeline = json.loads(args.timeline.read_text(encoding="utf-8"))
        entries = parse_timeline(timeline)
        collector = timeline.get("collector") or {}
        target_uid = collector.get("pod_uid")
        target_name = collector.get("pod_name")
        collector_written = _parse_time(collector.get("written_at"))
        counts: Counter[tuple[str, str, str]] = Counter()
        skipped = 0
        with args.tetragon.open(encoding="utf-8") as source:
            for raw in source:
                document = json.loads(raw)
                if "process_exec" not in document:
                    continue
                try:
                    item = parse_tetragon(document, data_origin="real")
                except (ValueError, UnsupportedTetragonEvent):
                    skipped += 1
                    continue
                process = document["process_exec"].get("process", {})
                original_pod = process.get("pod", {})
                if not isinstance(original_pod, dict):
                    original_pod = {}
                pod = item.get("pod") or {}
                pod_name = pod.get("pod") or "unknown"
                is_engine = (pod.get("namespace") == "serving-lab"
                             and isinstance(original_pod.get("pod_labels"), dict)
                             and original_pod["pod_labels"].get("app") == "serving-engine")
                uid = original_pod.get("uid")
                same_pod = is_engine and (
                    isinstance(uid, str) and uid == target_uid
                    or uid is None and isinstance(target_name, str) and pod_name == target_name
                )
                # A Pod name is unique for its lifetime. Use it when this
                # Tetragon version omits UID; never assign a different Pod's phase.
                at = _parse_time(item.get("observed_at"))
                phase_fresh = (collector.get("state") == "running" and at is not None
                               and collector_written is not None
                               and at <= collector_written + timedelta(seconds=15))
                phases = sorted({entry.phase for entry in entries
                                 if same_pod and phase_fresh and entry.active(at)})
                counts[("+".join(phases) if phases else "unknown", pod_name, item["binary"])] += 1
        report = {"schema": "runtime-phase-exec-inventory/1.0", "purpose": "observation_only",
                  "timeline_pod_uid_available": isinstance(target_uid, str),
                  "timeline_pod_name_available": isinstance(target_name, str),
                  "skipped": skipped,
                  "counts": [{"phase": phase, "pod": pod, "path": path, "count": count}
                             for (phase, pod, path), count in sorted(counts.items())]}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"phase inventory failed: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
