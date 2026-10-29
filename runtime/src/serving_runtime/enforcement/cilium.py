"""Cilium 1.20.3 CiliumNetworkPolicy candidate renderer.

The returned policies contain explicit ingress or egress sections. For selected
endpoints that makes the corresponding direction default-deny except for the
rendered rules. These candidates are not applied to or validated by a cluster.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .intent import EnforcementIntent


_ANNOTATIONS = {
    "serving-runtime.local/status": "candidate-cluster-unapplied-unverified",
    "serving-runtime.local/syntax-target": "Cilium 1.20.3",
}


def _metadata(intent: EnforcementIntent, name: str) -> dict[str, Any]:
    return {"name": name, "namespace": intent.namespace, "annotations": dict(_ANNOTATIONS)}


def _dns_endpoint(intent: EnforcementIntent) -> dict[str, Any]:
    labels = {"k8s:io.kubernetes.pod.namespace": intent.dns.namespace}
    labels.update(
        {
            key if ":" in key else f"k8s:{key}": value
            for key, value in intent.dns.labels.items()
        }
    )
    return {"matchLabels": labels}


def _port_rule(port: int, protocol: str) -> dict[str, Any]:
    return {"ports": [{"port": str(port), "protocol": protocol}]}


def _observer_port(intent: EnforcementIntent) -> int:
    return intent.engine.management_port or intent.engine.port or 8000


def engine_ingress(intent: EnforcementIntent) -> dict[str, Any]:
    gateway = {"matchLabels": dict(intent.gateway.labels)}
    observers = [{"matchLabels": dict(item.labels)} for item in intent.metrics_collectors]
    api_port = intent.engine.port or 8000
    if intent.engine.management_port is None:
        # 1.0 behaviour: Gateway and observers share the engine API port.
        rules = [{"fromEndpoints": [gateway, *observers], "toPorts": [_port_rule(api_port, "TCP")]}]
    else:
        # SP4: observers reach only the read-only management listener.
        rules = [{"fromEndpoints": [gateway], "toPorts": [_port_rule(api_port, "TCP")]}]
        if observers:
            rules.append(
                {
                    "fromEndpoints": observers,
                    "toPorts": [_port_rule(intent.engine.management_port, "TCP")],
                }
            )
    return {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": _metadata(intent, "runtime-engine-ingress"),
        "spec": {
            "endpointSelector": {"matchLabels": dict(intent.engine.labels)},
            "ingress": rules,
        },
    }


def _dns_egress(intent: EnforcementIntent, *, inspect_names: bool) -> dict[str, Any]:
    port_rule = {
        "ports": [
            {"port": str(intent.dns.port), "protocol": "UDP"},
            {"port": str(intent.dns.port), "protocol": "TCP"},
        ]
    }
    if inspect_names:
        port_rule["rules"] = {"dns": [{"matchPattern": "*"}]}
    return {"toEndpoints": [_dns_endpoint(intent)], "toPorts": [port_rule]}


def engine_egress(intent: EnforcementIntent) -> dict[str, Any]:
    has_fqdn = any(item.fqdn is not None for item in intent.engine_egress_allow)
    rules: list[dict[str, Any]] = []
    if has_fqdn:
        rules.append(_dns_egress(intent, inspect_names=True))
    for item in intent.engine_egress_allow:
        rule: dict[str, Any] = {
            "toPorts": [_port_rule(item.port, item.protocol)],
        }
        if item.fqdn is not None:
            rule["toFQDNs"] = [{"matchName": item.fqdn}]
        else:
            rule["toCIDR"] = [item.cidr]
        rules.append(rule)
    if not rules:
        # Cilium's explicit empty rule enables egress default-deny without
        # allowing any destination.
        rules.append({})
    return {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": _metadata(intent, "runtime-engine-egress"),
        "spec": {
            "endpointSelector": {"matchLabels": dict(intent.engine.labels)},
            "egress": rules,
        },
    }


def gateway_egress(intent: EnforcementIntent) -> dict[str, Any]:
    return {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": _metadata(intent, "runtime-gateway-egress"),
        "spec": {
            "endpointSelector": {"matchLabels": dict(intent.gateway.labels)},
            "egress": [
                {
                    "toEndpoints": [{"matchLabels": dict(intent.engine.labels)}],
                    "toPorts": [_port_rule(intent.engine.port or 8000, "TCP")],
                },
                _dns_egress(intent, inspect_names=False),
            ],
        },
    }


def _egress_rules(items) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for item in items:
        rule: dict[str, Any] = {"toPorts": [_port_rule(item.port, item.protocol)]}
        if item.fqdn is not None:
            rule["toFQDNs"] = [{"matchName": item.fqdn}]
        elif item.cidr is not None:
            rule["toCIDR"] = [item.cidr]
        else:
            rule["toEndpoints"] = [{"matchLabels": dict(item.endpoint)}]
        rules.append(rule)
    return rules


def acquire_egress(intent: EnforcementIntent) -> dict[str, Any]:
    """SP4: only the acquisition task may reach the approved artifact sources."""

    assert intent.acquire is not None
    has_fqdn = any(item.fqdn is not None for item in intent.acquire.egress_allow)
    return {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": _metadata(intent, "runtime-acquire-egress"),
        "spec": {
            "endpointSelector": {"matchLabels": dict(intent.acquire.labels)},
            "egress": [
                _dns_egress(intent, inspect_names=has_fqdn),
                *_egress_rules(intent.acquire.egress_allow),
            ],
        },
    }


def observer_egress(intent: EnforcementIntent, collector, index: int) -> dict[str, Any]:
    """SP4: one observer reaches the engine management listener and nothing else."""

    rules: list[dict[str, Any]] = [
        {
            "toEndpoints": [{"matchLabels": dict(intent.engine.labels)}],
            "toPorts": [_port_rule(_observer_port(intent), "TCP")],
        },
        _dns_egress(intent, inspect_names=False),
    ]
    if intent.observer_kube_api:
        # Plan §4.3: the registered API port only, not every port of the entity.
        rules.append(
            {
                "toEntities": ["kube-apiserver"],
                "toPorts": [_port_rule(intent.observer_kube_api_port, "TCP")],
            }
        )
    for receiver in intent.test_receivers:
        if "observer" in receiver.allowed_from:
            rules.append(
                {
                    "toEndpoints": [{"matchLabels": dict(receiver.labels)}],
                    "toPorts": [_port_rule(receiver.port, receiver.protocol)],
                }
            )
    return {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": _metadata(intent, f"runtime-observer-{index}-egress"),
        "spec": {
            "endpointSelector": {"matchLabels": dict(collector.labels)},
            "egress": rules,
        },
    }


def receiver_ingress(intent: EnforcementIntent, receiver) -> dict[str, Any]:
    """A test fixture accepts only the task roles that are meant to reach it."""

    sources: list[dict[str, Any]] = []
    if "acquire" in receiver.allowed_from and intent.acquire is not None:
        sources.append({"matchLabels": dict(intent.acquire.labels)})
    if "observer" in receiver.allowed_from:
        sources.extend({"matchLabels": dict(item.labels)} for item in intent.metrics_collectors)
    return {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": _metadata(intent, f"runtime-receiver-{receiver.name}-ingress"),
        "spec": {
            "endpointSelector": {"matchLabels": dict(receiver.labels)},
            "ingress": [
                {"fromEndpoints": sources, "toPorts": [_port_rule(receiver.port, receiver.protocol)]}
            ],
        },
    }


def render_manifests(intent: EnforcementIntent) -> dict[str, dict[str, Any]]:
    """Build fixed-name JSON manifests without touching the filesystem."""

    manifests = {
        "cilium-engine-ingress.json": engine_ingress(intent),
        "cilium-engine-egress.json": engine_egress(intent),
        "cilium-gateway-egress.json": gateway_egress(intent),
    }
    if intent.acquire is not None:
        manifests["cilium-acquire-egress.json"] = acquire_egress(intent)
    if intent.schema != "runtime-enforcement-intent/1.0":
        for index, collector in enumerate(intent.metrics_collectors):
            manifests[f"cilium-observer-{index}-egress.json"] = observer_egress(
                intent, collector, index
            )
    for receiver in intent.test_receivers:
        manifests[f"cilium-receiver-{receiver.name}-ingress.json"] = receiver_ingress(intent, receiver)
    return manifests


def write_manifests(
    manifests: dict[str, dict[str, Any]], out_dir: str | Path
) -> list[Path]:
    """Write fixed-name manifests, rejecting any path that could escape ``out_dir``."""

    output = Path(out_dir)
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = output.resolve()
    written: list[Path] = []
    for name, document in manifests.items():
        if Path(name).name != name:
            raise ValueError("manifest name must not contain a path")
        target = output / name
        resolved = target.resolve()
        if resolved.parent != root:
            raise ValueError("manifest target escapes output directory")
        target.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append(target)
    return written
