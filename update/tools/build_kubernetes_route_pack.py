"""검토된 PACK31에서 REQ02 기록 수집의 명시 DRAFT 팩을 만든다."""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import yaml

from guard.models import load_pack, sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.kubernetes_route_record import PROFILE, POLICY_TYPES, OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID = "PACK-KUBERNETES-ROUTE-RECORD"
LIMITATIONS = [
    "REQ02 기록 파일의 metadata 제품 연결만 제공한다. 정상/거부 실제 probe ledger, 생산자 인증, 기업 principal/operation/rewrite 정책과 최종 handler 인과 증명은 미완료다.",
    "정확 ENV/target 해시, 선정 session, 사전 고정 파일 bytes SHA256, timezone 유효창은 필수다. 관측한 해시를 승인값으로 사용하지 않는다.",
    "소유 engine namespace/Pod/container/image/restart/start 전후와 nofollow/fstat/pipe 경계를 확인한다. 별도 probe HTTP 요청은 보내지 않으며 선언된 probe가 실제 worker 주체를 증명하지 않는다.",
    "기록의 allow/deny/handler 주장은 인증되지 않았다. hash/enum 관측만 저장하며 모든 판정 축 UNKNOWN이다. PASS 또는 신뢰된 위반 FAIL을 생성하지 않는다.",
    "EXAMPLE/DECLARED 상한을 유지한다. native lifecycle lab/source/image gate 및 native0.32 미지원은 그대로다. 실제 Gateway/worker/GPU 검증은 별도다.",
]


def derive(seed, output):
    seed = Path(seed).resolve(); output = Path(output).resolve()
    if output.exists() or output.is_relative_to(seed):
        raise ValueError("NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED")
    raw = {p.relative_to(seed).as_posix(): p.read_bytes() for p in seed.rglob("*") if p.is_file()}
    manifest = yaml.safe_load(raw.pop("pack.yaml").decode("utf-8"))
    original = build_pack(manifest, raw)
    if original.ref().model_dump(mode="json") != SEED_REF or len(original.checks) != 77:
        raise ValueError("REVIEWED_PACK31_REQUIRED")
    check = yaml.safe_load(raw["checks/REQ-02.yaml"].decode("utf-8"))
    check.update(revision=3, executor="route_record", collection_profile=PROFILE,
        judge={"plugin": "rule_table", "params": {"pass_when_all": [], "fail_when_any": []}},
        policy_params=[{"key": key, "type": kind, "required": True} for key, kind in POLICY_TYPES.items()],
        required_observations=[{"key": key, "type": kind} for key, kind in OBSERVATIONS.items()],
        limitations=deepcopy(LIMITATIONS))
    changed = dict(raw)
    changed["checks/REQ-02.yaml"] = yaml.safe_dump(check, allow_unicode=True, sort_keys=False).encode("utf-8")
    new = deepcopy(manifest)
    new.update(pack_id=PACK_ID, revision=1,
        review={"status": "DRAFT", "reviewer": None, "reviewed_at": None},
        files_sha256=[{"path": name, "sha256": sha256_hex(data)} for name, data in sorted(changed.items())])
    result = build_pack(new, changed)
    if list(result.checks) != list(original.checks):
        raise ValueError("PACK_CHECK_SCOPE_CHANGED")
    output.mkdir(parents=True)
    for name, data in changed.items():
        path = output / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    (output / "pack.yaml").write_bytes(yaml.safe_dump(new, allow_unicode=True, sort_keys=False).encode("utf-8"))
    if load_pack(output).ref() != result.ref():
        raise ValueError("GENERATED_PACK_INTEGRITY_FAILED")
    return {"seed_ref": SEED_REF, "pack_ref": result.ref().model_dump(mode="json"),
            "checks": 77, "unchanged_definition_bytes": 76, "policy_values_created": False,
            "full_REQ02_complete": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(derive(args.seed, args.output), ensure_ascii=False))

if __name__ == "__main__":
    main()
