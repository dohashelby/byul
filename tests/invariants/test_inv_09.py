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
    from guard.update_acceptance import assess_update
    from guard.validation_evidence import read_objects, verify_store_files, verify_saved_bundle, inventory_tree
    store_path = tmp_path / "store"
    before = inventory_tree(store_path)
    objects = read_objects(store_path)
    saved_pack = verify_store_files(store_path, objects)
    acceptance = assess_update(report, objects, saved_pack)
    assert acceptance["accepted"] is True, acceptance
    assert acceptance["evidence_contract_valid"] is True
    # UNKNOWN or omitted rows cannot count as a completed two-image trial.
    # Pure contract tests use explicitly synthetic fixtures; this opt-in path
    # alone requires actual engines and original saved MEASURED evidence.
    from guard.store.bundle import export_bundle, restore_bundle
    export_bundle(store_path, tmp_path / "evidence.zip")
    restore_bundle(tmp_path / "evidence.zip", tmp_path / "restored")
    verify_saved_bundle(tmp_path / "evidence.zip", tmp_path / "restored")
    restored = read_objects(tmp_path / "restored")
    restored_pack = verify_store_files(tmp_path / "restored", restored)
    assert assess_update(report, restored, restored_pack)["accepted"] is True
    assert inventory_tree(store_path) == before
