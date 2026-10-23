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


def engine_ingress(intent: EnforcementIntent) -> dict[str, Any]:
    sources = [{"matchLabels": dict(intent.gateway.labels)}]
    sources.extend({"matchLabels": dict(item.labels)} for item in intent.metrics_collectors)
    return {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": _metadata(intent, "runtime-engine-ingress"),
        "spec": {
            "endpointSelector": {"matchLabels": dict(intent.engine.labels)},
            "ingress": [
                {
                    "fromEndpoints": sources,
                    "toPorts": [_port_rule(intent.engine.port or 8000, "TCP")],
                }
            ],
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
    rules: list[dict[str, Any]] = [_dns_egress(intent, inspect_names=has_fqdn)]
    for item in intent.engine_egress_allow:
        rule: dict[str, Any] = {
            "toPorts": [_port_rule(item.port, item.protocol)],
        }
        if item.fqdn is not None:
            rule["toFQDNs"] = [{"matchName": item.fqdn}]
        else:
            rule["toCIDR"] = [item.cidr]
        rules.append(rule)
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


def render_manifests(intent: EnforcementIntent) -> dict[str, dict[str, Any]]:
    """Build fixed-name JSON manifests without touching the filesystem."""

    return {
        "cilium-engine-ingress.json": engine_ingress(intent),
        "cilium-engine-egress.json": engine_egress(intent),
        "cilium-gateway-egress.json": gateway_egress(intent),
    }


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
