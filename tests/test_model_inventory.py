"""tools/model_inventory.py: 정렬·심볼릭 링크·해시 규칙."""

import hashlib

from guard.models.hashing import canonical_json
from tools.model_inventory import inventory


def test_inventory_follows_symlinks_and_is_order_independent(tmp_path):
    blobs = tmp_path / "blobs"
    snap = tmp_path / "snap"
    blobs.mkdir(); snap.mkdir()
    (blobs / "b1").write_bytes(b"weights")
    (snap / "z.json").write_text("{}", encoding="utf-8")
    (snap / "model.bin").symlink_to("../blobs/b1")
    (snap / "sub").mkdir()
    (snap / "sub/a.txt").write_text("a", encoding="utf-8")
    inv = inventory(snap)
    assert [f["path"] for f in inv["files"]] == ["model.bin", "sub/a.txt", "z.json"]
    assert inv["files"][0] == {"path": "model.bin", "bytes": 7, "sha256": hashlib.sha256(b"weights").hexdigest()}
    assert inv["inventory_sha256"] == hashlib.sha256(canonical_json(inv["files"])).hexdigest()
    assert inv["file_count"] == 3 and inv["total_bytes"] == 7 + 2 + 1


def test_content_change_changes_hash(tmp_path):
    (tmp_path / "a").write_bytes(b"1")
    h1 = inventory(tmp_path)["inventory_sha256"]
    (tmp_path / "a").write_bytes(b"2")
    assert inventory(tmp_path)["inventory_sha256"] != h1
