"""declared 판정기: 사람이 신고한 판정(declared_verdicts)을 RES로 옮긴다. 순수 함수(INV-14).

사람의 판정은 신고된 관측이다. 이 판정기는 신고된 PASS를 다음이 없으면 UNKNOWN으로 내린다:
- 첨부 증거 파일 1개 이상(EVD.artifacts)
- CheckDef.required_observations_prose의 각 항목에 대한 관측 설명(declared_observation_notes)
- CheckDef.policy_params에 선언된 필수 정책 키
- SUP-01/SUP-02에 선언된 승인 기록 체계의 비공백 문자열(문맥이며 권한 증명이 아님)
- SUP-14/LIF-07의 선언된 필수 항목별 신고 관측·첨부 연결 및 제한적 충족 조건
- OBS-11의 프로파일·독립 근거·시간/조회 창·경보/종속 처리에 대한 제한적 충족 조건
PASS 관문(정상 대조 성공·모든 챌린지 시도·구조화 필수 관측 수집, INV-11)은 판정 코어가 따로 적용한다.
신고된 FAIL은 그대로 둔다(INV-04). 항목별 관측은 DECLARED이며 실측·신원 인증이 아니다.
"""

from __future__ import annotations

from guard.models import CheckDef, Evidence, Verdict

from .base import AXES, JudgeOutput
from .policy_items import evaluate
from .audit_collection import evaluate as evaluate_audit

ALLOWED = {"PASS", "FAIL", "UNKNOWN"}


class DeclaredJudge:
    name = "declared"
    version = "0.5.0"

    def judge(self, check: CheckDef, evidence: Evidence, params: dict, policy_values: dict) -> JudgeOutput:
        obs = evidence.observations.get("declared_verdicts")
        if obs is None or not obs.collected or not isinstance(obs.value, dict):
            return JudgeOutput(verdicts={a: Verdict.UNKNOWN for a in AXES}, reasons=["no declared verdicts"],
                               missing_observations=["declared_verdicts"])
        op = evidence.execution.operator
        who = f"{op.by} at {op.at.isoformat()}" if op else "unknown operator"
        notes = evidence.observations.get("declared_observation_notes")
        answers = notes.value if notes is not None and notes.collected and isinstance(notes.value, dict) else {}
        unanswered = [item for item in check.required_observations_prose
                      if not isinstance(answers.get(item), str) or not answers[item].strip()]
        missing_policy = [p.key for p in check.policy_params if p.required and p.key not in policy_values]
        verdicts, reasons = {}, [f"declared by {who} (self-reported, not authenticated)"]
        approval_context = check.id in ("SUP-01", "SUP-02") and any(
            p.key == "approval_record_system" and p.type == "str" and p.required for p in check.policy_params)
        context_missing = False
        if approval_context:
            value = policy_values.get("approval_record_system")
            context_missing = not isinstance(value, str) or not value.strip()
            code = "APPROVAL_RECORD_SYSTEM_UNRESOLVED" if context_missing else "APPROVAL_RECORD_SYSTEM_DECLARED"
            reasons.append(f"POLICY_CONTEXT: {code}; context only, approval permissions not automatically verified")
        item_result = evaluate(check, evidence, policy_values)
        if item_result is None:
            item_result = evaluate_audit(check, evidence, policy_values)
        if item_result:
            reasons.extend(item_result[1])
        for axis in AXES:
            v = obs.value.get(axis) or "UNKNOWN"
            if not isinstance(v, str) or v not in ALLOWED:
                reasons.append(f"{axis}: declared value {v!r} is not a verdict; treated as UNKNOWN")
                v = "UNKNOWN"
            if v == "PASS" and missing_policy:
                reasons.append(f"{axis}: declared PASS withheld: NO_POLICY_VALUE: required policy keys missing {missing_policy}")
                v = "UNKNOWN"
            if v == "PASS" and context_missing:
                reasons.append(f"{axis}: declared PASS withheld: POLICY_CONTEXT: approval_record_system is blank")
                v = "UNKNOWN"
            if v == "PASS" and not evidence.artifacts:
                reasons.append(f"{axis}: declared PASS withheld: no attachment (at least one evidence file required)")
                v = "UNKNOWN"
            if v == "PASS" and unanswered:
                reasons.append(f"{axis}: declared PASS withheld: observation notes missing for {unanswered}")
                v = "UNKNOWN"
            if item_result and v != "FAIL":
                ceiling = item_result[0][axis]
                if ceiling == "FAIL":
                    v = "FAIL"
                elif v == "PASS" and ceiling != "PASS":
                    v = "UNKNOWN"
            verdicts[axis] = Verdict(v)
            reasons.append(f"{axis}: declared {obs.value.get(axis) or 'UNKNOWN'} → {v}")
        return JudgeOutput(verdicts=verdicts, reasons=reasons,
                           missing_observations=item_result[2] if item_result else [])
