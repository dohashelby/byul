"""guard select → run → judge → report 를 FakeExecutor로 처음부터 끝까지 (CLI)."""

import json

import pytest
from typer.testing import CliRunner

from guard.cli import app
from tests.prepared_fixtures import copy_workdir

runner = CliRunner()


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    copy_workdir(tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _ok(args):
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output + repr(res.exception)
    return res.output


@pytest.mark.parametrize("scenario, expected", [("pass", "PASS"), ("direct_200", "FAIL"),
                                                ("not_attempted", "UNKNOWN"), ("missing_obs", "UNKNOWN"),
                                                ("refused_with_positive_control", "PASS"),
                                                ("refused_without_positive_control", "UNKNOWN"),
                                                ("partial_routes", "UNKNOWN"), ("direct_200_on_invocations", "FAIL")])
def test_end_to_end(workdir, scenario, expected):
    out = _ok(["select", "--pack", "packs/PACK-BASELINE", "--env", "examples/env.yaml", "--policy", "examples/policy.yaml"])
    plan_id = out.strip().splitlines()[-1]
    assert plan_id.startswith("PLAN-")
    _ok(["run", "--plan", plan_id, "--fake-scenario", scenario])
    _ok(["judge", "--plan", plan_id])
    _ok(["report", "--plan", plan_id, "--json", "out.json", "--html", "out.html"])
    data = json.loads((workdir / "out.json").read_text(encoding="utf-8"))
    (row,) = [r for r in data["by_check"] if r["check_id"] == "REQ-01"]
    assert row["single"]["security"] == expected
    assert data["official"] is False and data["approval"] is None
    html = (workdir / "out.html").read_text(encoding="utf-8")
    assert "REQ-01" in html and "공식 아님" in html
    assert "OK" in _ok(["verify"])


def test_verify_detects_tampering(workdir):
    out = _ok(["select", "--pack", "packs/PACK-BASELINE", "--env", "examples/env.yaml", "--policy", "examples/policy.yaml"])
    plan_id = out.strip().splitlines()[-1]
    _ok(["run", "--plan", plan_id])
    art = next((workdir / ".guard/artifacts").iterdir())
    art.write_bytes(b"tampered")
    res = runner.invoke(app, ["verify"])
    assert res.exit_code == 1
