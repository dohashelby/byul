"""Step 4: 팩 보존본(내용 주소 저장). select 시점의 팩을 저장소에 남기고 judge·report가 PLAN의 팩 revision을 읽는다."""

import json
import shutil

import pytest
import yaml
from typer.testing import CliRunner

from guard.cli import app
from guard.models import PackIntegrityError, load_pack
from guard.store import Store
from guard.store.packs import PackNotStoredError, get_pack, put_pack
from tests.builders import PACK_DIR, ROOT

runner = CliRunner()


def test_round_trip_and_idempotent(tmp_path):
    store = Store(tmp_path / ".guard")
    pack = load_pack(PACK_DIR)
    ref = put_pack(store, PACK_DIR)
    assert ref == pack.ref()
    assert put_pack(store, PACK_DIR) == ref  # 같은 내용 재저장 무해
    back = get_pack(store, ref)
    assert back.ref() == ref and back.source == "store"
    assert back.checks.keys() == pack.checks.keys()
    assert back.checks["REQ-01"] == pack.checks["REQ-01"]
    assert (store.root / "artifacts" / ref.sha256).exists()  # 매니페스트 = 팩 Ref의 sha256
    assert store.verify() == []


def test_tampered_snapshot_is_rejected(tmp_path):
    store = Store(tmp_path / ".guard")
    ref = put_pack(store, PACK_DIR)
    manifest = json.loads((store.root / "artifacts" / ref.sha256).read_bytes())
    f = next(x for x in manifest["files_sha256"] if x["path"] == "checks/REQ-01.yaml")
    (store.root / "artifacts" / f["sha256"]).write_bytes(b"tampered")
    with pytest.raises(PackIntegrityError):
        get_pack(store, ref)


def test_missing_or_mismatched_ref(tmp_path):
    store = Store(tmp_path / ".guard")
    ref = load_pack(PACK_DIR).ref()
    with pytest.raises(PackNotStoredError):
        get_pack(store, ref)
    put_pack(store, PACK_DIR)
    with pytest.raises(PackNotStoredError):
        get_pack(store, ref.model_copy(update={"revision": ref.revision + 1}))  # 같은 sha, 다른 revision → 거부


def _bumped_pack(src_dir, dst_dir):
    """팩 사본에서 점검 하나를 바꿔 revision을 올린 새 팩(변환기 없이 매니페스트 재계산)."""
    import hashlib

    shutil.copytree(src_dir, dst_dir)
    p = dst_dir / "checks/SUP-01.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data["remediation"] = (data.get("remediation") or "") + " (수정)"
    data["revision"] += 1
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")
    m = yaml.safe_load((dst_dir / "pack.yaml").read_text(encoding="utf-8"))
    m["revision"] += 1
    m["files_sha256"] = [{"path": f["path"], "sha256": hashlib.sha256((dst_dir / f["path"]).read_bytes()).hexdigest()}
                         for f in m["files_sha256"]]
    (dst_dir / "pack.yaml").write_text(yaml.safe_dump(m, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")
    load_pack(dst_dir)  # 유효한 팩


def _ok(args):
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output + repr(res.exception)
    return res.output


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    for name in ("guard.toml", "examples"):
        src = ROOT / name
        (shutil.copytree if src.is_dir() else shutil.copy)(src, tmp_path / name)
    shutil.copytree(ROOT / "packs", tmp_path / "packs")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_old_plan_rejudged_from_snapshot_after_pack_changes(workdir):
    out = _ok(["select", "--pack", "packs/PACK-BASELINE", "--env", "examples/env.yaml", "--policy", "examples/policy.yaml"])
    plan = out.strip().splitlines()[-1]
    _ok(["run", "--plan", plan])
    _ok(["judge", "--plan", plan])
    _ok(["report", "--plan", plan, "--json", "before.json"])
    # 디스크의 팩을 새 revision으로 교체
    shutil.move("packs/PACK-BASELINE", "pack-old")
    _bumped_pack(workdir / "pack-old", workdir / "packs/PACK-BASELINE")
    # run은 계속 거부(과거 정의로 새 시험을 돌리지 않음)
    res = runner.invoke(app, ["run", "--plan", plan])
    assert res.exit_code != 0 and "differs" in (res.output + repr(res.exception))
    # judge·report는 보존본으로
    _ok(["judge", "--plan", plan])
    _ok(["report", "--plan", plan, "--json", "after.json"])
    before, after = (json.loads((workdir / f).read_text(encoding="utf-8")) for f in ("before.json", "after.json"))
    assert [(r["check_id"], (r.get("single") or {}).get("security")) for r in before["by_check"]] == \
           [(r["check_id"], (r.get("single") or {}).get("security")) for r in after["by_check"]]
    assert any("팩 보존본 사용" in l and "revision" in l for l in after["limitations"])
    assert not any("팩 보존본 사용" in l for l in before["limitations"])
    assert "OK" in _ok(["verify"])


def test_without_snapshot_still_refused(workdir):
    out = _ok(["select", "--pack", "packs/PACK-BASELINE", "--env", "examples/env.yaml", "--policy", "examples/policy.yaml"])
    plan = out.strip().splitlines()[-1]
    # 보존본을 지운 저장소를 흉내: 새 저장소에 PLAN만 옮길 수 없으므로 매니페스트 artifact를 제거
    store = Store(workdir / ".guard")
    ref = load_pack(workdir / "packs/PACK-BASELINE").ref()
    (store.root / "artifacts" / ref.sha256).unlink()
    shutil.move("packs/PACK-BASELINE", "pack-old")
    _bumped_pack(workdir / "pack-old", workdir / "packs/PACK-BASELINE")
    res = runner.invoke(app, ["judge", "--plan", plan])
    assert res.exit_code != 0  # 보호 장치 유지


def test_pack_import_command(workdir):
    _ok(["pack", "import", "--pack", "packs/PACK-BASELINE", "--store", "imported"])
    ref = load_pack(workdir / "packs/PACK-BASELINE").ref()
    assert get_pack(Store(workdir / "imported"), ref).ref() == ref
    # 손상된 팩 폴더는 거부
    p = workdir / "packs/PACK-BASELINE/checks/REQ-01.yaml"
    p.write_text(p.read_text(encoding="utf-8") + "\n# 변조\n", encoding="utf-8")
    res = runner.invoke(app, ["pack", "import", "--pack", "packs/PACK-BASELINE", "--store", "imported"])
    assert res.exit_code != 0
