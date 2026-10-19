"""1단계: checklist.json 77행 → packs/PACK-BASELINE/checks/*.yaml"""

import copy
import json
from pathlib import Path

import yaml

from tools.import_checklist import AREAS, import_checklist

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reference/framework/data/checklist.json"
OVERRIDES = ROOT / "tools/overrides"


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


def test_req01_is_first_request_row(tmp_path):
    pack = tmp_path / "pack"
    import_checklist(SOURCE, pack, OVERRIDES)
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


def test_unstructured_rows_keep_prose_and_null_executor(tmp_path):
    pack = tmp_path / "pack"
    import_checklist(SOURCE, pack, OVERRIDES)
    data = yaml.safe_load((pack / "checks/SUP-01.yaml").read_text(encoding="utf-8"))
    assert data["executor"] is None
    assert data["judge"] is None
    assert data["applies_when"] == {"prose": "승인 저장소 사용"}
    assert data["required_observations"] == []
    assert data["required_observations_prose"]


def test_rerun_is_byte_identical(tmp_path):
    pack = tmp_path / "pack"
    import_checklist(SOURCE, pack, OVERRIDES)
    first = _snapshot(pack)
    import_checklist(SOURCE, pack, OVERRIDES)
    assert _snapshot(pack) == first


def test_ids_survive_wording_change_and_reorder(tmp_path):
    pack = tmp_path / "pack"
    import_checklist(SOURCE, pack, OVERRIDES)
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


def test_removed_row_is_retired_and_new_row_gets_next_number(tmp_path):
    pack = tmp_path / "pack"
    import_checklist(SOURCE, pack, OVERRIDES)
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    changed = copy.deepcopy(data)
    removed = changed["platform_assessment_draft"].pop(2)
    new_row = copy.deepcopy(changed["platform_assessment_draft"][0])
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


def test_pack_manifest_hashes_all_files(tmp_path):
    import hashlib

    pack = tmp_path / "pack"
    import_checklist(SOURCE, pack, OVERRIDES)
    manifest = yaml.safe_load((pack / "pack.yaml").read_text(encoding="utf-8"))
    assert manifest["pack_id"] == "PACK-BASELINE"
    assert manifest["review"]["status"] == "DRAFT"
    assert len(manifest["checks"]) == 77
    paths = [f["path"] for f in manifest["files_sha256"]]
    assert paths == sorted(paths)
    assert "pack.yaml" not in paths
    for f in manifest["files_sha256"]:
        assert hashlib.sha256((pack / f["path"]).read_bytes()).hexdigest() == f["sha256"]


def test_pack_revision_bumps_only_on_change(tmp_path):
    pack = tmp_path / "pack"
    import_checklist(SOURCE, pack, OVERRIDES)
    import_checklist(SOURCE, pack, OVERRIDES)
    assert yaml.safe_load((pack / "pack.yaml").read_text(encoding="utf-8"))["revision"] == 1
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    data["observability_assessment_draft"][0]["remediation"] += " 수정"
    import_checklist(_write_source(tmp_path, data), pack, OVERRIDES)
    assert yaml.safe_load((pack / "pack.yaml").read_text(encoding="utf-8"))["revision"] == 2


def test_committed_pack_matches_importer(tmp_path):
    """저장소에 커밋된 팩이 변환기 출력과 같다(손으로 고친 흔적 없음)."""
    pack = tmp_path / "pack"
    import_checklist(SOURCE, pack, OVERRIDES)
    committed = ROOT / "packs/PACK-BASELINE"
    assert _snapshot(pack) == _snapshot(committed)
