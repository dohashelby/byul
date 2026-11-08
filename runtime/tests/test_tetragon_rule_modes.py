"""Narrow TG-SECRET-01 enforcement and legacy rendering compatibility."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serving_runtime.cli import main
from serving_runtime.enforcement.events import parse_tetragon
from serving_runtime.enforcement.intent import IntentError, parse_intent
from serving_runtime.enforcement.tetragon import render_manifests
from tools.k8s_lab_check import (
    LabChecker, ProcessResult, SECRET_PROBE_PATH, build_parser, evaluate_l05b_phase,
)


ROOT = Path(__file__).resolve().parents[1]


def document() -> dict:
    value = json.loads((ROOT / "examples/enforcement/intent-v1.1.example.json").read_text())
    value["protected_paths"]["secret_paths"] = [SECRET_PROBE_PATH]
    return value


class RuleModeTests(unittest.TestCase):
    def test_monitor_golden_and_probe_independence(self) -> None:
        base = document()
        absent = render_manifests(parse_intent(base))
        base["tetragon_rule_modes"] = {"TG-SECRET-01": "monitor", "TG-CACHE-01": "monitor"}
        explicit = render_manifests(parse_intent(base))
        self.assertEqual(json.dumps(absent, indent=2), json.dumps(explicit, indent=2))
        base["tetragon_mode"] = "enforce_probe"
        base["probe_path"] = "/opt/serving/probe/blocked-probe.txt"
        probe = render_manifests(parse_intent(base))
        self.assertIn("tetragon-tg-deny-01.json", probe)
        self.assertEqual(probe["tetragon-tg-secret-01.json"], absent["tetragon-tg-secret-01.json"])
        base["tetragon_rule_modes"] = {"TG-SECRET-01": "enforce"}
        combined = render_manifests(parse_intent(base))
        self.assertIn("tetragon-tg-deny-01.json", combined)
        self.assertEqual(combined["tetragon-tg-secret-01.json"]["spec"]["kprobes"][0]["selectors"][0]["matchActions"][-1],
                         {"action": "Override", "argError": -1})

    def test_enforce_golden_and_cli_summary(self) -> None:
        source = document()
        source["tetragon_rule_modes"] = {"TG-SECRET-01": "enforce"}
        intent = parse_intent(source)
        policy = render_manifests(intent)["tetragon-tg-secret-01.json"]
        self.assertEqual(policy["spec"]["podSelector"]["matchLabels"], source["engine"]["labels"])
        self.assertEqual(policy["spec"]["kprobes"][0]["call"], "security_file_permission")
        selector = policy["spec"]["kprobes"][0]["selectors"][0]
        self.assertEqual(selector["matchArgs"], [
            {"index": 0, "operator": "Equal", "values": [SECRET_PROBE_PATH]},
            {"index": 1, "operator": "Equal", "values": ["4"]},
        ])
        self.assertEqual(selector["matchActions"], [
            {"action": "Post"}, {"action": "Override", "argError": -1},
        ])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "intent.json"
            path.write_text(json.dumps(source))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["enforce", "render", "--intent", str(path),
                                       "--out", str(Path(temporary) / "rendered")]), 0)
            self.assertIn("차단 규칙 1개(미검증)", output.getvalue())

    def test_rejected_modes_and_directory_paths(self) -> None:
        for modes in ({"TG-DENY-01": "enforce"}, {"TG-UNKNOWN": "monitor"},
                      {"TG-ASSET-01": "enforce"}, {"TG-ASSET-02": "enforce"},
                      {"TG-CACHE-01": "enforce"}, {"TG-SECRET-01": "deny"}):
            with self.subTest(modes=modes), self.assertRaises(IntentError):
                source = document()
                source["tetragon_rule_modes"] = modes
                parse_intent(source)
        for paths in ([], ["/opt/serving/secret"], ["/opt/serving/secret/"],
                      ["/opt/serving/secret/read-probe.txt", "/opt/serving/secret/read-probe.txt/child"]):
            with self.subTest(paths=paths), self.assertRaises(IntentError):
                source = document()
                source["protected_paths"]["secret_paths"] = paths
                source["tetragon_rule_modes"] = {"TG-SECRET-01": "enforce"}
                parse_intent(source)
        source = document()
        source["schema"] = "runtime-enforcement-intent/1.0"
        source["tetragon_rule_modes"] = {"TG-SECRET-01": "monitor"}
        with self.assertRaises(IntentError):
            parse_intent(source)

    def test_override_observation_and_l05b_match(self) -> None:
        raw = {
            "time": "2026-11-05T00:00:01Z",
            "process_kprobe": {
                "process": {"pod": {"uid": "pod-1", "namespace": "serving-lab", "pod_name": "engine"}},
                "function_name": "security_file_permission",
                "policy_name": "runtime-tg-secret-01",
                "args": [{"file_arg": {"path": SECRET_PROBE_PATH}}, {"int_arg": 4}],
                "action": "KPROBE_ACTION_OVERRIDE",
            },
        }
        observation = parse_tetragon(raw, data_origin="synthetic")
        self.assertEqual((observation["rule_id"], observation["action_mode"], observation["action_raw"]),
                         ("TG-SECRET-01", "enforce", "KPROBE_ACTION_OVERRIDE"))
        line = json.dumps(raw)
        args = {"since": "2026-11-05T00:00:00Z", "pod_uid": "pod-1",
                "namespace": "serving-lab", "action": "KPROBE_ACTION_OVERRIDE"}
        self.assertTrue(evaluate_l05b_phase([line], **args))
        self.assertFalse(evaluate_l05b_phase([line], **{**args, "pod_uid": "other"}))
        self.assertFalse(evaluate_l05b_phase([line], **{**args, "action": "KPROBE_ACTION_POST"}))

    def test_l05b_requires_explicit_opt_in(self) -> None:
        args = build_parser().parse_args(["--state-dir", "/tmp/lab", "--secret-enforce-check"])
        self.assertTrue(args.secret_enforce_check)
        with tempfile.TemporaryDirectory() as temporary:
            common = {"context": "kind-runtime-lab", "namespace": "serving-lab",
                      "state_dir": Path(temporary), "cleanup": False, "repo_root": ROOT}
            self.assertNotIn("L-05b", LabChecker(**common).cases)
            checker = LabChecker(**common, secret_enforce_check=True)
            self.assertIn("L-05b", checker.cases)
            self.assertNotIn("L-07", checker.cases)

    def test_l05b_monitor_enforce_restore_without_cluster(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checker = LabChecker(
                context="kind-runtime-lab", namespace="serving-lab",
                state_dir=Path(temporary), cleanup=False, repo_root=ROOT,
                secret_enforce_check=True, sleeper=lambda _seconds: None,
            )
            checker.pods = {"engine": "engine-pod", "gateway": "gateway-pod"}
            applied: list[str] = []
            mode = "monitor"
            calls: list[tuple[str, ...]] = []
            reads = iter([
                ProcessResult(("read",), 0, '{"read":"ok"}\n', ""),
                ProcessResult(("read",), 13, '{"errno":1,"name":"EPERM"}\n', ""),
                ProcessResult(("read",), 0, '{"read":"ok"}\n', ""),
            ])

            def kubectl(_case, *args, **_kwargs):
                nonlocal mode
                calls.append(args)
                if args[:2] == ("exec", "daemonset/tetragon") and args[-2:] == ("tracingpolicy", "list"):
                    output = ("ID NAME STATE FILTERID NAMESPACE SENSORS KERNELMEMORY MODE\n"
                              f"7 runtime-tg-secret-01-l05b enabled 1 serving-lab generic_kprobe 1MB {mode}\n")
                    return ProcessResult(("kubectl", *args), 0, output, "")
                if "set-mode" in args:
                    mode = args[-1]
                return ProcessResult(("kubectl", *args), 0,
                                     "pod-1" if args[:2] == ("get", "pod") else "", "")

            def apply(_case, path):
                applied.append(str(path))
                return ProcessResult(("apply",), 0, "", "")

            def collect(_case, *, path, since, expected_events):
                action = ("KPROBE_ACTION_OVERRIDE" if "enforce" in path.name
                          else "KPROBE_ACTION_POST")
                raw = {"time": "2099-01-01T00:00:00Z", "process_kprobe": {
                    "process": {"pod": {"uid": "pod-1", "namespace": "serving-lab"}},
                    "policy_name": "runtime-tg-secret-01-l05b", "action": action,
                    "args": [{"file_arg": {"path": SECRET_PROBE_PATH}}, {"int_arg": 4}],
                }}
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(raw) + "\n")
                return [], {"elapsed_seconds": 0}

            with patch.object(checker, "_kubectl", side_effect=kubectl), \
                 patch.object(checker, "_apply", side_effect=apply), \
                 patch.object(checker, "_exec", side_effect=lambda *_args: next(reads)), \
                 patch.object(checker, "_gateway_request", return_value=ProcessResult(
                     ("gateway",), 0, '{"status":200}\n', "")), \
                 patch.object(checker, "_collect_tetragon_logs", side_effect=collect):
                checker._l05b(checker.cases["L-05b"])
            case = checker.cases["L-05b"]
            self.assertEqual(case["status"], "passed")
            self.assertEqual([step["phase"] for step in case["steps"]],
                             ["monitor", "enforce", "restored"])
            self.assertTrue(all(step["event_matched"] for step in case["steps"]))
            self.assertTrue(case["steps"][1]["normal_request_200"])
            self.assertEqual([Path(path).parent.name for path in applied], ["l05b-trial"])
            self.assertEqual({step["policy_id"] for step in case["steps"]}, {"7"})
            self.assertFalse(any("delete" in call for call in calls[:next(i for i, call in enumerate(calls) if "set-mode" in call)]))


if __name__ == "__main__":
    unittest.main()
