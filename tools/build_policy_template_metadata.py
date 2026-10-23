"""검토된 카탈로그의 실제 선언 경로만 패키지 설명으로 추출한다. 런타임 도구가 아니다."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from guard.models import Pack, load_pack

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src/guard/collectors/policy_template_metadata.json"


def _strings(value: str, where: str) -> list[str]:
    data = json.loads(value)
    if not isinstance(data, list) or any(not isinstance(item, str) for item in data):
        raise ValueError(f"{where}: JSON 문자열 배열 필요")
    return data


def build_metadata(pack: Pack, catalog: Path) -> dict:
    """선언·타입·출처가 빠지거나 중복되면 실패한다. 값/기본값은 추출하지 않는다."""
    declared = {f"values.{c.id}.{p.key}": p.type for c in pack.active_checks() for p in c.policy_params}
    entries = {}
    with Path(catalog).open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            where = row["proposed_policy_id"]
            paths = _strings(row["current_param_paths"], where)
            for path in paths:
                if path not in declared or path in entries:
                    raise ValueError(f"{where}: 미선언 또는 중복 경로 {path}")
                if row["classification"] != "a" or row["proposed_shape"] != declared[path]:
                    raise ValueError(f"{where}: 선언 분류/타입 불일치 {path}")
                if path.split(".")[1] not in _strings(row["check_ids"], where):
                    raise ValueError(f"{where}: 점검 연결 불일치 {path}")
                owner = row["owner_source_original"]
                role = "미지정" if owner == "원문 미지정" else (
                    "원문 역할: " + " · ".join(_strings(owner, where)) + " (실제 담당자 미지정)")
                entries[path] = {
                    "type": declared[path], "description": row["description"], "decision_role": role,
                    "consumer": " / ".join(_strings(row["current_consumer"], where)),
                    "verification": row["verification_level"].replace("재実測なし", "재실측 없음"),
                    "catalog_id": where,
                }
    missing = declared.keys() - entries.keys()
    if missing:
        raise ValueError(f"카탈로그 연결 누락: {sorted(missing)}")
    return {"pack": pack.ref().model_dump(mode="json"),
            "parameters": {key: entries[key] for key in sorted(entries)}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=ROOT / "packs/PACK-BASELINE")
    parser.add_argument("--catalog", type=Path, default=ROOT / "docs/policy_catalog.csv")
    parser.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args()
    pack = load_pack(args.pack)
    if args.out.resolve().is_relative_to(args.pack.resolve()):
        raise ValueError("설명 출력은 입력 팩 밖이어야 합니다")
    data = build_metadata(pack, args.catalog)
    content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    # 개발용 갱신은 지정 파일을 교체할 수 있다. 생성할 기업 정책 파일과는 다른 경계다.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if not args.out.exists() or args.out.read_bytes() != content:
        args.out.write_bytes(content)
    print(f"{len(data['parameters'])}개 선언의 설명 갱신: {args.out}")


if __name__ == "__main__":
    main()
