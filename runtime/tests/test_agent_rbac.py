from __future__ import annotations

import json
import subprocess
import unittest

from tools import agent_rbac


def allowed(objects, scope, verb, resource, group, namespace="serving-lab", tetragon="kube-system"):
    """Tiny RBAC evaluator for the rendered objects (exact names, no wildcards)."""
    kinds = {"ns": ("Role", namespace), "tetragon": ("Role", tetragon), "cluster": ("ClusterRole", None)}
    kind, ns = kinds[scope]
    for obj in objects:
        if obj["kind"] != kind or (ns and obj["metadata"].get("namespace") != ns):
            continue
        for rule in obj["rules"]:
            if group in rule["apiGroups"] and resource in rule["resources"] and verb in rule["verbs"]:
                return True
    return False


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.objects = agent_rbac.render("serving-lab", "kube-system")

    def test_every_required_permission_is_granted(self):
        for scope, verb, resource, group, _ in agent_rbac.REQUIRED:
            with self.subTest(scope=scope, verb=verb, resource=resource):
                self.assertTrue(allowed(self.objects, scope, verb, resource, group))

    def test_forbidden_permissions_are_not_granted(self):
        for scope, verb, resource, group in agent_rbac.FORBIDDEN:
            with self.subTest(scope=scope, verb=verb, resource=resource):
                self.assertFalse(allowed(self.objects, scope, verb, resource, group))

    def test_no_wildcards_secrets_or_rbac_changes(self):
        text = json.dumps(self.objects)
        self.assertNotIn('"*"', text)
        self.assertNotIn("secrets", text)
        self.assertNotIn("rolebindings", json.dumps([o.get("rules", []) for o in self.objects]))
        crd = [r for o in self.objects for r in o.get("rules", []) if "customresourcedefinitions" in r["resources"]]
        self.assertEqual(crd[0]["resourceNames"], ["ciliumnetworkpolicies.cilium.io"])
        self.assertEqual(crd[0]["verbs"], ["get"])

    def test_bindings_point_to_one_service_account(self):
        bindings = [o for o in self.objects if o["kind"].endswith("Binding")]
        self.assertEqual(len(bindings), 3)
        for b in bindings:
            self.assertEqual(b["subjects"], [{"kind": "ServiceAccount", "name": "runtime-agent", "namespace": "serving-lab"}])
        self.assertFalse(self.objects[0]["automountServiceAccountToken"])

    def test_rejects_bad_names(self):
        with self.assertRaises(ValueError):
            agent_rbac.render("Bad_NS", "kube-system")


class Runner:
    def __init__(self, answer):
        self.answer, self.commands = answer, []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        out = self.answer(command)
        if isinstance(out, BaseException):
            raise out
        return subprocess.CompletedProcess(command, 0, out, "server https://10.0.0.1 secret")


def least(command):
    """Answer like a cluster that applied exactly the rendered RBAC."""
    verb, target = command[command.index("can-i") + 1], command[command.index("can-i") + 2]
    if "--subresource" in command:
        target += "/" + command[command.index("--subresource") + 1]
    target = target.replace("/ciliumnetworkpolicies.cilium.io", "")
    for scope, v, resource, group, _ in agent_rbac.REQUIRED:
        base, _, sub = resource.partition("/")
        name = (f"{base}.{group}" if group else base) + (f"/{sub}" if sub else "")
        ns_ok = ("--all-namespaces" in command) == (scope == "cluster")
        if ns_ok and v == verb and name == target and (
                scope == "cluster" or command[-1] == ("serving-lab" if scope == "ns" else "kube-system")):
            return "yes\n"
    return "no\n"


class CheckTests(unittest.TestCase):
    def test_least_privilege_cluster_is_ok(self):
        run = Runner(least)
        result = agent_rbac.check("ctx", "serving-lab", "kube-system", kubeconfig="/k", runner=run)
        self.assertEqual(result["state"], "ok")
        self.assertEqual(len(run.commands), len(agent_rbac.REQUIRED) + len(agent_rbac.FORBIDDEN))
        self.assertEqual(run.commands[0][:5], ["kubectl", "--kubeconfig", "/k", "--context", "ctx"])
        self.assertIn(["auth", "can-i", "create", "pods", "--subresource", "exec", "-n", "kube-system"],
                      [c[5:] for c in run.commands])
        self.assertIn(["auth", "can-i", "get", "customresourcedefinitions.apiextensions.k8s.io/ciliumnetworkpolicies.cilium.io",
                       "--all-namespaces"], [c[5:] for c in run.commands])
        self.assertIn(["auth", "can-i", "list", "tracingpoliciesnamespaced.cilium.io", "--all-namespaces"],
                      [c[5:] for c in run.commands])

    def test_admin_like_account_is_too_broad(self):
        result = agent_rbac.check("ctx", "serving-lab", "kube-system", runner=Runner(lambda c: "yes\n"))
        self.assertEqual(result["state"], "too_broad")
        self.assertEqual(len(result["too_broad"]), len(agent_rbac.FORBIDDEN))
        self.assertEqual(agent_rbac.EXIT[result["state"]], 2)

    def test_missing_wins_and_errors_are_unknown_without_stderr(self):
        result = agent_rbac.check("ctx", "serving-lab", "kube-system", runner=Runner(lambda c: "no\n"))
        self.assertEqual(result["state"], "missing")
        self.assertEqual(len(result["missing"]), len(agent_rbac.REQUIRED))
        broken = agent_rbac.check("ctx", "serving-lab", "kube-system",
                                  runner=Runner(lambda c: subprocess.TimeoutExpired("kubectl", 20)))
        self.assertEqual(broken["state"], "unknown")
        self.assertNotIn("10.0.0.1", json.dumps(broken))


if __name__ == "__main__":
    unittest.main()
