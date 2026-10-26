"""현재 팩의 점검별 빈 정책·분기 입력 목록을 준비한다. 엔진을 실행하지 않는다.

이 출력은 실행 결과나 실행 승인이 아니다. 기업값·ENV·이미지·모델·target은
생성하지 않는다. 실제 실행 명령은 필요한 입력과 별도 승인을 받은 뒤 사용한다.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

import yaml

from guard.collectors.policy_template import build_policy_template
from guard.models import Pack, load_pack
from tools.checklist_status import registry_keys


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
ORDER = ("REQ-01", "REQ-03", "REQ-09", "PLT-01", "PLT-04",
         "REQ-07", "REQ-10", "REQ-11", "REQ-13", "OBS-03", "OBS-09",
         "SUP-04", "SUP-03", "REQ-02")
BRANCHES = {
    "normal": "원문 정상 대조와 해당 프로파일의 관측·challenge 범위를 확인한다.",
    "negative": "원문 위반 기준을 확인할 근거를 준비한다. 부정 입력 자체가 FAIL은 아니다.",
    "missing": "필수 관측 누락과 UNKNOWN 처리를 확인하고 이미 확보한 독립 FAIL은 보존한다.",
    "unsupported": "지원 밖 범위를 대체·축소하지 않고 미검증 사유를 확인한다.",
}
# 입력·관측의 차이를 구분하는 사례명이다. 기대 판정표나 실행 결과가 아니다.
SPECIAL = {
    "REQ-09": {
        "default-source": "기본 원인 수집 설정의 지원 범위와 UNKNOWN을 보존한다.",
        "opt-in-source": "검토된 opt-in 원인 수집의 이미지·소스·요청별 연결을 확인한다.",
        "http-error-object": "HTTP 200 오류 객체를 실제 수용·위험한 소비로 해석하지 않는다.",
    },
    "PLT-04": {
        "candidate-omitted": "선택 후보 정책 생략을 필수 입력 오류로 바꾸지 않는다.",
        "candidate-empty": "명시 []의 부분 범위와 실제 후보 미시험을 기록한다.",
        "candidate-absent": "동일 worker 주체의 후보 부재를 관측한다.",
        "candidate-denied": "동일 worker 주체의 후보 접근 거부를 관측한다.",
        "candidate-readable": "본문을 읽지 않는 제한된 읽기 권한 근거와 source 신원을 확인한다.",
        "subject-mismatch": "UID/namespace/source 불일치로 후보 권한을 확정하지 않는다.",
    },
    "REQ-07": {
        "template-changed": "기동 argv·readonly bytes·전후 identity 변화와 cache 범위를 확인한다.",
        "override-allowed": "유효하나 현재 제한 프로파일 밖인 정책을 축소하지 않는다.",
    },
    "REQ-10": {"runner-missing": "API 수용과 최종 runner 소비를 구분한다."},
    "REQ-11": {
        "output-oracle-missing": "입력 projection만으로 출력 귀속 보안 PASS를 만들지 않는다.",
        "output-swapped": "요청/출력 교환을 독립 오라클 없이 확인했다고 주장하지 않는다.",
    },
    "OBS-03": {
        "dump-missing": "4xx와 실제 exception dump를 구분한다.",
        "copy-missing": "원본과 기업이 정한 필수 사본의 완결성을 각각 확인한다.",
        "truncated": "잘린 자료·수집 실패와 독립 위반 보존을 확인한다.",
    },
    "OBS-09": {"source-changed": "제한 native INFO 원본/파서/표시 source 신원을 확인한다."},
    "SUP-04": {
        "approved": "승인 membership 일치도 전체 공급물 PASS가 아니다.",
        "unapproved": "실제 image ID·RepoDigest·전후 소유 identity로 비승인 실행을 확인한다.",
        "identity-changed": "전후 instance 불일치는 UNKNOWN으로 남긴다.",
        "control-failed-after-proof": "확정한 비승인 이미지 근거를 후속 정상 대조 오류로 지우지 않는다.",
    },
    "SUP-03": {
        "approved": "승인 일치·API projection은 독립 소비 근거가 아니며 UNKNOWN을 유지한다.",
        "unapproved": "비승인 mount를 실제 소비 FAIL로 승격하지 않는다.",
        "cached-template": "현재 파일과 기동 loaded/cache 세대를 구분한다.",
        "restarted": "PID·경로 재사용과 다른 instance/model 세대를 구분한다.",
    },
    "REQ-02": {
        "auth-trace-missing": "인가 판단 path/주체·rewrite 사건이 없으면 외부 응답으로 대체하지 않는다.",
        "handler-trace-missing": "최종 handler 실행/거부의 신뢰 가능한 연결이 필요하다.",
        "client-id-swapped": "클라이언트 ID 위조·사건 교환을 수집자 상관 근거로 탐지한다.",
    },
}


def build_cases(pack: Pack, check_ids=None) -> tuple[dict, dict[str, bytes]]:
    """정의·빈 정책·CLI 형식만 만든다. ENV·실행 설정·Store·판정은 만들지 않는다."""
    selected = list(ORDER if check_ids is None else check_ids)
    if (not selected
            or any(type(cid) is not str or not re.fullmatch(r"[A-Z]{3}-[0-9]{2}", cid)
                   or cid not in pack.checks or pack.checks[cid].retired for cid in selected)
            or len(selected) != len(set(selected))):
        raise ValueError("CASE_CHECK_SELECTION_INVALID")
    keys = registry_keys(ROOT / "src/guard/executors/registry.py", "EXECUTORS")
    blank = yaml.safe_load(build_policy_template(pack))
    payloads, cases, checks = {}, [], []
    for cid in selected:
        check = pack.checks[cid]
        params = check.judge.params if check.judge is not None else {}
        real = f"{check.executor}:real" in keys if check.executor else False
        observation_only = (real and check.judge is not None and check.judge.plugin == "rule_table"
                            and not params.get("pass_when_all") and not params.get("fail_when_any"))
        status = "OBSERVATION_ONLY" if observation_only else "COLLECTOR_CONNECTED" if real else "CONTRACT_ONLY"
        policy = deepcopy(blank)
        policy["values"] = {cid: {}} if check.policy_params else {}
        policy["scope"] = {"include": [cid], "exclude": []}
        relative = f"{cid}/policy-template.yaml"
        payloads[relative] = yaml.safe_dump(policy, allow_unicode=True, sort_keys=False).encode("utf-8")
        checks.append({"check_id": cid, "check_revision": check.revision, "scope": status,
                       "policy_template": relative,
                       "policy_inputs_required": [{"key": p.key, "type": p.type, "required": p.required}
                                                  for p in check.policy_params],
                       "required_observations": [p.key for p in check.required_observations],
                       "source_observations": list(check.required_observations_prose),
                       "normal_control": check.normal_control,
                       "satisfied_if": check.satisfied_if,
                       "fail_criteria": check.fail_criteria,
                       "unknown_criteria": check.unknown_criteria,
                       "not_exercised": list(check.challenge_steps),
                       "limitations": list(check.limitations)})
        for branch, requirement in {**BRANCHES, **SPECIAL.get(cid, {})}.items():
            case_id = f"{cid}-{branch}"
            cases.append({
                "case_id": case_id, "check_id": cid, "branch": branch, "requirement": requirement,
                "policy_template": relative, "ready_to_execute": False,
                "pending_inputs": ["explicit_environment", "explicit_execution_config",
                                   "case_specific_policy_decision", "trusted_collection_scope",
                                   "separate_execution_authorization"],
                "verdict": None, "evidence_refs": [], "result_refs": [],
                "command_template": (None if not real else [
                    "uv", "run", "guard", "validate", "--pack", "<PACK>",
                    "--env", "<ENV>", "--policy", "<FILLED-CASE-POLICY>",
                    "--config", "<CFG>", "--store", f"<NEW-STORE-{case_id}>",
                    "--output", f"<NEW-OUTPUT-{case_id}>"]),
            })
    return {
        "format": "guard-checklist-case-plan/1", "data_origin": "EXAMPLE", "planning_only": True,
        "engine_executed": False, "execution_authorized": False, "pack_ref": pack.ref().model_dump(mode="json"),
        "checks": checks, "cases": cases,
        "limitations": [
            "Plans and blank policy templates are not EVD, RES, RPT, readiness or experiment results.",
            "No enterprise values, environment identity, image, model or execution configuration was invented.",
            "Fill one policy per case after a policy decision; the template is not an approved runtime policy.",
            "The fixed five-check CPU acceptance tools remain unchanged; extra checks use separate validation.",
            "Same image/model/profile cases share a startup only after state independence is established.",
            "Two-image comparison needs two different pinned images and comparable conditions.",
            "REQ-02 needs trusted authorization/rewrite/handler evidence; SUP-03 remains observation only.",
            "Current-pack actual trials remain deferred; EXAMPLE fixtures are not real-engine proof.",
        ],
    }, payloads


def prepare_cases(pack: Pack, output: Path, check_ids=None) -> dict:
    output = Path(output)
    resolved = output.resolve()
    if resolved.is_relative_to(REPO.resolve()) or (
            pack.path is not None and resolved.is_relative_to(pack.path.resolve())):
        raise ValueError("CASE_OUTPUT_MUST_BE_OUTSIDE_SOURCE")
    plan, payloads = build_cases(pack, check_ids)
    plan["files"] = [{"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                     for name, raw in sorted(payloads.items())]
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in payloads.items():
        destination = output / name
        destination.parent.mkdir(exist_ok=False)
        with destination.open("xb") as stream:
            stream.write(raw)
    with (output / "case-plan.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--checks", nargs="+")
    args = parser.parse_args(argv)
    try:
        try:
            pack = load_pack(args.pack)
        except KeyError as error:
            # 팩 checks 목록에 있으나 파일 목록·본문이 함께 없는 예상 입력 오류만 정규화한다.
            # 준비 로직의 다른 조회 오류는 프로그래밍 오류일 수 있으므로 숨기지 않는다.
            missing = error.args[0] if len(error.args) == 1 else None
            if not isinstance(missing, str) or not missing.startswith("checks/") or not missing.endswith(".yaml"):
                raise
            raise ValueError("CASE_PACK_CHECK_MISSING") from error
        plan = prepare_cases(pack, args.output, args.checks)
    except (OSError, ValueError, yaml.YAMLError):
        print("CASE_PREPARATION_FAILED", file=sys.stderr)
        return 2
    print(json.dumps({"prepared": True, "engine_executed": False, "execution_authorized": False,
                      "checks": len(plan["checks"]), "cases": len(plan["cases"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
