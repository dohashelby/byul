"""Tests for the fixed-order Gateway performance comparison bundle."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from tools.perf_compare import (
    BLOCK_ORDERS,
    PlanError,
    _error_code,
    _read_inference_token,
    _request_once,
    build_block_comparisons,
    build_parser,
    execute_plan,
    load_plan,
    write_outputs,
)


SECRET_PROMPT = "PERF-SECRET-PROMPT-MUST-NOT-BE-STORED"
SECRET_RESPONSE = "PERF-SECRET-RESPONSE-MUST-NOT-BE-STORED"
SECRET_TOKEN = "perf-secret-token-must-not-be-stored"


class _GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen_modes: list[str] = []
    seen_authorization: list[str | None] = []
    lock = threading.Lock()

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        document = json.loads(body)
        parts = [part for part in self.path.split("/") if part]
        mode = parts[0] if parts else ""
        with self.lock:
            self.seen_modes.append(mode)
            self.seen_authorization.append(self.headers.get("Authorization"))
            request_number = len(self.seen_modes)
        if mode == "B2":
            payload = json.dumps(
                {"error": {"code": "INJECTED_B2_FAILURE", "message": SECRET_RESPONSE}}
            ).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Request-ID", f"fake-{request_number}")
            self.end_headers()
            self.wfile.write(payload)
            return
        if document.get("stream") is True:
            chunks = (
                "data: "
                + json.dumps(
                    {
                        "choices": [{"delta": {"content": SECRET_RESPONSE}}],
                        "usage": {"completion_tokens": 5},
                    }
                )
                + "\n\ndata: [DONE]\n\n"
            ).encode("utf-8")
            content_type = "text/event-stream"
        else:
            chunks = json.dumps(
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": SECRET_RESPONSE}}
                    ],
                    "usage": {"completion_tokens": 7},
                }
            ).encode("utf-8")
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(chunks)))
        self.send_header("X-Request-ID", f"fake-{request_number}")
        self.end_headers()
        self.wfile.write(chunks)


def _phase(
    *,
    mean: float | None,
    p50: float | None,
    p95: float | None,
    throughput: float | None,
    successful: int,
    errors: int,
) -> dict[str, object]:
    latency = {
        "denominator": successful,
        "mean_ms": mean,
        "p50_ms": p50,
        "p95_ms": p95,
    }
    return {
        "successful": successful,
        "errors": errors,
        "completed_requests_per_second": throughput,
        "e2e": dict(latency),
        "ttft": dict(latency),
    }


class PerfCompareTests(unittest.TestCase):
    def setUp(self) -> None:
        _GatewayHandler.seen_modes = []
        _GatewayHandler.seen_authorization = []
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _write_plan(self, directory: Path) -> Path:
        host, port = self.server.server_address[:2]
        plan = {
            "schema": "runtime-perf-plan/1.0",
            "modes": {
                mode: {
                    "gateway_url": f"http://{host}:{port}/{mode}",
                    "description": f"prepared {mode} Gateway",
                }
                for mode in ("B0", "B1", "B2")
            },
            "model": "test-model",
            "prompts": [SECRET_PROMPT],
            "max_tokens": 8,
            "temperature": 0,
            "n": 1,
            "concurrency": 1,
            "request_timeout_s": 2,
        }
        path = directory / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        return path

    def test_small_http_run_preserves_order_failures_budget_and_privacy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = load_plan(self._write_plan(root))
            report, raw = execute_plan(
                plan,
                warmup_count=1,
                normal_count=1,
                stream_count=1,
                block_count=3,
                run_id="test-perf-run",
            )
            paths = write_outputs(report, raw, root / "out")

            self.assertEqual(
                [tuple(block["order"]) for block in report["blocks"]],
                list(BLOCK_ORDERS),
            )
            expected_modes = [
                mode
                for order in BLOCK_ORDERS
                for mode in order
                for _phase_name in ("warmup", "normal", "stream")
            ]
            self.assertEqual(_GatewayHandler.seen_modes, expected_modes)
            self.assertEqual(set(_GatewayHandler.seen_authorization), {None})
            self.assertEqual([block["status"] for block in report["blocks"]], ["failed"] * 3)
            self.assertTrue(
                all(
                    next(run for run in block["runs"] if run["mode"] == "B2")["status"]
                    == "failed"
                    for block in report["blocks"]
                )
            )
            self.assertFalse(report["execution_budget"]["plan_budget"])
            self.assertEqual(report["execution_budget"]["label"], "계획 예산 아님")
            self.assertEqual(report["external_measurements"]["measurement_type"], "external")
            self.assertEqual(report["external_measurements"]["status"], "not_attached")
            self.assertEqual(raw["rows"][0]["included_in_metrics"], False)
            self.assertEqual(
                next(
                    row
                    for row in raw["rows"]
                    if row["mode"] == "B0" and row["phase"] == "normal"
                )["output_tokens"],
                7,
            )
            self.assertEqual(
                next(
                    row
                    for row in raw["rows"]
                    if row["mode"] == "B0" and row["phase"] == "stream"
                )["output_tokens"],
                5,
            )

            written = b"\n".join(path.read_bytes() for path in paths.values())
            self.assertNotIn(SECRET_PROMPT.encode(), written)
            self.assertNotIn(SECRET_RESPONSE.encode(), written)
            self.assertNotIn(b'"messages"', written)
            self.assertFalse(report["privacy"]["prompt_text_stored"])
            self.assertFalse(report["privacy"]["response_text_stored"])

            with self.assertRaises(FileExistsError):
                write_outputs(report, raw, root / "out")
            self.assertTrue(all(path.exists() for path in paths.values()))

    def test_bearer_header_reaches_every_phase_without_token_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report, raw = execute_plan(
                load_plan(self._write_plan(root)),
                warmup_count=1,
                normal_count=1,
                stream_count=1,
                block_count=1,
                bearer_token=SECRET_TOKEN,
                run_id="authenticated-perf-run",
            )
            paths = write_outputs(report, raw, root / "out")
            self.assertEqual(len(_GatewayHandler.seen_authorization), 9)
            self.assertEqual(
                set(_GatewayHandler.seen_authorization),
                {f"Bearer {SECRET_TOKEN}"},
            )
            written = b"\n".join(path.read_bytes() for path in paths.values())
            self.assertNotIn(SECRET_TOKEN.encode(), written)


class PerfTokenFileTests(unittest.TestCase):
    def test_cli_accepts_optional_token_env_file(self) -> None:
        args = build_parser().parse_args(
            ["plan.json", "--out-dir", "out", "--token-env-file", "tokens.env"]
        )
        self.assertEqual(args.token_env_file, "tokens.env")
        self.assertIsNone(
            build_parser().parse_args(["plan.json", "--out-dir", "out"]).token_env_file
        )

    def test_token_file_rejects_group_or_other_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tokens.env"
            path.write_text(
                f"RUNTIME_INFERENCE_TOKEN={SECRET_TOKEN}\n"
                "RUNTIME_VIEWER_TOKEN=viewer-token-for-shared-aws-file\n",
                encoding="utf-8",
            )
            os.chmod(path, 0o640)
            with self.assertRaisesRegex(PlanError, "group or others"):
                _read_inference_token(path)
            os.chmod(path, 0o600)
            self.assertEqual(_read_inference_token(path), SECRET_TOKEN)

    def test_auth_statuses_have_distinct_fallback_error_codes(self) -> None:
        self.assertEqual(_error_code(None, 401), "AUTH_HTTP_401")
        self.assertEqual(_error_code(None, 403), "ACCESS_HTTP_403")

    def test_request_adds_bearer_header_without_exposing_it_in_result(self) -> None:
        captured: dict[str, object] = {}

        class Response:
            status = 200
            will_close = False
            fp = BytesIO(b'{"usage":{"completion_tokens":1}}')

            def getheader(self, name: str) -> str | None:
                return {
                    "Content-Length": "33",
                    "X-Request-ID": "request-1",
                }.get(name)

        class Connection:
            def __init__(self, host: str, port: int, timeout: float) -> None:
                captured["endpoint"] = (host, port, timeout)

            def request(self, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> None:
                captured["request"] = (method, path, body, headers)

            def getresponse(self) -> Response:
                return Response()

            def close(self) -> None:
                return

        with patch("tools.perf_compare.http.client.HTTPConnection", Connection):
            result = _request_once(
                "http://gateway.example:8080",
                {"stream": False},
                stream=False,
                timeout_s=2,
                bearer_token=SECRET_TOKEN,
            )
        headers = captured["request"][3]
        self.assertEqual(headers["Authorization"], f"Bearer {SECRET_TOKEN}")
        self.assertNotIn(SECRET_TOKEN, json.dumps(result))

class PerfComparisonFormulaTests(unittest.TestCase):
    def test_injected_small_run_keeps_order_failures_and_private_artifacts(self) -> None:
        calls: list[tuple[str, bool]] = []

        def measure(
            gateway_url: str,
            payload: dict[str, object],
            *,
            stream: bool,
            timeout_s: float,
        ) -> dict[str, object]:
            del timeout_s
            mode = gateway_url.rsplit("/", 1)[-1]
            calls.append((mode, stream))
            self.assertEqual(
                payload["messages"],
                [{"role": "user", "content": SECRET_PROMPT}],
            )
            failed = mode == "B2"
            return {
                "success": not failed,
                "http_status": 503 if failed else 200,
                "request_id": f"injected-{len(calls)}",
                "error_code": "INJECTED_B2_FAILURE" if failed else None,
                "e2e_ms": 30.0 if mode == "B2" else 10.0,
                "ttft_ms": (5.0 if stream and not failed else None),
                "output_tokens": None if failed else 7,
            }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_document = {
                "schema": "runtime-perf-plan/1.0",
                "modes": {
                    mode: {
                        "gateway_url": f"http://127.0.0.1:1/{mode}",
                        "description": f"prepared {mode} Gateway",
                    }
                    for mode in ("B0", "B1", "B2")
                },
                "model": "test-model",
                "prompts": [SECRET_PROMPT],
                "max_tokens": 8,
                "temperature": 0,
                "n": 1,
                "concurrency": 1,
            }
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan_document), encoding="utf-8")
            report, raw = execute_plan(
                load_plan(plan_path),
                warmup_count=1,
                normal_count=1,
                stream_count=1,
                request_function=measure,
                run_id="injected-small-run",
            )
            paths = write_outputs(report, raw, root / "out")

            self.assertEqual(
                calls,
                [
                    (mode, phase == "stream")
                    for order in BLOCK_ORDERS
                    for mode in order
                    for phase in ("warmup", "normal", "stream")
                ],
            )
            self.assertEqual(
                [tuple(block["order"]) for block in report["blocks"]],
                list(BLOCK_ORDERS),
            )
            self.assertEqual(
                [block["status"] for block in report["blocks"]],
                ["failed", "failed", "failed"],
            )
            self.assertEqual(report["execution_budget"]["label"], "계획 예산 아님")
            self.assertTrue(
                all(
                    next(
                        run for run in block["runs"] if run["mode"] == "B2"
                    )["phases"]["normal"]["errors"]
                    == 1
                    for block in report["blocks"]
                )
            )
            written = b"\n".join(path.read_bytes() for path in paths.values())
            self.assertNotIn(SECRET_PROMPT.encode(), written)
            self.assertNotIn(b'"messages"', written)
            self.assertEqual(raw["rows"][0]["included_in_metrics"], False)
            with self.assertRaises(FileExistsError):
                write_outputs(report, raw, root / "out")

    def test_block_calculation_uses_each_blocks_b0_and_keeps_unavailable(self) -> None:
        baseline = _phase(
            mean=10,
            p50=10,
            p95=20,
            throughput=10,
            successful=100,
            errors=0,
        )
        b1 = _phase(
            mean=15,
            p50=20,
            p95=30,
            throughput=8,
            successful=98,
            errors=2,
        )
        failed = _phase(
            mean=None,
            p50=None,
            p95=None,
            throughput=0,
            successful=0,
            errors=100,
        )
        comparisons = build_block_comparisons(
            [
                {"mode": "B1", "phases": {"normal": b1, "stream": b1}},
                {"mode": "B2", "phases": {"normal": failed, "stream": failed}},
                {"mode": "B0", "phases": {"normal": baseline, "stream": baseline}},
            ]
        )
        normal = comparisons["B1_vs_B0"]["normal"]
        self.assertEqual(normal["latency_increase_pct"]["mean_ms"], 50.0)
        self.assertEqual(normal["latency_increase_pct"]["p50_ms"], 100.0)
        self.assertEqual(normal["latency_increase_pct"]["p95_ms"], 50.0)
        self.assertEqual(normal["throughput_decrease_pct"], 20.0)
        self.assertEqual(normal["candidate_errors"], 2)
        unavailable = comparisons["B2_vs_B0"]["stream"]
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["candidate_errors"], 100)
        self.assertIsNone(unavailable["latency_increase_pct"]["p95_ms"])


class PerfOutputTests(unittest.TestCase):
    def test_socket_free_outputs_keep_failures_budget_marker_and_no_text(self) -> None:
        def measured(
            gateway_url: str,
            payload: dict[str, object],
            *,
            stream: bool,
            timeout_s: float,
        ) -> dict[str, object]:
            self.assertEqual(payload["messages"][0]["content"], SECRET_PROMPT)
            self.assertEqual(timeout_s, 2)
            failed = gateway_url.endswith("/B2")
            return {
                "success": not failed,
                "http_status": 503 if failed else 200,
                "request_id": "stub-request",
                "error_code": "INJECTED_B2_FAILURE" if failed else None,
                "e2e_ms": 2.0 if gateway_url.endswith("/B1") else 1.0,
                "ttft_ms": 0.5 if stream and not failed else None,
                "output_tokens": 4 if not failed else None,
            }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = {
                "schema": "runtime-perf-plan/1.0",
                "modes": {
                    mode: {
                        "gateway_url": f"http://127.0.0.1:1/{mode}",
                        "description": mode,
                    }
                    for mode in ("B0", "B1", "B2")
                },
                "model": "test-model",
                "prompts": [SECRET_PROMPT],
                "max_tokens": 8,
                "temperature": 0,
                "n": 1,
                "concurrency": 1,
                "request_timeout_s": 2,
            }
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(document), encoding="utf-8")
            report, raw = execute_plan(
                load_plan(plan_path),
                warmup_count=1,
                normal_count=1,
                stream_count=1,
                block_count=3,
                request_function=measured,
                run_id="socket-free-perf-run",
            )
            paths = write_outputs(report, raw, root / "out")

            self.assertEqual([block["status"] for block in report["blocks"]], ["failed"] * 3)
            self.assertEqual(report["execution_budget"]["label"], "계획 예산 아님")
            self.assertEqual(len(raw["rows"]), 27)
            self.assertTrue(any(not row["success"] for row in raw["rows"]))
            written = b"\n".join(path.read_bytes() for path in paths.values())
            self.assertNotIn(SECRET_PROMPT.encode(), written)
            self.assertNotIn(SECRET_RESPONSE.encode(), written)
            with self.assertRaises(FileExistsError):
                write_outputs(report, raw, root / "out")



class PerfRealGatewayTests(unittest.TestCase):
    """Run against the real MVP Gateway (mock engine), not a hand-made fake.

    A fake server accepted any field and hid that the default policy rejects
    ``temperature``; this test sends the plan through the actual validator.
    """

    def test_default_policy_gateway_accepts_plan_requests(self) -> None:
        from serving_runtime.mvp.app import GatewayApp
        from serving_runtime.mvp.config import MVPConfig
        from serving_runtime.mvp.mock_engine import MockEngine
        from serving_runtime.mvp.policy import load_policy

        root_dir = Path(__file__).resolve().parents[1]
        policy = root_dir / "examples/mvp/policy.json"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            try:
                engine = MockEngine(port=0).start()
            except PermissionError as error:
                self.skipTest(f"local sockets are unavailable: {error}")
            self.addCleanup(engine.stop)
            gateway = GatewayApp(
                MVPConfig(
                    gateway_port=0,
                    engine_port=0,
                    policy_path=str(policy),
                    data_dir=str(root / "data"),
                ),
                load_policy(policy),
                engine.address,
            ).start()
            self.addCleanup(gateway.stop)
            url = f"http://{gateway.address[0]}:{gateway.address[1]}"
            example = json.loads((root_dir / "examples/perf/plan.example.json").read_text(encoding="utf-8"))
            self.assertNotIn("temperature", example)
            example["model"] = "test-model"
            example["modes"] = {
                mode: {"gateway_url": url, "description": mode} for mode in ("B0", "B1", "B2")
            }
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(example), encoding="utf-8")
            report, raw = execute_plan(
                load_plan(plan_path),
                warmup_count=1,
                normal_count=2,
                stream_count=2,
                block_count=1,
                run_id="real-gateway-perf-run",
            )
            errors = [row for row in raw["rows"] if not row["success"]]
            self.assertEqual(errors, [], errors[:1])
            self.assertEqual(report["blocks"][0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
