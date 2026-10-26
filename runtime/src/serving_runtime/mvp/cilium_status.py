"""Explicit-context Cilium policy object status collector."""

from __future__ import annotations

import json
import subprocess
from typing import Any, Callable

from .kube_context import DEFAULT_KUBE_CONTEXT, validate_kube_context

NAMESPACE = "serving-lab"


def policy_command(context: str = DEFAULT_KUBE_CONTEXT) -> list[str]:
    return [
        "kubectl",
        "--context",
        validate_kube_context(context),
        "--namespace",
        NAMESPACE,
        "get",
        "ciliumnetworkpolicies",
        "-o",
        "json",
    ]


class CiliumPolicyCollector:
    def __init__(
        self,
        *,
        timeout_s: float,
        context: str = DEFAULT_KUBE_CONTEXT,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.timeout_s = timeout_s
        self.context = validate_kube_context(context)
        self.runner = runner

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        return self.runner(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
            check=False,
            shell=False,
        )

    def collect(self) -> tuple[dict[str, Any], str]:
        result = self._run(policy_command(self.context))
        if result.returncode != 0:
            raise OSError("CiliumNetworkPolicy query failed")
        try:
            document = json.loads(result.stdout)
        except (json.JSONDecodeError, RecursionError) as error:
            raise ValueError("CiliumNetworkPolicy response is not valid JSON") from error
        if not isinstance(document, dict) or not isinstance(document.get("items"), list):
            raise ValueError("CiliumNetworkPolicy response has no items array")
        policies: list[dict[str, Any]] = []
        for raw_item in document["items"]:
            if not isinstance(raw_item, dict):
                continue
            metadata = (
                raw_item.get("metadata")
                if isinstance(raw_item.get("metadata"), dict)
                else {}
            )
            status = (
                raw_item.get("status")
                if isinstance(raw_item.get("status"), dict)
                else {}
            )
            name = metadata.get("name")
            if not isinstance(name, str) or not name:
                continue
            raw_conditions = status.get("conditions")
            conditions = []
            if isinstance(raw_conditions, list):
                for condition in raw_conditions:
                    if not isinstance(condition, dict):
                        continue
                    conditions.append(
                        {
                            "type": condition.get("type")
                            if isinstance(condition.get("type"), str)
                            else None,
                            "status": condition.get("status")
                            if isinstance(condition.get("status"), str)
                            else None,
                            "reason": condition.get("reason")
                            if isinstance(condition.get("reason"), str)
                            else None,
                            "message": condition.get("message")
                            if isinstance(condition.get("message"), str)
                            else None,
                            "last_transition_time": condition.get("lastTransitionTime")
                            if isinstance(condition.get("lastTransitionTime"), str)
                            else None,
                        }
                    )
            raw_nodes = status.get("nodes")
            nodes = raw_nodes if isinstance(raw_nodes, dict) else {}
            node_values = [value for value in nodes.values() if isinstance(value, dict)]
            node_status = {
                "total": len(node_values),
                "enforcing": sum(value.get("enforcing") is True for value in node_values),
                "errors": sum(
                    isinstance(value.get("error"), str) and bool(value["error"])
                    for value in node_values
                ),
                "last_updated": max(
                    (
                        value["lastUpdated"]
                        for value in node_values
                        if isinstance(value.get("lastUpdated"), str)
                    ),
                    default=None,
                ),
            }
            policies.append(
                {
                    "name": name,
                    "conditions": conditions,
                    "status_present": bool(status),
                    "node_status": node_status,
                }
            )
        policies.sort(key=lambda item: item["name"])
        return (
            {
                "context": self.context,
                "namespace": NAMESPACE,
                "object_count": len(policies),
                "configured": bool(policies),
                "policies": policies,
                "meaning": (
                    "policy object presence is configuration state, not proof of "
                    "packet blocking"
                ),
            },
            "kubernetes_api",
        )
