"""REQ-02 제품 진입점. 소유 컨테이너·생산자·전송은 EXAMPLE 대역이며 엔진을 기동하지 않는다."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.models import DataOrigin
from tests.builders import environment
from tests.test_native_observations import CaptureWorld, native_record
from tests.test_template_validation import IMAGE


@pytest.fixture
def route_input(monkeypatch):
    with TemporaryDirectory(prefix="guard-route-cli-") as directory:
        folder = Path(directory)
        env = environment(origin=DataOrigin.EXAMPLE)
        packet = native_record(env, kind="route")
        (folder / "env.json").write_text(json.dumps(env.to_dict()), encoding="utf-8")
        (folder / "target.json").write_text('{"run_id":"EXAMPLE-run"}', encoding="utf-8")
        # Use an actual external directory even when pytest's basetemp is inside the checkout.
        monkeypatch.setattr("guard.cli.__file__", str(folder / "repo/update/src/guard/cli.py"))
        output = folder / "route.json"
        args = ["capture-route-observations", "--env", str(folder / "env.json"),
            "--target", str(folder / "target.json"), "--container", "EXAMPLE-gateway",
            "--image", IMAGE, "--path", "/guard-observations/route.json", "--output", str(output)]
        yield args, output, env, packet


def test_product_default_only_validates_inputs_without_runtime_or_output(route_input, monkeypatch):
    args, output, _, _ = route_input
    monkeypatch.setattr("guard.runtime.docker.Docker", lambda *_: pytest.fail("unexpected Docker"))
    before = {p.name: p.read_bytes() for p in output.parent.iterdir()}
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output + repr(result.exception)
    summary = json.loads(result.output)
    assert summary["capture_executed"] is summary["ready_to_execute"] is False
    assert summary["engine_started"] is summary["product_connected"] is False
    assert summary["security"] == "UNKNOWN"
    assert not output.exists() and not (output.parent / ".guard").exists()
    assert {p.name: p.read_bytes() for p in output.parent.iterdir()} == before


def test_product_execute_reuses_owned_capture_with_exact_refs_and_example_ceiling(route_input, monkeypatch):
    args, output, env, packet = route_input
    world = CaptureWorld(packet)
    monkeypatch.setattr("guard.runtime.docker.Docker", lambda *_: world)
    before = {p.name: p.read_bytes() for p in output.parent.glob("*.json")}
    result = CliRunner().invoke(app, [*args, "--execute"])
    assert result.exit_code == 0, result.output + repr(result.exception)
    value = json.loads(output.read_text(encoding="utf-8"))
    assert value["record"] == packet and value["environment_ref"] == env.ref().model_dump(mode="json")
    assert value["data_origin"] == "EXAMPLE" and value["security"] == "UNKNOWN"
    assert value["product_connected"] is False
    assert value["capture"]["capture_sha256"] == hashlib.sha256(json.dumps(packet).encode()).hexdigest()
    assert value["capture"]["producer_authenticated"] is False
    assert value["summary"]["independent_source_verified"] is False
    assert value["summary"]["actual_consumption_verified"] is False
    assert world.calls == ["inspect", "read", "inspect"]
    assert all((output.parent / name).read_bytes() == raw for name, raw in before.items())
    assert not (output.parent / ".guard").exists()
    assert json.loads(result.output) == {"capture_executed": True, "engine_started": False,
                                        "product_connected": False, "security": "UNKNOWN"}


@pytest.mark.parametrize("broken", ["env-seal", "target-json", "duplicate-target", "target-run",
                                     "container", "image", "source-path", "existing", "missing-parent", "repo"])
def test_product_preflight_failure_has_fixed_code_before_runtime(route_input, monkeypatch, broken):
    args, output, _, _ = route_input
    folder = output.parent
    if broken == "env-seal":
        value = json.loads((folder / "env.json").read_text(encoding="utf-8"))
        value["sha256"] = "0" * 64
        (folder / "env.json").write_text(json.dumps(value), encoding="utf-8")
    elif broken == "target-json":
        (folder / "target.json").write_text("PRIVATE_INVALID_JSON", encoding="utf-8")
    elif broken == "duplicate-target":
        (folder / "target.json").write_text('{"run_id":"EXAMPLE-run","run_id":"EXAMPLE-run"}', encoding="utf-8")
    elif broken == "target-run":
        (folder / "target.json").write_text('{"run_id":"PRIVATE/INVALID"}', encoding="utf-8")
    elif broken in {"container", "image", "source-path"}:
        flag, value = {"container": ("--container", "PRIVATE/INVALID"), "image": ("--image", "unpinned:latest"),
                       "source-path": ("--path", "/guard-observations/../private")}[broken]
        args[args.index(flag) + 1] = value
    elif broken == "existing":
        output.write_bytes(b"EXAMPLE original")
    else:
        output = folder / ("missing/new.json" if broken == "missing-parent" else "repo/new.json")
        if broken == "repo":
            output.parent.mkdir()
        args[args.index("--output") + 1] = str(output)
    monkeypatch.setattr("guard.runtime.docker.Docker", lambda *_: pytest.fail("unexpected Docker"))
    result = CliRunner().invoke(app, [*args, "--execute"])
    assert result.exit_code == 2 and result.output == "NATIVE_ROUTE_CAPTURE_FAILED\n"
    assert output.read_bytes() == b"EXAMPLE original" if broken == "existing" else not output.exists()


@pytest.mark.parametrize("link", ["leaf", "parent", "junction"])
def test_product_rejects_output_links_before_runtime(route_input, monkeypatch, link):
    args, output, _, _ = route_input
    original = Path.is_symlink
    selected = output if link == "leaf" else output.parent
    if link == "junction":
        original_junction = Path.is_junction
        monkeypatch.setattr(Path, "is_junction", lambda self: self == selected or original_junction(self))
    else:
        monkeypatch.setattr(Path, "is_symlink", lambda self: self == selected or original(self))
    monkeypatch.setattr("guard.runtime.docker.Docker", lambda *_: pytest.fail("unexpected Docker"))
    result = CliRunner().invoke(app, [*args, "--execute"])
    assert result.exit_code == 2 and result.output == "NATIVE_ROUTE_CAPTURE_FAILED\n"
    assert not output.exists()


@pytest.mark.parametrize("checkout", ["repository", "worktree"])
def test_installed_product_rejects_git_checkout_output_before_runtime(route_input, monkeypatch, checkout):
    args, output, _, _ = route_input
    root = output.parent / "actual-checkout"
    root.mkdir()
    if checkout == "repository":
        (root / ".git").mkdir()
    else:
        (root / ".git").write_text("gitdir: EXAMPLE-managed-worktree\n", encoding="utf-8")
    destination = root / "nested"
    destination.mkdir()
    forbidden = destination / "route.json"
    args[args.index("--output") + 1] = str(forbidden)
    # The installed module path identifies a different root and cannot identify this checkout.
    monkeypatch.setattr("guard.cli.__file__", str(output.parent / "wheel/site-packages/guard/cli.py"))
    monkeypatch.setattr("guard.runtime.docker.Docker", lambda *_: pytest.fail("unexpected Docker"))
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = CliRunner().invoke(app, [*args, "--execute"])
    assert result.exit_code == 2 and result.output == "NATIVE_ROUTE_CAPTURE_FAILED\n"
    assert not forbidden.exists() and not output.exists()
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("broken", ["unowned", "restart", "hash", "partial", "pipe", "producer-field", "env-ref", "run"])
def test_product_capture_failure_does_not_publish_an_attachment(route_input, monkeypatch, broken):
    args, output, _, packet = route_input
    if broken == "producer-field":
        packet["events"][0]["secret"] = "PRIVATE_SECRET"
    elif broken == "env-ref":
        packet["environment_ref"]["revision"] += 1
    elif broken == "run":
        packet["run_id"] = "EXAMPLE-other-run"
    world = CaptureWorld(packet, broken)
    monkeypatch.setattr("guard.runtime.docker.Docker", lambda *_: world)
    result = CliRunner().invoke(app, [*args, "--execute"])
    assert result.exit_code == 2 and result.output == "NATIVE_ROUTE_CAPTURE_FAILED\n"
    assert not output.exists() and "PRIVATE_" not in result.output
    assert broken != "unowned" or "read" not in world.calls


def test_product_programming_error_is_not_disguised_as_input_failure(route_input, monkeypatch):
    args, output, _, _ = route_input
    error = RuntimeError("EXAMPLE programming error")
    def broken(*args, **kwargs):
        raise error
    monkeypatch.setattr("guard.collectors.route_observations.capture", broken)
    monkeypatch.setattr("guard.runtime.docker.Docker", lambda *_: object())
    result = CliRunner().invoke(app, [*args, "--execute"])
    assert result.exception is error and "NATIVE_ROUTE_CAPTURE_FAILED" not in result.output
    assert not output.exists()
