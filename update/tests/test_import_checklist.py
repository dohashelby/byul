"""1단계: checklist.json 77행 → packs/PACK-BASELINE/checks/*.yaml"""

import copy
import json
from pathlib import Path

import yaml
import pytest

from tools.import_checklist import AREAS, import_checklist
from tests.prepared_fixtures import copy_frozen_tree

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reference/framework/data/checklist.json"
OVERRIDES = ROOT / "tools/overrides"


@pytest.fixture(scope="session")
def pristine_imported_pack(tmp_path_factory):
    """Share only the unchanged first import, never a test's mutable output.

    Tests of initial creation still import directly. Rerun and changed-input
    tests copy this initial state and invoke the real importer afterwards.
    """
    pack = tmp_path_factory.mktemp("importer-pristine") / "pack"
    import_checklist(SOURCE, pack, OVERRIDES)
    return pack


@pytest.fixture
def imported_pack(tmp_path, pristine_imported_pack):
    pack = tmp_path / "pack"
    copy_frozen_tree(pristine_imported_pack, pack)
    return pack


def _snapshot(pack: Path) -> dict[str, bytes]:
    return {str(p.relative_to(pack)): p.read_bytes() for p in sorted(pack.rglob("*")) if p.is_file()}


def _write_source(tmp: Path, data: dict) -> Path:
    path = tmp / "checklist.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_creates_77_files_with_area_ids(tmp_path):
    pack = tmp_path / "pack"
    import_checklist(SOURCE, pack, OVERRIDES)
    files = sorted(p.stem for p in (pack / "checks").glob("*.yaml"))
    assert len(files) == 77
    expected = {"SUP": 16, "REQ": 21, "PLT": 12, "LIF": 16, "OBS": 12}
    for code, n in expected.items():
        assert [f for f in files if f.startswith(code + "-")] == [f"{code}-{i:02d}" for i in range(1, n + 1)]
    assert set(AREAS.values()) == set(expected)


def test_req01_is_first_request_row(imported_pack):
    pack = imported_pack
    data = yaml.safe_load((pack / "checks/REQ-01.yaml").read_text(encoding="utf-8"))
    src = json.loads(SOURCE.read_text(encoding="utf-8"))["request_state_assessment_draft"][0]
    assert data["question"] == src["question"]
    assert data["purpose"] == src["parent_purpose"]
    assert data["applies_when"]["prose"] == src["applies_when"]
    assert data["required_observations_prose"] == src["required_observations"]
    assert data["legacy"]["q_ids"] == src["legacy_q_ids"]
    # 자동화 대상만 구조화 (overrides)
    assert data["applies_when"]["all"] == [{"fact": "gateway", "eq": True}]
    assert data["executor"] == "direct_route"
    assert data["judge"]["plugin"] == "rule_table"


def test_unstructured_rows_keep_prose_and_null_executor(imported_pack):
    pack = imported_pack
    # 과거 원문 사례를 유지한다. 현행 원문의 전파는 U1 별도 계약 테스트에서 확인한다.
    legacy_source = json.loads(SOURCE.read_text(encoding="utf-8"))
    legacy_source["supply_assessment_draft"][0]["applies_when"] = "승인 저장소 사용"
    legacy_path = _write_source(pack.parent, legacy_source)
    import_checklist(legacy_path, pack, OVERRIDES)
    data = yaml.safe_load((pack / "checks/SUP-01.yaml").read_text(encoding="utf-8"))
    assert data["executor"] is None
    assert data["judge"] is None
    assert data["applies_when"] == {"prose": "승인 저장소 사용"}
    assert data["required_observations"] == []
    assert data["required_observations_prose"]


def test_rerun_is_byte_identical(imported_pack):
    pack = imported_pack
    first = _snapshot(pack)
    import_checklist(SOURCE, pack, OVERRIDES)
    assert _snapshot(pack) == first


def test_ids_survive_wording_change_and_reorder(tmp_path, imported_pack):
    pack = imported_pack
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    changed = copy.deepcopy(data)
    rows = changed["request_state_assessment_draft"]
    # REQ-01 문구 변경 + 행 순서 뒤집기
    original_req01_subtests = rows[0]["subtest_ids"]
    rows[0]["question"] = rows[0]["question"] + " (문구 수정)"
    rows.reverse()
    import_checklist(_write_source(tmp_path, changed), pack, OVERRIDES)
    req01 = yaml.safe_load((pack / "checks/REQ-01.yaml").read_text(encoding="utf-8"))
    assert req01["question"].endswith("(문구 수정)")
    assert req01["legacy"]["subtest_ids"] == original_req01_subtests
    req21 = yaml.safe_load((pack / "checks/REQ-21.yaml").read_text(encoding="utf-8"))
    assert req21["question"] == data["request_state_assessment_draft"][20]["question"]


def test_removed_row_is_retired_and_new_row_gets_next_number(tmp_path, imported_pack):
    pack = imported_pack
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    changed = copy.deepcopy(data)
    removed = changed["platform_assessment_draft"].pop(2)
    new_row = copy.deepcopy(changed["platform_assessment_draft"][0])
    new_row["id"] = "CHK-PLT-013"
    new_row["question"] = "새로 추가된 점검인가?"
    new_row["subtest_ids"] = ["Q-NEW/SC-NEW"]
    changed["platform_assessment_draft"].append(new_row)
    import_checklist(_write_source(tmp_path, changed), pack, OVERRIDES)
    plt03 = yaml.safe_load((pack / "checks/PLT-03.yaml").read_text(encoding="utf-8"))
    assert plt03["retired"] is True
    assert plt03["question"] == removed["question"]
    plt13 = yaml.safe_load((pack / "checks/PLT-13.yaml").read_text(encoding="utf-8"))
    assert plt13["question"] == "새로 추가된 점검인가?"
    assert plt13["retired"] is False


def test_pack_manifest_hashes_all_files(imported_pack):
    import hashlib

    pack = imported_pack
    manifest = yaml.safe_load((pack / "pack.yaml").read_text(encoding="utf-8"))
    assert manifest["pack_id"] == "PACK-BASELINE"
    assert manifest["review"]["status"] == "DRAFT"
    assert len(manifest["checks"]) == 77
    paths = [f["path"] for f in manifest["files_sha256"]]
    assert paths == sorted(paths)
    assert "pack.yaml" not in paths
    for f in manifest["files_sha256"]:
        assert hashlib.sha256((pack / f["path"]).read_bytes()).hexdigest() == f["sha256"]


def test_pack_revision_bumps_only_on_change(tmp_path, imported_pack):
    pack = imported_pack
    import_checklist(SOURCE, pack, OVERRIDES)
    assert yaml.safe_load((pack / "pack.yaml").read_text(encoding="utf-8"))["revision"] == 1
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    data["observability_assessment_draft"][0]["remediation"] += " 수정"
    import_checklist(_write_source(tmp_path, data), pack, OVERRIDES)
    assert yaml.safe_load((pack / "pack.yaml").read_text(encoding="utf-8"))["revision"] == 2


def test_committed_pack_matches_importer(tmp_path):
    """커밋된 팩에 변환기를 다시 돌려도 바뀌는 것이 없다(손으로 고친 흔적·미반영 override 없음)."""
    import shutil

    committed = ROOT / "packs/PACK-BASELINE"
    pack = tmp_path / "pack"
    shutil.copytree(committed, pack)
    import_checklist(SOURCE, pack, OVERRIDES)
    assert _snapshot(pack) == _snapshot(committed)

def test_fixed_ids_survive_all_wording_changes_and_duplicate_subtests(tmp_path, imported_pack):
    data = json.loads(SOURCE.read_text())
    before = {p.stem: yaml.safe_load(p.read_text())["framework_id"]
              for p in (imported_pack / "checks").glob("*.yaml")}
    for key in AREAS:
        for row in data[key]:
            row["question"] += " revised"
            row["subtest_ids"] = ["shared"]
        data[key].reverse()
    import_checklist(_write_source(tmp_path, data), imported_pack, OVERRIDES)
    after = {p.stem: yaml.safe_load(p.read_text())["framework_id"]
             for p in (imported_pack / "checks").glob("*.yaml")}
    assert before == after


def test_duplicate_fixed_id_rejected_without_pack_changes(tmp_path, imported_pack):
    data = json.loads(SOURCE.read_text())
    rows = data["observability_assessment_draft"]
    rows[1]["id"] = rows[0]["id"]
    before = _snapshot(imported_pack)
    with pytest.raises(ValueError, match="Duplicate framework ID"):
        import_checklist(_write_source(tmp_path, data), imported_pack, OVERRIDES)
    assert _snapshot(imported_pack) == before


def test_legacy_map_migration_preserves_all_internal_ids(tmp_path, imported_pack):
    import zipfile
    history = ROOT / "tests/fixtures/pack21-checks/source.zip"
    with zipfile.ZipFile(history) as archive:
        name = next(name for name in archive.namelist() if name.endswith("id_map.yaml"))
        (imported_pack / "id_map.yaml").write_bytes(archive.read(name))
    before = {p.stem: yaml.safe_load(p.read_text())["framework_id"]
              for p in (imported_pack / "checks").glob("*.yaml")}
    import_checklist(SOURCE, imported_pack, OVERRIDES)
    after = {p.stem: yaml.safe_load(p.read_text())["framework_id"]
             for p in (imported_pack / "checks").glob("*.yaml")}
    assert before == after
    assert all(not yaml.safe_load(p.read_text())["retired"]
               for p in (imported_pack / "checks").glob("*.yaml"))
