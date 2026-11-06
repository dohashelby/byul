import copy
import hashlib
import json
import subprocess
from pathlib import Path
import tempfile
import unittest

from serving_runtime.enforcement.intent import IntentError, parse_intent
from serving_runtime.enforcement.events import parse_tetragon
from serving_runtime.enforcement.tetragon import render_manifests
from tools.tetragon_capability import classify_capabilities
from tools.tetragon_mode import parse_policy_row, replace_after_load, switch_mode


ROOT = Path(__file__).resolve().parents[1]


def sample():
    return json.loads((ROOT / "examples/enforcement/intent-v1.1.example.json").read_text())


def table(name="runtime-tg-secret-01", mode="monitor", id="7", state="enabled"):
    return ("ID NAME STATE FILTERID NAMESPACE SENSORS KERNELMEMORY MODE\n"
            f"{id} {name} {state} 1 serving-lab generic_kprobe 1.00MB {mode}\n")


class Result:
    def __init__(self, code=0, output=""):
        self.returncode = code
        self.stdout = output


class MethodTests(unittest.TestCase):
    def test_legacy_bytes_and_explicit_extensionless_file(self):
        original = sample()
        before = json.dumps(render_manifests(parse_intent(original)), ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(hashlib.sha256(before.encode()).hexdigest(),
                         "58cbc5924122a2f61d525516eae8a3de5a321515f763abdd79272b24a1691d3e")
        self.assertEqual(before, json.dumps(render_manifests(parse_intent(copy.deepcopy(original))), ensure_ascii=False, separators=(",", ":")))
        original["protected_paths"]["secret_paths"] = ["/var/run/secrets/serviceaccount/token"]
        original["protected_paths"]["enforce_exact_files"] = ["/var/run/secrets/serviceaccount/token"]
        original["tetragon_rule_modes"] = {"TG-SECRET-01": "enforce"}
        original["tetragon_enforce_method"] = "sigkill_fd_install"
        policy = render_manifests(parse_intent(original))["tetragon-tg-secret-01.json"]
        self.assertEqual(policy["spec"]["options"], [{"name": "policy-mode", "value": "monitor"}])
        self.assertEqual(policy["spec"]["kprobes"][0]["call"], "fd_install")
        self.assertEqual(policy["spec"]["kprobes"][0]["selectors"][0]["matchActions"], [{"action": "Sigkill"}])

    def test_three_methods_and_file_declaration(self):
        for method in ("override_lsm", "sigkill_fd_install", "override_syscall"):
            with self.subTest(method=method):
                intent = sample()
                intent["protected_paths"]["secret_paths"] = ["/run/probe/token"]
                intent["protected_paths"]["enforce_exact_files"] = ["/run/probe/token"]
                intent["tetragon_rule_modes"] = {"TG-SECRET-01": "enforce"}
                intent["tetragon_enforce_method"] = method
                if method == "override_syscall":
                    intent["tetragon_openat_symbol"] = "__x64_sys_openat"
                policy = render_manifests(parse_intent(intent))["tetragon-tg-secret-01.json"]
                actions = policy["spec"]["kprobes"][0]["selectors"][0]["matchActions"]
                self.assertEqual([action["action"] for action in actions],
                                 ["Sigkill"] if method == "sigkill_fd_install" else ["Post", "Override", "Sigkill"])
                self.assertEqual(policy["metadata"]["annotations"]["serving-runtime.local/enforce-method"], method)
                if method == "override_syscall":
                    self.assertIn("relative paths", policy["metadata"]["annotations"]["serving-runtime.local/limit"])
        bad = sample()
        bad["protected_paths"]["secret_paths"] = ["/run/probe"]
        bad["protected_paths"]["enforce_exact_files"] = ["/run/probe", "/run/probe/token"]
        bad["tetragon_rule_modes"] = {"TG-SECRET-01": "enforce"}
        bad["tetragon_enforce_method"] = "sigkill_fd_install"
        with self.assertRaises(IntentError):
            parse_intent(bad)

    def test_fourth_run_cannot_claim_lsm(self):
        capability = classify_capabilities(kernel="7.0.0-test", config="CONFIG_BPF_KPROBE_OVERRIDE=y\nCONFIG_BPF_LSM=y\n",
                                           lsm="lockdown,yama,apparmor", injection_list="__x64_sys_openat [sys_openat]",
                                           probe="override_return: true\nlsm: false")
        self.assertIn("override_lsm", capability["unavailable"])
        self.assertIn("override_syscall", capability["available"])
        self.assertEqual(capability["recommended"], "override_syscall")
        self.assertIs(capability["apparmor"], True)

    def test_sigkill_fd_install_event_is_enforcement_attempt(self):
        raw = {"time": "2026-11-05T00:00:00Z", "process_kprobe": {
            "function_name": "fd_install", "policy_name": "runtime-tg-secret-01-l05c",
            "process": {"pod": {"uid": "probe-uid", "namespace": "serving-lab", "pod_name": "probe"}},
            "args": [{"int_arg": 3}, {"file_arg": {"path": "/run/probe/token"}}],
            "action": "KPROBE_ACTION_SIGKILL"}}
        observation = parse_tetragon(raw, data_origin="synthetic")
        self.assertEqual((observation["rule_id"], observation["action_mode"], observation["access"]),
                         ("TG-SECRET-01", "enforce", "open"))


class ModeTests(unittest.TestCase):
    def test_real_v171_table_memory_unit_does_not_shift_mode(self):
        output = ("ID NAME STATE FILTERID NAMESPACE SENSORS KERNELMEMORY MODE NPOST NENFORCE NMONITOR\n"
                  "13 runtime-tg-secret-01 enabled 13 serving-lab generic_kprobe 2.00 MB monitor 0 0 0\n")
        self.assertEqual(parse_policy_row(output, "runtime-tg-secret-01", "serving-lab")["mode"], "monitor")

    def test_parse_and_switch_without_delete(self):
        self.assertEqual(parse_policy_row(table(), "runtime-tg-secret-01", "serving-lab")["mode"], "monitor")
        calls = []
        outputs = [table(mode="monitor"), table(mode="enforce")]
        def run(command):
            calls.append(command)
            return Result(output=outputs.pop(0)) if command[-2:] == ["tracingpolicy", "list"] else Result()
        outcome = switch_mode("ctx", "serving-lab", "runtime-tg-secret-01", "enforce", run=run)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["before"]["id"], outcome["after"]["id"])
        self.assertFalse(any("delete" in command for command in calls))

    def test_failed_switch_verifies_rollback(self):
        outputs = [table(mode="monitor"), table(mode="monitor"), table(mode="monitor")]
        changes = []
        def run(command):
            if command[-2:] == ["tracingpolicy", "list"]:
                return Result(output=outputs.pop(0))
            changes.append(command)
            return Result(code=1) if command[-1] == "enforce" else Result()
        outcome = switch_mode("ctx", "serving-lab", "runtime-tg-secret-01", "enforce", run=run)
        self.assertFalse(outcome["ok"])
        self.assertTrue(outcome["rollback_ok"])
        self.assertEqual([command[-1] for command in changes], ["enforce", "monitor"])

    def test_timeout_after_switch_still_attempts_and_verifies_rollback(self):
        for rollback_fails in (False, True):
            with self.subTest(rollback_fails=rollback_fails):
                mode = "monitor"
                changes = []
                def run(command):
                    nonlocal mode
                    if command[-2:] == ["tracingpolicy", "list"]:
                        return Result(output=table(mode=mode))
                    changes.append(command[-1])
                    if command[-1] == "enforce":
                        mode = "enforce"
                        raise subprocess.TimeoutExpired(command, 70)
                    if rollback_fails:
                        raise OSError("kubectl unavailable")
                    mode = "monitor"
                    return Result()
                outcome = switch_mode("ctx", "serving-lab", "runtime-tg-secret-01", "enforce", run=run)
                self.assertFalse(outcome["ok"])
                self.assertEqual(outcome["rollback_ok"], not rollback_fails)
                self.assertEqual(changes, ["enforce", "monitor"])

    def test_current_state_timeout_prevents_mode_mutation(self):
        calls = []
        def run(command):
            calls.append(command)
            raise subprocess.TimeoutExpired(command, 70)
        outcome = switch_mode("ctx", "serving-lab", "runtime-tg-secret-01", "enforce", run=run)
        self.assertFalse(outcome["ok"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-2:], ["tracingpolicy", "list"])

    def test_replacement_loads_new_before_removing_old(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "new.json"
            manifest.write_text(json.dumps({"metadata": {"name": "runtime-tg-secret-02", "namespace": "serving-lab"}}))
            calls = []
            def run(command):
                calls.append(command)
                if command[-2:] == ["tracingpolicy", "list"]:
                    name = "runtime-tg-secret-01" if len(calls) == 1 else "runtime-tg-secret-02"
                    return Result(output=table(name=name))
                return Result()
            outcome = replace_after_load("ctx", "serving-lab", "runtime-tg-secret-01", manifest, run=run)
            self.assertTrue(outcome["ok"])
            self.assertLess(next(i for i, c in enumerate(calls) if "apply" in c), next(i for i, c in enumerate(calls) if "delete" in c))

    def test_replacement_load_failure_keeps_old_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "new.json"
            manifest.write_text(json.dumps({"metadata": {"name": "runtime-tg-secret-02", "namespace": "serving-lab"}}))
            calls = []
            def run(command):
                calls.append(command)
                if command[-2:] == ["tracingpolicy", "list"]:
                    return Result(output=table() if len([c for c in calls if c[-2:] == ["tracingpolicy", "list"]]) == 1
                                  else table(name="runtime-tg-secret-02", state="load_error"))
                return Result()
            outcome = replace_after_load("ctx", "serving-lab", "runtime-tg-secret-01", manifest, run=run)
            self.assertFalse(outcome["ok"])
            deletes = [command for command in calls if "delete" in command]
            self.assertEqual(len(deletes), 1)
            self.assertIn("runtime-tg-secret-02", deletes[0])
            self.assertNotIn("runtime-tg-secret-01", deletes[0])


if __name__ == "__main__":
    unittest.main()
