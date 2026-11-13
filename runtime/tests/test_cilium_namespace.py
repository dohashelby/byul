"""Cilium queries use the registered namespace, including non-lab targets."""
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock
import pytest

from serving_runtime.observation.cilium_status import CiliumPolicyCollector, policy_command
from serving_runtime.gateway.app import GatewayApp
from serving_runtime.config.config import MVPConfig
from serving_runtime.policy.policy import load_policy

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "examples/mvp/policy.json"


def test_cilium_scope_is_explicit_and_missing_namespace_never_queries():
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, '{"items": []}', ''))
    with pytest.raises(ValueError, match="Cilium namespace is required; set target kubernetes.namespace"):
        CiliumPolicyCollector(timeout_s=1, context="formal", runner=runner)
    runner.assert_not_called()
    collector = CiliumPolicyCollector(timeout_s=1, context="formal", namespace="payments", runner=runner)
    value, _ = collector.collect()
    assert runner.call_args.args[0] == policy_command("formal", "payments")
    assert value["context"] == "formal"
    assert value["namespace"] == "payments"
    for namespace in ("Bad_NS", "--other"):
        with pytest.raises(ValueError, match="Cilium namespace must be a Kubernetes name"):
            policy_command("formal", namespace)


def test_gateway_passes_target_scope_and_rejects_missing_registration(tmp_path):
    base = dict(gateway_port=0, engine_port=0, policy_path=str(POLICY), data_dir=str(tmp_path),
                kube_context="formal", cilium_policy_status=True)
    with pytest.raises(ValueError, match="Cilium namespace is required"):
        GatewayApp(MVPConfig(**base), load_policy(POLICY), ("127.0.0.1", 1))
    document = json.loads((ROOT / "examples/mvp/target.example.json").read_text())
    document["kubernetes"] = {"context": "formal", "namespace": "payments", "selector": {"app": "engine"}, "container": "engine"}
    target = tmp_path / "target.json"
    target.write_text(json.dumps(document))
    app = GatewayApp(MVPConfig(**base, target_path=str(target)), load_policy(POLICY), ("127.0.0.1", 1))
    assert app._cilium_policy_collector.context == "formal"
    assert app._cilium_policy_collector.namespace == "payments"
