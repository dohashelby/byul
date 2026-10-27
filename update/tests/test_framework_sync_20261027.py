"""Pinned upstream bytes, immutable pack stages and unchanged execution contracts."""
from pathlib import Path
import hashlib
import json
import zipfile

import yaml

from guard.models.checkdef import load_pack

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "reference/framework"
CANONICAL_SHA = "cf4903efe9a60543ceff5276a09ac2c40bb16d54658b21e5deafb03c948f8da7"
REQUIRED = {
    "CHK-REQ-005": ["모델별 사용 권한", "사용 종료 모델의 실행 중단"],
    "CHK-PLT-008": ["API 전송 보호", "내부 IPC·TP 전송 보호"],
    "CHK-LIF-013": ["전체 release 권한", "재취득·대체 공급", "기존 참조의 수명"],
    "CHK-OBS-006": ["조회·반출 권한", "저장 암호화·복호화 권한"],
}


def test_pinned_vendor_bytes_and_required_results():
    manifest = json.loads((VENDOR / "UPSTREAM.json").read_text())
    assert manifest["commit"] == "8a0c7fe42446ff0d3a1f58e2a18cf3069d5c714e"
    assert len(manifest["files"]) == 20
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


def test_old27_and_current_pack_revision_boundaries(tmp_path):
    old = _unpack(27, tmp_path / "old27")
    current = load_pack(ROOT / "packs/PACK-BASELINE")
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
