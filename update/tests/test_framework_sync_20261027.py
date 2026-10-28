"""Pinned upstream bytes, immutable pack stages and unchanged execution contracts."""
from pathlib import Path
import hashlib
import json
import zipfile

import yaml

from guard.models.checkdef import load_pack

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "reference/framework"
CANONICAL_SHA = "745e18640bc0c405baee1883dee53d1f35b644f0bc6116705a880b91893c590f"
REQUIRED = {
    "CHK-REQ-005": ["모델별 사용 권한", "사용 종료 모델의 실행 중단"],
    "CHK-PLT-008": ["API 전송 보호", "내부 IPC·TP 전송 보호"],
    "CHK-LIF-013": ["전체 release 권한", "재취득·대체 공급", "기존 참조의 수명"],
    "CHK-OBS-006": ["조회·반출 권한", "저장 암호화·복호화 권한"],
}


def test_pinned_vendor_bytes_and_required_results():
    manifest = json.loads((VENDOR / "UPSTREAM.json").read_text())
    assert manifest["commit"] == "91e84c778f4fc8ee1108eaf0551bf51f2f6a2061"
    assert len(manifest["files"]) == 22
    for entry in manifest["files"]:
        assert hashlib.sha256((VENDOR / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    raw = (VENDOR / "data/checklist.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CANONICAL_SHA
    source = json.loads(raw)
    rows = [row for name in ("supply", "request_state", "platform", "lifecycle", "observability")
            for row in source[name + "_assessment_draft"]]
    assert len(rows) == 77 and len({row["id"] for row in rows}) == 77
    assert {row["id"]: [s["name"] for s in row["required_subresults"]]
            for row in rows if row.get("required_subresults")} == REQUIRED
    assert all(s["applies_when"] and s["satisfied_if"]
               for row in rows for s in row.get("required_subresults", []))
    template = json.loads((VENDOR / "data/result_template.json").read_text())
    assert len(template) == 45 and template["checklist_revision"] == "sha256:" + CANONICAL_SHA


def _unpack(revision, target):
    folder = ROOT / f"tests/fixtures/pack{revision}-checks"
    info = json.loads((folder / "SNAPSHOT.json").read_text())
    raw = (folder / "source.zip").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == info["source_zip_sha256"]
    with zipfile.ZipFile(folder / "source.zip") as archive:
        assert set(archive.namelist()) == set(info["files"])
        for name in archive.namelist():
            assert hashlib.sha256(archive.read(name)).hexdigest() == info["files"][name]
        archive.extractall(target)
    return load_pack(target)


def test_historical27_to28_revision_boundaries_are_preserved(tmp_path):
    old = _unpack(27, tmp_path / "old27")
    current = _unpack(28, tmp_path / "old28")
    # load_pack verifies the committed manifest and every declared file hash.
    assert len(current.manifest.files_sha256) == 79
    assert old.manifest.revision == 27 and current.manifest.revision == 28
    assert set(old.checks) == set(current.checks) and len(current.checks) == 77
    changed = 0
    for cid in old.checks:
        before = old.checks[cid].model_dump(mode="json")
        after = current.checks[cid].model_dump(mode="json")
        assert before["framework_id"] == after["framework_id"]
        for key in ("executor", "judge", "policy_params", "required_observations", "functional_probes",
                    "route_scope", "functional_scope", "collection_profile"):
            assert before.get(key) == after.get(key), (cid, key)
        assert {k: v for k, v in before["applies_when"].items() if k != "prose"} == {
            k: v for k, v in after["applies_when"].items() if k != "prose"}
        b_revision = before.pop("revision")
        a_revision = after.pop("revision")
        if before == after:
            assert a_revision == b_revision
        else:
            changed += 1
            assert a_revision == b_revision + 1
    assert changed == 38
    assert all(not c.retired for c in current.checks.values()) and not current.reviewed


def test_historical28_to29_revision_and_supported_profiles(tmp_path):
    old = _unpack(28, tmp_path / "old28")
    from tests.pack_lineage_support import historical_pack
    current = historical_pack("main29", tmp_path / "main29")
    assert old.manifest.revision == 28 and current.manifest.revision == 29
    assert len(current.manifest.files_sha256) == 79
    assert set(old.checks) == set(current.checks) and len(current.checks) == 77
    changed = 0
    for cid in old.checks:
        before = old.checks[cid].model_dump(mode="json")
        after = current.checks[cid].model_dump(mode="json")
        assert before["framework_id"] == after["framework_id"]
        for key in ("executor", "judge", "policy_params", "required_observations", "functional_probes",
                    "route_scope", "functional_scope", "collection_profile", "limitations", "legacy"):
            if cid == "REQ-10" and key == "limitations":
                assert after[key] == before[key] + ["이 override의 실행 단계·정상 대조는 현재 sampling-api-v1 지원 프로파일에 한정한다. 새 schema/tool 선택·최종 소비 관측은 미구현·UNKNOWN이며 별도 연결이 필요하다."]
            else:
                assert before.get(key) == after.get(key), (cid, key)
        assert {k:v for k,v in before["applies_when"].items() if k != "prose"} == {
            k:v for k,v in after["applies_when"].items() if k != "prose"}
        old_revision = before.pop("revision")
        current_revision = after.pop("revision")
        if before == after:
            assert current_revision == old_revision
        else:
            changed += 1
            assert current_revision == old_revision + 1
    assert changed == 47 and not current.reviewed
    for cid in ("REQ-09", "REQ-10"):
        assert current.checks[cid].challenge_steps == old.checks[cid].challenge_steps
    assert len(current.checks["REQ-09"].challenge_steps) == 2
    assert current.checks["REQ-10"].normal_control == old.checks["REQ-10"].normal_control
    source = json.loads((VENDOR / "data/checklist.json").read_text())
    rows = [row for name in ("supply", "request_state", "platform", "lifecycle", "observability")
            for row in source[name + "_assessment_draft"]]
    assert sum(len(row["test_branch_conditions"]) for row in rows) == 116
    assert source["assessment_registry"]["revision"] == "2026-10-27-input-hardening"
