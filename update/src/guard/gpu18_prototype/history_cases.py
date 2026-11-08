"""과거5 항목의 합성 데모. 과거 원자료·실측·복구를 재현한 자료가 아니다.

표준 라이브러리와 메모리 상태만 사용한다. 시각은 논리 시각이고 정책값과
해시는 EXAMPLE 입력이다. 코드 선택은 문자열 토큰이며 import/실행하지 않는다.
workdir의 파일 생성과 EVD/RES/RPT 발행은 호출자 소유다. 기존 원자료 미확인,
실제 FAIL/UNKNOWN 및 실증 수량은 이 모듈의 모의 결과로 변경하지 않는다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


CHECK_IDS: tuple[str, ...] = ("LIF-08", "LIF-12", "LIF-16", "OBS-02", "SUP-08")


def _digest(label: str) -> str:
    return hashlib.sha256(("EXAMPLE-simulation:" + label).encode()).hexdigest()


_START = "2000-01-01T00:00:00Z"
_END = "2000-01-01T00:01:00Z"
_FAULTS = ("exception", "death", "delay", "communication_stall")

POLICY_VALUES: dict = {
    "LIF-08": {
        "rank_environment_sha256": _digest("rank-environment"),
        "rank_target_sha256": _digest("rank-target"),
        "rank_prepared_sha256": _digest("rank-prepared"),
        "rank_validity_start": _START,
        "rank_validity_end": _END,
        "rank_executor_backend": "EXAMPLE-simulation",
        "rank_expected_ranks": ["0", "1"],
        "rank_output_rank": 0,
        **{f"rank_{fault}_{kind}_ms": limit
           for fault in _FAULTS for kind, limit in (("detection", 10), ("notification", 20))},
    },
    "LIF-12": {
        "retry_environment_sha256": _digest("retry-environment"),
        "retry_target_sha256": _digest("retry-target"),
        "retry_scope_sha256": _digest("retry-scope"),
        "retry_policy_sha256": _digest("retry:allow;max_attempts=2;overlap=0;cost_units=8"),
        "retry_record_source_sha256": _digest("retry-record-source"),
        "retry_session": "EXAMPLE-simulation-retry",
        "retry_validity_start": _START,
        "retry_validity_end": _END,
        "retry_policy_mode": "allow",
        "retry_max_record_delay_ms": 10,
    },
    "LIF-16": {
        "transition_inputs_sha256": _digest("restore-inputs"),
        "transition_before_at": _START,
        "transition_during_at": "2000-01-01T00:00:10Z",
        "transition_after_at": "2000-01-01T00:00:20Z",
        "transition_recovered_at": "2000-01-01T00:00:30Z",
        "transition_before_generation": "EXAMPLE-before",
        "transition_during_generation": "EXAMPLE-isolated-copy",
        "transition_after_generation": "EXAMPLE-after",
        "transition_recovered_generation": "EXAMPLE-recovered",
    },
    "OBS-02": {
        "observation_state_environment_sha256": _digest("state-environment"),
        "observation_state_validity_start": _START,
        "observation_state_validity_end": _END,
        "observation_state_required_components": ["worker", "task", "collector"],
        "identity_prepared_sha256": _digest("state-identity-prepared"),
        "identity_record_session": "EXAMPLE-simulation-state",
        "identity_record_source_sha256": _digest("state-record-source"),
        "profiler_prepared_sha256": _digest("state-profiler-prepared"),
        "profiler_backend_kind": "EXAMPLE-simulation",
        "profiler_required_ranks": ["0"],
        "identity_target_sha256": _digest("state-identity-target"),
        "profiler_target_sha256": _digest("state-profiler-target"),
    },
    "SUP-08": {
        "code_selection_fields": ["code_revision"],
        "code_selection_allowed": False,
        "rejection_codes": ["400", "403"],
    },
}

INTEGRATION_REQUIRED: dict[str, str] = {
    "LIF-08": "과거 CPU 원 archive/argv/launcher/receipt/Store·정확Ref는 미확인. 실제 executor/topology/rank별 신원·fault 발생→감지→상위 통지·요청 결과·복구 생산자, 채택 기한과 별도 TP2 승인/환경이 필요하다. 이 모형은 GPU/TP 실측이 아니다.",
    "LIF-12": "과거 원 archive/명령/receipt/Store·정확Ref는 미확인. 실제 자동 retry 주체·조건·원/새 attempt·잔존 작업·중복/누적 비용·정책 한도·종결 직접 기록이 필요하다. LIF10/REQ12 지원으로 LIF12 지원을 가정하지 않는다.",
    "LIF-16": "과거 원 archive/명령/receipt/Store·정확Ref는 미확인. 실제 백업/설정/자격참조/volume/Gateway의 승인 대상·파일 소비·검증/접수 gate·분리 세대·잔존 참조·기한 내 복구가 필요하다. 선택 snapshot/CRIU/GPU 경로는 이 기본 메모리 모형에 포함되지 않는다.",
    "OBS-02": "과거 CPU 원 archive/명령/receipt/Store·정확Ref는 미확인. 동일 실행 세대의 API/로그 의미와 worker/작업/backend 독립 상태·시각·인과·실제 수집 종료가 필요하다. 실제 profiler 활성 경로와 다음 시작은 별도 원기록으로 대조해야 한다.",
    "SUP-08": "보고된 Mac ZIP3/tgz2의 실물·manifest/receipt/실행source·Store/전체Ref는 미확인. 실제 지원 요청필드·허용정책·진짜 model/code commit·Hub/cache provenance·최종 module/import/worker 소비 직접 생산자가 필요하다. 문자열 선택 토큰은 실제 import 증거가 아니다.",
}


class _Trace:
    def __init__(self, check_id: str, scenario: str) -> None:
        self.check_id = check_id
        self.scenario = scenario
        self.events: list[dict] = []

    def emit(self, kind: str, **values: object) -> dict:
        event = {
            "simulation": True, "data_origin": "EXAMPLE", "policy_status": "DRAFT",
            "official": False, "check_id": self.check_id, "scenario": self.scenario,
            "sequence": len(self.events), "kind": kind, **values,
        }
        self.events.append(event)
        return event


def _observed(value: object) -> dict:
    return {"collected": True, "value": value}


def _activation(trace: _Trace, violations: list[dict], missing: list[str], normal: bool) -> dict:
    observations = {
        "activation_steps": _observed(list(trace.events)),
        "activation_violations": _observed(violations),
        "activation_missing": _observed(missing),
        "activation_scope_complete": _observed(not missing),
    }
    if missing:
        # 실행 실패를 가장하지 않고 judge 입력의 핵심 관측 누락을 명시한다.
        observations["activation_scope_complete"] = {"collected": False, "value": None}
    return {
        "observations": observations,
        "normal_control": {"attempted": True, "succeeded": normal,
                           "note": "EXAMPLE 합성 상태의 정상 대조; 실제 엔진/과거 원자료 아님"},
        "events": trace.events,
    }


def _rank_failure(trace: _Trace) -> dict:
    policy = POLICY_VALUES[trace.check_id]
    # 정상 producer의 rank별 계산 완료를 output-rank consumer가 집계한다.
    completed = {rank: "ready" for rank in policy["rank_expected_ranks"]}
    rank_outputs = {}
    for rank in completed:
        completed[rank] = "running"
        trace.emit("rank-normal-dispatched", rank=rank, state=completed[rank])
        # 실제 계산 대신 무해한 정수 작업 단위를 소비하는 합성 worker다.
        rank_outputs[rank] = sum(range(int(rank) + 2))
        completed[rank] = "completed"
        trace.emit("rank-normal-work-completed", rank=rank, units=rank_outputs[rank], state=completed[rank])
    output_rank = str(policy["rank_output_rank"])
    normal = output_rank in completed and all(v == "completed" for v in completed.values())
    trace.emit("rank-normal-aggregate", ranks=completed, output_rank=output_rank, returned=normal)
    violations, missing = [], []
    for index, fault in enumerate(_FAULTS):
        fault_at = index * 100
        worker = {"rank": "1", "state": fault, "fault_at_ms": fault_at}
        trace.emit("rank-fault-produced", **worker)
        detected_at = fault_at + 4
        # 통제 오류는 실제 신호/프로세스 조작 없이 통지 지연 상태만 만든다.
        notification_at = fault_at + (policy[f"rank_{fault}_notification_ms"] + 1
                                     if trace.scenario == "violation" and fault == "communication_stall" else 8)
        if trace.scenario == "missing" and fault == "death":
            missing.append("death_detection_and_parent_notification")
            trace.emit("sensor-gap", sensor=missing[-1], attempted=True)
        else:
            sample = trace.emit("rank-supervisor-observed", fault=fault, rank=worker["rank"],
                                detected_at_ms=detected_at, notified_at_ms=notification_at,
                                fault_at_ms=fault_at, request_result="failed")
            for key, elapsed, limit in (
                ("detection_deadline", detected_at - fault_at, policy[f"rank_{fault}_detection_ms"]),
                ("notification_deadline", notification_at - fault_at, policy[f"rank_{fault}_notification_ms"]),
            ):
                if elapsed > limit:
                    violations.append({"fault": fault, "reason": key, "elapsed_ms": elapsed,
                                       "limit_ms": limit, "event_sequence": sample["sequence"]})
        worker["state"] = "ready"
        trace.emit("rank-recovery", rank=worker["rank"], state=worker["state"])
    return _activation(trace, violations, missing, normal)


def _retry(trace: _Trace) -> dict:
    # 비용 단위는 합성 스케줄러 tick이며 실제 돈/시간/정책값이 아니다.
    policy = {"max_attempts": 2, "max_overlap_ticks": 0, "max_cost_units": 8}
    trace.emit("retry-example-budget", **policy)
    normal_attempt = {"active": True, "output_count": 0}
    normal_attempt.update(active=False, output_count=1)
    normal = not normal_attempt["active"] and normal_attempt["output_count"] == 1
    trace.emit("retry-normal-call-completed", attempts=1, **normal_attempt)
    attempts = [{"id": "EXAMPLE-original", "active": True, "cost_units": 4}]
    trace.emit("attempt-timeout", attempt=dict(attempts[0]), trigger="automatic_timeout")
    bypass_cancel_barrier = trace.scenario == "violation"
    if not bypass_cancel_barrier:
        attempts[0]["active"] = False
        trace.emit("previous-attempt-cancelled", attempt=dict(attempts[0]))
    previous_active_at_retry = attempts[0]["active"]
    attempts.append({"id": "EXAMPLE-retry", "active": True, "cost_units": 0})
    overlap_ticks = 0
    for tick in range(3):
        active = [a for a in attempts if a["active"]]
        overlap_ticks += int(len(active) > 1)
        for attempt in active:
            attempt["cost_units"] += 1
        trace.emit("scheduler-tick", logical_tick=tick, attempts=[dict(a) for a in attempts])
    total_cost = sum(a["cost_units"] for a in attempts)
    violations, missing = [], []
    if trace.scenario == "missing":
        missing.append("automatic_retry_attempt_cost_and_overlap_record")
        trace.emit("sensor-gap", sensor=missing[-1], attempted=True)
    else:
        sample = trace.emit("retry-accounting-consumed", previous_active_at_retry=previous_active_at_retry,
                            attempt_count=len(attempts), overlap_ticks=overlap_ticks, total_cost_units=total_cost)
        for key, actual, limit in (("attempt_count", len(attempts), policy["max_attempts"]),
                                   ("overlap_ticks", overlap_ticks, policy["max_overlap_ticks"]),
                                   ("total_cost_units", total_cost, policy["max_cost_units"])):
            if actual > limit:
                violations.append({"reason": key, "actual": actual, "limit": limit,
                                   "event_sequence": sample["sequence"]})
    for attempt in attempts:
        attempt["active"] = False
    trace.emit("attempts-cleaned", attempts=[dict(a) for a in attempts])
    return _activation(trace, violations, missing, normal)


class _RestoreConsumer:
    def __init__(self, approved: dict[str, str]) -> None:
        self.approved = approved
        self.installed: dict[str, str] = {}
        self.gate_open = False
        self.stale_references = ["EXAMPLE-old-generation"]

    def apply(self, candidate: dict[str, str], *, bypass: bool = False) -> dict:
        matched = candidate == self.approved
        self.gate_open = False
        accepted = matched or bypass
        if accepted:
            self.installed = dict(candidate)
            if matched:
                self.stale_references.clear()
            self.gate_open = True
        return {"approved_match": matched, "accepted": accepted, "gate_open": self.gate_open,
                "installed": dict(self.installed), "stale_references": list(self.stale_references)}


def _restore(trace: _Trace) -> dict:
    approved = {"model": _digest("inert-model-token"), "config": _digest("inert-config-token")}
    consumer = _RestoreConsumer(approved)
    before = consumer.apply(approved)
    trace.emit("restore-normal-before", **before)
    isolated = dict(approved)
    isolated["config"] = _digest("isolated-copy-config-change")
    trace.emit("restore-copy-produced", approved=dict(approved), candidate=dict(isolated))
    consumer.stale_references = ["EXAMPLE-isolated-copy"]
    result = consumer.apply(isolated, bypass=trace.scenario == "violation")
    violations, missing = [], []
    if trace.scenario == "missing":
        missing.append("restore_actual_consumption_gate_and_residual_state")
        trace.emit("sensor-gap", sensor=missing[-1], attempted=True)
    else:
        sample = trace.emit("restore-consumer-inspected", **result)
        if not result["approved_match"] and result["accepted"]:
            violations.append({"reason": "unapproved_restore_consumed", "event_sequence": sample["sequence"]})
        if not result["approved_match"] and result["gate_open"]:
            violations.append({"reason": "service_before_approval", "event_sequence": sample["sequence"]})
    recovered = consumer.apply(approved)
    trace.emit("restore-approved-recovery", **recovered)
    normal = all(r["approved_match"] and r["accepted"] and r["gate_open"]
                 and not r["stale_references"] for r in (before, recovered))
    return _activation(trace, violations, missing, normal)


def _reported_state(state: dict[str, str]) -> str:
    if state["worker"] == "failed" or state["task"] == "failed":
        return "failed"
    if state["worker"] == "stopped" and state["collector"] == "stopped":
        return "stopped"
    if state["worker"] == "stopping" or state["task"] == "terminated":
        return "stopping"
    if state["task"] == "running":
        return "running"
    if state["worker"] == "ready":
        return "ready"
    return "preparing"


def _observation_state(trace: _Trace) -> dict:
    state = {"worker": "preparing", "task": "idle", "collector": "idle"}
    violations, missing = [], []
    # 합성 producer의 정상 작업과 수집 종료를 독립 상태 표본으로 비교한다.
    state.update(worker="ready", task="completed", collector="stopped")
    normal = state["task"] == "completed" and state["collector"] == "stopped"
    trace.emit("normal-state-completed", actual=dict(state), reported="completed")
    transitions = (
        {"worker": "preparing", "task": "idle", "collector": "idle"},
        {"worker": "ready"}, {"task": "running", "collector": "active"},
        {"worker": "failed", "task": "failed"},
        {"worker": "stopping", "task": "terminated"},
        {"worker": "stopped", "collector": "stopped"},
    )
    for transition in transitions:
        state.update(transition)
        produced_report = _reported_state(state)
        if trace.scenario == "violation" and state["worker"] == "stopping":
            produced_report = "stopped"  # 통제 오류: 종료 요청을 수집 종료로 조기 기록.
        trace.emit("state-report-produced", reported=produced_report)
        if trace.scenario == "missing" and state["worker"] == "stopping":
            missing.append("independent_worker_task_collector_state_at_stop")
            trace.emit("sensor-gap", sensor=missing[-1], attempted=True)
            continue
        # 실제 collector 상태가 active인 동안 stopped 보고를 받아들이지 않는다.
        observed_actual = dict(state)
        sample = trace.emit("independent-state-sample", actual=observed_actual, reported=produced_report)
        observed_meaning = _reported_state(observed_actual)
        if produced_report != observed_meaning:
            violations.append({"reason": "reported_state_differs_from_components",
                               "reported": produced_report, "observed": observed_meaning,
                               "event_sequence": sample["sequence"]})
    return _activation(trace, violations, missing, normal)


class _CodeConsumer:
    def __init__(self) -> None:
        self.selected: list[str] = []

    def select(self, token: str) -> str:
        # 토큰만 저장한다. 코드 로딩·import·subprocess·플러그인 실행 없음.
        self.selected.append(token)
        return "EXAMPLE-response:" + token


def _code_selection(trace: _Trace) -> dict:
    policy = POLICY_VALUES[trace.check_id]
    approved = "EXAMPLE-approved-code-v1"
    consumer = _CodeConsumer()
    normal_result = consumer.select(approved)
    normal = normal_result == "EXAMPLE-response:" + approved
    trace.emit("code-normal-consumed", selected=consumer.selected[-1], response=normal_result)
    trials, rejected, unverified, violations = [], [], [], []
    for field in policy["code_selection_fields"]:
        requested = "EXAMPLE-unapproved-code-v2"
        trace.emit("code-request-produced", field=field, requested=requested)
        deny = not policy["code_selection_allowed"]
        reject = deny and trace.scenario != "violation"
        if reject:
            outcome = policy["rejection_codes"][0]
            consumed = None
        else:
            consumer.select(requested)
            outcome = "200"
            consumed = consumer.selected[-1]
        if trace.scenario == "missing":
            unverified.append(field)
            trace.emit("sensor-gap", sensor="code_rejection_or_final_consumption", field=field, attempted=True)
        else:
            sample = trace.emit("code-consumer-inspected", field=field, outcome=outcome,
                                consumed=consumed, blocked_before_consumption=reject)
            trials.append(sample)
            if reject and consumed is None and outcome in policy["rejection_codes"]:
                rejected.append(field)
            if consumed is not None and (deny or consumed != approved):
                violations.append({"field": field, "consumed_token": consumed,
                                   "reason": "forbidden_override_selected", "event_sequence": sample["sequence"]})
    probe = "POST /v1/chat/completions"
    observations = {
        "code_selection_trials": _observed(trials),
        "code_selection_forbidden": _observed(not policy["code_selection_allowed"]),
        "code_selection_fields_planned": _observed(list(policy["code_selection_fields"])),
        "code_selection_rejected_fields": _observed(rejected),
        "code_selection_unverified_fields": _observed(unverified),
        "code_selection_confirmed_violations": _observed(violations),
        "code_selection_final_code_verified": _observed(not unverified),
        "functional_probes_planned": _observed([probe]),
        "functional_probe_results": _observed({probe: "pass" if normal else "fail"}),
    }
    if unverified:
        for key in ("code_selection_rejected_fields", "code_selection_confirmed_violations",
                    "code_selection_final_code_verified"):
            observations[key] = {"collected": False, "value": None}
    return {"observations": observations,
            "normal_control": {"attempted": True, "succeeded": normal,
                               "note": "EXAMPLE 문자열 코드 토큰의 정상 소비; 실제 코드 실행/과거 보고 재현 아님"},
            "events": trace.events}


def simulate(check_id: str, scenario: str, workdir: Path) -> dict:
    """합성 상태 전이에서 관측을 계산한다. 판정은 호출자의 기존 judge가 수행한다."""
    if check_id not in CHECK_IDS:
        raise ValueError(f"unsupported check_id: {check_id!r}")
    if scenario not in ("normal", "violation", "missing"):
        raise ValueError(f"unsupported scenario: {scenario!r}")
    # 이 모듈은 파일/모델/네트워크를 사용하지 않고 호출자가 산출물을 보존한다.
    del workdir
    trace = _Trace(check_id, scenario)
    trace.emit("simulation-start", historical_originals_verified=False)
    cases = {"LIF-08": _rank_failure, "LIF-12": _retry, "LIF-16": _restore,
             "OBS-02": _observation_state, "SUP-08": _code_selection}
    return cases[check_id](trace)
