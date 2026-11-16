"""Cover command dispatch paths not exercised by existing CLI tests."""

from contextlib import redirect_stderr, redirect_stdout
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch, sentinel

from serving_runtime.cli import main


def invoke(arguments):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(arguments)
    return code, out.getvalue(), err.getvalue()


class CliDispatchContractTests(unittest.TestCase):
    def test_auth_hash_token_reads_stdin_and_returns_existing_errors(self):
        with (
            patch("sys.stdin", io.StringIO("contract-token\n")),
            patch("serving_runtime.gateway.auth.token_digest", return_value="contract-digest") as digest,
        ):
            self.assertEqual(invoke(["auth", "hash-token"]), (0, "contract-digest\n", ""))
        digest.assert_called_once_with("contract-token")
        with (
            patch("sys.stdin", io.StringIO("bad\n")),
            patch("serving_runtime.gateway.auth.token_digest", side_effect=ValueError("invalid token")),
        ):
            self.assertEqual(invoke(["auth", "hash-token"]), (1, "", "토큰 변환 실패: invalid token\n"))

    def test_bundle_verify_calls_original_verifier_and_preserves_output(self):
        bundle = SimpleNamespace(bundle_id="verified-id", sha256="a" * 64, files=("one", "two"))
        with patch("serving_runtime.policy.policy_bundle.verify_policy_bundle", return_value=bundle) as verify:
            code, out, err = invoke(["bundle", "verify", "--bundle-dir", "reviewed-bundle"])
        verify.assert_called_once_with("reviewed-bundle")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertEqual(out, "정책 묶음 검증 성공: verified-id\nbundle.json SHA-256: " + "a" * 64 + "\n파일 수: 2\n")
        with patch(
            "serving_runtime.policy.policy_bundle.verify_policy_bundle", side_effect=ValueError("invalid bundle")
        ):
            self.assertEqual(
                invoke(["bundle", "verify", "--bundle-dir", "bad-bundle"]),
                (1, "", "정책 묶음 검증 실패: invalid bundle\n"),
            )

    def test_cache_check_forwards_intent_root_and_status_exit_code(self):
        for state, expected in (("ok", 0), ("other_generations_present", 0), ("unknown", 3)):
            with (
                self.subTest(state=state),
                patch("serving_runtime.enforcement.intent.load_intent", return_value=sentinel.intent) as load,
                patch("serving_runtime.enforcement.cache.check_cache", return_value={"state": state}) as check,
            ):
                code, out, err = invoke(["enforce", "cache-check", "--intent", "intent.json", "--root", "copy"])
                load.assert_called_once_with("intent.json")
                check.assert_called_once_with(sentinel.intent, root_override="copy")
                self.assertEqual(code, expected)
                self.assertEqual(out, '{\n  "state": "' + state + '"\n}\n')
                self.assertEqual(err, "")
        with patch("serving_runtime.enforcement.intent.load_intent", side_effect=ValueError("invalid intent")):
            self.assertEqual(
                invoke(["enforce", "cache-check", "--intent", "intent.json"]),
                (1, "", "캐시 점검 실패: invalid intent\n"),
            )

    def test_management_listener_maps_all_options_and_allow_path_presence(self):
        arguments = [
            "management-listener",
            "--listen-host",
            "127.0.0.1",
            "--listen-port",
            "18081",
            "--engine-url",
            "http://127.0.0.1:18088",
            "--timeout",
            "1.25",
            "--max-response-bytes",
            "2048",
            "--credential-file",
            "credential.txt",
            "--record-file",
            "records.jsonl",
            "--heartbeat-file",
            "health.json",
            "--target-ref",
            "target-1",
            "--run-id",
            "run-1",
            "--environment",
            "contract",
            "--policy-ref",
            "policy-1",
        ]
        expected = {
            "listen_host": "127.0.0.1",
            "listen_port": 18081,
            "engine_url": "http://127.0.0.1:18088",
            "timeout_s": 1.25,
            "max_response_bytes": 2048,
            "credential_file": "credential.txt",
            "record_path": "records.jsonl",
            "heartbeat_path": "health.json",
            "target_ref": "target-1",
            "run_id": "run-1",
            "environment": "contract",
            "policy_ref": "policy-1",
        }
        for paths in ([], ["--allow-path", "/metrics", "--allow-path", "/health"]):
            with (
                self.subTest(paths=paths),
                patch(
                    "serving_runtime.gateway.management_listener.ManagementListenerConfig.build",
                    return_value=sentinel.config,
                ) as build,
                patch("serving_runtime.gateway.management_listener.run") as run,
            ):
                self.assertEqual(invoke(arguments + paths), (0, "", ""))
                options = dict(expected)
                if paths:
                    options["allowed_paths"] = ("/metrics", "/health")
                build.assert_called_once_with(**options)
                run.assert_called_once_with(sentinel.config)
        with patch(
            "serving_runtime.gateway.management_listener.ManagementListenerConfig.build",
            side_effect=ValueError("invalid boundary"),
        ):
            self.assertEqual(invoke(arguments), (1, "", "관리 listener 실행 실패: invalid boundary\n"))

    def test_mvp_acceptance_keeps_report_argument_and_exit_code(self):
        for arguments, expected in (
            (["mvp-acceptance"], None),
            (["mvp-acceptance", "--report-dir", "reports"], "reports"),
        ):
            with (
                self.subTest(arguments=arguments),
                patch("serving_runtime.devtools.acceptance.run_cli", return_value=1) as run,
            ):
                self.assertEqual(invoke(arguments), (1, "", ""))
                run.assert_called_once_with(expected)
