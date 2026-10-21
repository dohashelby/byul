"""Manual import rejects ambiguous input before writes and commits EVD batches atomically."""
from copy import deepcopy
from pathlib import Path

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors.manual import ManualEvidenceError, build_template, import_evidence
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.select import select
from guard.models import Verdict
from guard.store import AppendOnlyError, Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import put_pack
from tests.builders import PACK_DIR, T0, baseline_pack, environment, policy, req01_obs
from tests.test_manual_evidence import _setup, _fill, _entry, _write


def refs(store):
    return [e.ref() for e in store.iter_kind("EVD")]


def completed_req(entry):
    _fill(entry)
    entry["override_reason"] = "synthetic fixture"
    observed = req01_obs()
    for observation in entry["required_observations"]:
        observation.update(observed[observation["key"]])
    return entry


@pytest.mark.parametrize("value", ["false", "true", 0, 1, [], {}])
def test_collected_never_coerces_wrong_types(tmp_path, value):
    store, pack, plan = _setup(tmp_path, include=("REQ-01",))
    doc = build_template(store, pack, plan, include_automated=True)
    entry = completed_req(_entry(doc, "REQ-01"))
    entry["required_observations"][0]["collected"] = value
    before, blobs = refs(store), set((store.root / "artifacts").iterdir())
    with pytest.raises(ManualEvidenceError, match="collected"):
        import_evidence(store, pack, _write(tmp_path, doc))
    assert refs(store) == before and set((store.root / "artifacts").iterdir()) == blobs


def test_real_false_withholds_pass(tmp_path):
    store, pack, plan = _setup(tmp_path, include=("REQ-01",))
    doc = build_template(store, pack, plan, include_automated=True)
    entry = completed_req(_entry(doc, "REQ-01"))
    entry["required_observations"][0]["collected"] = False
    import_evidence(store, pack, _write(tmp_path, doc))
    result, = judge_plan(store, pack, plan, T0)
    assert result.verdicts.security == Verdict.UNKNOWN


@pytest.mark.parametrize("has_previous", [False, True])
def test_duplicate_check_side_rejects_whole_batch(tmp_path, has_previous):
    if has_previous:
        store, pack, plan = _setup(tmp_path, include=("REQ-01",))
    else:
        store, pack = Store(tmp_path / "store"), baseline_pack()
        env = environment()
        pol = policy(pack.ref(), scope={"include":["REQ-01"]})
        store.put_many([env, pol])
        plan = select(pack, [env], pol); store.put(plan)
    doc = build_template(store, pack, plan, include_automated=True)
    entry = completed_req(_entry(doc, "REQ-01"))
    second = deepcopy(entry)
    second["verdicts"]["security"] = "FAIL"
    doc["checks"].append(second)
    before, blobs = refs(store), set((store.root / "artifacts").iterdir())
    with pytest.raises(ManualEvidenceError, match="duplicate check/side"):
        import_evidence(store, pack, _write(tmp_path, doc))
    assert refs(store) == before and set((store.root / "artifacts").iterdir()) == blobs


def two_manual_entries(tmp_path):
    store, pack, plan = _setup(tmp_path, include=("SUP-01", "SUP-02"))
    doc = build_template(store, pack, plan)
    for entry in doc["checks"]: _fill(entry)
    return store, pack, doc, _write(tmp_path, doc)


def test_late_attachment_read_failure_writes_nothing(tmp_path, monkeypatch):
    store, pack, doc, _ = two_manual_entries(tmp_path)
    (tmp_path / "second.txt").write_text("synthetic attachment", encoding="utf-8")
    doc["checks"][-1]["attachments"] = ["second.txt"]
    path = _write(tmp_path, doc)
    original = Path.read_bytes
    def read(file):
        if file.name == "second.txt": raise PermissionError("synthetic failure")
        return original(file)
    before, blobs = refs(store), set((store.root / "artifacts").iterdir())
    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(ManualEvidenceError, match="cannot read attachment"):
        import_evidence(store, pack, path)
    assert refs(store) == before and set((store.root / "artifacts").iterdir()) == blobs


def test_database_failure_rolls_back_all_evidence_objects(tmp_path, monkeypatch):
    store, pack, doc, path = two_manual_entries(tmp_path)
    before = refs(store)
    original, count = store._put, 0
    def put(connection, obj):
        nonlocal count
        count += 1
        if count == 2: raise AppendOnlyError("synthetic failure after first insertion")
        return original(connection, obj)
    monkeypatch.setattr(store, "_put", put)
    with pytest.raises(ManualEvidenceError, match="no evidence objects imported"):
        import_evidence(store, pack, path)
    assert count == 2 and refs(store) == before and store.verify() == []


def test_late_schema_failure_is_rejected_before_writes(tmp_path):
    store, pack, doc, path = two_manual_entries(tmp_path)
    doc["checks"][-1]["observation_notes"][0]["answer"] = 123
    before, blobs = refs(store), set((store.root / "artifacts").iterdir())
    with pytest.raises(ManualEvidenceError, match="answers must be text"):
        import_evidence(store, pack, _write(tmp_path, doc))
    assert refs(store) == before and set((store.root / "artifacts").iterdir()) == blobs


def test_cli_manual_replacement_and_bundle_round_trip(tmp_path):
    store, pack, plan = _setup(tmp_path, include=("REQ-01",))
    put_pack(store, PACK_DIR)
    config = tmp_path / "guard.toml"
    config.write_text('[adapter]\nname="fake"\n[executors]\ndirect_route="fake"\n', encoding="utf-8")
    runner = CliRunner()
    template = tmp_path / "manual.yaml"
    common = ["--plan", plan.id, "--pack", str(PACK_DIR), "--store", str(store.root), "--config", str(config)]
    result = runner.invoke(app, ["evidence", "template", *common, "--include-automated", "--out", str(template)])
    assert result.exit_code == 0, result.output
    import yaml
    doc = yaml.safe_load(template.read_text(encoding="utf-8"))
    entry = completed_req(_entry(doc, "REQ-01")); entry["verdicts"]["security"] = "FAIL"
    result = runner.invoke(app, ["evidence", "import", str(_write(tmp_path, doc)), "--pack", str(PACK_DIR),
                                 "--store", str(store.root)])
    assert result.exit_code == 0, result.output
    manual = store.find_latest("EVD", lambda e: e.check_id == "REQ-01")[0]
    assert manual.execution.operator is not None
    assert runner.invoke(app, ["run", *common]).exit_code == 0
    assert store.latest(manual.id).ref() == manual.ref()
    assert runner.invoke(app, ["run", *common, "--replace-manual"]).exit_code == 0
    replaced = store.latest(manual.id)
    assert replaced.revision == manual.revision + 1 and replaced.execution.operator is None
    judge_plan(store, pack, plan, T0)
    report = build_report(store, pack, plan, T0)
    row = next(r for r in report.by_check if r.check_id == "REQ-01")
    assert row.replaced.source == "manual" and row.evidence_source == "automated"
    export_bundle(store.root, tmp_path / "evidence.zip")
    restore_bundle(tmp_path / "evidence.zip", tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    assert restored.resolve(manual.ref()).ref() == manual.ref() and restored.verify() == []


def test_cli_has_all_evidence_commands_and_replace_flag():
    runner = CliRunner()
    result = runner.invoke(app, ["evidence", "--help"])
    assert result.exit_code == 0
    for command in ("template", "import", "export", "restore"): assert command in result.output
    assert "--replace-manual" in runner.invoke(app, ["run", "--help"]).output
