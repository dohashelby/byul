"""declared 판정기: 사람이 신고한 판정(declared_verdicts)을 RES로 옮긴다. 순수 함수(INV-14).

사람의 판정은 신고된 관측이다. 이 판정기는 신고된 PASS를 다음이 없으면 UNKNOWN으로 내린다:
- 첨부 증거 파일 1개 이상(EVD.artifacts)
- CheckDef.required_observations_prose의 각 항목에 대한 관측 설명(declared_observation_notes)
- CheckDef.policy_params에 선언된 필수 정책 키 (값의 충족 여부를 자동 판정하지 않음)
PASS 관문(정상 대조 성공·모든 챌린지 시도·구조화 필수 관측 수집, INV-11)은 판정 코어가 따로 적용한다.
신고된 FAIL은 그대로 둔다(INV-04).
"""

from __future__ import annotations

from guard.models import CheckDef, Evidence, Verdict

from .base import AXES, JudgeOutput

ALLOWED = {"PASS", "FAIL", "UNKNOWN"}


class DeclaredJudge:
    name = "declared"
    version = "0.2.0"

    def judge(self, check: CheckDef, evidence: Evidence, params: dict, policy_values: dict) -> JudgeOutput:
        obs = evidence.observations.get("declared_verdicts")
        if obs is None or not obs.collected or not isinstance(obs.value, dict):
            return JudgeOutput(verdicts={a: Verdict.UNKNOWN for a in AXES}, reasons=["no declared verdicts"],
                               missing_observations=["declared_verdicts"])
        op = evidence.execution.operator
        who = f"{op.by} at {op.at.isoformat()}" if op else "unknown operator"
        notes = evidence.observations.get("declared_observation_notes")
        answers = notes.value if notes is not None and notes.collected and isinstance(notes.value, dict) else {}
        unanswered = [item for item in check.required_observations_prose if not (answers.get(item) or "").strip()]
        missing_policy = [p.key for p in check.policy_params if p.required and p.key not in policy_values]
        verdicts, reasons = {}, [f"declared by {who} (self-reported, not authenticated)"]
        for axis in AXES:
            v = obs.value.get(axis) or "UNKNOWN"
            if v not in ALLOWED:
                reasons.append(f"{axis}: declared value {v!r} is not a verdict; treated as UNKNOWN")
                v = "UNKNOWN"
            if v == "PASS" and missing_policy:
                reasons.append(f"{axis}: declared PASS withheld: NO_POLICY_VALUE: required policy keys missing {missing_policy}")
                v = "UNKNOWN"
            if v == "PASS" and not evidence.artifacts:
                reasons.append(f"{axis}: declared PASS withheld: no attachment (at least one evidence file required)")
                v = "UNKNOWN"
            if v == "PASS" and unanswered:
                reasons.append(f"{axis}: declared PASS withheld: observation notes missing for {unanswered}")
                v = "UNKNOWN"
            verdicts[axis] = Verdict(v)
            reasons.append(f"{axis}: declared {obs.value.get(axis) or 'UNKNOWN'} → {v}")
        return JudgeOutput(verdicts=verdicts, reasons=reasons)
