"""Unit tests for socket-free MVP demo case judgement."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr

from serving_runtime.cli import main
from serving_runtime.mvp.demo import (
    demo_cases,
    evaluate_case,
    print_results,
    write_demo_report,
)


def detail(result: str = "ALLOWED", *, evidence: str = "received",
           rule_id: str | None = None, max_tokens: int = 64) -> dict:
    return {
        "final_state": {"result": result, "max_tokens": max_tokens},
        "evidence_state": evidence,
        "gateway_records": [
            {
                "phase": "final",
                "decision": {"rule_id": rule_id},
            }
        ],
    }


class DemoJudgementTests(unittest.TestCase):
    def test_case_order_keeps_recovery_immediately_after_denial(self) -> None:
        identifiers = [case.case_id for case in demo_cases()]
        self.assertEqual(identifiers[:5], ["MVP-01", "MVP-02a", "MVP-02b", "MVP-03", "MVP-04"])

    def test_expected_evidence_is_part_of_judgement(self) -> None:
        case = demo_cases()[0]
        passed = evaluate_case(case, 200, "a" * 32, detail())
        failed = evaluate_case(case, 200, "a" * 32, detail(evidence="unknown"))
        self.assertTrue(passed.passed)
        self.assertFalse(failed.passed)
        self.assertIn("증거 received 기대", failed.failures[0])

    def test_rule_and_default_value_are_checked(self) -> None:
        model_case = next(case for case in demo_cases() if case.case_id == "MVP-04")
        default_case = next(case for case in demo_cases() if case.case_id == "MVP-05a")
        self.assertTrue(
            evaluate_case(
                model_case,
                403,
                "b" * 32,
                detail("DENIED", evidence="not_received", rule_id="allowed_models"),
            ).passed
        )
        self.assertTrue(
            evaluate_case(
                default_case, 200, "c" * 32, detail(max_tokens=128)
            ).passed
        )

    def test_missing_request_id_and_wrong_http_fail(self) -> None:
        result = evaluate_case(demo_cases()[0], 500, None, None)
        self.assertFalse(result.passed)
        self.assertGreaterEqual(len(result.failures), 3)

    def test_report_and_korean_table(self) -> None:
        result = evaluate_case(demo_cases()[0], 200, "d" * 32, detail())
        with tempfile.TemporaryDirectory() as temporary:
            path = write_demo_report(temporary, "http://127.0.0.1:8080", [result])
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertEqual(path.parent, Path(temporary) / "demo-runs")
        output = io.StringIO()
        print_results([result], output)
        self.assertIn("사례 ID", output.getvalue())
        self.assertIn("통과", output.getvalue())

    def test_cli_connection_failure_is_exit_code_two(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error):
            status = main(["mvp-demo", "--gateway", ""])
        self.assertEqual(status, 2)
        self.assertIn("Gateway에 연결할 수 없습니다", error.getvalue())


if __name__ == "__main__":
    unittest.main()
