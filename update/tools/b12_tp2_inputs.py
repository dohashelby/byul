"""B4 TP2 분기 입력과 A6 인계 계획을 작성한다. 외부 명령 실행 기능은 없다.

모든 runtime 값·정책·시간은 미입력 상태로 시작한다. 파일 포인터는 선언값이며
실제 GPU 바인딩이나 실행 권한으로 취급하지 않는다.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from guard.collectors import gpu_common_inputs, group_resource_producer, worker_shard_producer
from guard.core.select import check_policy_value
from guard.executors import rank_failure_contract, resource_recovery_contract, supply_boot_contract
from guard.models import load_pack
from guard.runtime.bounded import strict_json

INPUT_SCHEMA = "guard-b-tp2-inputs/1"
OUTPUT_SCHEMA = "guard-b-tp2-prepare/1"
CHECK_IDS = ("LIF-08", "LIF-11", "SUP-06")
REFERENCE_KEYS = ("ENV", "target", "prepared", "selection", "scenario")
CONTROL_KEYS = ("normal", "failure", "recovery")
CHECK_KEYS = {"runtime_refs", "policy_values", "controls", "planned_upper_seconds"}
_SHA = re.compile(r"[0-9a-f]{64}")
SPECS = {
    "LIF-08": {
        "kind": "rank-fault", "owner": "A 공통 생산자 / B 실행 준비 / A6 하네스",
        "producer": None,
        "scenarios": ["normal-before", "exception", "death", "delay", "communication-stall", "normal-after"],
        "limitations": ["각 fault의 실제 발생·감지·상위 전달·기한을 구분한다.",
                        "관측 구간 이름이나 HTTP timeout을 실제 fault 활성으로 취급하지 않는다.",
                        "현재 커밋에는 공통 rank-fault 생산자 구현/신원 반환이 확인되지 않았다."],
    },
    "LIF-11": {
        "kind": "failed-group-resources", "owner": "B 소유 자원 선택 / A 생산자 / A6 하네스",
        "producer": group_resource_producer.producer,
        "scenarios": ["normal-before", "verified-worker-failure", "resource-census", "restarted-normal"],
        "limitations": ["실패 trigger와 조건부 기한, PID/start·IPC/SHM 소유·다음 세대 정상 추론을 결합한다.",
                        "GPU_MEMORY_RELEASE_UNOBSERVED는 등록 뒤에도 남을 수 있다.",
                        "선정 FD 부재나 컨테이너 종료는 전역 GPU/IPC 반환의 증거가 아니다."],
    },
    "SUP-06": {
        "kind": "worker-shard-load", "owner": "B 모델/shard 준비 / A 생산자 / A6 하네스",
        "producer": worker_shard_producer.producer,
        "scenarios": ["normal-all-required-shards", "missing-selected-shard", "restored-normal"],
        "limitations": ["필수 shard·rank 배정·적재 완료/실패·READY와 실제 tensor 소비는 구분한다.",
                        "누락 시험은 자기 소유 사본에서만 수행한다. 정상/실패/복구 입력을 별도 고정한다.",
                        "현재 fixture는 단일 rank CPU 결과이며 TP2 GPU 실측으로 승격하지 않는다."],
    },
}


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _dump(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _template(pack):
    return {"schema": INPUT_SCHEMA, "checks": {
        cid: {"runtime_refs": {key: None for key in REFERENCE_KEYS},
              "policy_values": {p.key: None for p in pack.checks[cid].policy_params},
              "controls": {key: None for key in CONTROL_KEYS}, "planned_upper_seconds": None}
        for cid in CHECK_IDS}}


def _pointer(value):
    # 선언된 포인터의 파일은 열지 않는다. 실제 파일·실행 세대는 A6에서 확인한다.
    if (type(value) is not dict or set(value) != {"path", "file_sha256"}
            or type(value["path"]) is not str or not value["path"].strip()
            or "\x00" in value["path"] or type(value["file_sha256"]) is not str
            or not _SHA.fullmatch(value["file_sha256"])):
        raise ValueError("artifact pointer requires path and file_sha256 (file bytes, not an object Ref hash)")


def validate_inputs(value, pack):
    if type(value) is not dict or set(value) != {"schema", "checks"} or value["schema"] != INPUT_SCHEMA:
        raise ValueError("B_TP2_INPUT_SCHEMA_INVALID")
    if type(value["checks"]) is not dict or not set(value["checks"]) <= set(CHECK_IDS):
        raise ValueError("B_TP2_CHECK_SCOPE_INVALID")
    merged = _template(pack)
    for cid, section in value["checks"].items():
        if type(section) is not dict or not set(section) <= CHECK_KEYS:
            raise ValueError("B_TP2_CHECK_FIELDS_INVALID")
        target = merged["checks"][cid]
        for field in ("runtime_refs", "controls"):
            if field not in section:
                continue
            incoming = section[field]
            if type(incoming) is not dict or not set(incoming) <= set(target[field]):
                raise ValueError("B_TP2_POINTER_FIELDS_INVALID")
            for key, pointer in incoming.items():
                if pointer is not None:
                    _pointer(pointer)
                target[field][key] = deepcopy(pointer)
        if "policy_values" in section:
            values = section["policy_values"]
            if type(values) is not dict or not set(values) <= set(target["policy_values"]):
                raise ValueError("B_TP2_POLICY_FIELDS_INVALID")
            for param in pack.checks[cid].policy_params:
                if param.key in values:
                    if values[param.key] is not None:
                        check_policy_value(cid, param.key, param.type, values[param.key])
                        if param.key.endswith("_sha256") and not _SHA.fullmatch(values[param.key]):
                            raise ValueError("B_TP2_POLICY_FINGERPRINT_INVALID")
                    target["policy_values"][param.key] = deepcopy(values[param.key])
        if "planned_upper_seconds" in section:
            seconds = section["planned_upper_seconds"]
            if seconds is not None and (type(seconds) is not int or seconds <= 0):
                raise ValueError("B_TP2_TIME_ESTIMATE_INVALID")
            target["planned_upper_seconds"] = seconds
        values = target["policy_values"]
        if all(value is not None for value in values.values()):
            if cid == "LIF-08":
                rank_failure_contract.normalize_policy(values)
            elif cid == "LIF-11":
                resource_recovery_contract.normalize_policy(values)
            else:
                supply_boot_contract.normalize_policy(cid, values)
    return merged


def _source_refs(root):
    names = [
        "tools/b12_tp2_inputs.py", "tools/b12_tp2_verify.py", "src/guard/collectors/gpu_common_inputs.py",
        "src/guard/collectors/group_resource_producer.py", "src/guard/collectors/worker_shard_producer.py",
        "src/guard/executors/rank_failure_contract.py", "src/guard/executors/resource_recovery_contract.py",
        "src/guard/executors/supply_boot_contract.py", "packs/PACK-BASELINE/pack.yaml",
    ]
    return [{"path": "update/" + name, "file_sha256": _digest((root / name).read_bytes())}
            for name in names]


def prepare(output: Path, inputs: dict | None = None, *, verify_inputs_root: Path | None = None) -> dict:
    update = Path(__file__).resolve().parents[1]
    pack = load_pack(update / "packs/PACK-BASELINE")
    values = validate_inputs(_template(pack) if inputs is None else inputs, pack)
    output = Path(output)
    if output.exists():
        raise ValueError("NEW_B_TP2_OUTPUT_DIRECTORY_REQUIRED")
    verification = None
    if verify_inputs_root is not None:
        from tools.b12_tp2_verify import verify_local_inputs
        verification = verify_local_inputs(values, verify_inputs_root)
    # Validate the entire input before creating any output. Never clean an existing path.
    output.mkdir(parents=True, exist_ok=False)
    branches, unresolved = [], []
    for cid in CHECK_IDS:
        section, spec, check = values["checks"][cid], SPECS[cid], pack.checks[cid]
        producer = spec["producer"]() if spec["producer"] is not None else None
        supported = bool(producer and gpu_common_inputs.SUPPORTED_PRODUCERS.get(
            (producer["name"], producer["version"])) == producer["source_sha256"])
        gaps = []
        for group in ("runtime_refs", "controls", "policy_values"):
            for key, value in section[group].items():
                if value is None:
                    gaps.append({"check_id": cid, "field": group + "." + key,
                                 "reason": "not_provided", "owner": spec["owner"]})
        if section["planned_upper_seconds"] is None:
            gaps.append({"check_id": cid, "field": "planned_upper_seconds",
                         "reason": "no_branch_time_upper_bound", "owner": "B 입력 / A6 시간표"})
        if not supported:
            gaps.append({"check_id": cid, "field": "producer",
                         "reason": "producer_identity_not_returned" if producer is None else "producer_not_registered",
                         "owner": "A 공통 생산자 / J1 등록"})
        gaps.extend([
            {"check_id": cid, "field": "runtime_binding", "reason": "declared_file_pointers_not_runtime_verified",
             "owner": "A6 실제 ENV/target/prepared/세대 확인"},
            {"check_id": cid, "field": "execution_authorization", "reason": "current_request_is_prepare_only_no_gpu",
             "owner": "공동 실행 조정"},
        ])
        prefix = cid.replace("-", "_")
        branch = {"check_id": cid, "executor": check.executor,
                  "collection_profile": getattr(check, "collection_profile", None),
                  "producer_kind": spec["kind"], "producer": producer, "producer_supported": supported,
                  "policy_types": {p.key: p.type for p in check.policy_params},
                  **section, "scenario_order": spec["scenarios"], "scope_limitations": spec["limitations"],
                  "argv_templates": [
                      ["guard", "select", "--pack", "packs/PACK-BASELINE", "--env", "${" + prefix + "_ENV_FILE}",
                       "--policy", "${" + prefix + "_POLICY_FILE}",
                       "--config", "${" + prefix + "_CONFIG_FILE}", "--store", "${STORE}"],
                      ["guard", "run", "--plan", "${" + prefix + "_PLAN_ID}",
                       "--config", "${" + prefix + "_CONFIG_FILE}", "--store", "${STORE}"],
                      ["guard", "judge", "--plan", "${" + prefix + "_PLAN_ID}",
                       "--as-of", "${AS_OF}", "--config", "${" + prefix + "_CONFIG_FILE}", "--store", "${STORE}"],
                      ["guard", "report", "--plan", "${" + prefix + "_PLAN_ID}",
                       "--json", "${" + prefix + "_REPORT_FILE}",
                       "--config", "${" + prefix + "_CONFIG_FILE}", "--store", "${STORE}"],
                  ],
                  "commands_executed": False, "execution_authorized": False, "ready_for_execution": False,
                  "unresolved": gaps}
        branches.append(branch)
        unresolved.extend(gaps)
        _dump(output / (cid + "-plan.json"), branch)
    _dump(output / "inputs-template.json", _template(pack))
    _dump(output / "declared-inputs.json", values)
    if verification is not None:
        _dump(output / "local-verification.json", verification)
    times = [b["planned_upper_seconds"] for b in branches]
    manifest = {"schema": OUTPUT_SCHEMA, "input_schema": INPUT_SCHEMA, "checks": list(CHECK_IDS),
                "pack_ref": pack.ref().model_dump(mode="json"), "data_origin": "EXAMPLE", "official": False,
                "preparation_only": True, "execution_authorized": False, "ready_for_execution": False,
                "GPU_or_AWS_operations": 0, "subprocesses_started": 0,
                "file_pointers_opened_or_verified": False, "unresolved": unresolved,
                "source_refs": _source_refs(update),
                "time_plan": {"B_total_upper_seconds": sum(times) if all(t is not None for t in times) else None,
                              "A_work_and_download_and_registration_seconds": None,
                              "cleanup_and_stop_reserve_seconds": None,
                              "joint_EC2_budget_USD_from_instruction": 20,
                              "EBS_included": False, "joint_budget_fit_verified": False},
                "artifacts": [{"path": p.name, "file_sha256": _digest(p.read_bytes())}
                              for p in sorted(output.iterdir()) if p.is_file()]}
    if verification is not None:
        manifest["local_verification"] = {
            "path": "local-verification.json", "status": verification["status"],
            "all_declared_files_verified": verification["all_declared_files_verified"],
            "all_declared_json_objects_verified": verification["all_declared_json_objects_verified"],
            "runtime_binding_verified": False, "prepared_assets_verified": False,
            "errors": verification["errors"], "unresolved": verification["unresolved"],
        }
        manifest["file_pointers_opened_or_verified"] = any(v.get("bytes_read", False) for v in verification["file_checks"])
    _dump(output / "manifest.json", manifest)
    return manifest


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--verify-inputs-root", type=Path,
                        help="이 로컬 루트 안의 선언된 JSON 파일과 SHA/결합만 검사. 명령을 실행하지 않음")
    args = parser.parse_args()
    try:
        if args.inputs:
            with args.inputs.open("rb") as stream:
                supplied = strict_json(stream.read(1024 * 1024 + 1), limit=1024 * 1024)
        else:
            supplied = None
        manifest = prepare(args.output, supplied, verify_inputs_root=args.verify_inputs_root)
    except (ValueError, OSError):
        parser.exit(2, "TP2 준비 입력이나 출력 경로를 확인하십시오. 기존 결과는 덮어쓰지 않습니다.\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if manifest.get("local_verification", {}).get("status") == "FAILED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
