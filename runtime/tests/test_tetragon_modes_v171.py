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
        def legacy_bytes(document):
            manifests = render_manifests(parse_intent(document))
            # The new monitor is additive; preserve the exact legacy policy hash.
            manifests.pop("tetragon-tg-code-01.json")
            return json.dumps(manifests, ensure_ascii=False, separators=(",", ":"))
        before = legacy_bytes(original)
        self.assertEqual(hashlib.sha256(before.encode()).hexdigest(),
                         "58cbc5924122a2f61d525516eae8a3de5a321515f763abdd79272b24a1691d3e")
        self.assertEqual(before, legacy_bytes(copy.deepcopy(original)))
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
                                 ["Sigkill"] if method == "sigkill_fd_install" else ["Post", "Override"] if method == "override_syscall" else ["Post", "Override", "Sigkill"])
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

    def test_all_methods_fit_tetragon_argument_budget_with_exact_identity(self):
        for method in ("override_lsm", "sigkill_fd_install", "override_syscall"):
            with self.subTest(method=method):
                document = sample()
                document["protected_paths"]["secret_paths"] = ["/run/probe/token"]
                document["protected_paths"]["enforce_exact_files"] = ["/run/probe/token"]
                document["tetragon_rule_modes"] = {"TG-SECRET-01": "enforce"}
                document["tetragon_enforce_method"] = method
                if method == "override_syscall":
                    document["tetragon_openat_symbol"] = "__x64_sys_openat"
                plain = render_manifests(parse_intent(document))["tetragon-tg-secret-01.json"]
                document["tetragon_kernel_identity"] = True
                verified = render_manifests(parse_intent(document))["tetragon-tg-secret-01.json"]
                probe = verified["spec"]["kprobes"][0]
                self.assertLessEqual(len(probe.get("args", [])) + len(probe.get("data", [])), 5)
                self.assertEqual(probe["selectors"], plain["spec"]["kprobes"][0]["selectors"])
                self.assertEqual(probe["call"], plain["spec"]["kprobes"][0]["call"])
                self.assertEqual(len(probe["data"]), 3)
                if method == "override_syscall":
                    self.assertEqual(probe["args"], [{"index": 1, "type": "string"}])

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

    def replacement(self, *, mode="monitor", actual_spec=None, effect=True, load_state="enabled", existing=False, mutate=False, expected_mode="monitor"):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        manifest = Path(directory.name) / "new.json"
        document = {"apiVersion": "cilium.io/v1alpha1", "kind": "TracingPolicyNamespaced",
                    "metadata": {"name": "runtime-tg-secret-02", "namespace": "serving-lab"},
                    "spec": {"options": [{"name": "policy-mode", "value": expected_mode}],
                             "kprobes": [{"call": "fd_install"}]}}
        manifest.write_text(json.dumps(document))
        calls, state = [], {"applied": False, "verified": False}
        def run(command):
            calls.append(command)
            if "create" in command:
                state["applied"] = True
                created = copy.deepcopy(document)
                created["metadata"].update(uid="new-uid", resourceVersion="2")
                return Result(output=json.dumps(created))
            if command[-2:] == ["tracingpolicy", "list"]:
                rows = table() + table(name="runtime-tg-secret-02", mode=mode, state=load_state).split("\n", 1)[1]
                if mutate and state["verified"]:
                    rows = rows.replace("runtime-tg-secret-02 enabled", "runtime-tg-secret-02 disabled")
                return Result(output=rows)
            if "get" in command:
                if "runtime-tg-secret-01" in command:
                    old = copy.deepcopy(document)
                    old["metadata"].update(name="runtime-tg-secret-01", uid="old-uid", resourceVersion="1")
                    return Result(output=json.dumps(old))
                if not state["applied"] and not existing:
                    return Result()
                actual = copy.deepcopy(document)
                actual["metadata"].update(uid="new-uid", resourceVersion="2")
                if actual_spec is not None:
                    actual["spec"] = actual_spec
                return Result(output=json.dumps(actual))
            if "delete" in command:
                options = json.loads(Path(command[-1]).read_text())
                self.assertIn(options["preconditions"]["uid"], {"new-uid", "old-uid"})
                calls[-1] = [*command, options["preconditions"]["uid"]]
            return Result()
        def verify(row):
            self.assertEqual(row["name"], "runtime-tg-secret-02")
            calls.append(["effect-probe"])
            state["verified"] = True
            if isinstance(effect, Exception):
                raise effect
            return effect
        outcome = replace_after_load("ctx", "serving-lab", "runtime-tg-secret-01", manifest,
                                     expected_mode=expected_mode, verify_effect=verify, run=run)
        return outcome, calls

    def test_replacement_verifies_spec_mode_and_live_effect_before_old_removal(self):
        outcome, calls = self.replacement()
        self.assertTrue(outcome["ok"])
        self.assertLess(calls.index(["effect-probe"]), next(i for i, c in enumerate(calls) if "delete" in c))
        self.assertTrue(any("/runtime-tg-secret-01" in token for token in calls[-1]))

    def test_replacement_mismatch_or_missing_effect_keeps_old(self):
        for params in ({"mode": "enforce"}, {"load_state": "load_error"}, {"actual_spec": {"kprobes": []}},
                       {"effect": False}, {"effect": None}, {"effect": RuntimeError("probe failed")}, {"mutate": True}):
            with self.subTest(params=params):
                outcome, calls = self.replacement(**params)
                self.assertFalse(outcome["ok"])
                deletes = [c for c in calls if "delete" in c]
                self.assertEqual(len(deletes), 1)
                self.assertTrue(any("/runtime-tg-secret-02" in token for token in deletes[0]))
                self.assertFalse(any("/runtime-tg-secret-01" in token for token in deletes[0]))

    def test_replacement_allows_explicit_mode_change(self):
        outcome, _ = self.replacement(mode="enforce", expected_mode="enforce")
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["old"]["mode"], "monitor")
        self.assertEqual(outcome["new"]["mode"], "enforce")

    def test_replacement_waits_for_async_load_before_effect(self):
        real_read = __import__("tools.tetragon_mode", fromlist=["_read_policy"])._read_policy
        from unittest.mock import patch
        reads = []
        def delayed(run, context, tetragon_namespace, policy_name, policy_namespace):
            if policy_name == "runtime-tg-secret-02":
                reads.append(policy_name)
                if len(reads) == 1:
                    return None
                if len(reads) == 2:
                    return {"state": "loading"}
            return real_read(run, context, tetragon_namespace, policy_name, policy_namespace)
        with patch("tools.tetragon_mode._read_policy", side_effect=delayed):
            outcome, calls = self.replacement()
        self.assertTrue(outcome["ok"])
        self.assertGreaterEqual(len(reads), 3)
        self.assertEqual(calls.count(["effect-probe"]), 1)

    def test_replacement_create_conflict_keeps_concurrent_object(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "new.json"
            manifest.write_text(json.dumps({"kind": "TracingPolicyNamespaced",
                "metadata": {"name": "new", "namespace": "serving-lab"},
                "spec": {"options": [{"name": "policy-mode", "value": "monitor"}], "kprobes": [{"call": "fd_install"}]}}))
            calls = []
            def run(command):
                calls.append(command)
                if command[-2:] == ["tracingpolicy", "list"]:
                    return Result(output=table(name="old"))
                if "get" in command and "old" in command:
                    return Result(output=json.dumps({"metadata": {"name": "old", "namespace": "serving-lab", "uid": "old-uid", "resourceVersion": "1"}}))
                if "get" in command:
                    return Result()
                if "create" in command:
                    return Result(code=1)  # a competing actor won the name
                self.fail("unexpected mutation: " + str(command))
            result = replace_after_load("ctx", "serving-lab", "old", manifest,
                expected_mode="monitor", verify_effect=lambda _: True, run=run)
            self.assertFalse(result["ok"])
            self.assertTrue(any("create" in c for c in calls))
            self.assertFalse(any("delete" in c or "apply" in c for c in calls))

    def test_replacement_existing_name_is_not_overwritten_or_deleted(self):
        outcome, calls = self.replacement(existing=True)
        self.assertFalse(outcome["ok"])
        self.assertFalse(any("create" in c or "delete" in c for c in calls))


if __name__ == "__main__":
    unittest.main()
