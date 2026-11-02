"""불변 PACK31 원본: 정확 Ref, 전체 bytes, 현행 팩 변경과의 분리."""
from __future__ import annotations

import hashlib

import yaml

from guard.models import load_pack
from tools.pack_seed import SEED_DIR, SEED_REF
from tools.build_kubernetes_api_pack import derive

FULL_PACK_BYTES_SHA256 = "42266d61aab87e55e1ac23a58f8302470e1d89bbf32a527310d4f97891dcc32f"


def _inventory():
    return {p.relative_to(SEED_DIR).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(SEED_DIR.rglob("*")) if p.is_file()}


def test_seed_is_the_exact_reviewed_pack31():
    pack = load_pack(SEED_DIR)
    assert pack.ref().model_dump(mode="json") == SEED_REF
    assert pack.manifest.revision == 31 and len(pack.checks) == 77
    manifest = yaml.safe_load((SEED_DIR / "pack.yaml").read_text(encoding="utf-8"))
    declared = {row["path"]: row["sha256"] for row in manifest["files_sha256"]}
    inventory = _inventory()
    assert set(inventory) == set(declared) | {"pack.yaml"}
    assert all(inventory[name] == digest for name, digest in declared.items())


def test_seed_full_bytes_are_pinned_independently_of_the_manifest():
    joined = "\n".join(f"{name} {digest}" for name, digest in _inventory().items())
    assert hashlib.sha256(joined.encode()).hexdigest() == FULL_PACK_BYTES_SHA256


def test_builder_accepts_the_seed_after_current_pack_changes(tmp_path):
    receipt = derive(SEED_DIR, tmp_path / "derived")
    assert receipt["seed_ref"] == SEED_REF and receipt["checks"] == 77
