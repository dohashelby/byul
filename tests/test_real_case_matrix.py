"""Opt-in existing isolated-engine cases; no fake executor can satisfy this gate."""
import json
import os
from pathlib import Path
import tomllib

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors.file import load_environment
from guard.models import DataOrigin
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from tests.builders import PACK_DIR


@pytest.mark.engine
@pytest.mark.parametrize("check_id", ["REQ-01", "REQ-03", "PLT-01", "PLT-04", "REQ-09"])
def test_real_pass_fail_unknown_cases(tmp_path, check_id):
    supplied = os.environ.get("GUARD_ENGINE_CASES")
    if not supplied:
        pytest.skip("requires GUARD_ENGINE_CASES with isolated real PASS/FAIL/UNKNOWN fixtures")
    manifest_path = Path(supplied).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("isolated_sandbox") is True, "only explicitly designated test sandboxes are allowed"
    cases = manifest.get("checks", {}).get(check_id)
    assert cases and set(cases) == {"PASS", "FAIL", "UNKNOWN"}, f"missing real cases for {check_id}"
    runner = CliRunner()
    for expected, case in cases.items():
        folder = tmp_path / expected
        folder.mkdir()
        def path(key):
            return (manifest_path.parent / case[key]).resolve()
        cfg = tomllib.loads(path("config").read_text(encoding="utf-8"))
        assert cfg.get("adapter", {}).get("name") == "vllm"
        assert cfg.get("executors") and all(v == "real" for v in cfg["executors"].values())
        env = load_environment(path("environment"))
        assert env.data_origin != DataOrigin.EXAMPLE
        assert env.engine.version and env.engine.image_digest and env.model.revision and env.model.inventory_sha256
        store_path = folder / "store"
        selected = runner.invoke(app, ["select", "--pack", str(PACK_DIR), "--env", str(path("environment")),
            "--policy", str(path("policy")), "--config", str(path("config")), "--store", str(store_path)])
        assert selected.exit_code == 0, selected.output + repr(selected.exception)
        store = Store(store_path)
        plan = store.latest(selected.output.strip().splitlines()[-1])
        assert {i.check_id for i in plan.items if i.selected} == {check_id}, "scope each fixture to exactly one check"
        item = plan.item(check_id)
        assert not item.gaps and cfg["executors"].get(item.executor) == "real"
        target = json.loads(Path(cfg[item.executor]["target"]).read_text(encoding="utf-8"))
        assert target.get("lab") is False and target.get("engine", {}).get("container")
        assert target.get("observed_version") == env.engine.version
        assert target.get("engine", {}).get("image_digest") == env.engine.image_digest
        for command in ("run", "judge"):
            result = runner.invoke(app, [command, "--plan", plan.id, "--pack", str(PACK_DIR),
                "--config", str(path("config")), "--store", str(store_path)])
            assert result.exit_code == 0, result.output + repr(result.exception)
        evd, = store.iter_kind("EVD")
        assert evd.execution.status.value == case.get("status", "COMPLETED")
        assert evd.execution.status.value in {"COMPLETED", "ABORTED"}, "NOT_RUN is not a real fixture execution"
        if evd.execution.status.value == "COMPLETED":
            assert evd.data_origin == DataOrigin.MEASURED
        assert not evd.execution.cleanup_errors
        result, = store.iter_kind("RES")
        assert result.verdicts.security.value == expected
        assert result.verdicts.functional.value == case["functional"]
        before = evd.ref()
        archive = folder / "evidence.zip"
        export_bundle(store_path, archive)
        restore_bundle(archive, folder / "restored")
        restored = Store(folder / "restored")
        assert restored.resolve(before).ref() == before
        assert restored.verify() == []
