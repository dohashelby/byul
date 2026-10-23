"""Actionable preparation advice, separate from readiness and engine evidence.

This layer projects existing checks; it does not measure capacity, authenticate
inputs, start Docker or change the policy. Exception payloads are never copied.
"""
from __future__ import annotations

import re

SCHEMA = "guard-preparation-guidance/1"


def _issue(code, stage, cause, action, *, side=None, check_id=None, status="FAILED", blocking=True):
    return {"code": code, "stage": stage, "side": side, "check_id": check_id,
            "status": status, "blocking": blocking, "cause": cause, "action": action}


def missing_input(name):
    name = name if name in {"pack", "policy", "config", "baseline", "candidate", "single"} else "input"
    return _issue("INPUT_MISSING", "input-files", f"{name} 입력이 필요합니다.",
        f"--{name}에 실제 준비한 입력 경로를 지정하세요. 예시 신원이나 후보 digest를 만들지 마세요.",
        side=name if name in {"baseline", "candidate", "single"} else None)


def read_inputs(paths, *, pack_loader, environment_loader, policy_loader, config_loader):
    """Load independent files even when another failed; defer dependent policy.

    Loader injection keeps the CLI's existing file/config semantics. No error
    text, file contents, credentials or user-controlled dictionary keys escape.
    """
    loaded, issues = {}, []

    def read(name, callback):
        try:
            loaded[name] = callback()
        except Exception as exc:
            code = "INPUT_MISSING" if isinstance(exc, FileNotFoundError) else (
                "INPUT_UNREADABLE" if isinstance(exc, OSError) else "INPUT_INVALID")
            issues.append(_issue(code, "input-files", f"{name} 입력을 읽거나 검증하지 못했습니다.",
                f"{name} 파일의 존재·읽기 권한·형식과 정확한 팩 참조를 확인하세요.",
                side=name if name in {"baseline", "candidate", "single"} else None))

    read("pack", lambda: pack_loader(paths["pack"]))
    for name in ("single", "baseline", "candidate"):
        if name in paths:
            read(name, lambda name=name: environment_loader(paths[name]))
    read("config", lambda: config_loader(paths["config"]))
    if "pack" in loaded:
        read("policy", lambda: policy_loader(paths["policy"], loaded["pack"]))
    else:
        issues.append(_issue("POLICY_NOT_CHECKED", "input-files",
            "팩이 유효하지 않아 정책의 팩 참조와 선언을 검사하지 못했습니다.",
            "팩 입력을 먼저 고친 뒤 같은 정책으로 준비 검사를 다시 실행하세요.", status="UNKNOWN"))
    return loaded, issues


def contract_issue(exc):
    """Classify fixed validator messages without exposing their arguments."""
    text = str(exc).lower() if type(exc) is ValueError else ""
    rules = (
        (("inventory",), "MODEL_INVENTORY", "model", "모델 inventory의 입력 또는 일치 조건이 충족되지 않았습니다.",
         "정확한 로컬 snapshot의 전체 inventory를 계산하고 두 ENV의 모델·revision·해시를 대조하세요."),
        (("snapshot", "offline hf"), "MODEL_SNAPSHOT", "model", "준비된 로컬 모델 snapshot을 확인하지 못했습니다.",
         "실제 snapshot 경로·파일·layout을 확인하세요. 도구는 모델을 내려받지 않습니다."),
        (("distinct image", "different image", "image-pair"), "IMAGE_PAIR", "images", "서로 다른 두 이미지 digest가 필요합니다.",
         "다른 태그로 같은 이미지를 재사용하지 말고 실제 후보 이미지와 후보 ENV를 준비하세요."),
        (("pinned image", "repository@sha256", "engine version"), "IMAGE_IDENTITY", "images", "엔진 버전 또는 pinned 이미지 입력이 부족합니다.",
         "실제 로컬 이미지의 버전과 repository@sha256를 확인해 ENV에 입력하세요."),
        (("cpu profile", "cpu/runtime", "gpu_count", "cpu-bounded"), "CPU_PROFILE", "hardware", "명시적 CPU 프로파일 조건이 충족되지 않았습니다.",
         "Linux CPU·아키텍처·모델·kernel·threads와 gpu_count=0을 확인하고 양쪽 실행 조건을 맞추세요."),
        (("execution conditions", "model identity"), "COMPARISON_CONDITIONS", "comparison", "양쪽 의미 있는 실행 조건이 다릅니다.",
         "모델·fixture·보안·자원 조건을 고정하세요. 프로파일 변경 진단을 이미지 업데이트로 수락하지 않습니다."),
        (("five minimum", "scope must", "selected no checks"), "POLICY_SCOPE", "selection", "이 진입점의 점검 선정 조건이 충족되지 않았습니다.",
         "최소 5점검 실행은 해당 범위만 선택하고 추가 점검은 별도 실행하세요. 기업 정책값을 축소하지 마세요."),
        (("policy", "selection gaps", "real executor"), "POLICY_OR_EXECUTOR", "selection", "선정·정책 또는 real 실행기 조건이 충족되지 않았습니다.",
         "필수 정책값·선정 공백·명시적 real 설정을 확인하세요. fake로 대체하지 마세요."),
    )
    for needles, code, stage, cause, action in rules:
        if any(needle in text for needle in needles):
            return _issue(code, stage, cause, action)
    return _issue("INPUT_CONTRACT", "configuration", "실행 입력의 의미 검증을 완료하지 못했습니다.",
                  "설정 형식·지원 프로파일과 정책을 확인하고 준비 검사를 다시 실행하세요.")


def annotate(data, *, input_issues=()):
    """Add stable guidance without changing ready/checks/check_support semantics."""
    issues = [dict(row) for row in input_issues]
    for row in data.get("checks", []):
        if row.get("ok") is True:
            continue
        name = row.get("check", "")
        if not isinstance(name, str):
            continue
        prefix, _, suffix = name.partition(":")
        side = suffix if suffix in {"baseline", "candidate", "single"} else None
        if prefix == "docker":
            issue = _issue("DOCKER_UNAVAILABLE", "docker", "로컬 Linux Docker 준비 상태를 확인하지 못했습니다.",
                "Docker Desktop의 Linux 컨테이너·WSL2 연동과 접속 권한을 확인하세요. 자동 시작·설정 변경은 하지 않습니다.")
        elif prefix in {"image", "entrypoint"}:
            issue = _issue("LOCAL_IMAGE_REQUIREMENTS", "images", "로컬 이미지의 존재·digest·아키텍처 또는 entrypoint 조건이 충족되지 않았습니다.",
                "해당 이미지와 보조 이미지를 사전 준비하고 daemon 아키텍처와 일치하는지 확인하세요. 자동 pull은 하지 않습니다.")
        elif prefix == "image-pair":
            issue = contract_issue(ValueError("image-pair"))
        elif prefix == "hardware":
            issue = _issue("HARDWARE_PROFILE", "hardware", "ENV의 실행 프로파일과 Docker 아키텍처 조건이 충족되지 않았습니다.",
                "해당 side의 실제 CPU·아키텍처를 확인하고 ENV를 바로잡으세요. GPU/driver 미확인을 CPU로 간주하지 마세요.", side=side)
        elif prefix == "resources":
            issue = _issue("DOCKER_CAPACITY_REQUIREMENTS", "resources", "Docker의 총 CPU/RAM이 실행 제한을 충족하는지 확인되지 않았습니다.",
                "Docker Desktop/WSL2 할당과 실행 제한을 확인하세요. 기존 컨테이너를 임의 중지하지 마세요.",
                side=side, status="UNKNOWN" if side is None else "FAILED")
        elif prefix == "inputs":
            issue = contract_issue(ValueError(row.get("detail", "") if isinstance(row.get("detail"), str) else ""))
        elif prefix == "input-files" and issues:
            continue
        else:
            issue = _issue("PREPARATION_INCOMPLETE", "input-files", "준비 검사 중 확인되지 않은 조건이 있습니다.",
                "입력 파일·지원 프로파일을 확인하고 같은 준비 명령을 다시 실행하세요.")
        issues.append(issue)
    for row in data.get("check_support", []):
        if row.get("status") == "SUPPORTED":
            continue
        check_id = row.get("check_id")
        if not isinstance(check_id, str) or re.fullmatch(r"(?:REQ|PLT|SUP|LIF|OBS)-\d{2}", check_id) is None:
            check_id = None
        side = row.get("side") if row.get("side") in {"baseline", "candidate", "single"} else None
        known = row.get("status") == "UNSUPPORTED"
        issues.append(_issue("CHECK_UNSUPPORTED" if known else "CHECK_SUPPORT_UNKNOWN", "check-support",
            "현재 제한 프로파일에서 해당 점검을 지원하지 않습니다." if known else "해당 점검의 지원 여부가 확인되지 않았습니다.",
            "원래 정책과 미지원 범위를 보존하고 지원 가능한 프로파일 또는 별도 증거 경로를 검토하세요.",
            side=side, check_id=check_id, status="FAILED" if known else "UNKNOWN", blocking=False))
    if input_issues:
        issues.append(_issue("HOST_NOT_CHECKED", "host", "입력 조건 오류로 호스트 검사를 수행하지 않았습니다.",
            "입력을 고친 뒤 준비 검사를 다시 실행하세요.", status="UNKNOWN"))
    issues.append(_issue("FREE_CAPACITY_UNMEASURED", "resources",
        "현재 여유 메모리·swap·다른 컨테이너의 사용량과 모델 적재 성공은 계측하지 않았습니다.",
        "총 메모리를 여유 메모리로 간주하지 말고 실증 전 자원 사용을 확인하세요.", status="UNKNOWN", blocking=False))
    ready = data.get("ready") is True
    guidance = {"schema": SCHEMA, "scope": "preparation-only", "issues": issues,
        "summary": "입력·호스트 준비 검사 통과. 점검 지원·실제 기동·판정은 별도 확인이 필요합니다." if ready
                   else "준비 조건이 충족되지 않았습니다. issues의 원인과 다음 조치를 확인하세요.",
        "engine_started": False, "downloads_performed": False,
        "limitations": ["준비 결과는 실제 엔진 증거·보안 PASS·배포 승인이 아닙니다.",
                        "의미 검증의 선행 조건이 실패하면 후속 조건은 미검증입니다. 모든 오류를 검사한 것으로 보지 않습니다."]}
    return {**data, "guidance": guidance}


def input_failure(issues):
    return annotate({"ready": False, "checks": [{"check": "input-files", "ok": False,
        "detail": "input files are missing, unreadable or invalid; see structured guidance"}],
        "check_support": []}, input_issues=issues)


class PreparationInputError(ValueError):
    def __init__(self, guidance):
        super().__init__("preparation inputs are incomplete; see structured guidance")
        self.guidance = guidance
