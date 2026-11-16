"""Explicit tool scope preserves old reviewed output and supports other target namespaces."""

import json
from pathlib import Path
from unittest.mock import patch
import pytest

from tools import k8s_observation_feed as feed, prepare_tetragon_deployment as prepare_tool, agent_rbac

ROOT = prepare_tool.ROOT
IMAGE = "python@sha256:" + "a" * 64


def test_prepared_lab_output_is_byte_identical_with_explicit_scope(tmp_path):
    import hashlib

    expected = json.loads((Path(__file__).resolve().parent / "data/prepared_lab_sha256.json").read_text())
    output = tmp_path / "new"
    intent = ROOT / "deploy/gpu-lab/intent.aws-lab.json"
    prepare_tool.prepare(intent, IMAGE, output, context="byul-aws-lab")
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()} == expected


def test_prepare_uses_intent_namespace_and_explicit_context(tmp_path):
    document = json.loads((ROOT / "deploy/gpu-lab/intent.aws-lab.json").read_text())
    document["namespace"] = "payments"
    intent = tmp_path / "intent.json"
    intent.write_text(json.dumps(document))
    output = tmp_path / "out"
    result = prepare_tool.prepare(intent, IMAGE, output, context="formal")
    assert (result["context"], result["namespace"]) == ("formal", "payments")
    assert json.loads((output / "sentinel.json").read_text())["metadata"]["namespace"] == "payments"
    service = (output / "byul-tetragon-stream.service").read_text()
    assert " --context formal --namespace payments " in service
    for path in output.glob("tetragon-*.json"):
        assert json.loads(path.read_text())["metadata"]["namespace"] == "payments"
    with pytest.raises(ValueError, match="context is required"):
        prepare_tool.prepare(intent, IMAGE, tmp_path / "missing")
    assert not (tmp_path / "missing").exists()


def test_namespace_is_required_before_processes_or_output(tmp_path):
    with pytest.raises(ValueError, match="target namespace is required; set --target-namespace"):
        feed.run(tmp_path / "out", "formal", max_ticks=0)
    assert not (tmp_path / "out").exists()
    command = feed.hubble_command(namespace="payments")
    assert command[command.index("--namespace") + 1] == "payments"
    for namespace in (None, "Bad_NS", "--other"):
        with pytest.raises(ValueError):
            feed.hubble_command(namespace=namespace)


def test_cli_missing_namespace_and_context_exit_two_before_execution():
    with patch.object(feed, "run") as run, pytest.raises(SystemExit) as error:
        feed.main(["--kube-context", "formal", "--output-dir", "/unused"])
    assert error.value.code == 2
    run.assert_not_called()
    for command in ("render", "check"):
        with patch("sys.argv", ["agent-rbac", command]), pytest.raises(SystemExit) as error:
            agent_rbac.main()
        assert error.value.code == 2
    with (
        patch("sys.argv", ["prepare", "--intent", "/unused", "--sentinel-image", IMAGE, "--output", "/unused"]),
        patch.object(prepare_tool, "prepare") as prepare,
        pytest.raises(SystemExit) as error,
    ):
        prepare_tool.main()
    assert error.value.code == 2
    prepare.assert_not_called()


def test_aws_units_preserve_old_scope_as_explicit_arguments():
    for relative in (
        "deploy/gpu-lab/host/byul-observation-feed.service",
        "deploy/gpu-lab-sglang/byul-observation-feed.service",
    ):
        line = next(line for line in (ROOT / relative).read_text().splitlines() if line.startswith("ExecStart="))
        assert "--kube-context byul-aws-lab " in line
        assert "--target-namespace serving-lab " in line
