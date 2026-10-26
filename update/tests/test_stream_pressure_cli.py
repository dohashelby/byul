"""Product LIF-04 entry point; EXAMPLE doubles, no Docker or serving engine."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from typer.testing import CliRunner

from guard.cli import app
from tests.test_stream_pressure import inputs


@pytest.fixture
def pressure_cli(monkeypatch):
    with TemporaryDirectory(prefix="guard-pressure-cli-") as directory:
        folder = Path(directory)
        env, target, cfg = inputs()
        for name, value in (("env", env.to_dict()), ("target", target), ("settings", cfg)):
            (folder / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
        monkeypatch.setattr("guard.cli.__file__", str(folder / "repo/update/src/guard/cli.py"))
        output = folder / "pressure.json"
        args = ["capture-stream-pressure", "--env", str(folder / "env.json"), "--target", str(folder / "target.json"),
                "--settings", str(folder / "settings.json"), "--output", str(output)]
        yield args, output, env, target, cfg


@pytest.mark.parametrize("lab", [False, True])
def test_default_validates_only_without_runtime_output_or_store(pressure_cli, monkeypatch, lab):
    args, output, _, _, _ = pressure_cli
    target_path = output.parent / "target.json"
    target = json.loads(target_path.read_text(encoding="utf-8"))
    target["lab"] = lab
    target_path.write_text(json.dumps(target), encoding="utf-8")
    monkeypatch.setattr("guard.executors.stream_pressure.create_pressure_client", lambda: pytest.fail("unexpected Docker"))
    monkeypatch.setattr("guard.executors.stream_pressure.execute_pressure", lambda *args: pytest.fail("unexpected execution"))
    before = {path.name: path.read_bytes() for path in output.parent.iterdir()}
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output + repr(result.exception)
    summary = json.loads(result.output)
    assert summary["capture_executed"] is summary["engine_started"] is summary["product_connected"] is False
    assert summary["security"] == "UNKNOWN"
    assert {path.name: path.read_bytes() for path in output.parent.iterdir()} == before
    assert not output.exists() and not (output.parent / ".guard").exists()


def test_execute_publishes_new_attachment_after_authorized_collection(pressure_cli, monkeypatch):
    args, output, env, target, cfg = pressure_cli
    seen = []
    attachment = {"schema": "guard-stream-pressure-capture/1", "check_id": "LIF-04", "data_origin": "EXAMPLE",
                  "environment_ref": env.ref().model_dump(mode="json"), "capture_executed": True,
                  "engine_started": False, "product_connected": False,
                  "security": "UNKNOWN", "functional": "UNKNOWN", "performance": "UNKNOWN"}
    def execute(given_env, given_target, given_cfg):
        seen.append((given_env, given_target, given_cfg))
        return deepcopy(attachment)
    monkeypatch.setattr("guard.executors.stream_pressure.execute_pressure", execute)
    before = {path.name: path.read_bytes() for path in output.parent.iterdir()}
    result = CliRunner().invoke(app, [*args, "--execute"])
    assert result.exit_code == 0, result.output + repr(result.exception)
    assert json.loads(output.read_text(encoding="utf-8")) == attachment
    assert seen == [(env, target, cfg)]
    assert all((output.parent / name).read_bytes() == value for name, value in before.items())
    assert not (output.parent / ".guard").exists()


@pytest.mark.parametrize("broken", ["env-seal", "target-json", "target-lab", "settings-json", "unknown-setting", "existing", "missing-parent", "repo"])
def test_invalid_inputs_or_destination_fail_before_runtime(pressure_cli, monkeypatch, broken):
    args, output, _, _, _ = pressure_cli
    if broken == "env-seal":
        path = output.parent / "env.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["content_sha256"] = "0" * 64
        path.write_text(json.dumps(value), encoding="utf-8")
    elif broken in {"target-json", "settings-json"}:
        (output.parent / ("target.json" if broken == "target-json" else "settings.json")).write_text("PRIVATE_INVALID", encoding="utf-8")
    elif broken == "target-lab":
        path = output.parent / "target.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["lab"] = "false"
        path.write_text(json.dumps(value), encoding="utf-8")
    elif broken == "unknown-setting":
        path = output.parent / "settings.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["PRIVATE_UNKNOWN_KEY"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
    elif broken == "existing":
        output.write_bytes(b"EXAMPLE original")
    else:
        output = output.parent / ("missing/new.json" if broken == "missing-parent" else "repo/new.json")
        if broken == "repo":
            output.parent.mkdir()
        args[args.index("--output") + 1] = str(output)
    monkeypatch.setattr("guard.executors.stream_pressure.execute_pressure", lambda *args: pytest.fail("unexpected execution"))
    monkeypatch.setattr("guard.executors.stream_pressure.create_pressure_client", lambda: pytest.fail("unexpected Docker"))
    result = CliRunner().invoke(app, [*args, "--execute"])
    assert result.exit_code == 2 and result.output == "STREAM_PRESSURE_CAPTURE_FAILED\n"
    assert output.read_bytes() == b"EXAMPLE original" if broken == "existing" else not output.exists()


def test_runtime_capture_failure_does_not_publish_partial_or_secret_output(pressure_cli, monkeypatch):
    args, output, _, _, _ = pressure_cli
    def failed(*args):
        raise ValueError("PRIVATE_RUNTIME_DIAGNOSTIC")
    monkeypatch.setattr("guard.executors.stream_pressure.execute_pressure", failed)
    result = CliRunner().invoke(app, [*args, "--execute"])
    assert result.exit_code == 2 and result.output == "STREAM_PRESSURE_CAPTURE_FAILED\n"
    assert not output.exists() and not (output.parent / ".guard").exists()
