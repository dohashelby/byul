"""검토된 PACK31에서 REQ02 native 인증/등록handler의 명시 DRAFT 팩을 만든다."""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import yaml

from guard.models import load_pack, sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.kubernetes_native_route import PROFILE, POLICY_TYPES, OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID = "PACK-KUBERNETES-NATIVE-ROUTE"
LIMITATIONS = ['REQ02 native8 인증 bool/경로/등록 callback과 자체HTTP3건의 동일 recording 후보를 연결한다. 실제principal/권한/Gateway rewriting/producer/worker/전체REQ02는 미확인이고 모든축UNKNOWN·normal succeeded=null이다.', '정확 ENV/target/prepared SHA와<=600초 aware 유효창, native8 session/PID-start/source-code/module/clock/전후Pod세대와선택headerhash를검사한다. hash나시간근접으로다른process/RPC를결합하지않는다.', 'REQ02 전용3요청 GETmodels-before/POSTcompletion/GETmodels-after와 고유ID/닫힌최대1토큰 입력만 전송한다. 다른점검ID/임의credential/원문요청응답·envargv·예외는저장하지않는다.', '기존 hook/parser/reader/정확 seal_session을 재사용하고 별도exec Python을 실제serving관측으로대체하지않는다. native이미지강한gatefalse·nodeimage/manifest ancestry미확인·EXAMPLE/labtrue/vLLM0.31/native0.32미지원을유지한다.', '뒤수집/세대교체/seal/report/첨부/정리오류가앞선정제HTTP/native자료를지우지않는다. 선언token결과와callback관측을확인된인가우회FAIL로승격하지않고전체PASS는없다.', '원REQ02질문/기준/분기·기존route_record제품과다른76정의bytes·c9/sources/worktree/팩/Ref/실증·부모A39/56/4·A1 19/27/2·GPU창종료/Start0/기존비용/EBS를유지한다.']


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
    check.update(revision=4, executor="native_route", collection_profile=PROFILE,
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
