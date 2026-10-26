"""Unit tests for the AWS acceptance tool; no network or kubectl is used."""

from __future__ import annotations

from argparse import Namespace
from contextlib import redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit

from tools.aws_lab_acceptance import (
    AWSAcceptance,
    CASE_IDS,
    CommandResult,
    HTTPResponse,
    CACHE_WRITE_CODE,
    TCP_PROBE_CODE,
    WRITE_DENY_CODE,
    _extract_policy_limit,
    _overall,
    evaluate_case,
    parse_args,
)
from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.observation_feed import HEARTBEAT_SCHEMA


INFERENCE = "unit-inference-token-never-report"
VIEWER = "unit-viewer-token-never-report"


class FakeRunner:
    def __init__(self, results: list[CommandResult] | None = None) -> None:
        self.results = list(results or [])
        self.history: list[dict[str, object]] = []

    def run(self, argv, *, timeout=30):
        self.history.append(
            {
                "argv": list(argv),
                "started_at": "2026-10-26T00:00:00+00:00",
                "ended_at": "2026-10-26T00:00:01+00:00",
                "returncode": self.results[0].returncode if self.results else 0,
            }
        )
        return self.results.pop(0) if self.results else CommandResult(0)


class FakeHTTP:
    def __init__(self, responses: list[HTTPResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def request(self, method, url, *, headers=None, body=None, timeout=30):
        self.calls.append(
            {
                "method": method,
                "path": urlsplit(url).path,
                "has_authorization": "Authorization" in (headers or {}),
                "has_body": body is not None,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected HTTP call")
        return self.responses.pop(0)


def response(status: int, body: object, request_id: str | None = None) -> HTTPResponse:
    headers = {"X-Request-ID": request_id} if request_id else {}
    return HTTPResponse(status, headers, json.dumps(body).encode("utf-8"))


def detail(
    result: str,
    *,
    reason: str | None = None,
    attempted: bool,
    outcome: str | None = None,
    status: int = 200,
) -> dict[str, object]:
    return {
        "final_state": {
            "result": result,
            "reason_code": reason,
            "http_status": status,
            "final_outcome": outcome,
            "delivery": {
                "attempted": attempted,
                "backend_receipt": "confirmed" if attempted else "not_queried",
                "response": "completed" if attempted else "not_started",
                "error_code": None,
            },
        }
    }


def arguments(root: Path, *, cases=CASE_IDS, **updates) -> Namespace:
    token_file = root / "tokens.env"
    token_file.write_text(
        f"RUNTIME_INFERENCE_TOKEN={INFERENCE}\nRUNTIME_VIEWER_TOKEN={VIEWER}\n",
        encoding="utf-8",
    )
    values = {
        "kube_context": "byul-aws-lab",
        "namespace": "serving-lab",
        "gateway_url": "http://127.0.0.1:18080",
        "token_env_file": str(token_file),
        "observation_dir": str(root / "observation"),
        "evidence_dir": str(root / "evidence"),
        "cases": tuple(cases),
        "allow_collector_stop": False,
        "allow_engine_restart": False,
    }
    values.update(updates)
    return Namespace(**values)


class ArgumentTests(unittest.TestCase):
    def test_context_and_token_file_are_required(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            parse_args(["--token-env-file", "tokens.env"])
        self.assertIn("--kube-context", stderr.getvalue())

    def test_context_is_validated_and_cases_are_ordered(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(
                [
                    "--kube-context",
                    "-unsafe",
                    "--token-env-file",
                    "tokens.env",
                ]
            )
        parsed = parse_args(
            [
                "--kube-context",
                "byul-aws-lab",
                "--token-env-file",
                "tokens.env",
                "--cases",
                "AWS-03,AWS-01",
                "--allow-collector-stop",
                "--allow-engine-restart",
            ]
        )
        self.assertEqual(parsed.cases, ("AWS-01", "AWS-03"))
        self.assertTrue(parsed.allow_collector_stop)
        self.assertTrue(parsed.allow_engine_restart)

    def test_disruptive_cases_are_not_run_without_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checker = AWSAcceptance(
                arguments(Path(temporary), cases=("AWS-07", "AWS-08")),
                runner=FakeRunner(),
                http=FakeHTTP([]),
            )
            self.assertEqual(checker.run(), 3)
            self.assertEqual(
                [(item["case_id"], item["result"]) for item in checker.cases],
                [("AWS-07", "not_run"), ("AWS-08", "not_run")],
            )


class DecisionTests(unittest.TestCase):
    PASS_FACTS = {
        "AWS-01": dict.fromkeys(
            ("http_200", "valid_completion", "record_allowed", "delivery_attempted", "engine_log_match"), True
        ),
        "AWS-02": dict.fromkeys(
            ("auth_codes", "records_denied", "not_delivered", "engine_log_absent", "engine_log_format_confirmed"), True
        ),
        "AWS-03": dict.fromkeys(
            ("policy_limit_read", "http_403", "reason_code", "not_delivered"), True
        ),
        "AWS-04": dict.fromkeys(("direct_failed", "gateway_control_200", "hubble_observed"), True),
        "AWS-05": dict.fromkeys(
            ("engine_receiver_failed", "receiver_control_connected", "receiver_hubble"), True
        ),
        "AWS-06": dict.fromkeys(
            ("model_write_denied", "cache_write_succeeded", "cache_harmless_removed", "cache_tetragon"), True
        ),
        "AWS-07": dict.fromkeys(
            ("stop_ok", "heartbeat_stopped_seen", "collectors_unavailable", "start_ok", "collectors_recovered"), True
        ),
        "AWS-08": dict.fromkeys(
            (
                "restart_ok",
                "pod_changed",
                "gateway_recovered",
                "engine_info_recovered",
                "gateway_instance_changed",
            ),
            True,
        ),
    }

    def test_every_case_passes_only_with_all_required_facts(self) -> None:
        for case_id, facts in self.PASS_FACTS.items():
            with self.subTest(case_id=case_id):
                self.assertEqual(evaluate_case(case_id, facts)[0], "passed")

    def test_every_case_fails_when_a_decisive_result_contradicts_expectation(self) -> None:
        decisive = {
            "AWS-01": "http_200",
            "AWS-02": "not_delivered",
            "AWS-03": "http_403",
            "AWS-04": "direct_failed",
            "AWS-05": "engine_receiver_failed",
            "AWS-06": "model_write_denied",
            "AWS-07": "heartbeat_stopped_seen",
            "AWS-08": "pod_changed",
        }
        for case_id, key in decisive.items():
            with self.subTest(case_id=case_id):
                facts = dict(self.PASS_FACTS[case_id])
                facts[key] = False
                self.assertEqual(evaluate_case(case_id, facts)[0], "failed")

    def test_missing_observation_or_log_is_unknown_not_passed(self) -> None:
        for case_id, key in (
            ("AWS-01", "engine_log_match"),
            ("AWS-02", "engine_log_format_confirmed"),
            ("AWS-04", "hubble_observed"),
            ("AWS-05", "receiver_hubble"),
            ("AWS-06", "cache_tetragon"),
        ):
            with self.subTest(case_id=case_id):
                facts = dict(self.PASS_FACTS[case_id])
                facts[key] = False
                self.assertEqual(evaluate_case(case_id, facts)[0], "unknown")

    def test_positive_log_format_does_not_hide_a_denied_request_engine_match(self) -> None:
        facts = dict(self.PASS_FACTS["AWS-02"])
        facts["engine_log_absent"] = False
        self.assertEqual(evaluate_case("AWS-02", facts)[0], "failed")

    def test_aws_05_control_failure_is_unknown_and_never_passed(self) -> None:
        facts = dict(self.PASS_FACTS["AWS-05"])
        facts["receiver_control_connected"] = False
        self.assertEqual(
            evaluate_case("AWS-05", facts),
            ("unknown", "대조 실패: receiver에 닿지 않음"),
        )

    def test_aws_07_clean_stop_passes_without_a_gap_fact(self) -> None:
        facts = dict(self.PASS_FACTS["AWS-07"])
        self.assertNotIn("gap_recorded", facts)
        self.assertEqual(evaluate_case("AWS-07", facts)[0], "passed")

    def test_aws_08_gateway_instance_change_is_decisive(self) -> None:
        facts = dict(self.PASS_FACTS["AWS-08"])
        facts["gateway_instance_changed"] = False
        self.assertEqual(evaluate_case("AWS-08", facts)[0], "failed")

    def test_explicit_not_run_takes_precedence(self) -> None:
        for case_id in CASE_IDS:
            with self.subTest(case_id=case_id):
                self.assertEqual(
                    evaluate_case(case_id, {"not_run_reason": "disabled"})[0],
                    "not_run",
                )

    def test_overall_requires_every_requested_case_to_pass(self) -> None:
        self.assertEqual(_overall([{"result": "passed"}]), "passed")
        self.assertEqual(
            _overall([{"result": "passed"}, {"result": "not_run"}]),
            "unknown",
        )
        self.assertEqual(
            _overall([{"result": "passed"}, {"result": "unknown"}]),
            "unknown",
        )
        self.assertEqual(
            _overall([{"result": "unknown"}, {"result": "failed"}]),
            "failed",
        )


class FakeExecutionTests(unittest.TestCase):
    def test_all_in_pod_python_snippets_compile(self) -> None:
        for code in (TCP_PROBE_CODE, WRITE_DENY_CODE, CACHE_WRITE_CODE):
            with self.subTest(code=code[:30]):
                compile(code, "<aws-acceptance-probe>", "exec")

    def test_aws_01_uses_fake_http_runner_and_requires_exact_engine_log(self) -> None:
        request_id = "normal-1"
        fake_http = FakeHTTP(
            [
                response(
                    200,
                    {"choices": [{"message": {"content": "response text not retained"}}]},
                    request_id,
                ),
                response(200, detail("ALLOWED", attempted=True, outcome="completed")),
            ]
        )
        fake_runner = FakeRunner(
            [CommandResult(0, f"INFO Received request chatcmpl-{request_id}.\n")]
        )
        with tempfile.TemporaryDirectory() as temporary:
            checker = AWSAcceptance(
                arguments(Path(temporary), cases=("AWS-01",)),
                runner=fake_runner,
                http=fake_http,
            )
            checker._run_case("AWS-01", checker.aws_01)
            self.assertEqual(checker.cases[0]["result"], "passed")
            self.assertTrue(checker.engine_log_format_confirmed)
            command = fake_runner.history[0]["argv"]
            self.assertEqual(command[0:3], ["kubectl", "--context", "byul-aws-lab"])
            serialized = json.dumps(checker.cases, ensure_ascii=False)
            self.assertNotIn("response text not retained", serialized)

    def test_aws_02_checks_both_auth_codes_records_and_no_engine_line(self) -> None:
        fake_http = FakeHTTP(
            [
                response(401, {"error": {"code": "AUTH_REQUIRED"}}, "missing-1"),
                response(200, detail("DENIED", reason="AUTH_REQUIRED", attempted=False, status=401)),
                response(401, {"error": {"code": "AUTH_INVALID"}}, "invalid-1"),
                response(200, detail("DENIED", reason="AUTH_INVALID", attempted=False, status=401)),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            checker = AWSAcceptance(
                arguments(Path(temporary), cases=("AWS-02",)),
                runner=FakeRunner([CommandResult(0, "unrelated log\n")]),
                http=fake_http,
            )
            checker.engine_log_format_confirmed = True
            checker._run_case("AWS-02", checker.aws_02)
            self.assertEqual(checker.cases[0]["result"], "passed")
            serialized = json.dumps(checker.cases)
            self.assertNotIn(checker.wrong_token, serialized)

    def test_aws_03_reads_limit_from_policy_api(self) -> None:
        fake_http = FakeHTTP(
            [
                response(200, {"current": {"output_max_tokens": 128}}),
                response(403, {"error": {"code": "MAX_TOKENS_EXCEEDED"}}, "limit-1"),
                response(
                    200,
                    detail(
                        "DENIED",
                        reason="MAX_TOKENS_EXCEEDED",
                        attempted=False,
                        status=403,
                    ),
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            checker = AWSAcceptance(
                arguments(Path(temporary), cases=("AWS-03",)),
                runner=FakeRunner(),
                http=fake_http,
            )
            checker._run_case("AWS-03", checker.aws_03)
            self.assertEqual(checker.cases[0]["result"], "passed")
            self.assertEqual(checker.cases[0]["evidence"]["policy_limit"], 128)
        self.assertEqual(_extract_policy_limit({"policy": {"output_max_tokens": 7}}), 7)
        self.assertIsNone(_extract_policy_limit({"current": {}}))

    def test_aws_05_uses_receiver_pod_ip_and_a_successful_control(self) -> None:
        engine = {
            "items": [
                {
                    "metadata": {"name": "serving-engine-abc", "uid": "engine-uid"},
                    "status": {
                        "containerStatuses": [
                            {"name": "engine", "containerID": "containerd://engine"}
                        ],
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                }
            ]
        }
        receiver = {
            "metadata": {
                "name": "test-receiver",
                "labels": {"app": "test-receiver"},
            },
            "status": {
                "podIP": "10.0.0.42",
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        }
        runner = FakeRunner(
            [
                CommandResult(0, json.dumps(engine)),
                CommandResult(0, json.dumps(receiver)),
                CommandResult(0, "CONNECT_FAILED\n"),
                CommandResult(1, "CONNECTED\n"),
                CommandResult(0, "CONNECT_FAILED\n"),
                CommandResult(0, "CONNECT_FAILED\n"),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            observation = root / "observation"
            observation.mkdir()
            flows = []
            for destination, port in (
                ("10.0.0.42", 8080),
                ("192.0.2.1", 80),
                ("169.254.169.254", 80),
            ):
                flows.append(
                    json.dumps(
                        {
                            "flow": {
                                "time": "9999-01-01T00:00:00+00:00",
                                "verdict": "DROPPED",
                                "source": {"pod_name": "serving-engine-abc"},
                                "destination": {},
                                "IP": {"destination": destination},
                                "l4": {"TCP": {"destination_port": port}},
                            }
                        }
                    )
                )
            (observation / "hubble.jsonl").write_text(
                "\n".join(flows) + "\n", encoding="utf-8"
            )
            checker = AWSAcceptance(
                arguments(root, cases=("AWS-05",)),
                runner=runner,
                http=FakeHTTP([]),
            )
            checker._run_case("AWS-05", checker.aws_05)
            self.assertEqual(checker.cases[0]["result"], "passed")
            self.assertEqual(runner.history[2]["argv"][-2:], ["10.0.0.42", "8080"])
            self.assertEqual(runner.history[3]["argv"][-2:], ["10.0.0.42", "8080"])
            self.assertEqual(runner.history[3]["argv"][5:8], ["exec", "pod/other-client", "-c"])

    def test_gateway_policy_status_exposes_the_numeric_limit_needed_by_aws_03(self) -> None:
        policy = SimpleNamespace(
            profile="default",
            output_max_tokens=128,
            max_n=1,
            extra_fields=(),
        )
        self.assertEqual(GatewayApp._profile_status(policy)["output_max_tokens"], 128)
        self.assertIsNone(GatewayApp._profile_status(None)["output_max_tokens"])

    def test_aws_07_clean_stop_uses_stopped_heartbeats_without_gap_files(self) -> None:
        before = {
            "collectors": [
                {"source": source, "state": "ok", "stale_after_s": 2.0}
                for source in ("observation_feed_hubble", "observation_feed_tetragon")
            ]
        }
        unavailable = {
            "collectors": [
                {"source": source, "state": "error", "stale_after_s": 2.0}
                for source in ("observation_feed_hubble", "observation_feed_tetragon")
            ]
        }
        recovered = {
            "collectors": [
                {"source": source, "state": "ok", "stale_after_s": 2.0}
                for source in ("observation_feed_hubble", "observation_feed_tetragon")
            ]
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            observation = root / "observation"
            observation.mkdir()
            for kind in ("hubble", "tetragon"):
                (observation / f"{kind}.heartbeat.json").write_text(
                    json.dumps(
                        {
                            "schema": HEARTBEAT_SCHEMA,
                            "kind": kind,
                            "state": "stopped",
                            "updated_at": "9999-01-01T00:00:00+00:00",
                        }
                    ),
                    encoding="utf-8",
                )
            checker = AWSAcceptance(
                arguments(
                    root,
                    cases=("AWS-07",),
                    allow_collector_stop=True,
                ),
                runner=FakeRunner([CommandResult(0), CommandResult(0)]),
                http=FakeHTTP(
                    [
                        response(200, before),
                        response(200, unavailable),
                        response(200, recovered),
                    ]
                ),
            )
            checker._run_case("AWS-07", checker.aws_07)
            case = checker.cases[0]
            self.assertEqual(case["result"], "passed")
            self.assertTrue(case["facts"]["heartbeat_stopped_seen"])
            self.assertIsNone(case["facts"]["supplementary_gap_recorded"])
            self.assertFalse(list(observation.glob("*.gaps.jsonl")))
            self.assertEqual(case["evidence"]["timings"]["stale_after_s"], 2.0)

    def test_aws_08_requires_gateway_instance_change_and_pod_replaced_history(self) -> None:
        def engine_pod(uid: str, container_id: str) -> str:
            return json.dumps(
                {
                    "items": [
                        {
                            "metadata": {"name": f"engine-{uid}", "uid": uid},
                            "status": {
                                "containerStatuses": [
                                    {"name": "engine", "containerID": container_id}
                                ],
                                "conditions": [{"type": "Ready", "status": "True"}],
                            },
                        }
                    ]
                }
            )

        before_target = {
            "instance": {"state": "ok", "current": {"instance_id": "old-instance"}},
            "history": [],
            "kubernetes": {"state": "stale", "error": "target Pod snapshot is stale"},
        }
        after_target = {
            "instance": {"state": "ok", "current": {"instance_id": "new-instance"}},
            "history": [
                {
                    "event": "instance_changed",
                    "reason": "pod_replaced",
                    "previous_instance_id": "old-instance",
                    "instance": {"instance_id": "new-instance"},
                }
            ],
            "kubernetes": {"state": "ok"},
        }
        runner = FakeRunner(
            [
                CommandResult(0, engine_pod("old-uid", "containerd://old")),
                CommandResult(0),
                CommandResult(0),
                CommandResult(0, engine_pod("new-uid", "containerd://new")),
            ]
        )
        http = FakeHTTP(
            [
                response(200, before_target),
                response(200, after_target),
                response(
                    200,
                    {"choices": [{"message": {"content": "hello"}}]},
                    "recovery-request",
                ),
                response(200, detail("ALLOWED", attempted=True)),
                response(
                    200,
                    {
                        "collectors": [
                            {
                                "source": "engine_info",
                                "state": "ok",
                                "last_success_at": "9999-01-01T00:00:00+00:00",
                            }
                        ]
                    },
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            checker = AWSAcceptance(
                arguments(
                    Path(temporary),
                    cases=("AWS-08",),
                    allow_engine_restart=True,
                ),
                runner=runner,
                http=http,
            )
            checker._run_case("AWS-08", checker.aws_08)
            case = checker.cases[0]
        self.assertEqual(case["result"], "passed")
        self.assertTrue(case["facts"]["gateway_instance_changed"])
        self.assertEqual(
            case["evidence"]["target_after"]["history_event"]["reason"],
            "pod_replaced",
        )
        self.assertIn("stale", case["evidence"]["snapshot_note"])


class EvidenceTests(unittest.TestCase):
    def test_tokens_and_token_hashes_never_appear_and_permissions_are_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checker = AWSAcceptance(
                arguments(Path(temporary), cases=("AWS-01",)),
                runner=FakeRunner(),
                http=FakeHTTP([]),
            )
            checker.cases.append(
                {
                    "case_id": "AWS-01",
                    "title": "정상",
                    "rt_ids": ["RT-API-01"],
                    "started_at": "now",
                    "ended_at": "now",
                    "result": "unknown",
                    "reason": f"malicious error included {INFERENCE}",
                    "commands": [],
                    "http": [],
                    "facts": {},
                    "evidence": {
                        "also": VIEWER,
                        "hash": hashlib.sha256(INFERENCE.encode()).hexdigest(),
                    },
                }
            )
            json_path, markdown_path = checker.write_report()
            combined = json_path.read_text(encoding="utf-8") + markdown_path.read_text(encoding="utf-8")
            for secret in (INFERENCE, VIEWER, checker.wrong_token):
                self.assertNotIn(secret, combined)
                self.assertNotIn(hashlib.sha256(secret.encode()).hexdigest(), combined)
            self.assertNotIn("Say hello in one short sentence.", combined)
            self.assertEqual(stat.S_IMODE(checker.evidence_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(json_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(markdown_path.stat().st_mode), 0o600)

    def test_token_file_parser_does_not_echo_bad_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = arguments(root)
            bad = "invalid token with spaces"
            Path(args.token_env_file).write_text(
                f"RUNTIME_INFERENCE_TOKEN={bad}\nRUNTIME_VIEWER_TOKEN={VIEWER}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "invalid token value") as raised:
                AWSAcceptance(args, runner=FakeRunner(), http=FakeHTTP([]))
            self.assertNotIn(bad, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
