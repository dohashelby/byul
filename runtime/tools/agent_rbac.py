"""Least-privilege Kubernetes permissions for the runtime-agent account.

One list (REQUIRED) is the source of truth for both:
  render  -> Role/ClusterRole/bindings JSON for the agent's ServiceAccount
  check   -> `kubectl auth can-i` for every required permission (must be yes)
             and for a set of permissions the agent must NOT have (should be no)

Who needs what (runtime-agent host services, not the Gateway):
  enforcement sync / tetragon_mode : TracingPolicyNamespaced and CiliumNetworkPolicy CRUD in the
                                     serving namespace, Cilium CRD get, server-side dry-run (patch)
  agent / tetragon_status          : Tetragon pods + `tetra` exec in kube-system, policy list in
                                     all namespaces, daemonset get
  bundle apply (sudo) / status     : Gateway rollout restart+status, port-forward
  engine_receipts (optional)       : engine pod logs
Nothing here reads Secrets, ConfigMaps, nodes, or changes RBAC.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any, Callable

NAME = re.compile(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?")

# (scope, verb, resource, api group, reason). scope: "ns" serving namespace, "tetragon" its namespace, "cluster".
REQUIRED: tuple[tuple[str, str, str, str, str], ...] = (
    ("ns", "get", "tracingpoliciesnamespaced", "cilium.io", "정책 조회·백업"),
    ("ns", "list", "tracingpoliciesnamespaced", "cilium.io", "정책 목록"),
    ("ns", "create", "tracingpoliciesnamespaced", "cilium.io", "정책 처음 적용"),
    ("ns", "update", "tracingpoliciesnamespaced", "cilium.io", "정책 교체·복원"),
    ("ns", "patch", "tracingpoliciesnamespaced", "cilium.io", "server-side dry-run"),
    ("ns", "delete", "tracingpoliciesnamespaced", "cilium.io", "모드 전환·생성 취소"),
    ("ns", "get", "ciliumnetworkpolicies", "cilium.io", "Cilium 정책 조회"),
    ("ns", "list", "ciliumnetworkpolicies", "cilium.io", "Cilium 정책 목록"),
    ("ns", "create", "ciliumnetworkpolicies", "cilium.io", "Cilium 적용(명시 승인 때만)"),
    ("ns", "update", "ciliumnetworkpolicies", "cilium.io", "Cilium 교체·복원"),
    ("ns", "patch", "ciliumnetworkpolicies", "cilium.io", "server-side dry-run·diff"),
    ("ns", "delete", "ciliumnetworkpolicies", "cilium.io", "생성 취소"),
    ("ns", "get", "deployments", "apps", "Gateway 포트포워드·rollout"),
    ("ns", "list", "deployments", "apps", "rollout status"),
    ("ns", "watch", "deployments", "apps", "rollout status"),
    ("ns", "patch", "deployments", "apps", "Gateway rollout restart(묶음 적용)"),
    ("ns", "get", "pods", "", "포트포워드·로그 대상"),
    ("ns", "list", "pods", "", "포트포워드·로그 대상"),
    ("ns", "watch", "pods", "", "포트포워드 대상 Pod 생성 대기"),
    ("ns", "create", "pods/portforward", "", "Gateway 상태 확인"),
    ("ns", "get", "pods/log", "", "엔진 수신 기록(선택)"),
    ("tetragon", "get", "pods", "", "Tetragon 에이전트 조회"),
    ("tetragon", "list", "pods", "", "Tetragon 에이전트 조회"),
    ("tetragon", "watch", "pods", "", "daemonset exec 대상 Pod 생성 대기"),
    ("tetragon", "get", "daemonsets", "apps", "Tetragon daemonset"),
    ("tetragon", "create", "pods/exec", "", "tetra 정책 모드 조회"),
    ("cluster", "list", "tracingpoliciesnamespaced", "cilium.io", "전체 정책 상태 수집(-A)"),
    ("cluster", "get", "customresourcedefinitions", "apiextensions.k8s.io", "Cilium 설치 여부"),
)

# The agent must not be able to do these. "yes" means the account is broader than needed.
FORBIDDEN: tuple[tuple[str, str, str, str], ...] = (
    ("ns", "get", "secrets", ""),
    ("tetragon", "get", "secrets", ""),
    ("ns", "get", "configmaps", ""),
    ("ns", "create", "pods/exec", ""),          # no shell into the engine or Gateway
    ("ns", "get", "pods/exec", ""),             # WebSocket exec must also be forbidden
    ("ns", "delete", "pods", ""),
    ("ns", "create", "deployments", "apps"),
    ("ns", "delete", "deployments", "apps"),
    ("tetragon", "delete", "pods", ""),
    ("tetragon", "patch", "daemonsets", "apps"),
    ("cluster", "list", "nodes", ""),
    ("cluster", "create", "clusterrolebindings", "rbac.authorization.k8s.io"),
    ("cluster", "create", "namespaces", ""),
    ("cluster", "patch", "customresourcedefinitions", "apiextensions.k8s.io"),
    ("cluster", "create", "tracingpolicies", "cilium.io"),  # cluster-wide policies are out of scope
)

CRD_NAMES = ["ciliumnetworkpolicies.cilium.io"]
RESOURCE_NAMES = {"customresourcedefinitions": CRD_NAMES,
                  "deployments": ["serving-gateway"], "daemonsets": ["tetragon"]}


def _split(resource: str) -> tuple[str, str | None]:
    base, _, sub = resource.partition("/")
    return base, sub or None


def _rules(entries) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for verb, resource, group in entries:
        grouped.setdefault((group, resource), set()).add(verb)
    rules = []
    for (group, resource), verbs in sorted(grouped.items()):
        rule = {"apiGroups": [group], "resources": [resource], "verbs": sorted(verbs)}
        if resource in RESOURCE_NAMES:
            rule["resourceNames"] = RESOURCE_NAMES[resource]
        rules.append(rule)
    return rules


def render(namespace: str, tetragon_namespace: str, account: str = "runtime-agent") -> list[dict[str, Any]]:
    """Return ServiceAccount, Roles, ClusterRole and bindings (kubectl accepts JSON)."""
    for value in (namespace, tetragon_namespace, account):
        if not NAME.fullmatch(value):
            raise ValueError("invalid name")
    by_scope: dict[str, list[tuple[str, str, str]]] = {"ns": [], "tetragon": [], "cluster": []}
    for scope, verb, resource, group, _ in REQUIRED:
        by_scope[scope].append((verb, resource, group))
    subject = [{"kind": "ServiceAccount", "name": account, "namespace": namespace}]
    labels = {"app.kubernetes.io/part-of": "byul-runtime", "app.kubernetes.io/component": "runtime-agent"}
    name = f"byul-{account}"

    def meta(ns: str | None = None) -> dict[str, Any]:
        out = {"name": name, "labels": labels}
        if ns:
            out["namespace"] = ns
        return out

    objects = [{"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {**meta(namespace), "name": account},
                "automountServiceAccountToken": False}]
    for ns, scope in ((namespace, "ns"), (tetragon_namespace, "tetragon")):
        objects.append({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": meta(ns),
                        "rules": _rules(by_scope[scope])})
        objects.append({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": meta(ns),
                        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": name},
                        "subjects": subject})
    objects.append({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": meta(),
                    "rules": _rules(by_scope["cluster"])})
    objects.append({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": meta(),
                    "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": name},
                    "subjects": subject})
    return objects


def _can_i(base: list[str], scope: str, verb: str, resource: str, group: str,
           namespace: str, tetragon_namespace: str) -> list[str]:
    base_resource, sub = _split(resource)
    target = f"{base_resource}.{group}" if group else base_resource
    if base_resource in RESOURCE_NAMES and (scope, verb, resource, group) in {r[:4] for r in REQUIRED}:
        target += "/" + RESOURCE_NAMES[base_resource][0]
    command = base + ["auth", "can-i", verb, target]
    if sub:  # "pods/log" would mean "the pod named log"; subresources need the flag
        command += ["--subresource", sub]
    if scope == "cluster":
        command += ["--all-namespaces"]
    else:
        command += ["-n", namespace if scope == "ns" else tetragon_namespace]
    return command


def check(context: str, namespace: str, tetragon_namespace: str, *, kubeconfig: str | None = None,
          as_user: str | None = None, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    base = ["kubectl"] + (["--kubeconfig", kubeconfig] if kubeconfig else []) + ["--context", context]
    if as_user:  # an administrator checks the ServiceAccount before handing its kubeconfig to the agent
        base += ["--as", as_user]

    def ask(scope, verb, resource, group) -> str:
        command = _can_i(base, scope, verb, resource, group, namespace, tetragon_namespace)
        try:
            result = runner(command, capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError):
            return "error"
        answer = (result.stdout or "").strip().splitlines()[:1]
        if answer == ["yes"]:
            return "yes"
        if answer == ["no"]:
            return "no"
        return "error"  # never print kubectl stderr (may contain server names)

    missing, unknown, too_broad = [], [], []
    for scope, verb, resource, group, reason in REQUIRED:
        answer = ask(scope, verb, resource, group)
        item = {"scope": scope, "verb": verb, "resource": resource, "group": group, "reason": reason}
        if answer == "no":
            missing.append(item)
        elif answer == "error":
            unknown.append(item)
    for scope, verb, resource, group in FORBIDDEN:
        answer = ask(scope, verb, resource, group)
        item = {"scope": scope, "verb": verb, "resource": resource, "group": group}
        if answer == "yes":
            too_broad.append(item)
        elif answer == "error":
            unknown.append(item)
    state = "missing" if missing else "unknown" if unknown else "too_broad" if too_broad else "ok"
    return {"schema_version": "runtime-agent-rbac-check/1.0", "state": state,
            "required": len(REQUIRED), "forbidden_probes": len(FORBIDDEN),
            "missing": missing, "too_broad": too_broad, "unknown": unknown}


EXIT = {"ok": 0, "missing": 1, "unknown": 1, "too_broad": 2}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    r = commands.add_parser("render", help="최소 권한 Role/ClusterRole JSON 출력")
    c = commands.add_parser("check", help="kubectl auth can-i로 필요한 권한·과한 권한 점검")
    for p in (r, c):
        p.add_argument("--namespace", default="serving-lab")
        p.add_argument("--tetragon-namespace", default="kube-system")
    r.add_argument("--account", default="runtime-agent")
    c.add_argument("--context", required=True)
    c.add_argument("--kubeconfig")
    c.add_argument("--as", dest="as_user", help="관리자 kubeconfig로 대상 계정 흉내(예: system:serviceaccount:serving-lab:runtime-agent)")
    args = parser.parse_args(argv)
    if not NAME.fullmatch(args.namespace) or not NAME.fullmatch(args.tetragon_namespace):
        parser.error("invalid namespace")
    if args.command == "render":
        print(json.dumps({"apiVersion": "v1", "kind": "List",
                          "items": render(args.namespace, args.tetragon_namespace, args.account)},
                         ensure_ascii=False, indent=1))
        return 0
    result = check(args.context, args.namespace, args.tetragon_namespace, kubeconfig=args.kubeconfig, as_user=args.as_user)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return EXIT[result["state"]]


if __name__ == "__main__":
    sys.exit(main())
