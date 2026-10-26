"""빈 양식 → 기존 로더/선정 계약 및 설치 가능한 선언 기반 생성 경계."""

import csv
from copy import deepcopy

import pytest
import yaml
from typer.testing import CliRunner

import guard.cli as cli
from guard.collectors.file import load_policy
from guard.collectors.policy_template import build_policy_template, write_policy_template
from guard.core.select import PolicyValueError, select
from guard.executors.input_validation import InputValidationExecutor
from guard.models import load_pack, sha256_hex
from tests.builders import PACK_DIR, ROOT, baseline_pack, environment


runner = CliRunner()


def _env():
    return environment({"gateway": {"value": True, "origin": "DECLARED", "source": "test fixture"}})


def _load(tmp_path, pack, values=None, scope=None):
    data = yaml.safe_load(build_policy_template(pack))
    if values is not None:
        data["values"] = values
    if scope is not None:
        data["scope"] = scope
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_policy(path, pack)


def _other_pack(tmp_path, params=None, *, retired=False):
    """다른 팩·점검 ID와 임의 파라미터 이름을 가진 팩. 실행기는 필요 없다."""
    check = deepcopy(baseline_pack().checks["REQ-01"].model_dump(mode="json"))
    check.update(id="REQ-42", retired=retired, executor=None, judge=None,
                 policy_params=params if params is not None else [
                     {"key": "budget", "type": "int", "required": True},
                     {"key": "enabled", "type": "bool", "required": True},
                     {"key": "names", "type": "list[str]", "required": True},
                     {"key": "note", "type": "str", "required": False}],
                 policy_prose={"policy_to_decide": "새 팩의 정책 문맥\nstatus: CONFIRMED"})
    body = yaml.safe_dump(check, allow_unicode=True).encode("utf-8")
    path = tmp_path / "arbitrary-pack"
    (path / "checks").mkdir(parents=True)
    (path / "checks/REQ-42.yaml").write_bytes(body)
    manifest = {"pack_id": "PACK-CUSTOM", "revision": 23, "engines": ["example"],
                "checks": ["REQ-42"], "files_sha256": [
                    {"path": "checks/REQ-42.yaml", "sha256": sha256_hex(body)}],
                "review": {"status": "DRAFT"}}
    (path / "pack.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return path


def test_current_declarations_metadata_and_empty_values(tmp_path):
    pack = baseline_pack()
    before = {p: p.read_bytes() for p in PACK_DIR.rglob("*") if p.is_file()}
    text = build_policy_template(pack)
    data = yaml.safe_load(text)
    declarations = {(c.id, p.key) for c in pack.active_checks() for p in c.policy_params}
    # Previous 54 declarations plus 11 explicit PLT-07 inputs, no enterprise values.
    assert len(declarations) == 75
    assert {cid for cid, key in declarations} == {"REQ-01", "REQ-03", "REQ-07", "REQ-09", "REQ-10", "REQ-11", "REQ-13", "OBS-03", "OBS-09", "PLT-01", "PLT-02", "PLT-04", "PLT-07", "PLT-08", "SUP-14", "LIF-07", "SUP-01", "SUP-02", "OBS-11", "OBS-04", "OBS-05", "OBS-07", "OBS-08", "REQ-15", "REQ-16", "SUP-04", "SUP-03", "SUP-13"}
    assert {(cid, key) for cid, key in declarations if cid == "SUP-04"} == {("SUP-04", "approved_image_digests")}
    assert {(cid, key) for cid, key in declarations if cid == "SUP-03"} == {("SUP-03", "allowed_template_sha256")}
    assert set(data["values"]) == {cid for cid, key in declarations}
    assert all(value == {} for value in data["values"].values())
    assert text.count("# 파라미터: ") == len(declarations)
    for cid, key in declarations:
        assert f"# 파라미터: values.{cid}.{key}\n" in text
    # 카탈로그는 런타임 의존성이 아니라 설명의 검토 출처다.
    with (ROOT / "docs/policy_catalog.csv").open(encoding="utf-8", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row["classification"] == "a"]
    for row in rows:
        assert row["description"] in text
    assert "실제 담당자 미지정" in text
    assert "파라미터별 소비 미확인" not in text
    assert data["status"] == "DRAFT"
    assert data["data_origin"] == "DECLARED"
    assert data["confirmed_by"] is None and data["confirmed_at"] is None
    assert data["scope"] == {"include": [], "exclude": []}
    assert data["pack"] == {"id": pack.manifest.pack_id, "revision": pack.manifest.revision}
    assert text == build_policy_template(pack)
    assert all(p.read_bytes() == content for p, content in before.items())


def test_blank_and_partial_forms_keep_existing_selection_gaps(tmp_path):
    pack = baseline_pack()
    plan = select(pack, [_env()], _load(tmp_path, pack))
    assert len(plan.items) == 77 and all(i.selected for i in plan.items)
    for check in pack.active_checks():
        assert ("NO_POLICY_VALUE" in plan.item(check.id).gaps) == bool(check.policy_params)
    partial = _load(tmp_path, pack, {"REQ-01": {"allowed_callers": ["allowed"]},
                                   "PLT-01": {"require_non_root": False}})
    plan = select(pack, [_env()], partial)
    assert "NO_POLICY_VALUE" not in plan.item("REQ-01").gaps
    assert "NO_POLICY_VALUE" in plan.item("PLT-01").gaps
    assert "NO_POLICY_VALUE" in plan.item("REQ-09").gaps
    excluded = _load(tmp_path, pack, scope={"include": ["REQ-01"], "exclude": []})
    plan = select(pack, [_env()], excluded)
    assert "NO_POLICY_VALUE" in plan.item("REQ-01").gaps
    assert not plan.item("REQ-09").selected and plan.item("REQ-09").gaps == []


@pytest.mark.parametrize("key,bad", [
    ("max_input_tokens", None), ("max_input_tokens", True), ("max_input_tokens", "8"),
    ("rejection_codes", None), ("rejection_codes", "400"), ("rejection_codes", [400]),
])
def test_filled_template_rejects_invalid_declared_values(tmp_path, key, bad):
    pack = baseline_pack()
    pol = _load(tmp_path, pack, {"REQ-09": {key: bad}})
    with pytest.raises(PolicyValueError):
        select(pack, [_env()], pol)


def test_empty_list_false_and_zero_are_values_not_missing(tmp_path):
    pack = load_pack(_other_pack(tmp_path))
    pol = _load(tmp_path, pack, {"REQ-42": {"budget": 0, "enabled": False, "names": []}})
    plan = select(pack, [_env()], pol)
    assert "NO_POLICY_VALUE" not in plan.item("REQ-42").gaps
    assert pol.values["REQ-42"] == {"budget": 0, "enabled": False, "names": []}


@pytest.mark.parametrize("values,budget", [
    ({"max_input_tokens": 0, "rejection_codes": ["400"]}, 8),
    ({"max_input_tokens": 8, "rejection_codes": ["400"]}, 8),
    ({"max_input_tokens": 1, "rejection_codes": []}, 8),
    ({"max_input_tokens": 1, "rejection_codes": ["401"]}, 8),
])
def test_req09_semantic_guards_are_not_relaxed_by_template(tmp_path, values, budget):
    pack = baseline_pack()
    values = {**values, "allowed_endpoints": [pack.checks["REQ-09"].functional_probes[0].id],
              "allowed_input_formats": ["token_ids"]}  # EXAMPLE complete scope; test the numeric guard.
    pol = _load(tmp_path, pack, {"REQ-09": values})
    plan = select(pack, [_env()], pol)
    assert "NO_POLICY_VALUE" not in plan.item("REQ-09").gaps
    executor = InputValidationExecutor({"vocab_size": 10, "vocab_source": "EXAMPLE test fixture",
                                        "max_probe_tokens": budget})
    with pytest.raises(ValueError):
        executor.validate_policy(pack.checks["REQ-09"], pol.values["REQ-09"])


def test_req09_boundary_fits_probe_budget(tmp_path):
    pack = baseline_pack()
    values = {"max_input_tokens": 7, "rejection_codes": ["400", "413", "422"],
              "allowed_endpoints": [pack.checks["REQ-09"].functional_probes[0].id],
              "allowed_input_formats": ["token_ids"]}
    pol = _load(tmp_path, pack, {"REQ-09": values})
    executor = InputValidationExecutor({"vocab_size": 10, "vocab_source": "EXAMPLE test fixture",
                                        "max_probe_tokens": 8})
    executor.validate_policy(pack.checks["REQ-09"], pol.values["REQ-09"])


def test_arbitrary_pack_comments_cannot_inject_yaml_and_no_baseline_metadata(tmp_path):
    path = _other_pack(tmp_path)
    pack = load_pack(path)
    text = build_policy_template(pack)
    data = yaml.safe_load(text)
    assert data["pack"] == {"id": "PACK-CUSTOM", "revision": 23}
    assert data["status"] == "DRAFT"
    assert data["values"] == {"REQ-42": {}}
    assert "values.REQ-42.budget" in text and "values.REQ-42.note" in text
    assert "# 타입 / 필수: str / 아니오" in text
    assert "# 정책 결정 역할: 미지정" in text
    assert "# 설명: 미지정" in text and "파라미터별 소비 미확인" in text
    assert "P-DRAFT" not in text and "allowed_callers" not in text


def test_changed_pack_ref_does_not_reuse_old_description():
    pack = baseline_pack().model_copy(deep=True)
    pack.manifest.revision += 1
    text = build_policy_template(pack)
    assert "# 설명: 미지정" in text
    assert "Phase 1 카탈로그" not in text


@pytest.mark.parametrize("retired,params", [(True, None), (False, [])])
def test_no_active_parameters_is_a_loadable_empty_form(tmp_path, retired, params):
    pack = load_pack(_other_pack(tmp_path, params, retired=retired))
    text = build_policy_template(pack)
    assert yaml.safe_load(text)["values"] == {}
    assert "# 파라미터:" not in text
    assert _load(tmp_path, pack).values == {}


def test_cli_no_store_env_config_or_docker_and_exclusive_utf8_lf(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "guard.toml").write_text("not valid toml [", encoding="utf-8")
    monkeypatch.setattr(cli, "_store", lambda *a: pytest.fail("template must not open Store"))
    output = tmp_path / "nested/policy.yaml"
    args = ["policy", "template", "--pack", str(PACK_DIR), "--out", str(output)]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output + repr(result.exception)
    content = output.read_bytes()
    assert b"\r" not in content and content.endswith(b"\n")
    assert "정책 입력 양식" in content.decode("utf-8")
    assert not (tmp_path / ".guard").exists()
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 1 and "정책 양식 생성 실패" in result.output
    assert output.read_bytes() == content
    second = tmp_path / "second.yaml"
    write_policy_template(baseline_pack(), second)
    assert second.read_bytes() == content


def test_invalid_pack_has_no_output_and_existing_directory_is_protected(tmp_path):
    path = _other_pack(tmp_path)
    (path / "checks/REQ-42.yaml").write_text("tampered", encoding="utf-8")
    output = tmp_path / "policy.yaml"
    result = runner.invoke(cli.app, ["policy", "template", "--pack", str(path), "--out", str(output)])
    assert result.exit_code == 1 and not output.exists()
    result = runner.invoke(cli.app, ["policy", "template", "--pack", str(PACK_DIR), "--out", str(tmp_path)])
    assert result.exit_code == 1 and tmp_path.is_dir()
