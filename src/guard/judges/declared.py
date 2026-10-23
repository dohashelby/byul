"""declared 판정기: 사람이 신고한 판정(declared_verdicts)을 RES로 옮긴다. 순수 함수(INV-14).

사람의 판정은 신고된 관측이다. 이 판정기는 신고된 PASS를 다음이 없으면 UNKNOWN으로 내린다:
- 첨부 증거 파일 1개 이상(EVD.artifacts)
- CheckDef.required_observations_prose의 각 항목에 대한 관측 설명(declared_observation_notes)
- CheckDef.policy_params에 선언된 필수 정책 키
- SUP-01/SUP-02에 선언된 승인 기록 체계의 비공백 문자열(문맥이며 권한 증명이 아님)
- SUP-14/LIF-07의 선언된 필수 항목별 신고 관측·첨부 연결 및 제한적 충족 조건
- OBS-11의 프로파일·독립 근거·시간/조회 창·경보/종속 처리에 대한 제한적 충족 조건
- OBS-04/05·OBS-07/08에 선언된 bool 정책의 해당 검토 분기(실제 활성 상태 증명 아님)
- M10 REQ-09 계약의 보안·기능 관측이 고정된 rule_table에서도 PASS 후보일 것
- M12 REQ-16의 필수 캐시 문맥별 수동 3case 계약이 해당 축에서 완결될 것
PASS 관문(정상 대조 성공·모든 챌린지 시도·구조화 필수 관측 수집, INV-11)은 판정 코어가 따로 적용한다.
신고된 FAIL은 그대로 둔다(INV-04). 항목별 관측은 DECLARED이며 실측·신원 인증이 아니다.
"""

from __future__ import annotations

from guard.models import CheckDef, Evidence, Verdict
from guard.input_policy import policy_context

from .base import AXES, JudgeOutput
from .policy_items import evaluate
from .audit_collection import evaluate as evaluate_audit
from .rule_table import RuleTableJudge
from . import manual_cache_context

ALLOWED = {"PASS", "FAIL", "UNKNOWN"}


def _branch_context(check: CheckDef, values: dict) -> tuple[bool, list[str]]:
    """A declared policy opens a review branch; it does not prove actual state."""
    branch = {"OBS-04": ("usage_allowed", False), "OBS-05": ("usage_allowed", True),
              "OBS-07": ("replay_allowed", True), "OBS-08": ("replay_allowed", True)}.get(check.id)
    if branch is None:
        return False, []
    key, expected = branch
    if not any(p.key == key and p.type == "bool" and p.required for p in check.policy_params):
        return False, []
    value = values.get(key)
    valid = type(value) is bool
    blocked = not valid or value is not expected
    shown = ("true" if value else "false") if valid else ("missing" if key not in values else "invalid")
    code = "POLICY_FLAG_UNRESOLVED" if not valid else ("POLICY_BRANCH_INACTIVE" if blocked else "POLICY_BRANCH_ACTIVE")
    limit = ("USAGE_FIELDS_DESTINATIONS_PERIOD_AND_ACTUAL_STATE_NOT_AUTOMATICALLY_VERIFIED"
             if key == "usage_allowed" else "REPLAY_CONSUMER_PERMISSIONS_PARSER_AND_ACTUAL_STATE_NOT_AUTOMATICALLY_VERIFIED")
    return blocked, [f"POLICY_CONTEXT: {check.id}.{key}={shown}; {code}; declared policy only, not proof of actual state",
                     f"POLICY_CONTEXT: {limit}; per-check scope only, shared-policy consistency not automatically verified"]


class DeclaredJudge:
    name = "declared"
    version = "0.9.0"

    def judge(self, check: CheckDef, evidence: Evidence, params: dict, policy_values: dict) -> JudgeOutput:
        input_context = policy_context(check, policy_values, evidence.observations)
        obs = evidence.observations.get("declared_verdicts")
        if obs is None or not obs.collected or not isinstance(obs.value, dict):
            return JudgeOutput(verdicts={a: Verdict.UNKNOWN for a in AXES}, reasons=["no declared verdicts"],
                               missing_observations=["declared_verdicts"])
        input_rules = None
        input_rule_issue = "unavailable"
        if input_context is not None and not input_context.blocked:
            if check.judge is not None and check.judge.plugin == "rule_table":
                try:
                    # Evaluate the pinned CheckDef directly. Calling the core
                    # would select this EVD's declared default_judge recursively.
                    input_rules = RuleTableJudge().judge(check, evidence, check.judge.params, policy_values)
                    input_rule_issue = "evaluated"
                except Exception:  # INV-04: a dependency error must not erase declared FAIL.
                    input_rule_issue = "evaluation_error"
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
        branch_blocked, branch_reasons = _branch_context(check, policy_values)
        reasons.extend(branch_reasons)
        if input_context is not None:
            reasons.extend(input_context.reasons)
        item_result = evaluate(check, evidence, policy_values)
        if item_result is None:
            item_result = evaluate_audit(check, evidence, policy_values)
        if item_result:
            reasons.extend(item_result[1])
        cache_result = None
        if manual_cache_context.contract_state(check) != "legacy":
            try:
                cache_result = manual_cache_context.evaluate(check, evidence, policy_values)
            except Exception:  # INV-04: dependency errors cannot erase a person's FAIL.
                cache_result = JudgeOutput(verdicts={}, missing_observations=[manual_cache_context.OBSERVATION],
                    reasons=["POLICY_CONTEXT: CACHE_CONTEXT_EVALUATION_ERROR; DECLARED PASS withheld"])
            if cache_result is not None:
                reasons.extend(cache_result.reasons)
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
            if v == "PASS" and branch_blocked:
                reasons.append(f"{axis}: declared PASS withheld: POLICY_CONTEXT: policy flag does not establish this review branch")
                v = "UNKNOWN"
            if v == "PASS" and input_context is not None and input_context.blocked:
                reasons.append(f"{axis}: declared PASS withheld: POLICY_CONTEXT: submitted input scope unresolved")
                v = "UNKNOWN"
            if v == "PASS" and input_context is not None and axis in ("security", "functional"):
                ceiling = input_rules.verdicts.get(axis, Verdict.UNKNOWN) if input_rules is not None else Verdict.UNKNOWN
                if ceiling != Verdict.PASS:
                    code = "OBSERVATION_CONFLICT" if ceiling == Verdict.FAIL else "RULE_UNRESOLVED"
                    reasons.append(f"POLICY_CONTEXT: REQ09_MANUAL_{code}; axis={axis}; "
                                   f"rule_table={ceiling.value}; {input_rule_issue}; DECLARED PASS withheld")
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
            if v == "PASS" and cache_result is not None and cache_result.verdicts.get(axis) != Verdict.PASS:
                reasons.append(f"POLICY_CONTEXT: CACHE_CONTEXT_PASS_WITHHELD; axis={axis}")
                v = "UNKNOWN"
            verdicts[axis] = Verdict(v)
            reasons.append(f"{axis}: declared {obs.value.get(axis) or 'UNKNOWN'} → {v}")
        return JudgeOutput(verdicts=verdicts, reasons=reasons,
                           missing_observations=sorted(set(item_result[2] if item_result else [])
                               | set(input_context.missing if input_context is not None else ())
                               | set(input_rules.missing_observations if input_rules is not None else ())
                               | set(cache_result.missing_observations if cache_result is not None else ())))
