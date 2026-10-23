"""Unit tests for S3B CPU pre-check decisions that do not contact live services."""

from argparse import Namespace
from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import subprocess

from tools.s3b_cpu_precheck import (
    Precheck,
    _backend_kinds_for_request_ids,
    _overall,
    main,
)


def arguments(state_dir: Path) -> Namespace:
    return Namespace(
        state_dir=str(state_dir),
        gateway="http://127.0.0.1:1",
        vllm_url="http://127.0.0.1:2",
    )


class PrecheckCredentialTests(unittest.TestCase):
    def test_key_file_uses_the_same_strict_header_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            key_path = state / "vllm-api-key"
            for ending in ("", "\n", "\r\n"):
                with self.subTest(ending=repr(ending)):
                    key_path.write_bytes(f"valid-visible-key{ending}".encode("ascii"))
                    self.assertEqual(Precheck(arguments(state)).key, "valid-visible-key")

            for invalid in (
                " leading-space",
                "trailing-space ",
                "internal\nnewline",
                "internal\rreturn",
                "trailing-return\r",
                "two-newlines\n\n",
                "two-crlf\r\n\r\n",
            ):
                with self.subTest(invalid=repr(invalid)):
                    key_path.write_bytes(invalid.encode("ascii"))
                    stderr = io.StringIO()
                    with redirect_stderr(stderr):
                        result = main(["--state-dir", str(state)])
                    self.assertEqual(result, 2)
                    self.assertIn("key is invalid", stderr.getvalue())
                    self.assertNotIn(invalid, stderr.getvalue())


class PrecheckOverallTests(unittest.TestCase):
    def test_overall_distinguishes_passed_failed_and_partial(self) -> None:
        self.assertEqual(_overall([{"passed": True}]), "passed")
        self.assertEqual(
            _overall([{"passed": True}, {"passed": None}]), "partial"
        )
        self.assertEqual(
            _overall([{"passed": None}, {"passed": False}]), "failed"
        )

    def test_s3_06_backend_kinds_ignore_records_from_earlier_runs(self) -> None:
        records = [
            {"request_id": "old", "backend_kind": "mock"},
            {"request_id": "current", "backend_kind": "vllm"},
            {"request_id": "other-current", "backend_kind": "vllm"},
        ]
        self.assertEqual(
            _backend_kinds_for_request_ids(records, {"current", "other-current"}),
            {"vllm"},
        )


class PrecheckCleanupAndCriteriaTests(unittest.TestCase):
    def make_precheck(self, state: Path) -> Precheck:
        (state / "vllm-api-key").write_text("valid-key", encoding="utf-8")
        args = arguments(state)
        args.model_alias = "test-model"
        args.container = "test-container"
        args.metrics_failure_port = 18081
        args.allow_engine_restart = True
        return Precheck(args)

    def test_temporary_gateway_is_killed_after_terminate_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            precheck = self.make_precheck(Path(temporary))
            process = Mock()
            process.wait.side_effect = [
                subprocess.TimeoutExpired("gateway", 10),
                0,
            ]
            cleanup = precheck.stop_gateway(process)
            process.terminate.assert_called_once_with()
            process.kill.assert_called_once_with()
            self.assertEqual(cleanup["result"], "killed_after_timeout")

    def test_docker_start_is_recorded_when_stopped_check_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            (state / "backend.json").write_text("{}", encoding="utf-8")
            precheck = self.make_precheck(state)
            precheck.spawn_gateway = Mock(return_value=Mock())
            precheck.stop_gateway = Mock(return_value={"result": "terminated"})
            precheck.post = Mock(
                side_effect=[
                    (200, "metrics-id", b"{}"),
                    RuntimeError("stopped check failed"),
                    (200, "recovery-id", b"{}"),
                ]
            )
            completed = [
                subprocess.CompletedProcess(["docker", "stop"], 0),
                subprocess.CompletedProcess(["docker", "start"], 0),
            ]
            with (
                patch("tools.s3b_cpu_precheck._wait", side_effect=["error", True, True]),
                patch("tools.s3b_cpu_precheck.subprocess.run", side_effect=completed) as run,
            ):
                precheck.s3_05()

            self.assertEqual(run.call_args_list[0].args[0][:2], ["docker", "stop"])
            self.assertEqual(run.call_args_list[1].args[0][:2], ["docker", "start"])
            actual = precheck.results[-1]["actual"]
            self.assertTrue(actual["docker_start_attempted"])
            self.assertEqual(actual["docker_start_returncode"], 0)
            self.assertFalse(precheck.results[-1]["passed"])

    def test_s3_04_requires_available_models_and_one_ok_metric(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            precheck = self.make_precheck(Path(temporary))
            precheck.collector = Mock(
                side_effect=[
                    {
                        "state": "ok",
                        "last_value": {
                            "model.map": {
                                "test-model": {"id": "backend-model", "available": True}
                            }
                        },
                        "data_origin": "external_backend",
                        "last_success_at": "now",
                    },
                    {
                        "state": "ok",
                        "last_value": {
                            "requests.running": {"state": "ok"},
                            "missing.metric": {"state": "unsupported_or_absent"},
                        },
                        "data_origin": "external_backend",
                    },
                ]
            )
            precheck.s3_04()
            result = precheck.results[-1]
            self.assertTrue(result["passed"])
            self.assertEqual(result["actual"]["metrics_not_ok"], ["missing.metric"])


if __name__ == "__main__":
    unittest.main()
