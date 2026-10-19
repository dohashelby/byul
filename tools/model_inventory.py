"""모델 inventory: 파일별 {path, bytes, sha256}을 경로순으로 정렬해 SPEC §2.3 정렬 JSON으로 직렬화하고 그 SHA-256을 낸다.

    uv run python tools/model_inventory.py ~/guard-models/hub/models--facebook--opt-125m/snapshots/<revision>

심볼릭 링크(HF cache의 snapshot → blobs)는 따라가서 실제 내용을 해시한다. 경로는 snapshot 기준 상대 경로(POSIX).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from guard.models.hashing import canonical_json, sha256_hex  # noqa: E402


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inventory(snapshot: Path) -> dict:
    snapshot = Path(snapshot).expanduser()
    files = []
    for p in sorted(snapshot.rglob("*")):
        if p.is_dir():
            continue
        real = p.resolve(strict=True)
        files.append({"path": p.relative_to(snapshot).as_posix(), "bytes": real.stat().st_size, "sha256": _file_sha256(real)})
    files.sort(key=lambda f: f["path"])
    return {"file_count": len(files), "total_bytes": sum(f["bytes"] for f in files), "files": files,
            "inventory_sha256": sha256_hex(canonical_json(files))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("snapshot", type=Path)
    ap.add_argument("--json", type=Path, help="결과를 파일로 저장")
    args = ap.parse_args()
    inv = inventory(args.snapshot)
    text = json.dumps(inv, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(text, encoding="utf-8")
    print(f"files={inv['file_count']} bytes={inv['total_bytes']} inventory_sha256={inv['inventory_sha256']}")


if __name__ == "__main__":
    main()
