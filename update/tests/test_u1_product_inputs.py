"""U1 입력·저장·CLI 경계. 모든 자료는 합성 자료이며 실제 엔진을 사용하지 않는다."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest
import yaml
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors.change import ChangeInput, read_change, save_change
from guard.collectors.file import load_environment, load_policy
from guard.collectors.updates import update_object
from guard.models import Change, DataOrigin, object_from_dict, sha256_hex
from guard.store import Store, IntegrityError
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import put_pack
from tests.builders import PACK_DIR, baseline_pack, environment, policy
from tests.test_u1_extractors import source_repo


def change_input(pack, payload=b"bounded source evidence", *, check="REQ-01"):
    return ChangeInput(Change(id="CHG-U1", data_origin=DataOrigin.DECLARED,
        baseline={"version": "1.0.0", "source_commit": "a" * 40},
        candidate={"version": "1.0.1", "source_commit": "b" * 40},
        extractor={"name": "manual", "version": "1"},
        facts=[{"id": "F1", "category": "AUTH", "target": "route", "certainty": "PARTIAL",
                "related_checks": [check], "evidence": [{"path": "proof.json", "sha256": sha256_hex(payload)}]}]
        ).seal(), {"proof.json": payload})


def files(tmp_path):
    pack = baseline_pack()
    env = environment()
    pol = policy(pack.ref())
    e, p = tmp_path / "env.yaml", tmp_path / "policy.yaml"
    e.write_text(yaml.safe_dump(env.to_dict()), encoding="utf-8")
    p.write_text(yaml.safe_dump(pol.to_dict()), encoding="utf-8")
    return pack, env, pol, e, p


def tree(path):
    return {p.relative_to(path).as_posix(): sha256_hex(p.read_bytes()) for p in path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("mutation", ["hash", "unknown-check", "missing-file", "escape", "noncanonical-artifact"])
def test_invalid_change_is_rejected_before_new_store(tmp_path, mutation):
    pack, _, _, e, p = files(tmp_path)
    ci = change_input(pack)
    raw = ci.change.to_dict()
    (tmp_path / "proof.json").write_bytes(ci.payloads["proof.json"])
    if mutation == "hash":
        (tmp_path / "proof.json").write_bytes(b"tampered")
    elif mutation == "unknown-check":
        raw["facts"][0]["related_checks"] = ["REQ-999"]
    elif mutation == "missing-file":
        (tmp_path / "proof.json").unlink()
    elif mutation == "escape":
        raw["facts"][0]["evidence"][0]["path"] = "../proof.json"
    else:
        raw["artifacts"] = [{"name": "proof.json", "path": "proof.json", "sha256": sha256_hex(ci.payloads["proof.json"]),
            "bytes": len(ci.payloads["proof.json"]), "collected_at": "2026-10-25T00:00:00Z", "collector": "manual"}]
    raw.pop("content_sha256")
    source = tmp_path / "change.yaml"
    source.write_text(yaml.safe_dump(raw), encoding="utf-8")
    store = tmp_path / "must-not-exist"
    reply = CliRunner().invoke(app, ["select", "--pack", str(PACK_DIR), "--env", str(e), "--policy", str(p),
                                    "--change", str(source), "--store", str(store), "--config", str(tmp_path / "none.toml")])
    assert reply.exit_code != 0
    assert not store.exists()


def test_chg_content_addresses_survive_bundle_and_detect_missing_bytes(tmp_path):
    pack = baseline_pack()
    store = Store(tmp_path / "store")
    saved = save_change(store, read_change(change_input(pack), pack))
    assert saved.artifacts and saved.facts[0].evidence[0].path == saved.artifacts[0].path
    assert not store.verify()
    exported = tmp_path / "bundle.zip"
    before = tree(store.root)
    export_bundle(store.root, exported)
    assert tree(store.root) == before
    restore_bundle(exported, tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    assert restored.resolve(saved.ref()).to_dict() == saved.to_dict()
    assert not restored.verify()
    (restored.root / saved.artifacts[0].path).unlink()
    assert any("missing" in problem for problem in restored.verify())
    with pytest.raises(IntegrityError):
        export_bundle(restored.root, tmp_path / "bad.zip")


def test_stored_chg_requires_all_fact_links_to_point_at_preserved_artifacts(tmp_path):
    pack, store = baseline_pack(), Store(tmp_path / "store")
    saved = save_change(store, change_input(pack))
    fact = saved.facts[0].model_copy(update={"evidence": [saved.facts[0].evidence[0].model_copy(update={"sha256": "0" * 64})]})
    bad = saved.model_copy(update={"id": "CHG-BAD", "facts": [fact]}).seal()
    with pytest.raises(IntegrityError, match="not preserved"):
        store.put(bad)
    with pytest.raises(ValueError, match="not preserved"):
        read_change(ChangeInput(bad, {a.path: (store.root / a.path).read_bytes() for a in bad.artifacts}), pack)


def test_read_only_store_never_creates_or_writes(tmp_path):
    with pytest.raises(ValueError):
        Store.read_only(tmp_path / "absent")
    assert not (tmp_path / "absent").exists()
    store = Store(tmp_path / "store")
    env = environment()
    store.put(env)
    before = tree(store.root)
    ro = Store.read_only(store.root)
    assert ro.resolve(env.ref()) == env
    with pytest.raises(sqlite3.OperationalError):
        ro.put(environment(id="ENV-OTHER"))
    assert tree(store.root) == before


def test_env_patch_is_append_only_latest_and_new_values_are_declared(tmp_path):
    store = Store(tmp_path / "store")
    original = environment(origin=DataOrigin.MEASURED,
        facts={"gateway": {"value": True, "origin": "MEASURED", "source": "old", "absence_evidence": "old-proof"}})
    store.put(original)
    patch = tmp_path / "patch.yaml"
    patch.write_text("facts:\n  gateway: false\n", encoding="utf-8")
    newer = update_object(store, original.id, patch, kind="ENV")
    newest = update_object(store, original.id + "@1", patch, kind="ENV")
    assert newer.id == newest.id == original.id
    assert [newer.revision, newest.revision] == [2, 3]
    assert newer.facts["gateway"].origin == DataOrigin.DECLARED
    assert newer.facts["gateway"].absence_evidence is None
    assert store.resolve(original.ref()).to_dict() == original.to_dict()
    serialized = tmp_path / "updated-env.yaml"
    serialized.write_text(yaml.safe_dump(newer.to_dict()), encoding="utf-8")
    assert load_environment(serialized).ref() == newer.ref()


@pytest.mark.parametrize("patch", [{"revision": 3}, {"id": "ENV-OTHER"}, {"facts": {"gateway": {"value": True, "origin": "MEASURED"}}}])
def test_patch_cannot_change_identity_or_claim_measurement(tmp_path, patch):
    store, env = Store(tmp_path / "store"), environment()
    store.put(env)
    path = tmp_path / "patch.yaml"
    path.write_text(yaml.safe_dump(patch), encoding="utf-8")
    with pytest.raises(ValueError):
        update_object(store, env.id, path, kind="ENV")
    assert store.revisions(env.id) == [1]


def test_policy_patch_preserves_unmentioned_values_and_round_trips(tmp_path):
    pack, store = baseline_pack(), Store(tmp_path / "store")
    put_pack(store, PACK_DIR)
    pol = policy(pack.ref(), values={"REQ-01": {"allowed_callers": ["A"]}, "PLT-01": {"x": 1}})
    store.put(pol)
    patch = tmp_path / "patch.yaml"
    patch.write_text("values:\n  REQ-01:\n    allowed_callers: [B]\n", encoding="utf-8")
    newer = update_object(store, pol.id, patch, kind="POL")
    assert newer.values == {"REQ-01": {"allowed_callers": ["B"]}, "PLT-01": {"x": 1}}
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(newer.to_dict()), encoding="utf-8")
    assert load_policy(path, pack).ref() == newer.ref()
    assert store.resolve(pol.ref()).values["REQ-01"]["allowed_callers"] == ["A"]


def test_legacy_objects_do_not_serialize_u1_fields(tmp_path):
    pack, env, pol, _, _ = files(tmp_path)
    from guard.core.select import select
    legacy = change_input(pack).change
    plan = select(pack, [env], pol)
    for obj in [env, pol, legacy, plan]:
        raw = obj.to_dict()
        loaded = object_from_dict(deepcopy(raw))
        assert loaded.to_dict() == raw
        assert loaded.hash_ok() and loaded.ref() == obj.ref()
    assert "analysis" not in legacy.to_dict() and "artifacts" not in legacy.to_dict()
    assert "previous_plan" not in plan.to_dict()["inputs"]
    assert "change_type" not in plan.to_dict()
    assert all("carried_over" not in item and "triggers" not in item for item in plan.to_dict()["items"])


def test_prepare_update_validates_chg_before_readiness_or_output(tmp_path, monkeypatch):
    import guard.cli as cli
    _, _, _, e, p = files(tmp_path)
    source = tmp_path / "bad.yaml"
    source.write_text("facts: []\n", encoding="utf-8")
    def forbidden(*args, **kwargs):
        pytest.fail("invalid CHG reached preparation or Docker")
    monkeypatch.setattr(cli, "update_readiness", forbidden)
    output = tmp_path / "no-store"
    result = CliRunner().invoke(app, ["prepare-update", "--pack", str(PACK_DIR), "--baseline", str(e), "--candidate", str(e),
        "--policy", str(p), "--change", str(source), "--store", str(output), "--config", str(tmp_path / "none.toml")])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["ready"] is False
    assert not output.exists()


def test_diff_to_cli_report_and_portable_store_without_engine(tmp_path, source_repo):
    pack, _, pol, e, p = files(tmp_path)
    raw = pol.to_dict()
    raw["scope"] = {"include": ["REQ-01"], "exclude": []}
    from guard.models import Policy
    raw["content_sha256"] = ""
    p.write_text(yaml.safe_dump(Policy.model_validate(raw).seal().to_dict()), encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text("[adapter]\nname='fake'\n[executors]\ndirect_route='fake'\n", encoding="utf-8")
    root = tmp_path / "store"
    runner = CliRunner()
    common = ["--store", str(root), "--config", str(config)]
    diff = runner.invoke(app, ["diff", "--from", "v1.0.0", "--to", "v1.0.1", "--repository", str(source_repo),
                              "--pack", str(PACK_DIR), *common])
    assert diff.exit_code == 0, diff.output
    store = Store(root)
    change = list(store.iter_kind("CHG"))[0]
    assert change.artifacts and not store.verify()
    assert change.baseline.version == "1.0.0" and change.candidate.version == "1.0.1"
    selected = runner.invoke(app, ["select", "--pack", str(PACK_DIR), "--env", str(e), "--policy", str(p),
                                  "--change", change.id, *common])
    assert selected.exit_code == 0, selected.output
    plan = list(store.iter_kind("PLAN"))[0]
    assert plan.inputs.change == change.ref()
    assert runner.invoke(app, ["run", "--plan", plan.id, *common]).exit_code == 0
    assert runner.invoke(app, ["judge", "--plan", plan.id, *common]).exit_code == 0
    report_json = tmp_path / "report.json"
    reported = runner.invoke(app, ["report", "--plan", plan.id, "--json", str(report_json), *common])
    assert reported.exit_code == 0, reported.output
    report = json.loads(report_json.read_text(encoding="utf-8"))
    assert report["official"] is False and report["change_summary"]
    assert not report_json.with_suffix(".html").exists()
    assert all(stage["observed"] is not True for entry in report["change_summary"]["entrypoints"] for stage in entry["stages"])
    export_bundle(root, tmp_path / "bundle.zip")
    restore_bundle(tmp_path / "bundle.zip", tmp_path / "restored")
    assert not Store(tmp_path / "restored").verify()


def test_harness_forwards_change_and_previous_refs_without_relaxing_acceptance(tmp_path, monkeypatch):
    from guard import update_validation as harness
    seen = []
    def command(argv, label, timeout):
        seen.append((label, argv))
        return {"returncode": 1, "timed_out": False}, "{}"
    monkeypatch.setattr(harness, "_command", command)
    paths = {name: tmp_path / name for name in ("pack", "baseline", "candidate", "policy", "config")}
    result = harness.execute_run(paths, tmp_path / "out", {}, {}, timeout=1,
        change="CHG-EXAMPLE@1", previous_plan="PLAN-EXAMPLE@2", previous_store=tmp_path / "prior")
    assert result["accepted"] is False
    assert result["stage"] == "preparation-insufficient"
    argv = seen[0][1]
    assert argv[argv.index("--change") + 1] == "CHG-EXAMPLE@1"
    assert argv[argv.index("--previous-plan") + 1] == "PLAN-EXAMPLE@2"
    assert argv[argv.index("--store") + 1] == str(tmp_path / "prior")
    assert len(seen) == 1


def test_product_update_preserves_change_graph_with_two_fake_sides(tmp_path):
    from guard.application import execute_update
    from tests.builders import T0
    pack = baseline_pack()
    a = environment(id="ENV-U1-A", engine={"name": "engine-x", "version": "1.0.0", "image_digest": "a" * 64})
    b = environment(id="ENV-U1-B", engine={"name": "engine-x", "version": "1.0.1", "image_digest": "b" * 64})
    pol = policy(pack.ref(), scope={"include": ["REQ-01"]})
    store = Store(tmp_path / "store")
    cfg = {"update": {"mode": "external"}, "adapter": {"name": "fake"}, "executors": {"direct_route": "fake"}}
    report, failed = execute_update(store, pack, [a, b], pol, cfg, tmp_path / "run", T0, change=change_input(pack))
    assert not failed
    assert len(list(store.iter_kind("EVD"))) == 2
    assert len(list(store.iter_kind("RES"))) == 2
    chg = list(store.iter_kind("CHG"))[0]
    assert chg.ref() in report.inputs_manifest
    assert report.change_summary["change_type"] == "INITIAL"
    assert all(e.data_origin == DataOrigin.EXAMPLE for e in store.iter_kind("EVD"))
    assert report.official is False
    assert not store.verify()
