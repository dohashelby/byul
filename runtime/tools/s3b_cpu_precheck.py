"""S3B CPU pre-check: run S3-01~06 through the Gateway against a real vLLM.

This is a pre-check on a local vLLM (for example the CPU image), not the
approved GPU S3B test. It expects these to be running already:

- a vLLM OpenAI-compatible server (default http://127.0.0.1:8000)
- the Gateway in vLLM mode (default http://127.0.0.1:18080), started with
  ``serving-runtime mvp --backend vllm --backend-config <state-dir>/backend.json
  --data-dir <state-dir>/data``

The vLLM API key is read from ``<state-dir>/vllm-api-key`` and is never
printed or written to the report. S3-05 stops and restarts the vLLM Docker
container only when ``--allow-engine-restart`` is given; otherwise that part
is reported as not run. The report is written to ``<state-dir>/reports/``.

Usage:
    .venv/bin/python tools/s3b_cpu_precheck.py [--allow-engine-restart]
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = Path.home() / ".local/state/serving-runtime/s3b-cpu"
PROMPT = "Say hello in one short sentence."
sys.path.insert(0, str(REPO_ROOT / "src"))

from serving_runtime.mvp.backend import (
    is_valid_credential_value,
    normalize_credential_value,
)


class PrecheckConfigurationError(ValueError):
    """A safe-to-display preflight configuration error."""


class TemporaryGatewayError(RuntimeError):
    """A temporary Gateway could not be started without ambiguity."""


def _overall(results: list[dict[str, Any]]) -> str:
    if any(item["passed"] is False for item in results):
        return "failed"
    if any(item["passed"] is None for item in results):
        return "partial"
    return "passed"


def _backend_kinds_for_request_ids(
    records: list[dict[str, Any]], request_ids: set[str]
) -> set[str]:
    """Return backend kinds only for requests issued by this tool run."""

    return {
        item["backend_kind"]
        for item in records
        if item.get("request_id") in request_ids
        and isinstance(item.get("backend_kind"), str)
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wait(predicate: Callable[[], Any], timeout: float) -> Any:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    return predicate()


class Precheck:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.state = Path(args.state_dir).expanduser()
        gateway = urlsplit(args.gateway)
        self.gateway_host = gateway.hostname or "127.0.0.1"
        self.gateway_port = gateway.port or 80
        self.vllm = args.vllm_url.rstrip("/")
        try:
            with (self.state / "vllm-api-key").open(
                "r", encoding="utf-8", newline=""
            ) as key_file:
                key = key_file.read()
        except (OSError, UnicodeError) as error:
            raise PrecheckConfigurationError(
                "vLLM API key file could not be read; the key was not displayed"
            ) from error
        key = normalize_credential_value(key)
        if not is_valid_credential_value(key):
            raise PrecheckConfigurationError(
                "vLLM API key is invalid; use non-empty visible ASCII, optionally "
                "followed by one LF or CRLF (the key was not displayed)"
            )
        self.key = key
        self.results: list[dict[str, Any]] = []

    # ---- HTTP helpers -------------------------------------------------
    def body(self, **updates: Any) -> bytes:
        document = {
            "model": self.args.model_alias,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": 16,
        }
        document.update(updates)
        return json.dumps(document).encode("utf-8")

    def post(self, payload: bytes, port: int | None = None) -> tuple[int, str | None, bytes]:
        target_port = self.gateway_port if port is None else port
        url = f"http://{self.gateway_host}:{target_port}/v1/chat/completions"
        request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=300) as response:
                return response.status, response.headers.get("X-Request-ID"), response.read()
        except HTTPError as error:
            with error:
                return error.code, error.headers.get("X-Request-ID"), error.read()

    def get(self, path: str, port: int | None = None) -> dict[str, Any]:
        target_port = self.gateway_port if port is None else port
        with urlopen(f"http://{self.gateway_host}:{target_port}{path}", timeout=30) as response:
            return json.loads(response.read())

    def final(self, request_id: str, port: int | None = None) -> tuple[dict[str, Any], dict[str, Any] | None]:
        detail = self.get(f"/api/requests/{request_id}", port)
        finals = [item for item in detail["gateway_records"] if item.get("phase") == "final"]
        return detail, (finals[-1] if finals else None)

    def collector(self, source: str, port: int | None = None) -> dict[str, Any]:
        return next(item for item in self.get("/api/collectors", port)["collectors"] if item["source"] == source)

    def vllm_metric(self, name: str) -> float:
        request = Request(f"{self.vllm}/metrics", headers={"Authorization": f"Bearer {self.key}"})
        with urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
        return sum(
            float(line.rsplit(" ", 1)[1])
            for line in text.splitlines()
            if line.startswith(name + "{") or line.startswith(name + " ")
        )

    def vllm_health(self) -> bool:
        try:
            with urlopen(f"{self.vllm}/health", timeout=5) as response:
                return response.status == 200
        except OSError:
            return False

    def vllm_logs_since(self, since: str) -> str:
        """Container logs without any line that could contain the API key."""

        completed = subprocess.run(
            ["docker", "logs", "--since", since, self.args.container],
            capture_output=True,
            text=True,
            check=False,
        )
        lines = (completed.stdout + completed.stderr).splitlines()
        return "\n".join(line for line in lines if "api_key" not in line.lower() and self.key not in line)

    @staticmethod
    def _received_log_present(logs: str, request_id: str | None) -> bool:
        return bool(request_id and f"Received request chatcmpl-{request_id}" in logs)

    @staticmethod
    def _aborted_log_present(logs: str, request_id: str | None) -> bool:
        return bool(request_id and f"Request chatcmpl-{request_id} aborted." in logs)

    def stream(self, payload: bytes, *, stop_after_first: bool = False,
               hold: threading.Event | None = None) -> tuple[int, str | None, bytes, float | None, float | None]:
        connection = http.client.HTTPConnection(self.gateway_host, self.gateway_port, timeout=300)
        connection.request("POST", "/v1/chat/completions", body=payload, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        request_id = response.getheader("X-Request-ID")
        if response.status != 200:
            data = response.read()
            connection.close()
            return response.status, request_id, data, None, None
        started = time.monotonic()
        first_at: float | None = None
        data = b""
        while True:
            chunk = response.read1(1024)
            if not chunk:
                break
            if first_at is None:
                first_at = time.monotonic() - started
                if hold is not None:
                    hold.wait(300)
                if stop_after_first:
                    if connection.sock is not None:
                        connection.sock.close()
                    connection.close()
                    return 200, request_id, chunk, first_at, None
            data += chunk
        connection.close()
        return 200, request_id, data, first_at, time.monotonic() - started

    def _port_available(self, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((self.gateway_host, port))
        except OSError:
            return False
        return True

    def stop_gateway(self, process: subprocess.Popen[bytes]) -> dict[str, Any]:
        cleanup: dict[str, Any] = {"terminate_attempted": True, "killed": False}
        try:
            process.terminate()
            process.wait(timeout=10)
            cleanup["result"] = "terminated"
        except subprocess.TimeoutExpired:
            cleanup["killed"] = True
            try:
                process.kill()
                process.wait(timeout=10)
                cleanup["result"] = "killed_after_timeout"
            except (OSError, subprocess.TimeoutExpired):
                cleanup["result"] = "kill_failed"
        except OSError:
            cleanup["result"] = "terminate_failed"
        return cleanup

    def spawn_gateway(
        self,
        port: int,
        extra: list[str],
        *,
        additional_ports: tuple[int, ...] = (),
    ) -> subprocess.Popen[bytes]:
        unavailable = [
            candidate
            for candidate in (port, *additional_ports)
            if not self._port_available(candidate)
        ]
        if unavailable:
            joined = ", ".join(str(candidate) for candidate in unavailable)
            raise TemporaryGatewayError(f"target port(s) already in use: {joined}")
        process = subprocess.Popen(
            [sys.executable, "-m", "serving_runtime", "mvp", "--gateway-port", str(port), *extra],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        ready = _wait(lambda: process.poll() is None and self._port_ready(port), 20)
        if not ready:
            cleanup = self.stop_gateway(process)
            raise TemporaryGatewayError(
                f"temporary Gateway did not become ready on port {port}; "
                f"cleanup={cleanup['result']}"
            )
        return process

    def _port_ready(self, port: int) -> bool:
        try:
            self.get("/api/status", port)
            return True
        except (OSError, ValueError):
            return False

    def record(self, case_id: str, description: str, passed: bool | None,
               actual: dict[str, Any], request_ids: list[str | None], note: str = "") -> None:
        self.results.append({
            "case_id": case_id,
            "description": description,
            "passed": passed,
            "actual": actual,
            "evidence_request_ids": [item for item in request_ids if item],
            "note": note,
        })
        label = "통과" if passed else ("미실행" if passed is None else "실패")
        print(f"{case_id}: {label} — {description}")

    # ---- cases --------------------------------------------------------
    def s3_01(self) -> str:
        since = _now()
        status, request_id, raw = self.post(self.body())
        detail, final = self.final(request_id)
        content = json.loads(raw)["choices"][0]["message"]["content"] if status == 200 else None
        s_status, s_id, s_data, first_at, total = self.stream(self.body(stream=True, max_tokens=24))
        _, s_final = self.final(s_id)
        logs = self.vllm_logs_since(since)
        ends_done = s_data.rstrip().endswith(b"data: [DONE]")
        request_logs = {
            request_id: self._received_log_present(logs, request_id),
            s_id: self._received_log_present(logs, s_id),
        }
        request_log_ok = all(request_logs.values())
        passed = bool(
            status == 200 and content and final and final["final_outcome"] == "completed"
            and final["backend_kind"] == "vllm" and s_status == 200 and ends_done
            and first_at is not None and total is not None and first_at < total
            and s_final and s_final["final_outcome"] == "completed"
            and (not self.args.require_backend_request_log or request_log_ok)
        )
        self.record("S3-01", "같은 고정 모델 일반·stream 실제 추론과 종료", passed, {
            "general_http": status,
            "general_content_len": len(content or ""),
            "general_final": final and final["final_outcome"],
            "stream_http": s_status,
            "stream_first_event_s": round(first_at or -1, 3),
            "stream_total_s": round(total or -1, 3),
            "stream_ends_with_done": ends_done,
            "stream_final": s_final and s_final["final_outcome"],
            "evidence_summary": detail["evidence_summary"],
            "vllm_log_contains_received_request": request_logs,
        }, [request_id, s_id],
            ("backend 요청 로그 요구: 허용 요청마다 vLLM Received request 로그가 있어야 통과."
             if self.args.require_backend_request_log else
             "backend 요청 로그 미요구: vLLM Received request 로그는 보조 관측으로만 기록."))
        return request_id

    def s3_02(self) -> None:
        before = (self.vllm_metric("vllm:request_success_total"), self.vllm_metric("vllm:prompt_tokens_total"))
        since = _now()
        denied_model = self.post(self.body(model="other-model"))
        denied_tokens = self.post(self.body(max_tokens=129))
        time.sleep(2)
        after = (self.vllm_metric("vllm:request_success_total"), self.vllm_metric("vllm:prompt_tokens_total"))
        logs = self.vllm_logs_since(since)
        _, final_model = self.final(denied_model[1])
        detail_tokens, final_tokens = self.final(denied_tokens[1])
        denied_log_presence = [
            bool(denied_model[1] and denied_model[1] in logs),
            bool(denied_tokens[1] and denied_tokens[1] in logs),
        ]
        passed = bool(
            denied_model[0] == 403 and denied_tokens[0] == 403
            and final_model and final_model["delivery"]["attempted"] is False
            and final_tokens and final_tokens["delivery"]["attempted"] is False
            and before == after
            and (not self.args.require_backend_request_log or not any(denied_log_presence))
        )
        self.record("S3-02", "금지 모델·출력 초과 사전 거부 + backend 미전달 근거", passed, {
            "http": [denied_model[0], denied_tokens[0]],
            "reason": [final_model and final_model["decision"]["reason_code"],
                       final_tokens and final_tokens["decision"]["reason_code"]],
            "vllm_counters_before_after": [before, after],
            "request_ids_in_vllm_logs": denied_log_presence,
            "evidence_summary": detail_tokens["evidence_summary"],
        }, [denied_model[1], denied_tokens[1]],
            ("backend 요청 로그 요구: 거부 요청 ID가 vLLM 로그 어디에도 없어야 통과."
             if self.args.require_backend_request_log else
             "backend 요청 로그 미요구: 격리 구간 카운터와 거부 요청 ID 부재는 보조 관측."))

    def s3_03(self) -> None:
        since = _now()
        status, cancel_id, _, _, _ = self.stream(self.body(stream=True, max_tokens=128), stop_after_first=True)
        released = _wait(lambda: self.get("/api/status")["concurrency"]["in_flight"] == 0, 60)
        _, cancel_final = self.final(cancel_id)
        _wait(
            lambda: self._aborted_log_present(self.vllm_logs_since(since), cancel_id),
            30,
        )
        cancel_logs = self.vllm_logs_since(since)
        aborted_log_present = self._aborted_log_present(cancel_logs, cancel_id)
        abort_lines = [
            line for line in cancel_logs.splitlines() if re.search(r"[Aa]bort", line)
        ][:3]

        hold = threading.Event()
        outputs: dict[int, tuple[Any, ...]] = {}

        def run(index: int) -> None:
            outputs[index] = self.stream(self.body(stream=True, max_tokens=64), hold=hold)

        threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        _wait(lambda: self.get("/api/status")["concurrency"]["in_flight"] == 2, 120)
        third = self.post(self.body())
        hold.set()
        for thread in threads:
            thread.join(300)
        held_ok = all(
            index in outputs and outputs[index][0] == 200
            and outputs[index][2].rstrip().endswith(b"data: [DONE]")
            for index in range(2)
        )

        timeout_actual: dict[str, Any] = {
            "http": None,
            "reason_code": None,
            "slot_released": False,
            "gateway_cleanup": None,
        }
        timeout_id: str | None = None
        timeout_process: subprocess.Popen[bytes] | None = None
        timeout_config = copy.deepcopy(
            json.loads((self.state / "backend.json").read_text(encoding="utf-8"))
        )
        timeout_config.setdefault("timeouts", {})["relay_timeout_s"] = 0.5
        timeout_config_path = self.state / "backend-timeout.json"
        timeout_config_path.write_text(json.dumps(timeout_config), encoding="utf-8")
        os.chmod(timeout_config_path, 0o600)
        try:
            timeout_process = self.spawn_gateway(
                self.args.timeout_gateway_port,
                [
                    "--backend", "vllm",
                    "--backend-config", str(timeout_config_path),
                    "--data-dir", str(self.state / "data-timeout"),
                ],
            )
            timeout_status, timeout_id, timeout_body = self.post(
                self.body(), port=self.args.timeout_gateway_port
            )
            try:
                timeout_code = json.loads(timeout_body)["error"]["code"]
            except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
                timeout_code = None
            timeout_released = _wait(
                lambda: self.get(
                    "/api/status", self.args.timeout_gateway_port
                )["concurrency"]["in_flight"] == 0,
                30,
            )
            timeout_actual.update(
                {
                    "http": timeout_status,
                    "reason_code": timeout_code,
                    "slot_released": bool(timeout_released),
                }
            )
        except TemporaryGatewayError as error:
            timeout_actual["error"] = str(error)
        except Exception as error:
            timeout_actual["error"] = (
                f"{type(error).__name__}: timeout Gateway check failed"
            )
        finally:
            if timeout_process is not None:
                timeout_actual["gateway_cleanup"] = self.stop_gateway(timeout_process)

        timeout_ok = bool(
            timeout_actual["http"] == 504
            and timeout_actual["reason_code"] == "ENGINE_TIMEOUT"
            and timeout_actual["slot_released"]
        )
        passed = bool(
            status == 200 and released and cancel_final
            and cancel_final["final_outcome"] == "client_disconnected"
            and third[0] == 429 and held_ok
            and self.get("/api/status")["concurrency"]["in_flight"] == 0
            and timeout_ok
            and (not self.args.require_backend_request_log or aborted_log_present)
        )
        self.record("S3-03", "client 취소·timeout·동시성 초과 — Gateway 정리/slot 회수", passed, {
            "cancel_final": cancel_final and cancel_final["final_outcome"],
            "slot_released": bool(released),
            "gateway_backend_cancel_field": cancel_final and cancel_final.get("backend_cancel"),
            "vllm_cancel_aborted_log_present": aborted_log_present,
            "vllm_log_abort_lines": abort_lines,
            "third_request_http": third[0],
            "held_streams_completed": held_ok,
            "timeout_gateway": timeout_actual,
        }, [cancel_id, third[1], timeout_id] + [outputs[index][1] for index in outputs],
            ("backend 요청 로그 요구: 취소 요청의 vLLM aborted 로그가 있어야 통과."
             if self.args.require_backend_request_log else
             "backend 요청 로그 미요구: 취소 요청의 vLLM aborted 로그는 보조 관측."))

    def s3_04(self) -> None:
        info = self.collector("engine_info")
        metrics = self.collector("metrics")
        info_value = info.get("last_value") or {}
        model_map = info_value.get("model.map") or {}
        mapped_models_available = bool(model_map) and all(
            isinstance(value, dict) and value.get("available") is True
            for value in model_map.values()
        )
        states = {name: value.get("state") for name, value in (metrics.get("last_value") or {}).items()}
        metrics_ok = sorted(name for name, state in states.items() if state == "ok")
        metrics_not_ok = sorted(name for name, state in states.items() if state != "ok")
        passed = bool(
            info["state"] == "ok"
            and mapped_models_available
            and metrics["state"] == "ok"
            and metrics_ok
        )
        self.record("S3-04", "모델·상태·선정 지표 출처/시각/단위 매핑", passed, {
            "engine_info": info_value,
            "engine_info_origin": info["data_origin"],
            "engine_info_last_success": info["last_success_at"],
            "mapped_models_available": mapped_models_available,
            "metrics_states": states,
            "metrics_ok": metrics_ok,
            "metrics_not_ok": metrics_not_ok,
            "metrics_origin": metrics["data_origin"],
        }, [], "지표 이름은 연결한 vLLM의 실제 /metrics 응답 기준. 선정 GPU 버전과 다를 수 있다.")

    def s3_05(self) -> None:
        config = json.loads((self.state / "backend.json").read_text(encoding="utf-8"))
        broken = copy.deepcopy(config)
        broken["metrics_url"] = "http://127.0.0.1:9/metrics"
        broken_path = self.state / "backend-badmetrics.json"
        broken_path.write_text(json.dumps(broken), encoding="utf-8")
        os.chmod(broken_path, 0o600)
        port = self.args.metrics_failure_port
        actual: dict[str, Any] = {
            "metrics_only_failure_collector": None,
            "inference_during_metrics_failure": None,
            "temporary_gateway_cleanup": None,
        }
        process: subprocess.Popen[bytes] | None = None
        try:
            process = self.spawn_gateway(port, [
                "--backend", "vllm", "--backend-config", str(broken_path),
                "--data-dir", str(self.state / "data-badmetrics"),
            ])
            metrics_state = _wait(lambda: self.collector("metrics", port)["state"] == "error" and "error", 30)
            inference_status = self.post(self.body(), port=port)[0]
            actual.update({
                "metrics_only_failure_collector": metrics_state,
                "inference_during_metrics_failure": inference_status,
            })
        except TemporaryGatewayError as error:
            actual["temporary_gateway_error"] = str(error)
            self.record(
                "S3-05",
                "수집만 중단 / 엔진 중단 / 복구가 독립 표시",
                False,
                actual,
                [],
                "임시 Gateway 포트 또는 준비 실패로 사례를 실행하지 않음.",
            )
            return
        except Exception as error:
            actual["metrics_check_error"] = (
                f"{type(error).__name__}: metrics-only failure check failed"
            )
            self.record(
                "S3-05",
                "수집만 중단 / 엔진 중단 / 복구가 독립 표시",
                False,
                actual,
                [],
            )
            return
        finally:
            if process is not None:
                actual["temporary_gateway_cleanup"] = self.stop_gateway(process)
        if not self.args.allow_engine_restart:
            actual["engine_restart"] = "미실행 (--allow-engine-restart 없음)"
            self.record("S3-05", "수집만 중단 / 엔진 중단 / 복구가 독립 표시", None, actual, [],
                        "수집 장애 부분만 실행했다. 엔진 중단·복구는 미실행.")
            return
        down_status: int | None = None
        health_error = False
        recovered = False
        health_ok = False
        up_status: int | None = None
        stop_completed: subprocess.CompletedProcess[str] | None = None
        start_completed: subprocess.CompletedProcess[str] | None = None
        try:
            stop_completed = subprocess.run(
                ["docker", "stop", self.args.container],
                capture_output=True,
                text=True,
                check=False,
            )
            actual["docker_stop_returncode"] = stop_completed.returncode
            if stop_completed.returncode != 0:
                raise RuntimeError("docker stop failed")
            down_status = self.post(self.body())[0]
            health_error = bool(
                _wait(
                    lambda: self.collector("engine_health")["state"]
                    in ("error", "stale"),
                    30,
                )
            )
        except Exception as error:
            actual["engine_stopped_check_error"] = (
                f"{type(error).__name__}: engine interruption check failed"
            )
        finally:
            actual["docker_start_attempted"] = True
            try:
                start_completed = subprocess.run(
                    ["docker", "start", self.args.container],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                actual["docker_start_returncode"] = start_completed.returncode
            except Exception as error:
                actual["docker_start_returncode"] = None
                actual["docker_start_error"] = (
                    f"{type(error).__name__}: docker start failed"
                )

        if start_completed is not None and start_completed.returncode == 0:
            try:
                recovered = bool(_wait(self.vllm_health, 400))
                health_ok = bool(
                    _wait(
                        lambda: self.collector("engine_health")["state"] == "ok",
                        60,
                    )
                )
                up_status = self.post(self.body())[0]
            except Exception as error:
                actual["engine_recovery_check_error"] = (
                    f"{type(error).__name__}: engine recovery check failed"
                )

        actual.update({
            "inference_while_engine_stopped": down_status,
            "health_collector_while_stopped": health_error,
            "engine_recovered": recovered,
            "health_collector_recovered": health_ok,
            "inference_after_recovery": up_status,
        })
        passed = bool(
            metrics_state == "error" and inference_status == 200 and down_status in (502, 504)
            and health_error and recovered and health_ok and up_status == 200
            and stop_completed is not None and stop_completed.returncode == 0
            and start_completed is not None and start_completed.returncode == 0
        )
        self.record("S3-05", "수집만 중단 / 엔진 중단 / 복구가 독립 표시", passed, actual, [])

    def s3_06(self, earlier_vllm_id: str) -> None:
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from serving_runtime.mvp.store import JsonlStore

        records, _ = JsonlStore(self.state / "data/gateway_records.jsonl").read_all()
        current_request_ids = {
            request_id
            for result in self.results
            for request_id in result.get("evidence_request_ids", [])
            if isinstance(request_id, str)
        }
        current_request_ids.add(earlier_vllm_id)
        kinds = _backend_kinds_for_request_ids(records, current_request_ids)
        port = self.args.mock_gateway_port
        actual: dict[str, Any] = {"temporary_gateway_cleanup": None}
        process: subprocess.Popen[bytes] | None = None
        try:
            process = self.spawn_gateway(
                port,
                [
                    "--engine-port", str(self.args.mock_engine_port),
                    "--data-dir", str(self.state / "data"),
                ],
                additional_ports=(self.args.mock_engine_port,),
            )
            mock_status, mock_id, _ = self.post(self.body(), port=port)
            mock_detail = self.get(f"/api/requests/{mock_id}", port)
            earlier = self.get(f"/api/requests/{earlier_vllm_id}", port)
        except TemporaryGatewayError as error:
            actual["temporary_gateway_error"] = str(error)
            self.record(
                "S3-06",
                "mock/실제 모드 전환·이전 기록 혼용 없음",
                False,
                actual,
                [],
                "임시 Gateway 포트 또는 준비 실패로 사례를 실행하지 않음.",
            )
            return
        except Exception as error:
            actual["case_error"] = f"{type(error).__name__}: mock mode check failed"
            self.record(
                "S3-06",
                "mock/실제 모드 전환·이전 기록 혼용 없음",
                False,
                actual,
                [],
            )
            return
        finally:
            if process is not None:
                actual["temporary_gateway_cleanup"] = self.stop_gateway(process)
        passed = bool(
            kinds == {"vllm"} and mock_status == 200
            and mock_detail["backend_kind"] == "mock" and mock_detail["evidence_state"] == "received"
            and earlier["backend_kind"] == "vllm" and earlier["engine_receipts"] == []
        )
        actual.update({
            "vllm_mode_record_kinds": sorted(kinds),
            "mock_request": [mock_status, mock_detail["evidence_state"]],
            "earlier_vllm_request_seen_from_mock_gateway": [
                earlier["backend_kind"], earlier["evidence_state"], len(earlier["engine_receipts"]),
            ],
        })
        self.record("S3-06", "mock/실제 모드 전환·이전 기록 혼용 없음", passed, actual, [mock_id, earlier_vllm_id],
            "같은 data_dir를 쓰는 mock Gateway가 이 data_dir에 mock 기록을 1건 추가한다.")

    # ---- report -------------------------------------------------------
    def write_report(self) -> Path:
        leaked = any(
            self.key in path.read_text(encoding="utf-8", errors="ignore")
            for path in self.state.glob("data*/*.jsonl")
        )
        image = subprocess.run(
            ["docker", "inspect", "-f", "{{.Config.Image}} {{.Image}}", self.args.container],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
        revision_file = self.state / "model-revision"
        overall = _overall(self.results)
        report = {
            "kind": "S3B CPU 사전 점검 (승인된 GPU S3B 아님)",
            "executed_at": _now(),
            "host": platform.platform(),
            "python": platform.python_version(),
            "container": self.args.container,
            "container_image": image,
            "vllm_url": self.vllm,
            "gateway": f"http://{self.gateway_host}:{self.gateway_port}",
            "model_revision": revision_file.read_text().strip() if revision_file.exists() else None,
            "engine_restart_allowed": self.args.allow_engine_restart,
            "backend_request_log_required": self.args.require_backend_request_log,
            "secret_in_records": leaked,
            "overall": overall,
            "not_run": [item["case_id"] for item in self.results if item["passed"] is None],
            "cases": self.results,
        }
        directory = self.state / "reports"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / f"s3b-cpu-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"overall: {overall}")
        print(f"secret_in_records: {leaked}")
        print(f"report: {path}")
        return path

    def run(self) -> int:
        if not self.vllm_health():
            print(f"vLLM health check failed: {self.vllm}/health", file=sys.stderr)
            return 2
        if not self._port_ready(self.gateway_port):
            print(f"Gateway is not reachable: {self.args.gateway}", file=sys.stderr)
            return 2
        earlier_id = self.s3_01()
        self.s3_02()
        self.s3_03()
        self.s3_04()
        self.s3_05()
        self.s3_06(earlier_id)
        self.write_report()
        overall = _overall(self.results)
        return {"passed": 0, "failed": 1, "partial": 3}[overall]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S3B CPU pre-check against a running vLLM (not the GPU S3B).")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE),
                        help="folder with vllm-api-key, backend.json, data/ (default: %(default)s)")
    parser.add_argument("--gateway", default="http://127.0.0.1:18080", help="vLLM-mode Gateway URL")
    parser.add_argument("--vllm-url", default="http://127.0.0.1:8000", help="vLLM server URL")
    parser.add_argument("--container", default="s3b-vllm-cpu", help="vLLM Docker container name")
    parser.add_argument("--model-alias", default="test-model", help="policy model name mapped to vLLM")
    parser.add_argument("--metrics-failure-port", type=int, default=18081,
                        help="port for the temporary Gateway with a broken metrics URL (S3-05)")
    parser.add_argument("--mock-gateway-port", type=int, default=18082, help="temporary mock Gateway port (S3-06)")
    parser.add_argument("--mock-engine-port", type=int, default=18088, help="temporary mock engine port (S3-06)")
    parser.add_argument("--timeout-gateway-port", type=int, default=18083,
                        help="temporary short-timeout vLLM Gateway port (S3-03)")
    parser.add_argument("--allow-engine-restart", action="store_true",
                        help="let S3-05 stop and restart the vLLM container (takes a few minutes)")
    parser.add_argument("--require-backend-request-log", action="store_true",
                        help="require vLLM request/abort log evidence for S3-01~03")
    args = parser.parse_args(argv)
    try:
        return Precheck(args).run()
    except PrecheckConfigurationError as error:
        print(f"pre-check configuration error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
