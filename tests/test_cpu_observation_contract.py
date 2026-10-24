"""Saved-CPU harness contracts with synthetic I/O only; no engine is executed.

The generated MEASURED-shaped records exercise audit rejection boundaries. They
are unit fixtures, never claims of real measurements or production host identity.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from guard.core.judge import judge_evidence
from guard.core.report import build_report
from guard.core.select import select
from guard.models import canonical_json
from guard.models.objects import object_from_dict
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import put_pack
from tests.builders import PACK_DIR, T0, baseline_pack, environment, policy
from tests.test_automation_phase1_contracts import VALUES, _run
from tests.prepared_fixtures import copy_frozen_tree, put_baseline_snapshot
from tools.validate_cpu_cases import (CHECKS, _inventory_tree, _objects, _verify_store_files,
                                      analyze_existing, compare_report, main, observation_contract)


CPU = {"architecture": "x86_64", "model": "EXAMPLE fixture CPU", "system": "Linux", "kernel": "EXAMPLE", "threads": 2}


def _write(path, data):
    path.write_bytes(canonical_json(data))


def _case(folder, monkeypatch, expected):
    """Combine the existing five fake-I/O executors under one frozen CPU PLAN."""
    folder.mkdir()
    pack = baseline_pack()
    store = Store(folder / "store")
    put_baseline_snapshot(store)
    env = environment({"gateway": {"value": True, "origin": "EXAMPLE", "source": "fake-I/O test"}},
                      engine={"name": "vllm", "version": "0.31.0"},
                      model={"id": "test-model"}, runtime={"gpu_count": 0, "cpu": CPU})
    pol = policy(pack.ref(), scope={"include": list(CHECKS)}, values=deepcopy(VALUES))
    store.put(env)
    store.put(pol)
    plan = select(pack, [env], pol)
    store.put(plan)
    for cid in CHECKS:
        child = folder / ("fixture-" + cid)
        child.mkdir()
        source, _, _, _, evd, _ = _run(child, monkeypatch, cid, expected[cid])
        for art in evd.artifacts:
            store.put_artifact((source.root / art.path).read_bytes())
        evd = evd.model_copy(update={"id": "EVD-CPU-HARNESS-" + cid, "plan": plan.ref(),
                                    "environment": env.ref(), "refs": [plan.ref(), env.ref()],
                                    "data_origin": type(evd.data_origin)("MEASURED"),
                                    "comparability": evd.comparability.model_copy(update={"cpu": env.runtime.cpu})}).seal()
        store.put(evd)
        result = judge_evidence(pack.checks[cid], evd, pol, T0,
                                condition_origins=[c.origin for c in plan.item(cid).conditions], plan=plan)
        store.put(result)
    rpt = build_report(store, pack, plan, T0)
    store.put(rpt)
    output = folder / "validation" / "VALIDATE-EXAMPLE"
    output.mkdir(parents=True)
    _write(output / "report.json", rpt.to_dict())
    (output / "report.html").write_text("<p>EXAMPLE synthetic fixture</p>", encoding="utf-8")
    objects = _objects(store.root)
    _write(folder / "original-objects.json", objects)
    export_bundle(store.root, folder / "evidence.zip")
    restore_bundle(folder / "evidence.zip", folder / "restored-store")
    return pack, rpt.to_dict(), objects


@pytest.fixture(scope="module")
def matrix(tmp_path_factory):
    root = tmp_path_factory.mktemp("synthetic-cpu-matrix")
    order = ["exposed-violations", "isolated-controls", "isolated-insufficient-observation"]
    expectations = [dict.fromkeys(CHECKS, "FAIL"), dict.fromkeys(CHECKS, "PASS"),
                    {cid: "PASS" if cid == "PLT-01" else "UNKNOWN" for cid in CHECKS}]
    cases, datasets = [], []
    with pytest.MonkeyPatch.context() as monkeypatch:
        for name, expected, hypothesis in zip(order, expectations, ["FAIL", "PASS", "UNKNOWN"]):
            pack, report, objects = _case(root / name, monkeypatch, expected)
            datasets.append((pack, report, objects))
            cases.append({"name": name, "completed": True,
                          "comparison": compare_report(report, dict.fromkeys(CHECKS, hypothesis))})
    _write(root / "manifest.json", {"kind": "guard-cpu-case-matrix/1", "execution_order": order,
                                    "pack_ref": pack.ref().model_dump(mode="json")})
    _write(root / "summary.json", {"success": False, "source_inputs_unchanged": True, "cases": cases})
    return root, datasets


def test_timing_pass_requires_full_original_evidence_and_frozen_rules(matrix):
    _, datasets = matrix
    pack, report, objects = datasets[2]
    before = deepcopy((report, objects))
    result = observation_contract(report, objects, pack, short_timeout=True)
    assert result["success"], result["problems"]
    assert result["checks"]["PLT-01"]["actual"]["security"] == "PASS"
    assert result["checks"]["PLT-01"]["pass_gate_blocks"] == []
    assert result["checks"]["PLT-01"]["required_observations"]
    assert result["checks"]["PLT-01"]["challenge_steps"]
    assert (report, objects) == before


def _rewrite(matrix, cid="PLT-01", *, evd_change=None, verdict=None, report_change=None):
    """Reseal malicious fixtures so audit must inspect meaning, not only hashes."""
    _, datasets = matrix
    pack, original_report, originals = datasets[2]
    def transform(obj):
        if obj["kind"] == "EVD" and obj["check_id"] == cid and evd_change:
            evd_change(obj)
        if obj["kind"] == "RES" and obj["check_id"] == cid and verdict:
            obj["verdicts"]["security"] = verdict
        if obj["kind"] == "RPT":
            if verdict:
                next(row for row in obj["by_check"] if row["check_id"] == cid)["single"]["security"] = verdict
            if report_change:
                report_change(obj)
    report, objects = _reseal_graph(originals, transform)
    return pack, report, objects


@pytest.mark.parametrize("missing", ["normal", "challenge", "observation"])
def test_resealed_incomplete_evidence_cannot_support_timing_pass(matrix, missing):
    def change(evd):
        if missing == "normal":
            evd["normal_control"]["succeeded"] = False
        elif missing == "challenge":
            evd["challenges"][0]["attempted"] = False
        else:
            key = next(iter(evd["observations"]))
            evd["observations"][key] = {"collected": False, "value": None}
    pack, report, objects = _rewrite(matrix, evd_change=change)
    result = observation_contract(report, objects, pack, short_timeout=True)
    assert result["success"] is False
    assert any("PASS" in p or "fixed rules" in p for p in result["problems"])


def test_resealed_confirmed_fail_cannot_be_hidden_as_unknown(matrix):
    def change(evd):
        evd["observations"]["privileged"] = {"collected": True, "value": True}
        evd["observations"]["process_scan_complete"] = {"collected": True, "value": False}
    pack, report, objects = _rewrite(matrix, evd_change=change, verdict="UNKNOWN")
    result = observation_contract(report, objects, pack, short_timeout=True)
    assert result["success"] is False
    assert any("confirmed FAIL erased" in p for p in result["problems"])


def test_saved_report_cannot_disagree_with_original_result(matrix):
    pack, report, objects = _rewrite(matrix, report_change=lambda r: next(
        row for row in r["by_check"] if row["check_id"] == "PLT-01")["single"].update(security="UNKNOWN"))
    result = observation_contract(report, objects, pack, short_timeout=True)
    assert result["success"] is False
    assert any("report differs" in p for p in result["problems"])


def _reseal_graph(objects, transform):
    """Keep every Ref valid while exercising a semantic relationship violation."""
    order = {kind: index for index, kind in enumerate(("ENV", "POL", "CHG", "PLAN", "EVD", "RES", "RPT"))}
    replacements, result = {}, []

    def walk(value):
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            if set(value) == {"kind", "id", "revision", "sha256"}:
                return replacements.get(canonical_json(value), value)
            return {key: walk(item) for key, item in value.items()}
        return value

    for original in sorted(deepcopy(objects), key=lambda obj: order[obj["kind"]]):
        old_ref = object_from_dict(original).ref().model_dump(mode="json")
        changed = walk(original)
        transform(changed)
        new = object_from_dict(changed).seal()
        replacements[canonical_json(old_ref)] = new.ref().model_dump(mode="json")
        result.append(new.to_dict())
    return next(obj for obj in result if obj["kind"] == "RPT"), result


@pytest.mark.parametrize("relationship", ["report-mode", "plan-mode", "multiple-environments", "different-environment"])
def test_resealed_wrong_original_environment_or_mode_is_rejected(matrix, relationship):
    _, datasets = matrix
    pack, _, objects = deepcopy(datasets[2])
    env = next(o for o in objects if o["kind"] == "ENV")
    unrelated = object_from_dict({**env, "id": "ENV-UNRELATED-SYNTHETIC"}).seal()
    objects.append(unrelated.to_dict())
    unrelated_ref = unrelated.ref().model_dump(mode="json")

    def transform(obj):
        if relationship == "report-mode" and obj["kind"] == "RPT":
            obj["mode"] = "update"
        elif relationship == "plan-mode" and obj["kind"] in {"PLAN", "RPT"}:
            obj["mode"] = "update"
        elif relationship == "multiple-environments" and obj["kind"] == "PLAN":
            obj["inputs"]["environments"].append(unrelated_ref)
            obj["refs"].append(unrelated_ref)
        elif relationship == "different-environment" and obj["kind"] == "EVD" and obj["check_id"] == "PLT-01":
            old = obj["environment"]
            obj["environment"] = unrelated_ref
            obj["refs"] = [unrelated_ref if ref == old else ref for ref in obj["refs"]]

    report, objects = _reseal_graph(objects, transform)
    result = observation_contract(report, objects, pack, short_timeout=True)
    assert result["success"] is False
    assert any("audit PLAN" in p or "original single ENV" in p for p in result["problems"])


def test_unsealed_or_wrong_hash_object_is_not_a_supported_timing_pass(matrix):
    _, datasets = matrix
    pack, report, objects = deepcopy(datasets[2])
    next(o for o in objects if o["kind"] == "EVD")["normal_control"]["succeeded"] = False
    result = observation_contract(report, objects, pack, short_timeout=True)
    assert result["success"] is False
    assert any("hash mismatch" in p for p in result["problems"])


def test_analysis_preserves_legacy_failure_while_verifying_observation_contract(matrix, tmp_path, monkeypatch):
    root, _ = matrix
    before = _inventory_tree(root)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("read-only analysis must not launch a process"))
    result = analyze_existing(root, tmp_path / "analysis")
    assert result["original_success"] is False
    assert result["original_hypothesis_status"]["success"] is False
    assert result["observation_contract_success"] is True, result["cases"]
    assert result["source_bytes_unchanged"] is True
    assert _inventory_tree(root) == before
    comparisons = result["original_hypothesis_status"]["comparisons"]
    matched = sum(sum(row["security"] == c["expected_security"][cid] for cid, row in c["actual"].items())
                  for c in comparisons)
    assert matched == 14
    assert json.loads((root / "summary.json").read_text())["success"] is False


@pytest.mark.parametrize("damage", ["artifact", "report", "snapshot", "saved-json", "bundle"])
def test_readonly_analysis_rejects_corrupt_saved_proof_without_repair(matrix, tmp_path, damage):
    root, _ = matrix
    source = tmp_path / "source"
    copy_frozen_tree(root, source)
    folder = source / "isolated-insufficient-observation"
    objects = _objects(folder / "store")
    if damage == "artifact":
        evd = next(o for o in objects if o["kind"] == "EVD")
        (folder / "store" / evd["artifacts"][0]["path"]).unlink()
    elif damage == "snapshot":
        plan = next(o for o in objects if o["kind"] == "PLAN")
        (folder / "store" / "artifacts" / plan["inputs"]["pack"]["sha256"]).write_bytes(b"corrupt")
    elif damage == "report":
        path = next((folder / "validation").glob("*/report.json"))
        report = json.loads(path.read_text(encoding="utf-8"))
        next(row for row in report["by_check"] if row["selected"])["single"]["security"] = "PASS"
        _write(path, report)
    elif damage == "saved-json":
        _write(folder / "original-objects.json", [])
    else:
        (folder / "evidence.zip").write_bytes(b"corrupt")
    before = _inventory_tree(source)
    result = analyze_existing(source, tmp_path / "analysis")
    assert result["observation_contract_success"] is False
    assert result["source_bytes_unchanged"] is True
    assert _inventory_tree(source) == before


def test_existing_analysis_output_is_never_overwritten(matrix, tmp_path):
    root, _ = matrix
    output = tmp_path / "keep"
    output.mkdir()
    (output / "important.txt").write_text("keep")
    with pytest.raises(ValueError):
        analyze_existing(root, output)
    assert (output / "important.txt").read_text() == "keep"


def test_analysis_cli_does_not_need_live_environment_or_current_pack(matrix, tmp_path, monkeypatch):
    root, _ = matrix
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("no child process is allowed"))
    assert main(["--analyze-existing", str(root), "--output", str(tmp_path / "result"),
                 "--pack", str(tmp_path / "missing-current-pack")]) == 0
