"""GPU18 10개 항목의 EXAMPLE/DRAFT 전용 상태 모형.

실제 파일·socket·프로세스·모델을 열거나 실행하지 않는다. 이 모듈의
인증/소비/정상 대조는 모형 내부의 계약이며 실제 worker 증거가 아니다.
판정값은 반환하지 않는다. 기존 judge가 관측에서 판정한다. 기본 팩의
규칙 부재/ceiling은 보존하며 별도 모의 팩에만 아래 규칙을 적용한다.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path


CHECK_IDS: tuple[str, ...] = (
    "LIF-11", "SUP-03", "PLT-02", "LIF-06", "SUP-09",
    "SUP-10", "SUP-11", "REQ-09", "REQ-10", "SUP-06",
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_TEMPLATE = "SIMULATION template-v1: {message}"
_SUPPLY_POLICY = {
    **{key: _digest("SIMULATION:" + key) for key in (
        "supply_environment_sha256", "supply_normal_target_sha256",
        "supply_challenge_target_sha256", "supply_normal_prepared_sha256",
        "supply_challenge_prepared_sha256", "supply_record_source_sha256",
    )},
    "supply_validity_start": "2000-01-01T00:00:00Z",
    "supply_validity_end": "2000-01-01T00:01:00Z",
}
POLICY_VALUES: dict = {
    "LIF-11": {
        **{key: _digest("SIMULATION:" + key) for key in (
            "recovery_environment_sha256", "recovery_target_sha256",
            "recovery_prepared_sha256", "recovery_selection_sha256",
            "recovery_scenario_sha256",
        )},
        "recovery_validity_start": "2000-01-01T00:00:00Z",
        "recovery_validity_end": "2000-01-01T00:01:00Z",
        "recovery_branch": "simulation-selected-resources",
        "recovery_executor_backend": "simulation",
        "recovery_expected_ranks": ["SIM-rank0", "SIM-rank1"],
        "recovery_phase": "after-worker-failure",
        "recovery_deadline_basis": "simulation-monotonic-ticks",
        "recovery_required_absent_after": "2000-01-01T00:00:05Z",
        "recovery_max_clock_skew_ms": 0,
        "recovery_required_resource_kinds": ["worker", "socket", "shared_memory"],
    },
    "SUP-03": {"allowed_template_sha256": [_digest(_TEMPLATE)]},
    "PLT-02": {
        "protected_unix_paths": ["SIMULATION:/ipc/control"],
        "allowed_ipc_uids": ["SIM-uid-allowed"],
    },
    "LIF-06": {
        "transition_inputs_sha256": _digest("SIMULATION:layerwise-state"),
        **{f"transition_{phase}_at": f"2000-01-01T00:00:0{i}Z"
           for i, phase in enumerate(("before", "during", "after", "recovered"))},
        "transition_before_generation": "SIM-generation-A",
        "transition_during_generation": "SIM-generation-A",
        "transition_after_generation": "SIM-generation-B",
        "transition_recovered_generation": "SIM-generation-A",
    },
    **{cid: deepcopy(_SUPPLY_POLICY) for cid in ("SUP-09", "SUP-10", "SUP-11", "SUP-06")},
    "REQ-09": {
        "max_input_tokens": 4, "rejection_codes": ["400", "422"],
        "allowed_endpoints": ["POST /v1/completions"],
        "allowed_input_formats": ["token_ids"],
    },
    "REQ-10": {
        "allowed_sampling_options": ["temperature", "top_p", "min_tokens"],
        "rejection_codes": ["400", "422"],
    },
}

INTEGRATION_REQUIRED: dict[str, str] = {
    "LIF-11": "선정 backend/TP rank의 실패 전후 PID/start·socket/SHM 소유 inventory, "
              "정책 기한 안 실제 회수, 같은 환경 재기동·정상 요청, ENV/target/prepared/Ref 필요.",
    "SUP-03": "승인된 config/tokenizer/template·secondary source 전체 inventory와 "
              "독립 worker의 파일 bytes 선택·실제 소비·request 결합 및 승인 제외 challenge 필요. "
              "기본 PACK33은 관측 전용이며 이 모형의 PASS/FAIL 규칙은 모의 DRAFT 팩에만 적용.",
    "PLT-02": "실제 IPC 종류·namespace·inode/owner/peer 주체와 허용/비허용 프로세스의 "
              "연결·읽기/쓰기·명령 소비·정상 TP 요청 증거 필요. 모형은 실제 OS 권한 시험이 아님.",
    "LIF-06": "선정 live/LoRA/rolling producer의 실제 부분 로딩·storage 참조·fallback, "
              "진행 요청별 weights/cache/replica 세대·복구 정상 대조와 고정 전이 정책 필요.",
    "SUP-09": "실제 loader trust 설정·고정 공급물 코드 bytes/판본·선택/거부/소비 경계와 "
              "서명된 원세대 record 및 양 세션 ENV/target/prepared 필요. 모형은 코드를 실행하지 않음.",
    "SUP-10": "실제 선택한 plugin 집합·autoload 설정·entry point와 호출/미호출 producer 기록, "
              "기동 session/정상 대조·정책 결합 필요. 모형은 plugin을 import/실행하지 않음.",
    "SUP-11": "설치 Python 시작 옵션·환경/탐색 경로·실제 loader/module 소비 producer와 "
              "정상/변경 세션 원자료 필요. 모형의 isolated 상태는 실제 interpreter 시험이 아님.",
    "REQ-09": "실제 vocab/모델·허용 endpoint/token-array 정책, 원인별 거부 및 "
              "consumer 소비/피해 직접 계측·동시/후속 정상 대조와 정확 request/worker Ref 필요.",
    "REQ-10": "실제 runner/worker의 sampling 최종 소비·V2 활성 및 옵션별 거부 경계, "
              "동시/후속 정상 요청·backend/rank 결합 필요. 기본 팩 FAIL 규칙 부재는 유지.",
    "SUP-06": "실제 모델/양자화별 필수 tensor·rank/shard 배정과 load 반환/후처리, "
              "공유 weights 사용 시 publisher/model/generation/layout/payload 실제 소비 producer 필요.",
}

# 기본 PACK33을 덮지 않는다. 루트의 별도 PACK-GPU18-PROTOTYPE에만 적용.
PROTOTYPE_JUDGE_PARAMS: dict = {
    "SUP-03": {
        "pass_when_all": [
            {"obs": key, "op": "eq", "value": True} for key in (
                "template_identity_verified", "selected_template_approved",
                "input_projection_exact_match", "template_consumer_verified", "supply_scope_complete",
            )
        ],
        "fail_when_any": [{"obs": "selected_template_approved", "op": "eq", "value": False,
                           "when": {"obs": "template_consumer_verified", "op": "eq", "value": True}}],
    },
    "PLT-02": {
        "pass_when_all": [
            {"obs": "ipc_scope_complete", "op": "eq", "value": True},
            {"obs": "unix_ipc_trials", "op": "nonempty"},
            {"obs": "unix_ipc_violations", "op": "empty"},
        ],
        "fail_when_any": [{"obs": "unix_ipc_violations", "op": "nonempty"}],
    },
    "REQ-10": {
        "pass_when_all": [
            {"obs": "sampling_options_tested", "op": "eq", "value_policy": "allowed_sampling_options"},
            {"obs": "sampling_challenge_outcomes", "op": "nonempty"},
            {"obs": "sampling_challenge_outcomes", "op": "superset_of", "value_obs": "sampling_options_planned"},
            {"obs": "sampling_challenge_outcomes", "op": "all_in", "value_policy": "rejection_codes"},
            {"obs": "runner_consumption_verified", "op": "eq", "value": True},
        ],
        "fail_when_any": [{"obs": "sampling_accepted_options", "op": "nonempty"}],
    },
}


def _event(stage: str, **fields) -> dict:
    return {**fields, "stage": stage, "simulation": True,
            "data_origin": "EXAMPLE", "policy_status": "DRAFT", "official": False}


def _result(values: dict, events: list, baseline_ok: bool, missing: bool, key: str) -> dict:
    observations = {name: {"collected": True, "value": value} for name, value in values.items()}
    if missing:
        observations[key] = {"collected": False, "value": None}
        events.append(_event("collector_missing", key=key))
    return {"observations": observations,
            "normal_control": {"attempted": True, "succeeded": baseline_ok,
                               "note": "SIMULATION in-memory normal control; no real engine/worker"},
            "events": events}


def _recovery(fault: bool, policy: dict) -> tuple[dict, list, bool, str]:
    def recover(leak: bool) -> tuple[list, dict]:
        owned = {kind: set(policy["recovery_expected_ranks"])
                 for kind in policy["recovery_required_resource_kinds"]}
        steps = [_event("resources_owned", inventory={k: sorted(v) for k, v in owned.items()}, tick=0)]
        for kind in owned:
            if not (leak and kind == "shared_memory"):
                owned[kind].clear()
            steps.append(_event("cleanup", kind=kind, remaining=sorted(owned[kind]), tick=3))
        steps.append(_event("restart_attempt", succeeded=not any(owned.values()), tick=4))
        return steps, owned

    normal, baseline = recover(False)
    steps, residual = recover(fault)
    violations = ["SIMULATION:resource-retained:" + kind for kind, owners in residual.items() if owners]
    values = {"activation_steps": steps, "activation_violations": violations,
              "activation_missing": [],
              "activation_scope_complete": set(residual) == set(policy["recovery_required_resource_kinds"])}
    return values, normal + steps, not any(baseline.values()), "activation_scope_complete"


def _transition(fault: bool, policy: dict) -> tuple[dict, list, bool, str]:
    before = policy["transition_before_generation"]
    after = policy["transition_after_generation"]
    layers = [before, before]
    cache = before
    steps = []

    def consume(phase: str) -> None:
        steps.append(_event("request_consumed", phase=phase, layers=list(layers), cache=cache))

    consume("before")
    pending = [after, None]
    steps.append(_event("layerwise_stage", pending=list(pending), active=list(layers)))
    if fault:
        layers[0] = pending[0]  # 모형의 잘못된 부분 공개. 실제 weights 변경 없음.
    consume("during")
    pending[1] = after
    layers, cache = list(pending), after
    consume("after")
    # 실패한 다음 부분 갱신은 버리고 이전 승인 storage 세대로 복구한다.
    pending = ["C", None]
    steps.append(_event("partial_update_failed", discarded=list(pending), storage_reference="SIM-storage-A"))
    recovered = policy["transition_recovered_generation"]
    layers, cache = [recovered, recovered], recovered
    consume("recovered")
    requests = [s for s in steps if s["stage"] == "request_consumed"]
    violations = ["SIMULATION:mixed-generation:" + s["phase"] for s in requests
                  if len(set(s["layers"] + [s["cache"]])) != 1]
    values = {"activation_steps": steps, "activation_violations": violations,
              "activation_missing": [], "activation_scope_complete": len(requests) == 4}
    return values, steps, requests[0]["layers"] == [before, before], "activation_scope_complete"


def _template(fault: bool, policy: dict) -> tuple[dict, list, bool, str]:
    inventory = {"config": "SIM-config", "tokenizer": "SIM-tokenizer", "template": _TEMPLATE}
    control = inventory["template"].replace("{message}", "ping")
    selected = "SIMULATION unapproved-template: {message}" if fault else inventory["template"]
    digest = _digest(selected)
    approved = digest in policy["allowed_template_sha256"]
    consumed = approved or fault  # fault=모형 approval guard 해제. 실행 코드는 없음.
    rendered = selected.replace("{message}", "ping") if consumed else None
    events = [_event("inventory", hashes={k: _digest(v) for k, v in inventory.items()}),
              _event("template_selected", digest=digest, approved=approved),
              _event("template_consumer", consumed=consumed, rendered=rendered)]
    values = {"selected_template_sha256": digest, "template_identity_verified": _digest(selected) == digest,
              "selected_template_approved": approved, "input_projection_exact_match": rendered is not None and "ping" in rendered,
              "template_consumer_verified": consumed, "supply_scope_complete": len(inventory) == 3,
              "functional_probes_planned": ["POST /v1/chat/completions"],
              "functional_probe_results": {"POST /v1/chat/completions": "pass" if rendered else "fail"}}
    return values, events, "ping" in control, "template_consumer_verified"


def _ipc(fault: bool, policy: dict) -> tuple[dict, list, bool, str]:
    trials = []
    for subject in ("SIM-uid-allowed", "SIM-uid-denied"):
        authorized = subject in policy["allowed_ipc_uids"]
        connected = authorized or fault
        inbox = ["ping"] if connected else []
        delivered = inbox.pop(0) if inbox else None
        consumed = delivered is not None
        trials.append(_event("ipc_consumer", subject=subject, endpoint=policy["protected_unix_paths"][0],
                             authorized=authorized, connected=connected, command_consumed=consumed,
                             result="pong" if delivered == "ping" else None))
    violations = ["SIMULATION:unauthorized-command-consumed:" + row["subject"] for row in trials
                  if not row["authorized"] and row["command_consumed"]]
    values = {"unix_ipc_violations": violations, "unix_ipc_trials": trials,
              "ipc_scope_complete": len(trials) == 2, "functional_probes_planned": ["POST /v1/completions"],
              "functional_probe_results": {"POST /v1/completions": "pass" if trials[0]["result"] == "pong" else "fail"}}
    return values, trials, trials[0]["result"] == "pong", "ipc_scope_complete"


def _supply(check_id: str, fault: bool) -> tuple[dict, list, bool, str]:
    """독립 baseline/challenge record를 생산·MAC 검증·소비한다. 코드 실행 없음."""
    def session(role: str, bypass: bool) -> dict:
        if check_id == "SUP-06":
            assigned = {"SIM-rank0": ["tensor0"], "SIM-rank1": ["tensor1"]}
            available = {"tensor0", "tensor1"} if role == "normal" else {"tensor0"}
            matches = all(set(tensors) <= available for tensors in assigned.values())
            selected = {"assignments": assigned, "available": sorted(available)}
        elif check_id == "SUP-10":
            selected_name = "SIM-plugin-selected" if role == "normal" else "SIM-plugin-unselected"
            matches = selected_name == "SIM-plugin-selected"
            selected = {"entry_point": selected_name, "autoload": bypass, "executed": False}
        elif check_id == "SUP-11":
            paths = ["SIM-approved-root"] if role == "normal" else ["SIM-cwd", "SIM-approved-root"]
            matches = "SIM-cwd" not in paths
            selected = {"module_search_roots": paths, "isolated": not bypass, "executed": False}
        else:
            approved = _digest("SIM-approved-model-code")
            selected_hash = approved if role == "normal" else _digest("SIM-unapproved-model-code")
            matches = selected_hash == approved
            selected = {"code_sha256": selected_hash, "approved_sha256": approved, "executed": False}
        admitted = matches or bypass
        payload = {"role": role, "selected": selected, "policy_matches": matches,
                   "admitted": admitted, "ready": admitted, "simulation": True}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        # 알려진 모의 key만 사용. 실제 producer의 인증키/원자료가 아니다.
        key = b"EXAMPLE-only-producer-consumer-key"
        signature = hmac.new(key, encoded, hashlib.sha256).hexdigest()
        accepted_record = hmac.compare_digest(signature, hmac.new(key, encoded, hashlib.sha256).hexdigest())
        decoded = json.loads(encoded)
        return _event("supply_record_consumed", **decoded, record_sha256=hashlib.sha256(encoded).hexdigest(),
                      authenticated_in_simulation=accepted_record, record_source="SIMULATION-only-producer")

    normal, challenge = session("normal", False), session("challenge", fault)
    sessions = [normal, challenge]
    violations = ["SIMULATION:nonapproved-ready:" + check_id for s in sessions
                  if not s["policy_matches"] and s["ready"]]
    native = [_event("simulated_native_consumption", role=s["role"], selected=s["selected"],
                     consumed=s["admitted"], actual_worker=False) for s in sessions]
    values = {"supply_boot_sessions": sessions, "supply_boot_violations": violations,
              "supply_boot_missing": [], "supply_boot_complete": len(sessions) == 2,
              "supply_boot_normal_verified": normal["policy_matches"] and normal["ready"],
              "supply_boot_change_applied": normal["selected"] != challenge["selected"],
              "supply_boot_record_authenticated": all(s["authenticated_in_simulation"] for s in sessions),
              "supply_boot_native_consumption": native,
              "supply_boot_change": _event("challenge_change", kind=check_id, applied=normal["selected"] != challenge["selected"])}
    return values, sessions + native, normal["ready"], "supply_boot_complete"


def _input(fault: bool, policy: dict) -> tuple[dict, list, bool, str]:
    vocab = 8
    maximum = policy["max_input_tokens"]
    consumed = []

    def submit(name: str, tokens: list[int], bypass: bool = False) -> dict:
        valid = len(tokens) <= maximum and all(0 <= token < vocab for token in tokens)
        accepted = valid or bypass
        if accepted:
            consumed.append({"name": name, "tokens": list(tokens), "valid": valid})
        return _event("input_consumer", name=name, tokens=list(tokens), valid=valid,
                      consumed=accepted, code="200" if accepted else "400")

    baseline = submit("baseline", [0])
    rows = [submit("token_id", [vocab], fault), submit("length", [0] * (maximum + 1))]
    after = submit("after", [0])
    violations = ["SIMULATION:invalid-input-consumed:" + x["name"] for x in consumed if not x["valid"]]
    values = {"accepted_challenges": [x["name"] for x in rows if x["consumed"]],
              "challenge_outcomes": {x["name"]: x["code"] for x in rows},
              "challenges_planned": [x["name"] for x in rows],
              "functional_probes_planned": ["POST /v1/completions"],
              "functional_probe_results": {"POST /v1/completions": "pass" if baseline["consumed"] and after["consumed"] else "fail"},
              "input_endpoints_tested": list(policy["allowed_endpoints"]), "input_formats_tested": list(policy["allowed_input_formats"]),
              "challenge_rejection_results": {x["name"]: "verified" if not x["consumed"] and x["code"] in policy["rejection_codes"] else "not_rejected" for x in rows},
              "challenge_consumption": {x["name"]: "consumed_invalid" if x["consumed"] else "not_consumed" for x in rows},
              "confirmed_input_violations": violations}
    return values, [baseline, *rows, after], baseline["consumed"], "challenge_rejection_results"


def _sampling(fault: bool, policy: dict) -> tuple[dict, list, bool, str]:
    normal = {"temperature": 0.5, "top_p": 0.5, "min_tokens": 0}
    invalid = {"temperature": -1.0, "top_p": 1.1, "min_tokens": 2}
    events = []

    def process(option: str, value, bypass: bool) -> dict:
        valid = (value >= 0 if option == "temperature" else 0 < value <= 1 if option == "top_p" else 0 <= value <= 1)
        accepted = valid or bypass
        return _event("sampling_runner", option=option, submitted=value, validated=valid,
                      consumed_value=value if accepted else None, accepted=accepted,
                      code="200" if accepted else "400")

    baseline = [process(option, normal[option], False) for option in policy["allowed_sampling_options"]]
    challenge = [process(option, invalid[option], fault and option == "temperature")
                 for option in policy["allowed_sampling_options"]]
    after = [process(option, normal[option], False) for option in policy["allowed_sampling_options"]]
    events.extend(baseline + challenge + after)
    healthy = all(x["accepted"] and x["validated"] for x in baseline + after)
    values = {"sampling_options_planned": [x["option"] for x in challenge],
              "sampling_options_tested": [x["option"] for x in baseline],
              "sampling_challenge_outcomes": {x["option"]: x["code"] for x in challenge},
              "sampling_accepted_options": [x["option"] for x in challenge if x["accepted"]],
              "functional_probes_planned": ["POST /v1/completions"],
              "functional_probe_results": {"POST /v1/completions": "pass" if healthy else "fail"},
              "sampling_health_results": {x["option"]: x["code"] for x in after},
              "runner_consumption_verified": all(not x["accepted"] or x["consumed_value"] == x["submitted"] for x in events)}
    return values, events, healthy, "runner_consumption_verified"


def simulate(check_id: str, scenario: str, workdir: Path) -> dict:
    """호출당 새 상태. workdir는 인터페이스만 받고 파일/네트워크를 사용하지 않는다."""
    if check_id not in CHECK_IDS:
        raise ValueError("unsupported check_id")
    if scenario not in ("normal", "violation", "missing"):
        raise ValueError("unsupported scenario")
    if not isinstance(workdir, Path):
        raise TypeError("workdir must be pathlib.Path")
    policy = deepcopy(POLICY_VALUES[check_id])
    fault = scenario == "violation"
    if check_id == "LIF-11":
        output = _recovery(fault, policy)
    elif check_id == "LIF-06":
        output = _transition(fault, policy)
    elif check_id == "SUP-03":
        output = _template(fault, policy)
    elif check_id == "PLT-02":
        output = _ipc(fault, policy)
    elif check_id.startswith("SUP-"):
        output = _supply(check_id, fault)
    elif check_id == "REQ-09":
        output = _input(fault, policy)
    else:
        output = _sampling(fault, policy)
    values, events, baseline_ok, missing_key = output
    return _result(values, events, baseline_ok, scenario == "missing", missing_key)
