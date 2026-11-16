"""Kubernetes consumers require explicit scope; file-only readers retain their input contract."""

from pathlib import Path
from unittest.mock import Mock, patch
import pytest

from serving_runtime.common.kube_context import validate_kube_context
from serving_runtime.observation.cilium_status import CiliumPolicyCollector
from serving_runtime.observation.observation_feed import ObservationFeed
from tools import k8s_observation_feed as helper
from tools.tetragon_trial import KindTrial


def test_missing_context_names_the_input_routes():
    for value in (None, ""):
        with pytest.raises(
            ValueError, match="context is required; set --kube-context / --context or target kubernetes.context"
        ):
            validate_kube_context(value)
    assert validate_kube_context("formal/context-1") == "formal/context-1"


def test_cilium_missing_context_fails_before_query():
    runner = Mock()
    with pytest.raises(ValueError, match="context is required"):
        CiliumPolicyCollector(timeout_s=1, runner=runner)
    runner.assert_not_called()


def test_health_receipt_missing_context_fails_before_write(tmp_path):
    feed = ObservationFeed(
        kind="tetragon", path=tmp_path / "feed", data_dir=tmp_path, data_origin="real", environment=None, run_id=None
    )
    with pytest.raises(ValueError, match="context is required"):
        feed._save_health_receipt({"identity": {"node": "node"}, "observed_at": "2026-11-13T00:00:00Z"})
    assert not (tmp_path / "tetragon-health-receipts.json").exists()


def test_tool_context_missing_fails_before_process_or_node_access():
    runner = Mock()
    with pytest.raises(ValueError, match="context is required"):
        KindTrial(Path("/unused"), "sigkill_fd_install", runner=runner)
    runner.assert_not_called()
    for command in (helper.hubble_port_forward_command, helper.tetragon_command):
        with pytest.raises(ValueError, match="context is required"):
            command()


def test_observation_cli_requires_context_before_running():
    with patch.object(helper, "run") as run, pytest.raises(SystemExit) as error:
        helper.main(["--output-dir", "/unused"])
    assert error.value.code == 2
    run.assert_not_called()


def test_gateway_state_source_requires_context_before_startup(tmp_path):
    from serving_runtime.gateway.app import GatewayApp
    from serving_runtime.config.config import MVPConfig
    from serving_runtime.policy.policy import load_policy

    policy = Path(__file__).resolve().parents[1] / "examples/mvp/policy.json"
    config = MVPConfig(
        data_dir=str(tmp_path), gateway_port=0, engine_port=0, tetragon_state_path=str(tmp_path / "state.json")
    )
    with pytest.raises(ValueError, match="context is required"):
        GatewayApp(config, load_policy(policy), ("127.0.0.1", 1))
