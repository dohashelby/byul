from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.k8s_observation_feed import redact_tetragon_line
from tools.aws_lab_acceptance import (
    AWSAcceptance, CommandResult, HTTPResponse, _aws20_method, _aws20_raw_action,
    evaluate_case, parse_args,
)


PATH = "/run/byul-probe/secret.txt"


def policy(method="override_lsm"):
    call = {"override_lsm": "security_file_permission", "sigkill_fd_install": "fd_install",
            "override_syscall": "sys_openat"}[method]
    actions = ([{"action": "Sigkill"}] if method == "sigkill_fd_install" else
               [{"action": "Post"}, {"action": "Override", "argError": -1}, {"action": "Sigkill"}])
    return {"kind": "TracingPolicyNamespaced",
            "metadata": {"name": "runtime-tg-secret-01", "namespace": "serving-lab",
                         "annotations": {"serving-runtime.local/enforce-method": method}},
            "spec": {"options": [{"name": "policy-mode", "value": "monitor"}],
                     "podSelector": {"matchLabels": {"app": "serving-engine"}},
                     "kprobes": [{"call": call, "selectors": [{"matchArgs": [{"index": 1 if method != "override_lsm" else 0,
                                                                          "operator": "Equal",
                                                                          "values": [PATH + ("\x00" if method == "override_syscall" else "")]},
                                                                         *([{"index": 1, "operator": "Equal", "values": ["4"]}] if method == "override_lsm" else [])],
                                                                 "matchActions": actions}]}]}}


class Runner:
    def __init__(self):
        self.mode = "monitor"
        self.calls = []

    def run(self, argv, *, timeout=30):
        self.calls.append(argv)
        if argv[-2:] == ["tracingpolicy", "list"]:
            return CommandResult(0, "ID NAME STATE FILTERID NAMESPACE SENSORS KERNELMEMORY MODE\n"
                                    f"7 runtime-tg-secret-01 enabled 1 serving-lab generic_kprobe 1MB {self.mode}\n")
        if "set-mode" in argv:
            self.mode = argv[-1]
            return CommandResult(0)
        if "get" in argv and "tracingpolicynamespaced" in argv:
            return CommandResult(0, json.dumps(policy()))
        if "get" in argv and "pod" in argv:
            return CommandResult(0, json.dumps({"metadata": {"uid": "pod-uid", "labels": {"app": "serving-engine"}}}))
        if "exec" in argv:
            return CommandResult(13, '{"errno":1,"name":"EPERM"}') if self.mode == "enforce" else CommandResult(0, '{"read":"ok"}')
        raise AssertionError(argv)


class AWS20Tests(unittest.TestCase):
    def test_method_and_raw_action(self):
        for method in ("override_lsm", "sigkill_fd_install", "override_syscall"):
            self.assertEqual(_aws20_method(policy(method), namespace="serving-lab"), method)
        altered = policy()
        altered["spec"]["kprobes"][0]["selectors"][0]["matchArgs"][0]["values"] = ["/real/secret"]
        self.assertIsNone(_aws20_method(altered, namespace="serving-lab"))
        raw = {"time": "2026-11-05T00:01:00Z", "process_kprobe": {
            "policy_name": "runtime-tg-secret-01", "action": "KPROBE_ACTION_SIGKILL",
            "process": {"pod": {"namespace": "serving-lab", "uid": "pod-uid"}},
            "args": [{"int_arg": 3}, {"file_arg": {"path": PATH}}]}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tetragon.jsonl"
            path.write_text(json.dumps(raw) + "\n")
            self.assertTrue(_aws20_raw_action(path, since="2026-11-05T00:00:00Z", pod_uid="pod-uid",
                                               namespace="serving-lab", action="KPROBE_ACTION_SIGKILL", method="sigkill_fd_install"))
            self.assertFalse(_aws20_raw_action(path, since="2026-11-05T00:00:00Z", pod_uid="other",
                                                namespace="serving-lab", action="KPROBE_ACTION_SIGKILL", method="sigkill_fd_install"))

    def test_file_open_and_override_only_syscall_candidates(self):
        from tests.test_tetragon_file_open import policy as rendered
        for method in ('lsm_file_open', 'override_syscall'):
            candidate = rendered(method)
            candidate['metadata']['namespace'] = 'serving-lab'
            probes = candidate['spec'].get('lsmhooks', candidate['spec'].get('kprobes'))
            probes[0]['selectors'][0]['matchArgs'][0]['values'] = [PATH + ('\x00' if method == 'override_syscall' else '')]
            self.assertEqual(_aws20_method(candidate, namespace='serving-lab'), method)
        raw = {'time': '2026-11-08T00:01:00Z', 'process_lsm': {
            'function_name': 'file_open', 'policy_name': 'runtime-tg-secret-01',
            'action': 'KPROBE_ACTION_OVERRIDE', 'process': {'pod': {'namespace': 'serving-lab', 'uid': 'uid'}},
            'args': [{'file_arg': {'path': PATH}}]}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text(json.dumps(raw) + '\n')
            self.assertTrue(_aws20_raw_action(path, since='2026-11-08T00:00:00Z', pod_uid='uid',
                namespace='serving-lab', action='KPROBE_ACTION_OVERRIDE', method='lsm_file_open'))

    def test_probe_survives_default_redaction_and_remains_exactly_bound(self):
        tag = "byul-read-probe-" + "a" * 32
        event = {"time": "2026-11-05T00:01:00Z", "process_kprobe": {
            "policy_name": "runtime-tg-secret-01", "action": "KPROBE_ACTION_SIGKILL",
            "process": {"arguments": "-c 'private payload' " + tag,
                        "pod": {"namespace": "serving-lab", "uid": "pod-uid"}},
            "args": [{"file_arg": {"path": PATH}}]}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tetragon.jsonl"
            cleaned = redact_tetragon_line(json.dumps(event).encode())
            self.assertNotIn(b"private payload", cleaned)
            self.assertNotIn(b"arguments", cleaned)
            path.write_bytes(cleaned)
            arguments = dict(since="2026-11-05T00:00:00Z", pod_uid="pod-uid", namespace="serving-lab",
                             action="KPROBE_ACTION_SIGKILL", method="sigkill_fd_install", probe_tag=tag)
            self.assertTrue(_aws20_raw_action(path, **arguments))
            for wrong in ({"probe_tag": tag + "-monitor"}, {"pod_uid": "other"},
                          {"namespace": "other"}, {"since": "2026-11-05T00:02:00Z"},
                          {"action": "KPROBE_ACTION_OVERRIDE"}):
                self.assertFalse(_aws20_raw_action(path, **{**arguments, **wrong}))

    def test_method_specific_verdicts_and_opt_in(self):
        override = dict.fromkeys(("enforce_active", "monitor_read_ok", "read_eperm", "raw_override", "normal_200", "cleanup_ok", "restore_read_ok"), True)
        override["enforce_method"] = "override_lsm"
        self.assertEqual(evaluate_case("AWS-20", override)[0], "passed")
        self.assertEqual(evaluate_case("AWS-20", {**override, "raw_override": None})[0], "unknown")
        self.assertEqual(evaluate_case("AWS-20", {**override, "cleanup_ok": False})[0], "failed")
        sigkill = dict.fromkeys(("enforce_active", "monitor_read_ok", "exit_sigkill", "raw_sigkill", "normal_200", "cleanup_ok", "restore_read_ok"), True)
        sigkill.update(enforce_method="sigkill_fd_install", engine_target=False)
        self.assertEqual(evaluate_case("AWS-20", sigkill)[0], "passed")
        self.assertEqual(evaluate_case("AWS-20", {**sigkill, "raw_sigkill": None})[0], "unknown")
        self.assertEqual(evaluate_case("AWS-20", {**sigkill, "engine_target": True})[0], "unknown")
        with self.assertRaises(SystemExit):
            parse_args(["--kube-context", "byul-aws-lab", "--token-env-file", "x", "--cases", "AWS-20"])
        args = parse_args(["--kube-context", "byul-aws-lab", "--token-env-file", "x", "--cases", "AWS-20",
                           "--allow-rule-switch", "--aws20-monitor-manifest", "monitor.json"])
        self.assertEqual(args.cases, ("AWS-20",))

    def _checker(self, root):
        (root / "tokens.env").write_text("RUNTIME_INFERENCE_TOKEN=test\nRUNTIME_VIEWER_TOKEN=view\n")
        monitor = root / "monitor.json"
        monitor.write_text(json.dumps(policy()))
        args = Namespace(kube_context="byul-aws-lab", namespace="serving-lab", gateway_url="http://127.0.0.1:18080",
                         token_env_file=str(root / "tokens.env"), observation_dir=str(root), evidence_dir=str(root / "evidence"),
                         engine="vllm", aws20_monitor_manifest=str(monitor), allow_rule_switch=True,
                         allow_engine_sigkill=True, cases=("AWS-20",))
        runner = Runner()
        checker = AWSAcceptance(args, runner=runner, sleeper=lambda _: None)
        checker._engine_pod = lambda: ({"name": "engine-1", "uid": "pod-uid", "ready": True}, CommandResult(0))
        checker._completion = lambda **kwargs: (HTTPResponse(200, {}, b"{}"), "request-1", True)
        checker._detail = lambda _: {"final_state": {"result": "ALLOWED", "delivery": {"attempted": True}}}
        checker._tetragon_collector_ok = lambda: True
        checker._tetragon_gaps_clear = lambda _: True
        return checker, runner

    def test_all_sigkill_candidates_require_engine_opt_in(self):
        for method in ("override_lsm", "override_syscall", "sigkill_fd_install"):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as directory:
                checker, runner = self._checker(Path(directory))
                checker.args.allow_engine_sigkill = False
                Path(checker.args.aws20_monitor_manifest).write_text(json.dumps(policy(method)))
                facts, _ = checker.aws_20()
                self.assertEqual(evaluate_case("AWS-20", facts)[0], "not_run")
                self.assertIn("--allow-engine-sigkill", facts["not_run_reason"])
                self.assertEqual(runner.calls, [])

    def test_live_case_switches_mode_without_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            checker, runner = self._checker(Path(directory))
            with patch("tools.aws_lab_acceptance._aws20_raw_action", return_value=True):
                facts, evidence = checker.aws_20()
            self.assertEqual(evaluate_case("AWS-20", facts)[0], "passed")
            self.assertEqual(runner.mode, "monitor")
            self.assertFalse(any("delete" in call or "apply" in call for call in runner.calls))
            self.assertEqual(evidence["policy_id"], "7")

    def test_request_error_still_restores_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            checker, runner = self._checker(Path(directory))
            checker._completion = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("request failed"))
            with patch("tools.aws_lab_acceptance._aws20_raw_action", return_value=True):
                with self.assertRaises(RuntimeError):
                    checker.aws_20()
            self.assertEqual(runner.mode, "monitor")


if __name__ == "__main__":
    unittest.main()
