"""팩·registry·기존 실험 기록으로 77행의 구현/실험 추적표를 재생성한다.

엔진이나 테스트를 실행하지 않는다. 과거 실측을 현재 팩의 실측으로 승격하지 않는다.
예: uv run python -m tools.checklist_status --check
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import csv
import io
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = Path("docs/checklist_validation_status.csv")
MD_PATH = Path("docs/CHECKLIST_VALIDATION_STATUS.md")
FIELDS = (
    "check_id", "area", "question", "pack_revision", "check_revision", "retired",
    "policy_keys", "executor", "judge", "real_executor_registered", "judge_registered",
    "implementation", "historical_grade", "grade_uncertainty", "experiment_group", "priority",
    "windows_cpu_status", "current_pack_actual_status", "required_observations",
    "normal_case", "violation_case", "missing_case", "unsupported_case", "reuse_candidate",
    "next_experiment", "prerequisites", "team_decisions", "historical_verification",
    "historical_pack_revisions", "historical_evidence_refs", "remaining_scope",
)
GROUPS = {
    "approval_workflow": "SUPPLY_APPROVAL", "supply_integrity": "SUPPLY_APPROVAL",
    "code_load_control": "SUPPLY_APPROVAL", "http_boundary": "ACCESS_AUTHORIZATION",
    "object_authz": "ACCESS_AUTHORIZATION", "input_validation": "INPUT_RESPONSE",
    "response_content": "INPUT_RESPONSE", "request_attribution": "INPUT_RESPONSE",
    "request_lifecycle": "INPUT_RESPONSE", "cache_isolation": "CACHE_IDENTITY",
    "log_analysis": "LOG_OBSERVABILITY", "record_access": "LOG_OBSERVABILITY",
    "record_integrity": "LOG_OBSERVABILITY", "telemetry_policy": "LOG_OBSERVABILITY",
    "fault_injection": "LIFECYCLE_RECOVERY", "lifecycle_transition": "LIFECYCLE_RECOVERY",
    "load_resource": "LIFECYCLE_RECOVERY",
}
REUSE = {
    "http_boundary": "direct_route / role_route: endpoint·신원·최종 소비 관측의 재사용 범위 검토",
    "object_authz": "HTTP probe 재사용 후보; 객체 소유권·신뢰 신원 관측 추가 필요",
    "input_validation": "input_validation / sampling_validation / template_validation 관측 비교",
    "response_content": "response_content의 합성 표식·필드 수집 재사용 후보",
    "request_attribution": "request_binding 입력 관측 재사용 후보; 독립 출력 귀속은 별도",
    "log_analysis": "log_analysis / log_integrity 수집 경로·완결성 재사용 후보",
    "record_integrity": "기존 해시·로그 수집 재사용 후보; 대상 저장소 권한은 별도",
    "container_config": "container_config / credential_exposure의 프로세스·mount 관측 비교",
    "credential_lifecycle": "credential_exposure의 전달 관측 재사용 후보; 회전·만료 효과는 별도",
    "fault_injection": "sandbox 기동·실패·정리 관측 재사용 후보; 대상 엔진의 접수 gate는 별도",
    "lifecycle_transition": "sandbox 상태·정상 대조 재사용 후보; 전환 세대·복구는 별도",
    "supply_integrity": "model_manifest·model_inventory 재사용 후보; 실제 소비 파일은 별도 계측",
    "approval_workflow": "기존 정책·수동 증거 경로; 실제 기업 권한 집행점은 별도",
    "cache_isolation": "REQ-15/16 수동 계약; 실제 캐시 격리 실행기로 간주하지 않음",
}


def registry_keys(path: Path, variable: str) -> set[str]:
    """등록 함수나 실행기를 호출하지 않고 실제 registry의 정적 키만 읽는다."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == variable:
            if not isinstance(node.value, ast.Dict) or any(not isinstance(k, ast.Constant) or not isinstance(k.value, str) for k in node.value.keys):
                raise ValueError(f"registry_keys_not_literal:{variable}")
            keys = [k.value for k in node.value.keys]
            if len(keys) != len(set(keys)):
                raise ValueError(f"duplicate_registry_key:{variable}")
            return set(keys)
    raise ValueError(f"registry_not_found:{variable}")


def _check_records(value):
    if isinstance(value, dict):
        if "check_id" in value and ("actual" in value or "verdicts" in value):
            yield value
        for child in value.values():
            yield from _check_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _check_records(child)


def historical_evidence(root: Path) -> dict[str, list[dict]]:
    """실제 CPU 이력이 명시된 세 선별 기록만 읽는다. 설치본/합성 기록은 실측이 아니다."""
    sources = (
        ("automation-phase1-20261023.json", ("pack_ref",), ("matrix", "all_eight")),
        ("automation-phase2-20261023.json", ("provenance", "pack"), ("actual_cpu_runs", "preserved_first_actual_attempt", "preserved_second_actual_attempt")),
        ("automation-phase3-20261023.json", ("integration", "pack"), ("attempts",)),
    )
    result: dict[str, list[dict]] = {}
    for filename, pack_path, branches in sources:
        path = root / "docs/evidence" / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        pack = data
        for key in pack_path:
            pack = pack[key]
        for branch in branches:
            for record in _check_records(data.get(branch)):
                selected = {"source": f"docs/evidence/{filename}", "branch": branch, "pack": pack}
                for key in ("evidence_ref", "result_ref", "verification_level"):
                    if record.get(key) is not None:
                        selected[key] = record[key]
                selected["verdicts"] = record.get("verdicts", record.get("actual"))
                bucket = result.setdefault(record["check_id"], [])
                if selected not in bucket:
                    bucket.append(selected)
    return result


def _indexed(rows: list[dict], key: str, label: str) -> dict[str, dict]:
    ids = [row[key] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate_{label}_id")
    return dict(zip(ids, rows, strict=True))


def build_rows(checks: list[dict], matrix: list[dict], *, pack_revision: int,
               executor_keys: set[str], judge_keys: set[str], evidence: dict[str, list[dict]]) -> list[dict[str, str]]:
    """원본 77행의 순서를 유지하고 구현 상태만 현재 등록에서 계산한다."""
    by_id = _indexed(checks, "id", "check")
    matrix_by_id = _indexed(matrix, "id", "matrix")
    if set(by_id) != set(matrix_by_id):
        raise ValueError("matrix_check_ids_differ")
    rows = []
    for old in matrix:
        check_id = old["id"]
        check = by_id[check_id]
        executor = check.get("executor") or ""
        judge = (check.get("judge") or {}).get("plugin", "")
        real = bool(executor and f"{executor}:real" in executor_keys)
        judging = bool(judge and judge in judge_keys)
        policy_keys = [p["key"] for p in check.get("policy_params", [])]
        family = old["executor_kind"].split(" (")[0]
        history = evidence.get(check_id, [])
        if real and judging:
            implementation = "REAL_EXECUTOR_AND_JUDGE_CONNECTED"
        elif executor or judge:
            implementation = "PARTIALLY_CONNECTED"
        elif policy_keys:
            implementation = "POLICY_REGISTERED_NO_REAL_EXECUTOR"
        else:
            implementation = "NO_REAL_EXECUTOR"
        if real:
            priority = "P0_CURRENT_DEFINITION_REVALIDATION" if check_id in {"REQ-01", "REQ-03", "REQ-09", "PLT-01", "PLT-04"} else "P1_OBSERVATION_GAP"
        else:
            priority = {"A": "P2_LOCAL_FEASIBILITY_REVIEW", "B": "P3_ADDITIONAL_INFRA", "C": "P4_ENTERPRISE_ENV"}.get(old["grade"], "ENVIRONMENT_REVIEW_REQUIRED")
        observations = [o["key"] for o in check.get("required_observations", [])]
        observations += check.get("required_observations_prose", [])
        policy = check.get("policy_prose") or {}
        remaining = old.get("remaining_scope", "")
        rows.append({
            "check_id": check_id, "area": check["area"], "question": check["question"],
            "pack_revision": str(pack_revision), "check_revision": str(check["revision"]),
            "retired": str(bool(check.get("retired"))).lower(), "policy_keys": "; ".join(policy_keys),
            "executor": executor, "judge": judge, "real_executor_registered": str(real).lower(),
            "judge_registered": str(judging).lower(), "implementation": implementation,
            "historical_grade": old["grade"], "grade_uncertainty": old["uncertain"],
            "experiment_group": GROUPS.get(family, "PLATFORM_CREDENTIAL_NETWORK"), "priority": priority,
            "windows_cpu_status": "HISTORICAL_LIMITED_PROFILE_ONLY" if history else "CURRENT_ENVIRONMENT_NOT_VERIFIED",
            "current_pack_actual_status": "DEFERRED_NOT_MEASURED",
            "required_observations": "; ".join(observations),
            "normal_case": check.get("normal_control") or "정상 대조의 정책·접수 조건을 먼저 확정",
            "violation_case": "; ".join(check.get("challenge_steps", [])) + " / 판정 근거: " + (check.get("fail_criteria") or "미정"),
            "missing_case": check.get("unknown_criteria") or "필수 관측을 누락시키고 미수집·실패 전파를 확인",
            "unsupported_case": str((check.get("applies_when") or {}).get("prose", "")) + " / " + str(check.get("remaining_setup") or "지원 환경·정책 범위를 확인"),
            "reuse_candidate": REUSE.get(family, "기존 수집을 대조한 뒤 부족한 관측·새 시험 절차만 추가"),
            "next_experiment": ("현행 정의의 정상·위반·미수집을 실제로 재확인: " if real else "지원 범위·필수 관측을 확정하고 실제 정상·위반·미수집 시험 구현: ") + check["question"],
            "prerequisites": "; ".join(v for v in (old.get("new_infra"), check.get("remaining_setup")) if v),
            "team_decisions": policy.get("enterprise_decisions") or policy.get("policy_to_decide") or "",
            "historical_verification": old.get("verification_status", ""),
            "historical_pack_revisions": ";".join(str(v) for v in sorted({h["pack"]["revision"] for h in history})),
            "historical_evidence_refs": json.dumps(history, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "remaining_scope": remaining,
        })
    return rows


def load_rows(root: Path = ROOT) -> list[dict[str, str]]:
    checks = []
    for path in sorted((root / "packs/PACK-BASELINE/checks").glob("*.yaml")):
        check = yaml.safe_load(path.read_text(encoding="utf-8"))
        if check["id"] != path.stem:
            raise ValueError("check_filename_id_mismatch")
        checks.append(check)
    with (root / "docs/automation_matrix.csv").open(encoding="utf-8", newline="") as stream:
        matrix = list(csv.DictReader(stream))
    pack = yaml.safe_load((root / "packs/PACK-BASELINE/pack.yaml").read_text(encoding="utf-8"))
    return build_rows(checks, matrix, pack_revision=pack["revision"],
                      executor_keys=registry_keys(root / "src/guard/executors/registry.py", "EXECUTORS"),
                      judge_keys=registry_keys(root / "src/guard/judges/registry.py", "JUDGES"),
                      evidence=historical_evidence(root))


def render_csv(rows: list[dict[str, str]]) -> str:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def render_markdown(rows: list[dict[str, str]]) -> str:
    real = sum(r["real_executor_registered"] == "true" and r["judge_registered"] == "true" for r in rows)
    policy = sum(bool(r["policy_keys"]) for r in rows)
    paths = sum(len(r["policy_keys"].split("; ")) for r in rows if r["policy_keys"])
    groups = Counter(r["experiment_group"] for r in rows)
    lines = ["# 체크리스트 구현·실험 추적", "", "이 문서와 CSV는 `tools/checklist_status.py`의 생성물이다. 수정할 정본은 팩·registry·기존 자동화표·명시된 선별 실험 기록이다.", "",
             f"현행 팩 revision {rows[0]['pack_revision'] if rows else '없음'}의 정의 {len(rows)}개, 정책 등록 {policy}점검/{paths}경로, 실제 실행·판정 연결 {real}개, 미연결 {len(rows)-real}개다. 연결 수는 실제 전체 분기 검증 완료 수가 아니다.", "",
             "- [분기별 전체 CSV](checklist_validation_status.csv): 현재 정의·필수 관측·정상/위반/미수집/지원 밖 계획·선행 조건·기업 결정·과거 EVD/RES를 연결한다.",
             "- 이번 구현 묶음은 사용자 지시에 따라 실제 vLLM 실증을 보류한다. `current_pack_actual_status=DEFERRED_NOT_MEASURED`이며, 과거 기록이나 단위 시험을 현행 팩의 새 실측으로 승격하지 않는다.",
             "- `historical_pack_revisions`와 EVD/RES는 구조화된 과거 CPU 선별 기록(팩17·18·19)에서만 읽는다. 팩21·22의 후속 두 이미지 결과는 원래 자동화표의 `historical_verification`·`remaining_scope`와 [실측 문서](UPDATE_VALIDATION.md)를 함께 따른다.",
             "- 이 선별 JSON은 독립 복원 가능한 원본 bundle 전체가 아니다. 추적된 EVD Ref만으로 현행 코드 재현·무결성 검증·기업 승인을 주장하지 않는다.",
             "- A/B/C는 과거 환경 가능성 분류다. Windows 지원 확정·현재 시험 완료·미지원의 비적용 판정으로 사용하지 않는다.",
             "- CSV의 모든 시험 계획은 아직 실행 결과가 아니다. 정상 기대값도 현행 계약의 증거 관문을 통과해야 하며, REQ-11은 출력 귀속 미검증 시 보안 UNKNOWN을 유지한다.", "",
             "| 실험 묶음 | 점검 수 |", "|---|---:|"]
    lines.extend(f"| {group} | {count} |" for group, count in sorted(groups.items()))
    lines += ["", "우선순위는 기존 5개 현행 정의 재확인(P0), 다른 자동 연결의 관측 공백(P1), 로컬 가능성 재검토(P2), 추가 인프라(P3), 기업 환경(P4)이다. 어려운 계측 하나가 독립적인 신규 점검을 모두 막지는 않는다.", "",
              "REQ-05는 최종 모델·alias·퇴역 방침, LIF-09는 대상의 부분 실패와 외부 접수 gate, OBS-04는 실제 생성·송신 관측을 먼저 확정한다. 기존 실행기 재사용 후보가 곧 해당 점검의 지원이나 실행 완료를 뜻하지 않는다.", "",
              "실험 완료 시 현재 팩·이미지 digest·모델·정책·분기별 EVD/RES·재현 명령·원본 RPT·플랫폼 계약 검증·미검증 범위를 별도 실험 기록으로 남긴 뒤 정본에 연결한다. 본 추적 도구는 엔진·네트워크·테스트를 실행하지 않는다.", "",
              "재생성: `uv run python -m tools.checklist_status`", "",
              "변경 확인: `uv run python -m tools.checklist_status --check`", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    rows = load_rows(args.root)
    outputs = {CSV_PATH: render_csv(rows), MD_PATH: render_markdown(rows)}
    for relative, value in outputs.items():
        path = args.root / relative
        expected = value.encode("utf-8")
        if args.check:
            if not path.exists() or path.read_bytes() != expected:
                print(f"OUT_OF_DATE {relative.as_posix()}")
                return 1
        else:
            path.write_bytes(expected)
    print(f"checks={len(rows)} real_connected={sum(r['implementation'] == 'REAL_EXECUTOR_AND_JUDGE_CONNECTED' for r in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
