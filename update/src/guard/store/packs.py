"""팩 보존본: select 시점 팩을 저장소의 내용 주소 파일(artifacts/<sha256>)로 남긴다 (Step 4).

- 매니페스트: 정렬 JSON 바이트를 `artifacts/<팩 Ref의 sha256>`에 둔다. 팩 Ref의 sha256이 곧 그 바이트의 해시다.
- 파일: 매니페스트 `files_sha256`의 각 파일 바이트를 `artifacts/<파일 sha256>`에 둔다.
새 객체 종류·DB 스키마는 만들지 않는다. 읽을 때 매니페스트·파일 해시를 모두 검증한다(build_pack).
"""

from __future__ import annotations

import json
from pathlib import Path

from guard.models import Pack, Ref, canonical_json, load_pack, sha256_hex
from guard.models.checkdef import PackIntegrityError, build_pack

from .sqlite import Store


class PackNotStoredError(LookupError):
    pass


def put_pack(store: Store, pack_dir: Path) -> Ref:
    """팩 폴더를 검증한 뒤 보존본으로 저장한다. 같은 내용은 한 번만 저장된다."""
    pack = load_pack(pack_dir)  # 폴더 해시 검증
    ref = pack.ref()
    manifest_bytes = canonical_json(pack.manifest.model_dump(mode="json"))
    assert sha256_hex(manifest_bytes) == ref.sha256
    for f in pack.manifest.files_sha256:
        stored = store.put_artifact((Path(pack_dir) / f.path).read_bytes())
        if stored.sha256 != f.sha256:
            raise PackIntegrityError(f"sha256 mismatch while storing {f.path}")
    store.put_artifact(manifest_bytes)
    return ref


def get_pack(store: Store, ref: Ref) -> Pack:
    """PLAN이 가리키는 팩 Ref로 보존본을 읽는다. 없거나 Ref(id·revision·sha256)와 다르면 거부."""
    mpath = store.root / "artifacts" / ref.sha256
    if ref.kind != "PACK" or not mpath.exists():
        raise PackNotStoredError(f"pack {ref.id} rev {ref.revision} is not stored in {store.root}")
    raw = mpath.read_bytes()
    if sha256_hex(raw) != ref.sha256:
        raise PackIntegrityError(f"stored manifest for {ref.id} rev {ref.revision} is corrupted")
    manifest = json.loads(raw)
    if manifest.get("pack_id") != ref.id or manifest.get("revision") != ref.revision:
        raise PackNotStoredError(f"stored manifest is {manifest.get('pack_id')} rev {manifest.get('revision')}, "
                                 f"not {ref.id} rev {ref.revision}")
    files = {}
    for f in manifest["files_sha256"]:
        p = store.root / "artifacts" / f["sha256"]
        if not p.exists():
            raise PackIntegrityError(f"stored pack file missing: {f['path']}")
        files[f["path"]] = p.read_bytes()
    pack = build_pack(manifest, files, source="store")
    if pack.ref() != ref:
        raise PackIntegrityError(f"stored pack does not match {ref.id} rev {ref.revision}")
    return pack


__all__ = ["PackNotStoredError", "get_pack", "put_pack"]
