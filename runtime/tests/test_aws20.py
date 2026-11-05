from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.aws_lab_acceptance import (
    AWSAcceptance, CommandResult, HTTPResponse, _aws20_raw_override, _aws20_same_rule,
    _secret_policy_mode, evaluate_case, parse_args,
)


PATH = "/run/byul-probe/secret.txt"


def policy(mode: str) -> dict:
    return {
        "kind": "TracingPolicyNamespaced",
        "metadata": {"name": "runtime-tg-secret-01", "namespace": "serving-lab"},
        "spec": {"podSelector": {"matchLabels": {"app": "serving-engine"}}, "kprobes": [{
            "call": "security_file_permission", "selectors": [{
                "matchArgs": [
                    {"index": 0, "operator": "Equal" if mode == "enforce" else "Prefix", "values": [PATH]},
                    {"index": 1, "operator": "Equal", "values": ["4"]},
                ],
                "matchActions": [{"action": "Post"}] + ([{"action": "Override", "argError": -1}] if mode == "enforce" else []),
            }],
        }]},
    }


class Runner:
    def __init__(self):
        self.mode = "enforce"
        self.calls = []
        self.history = []

    def run(self, argv, *, timeout=30):
        self.calls.append(argv)
        if "get" in argv and "tracingpolicynamespaced" in argv:
            return CommandResult(0, json.dumps(policy(self.mode)))
        if "get" in argv and "pod" in argv:
            return CommandResult(0, json.dumps({"metadata": {"uid": "pod-uid", "labels": {"app": "serving-engine"}}}))
        if "exec" in argv:
            return CommandResult(13, '{"errno":1,"name":"EPERM"}') if self.mode == "enforce" else CommandResult(0, '{"read":"ok"}')
        if "delete" in argv:
            return CommandResult(0)
        if "apply" in argv:
            self.mode = "monitor"
            return CommandResult(0)
        raise AssertionError(argv)


class AWS20Tests(unittest.TestCase):
    def test_policy_and_raw_uid_action_match(self):
        self.assertEqual(_secret_policy_mode(policy("enforce"), namespace="serving-lab"), "enforce")
        self.assertEqual(_secret_policy_mode(policy("monitor"), namespace="serving-lab"), "monitor")
        self.assertTrue(_aws20_same_rule(policy("enforce"), policy("monitor")))
        altered = policy("enforce")
        altered["spec"]["kprobes"][0]["selectors"][0]["matchArgs"][0]["values"] = ["/real/secret"]
        self.assertIsNone(_secret_policy_mode(altered, namespace="serving-lab"))
        self.assertFalse(_aws20_same_rule(altered, policy("monitor")))
        raw = {"time": "2026-11-05T00:01:00Z", "process_kprobe": {
            "policy_name": "runtime-tg-secret-01", "action": "KPROBE_ACTION_OVERRIDE",
            "process": {"pod": {"namespace": "serving-lab", "uid": "pod-uid"}},
            "args": [{"file_arg": {"path": PATH}}, {"int_arg": 4}],
        }}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tetragon.jsonl"
            path.write_text(json.dumps(raw) + "\n")
            self.assertTrue(_aws20_raw_override(path, since="2026-11-05T00:00:00Z", pod_uid="pod-uid", namespace="serving-lab"))
            self.assertFalse(_aws20_raw_override(path, since="2026-11-05T00:00:00Z", pod_uid="other", namespace="serving-lab"))

    def test_evaluation_and_opt_in(self):
        facts = dict.fromkeys(("enforce_active", "read_eperm", "raw_override", "normal_200", "cleanup_ok", "restore_read_ok"), True)
        self.assertEqual(evaluate_case("AWS-20", facts)[0], "passed")
        self.assertEqual(evaluate_case("AWS-20", {**facts, "raw_override": None})[0], "unknown")
        self.assertEqual(evaluate_case("AWS-20", {**facts, "cleanup_ok": False})[0], "failed")
        with self.assertRaises(SystemExit):
            parse_args(["--kube-context", "byul-aws-lab", "--token-env-file", "x", "--cases", "AWS-20"])
        args = parse_args(["--kube-context", "byul-aws-lab", "--token-env-file", "x", "--cases", "AWS-20", "--allow-rule-switch", "--aws20-monitor-manifest", "monitor.json"])
        self.assertEqual(args.cases, ("AWS-20",))

    def test_live_case_restores_monitor_after_enforce_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tokens.env").write_text("RUNTIME_INFERENCE_TOKEN=test\nRUNTIME_VIEWER_TOKEN=view\n")
            monitor = root / "monitor.json"
            monitor.write_text(json.dumps(policy("monitor")))
            args = Namespace(kube_context="byul-aws-lab", namespace="serving-lab", gateway_url="http://127.0.0.1:18080",
                             token_env_file=str(root / "tokens.env"), observation_dir=str(root), evidence_dir=str(root / "evidence"),
                             engine="vllm", aws20_monitor_manifest=str(monitor), allow_rule_switch=True, cases=("AWS-20",))
            runner = Runner()
            checker = AWSAcceptance(args, runner=runner, sleeper=lambda _: None)
            checker._engine_pod = lambda: ({"name": "engine-1", "uid": "pod-uid", "ready": True}, CommandResult(0))
            checker._completion = lambda **kwargs: (HTTPResponse(200, {}, b"{}"), "request-1", True)
            checker._detail = lambda _: {"final_state": {"result": "ALLOWED", "delivery": {"attempted": True}}}
            checker._tetragon_collector_ok = lambda: True
            checker._tetragon_gaps_clear = lambda _: True
            with patch("tools.aws_lab_acceptance._aws20_raw_override", return_value=True):
                facts, _ = checker.aws_20()
            self.assertEqual(evaluate_case("AWS-20", facts)[0], "passed")
            self.assertEqual(runner.mode, "monitor")
            self.assertTrue(any("delete" in call for call in runner.calls))
            self.assertTrue(any("apply" in call for call in runner.calls))

    def test_request_error_still_restores_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tokens.env").write_text("RUNTIME_INFERENCE_TOKEN=test\nRUNTIME_VIEWER_TOKEN=view\n")
            monitor = root / "monitor.json"
            monitor.write_text(json.dumps(policy("monitor")))
            args = Namespace(kube_context="byul-aws-lab", namespace="serving-lab", gateway_url="http://127.0.0.1:18080",
                             token_env_file=str(root / "tokens.env"), observation_dir=str(root), evidence_dir=str(root / "evidence"),
                             engine="vllm", aws20_monitor_manifest=str(monitor), allow_rule_switch=True, cases=("AWS-20",))
            runner = Runner()
            checker = AWSAcceptance(args, runner=runner, sleeper=lambda _: None)
            checker._engine_pod = lambda: ({"name": "engine-1", "uid": "pod-uid", "ready": True}, CommandResult(0))
            checker._completion = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("request failed"))
            with patch("tools.aws_lab_acceptance._aws20_raw_override", return_value=True):
                with self.assertRaises(RuntimeError):
                    checker.aws_20()
            self.assertEqual(runner.mode, "monitor")


if __name__ == "__main__":
    unittest.main()
