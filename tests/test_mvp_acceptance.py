"""M4 acceptance orchestration and session/corruption regression tests."""

import json
from pathlib import Path
import tempfile
import unittest

from serving_runtime.mvp.acceptance import execute_acceptance, write_reports
from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.query import QueryService
from serving_runtime.mvp.store import JsonlStore


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "examples/mvp/policy.json"


class M4UnitTests(unittest.TestCase):
    def test_gateway_instances_have_distinct_observation_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = MVPConfig(data_dir=temporary, gateway_port=0, engine_port=0)
            first = GatewayApp(config, load_policy(POLICY), ("127.0.0.1", 1))
            second = GatewayApp(config, load_policy(POLICY), ("127.0.0.1", 1))
            self.assertNotEqual(first.observation_session_id, second.observation_session_id)

    def test_summary_details_and_current_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            gateway = JsonlStore(directory / "gateway.jsonl")
            engine = JsonlStore(directory / "engine.jsonl")
            current = "a" * 32
            gateway.append({
                "phase": "final",
                "request_id": "1" * 32,
                "observation_session_id": current,
                "recorded_at": "2026-10-21T00:00:00+00:00",
                "decision": {"result": "DENIED"},
                "request": {},
                "delivery": {},
            })
            with gateway.path.open("ab") as output:
                for _ in range(25):
                    output.write(b"{bad\n")
            service = QueryService(gateway, engine, lambda: {"ok": True}, current)
            summary = service.summary()
            self.assertEqual(summary["corrupt_lines"]["gateway"], 25)
            self.assertEqual(len(summary["corrupt_details"]["gateway"]), 20)
            self.assertTrue(service.requests()[0]["current_session"])


class FullAcceptanceTests(unittest.TestCase):
    def test_mvp_s1_s2_and_s3a_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            data_dir.mkdir()
            try:
                results = execute_acceptance(data_dir)
            except PermissionError as error:
                self.skipTest(f"local sockets are unavailable: {error}")
            self.assertEqual(
                [result.case_id for result in results],
                [f"MVP-{index:02d}" for index in range(1, 12)]
                + [f"S1-{index:02d}" for index in range(1, 7)]
                + [f"S2-{index:02d}" for index in range(1, 7)]
                + [f"S3A-{index:02d}" for index in range(1, 9)],
            )
            self.assertTrue(all(result.passed for result in results), [
                (result.case_id, result.actual) for result in results if not result.passed
            ])
            report_dir = Path(temporary) / "reports"
            json_path, md_path = write_reports(report_dir, data_dir, results)
            report = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertEqual(len(report["cases"]), 31)
            self.assertIn("fake_backend_test", report["s3a_backend_fixture"])
            self.assertIn("실제 vLLM 실행·검증 아님", md_path.read_text(encoding="utf-8"))
            self.assertTrue(md_path.exists())


if __name__ == "__main__":
    unittest.main()
