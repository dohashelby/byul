"""INV-09: fixed model/fixture/config + known GPU/driver or explicit CPU profile."""

import pytest

from guard.core.compare import comparable, compare_results
from guard.models import Comparison, Verdict
from guard.models.objects import Comparability

FULL = dict(model_inventory_sha256="m" * 64, fixture_sha256="f" * 64, config_sha256="c" * 64, gpu="L4", driver="550")


def test_identical_conditions_are_comparable():
    assert comparable(Comparability(**FULL), Comparability(**FULL))


@pytest.mark.parametrize("field", list(FULL))
def test_any_difference_is_not_comparable(field):
    other = {**FULL, field: "different"}
    assert not comparable(Comparability(**FULL), Comparability(**other))


@pytest.mark.parametrize("field", list(FULL))
def test_any_null_is_not_comparable(field):
    nulled = {**FULL, field: None}
    assert not comparable(Comparability(**nulled), Comparability(**nulled))


def test_compare_results_uses_comparability():
    a, b = Comparability(**FULL), Comparability(**{**FULL, "driver": "560"})
    assert compare_results(Verdict.PASS, a, Verdict.FAIL, a) == Comparison.REGRESSED
    assert compare_results(Verdict.PASS, a, Verdict.FAIL, b) == Comparison.NOT_COMPARABLE


@pytest.mark.engine
def test_update_mode_with_two_real_images(tmp_path):
    """Opt-in actual update, never a synthetic substitute for two engines."""
    import os
    import json
    from pathlib import Path
    import tomllib
    from typer.testing import CliRunner
    from guard.cli import app
    from guard.collectors.file import load_environment
    required = ["GUARD_UPDATE_CONFIG", "GUARD_UPDATE_BASELINE", "GUARD_UPDATE_CANDIDATE", "GUARD_UPDATE_POLICY"]
    if not all(os.environ.get(key) for key in required):
        pytest.skip("requires Docker/models and explicit GUARD_UPDATE_CONFIG/BASELINE/CANDIDATE/POLICY")
    config, baseline, candidate, policy = [Path(os.environ[key]).resolve() for key in required]
    cfg = tomllib.loads(config.read_text(encoding="utf-8"))
    assert cfg.get("update", {}).get("mode") == "managed"
    assert cfg.get("executors") and all(v == "real" for v in cfg["executors"].values())
    assert load_environment(baseline).engine.image_digest != load_environment(candidate).engine.image_digest
    from tests.builders import PACK_DIR
    result = CliRunner().invoke(app, ["update", "--pack", str(PACK_DIR), "--baseline", str(baseline),
        "--candidate", str(candidate), "--policy", str(policy), "--config", str(config),
        "--store", str(tmp_path / "store"), "--output", str(tmp_path / "reports")])
    assert result.exit_code == 0, result.output + repr(result.exception)
    report = json.loads(next((tmp_path / "reports").glob("*/report.json")).read_text(encoding="utf-8"))
    rows = [r for r in report["by_check"] if r["selected"] and not r["gaps"]]
    assert rows and all(r["comparison"] not in (None, "NOT_COMPARABLE") for r in rows)
    from guard.store import Store
    from guard.store.bundle import export_bundle, restore_bundle
    store = Store(tmp_path / "store")
    ids = {r["check_id"] for r in rows}
    evidence = [e for e in store.iter_kind("EVD") if e.check_id in ids]
    assert len(evidence) == 2 * len(ids)
    assert all(e.execution.status.value == "COMPLETED" and e.data_origin.value == "MEASURED"
               and not e.execution.cleanup_errors for e in evidence)
    assert {e.side.value for e in evidence} == {"baseline", "candidate"}
    export_bundle(store.root, tmp_path / "evidence.zip")
    restore_bundle(tmp_path / "evidence.zip", tmp_path / "restored")
    assert Store(tmp_path / "restored").verify() == []
