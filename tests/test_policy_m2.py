"""M2: 정책 등록·수동 입력 전제·팩 출력 경계·설명 갱신 계약."""

import csv
import json
import os
import shutil

import pytest
import yaml
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors.file import load_policy
from guard.collectors.policy_template import write_policy_template
from guard.core.select import PolicyValueError, select
from guard.judges.declared import DeclaredJudge
from guard.models.checkdef import PolicyParam
from guard.store import Store
from tests.builders import PACK_DIR, ROOT, T0, baseline_pack, environment, evidence
from tools.build_policy_template_metadata import build_metadata

runner = CliRunner()
PARAMS = {"SUP-14": "required_checks", "LIF-07": "required_readiness_items"}


def _ok(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output + repr(result.exception)
    return result.output


def _pol(tmp_path, values):
    path = tmp_path / "policy.yaml"
    write_policy_template(baseline_pack(), path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["values"] = values
    data["scope"]["include"] = list(PARAMS)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_policy(path, baseline_pack())


def test_new_declarations_do_not_create_automatic_executors():
    pack = baseline_pack()
    assert len(pack.checks) == 77
    assert sum(bool(c.policy_params) for c in pack.active_checks()) == 7
    assert sum(len(c.policy_params) for c in pack.active_checks()) == 13
    assert {c.id for c in pack.active_checks() if c.executor} == {"REQ-01", "REQ-03", "PLT-01", "PLT-04", "REQ-09"}
    for cid, key in PARAMS.items():
        check = pack.checks[cid]
        assert [(p.key, p.type, p.required) for p in check.policy_params] == [(key, "list[str]", True)]
        assert check.executor is None and check.judge is None


@pytest.mark.parametrize("cid", list(PARAMS))
@pytest.mark.parametrize("bad", [None, "EXAMPLE-id", [7], False])
def test_new_lists_reject_invalid_types(tmp_path, cid, bad):
    pol = _pol(tmp_path, {cid: {PARAMS[cid]: bad}})
    with pytest.raises(PolicyValueError, match=cid):
        select(baseline_pack(), [environment()], pol)


def test_partial_and_explicit_empty_policy_preserve_no_executor(tmp_path):
    pack = baseline_pack()
    pol = _pol(tmp_path, {"SUP-14": {"required_checks": []}})
    plan = select(pack, [environment()], pol)
    assert plan.item("SUP-14").gaps == ["NO_EXECUTOR"]
    assert set(plan.item("LIF-07").gaps) == {"NO_EXECUTOR", "NO_POLICY_VALUE"}


def _manual_evidence(check, verdicts):
    return evidence(check_id=check.id, observations={
        "declared_verdicts": {"value": verdicts, "collected": True},
        "declared_observation_notes": {"value": {k: "EXAMPLE note" for k in check.required_observations_prose},
                                       "collected": True}},
        artifacts=[{"name": "EXAMPLE unit fixture", "path": "unit-only", "sha256": "0"*64,
                    "bytes": 0, "collected_at": T0, "collector": "EXAMPLE"}])


def test_declared_required_presence_is_not_truthiness_or_optional_requirement():
    check = baseline_pack().checks["SUP-14"].model_copy(deep=True)
    check.policy_params = [PolicyParam(key="names", type="list[str]"),
                           PolicyParam(key="enabled", type="bool"), PolicyParam(key="budget", type="int"),
                           PolicyParam(key="optional", type="str", required=False)]
    evd = _manual_evidence(check, {axis: "PASS" for axis in ["security", "functional", "performance"]})
    values = {"names": [], "enabled": False, "budget": 0}
    result = DeclaredJudge().judge(check, evd, {}, values)
    assert all(v == "PASS" for v in result.verdicts.values())
    del values["enabled"]
    result = DeclaredJudge().judge(check, evd, {}, values)
    assert all(v == "UNKNOWN" for v in result.verdicts.values())
    assert any("NO_POLICY_VALUE" in r and "enabled" in r for r in result.reasons)


def test_declared_fail_survives_missing_policy_and_all_basis():
    check = baseline_pack().checks["SUP-14"]
    evd = _manual_evidence(check, {"security": "FAIL", "functional": "PASS", "performance": "UNKNOWN"})
    evd.artifacts = []
    evd.observations["declared_observation_notes"].value = {}
    result = DeclaredJudge().judge(check, evd, {}, {})
    assert result.verdicts == {"security": "FAIL", "functional": "UNKNOWN", "performance": "UNKNOWN"}


@pytest.mark.parametrize("mode,declared", [("missing", "PASS"), ("partial", "PASS"), ("filled", "PASS"), ("missing", "FAIL")])
def test_cli_real_attachment_policy_to_manual_judgment(tmp_path, monkeypatch, mode, declared):
    monkeypatch.chdir(tmp_path)
    _ok(["policy", "template", "--pack", str(PACK_DIR), "--out", "policy.yaml"])
    data = yaml.safe_load((tmp_path / "policy.yaml").read_text(encoding="utf-8"))
    data["scope"]["include"] = [*PARAMS, "SUP-01"]
    supplied = set() if mode == "missing" else ({"SUP-14"} if mode == "partial" else set(PARAMS))
    for cid in supplied:
        data["values"][cid] = {PARAMS[cid]: ["EXAMPLE-enterprise-item"]}
    (tmp_path / "policy.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    shutil.copyfile(ROOT / "examples/env.yaml", tmp_path / "env.yaml")
    plan_id = _ok(["select", "--pack", str(PACK_DIR), "--env", "env.yaml", "--policy", "policy.yaml"]).strip().splitlines()[-1]
    _ok(["evidence", "template", "--plan", plan_id, "--pack", str(PACK_DIR), "--out", "manual.yaml"])
    document = yaml.safe_load((tmp_path / "manual.yaml").read_text(encoding="utf-8"))
    attachment = tmp_path / "synthetic-evidence.txt"
    attachment.write_text("EXAMPLE synthetic attachment; no engine executed\n", encoding="utf-8")
    for entry in document["checks"]:
        entry["verdicts"] = {"security": declared, "functional": "PASS", "performance": "UNKNOWN"}
        entry["normal_control"]["result"] = "succeeded"
        for step in entry["challenges"]:
            step["attempted"] = True
        for note in entry["observation_notes"]:
            note["answer"] = "EXAMPLE synthetic observation"
        entry.update(attachments=[attachment.name], entered_by="EXAMPLE test operator", entered_at=T0.isoformat(),
                     basis="EXAMPLE synthetic contract test; no engine executed")
    (tmp_path / "manual.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    _ok(["evidence", "import", "manual.yaml", "--pack", str(PACK_DIR)])
    args = ["judge", "--plan", plan_id, "--pack", str(PACK_DIR), "--as-of", T0.isoformat()]
    _ok(args)
    store = Store(tmp_path / ".guard")
    results = list(store.iter_kind("RES"))
    for res in results:
        expected = declared if declared == "FAIL" or res.check_id not in PARAMS or res.check_id in supplied else "UNKNOWN"
        assert res.verdicts.security == expected
        assert res.judge.version == "0.2.0"
        if expected == "UNKNOWN":
            assert any("NO_POLICY_VALUE" in reason and PARAMS[res.check_id] in reason for reason in res.reasons)
    original = {obj.ref().sha256: obj.to_dict() for obj in [*store.iter_kind("EVD"), *results]}
    _ok(args)  # 동일 입력/판정 시각 재판정은 멱등이며 과거 객체를 덮지 않는다.
    assert {o.ref().sha256: o.to_dict() for o in [*store.iter_kind("EVD"), *store.iter_kind("RES")]} == original
    assert not store.verify()
    assert attachment.read_text(encoding="utf-8").startswith("EXAMPLE")


def test_template_rejects_inside_pack_before_creating_directories(tmp_path):
    target = tmp_path / "pack"
    shutil.copytree(PACK_DIR, target)
    from guard.models import load_pack
    pack = load_pack(target)
    before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="입력 팩 밖"):
        write_policy_template(pack, target / "new-directory/policy.yaml")
    assert not (target / "new-directory").exists()
    assert {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()} == before
    assert load_pack(target).ref() == pack.ref()


def test_template_rejects_symlink_parent_into_input_pack(tmp_path):
    from guard.models import load_pack
    target = tmp_path / "pack"
    shutil.copytree(PACK_DIR, target)
    link = tmp_path / "alias"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"디렉터리 symlink 권한/지원 필요: {type(exc).__name__}")
    with pytest.raises(ValueError, match="입력 팩 밖"):
        write_policy_template(load_pack(target), link / "new-directory/policy.yaml")
    assert not (target / "new-directory").exists()


def test_packaged_metadata_matches_catalog_and_pack():
    expected = build_metadata(baseline_pack(), ROOT / "docs/policy_catalog.csv")
    actual = json.loads((ROOT / "src/guard/collectors/policy_template_metadata.json").read_text(encoding="utf-8"))
    assert expected == actual and len(actual["parameters"]) == 13
    for cid, key in PARAMS.items():
        assert f"values.{cid}.{key}" in actual["parameters"]


@pytest.mark.parametrize("issue", ["missing", "duplicate", "type", "foreign"])
def test_metadata_builder_rejects_stale_or_ambiguous_bindings(tmp_path, issue):
    with (ROOT / "docs/policy_catalog.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames
        rows = [r for r in reader if r["classification"] == "a"]
    if issue == "missing":
        rows.pop()
    elif issue == "duplicate":
        rows.append(dict(rows[0]))
    elif issue == "type":
        rows[0]["proposed_shape"] = "str"
    else:
        rows[0]["current_param_paths"] = '["values.REQ-99.foreign"]'
    catalog = tmp_path / "catalog.csv"
    with catalog.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader(); writer.writerows(rows)
    with pytest.raises(ValueError):
        build_metadata(baseline_pack(), catalog)
