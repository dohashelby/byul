"""GPU18 공개 API의 독립 검토 계약.

검토 범위: 정확한 18개와 정상/위반/관측 누락, EXAMPLE/DRAFT/비공식,
실제 EVD→RES→RPT 신원, 모의 사건 표시, 원팩 대조, 저장 팩 재판정,
반출물 변조 거부, 전역 registry 복원 및 로컬 실행 경계.
기존 실측이나 제품 판정을 고치지 않는다. 구현이 없는 동안에도 수집 가능하며,
실행 시 구현 부재를 skip하거나 기대값 완화로 숨기지 않는다.
"""

from datetime import datetime
import hashlib
import importlib
import json
import os
from pathlib import Path
import runpy
import socket
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import zipfile

import pytest


EXPECTED_IDS = frozenset({
    "LIF-06", "LIF-08", "LIF-11", "LIF-12", "LIF-16", "OBS-02",
    "PLT-02", "REQ-08", "REQ-09", "REQ-10", "SUP-03", "SUP-05",
    "SUP-06", "SUP-08", "SUP-09", "SUP-10", "SUP-11", "SUP-16",
})
EXPECTED_VERDICTS = {"normal": "PASS", "violation": "FAIL", "missing": "UNKNOWN"}
KINDS = ("ENV", "POL", "CHG", "PLAN", "EVD", "RES", "RPT")


@pytest.fixture(scope="module", autouse=True)
def no_external_execution(tmp_path_factory):
    """금지 호출을 삼켜도 시도 목록이 남는다. CLI도 같은 프로세스에서 실행한다."""
    attempts = []

    def deny(operation):
        def blocked(*args, **kwargs):
            attempts.append(operation)
            raise AssertionError(f"prototype attempted external execution: {operation}")
        return blocked

    class BlockedPopen(subprocess.Popen):
        # Windows asyncio.windows_utils는 Popen을 상속한다. 타입을 함수로
        # 바꾸지 않고 실제 프로세스 생성만 거부한다.
        def __new__(cls, *args, **kwargs):
            deny("subprocess.Popen")(*args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        # 반출기의 내부 임시 SQLite도 호출자가 지정한 pytest 출력 아래에 둔다.
        local_temp = str(tmp_path_factory.mktemp("gpu18-internal-temp"))
        patch.setattr(tempfile, "tempdir", local_temp)
        for name in ("TMPDIR", "TEMP", "TMP"):
            patch.setenv(name, local_temp)
        patch.setattr(subprocess, "Popen", BlockedPopen)
        for owner, names in (
            (os, ("system", "popen", "startfile")),
            (socket, ("create_connection", "getaddrinfo")),
            (socket.socket, ("connect", "connect_ex", "sendto", "bind", "listen", "accept")),
        ):
            for name in names:
                if hasattr(owner, name):
                    patch.setattr(owner, name, deny(f"{owner.__name__}.{name}"))
        yield attempts
    assert attempts == [], f"금지된 외부 호출 시도: {attempts}"


def _registries():
    from guard.executors import registry as executors
    from guard.judges import registry as judges
    return (executors.EXECUTORS, executors.EXECUTOR_DEFAULT_JUDGES,
            executors.ADAPTERS, judges.JUDGES)


def _snapshot_registries():
    return [(mapping, dict(mapping)) for mapping in _registries()]


def _assert_registries_restored(before):
    for current, (original, values) in zip(_registries(), before, strict=True):
        assert current is original
        assert current.keys() == values.keys()
        assert all(current[key] is value for key, value in values.items())


def _file_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


@pytest.fixture(scope="module")
def demo(tmp_path_factory, no_external_execution):
    import guard
    from guard.store import Store

    # import 시점의 전역 변경도 검출한다. 원본 팩은 읽기 전용으로 대조한다.
    registry_before = _snapshot_registries()
    pack_dir = Path(guard.__file__).resolve().parents[2] / "reference" / "pack-seeds" / "PACK-BASELINE-33"
    original_pack = _file_hashes(pack_dir)
    runner = importlib.import_module("guard.gpu18_prototype.runner")
    output = tmp_path_factory.mktemp("gpu18-review") / "empty-output"
    output.mkdir()
    try:
        summary = runner.run_demo(output)
    finally:
        _assert_registries_restored(registry_before)
        assert _file_hashes(pack_dir) == original_pack
    stores = [Store.read_only(path.parent) for path in sorted(output.rglob("store.sqlite"))]
    assert stores, "요약 외에 실제 저장소가 있어야 한다"
    return SimpleNamespace(output=output, summary=summary, stores=stores, runner=runner)


def _runs(demo):
    return [*demo.summary["runs"], *demo.summary["baseline_runs"]]


def _pinned_object(store, run, name, kind):
    from guard.models import Ref
    if name + "_ref" in run:
        ref = Ref.model_validate(run[name + "_ref"])
        assert ref.kind == kind and ref.id == run[name + "_id"]
        return store.resolve(ref)
    objects = [obj for obj in store.iter_kind(kind) if obj.id == run[name + "_id"]]
    assert len(objects) == 1, "ID만 반환하면 저장소에서 정확한 객체가 유일해야 한다"
    return objects[0]


def _plan_store(demo, run):
    for store in demo.stores:
        for plan in store.iter_kind("PLAN"):
            if plan.id == run["plan_id"]:
                return store, _pinned_object(store, run, "plan", "PLAN")
    raise AssertionError(f"저장된 PLAN이 없음: {run['plan_id']}")


def _evidence_results(store, plan):
    evidence = {e.check_id: e for e in store.iter_kind("EVD") if e.plan == plan.ref()}
    keys = {(e.id, e.revision, e.content_sha256) for e in evidence.values()}
    results = {r.check_id: r for r in store.iter_kind("RES")
               if (r.evidence.id, r.evidence.revision, r.evidence.sha256) in keys}
    return evidence, results


def _object_inventory(store):
    return {(obj.kind.value, obj.id, obj.revision): obj.to_dict()
            for kind in KINDS for obj in store.iter_kind(kind)}


def _report_content(report):
    return {key: value for key, value in report.to_dict().items()
            if key not in {"id", "created_at", "content_sha256"}}


def test_public_summary_has_exact_scope_and_expected_scenarios(demo):
    summary = demo.summary
    assert summary == json.loads((demo.output / "summary.json").read_text(encoding="utf-8"))
    assert len(summary["check_ids"]) == 18
    assert set(summary["check_ids"]) == EXPECTED_IDS
    assert len(summary["scenarios"]) == 3
    assert set(summary["scenarios"]) == set(EXPECTED_VERDICTS)
    assert summary["data_origin"] == "EXAMPLE"
    assert summary["official"] is False
    assert summary["bundle_verified"] is True
    assert summary["restored_rejudge_stable"] is True
    assert len(summary["runs"]) == 3
    assert {run["scenario"] for run in summary["runs"]} == set(EXPECTED_VERDICTS)
    for run in summary["runs"]:
        assert isinstance(run["plan_id"], str) and isinstance(run["report_id"], str)
        assert run["verdicts"] == dict.fromkeys(EXPECTED_IDS, EXPECTED_VERDICTS[run["scenario"]])
    assert set(summary["integration_required"]) == EXPECTED_IDS
    assert all(isinstance(text, str) and text.strip()
               for text in summary["integration_required"].values())


def test_stored_pipeline_refs_and_judgments_match_summary(demo):
    from guard.store.packs import get_pack

    for run in _runs(demo):
        store, plan = _plan_store(demo, run)
        pack = get_pack(store, plan.inputs.pack)
        assert {item.check_id for item in plan.items if item.selected} == EXPECTED_IDS
        evidence, results = _evidence_results(store, plan)
        assert set(evidence) == set(results) == EXPECTED_IDS
        assert {cid: result.verdicts.security.value for cid, result in results.items()} == run["verdicts"]
        policy = store.resolve(plan.inputs.policy)
        for cid, evd in evidence.items():
            values = policy.values[cid]
            for param in pack.checks[cid].policy_params:
                if param.required:
                    assert param.key in values, (cid, param.key)
                if param.key not in values:
                    continue
                value = values[param.key]
                expected_type = {"str": str, "bool": bool, "int": int}.get(param.type)
                if expected_type is not None:
                    assert type(value) is expected_type, (cid, param.key, param.type)
                elif param.type == "list[str]":
                    assert isinstance(value, list) and all(type(v) is str for v in value)
            assert store.resolve(evd.environment).ref() == evd.environment
            assert evd.execution.status.value == "COMPLETED"
            assert evd.execution.executor is not None
            assert results[cid].judge.name == pack.checks[cid].judge.plugin == "rule_table"
            assert results[cid].evidence == evd.ref()
            assert store.resolve(results[cid].evidence).to_dict() == evd.to_dict()
        report = _pinned_object(store, run, "report", "RPT")
        assert plan.ref() in report.inputs_manifest
        assert all(result.ref() in report.inputs_manifest for result in results.values())
        selected = {row.check_id: row for row in report.by_check if row.selected}
        assert set(selected) == EXPECTED_IDS
        assert {cid: row.single.security.value for cid, row in selected.items()} == run["verdicts"]


def test_every_saved_object_remains_example_and_draft_nonofficial(demo):
    for store in demo.stores:
        for kind in KINDS:
            for obj in store.iter_kind(kind):
                assert obj.data_origin.value == "EXAMPLE"
                if kind == "RES":
                    assert obj.verification_level.value == "EXAMPLE"
                elif kind == "POL":
                    assert obj.status.value == "DRAFT"
                    assert obj.confirmed_by is None and obj.confirmed_at is None
                elif kind == "RPT":
                    assert obj.official is False
                    assert obj.policy_status.value == "DRAFT"
                    assert obj.pack_review_status.value == "DRAFT"
                    assert obj.approval is None


def test_required_observations_controls_and_simulation_traces(demo):
    from guard.store.packs import get_pack

    for run in demo.summary["runs"]:
        store, plan = _plan_store(demo, run)
        pack = get_pack(store, plan.inputs.pack)
        evidence, results = _evidence_results(store, plan)
        for cid, evd in evidence.items():
            check = pack.checks[cid]
            assert evd.normal_control.attempted is True
            assert {c.step for c in evd.challenges if c.attempted} >= set(check.challenge_steps)
            missing = {spec.key for spec in check.required_observations
                       if spec.key not in evd.observations or not evd.observations[spec.key].collected}
            if run["scenario"] == "missing":
                assert missing, cid
                assert results[cid].missing_observations
            else:
                assert not missing, (cid, missing)
                assert evd.normal_control.succeeded is True
            traces = [json.loads((store.root / a.path).read_text(encoding="utf-8"))
                      for a in evd.artifacts]
            assert traces, cid
            for trace in traces:
                assert trace["simulation"] is True
                assert trace["data_origin"] == "EXAMPLE" and trace["official"] is False
                assert trace["check_id"] == cid and trace["scenario"] == run["scenario"]
                assert trace["events"]
                assert all(event.get("simulation") is True for event in trace["events"])
                raw_observations = trace["observations"]
                assert raw_observations.keys() <= evd.observations.keys()
                for key, obs in evd.observations.items():
                    # 코어는 반환에서 빠진 필수 관측을 수집 실패/null로 정규화한다.
                    assert raw_observations.get(key, {"collected": False, "value": None}) == (
                        obs.model_dump(mode="json"))


def test_original_pack_is_separately_judged_from_same_observations(demo):
    from guard.store.packs import get_pack

    baseline = demo.summary["baseline_runs"]
    assert len(baseline) == 3
    assert {r["scenario"] for r in baseline} == set(EXPECTED_VERDICTS)
    assert set(demo.summary["derivation"]["judge_differences"]) == {"SUP-03", "PLT-02", "REQ-10"}
    originals = {r["scenario"]: r for r in baseline}
    for run in demo.summary["runs"]:
        store, plan = _plan_store(demo, run)
        source_store, source_plan = _plan_store(demo, originals[run["scenario"]])
        source_pack = get_pack(source_store, source_plan.inputs.pack)
        prototype_pack = get_pack(store, plan.inputs.pack)
        assert source_pack.manifest.pack_id == "PACK-BASELINE"
        assert demo.summary["derivation"]["source_pack"] == source_pack.ref().model_dump(mode="json")
        assert prototype_pack.manifest.pack_id != source_pack.manifest.pack_id
        simulated, _ = _evidence_results(store, plan)
        source_evidence, _ = _evidence_results(source_store, source_plan)
        assert set(source_evidence) == EXPECTED_IDS
        for cid in EXPECTED_IDS:
            assert simulated[cid].observations == source_evidence[cid].observations
            assert simulated[cid].normal_control == source_evidence[cid].normal_control
            # 전용 판정표 외 관측·정책·질문 계약은 원본을 보존한다.
            before = source_pack.checks[cid].model_dump(mode="json", exclude={"judge"})
            after = prototype_pack.checks[cid].model_dump(mode="json", exclude={"judge"})
            assert before == after


def test_independent_failure_is_not_erased_by_failed_pass_gate(demo):
    from guard.core.judge import judge_evidence
    from guard.models.objects import NormalControl
    from guard.store.packs import get_pack

    run = next(r for r in demo.summary["runs"] if r["scenario"] == "violation")
    store, plan = _plan_store(demo, run)
    pack = get_pack(store, plan.inputs.pack)
    policy = store.resolve(plan.inputs.policy)
    evidence, _ = _evidence_results(store, plan)
    for cid, evd in evidence.items():
        incomplete_control = evd.model_copy(update={
            "normal_control": NormalControl(attempted=False, succeeded=None)}).seal()
        result = judge_evidence(pack.checks[cid], incomplete_control, policy,
                                datetime.fromisoformat(demo.summary["as_of"]), plan=plan)
        assert result.verdicts.security.value == "FAIL", cid
        assert result.data_origin.value == "EXAMPLE"


def test_independent_restore_rejudges_saved_pack_and_preserves_exact_results(demo, tmp_path):
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.store import Store
    from guard.store.bundle import restore_bundle, verify_complete
    from guard.store.packs import get_pack

    bundle, = demo.output.glob("*.zip")
    destination = tmp_path / "independent-restored"
    restore_bundle(bundle, destination)
    restored = Store(destination)
    verify_complete(restored)
    original_store, _ = _plan_store(demo, demo.summary["runs"][0])
    assert _object_inventory(restored) == _object_inventory(original_store)
    before = _object_inventory(restored)
    as_of = datetime.fromisoformat(demo.summary["as_of"])
    registry_before = _snapshot_registries()
    for run in _runs(demo):
        plan = _pinned_object(restored, run, "plan", "PLAN")
        pack = get_pack(restored, plan.inputs.pack)
        assert pack.source == "store"
        _, old_results = _evidence_results(restored, plan)
        results = judge_plan(restored, pack, plan, as_of)
        assert {r.check_id: r.ref() for r in results} == {
            cid: r.ref() for cid, r in old_results.items()}
        assert {r.check_id: r.verdicts.security.value for r in results} == run["verdicts"]
        assert _report_content(build_report(restored, pack, plan, as_of)) == _report_content(
            _pinned_object(restored, run, "report", "RPT"))
    _assert_registries_restored(registry_before)
    assert _object_inventory(restored) == before
    verify_complete(restored)


@pytest.mark.parametrize("damage", ["checksum", "matching_checksum_invalid_db", "missing_saved_pack"])
def test_prototype_bundle_rejects_tampering_before_publication(demo, tmp_path, damage):
    from guard.store import IntegrityError
    from guard.store.bundle import restore_bundle

    bundle, = demo.output.glob("*.zip")
    with zipfile.ZipFile(bundle) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    if damage == "checksum":
        name = next(name for name in entries if name.startswith("artifacts/"))
        raw = entries[name]
        assert raw
        entries[name] = bytes([raw[0] ^ 1]) + raw[1:]
    elif damage == "matching_checksum_invalid_db":
        entries["store.sqlite"] = b"invalid prototype database"
        manifest["files"]["store.sqlite"] = {
            "bytes": len(entries["store.sqlite"]),
            "sha256": hashlib.sha256(entries["store.sqlite"]).hexdigest()}
    else:
        _, plan = _plan_store(demo, demo.summary["runs"][0])
        name = "artifacts/" + plan.inputs.pack.sha256
        del entries[name]
        del manifest["files"][name]
    entries["manifest.json"] = json.dumps(manifest).encode("utf-8")
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(changed, "w") as archive:
        for name, raw in entries.items():
            archive.writestr(name, raw)
    destination = tmp_path / "must-not-publish"
    with pytest.raises((IntegrityError, ValueError, LookupError, sqlite3.DatabaseError)):
        restore_bundle(changed, destination)
    assert not destination.exists()


@pytest.mark.parametrize("kind", ["nonempty_directory", "existing_file"])
def test_existing_nonempty_output_is_refused_without_changes(demo, tmp_path, kind):
    target = tmp_path / "keep"
    if kind == "nonempty_directory":
        target.mkdir()
        marker = target / "original.bin"
    else:
        marker = target
    marker.write_bytes(b"preserve existing user data")
    before = _snapshot_registries()
    with pytest.raises((ValueError, FileExistsError, NotADirectoryError)):
        demo.runner.run_demo(target)
    _assert_registries_restored(before)
    assert marker.read_bytes() == b"preserve existing user data"
    if target.is_dir():
        assert list(target.iterdir()) == [marker]


def test_registry_is_restored_after_evidence_storage_failure(demo, tmp_path, monkeypatch):
    from guard.store import Store

    original_put = Store.put
    failed = []
    before = _snapshot_registries()

    def fail_evidence(store, obj):
        if obj.kind.value == "EVD":
            failed.append(obj.check_id)
            raise OSError("injected prototype evidence storage failure")
        return original_put(store, obj)

    monkeypatch.setattr(Store, "put", fail_evidence)
    try:
        with pytest.raises(OSError, match="injected prototype evidence storage failure"):
            demo.runner.run_demo(tmp_path / "failed-run")
    finally:
        _assert_registries_restored(before)
    assert failed, "실제 EVD 저장 경계에 도달해야 한다"


def test_module_cli_uses_the_same_offline_public_contract(demo, tmp_path, monkeypatch, capsys):
    output = tmp_path / "new-cli-output"
    before = _snapshot_registries()
    monkeypatch.setattr(sys, "argv", ["guard.gpu18_prototype", "--output", str(output)])
    monkeypatch.chdir(tmp_path)
    try:
        runpy.run_module("guard.gpu18_prototype", run_name="__main__")
    except SystemExit as exc:
        assert exc.code in (None, 0)
    finally:
        _assert_registries_restored(before)
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert printed == saved
    assert set(saved["check_ids"]) == EXPECTED_IDS
    assert saved["data_origin"] == "EXAMPLE" and saved["official"] is False
    for run in saved["runs"]:
        assert run["verdicts"] == dict.fromkeys(EXPECTED_IDS, EXPECTED_VERDICTS[run["scenario"]])
