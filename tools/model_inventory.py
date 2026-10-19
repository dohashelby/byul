"""모델 inventory: 파일별 {path, bytes, sha256}을 경로순으로 정렬해 SPEC §2.3 정렬 JSON으로 직렬화하고 그 SHA-256을 낸다.

    uv run python tools/model_inventory.py ~/guard-models/hub/models--facebook--opt-125m/snapshots/<revision> [--scope all|vllm]

범위
- `all`(기본, **비교용** → ENV model.inventory_sha256, INV-09): 마운트한 snapshot 전체.
  엔진 버전마다 읽는 파일이 달라질 수 있으므로 비교 조건은 엔진과 무관한 마운트 내용 전체로 고정한다.
- `vllm`(**참고용** → ENV model.files_read_by_engine, 판정·비교에 쓰지 않음): snapshot 중 vLLM이 읽는 파일만 —
  설정·토크나이저 파일과 `load_format=auto`가 고르는 가중치 형식 하나(*.safetensors > *.bin > *.pt)와 그 index.
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


CONFIG_FILES = {"config.json", "generation_config.json", "preprocessor_config.json", "processor_config.json"}
TOKENIZER_FILES = {"tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json",
                   "vocab.json", "vocab.txt", "merges.txt", "tokenizer.model", "chat_template.jinja", "chat_template.json"}
WEIGHT_FORMATS = [  # vLLM load_format=auto의 우선순위
    (".safetensors", "model.safetensors.index.json"),
    (".bin", "pytorch_model.bin.index.json"),
    (".pt", None),
]


def vllm_read_files(paths: list[str]) -> list[str]:
    """snapshot 상대 경로 목록 중 vLLM이 읽는 파일."""
    names = set(paths)
    keep = {p for p in names if Path(p).name in CONFIG_FILES | TOKENIZER_FILES or Path(p).suffix == ".tiktoken"}
    for suffix, index in WEIGHT_FORMATS:
        weights = {p for p in names if p.endswith(suffix)}
        if weights:
            keep |= weights
            if index and index in names:
                keep.add(index)
            break
    return sorted(keep)


def inventory(snapshot: Path, scope: str = "all") -> dict:
    if scope not in ("vllm", "all"):
        raise ValueError(scope)
    snapshot = Path(snapshot).expanduser()
    all_paths = sorted(p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if not p.is_dir())
    selected = set(vllm_read_files(all_paths) if scope == "vllm" else all_paths)
    files = []
    for p in sorted(snapshot.rglob("*")):
        if p.is_dir() or p.relative_to(snapshot).as_posix() not in selected:
            continue
        real = p.resolve(strict=True)
        files.append({"path": p.relative_to(snapshot).as_posix(), "bytes": real.stat().st_size, "sha256": _file_sha256(real)})
    files.sort(key=lambda f: f["path"])
    return {"scope": scope, "file_count": len(files), "total_bytes": sum(f["bytes"] for f in files), "files": files,
            "inventory_sha256": sha256_hex(canonical_json(files))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("snapshot", type=Path)
    ap.add_argument("--json", type=Path, help="결과를 파일로 저장")
    ap.add_argument("--scope", choices=["all", "vllm"], default="all")
    args = ap.parse_args()
    inv = inventory(args.snapshot, args.scope)
    text = json.dumps(inv, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(text, encoding="utf-8")
    print(f"scope={inv['scope']} files={inv['file_count']} bytes={inv['total_bytes']} inventory_sha256={inv['inventory_sha256']}")


if __name__ == "__main__":
    main()
