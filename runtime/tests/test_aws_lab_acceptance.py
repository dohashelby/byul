"""Unit tests for the AWS acceptance tool; no network or kubectl is used."""

from __future__ import annotations

from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import urlsplit

from tools.aws_lab_acceptance import (
    AWSAcceptance,
    CASE_IDS,
    CommandResult,
    HTTPResponse,
    CACHE_WRITE_CODE,
    TCP_PROBE_CODE,
    WRITE_DENY_CODE,
    ROUTE_RECORD_PROBE_CODE,
    COUNT_TOKENS_CODE,
    _extract_policy_limit,
    _overall,
    evaluate_case,
    parse_args,
    phase_exec_inventory,
    _completion_usage,
    _timeline_reset,
    _fixed_request_hash,
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
                "json": json.loads(body) if body is not None else None,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected HTTP call")
        return self.responses.pop(0)


def response(status: int, body: object, request_id: str | None = None) -> HTTPResponse:
    headers = {"X-Request-ID": request_id} if request_id else {}
    return HTTPResponse(status, headers, json.dumps(body).encode("utf-8"))


def sse_response(body: bytes, request_id: str) -> HTTPResponse:
    return HTTPResponse(200, {"X-Request-ID": request_id}, body)


class FakeClock:
    def __init__(self, *, sleep_jump: float | None = None) -> None:
        self.value = 0.0
        self.sleep_jump = sleep_jump

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += self.sleep_jump if self.sleep_jump is not None else seconds


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
    def test_request_case_checks_usage_and_both_stream_endings(self) -> None:
        class LogRunner(FakeRunner):
            def run(self, argv, *, timeout=30):
                return CommandResult(0, "\n".join(
                    f'Received request {prefix}{rid}' for prefix, rid in
                    (("chatcmpl-", "chat-n"), ("chatcmpl-", "chat-s"),
                     ("cmpl-", "comp-n-0"), ("cmpl-", "comp-s-0"))))
        with tempfile.TemporaryDirectory() as directory:
            responses = [
                response(200, {"choices": [{"message": {"content": "ok"}}], "usage": {"completion_tokens": 8}}, "chat-n"),
                sse_response(b'data: {"choices":[{}]}\n\ndata: [DONE]\n\n', "chat-s"),
                response(200, detail("ALLOWED", attempted=True, outcome="completed")),
                response(200, {"choices": [{"text": "ok"}], "usage": {"completion_tokens": 8}}, "comp-n"),
                sse_response(b'data: {"choices":[{}]}\n\ndata: [DONE]\n\n', "comp-s"),
                response(200, detail("ALLOWED", attempted=True, outcome="completed")),
            ]
            checker = AWSAcceptance(arguments(Path(directory)), runner=LogRunner(), http=FakeHTTP(responses))
            checker._context_limits = lambda: (2048, 256)
            checker._context_probe = lambda route, token, limit, output: (True, True, {"route": route})
            checker._engine_pod = lambda: ({"name": "engine-1", "uid": "pod-1", "container_id": "containerd://1", "started_at": "now"}, CommandResult(0))
            facts, evidence = checker.aws_18()
            self.assertEqual(evaluate_case("AWS-18", facts)[0], "passed")
            self.assertEqual(len(evidence["requests"]), 4)
            self.assertTrue(all(row["truncated"] is False for row in evidence["requests"] if row["stream"]))
            checker.http.responses = responses = [response(200, {"choices": [{"message": {"content": "ok"}}]}, "chat-n"),
                                                  sse_response(b'data: {"choices":[{}]}\n\n', "chat-s"),
                                                  response(200, detail("ALLOWED", attempted=True, outcome="completed")),
                                                  response(200, {"choices": [{"text": "ok"}]}, "comp-n"),
                                                  sse_response(b'data: {"choices":[{}]}\n\ndata: [DONE]\n\n', "comp-s"),
                                                  response(200, detail("ALLOWED", attempted=True, outcome="completed"))]
            checker._engine_pod = lambda: ({"name": "engine-1", "uid": "pod-1", "container_id": "containerd://1", "started_at": "now"}, CommandResult(0))
            facts, evidence = checker.aws_18()
            self.assertIsNone(facts["usage"])
            self.assertFalse(facts["stream"])
            self.assertTrue(evidence["requests"][1]["truncated"])

    def test_aws_18_done_with_failed_gateway_record_is_not_stream_success(self) -> None:
        failed = detail("ERROR", reason="ENGINE_BAD_RESPONSE", attempted=True,
                        outcome="invalid_completion_stream")
        failed["final_state"]["delivery"].update(
            response="failed", error_code="ENGINE_BAD_RESPONSE")
        responses = [
            response(200, {"choices": [{"message": {"content": "ok"}}],
                           "usage": {"completion_tokens": 1}}, "chat-n"),
            sse_response(b'data: {"choices":[{}]}\n\ndata: [DONE]\n\n', "chat-s"),
            response(200, detail("ALLOWED", attempted=True, outcome="completed")),
            response(200, {"choices": [{"text": "ok"}],
                           "usage": {"completion_tokens": 1}}, "comp-n"),
            sse_response(b'data: {"choices":[{}]}\n\ndata: [DONE]\n\ndata: {bad}\n\n', "comp-s"),
            response(200, failed),
        ]
        with tempfile.TemporaryDirectory() as directory:
            checker = AWSAcceptance(arguments(Path(directory)), runner=FakeRunner(),
                                    http=FakeHTTP(responses))
            checker._context_limits = lambda: None
            checker._engine_pod = lambda: ({"name": "engine-1", "uid": "pod-1", "container_id": "containerd://1", "started_at": "now"}, CommandResult(0))
            facts, evidence = checker.aws_18()
        self.assertFalse(facts["stream"])
        self.assertEqual(evidence["requests"][3]["record"]["delivery"]["error_code"],
                         "ENGINE_BAD_RESPONSE")

    def test_aws_18_missing_gateway_record_is_unknown(self) -> None:
        responses = [
            response(200, {"choices": [{}], "usage": {"completion_tokens": 1}}, "chat-n"),
            sse_response(b'data: {"choices":[{}]}\n\ndata: [DONE]\n\n', "chat-s"),
            response(404, {}),
            response(200, {"choices": [{}], "usage": {"completion_tokens": 1}}, "comp-n"),
            sse_response(b'data: {"choices":[{}]}\n\ndata: [DONE]\n\n', "comp-s"),
            response(200, detail("ALLOWED", attempted=True, outcome="completed")),
        ]
        with tempfile.TemporaryDirectory() as directory:
            checker = AWSAcceptance(arguments(Path(directory)), runner=FakeRunner(),
                                    http=FakeHTTP(responses))
            checker._context_limits = lambda: None
            facts, _ = checker.aws_18()
        self.assertIsNone(facts["stream"])

    def test_rejection_case_requires_positive_sglang_log(self) -> None:
        class LogRunner(FakeRunner):
            def __init__(self, logs):
                super().__init__()
                self.logs = logs
            def run(self, argv, *, timeout=30):
                return CommandResult(0, self.logs)
        with tempfile.TemporaryDirectory() as directory:
            replies = [response(200, {"choices": [{"message": {"content": "ok"}}]}, "normal")]
            replies += [response(404, {"error": {"code": "NOT_FOUND"}}, f"path-{i}") for i in range(12)]
            replies += [response(400, {"error": {"code": "UNSUPPORTED_FIELD"}}, f"field-{i}") for i in range(4)]
            runner = LogRunner('{"event":"request.received","headers":{"x-request-id":"normal"}}')
            checker = AWSAcceptance(arguments(Path(directory), engine="sglang"), runner=runner, http=FakeHTTP(replies))
            checker._route_rejection_record = lambda _id: {"found": True, "reason": "NOT_FOUND",
                                                            "response_code": 404, "forwarded_to_engine": False}
            checker._detail = lambda _id: detail("INVALID", reason="UNSUPPORTED_FIELD", attempted=False, status=400)
            checker._engine_pod = lambda: ({"name": "engine-1", "uid": "pod-1", "container_id": "containerd://1", "started_at": "now"}, CommandResult(0))
            facts, evidence = checker.aws_19()
            self.assertEqual(evaluate_case("AWS-19", facts)[0], "passed")
            self.assertEqual(len(evidence["attempts"]), 16)
            runner.logs = ""
            checker.http.responses = list(replies)
            facts, _ = checker.aws_19()
            self.assertIsNone(facts["positive_control"])
            self.assertIsNone(facts["engine_log_absent"])
            self.assertEqual(evaluate_case("AWS-19", facts)[0], "unknown")

    def test_route_record_probe_distinguishes_lookup_failure_from_missing_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checker = AWSAcceptance(arguments(Path(directory)), runner=FakeRunner(), http=FakeHTTP([]))
            for result in (CommandResult(1), CommandResult(0, "not json"), CommandResult(0, "{}")):
                checker._kubectl = lambda *_args, **_kwargs: result
                self.assertIsNone(checker._route_rejection_record("path-1"))
            checker._kubectl = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("kubectl unavailable"))
            self.assertIsNone(checker._route_rejection_record("path-1"))
            checker._kubectl = lambda *_args, **_kwargs: CommandResult(0, '{"found":false}')
            self.assertEqual(checker._route_rejection_record("path-1"), {"found": False})

    def test_rejection_lookup_failure_is_unknown_but_found_false_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            replies = [response(200, {"choices": [{}]}, "normal")]
            replies += [response(404, {}, f"path-{i}") for i in range(12)]
            replies += [response(400, {"error": {"code": "UNSUPPORTED_FIELD"}}, f"field-{i}") for i in range(4)]
            checker = AWSAcceptance(arguments(root), runner=FakeRunner(), http=FakeHTTP(list(replies)))
            checker._detail = lambda _id: detail("INVALID", reason="UNSUPPORTED_FIELD", attempted=False, status=400)
            checker._engine_logs = lambda _since: (CommandResult(1), "")
            checker._route_rejection_record = lambda _id: None
            facts, _evidence = checker.aws_19()
            self.assertIsNone(facts["records"])
            self.assertIsNone(facts["reasons"])
            self.assertIsNone(facts["not_delivered"])
            self.assertEqual(evaluate_case("AWS-19", facts)[0], "unknown")
            checker.http.responses = list(replies)
            checker._route_rejection_record = lambda _id: {"found": False}
            checker._engine_pod = lambda: ({"name": "engine-1", "uid": "pod-1", "container_id": "containerd://1", "started_at": "now"}, CommandResult(0))
            facts, evidence = checker.aws_19()
            self.assertFalse(facts["records"])
            self.assertEqual(evaluate_case("AWS-19", facts)[0], "failed")
            self.assertEqual(len(evidence["missing_route_record_ids"]), 12)
            self.assertIn("한도 초과", evidence["route_record_limit_note"])

    def test_context_probe_exact_boundary_and_over_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            http = FakeHTTP([response(200, {"choices": [{"text": "ok"}],
                                             "usage": {"completion_tokens": 8}}, "boundary"),
                             response(403, {"error": {"code": "CONTEXT_LIMIT_EXCEEDED"}}, "over")])
            checker = AWSAcceptance(arguments(Path(directory)), runner=FakeRunner(), http=http)
            checker._engine_token_count = lambda route, prompt: len(prompt.split())
            checker._detail = lambda _id: detail("DENIED", reason="CONTEXT_LIMIT_EXCEEDED",
                                                  attempted=False, status=403)
            boundary, exceeded, evidence = checker._context_probe("/v1/completions", INFERENCE, 128, 128)
            self.assertTrue(boundary)
            self.assertTrue(exceeded)
            self.assertEqual(evidence["sum"], 128)
            self.assertEqual([call["json"]["max_tokens"] for call in http.calls], [8, 9])

    def test_context_probe_marks_engine_length_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            http = FakeHTTP([response(200, {"choices": [{"finish_reason": "length"}], "usage": {"completion_tokens": 3}}, "boundary"),
                             response(403, {"error": {"code": "CONTEXT_LIMIT_EXCEEDED"}}, "over")])
            checker = AWSAcceptance(arguments(Path(directory)), runner=FakeRunner(), http=http)
            checker._engine_token_count = lambda route, prompt: len(prompt.split())
            checker._detail = lambda _id: detail("DENIED", reason="CONTEXT_LIMIT_EXCEEDED", attempted=False, status=403)
            boundary, _exceeded, evidence = checker._context_probe("/v1/completions", INFERENCE, 128, 128)
            self.assertFalse(boundary)
            self.assertTrue(evidence["engine_truncated"])

    def test_direct_token_count_keeps_prompt_out_of_command_journal(self) -> None:
        class CountingRunner(FakeRunner):
            def run(self, argv, *, timeout=30, input=None):
                self.history.append({"argv": list(argv), "returncode": 0})
                self.input = input
                return CommandResult(0, "12\n")
        with tempfile.TemporaryDirectory() as directory:
            runner = CountingRunner()
            checker = AWSAcceptance(arguments(Path(directory), engine="sglang"), runner=runner, http=FakeHTTP([]))
            checker._engine_pod = lambda: ({"name": "fake-engine"}, CommandResult(0))
            self.assertEqual(checker._engine_token_count("/v1/chat/completions", "private prompt"), 12)
            self.assertNotIn("private prompt", str(runner.history))
            self.assertIn("private prompt", runner.input)
            self.assertNotIn("add_generation_prompt", runner.input)

    def test_context_limits_come_from_gateway_policy_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            http = FakeHTTP([response(200, {"current": {"max_context_tokens": 2048,
                                                         "output_max_tokens": 256}})])
            checker = AWSAcceptance(arguments(Path(directory)), runner=FakeRunner(), http=http)
            self.assertEqual(checker._context_limits(), (2048, 256))
            self.assertEqual(http.calls[0]["path"], "/api/policy")

    def test_pod_configuration_keeps_names_and_redacts_admin_arg(self) -> None:
        pod = {"items": [{"spec": {"containers": [{"name": "engine", "image": "image@sha256:abc",
                  "command": ["/bin/sh"], "args": ["exec python3 -m sglang.launch_server --admin-api-key \"private-key\""],
                  "env": [{"name": "SGLANG_LOG_REQUEST_HEADERS", "value": "x-request-id"},
                          {"name": "SECRET", "value": "private-env"}]}]},
                  "status": {"containerStatuses": [{"name": "engine", "imageID": "sha256:abc"}]}}]}
        with tempfile.TemporaryDirectory() as directory:
            checker = AWSAcceptance(arguments(Path(directory)),
                                    runner=FakeRunner([CommandResult(0, json.dumps(pod))]), http=FakeHTTP([]))
            evidence = checker._pod_configuration()
            self.assertEqual(evidence["env_names"], ["SGLANG_LOG_REQUEST_HEADERS", "SECRET"])
            self.assertEqual(evidence["image_id"], "sha256:abc")
            self.assertNotIn("private-key", str(evidence))
            self.assertNotIn("private-env", str(evidence))

    def test_request_and_rejection_cases_are_explicit(self) -> None:
        self.assertEqual(parse_args(["--kube-context", "byul-aws-lab", "--token-env-file", "unused.env"]).cases[-1], "AWS-11")
        selected = parse_args(["--kube-context", "byul-aws-lab", "--token-env-file", "unused.env", "--cases", "AWS-18,AWS-19"])
        self.assertEqual(selected.cases, ("AWS-18", "AWS-19"))
        self.assertEqual(evaluate_case("AWS-18", {"normal": True, "stream": True, "usage": None})[0], "unknown")
        self.assertEqual(evaluate_case("AWS-18", {"normal": True, "stream": True, "usage": True,
                                                   "log_match": True, "context_boundary": None,
                                                   "context_exceeded": None})[0], "unknown")
        self.assertEqual(evaluate_case("AWS-19", {"codes": True, "reasons": True, "records": True, "not_delivered": True, "engine_log_absent": True, "positive_control": None})[0], "unknown")

    def test_usage_and_fixed_request_hash(self) -> None:
        self.assertEqual(_completion_usage(response(200, {"usage": {"completion_tokens": 8}})), 8)
        self.assertIsNone(_completion_usage(response(200, {"choices": []})))
        self.assertIsNone(_completion_usage(response(200, {"usage": {"completion_tokens": "8"}})))
        self.assertEqual(_fixed_request_hash(), _fixed_request_hash())
        self.assertEqual(len(_fixed_request_hash()), 64)

    def test_engine_option_defaults_to_vllm_and_accepts_sglang(self) -> None:
        argv = ["--kube-context", "byul-aws-lab", "--token-env-file", "unused.env"]
        self.assertEqual(parse_args(argv).engine, "vllm")
        self.assertEqual(parse_args(argv + ["--engine", "sglang"]).engine, "sglang")

    def test_sglang_json_log_matches_only_header_and_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checker = AWSAcceptance(arguments(Path(directory), engine="sglang"), runner=FakeRunner(), http=FakeHTTP([]))
            sample = ('INFO: {"event":"request.received","headers":{"x-request-id":"normal-1"}}\n'
                      '{"event":"request.finished","headers":{"x-request-id":"normal-1"}}')
            self.assertTrue(checker._log_received(sample, "normal-1", "/v1/chat/completions"))
            self.assertFalse(checker._log_received(sample, "denied-1", "/v1/chat/completions"))
            self.assertFalse(checker._log_received('{"event":"other","headers":{"x-request-id":"normal-1"}}',
                                                   "normal-1", "/v1/completions"))
            self.assertFalse(checker._log_received('Received request chatcmpl-normal-1',
                                                   "normal-1", "/v1/chat/completions"))
            self.assertEqual(checker._sglang_prompt_fields(sample), [])
            self.assertEqual(checker._sglang_prompt_fields('{"event":"request.received","obj":{"text":"secret"}}'), ["text"])

    def test_phase_inventory_groups_without_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timeline = root / "timeline.json"
            timeline.write_text(json.dumps({"schema_version": "runtime-phase-timeline/1.0",
                "entries": [{"phase": "inferring", "from": "2026-11-02T10:00:00Z",
                             "until": None, "basis": "engine_signal"}],
                "collector": {"state": "running", "pod_uid": "uid-a", "pod_name": "engine-a",
                              "written_at": "2026-11-02T10:00:02Z", "last_log_at": None,
                              "log_delivery_lag_seconds": None}}), encoding="utf-8")
            source = root / "tetragon.jsonl"
            source.write_text(json.dumps({"time": "2026-11-02T10:00:01Z", "process_exec": {
                "process": {"binary": "/usr/bin/python3", "pod": {"namespace": "serving-lab",
                    "pod_name": "engine-a", "uid": "uid-a", "pod_labels": {"app": "serving-engine"}}}}}) + "\n", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(phase_exec_inventory(["--tetragon", str(source), "--timeline", str(timeline)]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["counts"], [{"phase": "inferring", "pod": "engine-a",
                                                  "path": "/usr/bin/python3", "count": 1}])
            self.assertNotIn("verdict", output.getvalue())
            old = json.loads(timeline.read_text(encoding="utf-8"))
            old["collector"]["written_at"] = "2026-11-02T09:59:00Z"
            timeline.write_text(json.dumps(old), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(phase_exec_inventory(["--tetragon", str(source), "--timeline", str(timeline)]), 0)
            self.assertEqual(json.loads(output.getvalue())["counts"][0]["phase"], "unknown")

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

    def test_exec_evidence_requires_exact_engine_pod(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checker = AWSAcceptance(arguments(Path(temporary)), runner=FakeRunner(), http=FakeHTTP([]))
            target = {"pod": {"namespace": "serving-lab", "pod": "serving-engine-1"}, "binary": "/bin/sh"}
            other = {"pod": {"namespace": "serving-lab", "pod": "other-client"}, "binary": "/bin/sh"}
            self.assertTrue(checker._engine_event(target, "serving-engine-1"))
            self.assertFalse(checker._engine_event(other, "serving-engine-1"))


class DecisionTests(unittest.TestCase):
    def test_aws11_gap_makes_warning_inventory_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observation = root / "observation"
            observation.mkdir()
            checker = AWSAcceptance(arguments(root), runner=FakeRunner(), http=FakeHTTP([]))
            checker.normal_before = {"normal_ok": True, "stream_ok": True, "cache_ok": True}
            checker.detection_started_at = "2026-11-02T10:00:00Z"
            checker._normal_control = lambda: {"normal_ok": True, "stream_ok": True, "cache_ok": True}
            checker._tetragon_observations = lambda _since: (True, [{
                "kind": "process_exec", "phase": ["inferring"], "exec_verdict": "allowed_in_phase",
                "phase_timeline_fresh": True, "observed_at": "2026-11-02T10:00:02Z"}])
            checker._collectors = lambda: (response(200, {}), {
                "observation_feed_tetragon": {"state": "ok"}})
            gap = observation / "tetragon.gaps.jsonl"
            gap.write_text(json.dumps({"event": "gap_opened", "kind": "tetragon", "gap_id": "gap-1",
                                       "opened_at": "2026-11-02T10:00:01Z"}) + "\n", encoding="utf-8")
            facts, evidence = checker.aws_11()
            self.assertIsNone(facts["false_warnings_zero"])
            self.assertFalse(evidence["warning_list_complete"])
            checker._run_case("AWS-11", checker.aws_11)
            self.assertEqual(checker.cases[-1]["result"], "unknown")
            with gap.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "gap_closed", "kind": "tetragon", "gap_id": "gap-1",
                                         "opened_at": "2026-11-02T10:00:01Z",
                                         "closed_at": "2026-11-02T10:00:03Z",
                                         "closed_by": "collector_restart", "backfill": "not_requested"}) + "\n")
            self.assertFalse(checker._tetragon_gaps_clear("2026-11-02T10:00:02Z"))
            self.assertTrue(checker._tetragon_gaps_clear("2026-11-02T10:00:04Z"))

    def test_aws11_unavailable_tetragon_collector_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checker = AWSAcceptance(arguments(Path(directory)), runner=FakeRunner(), http=FakeHTTP([]))
            checker.normal_before = {"normal_ok": True, "stream_ok": True, "cache_ok": True}
            checker.detection_started_at = "2026-11-02T10:00:00Z"
            checker._normal_control = lambda: {"normal_ok": True, "stream_ok": True, "cache_ok": True}
            checker._tetragon_observations = lambda _since: (True, [{
                "kind": "process_exec", "phase": ["inferring"], "exec_verdict": "allowed_in_phase",
                "phase_timeline_fresh": True, "observed_at": "2026-11-02T10:00:02Z"}])
            checker._collectors = lambda: (response(200, {}), {
                "observation_feed_tetragon": {"state": "error"}})
            facts, evidence = checker.aws_11()
            self.assertIsNone(facts["false_warnings_zero"])
            self.assertFalse(evidence["warning_list_complete"])

    def test_aws11_missing_gap_record_directory_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checker = AWSAcceptance(arguments(Path(directory)), runner=FakeRunner(), http=FakeHTTP([]))
            checker.normal_before = {"normal_ok": True, "stream_ok": True, "cache_ok": True}
            checker.detection_started_at = "2026-11-02T10:00:00Z"
            checker._normal_control = lambda: {"normal_ok": True, "stream_ok": True, "cache_ok": True}
            checker._tetragon_observations = lambda _since: (True, [{
                "kind": "process_exec", "phase": ["inferring"], "exec_verdict": "allowed_in_phase",
                "phase_timeline_fresh": True, "observed_at": "2026-11-02T10:00:02Z"}])
            checker._collectors = lambda: (response(200, {}), {
                "observation_feed_tetragon": {"state": "ok"}})
            facts, evidence = checker.aws_11()
            self.assertIsNone(facts["false_warnings_zero"])
            self.assertFalse(evidence["warning_list_complete"])

    def test_aws11_unknown_without_valid_phase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "observation").mkdir()
            checker = AWSAcceptance(arguments(Path(directory)), runner=FakeRunner(), http=FakeHTTP([]))
            checker.normal_before = {"normal_ok": True, "stream_ok": True, "cache_ok": True}
            checker.detection_started_at = "2026-11-02T10:00:00Z"
            checker._normal_control = lambda: {"normal_ok": True, "stream_ok": True, "cache_ok": True}
            rows = [{"kind": "process_exec", "phase": ["unknown"], "exec_verdict": "phase_unknown",
                     "observed_at": "2026-11-02T10:00:01Z"}]
            checker._tetragon_observations = lambda _since: (True, rows)
            checker._collectors = lambda: (response(200, {}), {
                "observation_feed_tetragon": {"state": "ok"}})
            facts, evidence = checker.aws_11()
            self.assertIsNone(facts["false_warnings_zero"])
            self.assertEqual(evidence["phase_evidence"]["unknown_exec_count"], 1)
            rows[0].update(phase=["inferring"], exec_verdict="allowed_in_phase",
                           phase_basis=["engine_signal"], phase_timeline_fresh=True,
                           phase_timeline_written_at="2026-11-02T10:00:01Z")
            facts, evidence = checker.aws_11()
            self.assertTrue(facts["false_warnings_zero"])
            self.assertEqual(evidence["phase_evidence"]["bases"], ["engine_signal"])
            rows[0]["phase_timeline_fresh"] = False
            facts, _evidence = checker.aws_11()
            self.assertIsNone(facts["false_warnings_zero"])

    def test_aws11_sglang_ignores_leftover_phase_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observation = root / "observation"
            observation.mkdir()
            (observation / "phase-timeline.json").write_text(json.dumps({
                "schema_version": "runtime-phase-timeline/1.0",
                "collector": {"state": "running", "pod_uid": "old-vllm", "written_at": "2026-11-02T00:00:00Z"},
                "entries": [{"phase": "inferring"}],
            }), encoding="utf-8")
            checker = AWSAcceptance(arguments(root, engine="sglang"), runner=FakeRunner(), http=FakeHTTP([]))
            checker.normal_before = {"normal_ok": True, "stream_ok": True, "cache_ok": True}
            checker.detection_started_at = "2026-11-02T10:00:00Z"
            checker._normal_control = lambda: {"normal_ok": True, "stream_ok": True, "cache_ok": True}
            checker._tetragon_observations = lambda _since: (True, [{"kind": "process_exec", "pod": {"pod": "engine-current"}, "phase": ["inferring"],
                "exec_verdict": "allowed_in_phase", "phase_timeline_fresh": True}])
            checker._collectors = lambda: (response(200, {}), {"observation_feed_tetragon": {"state": "ok"}})
            facts, _evidence = checker.aws_11()
            self.assertIsNone(facts["false_warnings_zero"])
            self.assertEqual(evaluate_case("AWS-11", facts)[0], "unknown")

    def test_aws11_sglang_uses_current_phase_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "observation").mkdir()
            checker = AWSAcceptance(arguments(Path(directory), engine="sglang"), runner=FakeRunner(), http=FakeHTTP([]))
            checker.normal_before = {"normal_ok": True, "stream_ok": True, "cache_ok": True}
            checker.detection_started_at = "2026-11-03T00:00:00Z"
            checker._normal_control = lambda: {"normal_ok": True, "stream_ok": True, "cache_ok": True}
            checker._tetragon_observations = lambda _since: (True, [{"kind": "process_exec", "pod": {"pod": "engine-current"}, "phase": ["inferring"],
                "exec_verdict": "allowed_in_phase", "phase_timeline_fresh": True}])
            checker._collectors = lambda: (response(200, {}), {"observation_feed_tetragon": {"state": "ok"}})
            checker._engine_pod = lambda: ({"uid": "sglang-pod", "name": "engine-current"}, CommandResult(0))
            checker._timeline = lambda: {"extraction": {"engine": "sglang"},
                "collector": {"state": "running", "pod_uid": "sglang-pod", "written_at": datetime.now(timezone.utc).isoformat()}}
            facts, _ = checker.aws_11()
            self.assertTrue(facts["false_warnings_zero"])
            self.assertEqual(evaluate_case("AWS-11", facts)[0], "passed")
            checker._tetragon_observations = lambda _since: (True, [{"kind": "process_exec", "pod": {"pod": "old-engine"},
                "phase": ["inferring"], "exec_verdict": "allowed_in_phase", "phase_timeline_fresh": True}])
            self.assertIsNone(checker.aws_11()[0]["false_warnings_zero"])

    def test_aws11_variable_paths_before_phase_do_not_count_as_false_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "observation").mkdir()
            checker = AWSAcceptance(arguments(Path(directory)), runner=FakeRunner(), http=FakeHTTP([]))
            checker.normal_before = {"normal_ok": True, "stream_ok": True, "cache_ok": True}
            checker.detection_started_at = "2026-11-03T00:00:00Z"
            checker._normal_control = lambda: {"normal_ok": True, "stream_ok": True, "cache_ok": True}
            rows = [{"kind": "process_exec", "binary": binary, "phase": ["unknown"],
                     "exec_verdict": "phase_unknown"} for binary in ("/proc/self/exe", "/dev/fd/11")]
            rows.append({"kind": "process_exec", "binary": "/usr/bin/nvidia-ctk", "phase": ["loading"],
                         "exec_verdict": "allowed_in_phase", "phase_timeline_fresh": True})
            checker._tetragon_observations = lambda _since: (True, rows)
            checker._collectors = lambda: (response(200, {}), {"observation_feed_tetragon": {"state": "ok"}})
            facts, evidence = checker.aws_11()
            self.assertTrue(facts["false_warnings_zero"])
            self.assertEqual(evidence["false_warning_count"], 0)
            self.assertEqual(evidence["phase_evidence"]["unknown_exec_count"], 2)

    PASS_FACTS = {
        "AWS-01": dict.fromkeys(
            ("http_200", "valid_completion", "record_allowed", "delivery_attempted", "engine_log_match", "stream_ok"), True
        ),
        "AWS-02": dict.fromkeys(
            ("auth_codes", "records_denied", "not_delivered", "engine_log_absent", "engine_log_format_confirmed"), True
        ),
        "AWS-03": dict.fromkeys(
            (
                "policy_limit_read",
                "boundary_allowed",
                "http_403",
                "reason_code",
                "not_delivered",
                "engine_log_absent",
                "engine_log_format_confirmed",
            ),
            True,
        ),
        "AWS-04": dict.fromkeys(
            ("direct_failed", "gateway_tcp_control", "gateway_control_200", "hubble_dropped"), True
        ),
        "AWS-05": dict.fromkeys(
            ("engine_receiver_failed", "receiver_control_connected", "receiver_hubble_dropped"), True
        ),
        "AWS-06": dict.fromkeys(
            ("model_write_denied", "cache_write_succeeded", "cache_harmless_removed", "cache_tetragon"), True
        ),
        "AWS-07": dict.fromkeys(
            ("stop_ok", "heartbeat_stopped_seen", "collectors_unavailable", "stopped_request_200_unverified", "start_ok", "collectors_recovered", "target_recovered", "inference_recovered"), True
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
        "AWS-09": dict.fromkeys(("absolute_exec_ok", "path_exec_ok", "path_observed", "engine_pod_match", "suspect_verdict"), True),
        "AWS-10": dict.fromkeys(("read_ok", "path_match", "engine_pod_match", "read_access", "monitor_detected"), True),
        "AWS-11": dict.fromkeys(("normal_before", "normal_after", "stream_before", "stream_after", "cache_current_before", "cache_current_after", "false_warnings_zero"), True),
        "AWS-12": dict.fromkeys(("baseline_connected", "policy_timeout", "hubble_dropped", "restored_connected", "cleanup_ok"), True),
        "AWS-13": dict.fromkeys(("bad_bundle_rejected", "recovered_200", "recovered_denied", "cleanup_ok"), True),
        "AWS-14": dict.fromkeys(("restart_ok", "initial_503", "recovered_200", "within_10s"), True),
        "AWS-15": dict.fromkeys(("stop_ok", "restart_ok", "observed_503", "recovered_200", "cleanup_ok"), True),
        "AWS-16": dict.fromkeys(("stop_ok", "expired_unknown", "request_200", "cleanup_ok"), True),
        "AWS-17": dict.fromkeys(("pod_changed", "timeline_new_uid", "timeline_reset", "old_phase_absent"), True),
        "AWS-18": dict.fromkeys(("normal", "stream", "usage", "log_match", "context_boundary", "context_exceeded"), True),
        "AWS-19": dict.fromkeys(("codes", "reasons", "records", "not_delivered", "engine_log_absent", "positive_control"), True),
        "AWS-20": {"enforce_method": "override_lsm", **dict.fromkeys(
            ("enforce_active", "monitor_read_ok", "read_eperm", "raw_override", "normal_200", "cleanup_ok", "restore_read_ok"), True
        )},
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
            "AWS-09": "suspect_verdict",
            "AWS-10": "monitor_detected",
            "AWS-11": "false_warnings_zero",
            "AWS-12": "restored_connected",
            "AWS-13": "bad_bundle_rejected",
            "AWS-14": "within_10s",
            "AWS-15": "recovered_200",
            "AWS-16": "request_200",
            "AWS-17": "pod_changed",
            "AWS-18": "usage",
            "AWS-19": "codes",
            "AWS-20": "read_eperm",
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
            ("AWS-03", "engine_log_format_confirmed"),
            ("AWS-04", "hubble_dropped"),
            ("AWS-05", "receiver_hubble_dropped"),
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

    def test_new_decisive_controls_cannot_be_omitted(self) -> None:
        for case_id, key in (
            ("AWS-01", "stream_ok"),
            ("AWS-03", "boundary_allowed"),
            ("AWS-03", "engine_log_absent"),
            ("AWS-07", "inference_recovered"),
        ):
            with self.subTest(case_id=case_id, key=key):
                facts = dict(self.PASS_FACTS[case_id])
                facts[key] = False
                self.assertEqual(evaluate_case(case_id, facts)[0], "failed")

    def test_forwarded_hubble_or_non_timeout_failure_does_not_pass(self) -> None:
        for case_id, key in (
            ("AWS-04", "hubble_dropped"),
            ("AWS-05", "receiver_hubble_dropped"),
            ("AWS-04", "direct_failed"),
            ("AWS-05", "engine_receiver_failed"),
        ):
            with self.subTest(case_id=case_id, key=key):
                facts = dict(self.PASS_FACTS[case_id])
                facts[key] = False
                self.assertNotEqual(evaluate_case(case_id, facts)[0], "passed")

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

    def test_new_case_missing_evidence_and_cleanup_priority(self) -> None:
        for case_id in CASE_IDS[11:]:
            with self.subTest(case_id=case_id):
                facts = dict(self.PASS_FACTS[case_id])
                facts[next(iter(facts))] = None
                self.assertEqual(evaluate_case(case_id, facts)[0], "unknown")
        baseline = dict(self.PASS_FACTS["AWS-12"])
        baseline["baseline_connected"] = False
        self.assertIn("비교 불가", evaluate_case("AWS-12", baseline)[1])
        for case_id in ("AWS-12", "AWS-13", "AWS-15", "AWS-16"):
            facts = dict(self.PASS_FACTS[case_id], cleanup_ok=False, unknown_reason="probe missing")
            self.assertEqual(evaluate_case(case_id, facts)[0], "failed")
        connected = dict(self.PASS_FACTS["AWS-12"], policy_connected=True, policy_timeout=False)
        self.assertEqual(evaluate_case("AWS-12", connected)[0], "failed")
        refused = dict(self.PASS_FACTS["AWS-12"], policy_other_error=True, policy_timeout=False)
        self.assertEqual(evaluate_case("AWS-12", refused)[0], "unknown")

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
    def test_engine_pod_ignores_finished_and_terminating_pods(self) -> None:
        def item(name: str, phase: str, *, deleting: bool = False) -> dict[str, object]:
            metadata: dict[str, object] = {"name": name, "uid": name}
            if deleting:
                metadata["deletionTimestamp"] = "2026-11-01T00:00:00Z"
            return {
                "metadata": metadata,
                "status": {
                    "phase": phase,
                    "containerStatuses": [{"name": "engine", "containerID": name}],
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            }

        finished = [item("failed", "Failed"), item("succeeded", "Succeeded")]
        running = item("running", "Running")
        cases = (
            (finished + [running], "running"),
            (finished, None),
            (finished + [running, item("other", "Running")], None),
            ([running, item("terminating", "Running", deleting=True)], "running"),
        )
        for pods, expected in cases:
            with self.subTest(expected=expected, count=len(pods)), tempfile.TemporaryDirectory() as temporary:
                runner = FakeRunner([CommandResult(0, json.dumps({"items": pods}))])
                checker = AWSAcceptance(arguments(Path(temporary)), runner=runner, http=FakeHTTP([]))
                selected, _ = checker._engine_pod()
                self.assertEqual(selected["name"] if selected else None, expected)

    def test_engine_log_matching_is_route_aware(self) -> None:
        request_id = "route-1"
        chat = f"Received request chatcmpl-{request_id}"
        completion = f"Received request cmpl-{request_id}-0"
        with tempfile.TemporaryDirectory() as directory:
            checker = AWSAcceptance(arguments(Path(directory)), runner=FakeRunner(), http=FakeHTTP([]))
            self.assertTrue(checker._log_received(chat, request_id, "/v1/chat/completions"))
            self.assertFalse(checker._log_received(chat, request_id, "/v1/completions"))
            self.assertTrue(checker._log_received(completion, request_id, "/v1/completions"))

    def test_callback_log_exact_uuid_and_record_generation(self):
        rid = "a" * 32
        generation = {"name": "engine", "uid": "pod-1", "container_id": "containerd://1", "started_at": "now"}
        with tempfile.TemporaryDirectory() as directory:
            checker = AWSAcceptance(arguments(Path(directory)), runner=FakeRunner(), http=FakeHTTP([]))
            checker._engine_log_generation = generation
            final = {"target": {"pod_uid": "pod-1", "container_id": "containerd://1"},
                     "upstream_request_id": "byul-cb-" + rid, "phase": "final", "request_id": rid}
            checker._detail = lambda _: {"final_state": {"result": "ALLOWED"}, "gateway_records": [final]}
            log = "Received request chatcmpl-byul-cb-" + rid + ": prompt omitted"
            self.assertTrue(checker._log_received(log, rid, "/v1/chat/completions"))
            final.update(phase="final", request_id=rid)
            checker._detail = lambda _: {"final_state": {"result": "ALLOWED"}, "gateway_records": [final]}
            self.assertTrue(checker._log_received(log, rid, "/v1/chat/completions"))
            checker._detail = lambda _: {"final_state": {"result": "ALLOWED"}, "gateway_records": [final, final]}
            self.assertFalse(checker._log_received(log, rid, "/v1/chat/completions"))
            checker._detail = lambda _: {"final_state": {"result": "ALLOWED"}, "gateway_records": [final]}
            self.assertFalse(checker._log_received(log, "b" * 32, "/v1/chat/completions"))
            self.assertFalse(checker._log_received(log.replace(rid, rid[:-1]), rid, "/v1/chat/completions"))
            self.assertFalse(checker._log_received(log.replace(rid, rid + "b"), rid, "/v1/chat/completions"))
            final["target"]["container_id"] = "containerd://other"
            self.assertFalse(checker._log_received(log, rid, "/v1/chat/completions"))

    def test_actual_api_detail_shape_and_closed_metadata_boundaries(self):
        document = json.loads((Path(__file__).parent / "fixtures/aws-request-detail-20261106.json").read_text())
        rid = document["request_id"]
        final = next(row for row in document["gateway_records"] if row["phase"] == "final")
        with tempfile.TemporaryDirectory() as directory:
            checker = AWSAcceptance(arguments(Path(directory)), runner=FakeRunner(), http=FakeHTTP([]))
            checker._engine_log_generation = {"uid": final["target"]["pod_uid"], "container_id": final["target"]["container_id"]}
            checker._detail = lambda _: document
            log = "Received request chatcmpl-" + final["upstream_request_id"] + ":"
            self.assertTrue(checker._log_received(log, rid, "/v1/chat/completions"))
            for malformed in [None, {}, "invalid", [], [final, final], [{**final, "request_id": "other"}], [{**final, "phase": "started"}]]:
                with self.subTest(rows=type(malformed).__name__):
                    # Even a context-looking projection cannot bypass canonical rows.
                    checker._detail = lambda _, rows=malformed: {"final_state": final, "gateway_records": rows}
                    self.assertFalse(checker._log_received(log, rid, "/v1/chat/completions"))

    def test_engine_logs_bind_one_unchanged_generation(self):
        generation = {"name": "engine-1", "uid": "pod-1", "container_id": "containerd://1", "started_at": "now"}
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner([CommandResult(0, "bounded log")])
            checker = AWSAcceptance(arguments(Path(directory)), runner=runner, http=FakeHTTP([]))
            checker._engine_pod = lambda: (generation, CommandResult(0))
            result, log = checker._engine_logs("2026-11-06T00:00:00Z")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(log, "bounded log")
            self.assertIn("pod/engine-1", runner.history[0]["argv"])
            generations = iter([generation, {**generation, "container_id": "containerd://2"}])
            checker._engine_pod = lambda: (next(generations), CommandResult(0))
            result, log = checker._engine_logs("2026-11-06T00:00:00Z")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(log, "")

    def test_all_in_pod_python_snippets_compile(self) -> None:
        for code in (TCP_PROBE_CODE, WRITE_DENY_CODE, CACHE_WRITE_CODE, ROUTE_RECORD_PROBE_CODE, COUNT_TOKENS_CODE):
            with self.subTest(code=code[:30]):
                compile(code, "<aws-acceptance-probe>", "exec")

    def test_aws_01_uses_fake_http_runner_and_requires_exact_engine_log(self) -> None:
        request_id = "normal-1"
        stream_request_id = "stream-1"
        fake_http = FakeHTTP(
            [
                response(
                    200,
                    {"choices": [{"message": {"content": "response text not retained"}}]},
                    request_id,
                ),
                response(200, detail("ALLOWED", attempted=True, outcome="completed")),
                sse_response(
                    b'data: {"choices":[{"delta":{"content":"stream body not retained"}}]}\n\ndata: [DONE]\n\n',
                    stream_request_id,
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
            checker._engine_pod = lambda: ({"name": "engine-1", "uid": "pod-1", "container_id": "containerd://1", "started_at": "now"}, CommandResult(0))
            checker._run_case("AWS-01", checker.aws_01)
            self.assertEqual(checker.cases[0]["result"], "passed")
            self.assertTrue(checker.engine_log_format_confirmed)
            command = fake_runner.history[0]["argv"]
            self.assertEqual(command[0:3], ["kubectl", "--context", "byul-aws-lab"])
            serialized = json.dumps(checker.cases, ensure_ascii=False)
            self.assertNotIn("response text not retained", serialized)
            self.assertNotIn("stream body not retained", serialized)
            self.assertNotIn(INFERENCE, serialized)

    def test_aws_01_stream_without_done_cannot_pass(self) -> None:
        fake_http = FakeHTTP(
            [
                response(200, {"choices": [{"message": {"content": "hello"}}]}, "normal-1"),
                response(200, detail("ALLOWED", attempted=True)),
                sse_response(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n', "stream-1"),
                response(200, detail("ALLOWED", attempted=True)),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            checker = AWSAcceptance(
                arguments(Path(temporary), cases=("AWS-01",)),
                runner=FakeRunner([CommandResult(0, "Received request chatcmpl-normal-1\n")]),
                http=fake_http,
            )
            checker._engine_pod = lambda: ({"name": "engine-1", "uid": "pod-1", "container_id": "containerd://1", "started_at": "now"}, CommandResult(0))
            checker._run_case("AWS-01", checker.aws_01)
            self.assertEqual(checker.cases[0]["result"], "failed")
            self.assertFalse(checker.cases[0]["facts"]["stream_ok"])

    def test_aws_01_missing_stream_record_is_unknown(self) -> None:
        fake_http = FakeHTTP(
            [
                response(200, {"choices": [{"message": {"content": "hello"}}]}, "normal-1"),
                response(200, detail("ALLOWED", attempted=True)),
                sse_response(
                    b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n',
                    "stream-1",
                ),
                response(404, {"error": {"code": "NOT_FOUND"}}),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            checker = AWSAcceptance(
                arguments(Path(temporary), cases=("AWS-01",)),
                runner=FakeRunner([CommandResult(0, "Received request chatcmpl-normal-1\n")]),
                http=fake_http,
            )
            checker._engine_pod = lambda: ({"name": "engine-1", "uid": "pod-1", "container_id": "containerd://1", "started_at": "now"}, CommandResult(0))
            checker._run_case("AWS-01", checker.aws_01)
            case = checker.cases[0]
            self.assertEqual(case["result"], "unknown")
            self.assertIsNone(case["facts"]["stream_ok"])
            self.assertEqual(case["reason"], "판정 근거 부족: stream_ok")

    def test_aws_01_done_with_failed_final_record_is_not_stream_success(self) -> None:
        failed = detail("ERROR", reason="ENGINE_BAD_RESPONSE", attempted=True,
                        outcome="invalid_completion_stream")
        failed["final_state"]["delivery"].update(
            response="failed", error_code="ENGINE_BAD_RESPONSE")
        fake_http = FakeHTTP([
            response(200, {"choices": [{"message": {"content": "hello"}}]}, "normal-1"),
            response(200, detail("ALLOWED", attempted=True, outcome="completed")),
            sse_response(b'data: {"choices":[{}]}\n\ndata: [DONE]\n\n', "stream-1"),
            response(200, failed),
        ])
        with tempfile.TemporaryDirectory() as temporary:
            checker = AWSAcceptance(arguments(Path(temporary), cases=("AWS-01",)),
                                    runner=FakeRunner(), http=fake_http)
            facts, _ = checker.aws_01()
        self.assertFalse(facts["stream_ok"])

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
            checker._engine_pod = lambda: ({"name": "engine-1", "uid": "pod-1", "container_id": "containerd://1", "started_at": "now"}, CommandResult(0))
            checker._run_case("AWS-02", checker.aws_02)
            self.assertEqual(checker.cases[0]["result"], "passed")
            serialized = json.dumps(checker.cases)
            self.assertNotIn(checker.wrong_token, serialized)

    def test_aws_03_reads_limit_from_policy_api(self) -> None:
        fake_http = FakeHTTP(
            [
                response(200, {"current": {"output_max_tokens": 128}}),
                response(
                    200,
                    {"choices": [{"message": {"content": "boundary response"}}]},
                    "boundary-1",
                ),
                response(200, detail("ALLOWED", attempted=True)),
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
                runner=FakeRunner([CommandResult(0, "Received request chatcmpl-boundary-1\n")]),
                http=fake_http,
            )
            checker.engine_log_format_confirmed = True
            checker._engine_pod = lambda: ({"name": "engine-1", "uid": "pod-1", "container_id": "containerd://1", "started_at": "now"}, CommandResult(0))
            checker._run_case("AWS-03", checker.aws_03)
            self.assertEqual(checker.cases[0]["result"], "passed")
            self.assertEqual(checker.cases[0]["evidence"]["policy_limit"], 128)
            completion_payloads = [call["json"] for call in fake_http.calls if call["has_body"]]
            self.assertEqual([item["max_tokens"] for item in completion_payloads], [128, 129])
        self.assertEqual(_extract_policy_limit({"policy": {"output_max_tokens": 7}}), 7)
        self.assertIsNone(_extract_policy_limit({"current": {}}))

    def test_aws_03_requires_positive_log_format_and_denied_id_absence(self) -> None:
        def run(logs: str, confirmed: bool) -> str:
            http = FakeHTTP(
                [
                    response(200, {"current": {"output_max_tokens": 8}}),
                    response(200, {"choices": [{"message": {"content": "ok"}}]}, "boundary"),
                    response(200, detail("ALLOWED", attempted=True)),
                    response(403, {"error": {"code": "MAX_TOKENS_EXCEEDED"}}, "denied"),
                    response(200, detail("DENIED", reason="MAX_TOKENS_EXCEEDED", attempted=False, status=403)),
                ]
            )
            with tempfile.TemporaryDirectory() as temporary:
                checker = AWSAcceptance(
                    arguments(Path(temporary), cases=("AWS-03",)),
                    runner=FakeRunner([CommandResult(0, logs)]),
                    http=http,
                )
                checker.engine_log_format_confirmed = confirmed
                checker._engine_pod = lambda: ({"name": "engine-1", "uid": "pod-1", "container_id": "containerd://1", "started_at": "now"}, CommandResult(0))
                checker._run_case("AWS-03", checker.aws_03)
                return checker.cases[0]["result"]

        self.assertEqual(run("Received request chatcmpl-boundary\n", False), "unknown")
        self.assertEqual(
            run("Received request chatcmpl-denied\n", True),
            "failed",
        )

    def test_aws_04_requires_timeout_dropped_flow_and_gateway_tcp_control(self) -> None:
        runner = FakeRunner(
            [
                CommandResult(1, "CONNECTED\n"),
                CommandResult(0, "CONNECT_FAILED errno=110 name=ETIMEDOUT\n"),
                CommandResult(0, "CONNECT_FAILED errno=110 name=ETIMEDOUT\n"),
                CommandResult(1, "CONNECTED\n"),
            ]
        )
        http = FakeHTTP(
            [
                response(200, {"choices": [{"message": {"content": "ok"}}]}, "control"),
                response(200, detail("ALLOWED", attempted=True)),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            observation = root / "observation"
            observation.mkdir()
            (observation / "hubble.jsonl").write_text(
                json.dumps(
                    {
                        "flow": {
                            "time": "9999-01-01T00:00:00+00:00",
                            "verdict": "DROPPED",
                            "source": {"pod_name": "other-client"},
                            "destination": {"pod_name": "serving-engine-abc"},
                            "IP": {"destination": "10.0.0.10"},
                            "l4": {"TCP": {"destination_port": 8000}},
                        }
                    }
                ) + "\n",
                encoding="utf-8",
            )
            checker = AWSAcceptance(
                arguments(root, cases=("AWS-04",)), runner=runner, http=http
            )
            checker._run_case("AWS-04", checker.aws_04)
            case = checker.cases[0]
            self.assertEqual(case["result"], "passed")
            self.assertEqual(case["evidence"]["direct_probe"]["errno_name"], "ETIMEDOUT")
            self.assertTrue(case["facts"]["gateway_tcp_control"])

    def test_aws_04_refused_is_unknown_not_policy_block(self) -> None:
        runner = FakeRunner(
            [
                CommandResult(1, "CONNECTED\n"),
                CommandResult(0, "CONNECT_FAILED errno=111 name=ECONNREFUSED\n"),
                CommandResult(0, "CONNECT_FAILED errno=110 name=ETIMEDOUT\n"),
                CommandResult(1, "CONNECTED\n"),
            ]
        )
        http = FakeHTTP(
            [
                response(200, {"choices": [{"message": {"content": "ok"}}]}, "control"),
                response(200, detail("ALLOWED", attempted=True)),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "observation").mkdir()
            checker = AWSAcceptance(arguments(root, cases=("AWS-04",)), runner=runner, http=http)
            checker._wait_observation_refs = lambda *_args, **_kwargs: (True, [])
            checker._run_case("AWS-04", checker.aws_04)
            self.assertEqual(checker.cases[0]["result"], "unknown")
            self.assertIn("ECONNREFUSED", checker.cases[0]["reason"])

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
                CommandResult(0, "CONNECT_FAILED errno=110 name=ETIMEDOUT\n"),
                CommandResult(1, "CONNECTED\n"),
                CommandResult(0, "CONNECT_FAILED errno=110 name=ETIMEDOUT\n"),
                CommandResult(0, "CONNECT_FAILED errno=110 name=ETIMEDOUT\n"),
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

    def test_aws_05_refused_is_unknown_not_policy_block(self) -> None:
        engine = {
            "items": [{
                "metadata": {"name": "serving-engine-abc", "uid": "uid"},
                "status": {
                    "containerStatuses": [{"name": "engine", "containerID": "containerd://id"}],
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            }]
        }
        receiver = {
            "metadata": {"name": "test-receiver", "labels": {"app": "test-receiver"}},
            "status": {"podIP": "10.0.0.42", "conditions": [{"type": "Ready", "status": "True"}]},
        }
        runner = FakeRunner(
            [
                CommandResult(0, json.dumps(engine)),
                CommandResult(0, json.dumps(receiver)),
                CommandResult(0, "CONNECT_FAILED errno=111 name=ECONNREFUSED\n"),
                CommandResult(1, "CONNECTED\n"),
                CommandResult(0, "CONNECT_FAILED errno=110 name=ETIMEDOUT\n"),
                CommandResult(0, "CONNECT_FAILED errno=110 name=ETIMEDOUT\n"),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "observation").mkdir()
            checker = AWSAcceptance(
                arguments(root, cases=("AWS-05",)),
                runner=runner,
                http=FakeHTTP([]),
            )
            checker._wait_observation_refs = lambda *_args, **_kwargs: (True, [])
            checker._run_case("AWS-05", checker.aws_05)
            self.assertEqual(checker.cases[0]["result"], "unknown")
            self.assertIn("ECONNREFUSED", checker.cases[0]["reason"])

    def test_gateway_policy_status_exposes_the_numeric_limit_needed_by_aws_03(self) -> None:
        policy = SimpleNamespace(
            profile="default",
            output_max_tokens=128,
            max_context_tokens=2048,
            max_n=1,
            extra_fields=(),
        )
        self.assertEqual(GatewayApp._profile_status(policy)["output_max_tokens"], 128)
        self.assertEqual(GatewayApp._profile_status(policy)["max_context_tokens"], 2048)
        self.assertIsNone(GatewayApp._profile_status(None)["max_context_tokens"])
        self.assertIsNone(GatewayApp._profile_status(None)["output_max_tokens"])

    def test_aws_06_only_erofs_proves_read_only_mount(self) -> None:
        engine = json.dumps(
            {
                "items": [{
                    "metadata": {"name": "serving-engine-abc", "uid": "uid"},
                    "status": {
                        "containerStatuses": [{"name": "engine", "containerID": "containerd://id"}],
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                }]
            }
        )
        for error_number, error_name, expected in (
            (30, "EROFS", "passed"),
            (13, "EACCES", "unknown"),
            (1, "EPERM", "unknown"),
        ):
            with self.subTest(errno=error_name), tempfile.TemporaryDirectory() as temporary:
                checker = AWSAcceptance(
                    arguments(Path(temporary), cases=("AWS-06",)),
                    runner=FakeRunner(
                        [
                            CommandResult(0, engine),
                            CommandResult(0, f"WRITE_DENIED errno={error_number} name={error_name}\n"),
                            CommandResult(0, "CACHE_WRITE_REMOVED\n"),
                        ]
                    ),
                    http=FakeHTTP([]),
                )
                checker._wait_observation_refs = lambda *_args, **_kwargs: (
                    True,
                    [{"reference": "fixture", "rule_id": "TG-CACHE-01"}],
                )
                checker._run_case("AWS-06", checker.aws_06)
                case = checker.cases[0]
                self.assertEqual(case["result"], expected)
                self.assertEqual(case["evidence"]["model_probe"]["marker"], "WRITE_DENIED")
                self.assertEqual(case["evidence"]["model_probe"]["errno_name"], error_name)

    def test_aws_07_clean_stop_uses_stopped_heartbeats_without_gap_files(self) -> None:
        before = {
            "collectors": [
                {"source": source, "state": "ok", "stale_after_s": 2.0}
                for source in ("observation_feed_hubble", "observation_feed_tetragon")
            ] + [{"source": "target_pods", "state": "ok", "stale_after_s": 15.0}]
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
            clock = FakeClock()
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
                        response(200, {"verification": {"state": "unavailable", "last_confirmed_at": "2026-10-26T00:00:00+00:00"}, "gateway_hold": None}),
                        response(200, {"choices": [{"message": {"content": "during stop"}}]}, "stopped-request"),
                        response(200, detail("ALLOWED", attempted=True)),
                        response(200, {"verification": {"state": "unavailable", "last_confirmed_at": "2026-10-26T00:00:00+00:00"}, "gateway_hold": None}),
                        response(200, recovered),
                        response(200, {"verification": {"state": "ok", "last_confirmed_at": "2026-10-26T00:00:20+00:00"}, "gateway_hold": None}),
                        response(200, {"choices": [{"message": {"content": "recovered"}}]}, "recovered-request"),
                        response(200, detail("ALLOWED", attempted=True)),
                    ]
                ), sleeper=clock.sleep, monotonic=clock.monotonic,
            )
            checker._run_case("AWS-07", checker.aws_07)
            case = checker.cases[0]
            self.assertEqual(case["result"], "passed")
            self.assertTrue(case["facts"]["heartbeat_stopped_seen"])
            self.assertIsNone(case["facts"]["supplementary_gap_recorded"])
            self.assertFalse(list(observation.glob("*.gaps.jsonl")))
            self.assertEqual(case["evidence"]["timings"]["stale_after_s"], 15.0)
            self.assertGreaterEqual(clock.value, 17.0)
            self.assertEqual(
                case["evidence"]["stopped_interval_request"],
                {"http_status": 200, "reason_code": None, "error": None},
            )
            self.assertTrue(case["facts"]["stopped_request_200_unverified"])
            self.assertTrue(case["facts"]["target_recovered"])
            self.assertTrue(case["facts"]["inference_recovered"])

            failed_clock = FakeClock()
            failed = AWSAcceptance(
                arguments(root, cases=("AWS-07",), allow_collector_stop=True,
                    evidence_dir=str(root / "failed-evidence")),
                runner=FakeRunner([CommandResult(0), CommandResult(0)]),
                http=FakeHTTP([
                    response(200, before), response(200, unavailable),
                    response(200, {"verification": {"state": "blocked", "last_confirmed_at": None}, "gateway_hold": {"reason_code": "TARGET_UNAVAILABLE"}}),
                    response(200, recovered),
                    response(200, {"verification": {"state": "ok", "last_confirmed_at": None}, "gateway_hold": None}),
                    response(200, {"choices": [{"message": {"content": "recovered"}}]}, "recovered-request"),
                    response(200, detail("ALLOWED", attempted=True)),
                ]), sleeper=failed_clock.sleep, monotonic=failed_clock.monotonic,
            )
            failed._run_case("AWS-07", failed.aws_07)
            self.assertEqual(failed.cases[0]["result"], "failed")
            self.assertFalse(failed.cases[0]["facts"]["stopped_request_200_unverified"])
            self.assertEqual(failed.cases[0]["evidence"]["request_target_verification"]["state"], "blocked")
            self.assertIsNone(failed.cases[0]["evidence"]["stopped_interval_request"]["http_status"])

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
                response(
                    200,
                    {"choices": [{"message": {"content": "hello"}}]},
                    "recovery-request",
                ),
                response(200, detail("ALLOWED", attempted=True)),
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
        self.assertEqual(
            [call["path"] for call in http.calls],
            ["/api/target", "/api/target", "/api/collectors", "/v1/chat/completions", "/api/requests/recovery-request"],
        )

    def test_aws_08_retries_temporary_hold_but_reports_final_failure(self) -> None:
        def engine_pod(uid: str) -> str:
            return json.dumps(
                {
                    "items": [{
                        "metadata": {"name": f"engine-{uid}", "uid": uid},
                        "status": {
                            "containerStatuses": [{"name": "engine", "containerID": f"containerd://{uid}"}],
                            "conditions": [{"type": "Ready", "status": "True"}],
                        },
                    }]
                }
            )

        before_target = {
            "instance": {"state": "ok", "current": {"instance_id": "old"}},
            "history": [],
            "kubernetes": {"state": "ok"},
        }
        after_target = {
            "instance": {"state": "ok", "current": {"instance_id": "new"}},
            "history": [{
                "event": "instance_changed",
                "reason": "pod_replaced",
                "previous_instance_id": "old",
                "instance": {"instance_id": "new"},
            }],
            "kubernetes": {"state": "ok"},
        }
        collectors = {
            "collectors": [{
                "source": "engine_info",
                "state": "ok",
                "last_success_at": "9999-01-01T00:00:00+00:00",
            }]
        }

        def run(recovery_responses: list[HTTPResponse], clock: FakeClock) -> dict[str, object]:
            runner = FakeRunner(
                [
                    CommandResult(0, engine_pod("old-uid")),
                    CommandResult(0),
                    CommandResult(0),
                    CommandResult(0, engine_pod("new-uid")),
                ]
            )
            http = FakeHTTP(
                [response(200, before_target), response(200, after_target), response(200, collectors), *recovery_responses]
            )
            with tempfile.TemporaryDirectory() as temporary:
                checker = AWSAcceptance(
                    arguments(Path(temporary), cases=("AWS-08",), allow_engine_restart=True),
                    runner=runner,
                    http=http,
                    monotonic=clock.monotonic,
                    sleeper=clock.sleep,
                )
                checker._run_case("AWS-08", checker.aws_08)
                return checker.cases[0]

        transient_clock = FakeClock()
        transient = run(
            [
                response(503, {"error": {"code": "TARGET_UNVERIFIED"}}, "hold"),
                response(200, detail("HELD", reason="TARGET_UNVERIFIED", attempted=False, status=503)),
                response(200, {"choices": [{"message": {"content": "ok"}}]}, "ok"),
                response(200, detail("ALLOWED", attempted=True)),
            ],
            transient_clock,
        )
        self.assertEqual(transient["result"], "passed")
        self.assertEqual(transient["evidence"]["recovery_request"]["attempts"], 2)

        failure_clock = FakeClock(sleep_jump=89.0)
        failed = run(
            [
                response(503, {"error": {"code": "TARGET_UNVERIFIED"}}, "hold-1"),
                response(200, detail("HELD", reason="TARGET_UNVERIFIED", attempted=False, status=503)),
                response(503, {"error": {"code": "TARGET_UNAVAILABLE"}}, "hold-2"),
                response(200, detail("HELD", reason="TARGET_UNAVAILABLE", attempted=False, status=503)),
            ],
            failure_clock,
        )
        self.assertEqual(failed["result"], "failed")
        self.assertEqual(failed["evidence"]["recovery_request"]["attempts"], 2)
        self.assertEqual(failed["evidence"]["recovery_request"]["last_status"], 503)
        self.assertEqual(
            failed["evidence"]["recovery_request"]["last_reason_code"],
            "TARGET_UNAVAILABLE",
        )


class EvidenceTests(unittest.TestCase):
    @staticmethod
    def _case(case_id: str, evidence: dict, facts: dict | None = None) -> dict:
        facts = dict(DecisionTests.PASS_FACTS[case_id] if facts is None else facts)
        result, reason = evaluate_case(case_id, facts)
        return {
            "case_id": case_id, "title": case_id, "rt_ids": [],
            "result": result, "reason": reason, "facts": facts, "evidence": evidence,
        }

    def test_public_report_preserves_real_run_and_excludes_raw_details(self):
        from serving_runtime.mvp.lab_report import LabReportSource, AWS_REPORT_SCHEMA
        from tools import verification_material
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            public.mkdir(mode=0o2750)
            public.chmod(0o2750)
            checker = AWSAcceptance(arguments(root, cases=("AWS-06",), public_report_dir=public), runner=FakeRunner(), http=FakeHTTP([]))
            checker.run_started_at = "2020-01-01T00:00:00Z"
            checker.cases.append(self._case("AWS-06", {"raw": "private-command-output"}))
            link = os.link
            def observed_link(source, target):
                self.assertEqual(LabReportSource(public).read()["state"], "missing")
                self.assertEqual(stat.S_IMODE(Path(source).stat().st_mode), 0o640)
                return link(source, target)
            with patch.object(verification_material.os, "link", side_effect=observed_link):
                private, markdown = checker.write_report()
            raw = json.loads(private.read_text())
            result = LabReportSource(public).read()
            self.assertEqual(result["state"], "ok")
            view = result["report"]
            self.assertEqual(view["schema"], AWS_REPORT_SCHEMA)
            for key in ("run_id", "started_at", "namespace", "overall"):
                self.assertEqual(view[key], raw[key])
            self.assertEqual(view["context"], raw["kube_context"])
            self.assertEqual(view["finished_at"], raw["ended_at"])
            self.assertEqual([c["id"] for c in view["cases"]], ["AWS-06"])
            self.assertIn("private-command-output", private.read_text())
            published = Path(result["path"])
            for secret in (INFERENCE, VIEWER, "private-command-output", "gateway_url", "observation_dir", "facts", "commands"):
                self.assertNotIn(secret, published.read_text())
            self.assertEqual(stat.S_IMODE(published.stat().st_mode), 0o640)
            self.assertEqual(published.stat().st_gid, public.stat().st_gid)
            self.assertEqual(stat.S_IMODE(public.stat().st_mode), 0o2750)
            for path in (private, markdown):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(public.glob(".verification-*")), [])

    def test_public_report_preserves_all_result_states_and_schema_case_families(self):
        from tools.aws_lab_acceptance import publish_report
        from serving_runtime.mvp.lab_report import LabReportSource, REPORT_SCHEMA
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = {"run_id": "aws-acceptance-20200101T000000Z-1", "started_at": "2020-01-01T00:00:00Z",
                      "ended_at": "2020-01-01T00:01:00Z", "kube_context": "private-192.0.2.99", "namespace": "serving-lab", "engine": "vllm",
                      "cases": [{"case_id": f"AWS-{i:02d}", "result": result, "reason": INFERENCE}
                                for i, result in enumerate(("passed", "failed", "unknown", "not_run"), 1)]}
            path = publish_report(report, root, (INFERENCE,))
            value = LabReportSource(root).read()["report"]
            self.assertEqual(value["overall"], "failed")
            self.assertEqual([c["status"] for c in value["cases"]], ["passed", "failed", "unknown", "not_run"])
            self.assertTrue(value["context"].startswith("environment-"))
            self.assertNotIn("192.0.2.99", path.read_text())
            for malformed in (dict(value, schema=REPORT_SCHEMA), dict(value, cases=value["cases"] * 2), dict(value, cases=[]), dict(value, schema=[])):
                with self.assertRaises(ValueError):
                    LabReportSource._validate(malformed)
            invalid_status = dict(value, cases=[dict(value["cases"][0], status=[])])
            with self.assertRaises(ValueError):
                LabReportSource._validate(invalid_status)
            value["cases"][0]["id"] = "L-01"
            with self.assertRaises(ValueError):
                LabReportSource._validate(value)

    def test_run_publication_keeps_failure_and_unknown_exit_codes(self):
        from serving_runtime.mvp.lab_report import LabReportSource
        for status, expected in (("failed", 1), ("unknown", 3), ("not_run", 3), ("passed", 0)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                public = root / "public"
                public.mkdir(mode=0o2750)
                args = arguments(root, cases=("AWS-06",), public_report_dir=public)
                checker = AWSAcceptance(args, runner=FakeRunner(), http=FakeHTTP([]))
                case = self._case("AWS-06", {})
                case["result"] = status
                with patch.object(checker, "_run_case", side_effect=lambda *args: checker.cases.append(case)), redirect_stdout(io.StringIO()):
                    self.assertEqual(checker.run(), expected)
                self.assertEqual(LabReportSource(public).read()["report"]["cases"][0]["status"], status)

    def test_publication_refuses_unsafe_destination_and_keeps_private_evidence_on_failure(self):
        from tools.aws_lab_acceptance import ConfigurationError
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = AWSAcceptance(arguments(root, cases=("AWS-06",), public_report_dir=root / "missing"), runner=FakeRunner(), http=FakeHTTP([]))
            checker.cases.append(self._case("AWS-06", {}))
            with self.assertRaisesRegex(ConfigurationError, "PUBLIC_REPORT_WRITE_FAILED"):
                checker.write_report()
            self.assertTrue((checker.evidence_dir / "report.json").is_file())
            self.assertTrue((checker.evidence_dir / "report.md").is_file())
            public = root / "public"
            public.mkdir(mode=0o2750)
            checker.args.public_report_dir = public
            checker.write_report()
            path = next(public.glob("*.json"))
            before = path.read_bytes()
            with self.assertRaisesRegex(ConfigurationError, "PUBLIC_REPORT_WRITE_FAILED"):
                checker.write_report()
            self.assertEqual(path.read_bytes(), before)
            link = root / "link"
            link.symlink_to(public, target_is_directory=True)
            checker.args.public_report_dir = link
            with self.assertRaisesRegex(ConfigurationError, "PUBLIC_REPORT_WRITE_FAILED"):
                checker.write_report()
            public.chmod(0o2770)
            checker.args.public_report_dir = public
            with self.assertRaisesRegex(ConfigurationError, "PUBLIC_REPORT_WRITE_FAILED"):
                checker.write_report()

    def test_markdown_uses_korean_sections_and_only_selected_case_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checker = AWSAcceptance(arguments(Path(temporary), cases=("AWS-06",)), runner=FakeRunner(), http=FakeHTTP([]))
            checker.cases.append(self._case("AWS-06", {
                "model_probe": {"errno_name": "EROFS"},
                "cache_probe": {"marker": "CACHE_WRITE_REMOVED"},
            }))
            json_path, md_path = checker.write_report()
            md = md_path.read_text(encoding="utf-8")
            report = json.loads(json_path.read_text(encoding="utf-8"))
            for heading in ("# AWS 런타임 인수 시험 보고서", "## 시험 결과", "## 판정 기준", "## 아는 한계"):
                self.assertIn(heading, md)
            self.assertIn("통과가 기능 완성을 뜻하지 않는다", md)
            self.assertIn("/models 쓰기 EROFS", md)
            self.assertIn("/cache 쓰기·삭제 CACHE_WRITE_REMOVED", md)
            self.assertIn("TG-CACHE-01 기록 예", md)
            for excluded in ("AWS-04", "AWS-05", "AWS-07", "Decision notes", "Known gaps"):
                self.assertNotIn(excluded, md)
            self.assertEqual(report["schema"], "runtime-aws-acceptance/1.0")
            self.assertEqual(len(report["decision_notes"]), 3)
            self.assertEqual(len(report["known_gaps"]), 2)
            self.assertEqual(report["requested_cases"], ["AWS-06"])

    def test_each_case_displays_collected_values_without_copying_sensitive_text(self) -> None:
        examples = {
            "AWS-01": ({"http_codes": [200], "stream": {"http_status": 200, "sse_data_received": True, "done_received": True}}, "일반 HTTP 200, stream HTTP 200"),
            "AWS-02": ({"attempts": [{"kind": "missing", "status": 401, "error_code": "AUTH_REQUIRED"}, {"kind": "invalid", "status": 401, "error_code": "AUTH_INVALID"}]}, "missing HTTP 401/AUTH_REQUIRED, invalid HTTP 401/AUTH_INVALID"),
            "AWS-03": ({"policy_limit": 256, "boundary": {"http_status": 200}}, "상한 256토큰, 상한 요청 HTTP 200"),
            "AWS-04": ({"direct_probe": {"errno_name": "EAGAIN"}}, "직통 연결 EAGAIN"),
            "AWS-05": ({"engine_receiver_probe": {"errno_name": "ETIMEDOUT"}}, "engine→receiver 연결 ETIMEDOUT"),
            "AWS-06": ({"model_probe": {"errno_name": "EROFS"}, "cache_probe": {"marker": "CACHE_WRITE_REMOVED"}}, "/models 쓰기 EROFS"),
            "AWS-07": ({"stopped_interval_request": {"http_status": 200}, "stopped_target_verification": {"state": "unavailable"}, "recovered_target_verification": {"state": "ok"}, "recovery_request": {"last_status": 200}}, "중단 중 요청 HTTP 200, 대상 상태 unavailable; 복구 대상 ok, 요청 HTTP 200"),
            "AWS-08": ({"engine_info": {"state": "ok"}, "recovery_request": {"last_status": 200}}, "engine_info ok, 복구 요청 HTTP 200"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            checker = AWSAcceptance(arguments(Path(temporary)), runner=FakeRunner(), http=FakeHTTP([]))
            for case_id, (evidence, _expected) in examples.items():
                evidence["unrelated_text"] = "private-prompt-and-response-body"
                checker.cases.append(self._case(case_id, evidence))
            md = checker.write_report()[1].read_text(encoding="utf-8")
            for case_id, (_evidence, expected) in examples.items():
                with self.subTest(case_id=case_id):
                    self.assertIn(expected, md)
            self.assertNotIn("private-prompt-and-response-body", md)
            self.assertNotIn(INFERENCE, md)
            self.assertNotIn(VIEWER, md)

    def test_missing_values_and_nonpassing_required_facts_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checker = AWSAcceptance(arguments(Path(temporary)), runner=FakeRunner(), http=FakeHTTP([]))
            failed = dict(DecisionTests.PASS_FACTS["AWS-07"])
            failed["stopped_request_200_unverified"] = False
            unknown = dict(DecisionTests.PASS_FACTS["AWS-06"])
            unknown["cache_tetragon"] = None
            auth_failed = dict(DecisionTests.PASS_FACTS["AWS-02"])
            auth_failed["auth_codes"] = False
            checker.cases.extend((
                self._case("AWS-07", {"stopped_interval_request": {"http_status": 503}}, failed),
                self._case("AWS-06", {}, unknown),
                self._case("AWS-08", {}, {"not_run_reason": "--allow-engine-restart 없음"}),
                self._case("AWS-02", {"attempts": [{"kind": "missing", "status": 403, "error_code": "OTHER_CODE"}]}, auth_failed),
            ))
            md = checker.write_report()[1].read_text(encoding="utf-8")
            self.assertIn("중단 중 요청 HTTP 503, 대상 상태 확인 못 함", md)
            self.assertIn("stopped_request_200_unverified=아니요", md)
            self.assertIn("cache_tetragon=확인 못 함", md)
            self.assertIn("restart_ok=확인 못 함", md)
            self.assertIn("/models 쓰기 확인 못 함", md)
            self.assertIn("missing HTTP 403/OTHER_CODE", md)
            self.assertIn("auth_codes=아니요", md)

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


class NewCaseExecutionTests(unittest.TestCase):
    def test_delayed_forward_is_kept_alive_until_ready(self) -> None:
        class Forward:
            closed = False
            def poll(self): return 1 if self.closed else None
            def terminate(self): self.closed = True
            def wait(self, timeout=None): return 0

        class DelayedRunner(FakeRunner):
            def __init__(self):
                super().__init__()
                self.forwards = []
            def start_port_forward(self, argv):
                forward = Forward()
                self.forwards.append(forward)
                return forward

        class DelayedHTTP:
            def __init__(self, runner, clock):
                self.runner, self.clock = runner, clock
                self.fail = False
            def request(self, method, url, **kwargs):
                if self.fail or not self.runner.forwards or self.clock.value < 0.3:
                    raise URLError("forward is starting")
                return response(200, {})

        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock()
            runner = DelayedRunner()
            http = DelayedHTTP(runner, clock)
            checker = AWSAcceptance(arguments(Path(directory)), runner=runner,
                                    http=http,
                                    sleeper=clock.sleep, monotonic=clock.monotonic)
            checker._restart_pending = True
            checker._port_accepting = lambda: clock.value >= 0.3
            self.assertEqual(checker._request("GET", "/health", token=None).status, 200)
            self.assertEqual(len(runner.forwards), 1)
            self.assertFalse(runner.forwards[0].closed)
            http.fail = True
            with self.assertRaises(URLError):
                checker._request("GET", "/health", token=None)
            self.assertEqual(len(runner.forwards), 1)
            self.assertFalse(runner.forwards[0].closed)
            checker._close_forward()

    def test_forward_readiness_wait_has_upper_bound(self) -> None:
        class Forward:
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): return 0
        class ForwardRunner(FakeRunner):
            def __init__(self):
                super().__init__()
                self.opens = 0
            def start_port_forward(self, argv):
                self.opens += 1
                return Forward()
        class UnreadyHTTP:
            def request(self, method, url, **kwargs):
                raise URLError("not ready")
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock()
            runner = ForwardRunner()
            checker = AWSAcceptance(arguments(Path(directory)), runner=runner, http=UnreadyHTTP(),
                                    sleeper=clock.sleep, monotonic=clock.monotonic)
            checker._restart_pending = True
            checker._port_accepting = lambda: False
            with self.assertRaises(URLError):
                checker._request("GET", "/health", token=None, timeout=30)
            self.assertEqual(runner.opens, 1)
            self.assertLessEqual(clock.value, 2.1)

    def test_restart_reconnect_misses_503_is_unknown(self) -> None:
        class Forward:
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): return 0

        class ForwardRunner(FakeRunner):
            def start_port_forward(self, argv):
                return Forward()

        class HTTP:
            def __init__(self): self.calls = 0
            def request(self, method, url, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise URLError("old forward died")
                return response(200, {"choices": [{"message": {"content": "ok"}}]})

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = FakeClock()
            checker = AWSAcceptance(arguments(root, cases=("AWS-14",), allow_gateway_restart=True), runner=ForwardRunner(), http=HTTP(), sleeper=clock.sleep, monotonic=clock.monotonic)
            count = []
            def kubectl(*argv, **kwargs):
                if "get" in argv:
                    count.append(1)
                    return CommandResult(0, json.dumps({"items": [{"metadata": {"uid": "old" if len(count) == 1 else "new"}}]}))
                return CommandResult(0)
            checker._kubectl = kubectl
            facts, evidence = checker.aws_14()
            self.assertEqual(evaluate_case("AWS-14", facts)[0], "unknown")
            self.assertEqual(evidence["statuses"], [200])
            self.assertIsNone(facts["within_10s"])
            self.assertTrue(evidence["reconnect_at"])
            checker._close_forward()

    def test_nonloopback_restart_does_not_open_forward(self) -> None:
        class RemoteRunner(FakeRunner):
            def start_port_forward(self, argv):
                raise AssertionError("remote gateway must not open a local forward")

        with tempfile.TemporaryDirectory() as directory:
            checker = AWSAcceptance(arguments(Path(directory), gateway_url="http://gateway.example:8080"), runner=RemoteRunner(), http=FakeHTTP([]))
            checker._gateway_restart_started()
            self.assertFalse(checker._restart_pending)
            with self.assertRaises(AssertionError):
                checker._request("GET", "/health", token=None)

    def test_bad_bundle_restarts_without_waiting_for_bad_pod_connectivity(self) -> None:
        class RestartRunner(FakeRunner):
            def __init__(self):
                super().__init__()
                self.restarts = 0
                self.forwards = 0

            def run(self, argv, *, timeout=30, input=None):
                command = list(argv)
                self.history.append({"argv": command, "returncode": 0})
                if command[-3:] == ["rollout", "restart", "deployment/serving-gateway"]:
                    self.restarts += 1
                if "rollout" in command and "status" in command and self.restarts == 1:
                    return CommandResult(1)
                if "logs" in command:
                    return CommandResult(0, "SHA-256 mismatch")
                return CommandResult(0)

            def start_port_forward(self, argv):
                self.forwards += 1
                class Forward:
                    def poll(self): return None
                    def terminate(self): pass
                    def wait(self, timeout=None): return 0
                return Forward()

        class HTTP:
            def __init__(self, runner):
                self.runner = runner
                self.responses = [response(200, {"choices": [{"message": {"content": "ok"}}]}), response(401, {})]

            def request(self, method, url, **kwargs):
                if self.runner.forwards == 0:
                    raise URLError("old forward died")
                return self.responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundles = root / "bundles"
            previous = bundles / "previous"
            previous.mkdir(parents=True)
            (previous / "policy.json").write_bytes(b"{}")
            (previous / "bundle.json").write_text(json.dumps({"files": [{"role": "gateway_policy", "path": "policy.json"}]}))
            (bundles / "current").symlink_to("previous")
            runner = RestartRunner()
            checker = AWSAcceptance(arguments(root, cases=("AWS-13",), allow_bundle_switch=True, bundle_root=str(bundles)), runner=runner, http=HTTP(runner))
            checker._gateway_pods = lambda: [{"metadata": {"uid": "old"}}]
            checker._gateway_crashed = lambda _window, _uids: (True, {"restart_count": 1, "pod_name": "gateway-new", "pod_uid": "new"})
            checker._retry_inference = lambda _timeout: (checker._completion(token=INFERENCE)[0].status == 200, {"result": "ALLOWED"}, {"last_status": 200})
            checker._detail = lambda _id: detail("DENIED", reason="AUTH_REQUIRED", attempted=False, status=401)
            self.assertEqual(checker.run(), 0)
            self.assertEqual(runner.restarts, 2)
            self.assertEqual(runner.forwards, 1)

    def test_collector_restart_rebinds_forward_before_503(self) -> None:
        class RunnerWithForward(FakeRunner):
            def __init__(self):
                super().__init__()
                self.restarted = False
                self.forward = None

            def run(self, argv, *, timeout=30):
                if list(argv)[-3:] == ["rollout", "restart", "deployment/serving-gateway"]:
                    self.restarted = True
                return super().run(argv, timeout=timeout)

            def start_port_forward(self, argv):
                class Forward:
                    closed = False
                    def poll(self): return 1 if self.closed else None
                    def terminate(self): self.closed = True
                    def wait(self, timeout=None): return 0
                self.forward = Forward()
                return self.forward

        class HTTP:
            def __init__(self, runner):
                self.runner = runner
                self.responses = [response(503, {"error": {"code": "TARGET_UNAVAILABLE"}}), response(200, {"choices": [{"message": {"content": "ok"}}]})]

            def request(self, method, url, **kwargs):
                if self.runner.restarted and (self.runner.forward is None or self.runner.forward.closed):
                    raise URLError("forward died on restart")
                return self.responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = RunnerWithForward()
            checker = AWSAcceptance(arguments(root, cases=("AWS-15",), allow_collector_stop=True, allow_gateway_restart=True), runner=runner, http=HTTP(runner))
            checker._wait_collectors_ok = lambda _timeout: (True, {"observation_feed_hubble": {"state": "ok"}})
            checker._retry_inference = lambda _timeout: (checker._completion(token=INFERENCE)[0].status == 200, None, {"last_status": 200})
            self.assertEqual(checker.run(), 0)
            self.assertEqual(checker.cases[0]["evidence"]["stopped_request_statuses"], [503])
            self.assertTrue(checker.cases[0]["evidence"]["reconnect_at"])
            self.assertTrue(runner.forward.closed)

    def test_restart_rebinds_dead_user_forward_and_cleans_owned_forward(self) -> None:
        class Forward:
            def __init__(self, runner):
                self.runner = runner
                self.closed = False

            def poll(self):
                return 1 if self.closed else None

            def terminate(self):
                self.closed = True

            def wait(self, timeout=None):
                return 0

        class RestartRunner(FakeRunner):
            def __init__(self):
                super().__init__()
                self.user_forward_live = True
                self.owned = []

            def run(self, argv, *, timeout=30):
                if list(argv)[-3:] == ["rollout", "restart", "deployment/serving-gateway"]:
                    self.user_forward_live = False
                return super().run(argv, timeout=timeout)

            def start_port_forward(self, argv):
                self.asserted_argv = list(argv)
                forward = Forward(self)
                if not self.owned:
                    forward.closed = True  # old user forward still owns the port
                self.owned.append(forward)
                return forward

        class RestartHTTP:
            def __init__(self, runner):
                self.runner = runner
                self.responses = [response(503, {"error": {"code": "TARGET_UNAVAILABLE"}}), response(200, {"choices": [{"message": {"content": "ok"}}]})]

            def request(self, method, url, **kwargs):
                if not self.runner.user_forward_live and not any(not item.closed for item in self.runner.owned):
                    raise URLError("forward died")
                return self.responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "observation").mkdir()
            runner = RestartRunner()
            clock = FakeClock()
            checker = AWSAcceptance(arguments(root, cases=("AWS-14",), allow_gateway_restart=True), runner=runner, http=RestartHTTP(runner), sleeper=clock.sleep, monotonic=clock.monotonic)
            pod_calls = []
            def kubectl(*argv, **kwargs):
                if "get" in argv:
                    pod_calls.append(1)
                    uid = "old" if len(pod_calls) == 1 else "new"
                    return CommandResult(0, json.dumps({"items": [{"metadata": {"uid": uid}}]}))
                return runner.run(["kubectl", "--context", checker.context, "-n", checker.namespace, *argv])
            checker._kubectl = kubectl
            self.assertEqual(checker.run(), 0)
            self.assertEqual(checker.cases[0]["result"], "passed")
            self.assertEqual(checker.cases[0]["evidence"]["statuses"], [503, 200])
            self.assertTrue(checker.cases[0]["evidence"]["reconnect_at"])
            self.assertEqual(len(runner.owned), 2)
            self.assertEqual(runner.asserted_argv[-3:], ["deploy/serving-gateway", "18080:8080", "--address=127.0.0.1"])
            self.assertTrue(all(item.closed for item in runner.owned))

    def test_compare_uses_running_client_image_or_stays_unknown(self) -> None:
        class ImageRunner(FakeRunner):
            def __init__(self, image):
                super().__init__()
                self.image = image
                self.manifest = None

            def run(self, argv, *, timeout=30, input=None):
                command = list(argv)
                self.history.append({"argv": command, "returncode": 0})
                if command[-5:] == ["get", "pod", "other-client", "-o", "json"]:
                    if self.image is None:
                        return CommandResult(1)
                    return CommandResult(0, json.dumps({"spec": {"containers": [{"name": "client", "image": self.image, "imagePullPolicy": "IfNotPresent"}]}, "status": {"phase": "Running", "containerStatuses": [{"name": "client", "ready": True}]}}))
                if "apply" in command and "-f" in command and "-" in command:
                    self.manifest = input
                    return CommandResult(1)
                return CommandResult(0)

        for image in ("registry.local/serving-tools@sha256:" + "a" * 64, None):
            with self.subTest(image=image), tempfile.TemporaryDirectory() as directory:
                runner = ImageRunner(image)
                checker = AWSAcceptance(arguments(Path(directory), cases=("AWS-12",), allow_compare_policy=True), runner=runner, http=FakeHTTP([]))
                facts, evidence = checker.aws_12()
                self.assertEqual(evaluate_case("AWS-12", facts)[0], "unknown")
                if image is None:
                    self.assertIsNone(runner.manifest)
                else:
                    self.assertEqual(evidence["compare_image"], image)
                    self.assertEqual(runner.manifest.count("image: " + image), 2)
                    self.assertNotIn("python:3.12-slim", runner.manifest)

    def test_gateway_restart_observes_target_503_then_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observation = root / "observation"
            observation.mkdir()
            (observation / "target-pods.json").write_text(json.dumps({"collected_at": "2026-11-03T00:00:00Z"}))
            clock = FakeClock()
            checker = AWSAcceptance(arguments(root, cases=("AWS-14",), allow_gateway_restart=True), runner=FakeRunner(), http=FakeHTTP([]), sleeper=clock.sleep, monotonic=clock.monotonic)
            pod_calls = []
            def kubectl(*argv, **_kwargs):
                if "get" in argv:
                    pod_calls.append(1)
                    uid = "old" if len(pod_calls) == 1 else "new"
                    return CommandResult(0, json.dumps({"items": [{"metadata": {"uid": uid}, "status": {"containerStatuses": [{"state": {"running": {"startedAt": "2026-11-03T00:00:00Z"}}}]}}]}))
                return CommandResult(0)
            checker._kubectl = kubectl
            completions = iter(((response(503, {"error": {"code": "TARGET_UNAVAILABLE"}}), None, False), (response(200, {"choices": [{"message": {"content": "ok"}}]}), None, True)))
            checker._completion = lambda **_kwargs: next(completions)
            facts, evidence = checker.aws_14()
            self.assertEqual(evaluate_case("AWS-14", facts)[0], "passed")
            self.assertEqual(evidence["reason_codes"][0], "TARGET_UNAVAILABLE")

    def test_collector_recovery_failure_overrides_observed_503(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = FakeRunner([CommandResult(0), CommandResult(0), CommandResult(0), CommandResult(1)])
            checker = AWSAcceptance(arguments(root, cases=("AWS-15",), allow_collector_stop=True, allow_gateway_restart=True), runner=runner, http=FakeHTTP([]), sleeper=lambda _s: None)
            checker._completion = lambda **_kwargs: (response(503, {"error": {"code": "TARGET_UNAVAILABLE"}}), None, False)
            facts, evidence = checker.aws_15()
            self.assertEqual(evaluate_case("AWS-15", facts)[0], "failed")
            self.assertEqual(evidence["collector_start_rc"], 1)

    def test_stage_expires_during_collector_stop_and_request_stays_200(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checker = AWSAcceptance(arguments(root, cases=("AWS-16",), engine="sglang", allow_collector_stop=True), runner=FakeRunner(), http=FakeHTTP([]), sleeper=lambda _s: None)
            timeline = {"schema_version": "runtime-phase-timeline/1.0", "extraction": {"engine": "sglang"}, "collector": {"state": "running", "pod_uid": "old", "written_at": datetime.now(timezone.utc).isoformat()}, "entries": [{"phase": "inferring"}]}
            checker._timeline = lambda: timeline
            checker._engine_pod = lambda: ({"name": "engine", "uid": "old"}, CommandResult(0))
            checker._wait_tetragon = lambda *_args: (True, {"kind": "process_exec", "phase": ["unknown"]})
            checker._completion = lambda **_kwargs: (response(200, {}), None, True)
            checker._wait_collectors_ok = lambda _s: (True, {"observation_feed_hubble": {"state": "ok"}, "observation_feed_tetragon": {"state": "ok"}})
            with patch("tools.aws_lab_acceptance.ExecPolicy._collector_stale", side_effect=(False, True)):
                facts, _evidence = checker.aws_16()
            self.assertEqual(evaluate_case("AWS-16", facts)[0], "passed")

    def test_aws16_rejects_stale_or_other_pod_timeline_before_stopping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = FakeRunner()
            checker = AWSAcceptance(arguments(root, cases=("AWS-16",), allow_collector_stop=True), runner=runner, http=FakeHTTP([]))
            checker._engine_pod = lambda: ({"name": "engine", "uid": "current"}, CommandResult(0))
            timeline = {"schema_version": "runtime-phase-timeline/1.0", "extraction": {"engine": "vllm"}, "collector": {
                "state": "running", "pod_uid": "current", "written_at": "2026-11-02T00:00:00Z"},
                "entries": [{"phase": "inferring"}]}
            checker._timeline = lambda: timeline
            for written_at, pod_uid in (("2026-11-02T00:00:00Z", "current"),
                                        (datetime.now(timezone.utc).isoformat(), "other")):
                timeline["collector"].update(written_at=written_at, pod_uid=pod_uid)
                facts, _evidence = checker.aws_16()
                self.assertEqual(evaluate_case("AWS-16", facts)[0], "unknown")
                self.assertFalse(any(row["argv"][:3] == ["systemctl", "--user", "stop"] for row in runner.history))

    def test_aws16_sglang_never_stops_collector_with_leftover_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = FakeRunner()
            checker = AWSAcceptance(arguments(root, cases=("AWS-16",), engine="sglang", allow_collector_stop=True), runner=runner, http=FakeHTTP([]))
            observation = root / "observation"
            observation.mkdir()
            for has_timeline in (True, False):
                timeline = observation / "phase-timeline.json"
                if has_timeline:
                    timeline.write_text(json.dumps({"schema_version": "runtime-phase-timeline/1.0",
                        "collector": {"state": "running", "pod_uid": "old-vllm", "written_at": "2026-11-02T00:00:00Z"},
                        "entries": [{"phase": "inferring"}]}), encoding="utf-8")
                else:
                    timeline.unlink()
                facts, _evidence = checker.aws_16()
                self.assertIn(facts["unknown_reason"],
                              {"현재 엔진 Pod의 신선한 단계 시간표 미확인", "단계 시간표 비활성 또는 읽기 실패"})
                self.assertEqual(evaluate_case("AWS-16", facts)[0], "unknown")
                self.assertFalse(any(row["argv"][:3] == ["systemctl", "--user", "stop"] for row in runner.history))

    def test_aws16_and_aws17_reject_other_engine_timeline_before_mutation(self) -> None:
        for engine, other in (("sglang", "vllm"), ("vllm", "sglang")):
            for case_id in ("AWS-16", "AWS-17"):
                with self.subTest(engine=engine, case=case_id), tempfile.TemporaryDirectory() as directory:
                    runner = FakeRunner()
                    checker = AWSAcceptance(arguments(Path(directory), cases=(case_id,), engine=engine,
                        allow_collector_stop=True, allow_engine_restart=True), runner=runner, http=FakeHTTP([]))
                    checker._engine_pod = lambda: ({"name": "engine", "uid": "current"}, CommandResult(0))
                    checker._timeline = lambda: {"extraction": {"engine": other},
                        "collector": {"state": "running", "pod_uid": "current",
                                      "written_at": datetime.now(timezone.utc).isoformat()},
                        "entries": [{"phase": "inferring"}]}
                    facts, _evidence = checker.aws_16() if case_id == "AWS-16" else checker.aws_17()
                    self.assertEqual(evaluate_case(case_id, facts)[0], "unknown")
                    self.assertFalse(runner.history)

    def test_pod_stage_probe_uses_real_tetragon_uid_shape_and_exit_accounting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observations = root / "observation"
            observations.mkdir()
            event_time = "2026-11-03T00:00:01Z"
            (observations / "tetragon.jsonl").write_text(
                json.dumps({"time": event_time, "process_exec": {"process": {"binary": "/bin/sh", "pod": {"uid": "new-uid"}}}}) + "\n"
                + json.dumps({"time": event_time, "process_exit": {"process": {"pod": {"uid": "new-uid"}}}}) + "\n"
            )
            runner = FakeRunner()
            checker = AWSAcceptance(arguments(root, cases=("AWS-17",), engine="sglang", allow_engine_restart=True), runner=runner, http=FakeHTTP([]), sleeper=lambda _s: None)
            pods = iter(({"name": "engine-old", "uid": "old-uid"}, {"name": "engine-new", "uid": "new-uid", "started_at": "2026-11-03T00:00:00Z"}))
            checker._engine_pod = lambda: (next(pods), CommandResult(0))
            timelines = iter((
                {"schema_version": "runtime-phase-timeline/1.0", "extraction": {"engine": "sglang"}, "collector": {"pod_uid": "old-uid", "state": "running", "written_at": datetime.now(timezone.utc).isoformat()}, "entries": [{"phase": "inferring", "from": "2026-11-02T23:59:00Z"}]},
                {"schema_version": "runtime-phase-timeline/1.0", "extraction": {"engine": "sglang"}, "collector": {"pod_uid": "new-uid", "state": "running"}, "entries": [{"phase": "ready", "from": "2026-11-03T00:00:00Z"}]},
                {"schema_version": "runtime-phase-timeline/1.0", "extraction": {"engine": "sglang"}, "collector": {"pod_uid": "new-uid", "state": "running"}, "entries": [{"phase": "ready", "from": "2026-11-03T00:00:00Z"}]},
            ))
            checker._timeline = lambda: next(timelines)
            checker._tetragon_observations = lambda _since: (True, [{"kind": "process_exec", "binary": "/bin/sh", "observed_at": event_time, "pod": {"namespace": "serving-lab", "pod": "engine-new"}, "phase": ["ready"], "phase_timeline_fresh": True, "phase_timeline_written_at": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()}])
            counts = iter(({"skipped_unsupported_total": 0, "corrupt_lines_total": 0}, {"skipped_unsupported_total": 1, "corrupt_lines_total": 0}))
            checker._tetragon_counts = lambda: next(counts)
            with patch("tools.aws_lab_acceptance._now", return_value="2026-11-03T00:00:00Z"):
                facts, evidence = checker.aws_17()
            self.assertEqual(evaluate_case("AWS-17", facts)[0], "passed")
            self.assertEqual(evidence["raw_process_pod_uid"], "new-uid")
            self.assertTrue(evidence["timeline_reset"]["entries_after_new_start"])
            self.assertTrue(evidence["process_exit_accounting"]["expected_pattern_seen"])

    def test_aws17_rejects_stale_timeline_and_sglang_leftover_before_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = FakeRunner()
            checker = AWSAcceptance(arguments(root, cases=("AWS-17",), allow_engine_restart=True), runner=runner, http=FakeHTTP([]))
            checker._engine_pod = lambda: ({"name": "engine", "uid": "current"}, CommandResult(0))
            timeline = {"schema_version": "runtime-phase-timeline/1.0", "extraction": {"engine": "vllm"},
                "collector": {"state": "running", "pod_uid": "current", "written_at": "2026-11-02T00:00:00Z"},
                "entries": [{"phase": "inferring"}]}
            checker._timeline = lambda: timeline
            facts, _evidence = checker.aws_17()
            self.assertEqual(evaluate_case("AWS-17", facts)[0], "unknown")
            self.assertFalse(runner.history)
            checker.engine = "sglang"
            timeline["extraction"]["engine"] = "sglang"
            facts, _evidence = checker.aws_17()
            self.assertIn("활성 단계 수집 미확인", facts["unknown_reason"])
            self.assertFalse(runner.history)

    def test_aws17_rejects_old_entry_carried_into_new_pod_timeline(self) -> None:
        previous = {"entries": [{"phase": "inferring", "from": "2026-11-03T00:00:00Z"}]}
        new_start = "2026-11-03T00:01:00Z"
        carried = {"entries": [previous["entries"][0], {"phase": "ready", "from": "2026-11-03T00:01:01Z"}]}
        reset = {"entries": [{"phase": "ready", "from": "2026-11-03T00:01:01Z"}]}
        self.assertFalse(_timeline_reset(previous, carried, new_start))
        self.assertTrue(_timeline_reset(previous, reset, new_start))
        self.assertEqual(evaluate_case("AWS-17", {"pod_changed": True, "timeline_new_uid": True,
                                                   "timeline_reset": _timeline_reset(previous, carried, new_start),
                                                   "old_phase_absent": True})[0], "failed")

    def test_compare_policy_uses_only_fixture_and_records_cleanup_failure(self) -> None:
        class CompareRunner(FakeRunner):
            def __init__(self, fail_cleanup=False):
                super().__init__()
                self.probes = 0
                self.fail_cleanup = fail_cleanup

            def run(self, argv, *, timeout=30, input=None):
                command = list(argv)
                self.history.append({"argv": command, "returncode": 0})
                if "exec" in command:
                    self.probes += 1
                    if self.probes == 2:
                        return CommandResult(0, "CONNECT_FAILED errno=110 name=ETIMEDOUT")
                    return CommandResult(1, "CONNECTED")
                if "get" in command and "runtime-compare-receiver" in command and "json" in command:
                    return CommandResult(0, json.dumps({"status": {"podIP": "10.0.0.12"}}))
                if "get" in command and "other-client" in command and "json" in command:
                    return CommandResult(0, json.dumps({"spec": {"containers": [{"name": "client", "image": "python@sha256:" + "a" * 64, "imagePullPolicy": "IfNotPresent"}]}, "status": {"phase": "Running", "containerStatuses": [{"name": "client", "ready": True}]}}))
                if "delete" in command and self.fail_cleanup and "runtime-compare-client" in command:
                    return CommandResult(1)
                return CommandResult(0)

        for fail_cleanup in (False, True):
            with self.subTest(fail_cleanup=fail_cleanup), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                runner = CompareRunner(fail_cleanup)
                checker = AWSAcceptance(arguments(root, cases=("AWS-12",), allow_compare_policy=True), runner=runner, http=FakeHTTP([]), sleeper=lambda _s: None)
                checker._wait_observation_refs = lambda *_args: (True, [{"verdict": "dropped"}])
                facts, evidence = checker.aws_12()
                self.assertEqual(evaluate_case("AWS-12", facts)[0], "failed" if fail_cleanup else "passed")
                commands = [" ".join(row["argv"]) for row in runner.history]
                self.assertTrue(any("policy-compare-cilium.yaml" in row for row in commands))
                self.assertTrue(all("serving-engine" not in row and "serving-gateway" not in row for row in commands))
                self.assertEqual(evidence["blocked_probe"]["errno_name"], "ETIMEDOUT")

    def test_gateway_crash_detection_reads_newest_pod_state(self) -> None:
        def pods(restart_count, waiting=None):
            state = {"waiting": {"reason": waiting}} if waiting else {"running": {}}
            return json.dumps({"items": [
                {"metadata": {"name": "old", "uid": "old", "creationTimestamp": "2026-11-03T00:00:00Z"}, "status": {"containerStatuses": [{"name": "gateway", "restartCount": 5, "state": {"running": {}}}]}},
                {"metadata": {"name": "new", "uid": "new", "creationTimestamp": "2026-11-03T00:01:00Z"}, "status": {"containerStatuses": [{"name": "gateway", "restartCount": restart_count, "state": state}]}},
            ]})
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            for name in ("crashed", "steady", "unreadable"):
                (Path(directory) / name).mkdir()
            crashed = AWSAcceptance(arguments(Path(directory) / "crashed"), runner=FakeRunner([CommandResult(0, pods(1, "CrashLoopBackOff"))]),
                                    http=FakeHTTP([]), sleeper=clock.sleep, monotonic=clock.monotonic)
            self.assertIs(crashed._gateway_crashed(10.0, set())[0], True)
            self.assertIsNone(crashed._gateway_crashed(0.0, {"old", "new"})[0])
            steady = AWSAcceptance(arguments(Path(directory) / "steady"), runner=FakeRunner([CommandResult(0, pods(0))] * 20),
                                   http=FakeHTTP([]), sleeper=clock.sleep, monotonic=clock.monotonic)
            self.assertIs(steady._gateway_crashed(10.0, {"old"})[0], False)
            unreadable = AWSAcceptance(arguments(Path(directory) / "unreadable"), runner=FakeRunner([CommandResult(1)] * 20),
                                       http=FakeHTTP([]), sleeper=clock.sleep, monotonic=clock.monotonic)
            self.assertIsNone(unreadable._gateway_crashed(10.0, set())[0])

    def test_bad_bundle_is_restored_even_when_gateway_recovery_fails(self) -> None:
        for fail_recovery in (False, True):
            with self.subTest(fail_recovery=fail_recovery), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bundles = root / "bundles"
                previous = bundles / "previous"
                previous.mkdir(parents=True)
                (previous / "policy.json").write_bytes(b"{}")
                (previous / "bundle.json").write_text(json.dumps({"files": [{"role": "gateway_policy", "path": "policy.json"}]}))
                (bundles / "current").symlink_to("previous")
                runner = FakeRunner([CommandResult(0, "SHA-256 mismatch")])
                checker = AWSAcceptance(arguments(root, cases=("AWS-13",), allow_bundle_switch=True, bundle_root=str(bundles)), runner=runner, http=FakeHTTP([]))
                calls = []
                def restart(_deployment, _timeout=90):
                    calls.append(1)
                    return (len(calls) == 2 and not fail_recovery), {"attempt": len(calls)}
                checker._restart = restart
                checker._gateway_pods = lambda: [{"metadata": {"uid": "old"}}]
                checker._gateway_crashed = lambda _window, _uids: (True, {"restart_count": 1, "pod_name": "gateway-new", "pod_uid": "new"})
                checker._retry_inference = lambda _timeout: (True, {"result": "ALLOWED"}, {"attempts": 1, "last_status": 200})
                checker._completion = lambda **_kwargs: (response(401, {}, "denied-1"), "denied-1", False)
                checker._detail = lambda _id: detail("DENIED", reason="AUTH_REQUIRED", attempted=False, status=401)
                facts, _evidence = checker.aws_13()
                self.assertEqual(evaluate_case("AWS-13", facts)[0], "failed" if fail_recovery else "passed")
                log_calls = [item["argv"] for item in runner.history if "logs" in item["argv"]]
                self.assertTrue(log_calls and all("pod/gateway-new" in call for call in log_calls))
                self.assertEqual((bundles / "current").readlink(), Path("previous"))
                self.assertEqual(sorted(path.name for path in bundles.iterdir()), ["current", "previous"])

    def test_bad_bundle_retries_previous_and_current_logs_with_diagnostics(self) -> None:
        class LogRunner(FakeRunner):
            def __init__(self, logs):
                super().__init__()
                self.logs = list(logs)

            def run(self, argv, *, timeout=30):
                if "logs" in argv:
                    self.history.append({"argv": list(argv)})
                    return self.logs.pop(0)
                return super().run(argv, timeout=timeout)

        scenarios = (
            ([CommandResult(0, "starting\n"), CommandResult(0, "still starting\n"),
              CommandResult(1, "", "container waiting"), CommandResult(0, "MVP 실행 실패: runtime-policy.json: SHA-256이 bundle.json과 다릅니다\n")], True, 4, "hash_error"),
            ([CommandResult(1, "") if index % 2 else CommandResult(0, "token=" + INFERENCE + "\n")
              for index in range(8)], None, 8, "known_token"),
            ([CommandResult(0, line + "\n") for line in (
                "RUNTIME_ADMIN_KEY=fake-admin-key OPENAI_API_KEY: fake-openai-key",
                "MiXeD_CrEdEnTiAl=FakeCredential custom_AUTH: FakeAuth",
                "service_secret=FakeSecret user_password: FakePassword",
                "api.token=FakeToken Bearer FakeBearer",
            )] + [CommandResult(1, "") for _ in range(4)], None, 8, "assignments"),
            ([CommandResult(0, line + "\n") for line in (
                '{"api_key": "sk-json-double"}',
                "{'api_key': 'sk-json-single'}",
                '"RUNTIME_ADMIN_KEY":"sk-runtime-json"',
                "--api-key sk-cli-api --admin-key 'sk-cli-admin'",
                "RUNTIME_ADMIN_KEY=sk-existing-equals OPENAI_API_KEY: sk-existing-colon",
                "x-api-key: sk-existing-header Authorization: Bearer sk-existing-bearer",
                "gateway startup failed: policy file unavailable",
                "container waiting",
            )], None, 8, "quoted_and_options"),
        )
        for log_results, expected, expected_reads, scenario_name in scenarios:
            with self.subTest(scenario=scenario_name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bundles = root / "bundles"
                previous = bundles / "previous"
                previous.mkdir(parents=True)
                (previous / "policy.json").write_bytes(b"{}")
                (previous / "bundle.json").write_text(json.dumps({"files": [{"role": "gateway_policy", "path": "policy.json"}]}))
                (bundles / "current").symlink_to("previous")
                runner = LogRunner(log_results)
                clock = FakeClock()
                checker = AWSAcceptance(arguments(root, cases=("AWS-13",), allow_bundle_switch=True,
                                                  bundle_root=str(bundles)), runner=runner,
                                        http=FakeHTTP([]), sleeper=clock.sleep, monotonic=clock.monotonic)
                checker._gateway_pods = lambda: [{"metadata": {"uid": "old"}}]
                checker._gateway_crashed = lambda _window, _uids: (True, {"pod_name": "gateway-new"})
                checker._restart = lambda _deployment, _timeout=90: (False, {})
                facts, evidence = checker.aws_13()
                self.assertIs(facts["bad_bundle_rejected"], expected)
                reads = evidence["bad_gateway_log_reads"]
                self.assertEqual(len(reads), expected_reads)
                self.assertEqual([row["source"] for row in reads], ["previous", "current"] * (expected_reads // 2))
                self.assertTrue(all("returncode" in row and "stdout_length" in row and
                                    "stderr_length" in row and len(row["first_line_prefix"]) <= 120 for row in reads))
                if expected:
                    self.assertEqual(reads[2]["first_line_prefix"], "container waiting")
                    self.assertEqual(reads[3]["first_line_prefix"],
                                     "MVP 실행 실패: runtime-policy.json: SHA-256이 bundle.json과 다릅니다")
                self.assertNotIn(INFERENCE, json.dumps(reads))
                for secret in ("fake-admin-key", "fake-openai-key", "FakeCredential", "FakeAuth",
                               "FakeSecret", "FakePassword", "FakeToken", "FakeBearer",
                               "sk-json-double", "sk-json-single", "sk-runtime-json", "sk-cli-api",
                               "sk-cli-admin", "sk-existing-equals", "sk-existing-colon",
                               "sk-existing-header", "sk-existing-bearer"):
                    self.assertNotIn(secret, json.dumps(reads))
                if scenario_name == "quoted_and_options":
                    self.assertEqual(reads[0]["first_line_prefix"], '{"api_key": [REDACTED]}')
                    self.assertEqual(reads[1]["first_line_prefix"], "{'api_key': [REDACTED]}")
                    self.assertEqual(reads[2]["first_line_prefix"], '"RUNTIME_ADMIN_KEY":[REDACTED]')
                    self.assertEqual(reads[3]["first_line_prefix"],
                                     "--api-key [REDACTED] --admin-key [REDACTED]")
                    self.assertEqual(reads[6]["first_line_prefix"],
                                     "gateway startup failed: policy file unavailable")
                self.assertEqual((bundles / "current").readlink(), Path("previous"))

    def test_bad_bundle_log_reads_stop_at_eight_second_deadline(self) -> None:
        class TimedLogRunner(FakeRunner):
            def __init__(self, clock):
                super().__init__()
                self.clock = clock
                self.timeouts = []

            def run(self, argv, *, timeout=30):
                if "logs" in argv:
                    self.timeouts.append(timeout)
                    self.clock.value += timeout
                    return CommandResult(1, "", "still waiting")
                return super().run(argv, timeout=timeout)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundles = root / "bundles"
            previous = bundles / "previous"
            previous.mkdir(parents=True)
            (previous / "policy.json").write_bytes(b"{}")
            (previous / "bundle.json").write_text(json.dumps({
                "files": [{"role": "gateway_policy", "path": "policy.json"}]}))
            (bundles / "current").symlink_to("previous")
            clock = FakeClock()
            runner = TimedLogRunner(clock)
            checker = AWSAcceptance(arguments(root, cases=("AWS-13",), allow_bundle_switch=True,
                                              bundle_root=str(bundles)), runner=runner,
                                    http=FakeHTTP([]), sleeper=clock.sleep, monotonic=clock.monotonic)
            checker._gateway_pods = lambda: [{"metadata": {"uid": "old"}}]
            checker._gateway_crashed = lambda _window, _uids: (True, {"pod_name": "gateway-new"})
            checker._restart = lambda _deployment, _timeout=90: (False, {})
            facts, evidence = checker.aws_13()
            self.assertIsNone(facts["bad_bundle_rejected"])
            self.assertEqual(len(evidence["bad_gateway_log_reads"]), 2)
            self.assertEqual(runner.timeouts, [3.0, 3.0])
            self.assertLessEqual(clock.value, 8.0)
            self.assertEqual((bundles / "current").readlink(), Path("previous"))


if __name__ == "__main__":
    unittest.main()
