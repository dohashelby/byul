"""PACK-BASELINE에서 SUP-08만 실행 판본 관측 판정으로 바꾼 DRAFT 팩을 만든다.

SUP-08 revision 5: 요청의 코드 판본 선택 시도 전후로 엔진의 동적 모듈 판본 폴더를 읽어,
승인 목록 밖 판본이 새로 생기면 FAIL, 거부되거나 승인 판본만 있으면 해결된 시도로 본다.
나머지 76개 정의 bytes는 그대로 둔다.
"""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import yaml
from guard.models import load_pack, sha256_hex
from guard.models.checkdef import build_pack

PACK_ID = "PACK-CODE-SELECTION"
SEED_PACK_ID = "PACK-BASELINE"
CHECK = "checks/SUP-08.yaml"
LIMITATIONS = [
    "구현 분기: 지정 필드(점으로 이은 중첩 경로 포함)에 무해한 비허용 판본 이름을 넣고, 시도 직전·직후 엔진 컨테이너의 동적 모듈 판본 폴더 목록을 읽기 전용으로 비교한다.",
    "승인 목록(approved_code_revisions) 밖 판본 폴더가 시도 직후 새로 생기면 그 요청 값이 코드 선택에 반영된 것으로 보고 위반으로 적는다. 시도 전부터 있던 비승인 폴더는 이번 요청에 귀속할 수 없어 미확인으로 남긴다.",
    "시도가 거부되었거나 시도 직후 승인 판본 폴더만 있으면 해결된 시도로 본다. 응답 2xx만으로는 위반·통과를 정하지 않는다.",
    "폴더 이름·파일 이름·크기·SHA-256만 수집하며 코드를 import하거나 실행하지 않는다. 처리기 캐시 재사용으로 판본 선택이 일어나지 않은 경우는 그 실행 구성에서 반영되지 않은 것으로 기록한다.",
    "모듈 목록을 읽지 못한 구성(관측 설정 없음, 일반 HTTP 경로)은 기존처럼 accepted_unverified로 UNKNOWN이다.",
]


def check_definition(raw):
    check = yaml.safe_load(raw.decode("utf-8"))
    if check.get("id") != "SUP-08" or check.get("collection_profile") != "request-code-selection-v1":
        raise ValueError("SUP08_SEED_UNEXPECTED")
    params = [row for row in check["policy_params"] if row["key"] != "approved_code_revisions"]
    params.append({"key": "approved_code_revisions", "type": "list[str]", "required": True})
    observations = [row for row in check["required_observations"]
                    if row["key"] not in {"code_selection_ignored_fields", "code_selection_resolved_fields"}]
    observations[5:5] = [{"key": "code_selection_ignored_fields", "type": "list"},
                         {"key": "code_selection_resolved_fields", "type": "list"}]
    check.update(
        revision=5,
        policy_params=params,
        required_observations=observations,
        judge={"plugin": "rule_table", "params": {
            "pass_when_all": [
                {"obs": "code_selection_forbidden", "op": "eq", "value": True},
                {"obs": "code_selection_resolved_fields", "op": "superset_of", "value_obs": "code_selection_fields_planned"},
                {"obs": "code_selection_unverified_fields", "op": "empty"},
                {"obs": "code_selection_confirmed_violations", "op": "empty"}],
            "fail_when_any": [{"obs": "code_selection_confirmed_violations", "op": "nonempty"}],
            "axes": deepcopy(check["judge"]["params"]["axes"])}},
        limitations=deepcopy(LIMITATIONS))
    return yaml.safe_dump(check, allow_unicode=True, sort_keys=False).encode("utf-8")


def derive(seed, output):
    seed = Path(seed).resolve(); output = Path(output).resolve()
    if output.exists() or output.is_relative_to(seed):
        raise ValueError("NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED")
    raw = {p.relative_to(seed).as_posix(): p.read_bytes() for p in seed.rglob("*") if p.is_file()}
    manifest = yaml.safe_load(raw.pop("pack.yaml").decode("utf-8"))
    original = build_pack(manifest, raw)
    if manifest.get("pack_id") != SEED_PACK_ID or len(original.checks) != 77:
        raise ValueError("BASELINE_PACK_REQUIRED")
    changed = dict(raw); changed[CHECK] = check_definition(raw[CHECK])
    new = deepcopy(manifest)
    new.update(pack_id=PACK_ID, revision=1, review={"status": "DRAFT", "reviewer": None, "reviewed_at": None},
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
    return {"seed_ref": original.ref().model_dump(mode="json"), "pack_ref": result.ref().model_dump(mode="json"),
            "checks": 77, "unchanged_definition_bytes": 76, "changed": [CHECK]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(); print(json.dumps(derive(args.seed, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
