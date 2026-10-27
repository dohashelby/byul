"""Rule and adapter tests for cpu_vllm_check; no sockets or Docker are used."""

from __future__ import annotations

from argparse import Namespace
import json
import os
from pathlib import Path
import tempfile
import unittest

from tools.cpu_vllm_check import (
    BASE_CASE_IDS,
    CPUVLLMCheck,
    CommandResult,
    HTTPResponse,
    StreamResponse,
    _overall,
    _redact,
    evaluate_case,
    requested_case_ids,
    target_hold_reason,
)


INFERENCE = "unit-inference-token-never-report"
VIEWER = "unit-viewer-token-never-report"


class FakeRunner:
    def __init__(self, results: list[CommandResult] | None = None) -> None:
        self.results = list(results or [])
        self.history: list[dict[str, object]] = []

    def run(self, argv, *, timeout=30):
        result = self.results.pop(0) if self.results else CommandResult(0)
        self.history.append({
            "argv": list(argv),
            "started_at": "2026-10-27T00:00:00+00:00",
            "ended_at": "2026-10-27T00:00:01+00:00",
            "returncode": result.returncode,
        })
        return result


class FakeHTTP:
    def __init__(
        self,
        responses: list[HTTPResponse] | None = None,
        streams: list[StreamResponse] | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.streams = list(streams or [])
        self.calls: list[dict[str, object]] = []

    def request(self, method, url, *, headers=None, body=None, timeout=30):
        self.calls.append({"kind": "request", "method": method, "url": url})
        if not self.responses:
            raise AssertionError("unexpected fake HTTP request")
        return self.responses.pop(0)

    def stream(self, url, *, headers, body, timeout, abort_after_first=False):
        self.calls.append({
            "kind": "stream", "url": url,
            "abort_after_first": abort_after_first,
        })
        if not self.streams:
            raise AssertionError("unexpected fake HTTP stream")
        return self.streams.pop(0)


def arguments(root: Path, *, include_logprobs: bool = False) -> Namespace:
    token_file = root / "tokens.env"
    token_file.write_text(
        f"RUNTIME_INFERENCE_TOKEN={INFERENCE}\nRUNTIME_VIEWER_TOKEN={VIEWER}\n",
        encoding="utf-8",
    )
    os.chmod(token_file, 0o600)
    return Namespace(
        gateway_url="http://127.0.0.1:18180",
        token_env_file=str(token_file),
        vllm_url="http://127.0.0.1:8000",
        docker_container="s3b-vllm-cpu",
        evidence_dir=str(root / "evidence"),
        include_logprobs=include_logprobs,
        logprobs_timeout=5.0,
    )


class RuleTests(unittest.TestCase):
    def test_rules_distinguish_pass_fail_unknown_and_not_run(self) -> None:
        passed = {name: True for name in (
            "http_200", "valid_response", "record_allowed",
            "delivery_attempted", "engine_log_match",
        )}
        self.assertEqual(evaluate_case("CPU-01", passed)[0], "passed")
        self.assertEqual(evaluate_case("CPU-01", {**passed, "http_200": False})[0], "failed")
        self.assertEqual(evaluate_case("CPU-01", {**passed, "engine_log_match": None})[0], "unknown")
        self.assertEqual(
            evaluate_case("CPU-15", {"not_run_reason": "opt-in disabled"})[0],
            "not_run",
        )

    def test_overall_requires_every_requested_case_to_pass(self) -> None:
        cases = [{"case_id": case_id, "result": "passed"} for case_id in BASE_CASE_IDS]
        cases.append({"case_id": "CPU-15", "result": "not_run"})
        self.assertEqual(_overall(cases, BASE_CASE_IDS), "passed")
        self.assertEqual(_overall(cases, (*BASE_CASE_IDS, "CPU-15")), "failed")


class SafetyAndOptInTests(unittest.TestCase):
    def test_recursive_redaction_removes_tokens(self) -> None:
        value = {
            "plain": INFERENCE,
            "nested": [f"prefix-{VIEWER}-suffix", {"value": INFERENCE}],
        }
        serialized = json.dumps(_redact(value, (INFERENCE, VIEWER)))
        self.assertNotIn(INFERENCE, serialized)
        self.assertNotIn(VIEWER, serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_report_redacts_tokens_and_keeps_logprobs_not_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = CPUVLLMCheck(
                arguments(root), runner=FakeRunner(), http=FakeHTTP()
            )
            checker.cases = [
                {
                    "case_id": case_id,
                    "title": case_id,
                    "started_at": "start",
                    "ended_at": "end",
                    "result": "passed",
                    "reason": INFERENCE if case_id == "CPU-01" else "ok",
                    "facts": {},
                    "evidence": {"request_ids": [], "http_codes": [], "record_fields": None},
                    "http": [],
                    "commands": [],
                }
                for case_id in BASE_CASE_IDS
            ]
            checker._add_logprobs_not_run()
            json_path, markdown_path = checker.write_report()
            combined = json_path.read_text(encoding="utf-8") + markdown_path.read_text(encoding="utf-8")
            self.assertNotIn(INFERENCE, combined)
            self.assertNotIn(VIEWER, combined)
            document = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(document["overall"], "passed")
            self.assertNotIn("CPU-15", document["requested_cases"])

    def test_logprobs_is_selected_only_by_explicit_opt_in(self) -> None:
        self.assertNotIn("CPU-15", requested_case_ids(False))
        self.assertIn("CPU-15", requested_case_ids(True))
        with tempfile.TemporaryDirectory() as temporary:
            fake_http = FakeHTTP()
            checker = CPUVLLMCheck(
                arguments(Path(temporary)), runner=FakeRunner(), http=fake_http
            )
            checker._add_logprobs_not_run()
            self.assertEqual(checker.cases[-1]["result"], "not_run")
            self.assertEqual(fake_http.calls, [])

    def test_fake_runner_supplies_engine_logs_without_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner = FakeRunner([CommandResult(0, "Received request chatcmpl-request-1")])
            checker = CPUVLLMCheck(
                arguments(Path(temporary)), runner=runner, http=FakeHTTP()
            )
            ok, logs = checker._engine_logs("2026-10-27T00:00:00+00:00")
            self.assertTrue(ok)
            self.assertTrue(
                checker._received(logs, "request-1", "/v1/chat/completions")
            )
            self.assertFalse(
                checker._received(logs, "request-1", "/v1/completions")
            )
            self.assertTrue(
                checker._received(
                    "Received request cmpl-request-1-0",
                    "request-1",
                    "/v1/completions",
                )
            )
            self.assertEqual(runner.history[0]["argv"][:3], ["docker", "logs", "--since"])


class HoldDetectionTests(unittest.TestCase):
    def test_hold_reason_is_reported_exactly(self) -> None:
        self.assertIsNone(target_hold_reason({"gateway_hold": None}))
        self.assertEqual(
            target_hold_reason({
                "gateway_hold": {
                    "reason_code": "TARGET_UNVERIFIED",
                    "message": "engine info is not ready",
                }
            }),
            "TARGET_UNVERIFIED",
        )
        self.assertEqual(target_hold_reason(None), "TARGET_STATUS_UNAVAILABLE")

    def test_target_case_uses_fake_http_and_detects_no_hold(self) -> None:
        target = {
            "registration": {
                "state": "ok",
                "target_id": "cpu-vllm-lab-001",
                "engine": {"kind": "vllm", "version": "0.31.0"},
                "model": {"id": "qwen2.5-0.5b-instruct"},
            },
            "gateway_hold": None,
        }
        fake_http = FakeHTTP([
            HTTPResponse(200, {}, json.dumps(target).encode("utf-8"))
        ])
        with tempfile.TemporaryDirectory() as temporary:
            checker = CPUVLLMCheck(
                arguments(Path(temporary)), runner=FakeRunner(), http=fake_http
            )
            checker.normal_status = 200
            checker.normal_error_code = None
            checker.normal_request_id = "normal-request"
            facts, evidence = checker.cpu_12()
        self.assertEqual(evaluate_case("CPU-12", facts)[0], "passed")
        self.assertIsNone(evidence["gateway_hold_reason"])
        self.assertEqual(len(fake_http.calls), 1)


if __name__ == "__main__":
    unittest.main()
