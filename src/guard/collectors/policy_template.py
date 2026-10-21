"""팩 선언으로 빈 POL 양식을 만든다. ENV·Store·실행기에는 접근하지 않는다."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import yaml

from guard.models import Pack


def _descriptions(pack: Pack) -> dict:
    """설명은 선택 자료다. 정확한 팩 Ref가 같을 때만 보존한 설명을 사용한다."""
    try:
        data = json.loads(files("guard.collectors").joinpath("policy_template_metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if data.get("pack") != pack.ref().model_dump(mode="json"):
        return {}
    return data.get("parameters", {})


def _comment(label: str, value: str, indent: str = "  ") -> str:
    # 팩의 여러 줄 설명도 모두 주석으로 유지한다. YAML 데이터로 삽입하지 않는다.
    lines = str(value).splitlines() or ["미지정"]
    return "".join(f"{indent}# {label if i == 0 else '  '}: {line}\n" for i, line in enumerate(lines))


def build_policy_template(pack: Pack) -> str:
    """같은 팩에서 같은 UTF-8/LF 본문을 생성한다. 기업 값과 실행 ID를 만들지 않는다."""
    descriptions = _descriptions(pack)
    checks = sorted((c for c in pack.active_checks() if c.policy_params), key=lambda c: c.id)
    text = (
        "# guard 기업 정책 입력 양식 — DRAFT / 공식 아님\n"
        "# 미입력 파라미터 키는 생략했다. 주석은 실제 정책값이 아니다.\n"
        "# 입력할 점검의 {}를 지우고 그 아래에 기업이 결정한 키: 값을 들여쓰기한다.\n"
        "# null은 미입력이 아니며, [], false, 0도 값이다. 기본값으로 복사하지 않는다.\n"
        "# scope의 빈 include는 팩 전체다. 양식의 점검 목록은 선정/자동화 완료 목록이 아니다.\n"
    )
    text += yaml.safe_dump(
        {"data_origin": "DECLARED", "status": "DRAFT",
         "pack": {"id": pack.manifest.pack_id, "revision": pack.manifest.revision}},
        allow_unicode=True, sort_keys=False,
    )
    text += "values:\n" if checks else "values: {}\n"
    for check in checks:
        keys = [p.key for p in check.policy_params]
        if len(keys) != len(set(keys)):
            raise ValueError(f"{check.id}: 중복 policy_params 키")
        text += f"  {json.dumps(check.id, ensure_ascii=False)}: {{}}\n"
        text += _comment("점검", check.question)
        prose = (check.model_extra or {}).get("policy_prose") or {}
        if isinstance(prose, dict) and prose.get("policy_to_decide"):
            text += _comment("점검 정책 문맥", prose["policy_to_decide"])
        for limitation in check.limitations:
            text += _comment("점검 한계", limitation)
        for param in check.policy_params:
            path = f"values.{check.id}.{param.key}"
            info = descriptions.get(path, {})
            if info.get("type") != param.type:
                info = {}
            text += _comment("파라미터", path)
            text += _comment("타입 / 필수", f"{param.type} / {'예' if param.required else '아니오'}")
            text += _comment("설명", info.get("description", "미지정 (팩에 개별 설명 없음)"))
            text += _comment("정책 결정 역할", info.get("decision_role", "미지정"))
            text += _comment("현재 소비 범위", info.get("consumer",
                f"미지정 (팩 연결: 실행기 {check.executor or '없음'}, 판정기 "
                f"{check.judge.plugin if check.judge else '없음'}; 파라미터별 소비 미확인)"))
            text += _comment("검증 기록", info.get("verification", "미지정 (이 명령은 시험을 실행하지 않음)"))
            if info.get("catalog_id"):
                text += _comment("설명 출처", f"Phase 1 카탈로그 {info['catalog_id']} / 입력 팩 Ref 일치")
            text += _comment("입력 키 (주석만 제공)", f"{json.dumps(param.key, ensure_ascii=False)}: <기업이 결정한 값>")
        text += "\n"
    text += yaml.safe_dump(
        {"scope": {"include": [], "exclude": []}, "exceptions": [],
         "confirmed_by": None, "confirmed_at": None},
        allow_unicode=True, sort_keys=False,
    )
    return text


def write_policy_template(pack: Pack, output: Path) -> None:
    """새 파일에만 쓴다. 기존 파일·symlink는 배타적 생성으로 보호한다."""
    output = Path(output)
    if pack.path is not None and output.resolve().is_relative_to(pack.path.resolve()):
        raise ValueError("출력 경로는 입력 팩 밖이어야 합니다 (팩 파일 목록·해시 보존)")
    content = build_policy_template(pack)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
