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
        self.key = (self.state / "vllm-api-key").read_text(encoding="utf-8").strip()
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
        url = f"http://{self.gateway_host}:{port or self.gateway_port}/v1/chat/completions"
        request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=300) as response:
                return response.status, response.headers.get("X-Request-ID"), response.read()
        except HTTPError as error:
            with error:
                return error.code, error.headers.get("X-Request-ID"), error.read()

    def get(self, path: str, port: int | None = None) -> dict[str, Any]:
        with urlopen(f"http://{self.gateway_host}:{port or self.gateway_port}{path}", timeout=30) as response:
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

    def spawn_gateway(self, port: int, extra: list[str]) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [sys.executable, "-m", "serving_runtime", "mvp", "--gateway-port", str(port), *extra],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait(lambda: self._port_ready(port), 20)
        return process

    def _port_ready(self, port: int) -> bool:
        try:
            self.get("/api/status", port)
            return True
        except OSError:
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
        passed = bool(
            status == 200 and content and final and final["final_outcome"] == "completed"
            and final["backend_kind"] == "vllm" and s_status == 200 and ends_done
            and first_at is not None and total is not None and first_at < total
            and s_final and s_final["final_outcome"] == "completed"
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
            "vllm_log_contains_gateway_request_id": {request_id: request_id in logs, s_id: s_id in logs},
        }, [request_id, s_id], "vLLM 로그에 Gateway X-Request-ID가 남는지 함께 기록(backend 측 증거원 후보).")
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
        passed = bool(
            denied_model[0] == 403 and denied_tokens[0] == 403
            and final_model and final_model["delivery"]["attempted"] is False
            and final_tokens and final_tokens["delivery"]["attempted"] is False
            and before == after and denied_model[1] not in logs and denied_tokens[1] not in logs
        )
        self.record("S3-02", "금지 모델·출력 초과 사전 거부 + backend 미전달 근거", passed, {
            "http": [denied_model[0], denied_tokens[0]],
            "reason": [final_model and final_model["decision"]["reason_code"],
                       final_tokens and final_tokens["decision"]["reason_code"]],
            "vllm_counters_before_after": [before, after],
            "request_ids_in_vllm_logs": [denied_model[1] in logs, denied_tokens[1] in logs],
            "evidence_summary": detail_tokens["evidence_summary"],
        }, [denied_model[1], denied_tokens[1]],
            "다른 요청이 없는 격리 구간의 vLLM 카운터 전후 동일·로그에 요청 ID 없음은 보조 근거.")

    def s3_03(self) -> None:
        since = _now()
        status, cancel_id, _, _, _ = self.stream(self.body(stream=True, max_tokens=128), stop_after_first=True)
        released = _wait(lambda: self.get("/api/status")["concurrency"]["in_flight"] == 0, 60)
        _, cancel_final = self.final(cancel_id)
        _wait(lambda: re.search(r"[Aa]bort", self.vllm_logs_since(since)), 30)
        abort_lines = [line for line in self.vllm_logs_since(since).splitlines() if re.search(r"[Aa]bort", line)][:3]

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
        passed = bool(
            status == 200 and released and cancel_final
            and cancel_final["final_outcome"] == "client_disconnected"
            and third[0] == 429 and held_ok
            and self.get("/api/status")["concurrency"]["in_flight"] == 0
        )
        self.record("S3-03", "client 취소·동시성 초과 — Gateway 정리/slot 회수", passed, {
            "cancel_final": cancel_final and cancel_final["final_outcome"],
            "slot_released": bool(released),
            "gateway_backend_cancel_field": cancel_final and cancel_final.get("backend_cancel"),
            "vllm_log_abort_lines": abort_lines,
            "third_request_http": third[0],
            "held_streams_completed": held_ok,
        }, [cancel_id, third[1]] + [outputs[index][1] for index in outputs],
            "엔진 내부 생성 취소는 vLLM 로그의 abort 기록으로만 판단한다. 통과는 Gateway 측 기준이다.")

    def s3_04(self) -> None:
        info = self.collector("engine_info")
        metrics = self.collector("metrics")
        states = {name: value.get("state") for name, value in (metrics.get("last_value") or {}).items()}
        passed = info["state"] == "ok" and metrics["state"] == "ok" and bool(states)
        self.record("S3-04", "모델·상태·선정 지표 출처/시각/단위 매핑", passed, {
            "engine_info": info["last_value"],
            "engine_info_origin": info["data_origin"],
            "engine_info_last_success": info["last_success_at"],
            "metrics_states": states,
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
        process = self.spawn_gateway(port, [
            "--backend", "vllm", "--backend-config", str(broken_path),
            "--data-dir", str(self.state / "data-badmetrics"),
        ])
        try:
            metrics_state = _wait(lambda: self.collector("metrics", port)["state"] == "error" and "error", 30)
            inference_status = self.post(self.body(), port=port)[0]
        finally:
            process.terminate()
            process.wait(10)
        actual: dict[str, Any] = {
            "metrics_only_failure_collector": metrics_state,
            "inference_during_metrics_failure": inference_status,
        }
        if not self.args.allow_engine_restart:
            actual["engine_restart"] = "미실행 (--allow-engine-restart 없음)"
            self.record("S3-05", "수집만 중단 / 엔진 중단 / 복구가 독립 표시", None, actual, [],
                        "수집 장애 부분만 실행했다. 엔진 중단·복구는 미실행.")
            return
        subprocess.run(["docker", "stop", self.args.container], capture_output=True, check=False)
        down_status = self.post(self.body())[0]
        health_error = _wait(lambda: self.collector("engine_health")["state"] in ("error", "stale"), 30)
        subprocess.run(["docker", "start", self.args.container], capture_output=True, check=False)
        recovered = _wait(self.vllm_health, 400)
        health_ok = _wait(lambda: self.collector("engine_health")["state"] == "ok", 60)
        up_status = self.post(self.body())[0]
        actual.update({
            "inference_while_engine_stopped": down_status,
            "health_collector_while_stopped": bool(health_error),
            "engine_recovered": bool(recovered),
            "health_collector_recovered": bool(health_ok),
            "inference_after_recovery": up_status,
        })
        passed = bool(
            metrics_state == "error" and inference_status == 200 and down_status in (502, 504)
            and health_error and recovered and health_ok and up_status == 200
        )
        self.record("S3-05", "수집만 중단 / 엔진 중단 / 복구가 독립 표시", passed, actual, [])

    def s3_06(self, earlier_vllm_id: str) -> None:
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from serving_runtime.mvp.store import JsonlStore

        records, _ = JsonlStore(self.state / "data/gateway_records.jsonl").read_all()
        kinds = {item.get("backend_kind") for item in records}
        port = self.args.mock_gateway_port
        process = self.spawn_gateway(port, [
            "--engine-port", str(self.args.mock_engine_port), "--data-dir", str(self.state / "data"),
        ])
        try:
            mock_status, mock_id, _ = self.post(self.body(), port=port)
            mock_detail = self.get(f"/api/requests/{mock_id}", port)
            earlier = self.get(f"/api/requests/{earlier_vllm_id}", port)
        finally:
            process.terminate()
            process.wait(10)
        passed = bool(
            kinds == {"vllm"} and mock_status == 200
            and mock_detail["backend_kind"] == "mock" and mock_detail["evidence_state"] == "received"
            and earlier["backend_kind"] == "vllm" and earlier["engine_receipts"] == []
        )
        self.record("S3-06", "mock/실제 모드 전환·이전 기록 혼용 없음", passed, {
            "vllm_mode_record_kinds": sorted(item for item in kinds if item),
            "mock_request": [mock_status, mock_detail["evidence_state"]],
            "earlier_vllm_request_seen_from_mock_gateway": [
                earlier["backend_kind"], earlier["evidence_state"], len(earlier["engine_receipts"]),
            ],
        }, [mock_id, earlier_vllm_id],
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
            "secret_in_records": leaked,
            "passed": all(item["passed"] is not False for item in self.results),
            "not_run": [item["case_id"] for item in self.results if item["passed"] is None],
            "cases": self.results,
        }
        directory = self.state / "reports"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / f"s3b-cpu-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
        return 0 if all(item["passed"] is not False for item in self.results) else 1


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
    parser.add_argument("--allow-engine-restart", action="store_true",
                        help="let S3-05 stop and restart the vLLM container (takes a few minutes)")
    return Precheck(parser.parse_args(argv)).run()


if __name__ == "__main__":
    raise SystemExit(main())
