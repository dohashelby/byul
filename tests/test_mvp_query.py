"""M3 read-only query API, evidence judgement, and dashboard asset tests."""

from contextlib import closing
import http.client
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.query import QueryError, QueryService, RequestNotFoundError
from serving_runtime.mvp.store import JsonlStore


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_POLICY = ROOT / "examples/mvp/policy.json"
STATIC_DIR = ROOT / "src/serving_runtime/mvp/static"
SESSION = "a" * 32


def record(request_id: str, phase: str, result: str, *, attempted: bool = False,
           session: str = SESSION, at: str = "2026-10-21T00:00:00+00:00") -> dict:
    return {
        "phase": phase,
        "request_id": request_id,
        "observation_session_id": session,
        "recorded_at": at,
        "decision": {"result": result, "reason_code": "X", "http_status": 200},
        "request": {"model": "test-model", "max_tokens": 64},
        "delivery": {"attempted": attempted, "backend_receipt": "not_queried"},
    }


class QueryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        directory = Path(self.temporary.name)
        self.gateway = JsonlStore(directory / "gateway_records.jsonl")
        self.engine = JsonlStore(directory / "engine_receipts.jsonl")
        self.service = QueryService(self.gateway, self.engine, lambda: {"ok": True})

    def lookup(self, status: str):
        return lambda request_id: {"status": status, "queried_at": "t", "error": None}

    def test_summary_counts_requests_not_lines(self) -> None:
        self.gateway.append(record("1" * 32, "started", "ALLOWED"))
        self.gateway.append(record("1" * 32, "final", "ALLOWED", attempted=True))
        self.gateway.append(record("2" * 32, "final", "DENIED"))
        self.gateway.append(record("3" * 32, "started", "ALLOWED"))
        summary = self.service.summary()
        self.assertEqual(summary["counts"]["ALLOWED"], 1)
        self.assertEqual(summary["counts"]["DENIED"], 1)
        self.assertEqual(summary["incomplete"], 1)
        self.assertEqual(summary["total"], 3)

    def test_older_records_expose_new_stream_fields_as_none(self) -> None:
        self.gateway.append(record("4" * 32, "final", "ALLOWED", attempted=True))
        view = self.service.requests()[0]
        self.assertIsNone(view["stream"])
        self.assertIsNone(view["http_start_status"])
        self.assertIsNone(view["final_outcome"])
        self.assertIsNone(view["backend_cancel"])

    def test_filter_limit_and_invalid_filter(self) -> None:
        for index in range(3):
            self.gateway.append(record(f"{index}" * 32, "final", "DENIED", at=f"2026-10-21T00:00:0{index}+00:00"))
        self.gateway.append(record("9" * 32, "final", "ALLOWED"))
        denied = self.service.requests(result="DENIED", limit=2)
        self.assertEqual([item["request_id"][0] for item in denied], ["2", "1"])
        with self.assertRaises(QueryError):
            self.service.requests(result="BOGUS")
        with self.assertRaises(QueryError):
            self.service.requests(limit=0)

    def test_corrupt_lines_are_reported_and_queries_still_work(self) -> None:
        self.gateway.append(record("1" * 32, "final", "ALLOWED"))
        with self.gateway.path.open("ab") as output:
            output.write(b"{broken\n")
        summary = self.service.summary()
        self.assertEqual(summary["corrupt_lines"]["gateway"], 1)
        self.assertEqual(len(self.service.requests()), 1)

    def test_detail_not_found(self) -> None:
        with self.assertRaises(RequestNotFoundError):
            self.service.detail("f" * 32, self.lookup("not_found"), SESSION)

    def test_denied_same_session_not_found_is_confirmed_non_delivery(self) -> None:
        self.gateway.append(record("1" * 32, "final", "DENIED"))
        detail = self.service.detail("1" * 32, self.lookup("not_found"), SESSION)
        self.assertEqual(detail["evidence_state"], "not_received")
        self.assertIn("미전달", detail["evidence_summary"])

    def test_lookup_failure_is_unknown_not_non_delivery(self) -> None:
        self.gateway.append(record("1" * 32, "final", "DENIED"))
        detail = self.service.detail("1" * 32, self.lookup("unknown"), SESSION)
        self.assertEqual(detail["evidence_state"], "unknown")
        self.assertNotIn("미수신 확인", detail["evidence_summary"])

    def test_previous_session_not_found_is_unknown(self) -> None:
        self.gateway.append(record("1" * 32, "final", "DENIED", session="b" * 32))
        detail = self.service.detail("1" * 32, self.lookup("not_found"), SESSION)
        self.assertEqual(detail["evidence_state"], "unknown")

    def test_file_receipt_confirms_after_restart(self) -> None:
        self.gateway.append(record("1" * 32, "final", "ALLOWED", attempted=True, session="b" * 32))
        self.engine.append({"request_id": "1" * 32, "received_at": "t"})
        detail = self.service.detail("1" * 32, self.lookup("not_found"), SESSION)
        self.assertEqual(detail["evidence_state"], "received")
        self.assertEqual(len(detail["engine_receipts"]), 1)


class DashboardAssetTests(unittest.TestCase):
    def test_script_never_renders_html_strings(self) -> None:
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "document.write"):
            self.assertNotIn(forbidden, script)

    def test_engine_card_reads_both_backend_modes(self) -> None:
        # The external vLLM mode has no `mock_engine` key in /api/status.
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("status.mock_engine || status.backend", script)

    def test_external_badge_depends_on_health_and_keeps_gpu_warning(self) -> None:
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn('engine.healthy ? "연결됨" : "연결 안 됨"', script)
        self.assertIn("외부 ${engineName} 모드", script)
        self.assertIn('{ vllm: "vLLM", sglang: "SGLang" }', script)
        self.assertIn("정식 GPU 시험(S3B) 전", script)
        self.assertIn('engine.healthy ? "badge-external" : "badge-ERROR"', script)

    def test_dashboard_has_no_mutating_controls(self) -> None:
        page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<form", page)
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('method: "POST"', script)


class QueryHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name)
        try:
            self.engine = MockEngine(port=0, receipts_path=self.data_dir / "engine_receipts.jsonl").start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.engine.stop)
        self.gateway = self.start_gateway(load_policy(EXAMPLE_POLICY))

    def start_gateway(self, policy_state, data_dir: Path | None = None) -> GatewayApp:
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(EXAMPLE_POLICY),
            data_dir=str(data_dir or self.data_dir),
            relay_timeout_s=0.5,
        )
        try:
            gateway = GatewayApp(config, policy_state, self.engine.address).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(gateway.stop)
        return gateway

    def url(self, path: str, gateway: GatewayApp | None = None) -> str:
        host, port = (gateway or self.gateway).address
        return f"http://{host}:{port}{path}"

    def get(self, path: str) -> tuple[int, object, bytes]:
        try:
            response = urlopen(self.url(path), timeout=2)
        except HTTPError as error:
            with closing(error):
                return error.code, error.headers, error.read()
        with closing(response):
            return response.status, response.headers, response.read()

    def post(self, max_tokens: int) -> str:
        body = json.dumps({
            "model": "test-model",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": max_tokens,
        }).encode("utf-8")
        request = Request(self.url("/v1/chat/completions"), data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            response = urlopen(request, timeout=2)
        except HTTPError as error:
            with closing(error):
                return error.headers["X-Request-ID"]
        with closing(response):
            return response.headers["X-Request-ID"]

    def test_mvp_11_list_detail_and_evidence_agree(self) -> None:
        allowed = self.post(64)
        denied = self.post(129)

        status, _, body = self.get("/api/summary")
        summary = json.loads(body)
        self.assertEqual((summary["counts"]["ALLOWED"], summary["counts"]["DENIED"]), (1, 1))

        status, _, body = self.get("/api/requests?result=DENIED")
        self.assertEqual(status, 200)
        self.assertEqual([item["request_id"] for item in json.loads(body)["requests"]], [denied])

        status, _, body = self.get(f"/api/requests/{allowed}")
        detail = json.loads(body)
        self.assertEqual(detail["evidence_state"], "received")
        self.assertEqual(detail["final_state"]["result"], "ALLOWED")

        status, _, body = self.get(f"/api/requests/{denied}")
        detail = json.loads(body)
        self.assertEqual(detail["evidence_state"], "not_received")
        self.assertEqual(detail["gateway_records"][-1]["policy"]["policy_id"], "local-policy-001")

        status, headers, body = self.get(f"/api/requests/{denied}/evidence")
        self.assertEqual(status, 200)
        self.assertIn(f'filename="evidence-{denied}.json"', headers["Content-Disposition"])
        self.assertEqual(json.loads(body)["request_id"], denied)

    def test_bad_queries(self) -> None:
        self.assertEqual(self.get("/api/requests?result=BOGUS")[0], 400)
        self.assertEqual(self.get("/api/requests?limit=abc")[0], 400)
        self.assertEqual(self.get("/api/requests/not-an-id")[0], 400)
        self.assertEqual(self.get("/api/requests/" + "f" * 32)[0], 404)

    def test_stopped_engine_makes_evidence_unknown(self) -> None:
        denied = self.post(129)
        self.engine.stop()
        detail = json.loads(self.get(f"/api/requests/{denied}")[2])
        self.assertEqual(detail["engine_live"]["status"], "unknown")
        self.assertEqual(detail["evidence_state"], "unknown")

    def test_static_path_traversal_is_not_served(self) -> None:
        host, port = self.gateway.address
        for path in ("/static/../app.py", "/static/app.py", "/static/%2e%2e/app.py", "/app.py"):
            connection = http.client.HTTPConnection(host, port, timeout=2)
            self.addCleanup(connection.close)
            connection.request("GET", path)
            self.assertEqual(connection.getresponse().status, 404, path)

    def test_invalid_policy_body_is_capped(self) -> None:
        policy = load_policy(self.data_dir / "missing.json")
        gateway = self.start_gateway(policy)
        host, port = gateway.address
        connection = http.client.HTTPConnection(host, port, timeout=2)
        self.addCleanup(connection.close)
        connection.putrequest("POST", "/v1/chat/completions")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(2 * 1024 * 1024))
        connection.endheaders()
        self.assertEqual(connection.getresponse().status, 413)

    def test_unwritable_data_dir_is_reported_before_first_request(self) -> None:
        blocker = self.data_dir / "not-a-directory"
        blocker.write_text("x", encoding="utf-8")
        gateway = self.start_gateway(load_policy(EXAMPLE_POLICY), data_dir=blocker / "sub")
        with closing(urlopen(self.url("/api/status", gateway), timeout=2)) as response:
            status = json.loads(response.read())
        self.assertEqual(status["record_store"]["state"], "error")
        self.assertFalse(status["record_store"]["ok"])


if __name__ == "__main__":
    unittest.main()
