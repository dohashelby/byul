"""합성 소스의 rename 미분류가 실제 CLI의 선정·보고·반출까지 보존된다.

소스 파일·이미지 식별자는 모두 EXAMPLE이다. 로컬 git만 사용하며 fake의
판정은 실제 엔진의 보안 상태나 이미지 소스의 진위를 입증하지 않는다.
"""

from __future__ import annotations

import json
import http.client
from pathlib import Path
import socket
import subprocess

import pytest
from typer.testing import CliRunner
import yaml

from guard.cli import app
from guard.models import DataOrigin, sha256_hex
from guard.runtime.docker import Docker, DockerError
from guard.store import Store
from tests.builders import PACK_DIR, baseline_pack, environment, policy


SCOPE = {"REQ-01", "REQ-09", "REQ-16", "SUP-01"}
BEFORE_PATH = "vllm/entrypoints/example_before.py"
AFTER_PATH = "vllm/new_unreviewed_domain/example_after.py"


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


@pytest.fixture(scope="module")
def renamed_source(tmp_path_factory):
    """R100: 알려진 API 경로에서 미분류 제품 경로로 같은 바이트를 이동한다."""
    repo = tmp_path_factory.mktemp("u1-example-rename")
    _git(repo, "init")
    _git(repo, "config", "user.email", "example@offline.invalid")
    _git(repo, "config", "user.name", "EXAMPLE offline source")
    _git(repo, "config", "core.autocrlf", "false")
    source = repo / BEFORE_PATH
    source.parent.mkdir(parents=True)
    source.write_bytes(b"@router.get('/example')\r\ndef example():\r\n    raise RuntimeError('never import EXAMPLE')\r\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-m", "EXAMPLE baseline")
    _git(repo, "tag", "v1.0.0")
    destination = repo / AFTER_PATH
    destination.parent.mkdir(parents=True)
    source.rename(destination)
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-m", "EXAMPLE rename")
    _git(repo, "tag", "v1.0.1")
    _git(repo, "tag", "v1.1.0")
    return repo


@pytest.fixture
def offline_only(monkeypatch):
    """git 로컬 읽기 이외의 취득·통신·컨테이너 호출은 시험 자체를 실패시킨다."""
    from guard.adapters import git_diff
    from guard.runtime import sandbox

    def forbidden(*args, **kwargs):
        pytest.fail("offline selection contract attempted a network or engine operation")

    monkeypatch.setattr(git_diff, "prepare_source_repository", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(http.client.HTTPConnection, "request", forbidden)
    monkeypatch.setattr(Docker, "_docker", forbidden)
    monkeypatch.setattr(sandbox, "up", forbidden)
    probes = []

    def unavailable_daemon(*args, **kwargs):
        probes.append("EXAMPLE offline daemon boundary")
        raise DockerError("EXAMPLE: Docker is intentionally unavailable")

    monkeypatch.setattr(Docker, "daemon_info", unavailable_daemon)
    return probes


def _tree(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): sha256_hex(path.read_bytes())
            for path in root.rglob("*") if path.is_file()}


def _invoke(runner: CliRunner, arguments: list[str], expected=0):
    result = runner.invoke(app, arguments)
    assert result.exit_code == expected, f"{result.output}\n{result.exception!r}"
    return result


def _case(tmp_path, renamed_source, *, candidate_version="1.0.1", relation="MATCH"):
    runner, pack = CliRunner(), baseline_pack()
    config = tmp_path / "offline.toml"
    config.write_text("[update]\nmode='external'\n[adapter]\nname='fake'\n"
                      "[executors]\ndirect_route='fake'\n", encoding="utf-8")
    root = tmp_path / "store"
    common = ["--store", str(root), "--config", str(config)]
    _invoke(runner, ["diff", "--from", "v1.0.0", "--to", f"v{candidate_version}",
                     "--repository", str(renamed_source), "--pack", str(PACK_DIR), *common])
    store = Store(root)
    change, = store.iter_kind("CHG")
    before = environment(id="ENV-EXAMPLE-PATH-BEFORE", engine={
        "name": "engine-x", "version": "1.0.0", "source_commit": change.baseline.source_commit,
        "source_commit_status": "MATCH", "image_digest": "EXAMPLE@sha256:" + "a" * 64})
    after = environment(id="ENV-EXAMPLE-PATH-AFTER", engine={
        "name": "engine-x", "version": candidate_version, "source_commit": change.candidate.source_commit,
        "source_commit_status": relation, "image_digest": "EXAMPLE@sha256:" + "b" * 64})
    pol = policy(pack.ref(), scope={"include": sorted(SCOPE), "exclude": []}, values={
        "REQ-01": {"allowed_callers": ["allowed"]},
        "REQ-09": {"max_input_tokens": 16, "rejection_codes": ["400"],
                   "allowed_endpoints": ["POST /v1/completions"], "allowed_input_formats": ["token_ids"]},
        "REQ-16": {"required_cache_contexts": ["model_generation"]},
        "SUP-01": {"approval_record_system": "EXAMPLE offline approval records"}})
    paths = {}
    for name, obj in (("baseline", before), ("candidate", after), ("policy", pol)):
        path = tmp_path / f"{name}.yaml"
        path.write_text(yaml.safe_dump(obj.to_dict()), encoding="utf-8")
        paths[name] = path
    _invoke(runner, ["select", "--pack", str(PACK_DIR), "--env", str(paths["baseline"]),
                     "--policy", str(paths["policy"]), *common])
    previous, = store.iter_kind("PLAN")
    arguments = ["--pack", str(PACK_DIR), "--baseline", str(paths["baseline"]),
                 "--candidate", str(paths["candidate"]), "--policy", str(paths["policy"]),
                 "--change", f"{change.id}@{change.revision}", *common]
    return runner, store, change, previous, arguments, common


def _updated_case(tmp_path, case, *, previous=True):
    runner, store, change, prior, arguments, common = case
    previous_args = ["--previous-plan", f"{prior.id}@{prior.revision}"] if previous else []
    # REQ-09는 실행기를 설정하지 않았으므로 NOT_RUN을 남기며 명령은 실패를 알린다.
    # 이 실패를 선정 실패나 보안 FAIL, 실제 실측 성공으로 바꾸지 않는다.
    _invoke(runner, ["update", *arguments, *previous_args, "--output", str(tmp_path / "reports")], expected=1)
    plans = [plan for plan in store.iter_kind("PLAN") if plan.id != prior.id]
    plan, = plans
    report_path, = (tmp_path / "reports").glob("*/report.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return plan, report, report_path


def test_partial_rename_reaches_cli_selection_report_and_portable_evidence(tmp_path, renamed_source, offline_only):
    case = _case(tmp_path, renamed_source)
    runner, store, change, previous, arguments, common = case
    assert change.extractor.version == "0.2.0"
    payload = json.loads((store.root / change.artifacts[0].path).read_bytes())
    assert payload["schema"] == "guard.engine-source-diff/2"
    row, = payload["changes"]
    assert row["status"] == "R100" and row["unmapped"] is True
    assert row["before_sha256"] == row["after_sha256"]
    assert {entry["side"]: entry["unmapped"] for entry in row["path_classifications"]} == {
        "baseline": False, "candidate": True}
    unknown, = [fact for fact in change.facts if fact.unmapped]
    assert unknown.category == "OTHER" and unknown.certainty == "UNKNOWN" and unknown.target == AFTER_PATH
    assert any(fact.category == "API_ROUTE" and fact.certainty == "PARTIAL" for fact in change.facts)

    before = _tree(store.root)
    prepared = _invoke(runner, ["prepare-update", *arguments, "--previous-plan", previous.id], expected=1)
    assert json.loads(prepared.stdout)["ready"] is False
    assert len(offline_only) == 1
    assert _tree(store.root) == before
    assert not (tmp_path / "reports").exists()
    assert not list(store.iter_kind("EVD")) and not list(store.iter_kind("RES"))

    plan, report, report_path = _updated_case(tmp_path, case)
    assert plan.change_type == "ENGINE_PATCH" and plan.inputs.change == change.ref()
    assert plan.inputs.previous_plan == previous.ref()
    selected = {item.check_id: item for item in plan.items if item.selected}
    assert set(selected) == SCOPE
    # 미분류 확대가 없으면 REQ-16은 재사용 거부로만 선정된다. 최종 수가 같아도
    # 원인 누락을 잡도록 실제 경로 유래 trigger와 fact Ref를 검사한다.
    fallback = {cid for cid, item in selected.items()
                if any(trigger.type == "UNMAPPED_ENGINE" for trigger in item.triggers)}
    assert fallback == {"REQ-01", "REQ-09", "REQ-16"}
    assert any(trigger.type == "PATCH_ALWAYS" for trigger in selected["REQ-09"].triggers)
    assert any(trigger.type == "ENGINE_DIFF" for trigger in selected["REQ-01"].triggers)
    assert any(trigger.type == "REUSE_REJECTED" and trigger.refs == ["NO_PRIOR_RESULT"]
               for trigger in selected["SUP-01"].triggers)
    assert all(trigger.refs == [unknown.id] for item in selected.values()
               for trigger in item.triggers if trigger.type == "UNMAPPED_ENGINE")
    assert not any(item.carried_over for item in plan.items)
    assert report["official"] is False and report["data_origin"] == "EXAMPLE"
    assert report["carry_counts"] == {"selected": 4, "executed": 1, "measured": 0,
                                      "carried": 0, "not_run": 3, "manual_required": 3}
    summary = report["change_summary"]
    assert summary["source_image_relation"] == "CONFIRMED"  # EXAMPLE 메타데이터 대조만이다.
    assert [value["target"] for value in summary["unmapped"]] == [AFTER_PATH]
    assert "SOURCE_IMAGE_RELATION_USES_DECLARED_METADATA_NOT_IMAGE_CONTENT_ATTESTATION" in summary["limitations"]
    assert all(stage["observed"] is not True for entry in summary["entrypoints"] for stage in entry["stages"])
    # BYUL에서는 JSON이 원본이며 HTML 보고서 대신 정확한 원본 필드·Ref를 확인한다.
    assert not report_path.with_suffix(".html").exists()
    reported = {row["check_id"]: row for row in report["by_check"]}
    assert all(any(trigger["type"] == "UNMAPPED_ENGINE"
                   for trigger in reported[cid]["triggers"]) for cid in fallback)
    result_refs = [row[side]["result"] for row in report["by_check"]
                   for side in ("single", "baseline", "candidate")
                   if isinstance(row.get(side), dict) and row[side].get("result")]
    for value in store.iter_kind("RES"):
        assert value.ref().model_dump(mode="json") in result_refs
    assert all(value.data_origin == DataOrigin.EXAMPLE for value in store.iter_kind("EVD"))
    _invoke(runner, ["verify", *common])
    before = _tree(store.root)
    bundle, restored = tmp_path / "bundle.zip", tmp_path / "restored"
    _invoke(runner, ["evidence", "export", "--store", str(store.root), "--output", str(bundle)])
    assert _tree(store.root) == before
    _invoke(runner, ["evidence", "restore", "--bundle", str(bundle), "--destination", str(restored)])
    restored_store = Store(restored)
    assert restored_store.resolve(change.ref()).to_dict() == change.to_dict()
    assert restored_store.resolve(plan.ref()).to_dict() == plan.to_dict()
    _invoke(runner, ["verify", "--store", str(restored), "--config", str(tmp_path / "absent.toml")])
    (restored / change.artifacts[0].path).write_bytes(b"EXAMPLE corrupted source evidence")
    _invoke(runner, ["verify", "--store", str(restored), "--config", str(tmp_path / "absent.toml")], expected=1)
    assert _tree(store.root) == before


@pytest.mark.parametrize("version,relation,previous,kind,reason", [
    ("1.0.1", "MATCH", False, "INITIAL", "NO_PREVIOUS_PLAN"),
    ("1.1.0", "MATCH", True, "ENGINE_RELEASE", "ENGINE_RELEASE"),
    ("1.0.1", "UNVERIFIED", True, "ENGINE_PATCH", "SOURCE_IMAGE_RELATION_UNVERIFIED_OR_MISMATCH"),
])
def test_cli_does_not_reduce_scope_without_patch_preconditions(
        tmp_path, renamed_source, offline_only, version, relation, previous, kind, reason):
    case = _case(tmp_path, renamed_source, candidate_version=version, relation=relation)
    plan, report, _ = _updated_case(tmp_path, case, previous=previous)
    selected = [item for item in plan.items if item.selected]
    assert plan.change_type == kind
    assert {item.check_id for item in selected} == SCOPE
    assert all(f"U1 full scope: {reason}" in item.reason for item in selected)
    assert not any(item.carried_over for item in plan.items)
    assert report["carry_counts"]["selected"] == 4
    assert report["carry_counts"]["carried"] == report["carry_counts"]["measured"] == 0
    assert report["official"] is False
    assert not offline_only  # update의 external fake 경로는 daemon 조회조차 하지 않는다.
