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
    inv = inventory(snap, scope="all")
    assert [f["path"] for f in inv["files"]] == ["model.bin", "sub/a.txt", "z.json"]
    assert inv["files"][0] == {"path": "model.bin", "bytes": 7, "sha256": hashlib.sha256(b"weights").hexdigest()}
    assert inv["inventory_sha256"] == hashlib.sha256(canonical_json(inv["files"])).hexdigest()
    assert inv["file_count"] == 3 and inv["total_bytes"] == 7 + 2 + 1


def test_content_change_changes_hash(tmp_path):
    (tmp_path / "a").write_bytes(b"1")
    h1 = inventory(tmp_path, scope="all")["inventory_sha256"]
    (tmp_path / "a").write_bytes(b"2")
    assert inventory(tmp_path, scope="all")["inventory_sha256"] != h1


OPT125M_SNAPSHOT = [".gitattributes", "LICENSE.md", "README.md", "config.json", "flax_model.msgpack", "generation_config.json",
                    "merges.txt", "pytorch_model.bin", "special_tokens_map.json", "tf_model.h5", "tokenizer_config.json", "vocab.json"]


def test_vllm_scope_selects_only_files_vllm_reads():
    from tools.model_inventory import vllm_read_files

    assert vllm_read_files(OPT125M_SNAPSHOT) == [
        "config.json", "generation_config.json", "merges.txt", "pytorch_model.bin",
        "special_tokens_map.json", "tokenizer_config.json", "vocab.json"]


def test_safetensors_preferred_over_bin():
    from tools.model_inventory import vllm_read_files

    paths = ["config.json", "model-00001.safetensors", "model-00002.safetensors", "model.safetensors.index.json",
             "pytorch_model.bin", "pytorch_model.bin.index.json", "tokenizer.json"]
    assert vllm_read_files(paths) == ["config.json", "model-00001.safetensors", "model-00002.safetensors",
                                      "model.safetensors.index.json", "tokenizer.json"]


def test_default_scope_is_whole_snapshot_for_comparison(tmp_path):
    for name in ("config.json", "README.md", "pytorch_model.bin", "tf_model.h5"):
        (tmp_path / name).write_bytes(name.encode())
    inv = inventory(tmp_path)
    assert inv["scope"] == "all" and inv["file_count"] == 4
    ref = inventory(tmp_path, scope="vllm")
    assert [f["path"] for f in ref["files"]] == ["config.json", "pytorch_model.bin"]
    assert ref["inventory_sha256"] != inv["inventory_sha256"]


def test_reference_field_not_used_for_comparability():
    """참고용 files_read_by_engine이 달라도 비교 조건(model.inventory_sha256)만 같으면 EVD 비교 조건은 같다."""
    from pathlib import Path as P

    src = (P(__file__).resolve().parents[1] / "src/guard/executors/direct_route.py").read_text(encoding="utf-8")
    assert "files_read_by_engine" not in src
    assert '"model_inventory_sha256": env.model.inventory_sha256' in src
