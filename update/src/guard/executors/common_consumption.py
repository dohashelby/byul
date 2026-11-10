"""B의 runner/supply/worker-shard 소비 경계. A 봉투·등록·규칙은 변경하지 않는다.

독립적으로 제공된 scope와 파일 bytes pin을 확인하고 원 A 투영 함수를 호출한다.
metadata count를 token 값/decoder/worker 전체 소비나 승인 권한으로 승격하지 않는다.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import stat

from guard.collectors import gpu_common_inputs as common
from guard.collectors import worker_shard_producer
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex
from guard.runtime.bounded import strict_json

MAX_INPUT_BYTES = 2 * 1024 * 1024
SCOPE_KEYS = {"environment_ref", "target_sha256", "prepared_sha256", "session"}


def validate_record(record, scope, *, kind, check_id):
    if type(scope) is not dict or set(scope) != SCOPE_KEYS:
        raise ValueError("B_COMMON_SCOPE_REQUIRED")
    result = common.validate(record, kind=kind, **scope)
    if check_id not in result["checks"]:
        raise ValueError("B_COMMON_CHECK_UNSUPPORTED")
    return result


def load_input(config, *, environment_ref, target_sha256, prepared_sha256=None):
    """원 record의 신원을 기대값으로 재사용하지 않는다. 명시적인 외부 결합 입력만 받는다."""
    if (type(config) is not dict or set(config) - {"path", "sha256", "scope", "requests", "data_origin"}
            or config.get("data_origin", "EXAMPLE") not in {"EXAMPLE", "DECLARED"}):
        raise ValueError("B_COMMON_INPUT_INVALID")
    scope = config["scope"]
    if (type(scope) is not dict or set(scope) != SCOPE_KEYS or scope["environment_ref"] != environment_ref
            or scope["target_sha256"] != target_sha256
            or prepared_sha256 is not None and scope["prepared_sha256"] != prepared_sha256):
        raise ValueError("B_COMMON_INPUT_BINDING_MISMATCH")
    path = Path(config["path"])
    if path.is_symlink():
        raise ValueError("B_COMMON_INPUT_NOT_REGULAR")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_INPUT_BYTES:
            raise ValueError("B_COMMON_INPUT_NOT_REGULAR")
        raw = stream.read(MAX_INPUT_BYTES + 1)
        after = os.fstat(stream.fileno())
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if (identity(before) != identity(after) or len(raw) != before.st_size
            or sha256_hex(raw) != config["sha256"]):
        raise ValueError("B_COMMON_INPUT_BYTES_MISMATCH")
    return strict_json(raw, limit=MAX_INPUT_BYTES), scope


def runner(record, scope, *, check_id, requests, required_cases):
    result = validate_record(record, scope, kind="runner-consumption", check_id=check_id)
    if (type(requests) is not dict or not requests or any(type(key) is not str or not key for key in requests)
            or any(not common._check(value, "sha") for value in requests.values())
            or len(set(requests.values())) != len(requests)):
        raise ValueError("B_RUNNER_REQUEST_BINDING_INVALID")
    gaps = list(result["gaps"])
    if not required_cases or not set(required_cases) <= set(requests):
        gaps.append("B_RUNNER_CASE_COVERAGE_UNAVAILABLE")
    selected, consumed_rejections = {}, []
    for case in required_cases:
        request = requests.get(case)
        events = [row for row in record["events"] if row["request_sha256"] == request]
        api = [row for row in events if row["event"] in {"api_validated", "api_rejected"}]
        received = [row for row in events if row["event"] == "runner_received"]
        applied = [row for row in events if row["event"] == "sampler_applied"]
        selected[case] = {"request_sha256": request, "api_events": len(api),
                          "api_state": api[0]["event"] if len(api) == 1 else None,
                          "causal_order_verified": len(api) == 1 and bool(received) and all(row["seq"] > api[0]["seq"] for row in received),
                          "runner_events": len(received), "sampler_events": len(applied),
                          "runner_values": [{key: row[key] for key in ("seq", "binding", "prompt_tokens", "max_token_id")}
                                            for row in received]}
        if len(api) != 1:
            gaps.append("B_RUNNER_API_EVENT_UNAVAILABLE:" + case)
            continue
        if api[0]["event"] == "api_rejected":
            if received or applied:
                consumed_rejections.append(case)
        elif not received or not applied:
            gaps.append("B_RUNNER_CONSUMPTION_UNAVAILABLE:" + case)
        elif any(row["seq"] <= api[0]["seq"] for row in received) or any(
                row["seq"] <= min(r["seq"] for r in received) for row in applied):
            gaps.append("B_RUNNER_CAUSAL_ORDER_UNAVAILABLE:" + case)
    selected_requests = {requests.get(case) for case in required_cases}
    bindings = [record["bindings"][row["binding"]] for row in record["events"]
                if row["request_sha256"] in selected_requests]
    if len({(b["pod_uid"], b["container_id"], b["restart_count"]) for b in bindings}) > 1:
        gaps.append("B_RUNNER_PROCESS_GENERATION_MISMATCH")
    identities = {}
    for binding in bindings:
        key = tuple(binding[k] for k in ("pod_uid", "container_id", "restart_count", "worker_pid"))
        identities.setdefault(key, set()).add(binding["start_ticks"])
    if any(len(starts) > 1 for starts in identities.values()):
        gaps.append("B_RUNNER_PROCESS_ID_REUSED")
    reused = {key for key, starts in identities.items() if len(starts) > 1}
    for case, details in selected.items():
        case_bindings = [record["bindings"][row["binding"]] for row in record["events"]
                         if row["request_sha256"] == details["request_sha256"]]
        generations = {(b["pod_uid"], b["container_id"], b["restart_count"]) for b in case_bindings}
        details["identity_binding_verified"] = bool(case_bindings) and len(generations) == 1 and not any(
            tuple(b[k] for k in ("pod_uid", "container_id", "restart_count", "worker_pid")) in reused
            for b in case_bindings)
        # Only the new native rejection/consumption confirmation uses this
        # fact. /1 completeness keeps its original optional-ticks contract.
        details["process_start_identity_observed"] = bool(case_bindings) and all(
            b["start_ticks"] is not None for b in case_bindings)
    candidates = []
    for candidate in result["violation_candidates"]:
        # /2 and /3 rejected-input candidates point to an exact validated
        # event seq instead of repeating its request identity. Preserve that
        # reference; never use time, order of cases or a leftover request.
        request = candidate.get("request_sha256")
        seq = candidate.get("seq")
        if (request is None and record["schema"] in {"guard-gpu-common-input/2", "guard-gpu-common-input/3"}
                and type(seq) is int and 0 <= seq < len(record["events"])):
            event = record["events"][seq]
            if event["seq"] == seq:
                request = event["request_sha256"]
        if request in selected_requests:
            candidates.append({**candidate, "request_sha256": request})
    if candidates:
        gaps.append("B_RUNNER_CONSUMPTION_CONTRADICTION")
    return {**result, "complete": result["complete"] and not gaps, "gaps": sorted(set(gaps)),
            "cases": selected, "selected_candidates": candidates,
            "consumed_rejections": consumed_rejections if result["supported"] else [],
            "token_identity_verified": False, "whole_worker_scope_verified": False}


def supply_reads(record, scope, *, check_id="SUP-03"):
    result = validate_record(record, scope, kind="supply-reads", check_id=check_id)
    return {**result, "files": deepcopy(record["events"]), "actual_consumer_verified": False,
            "approval_authority_verified": False, "tensor_application_verified": False}


def worker_shards(record, scope, values):
    from .supply_boot_contract import _worker_shard
    result = validate_record(record, scope, kind="worker-shard-load", check_id="SUP-06")
    facts = worker_shard_producer.shard_rows(record)
    # The legacy boot record has an exact three-key row contract. Preserve new
    # load facts separately instead of widening that A/B exchange implicitly.
    rows = [{key: row[key] for key in ("rank", "required_missing", "ready_accepted")} for row in facts]
    candidates, missing = _worker_shard(rows, values)
    candidates += [{"reason": "failed_load_reported_ready", "rank": row["rank"]}
                   for row in facts if row["load_failed"] and row["ready_accepted"]]
    missing = [*missing, *result["gaps"]]
    return {**result, "rows": rows, "load_facts": facts, "candidates": candidates,
            "violations": candidates if result["supported"] else [],
            "missing": sorted(set(missing)), "complete": result["complete"] and not missing}


def _abort_incomplete(draft):
    # Do not erase an existing observation or independent FAIL to cap PASS.
    draft.status = ExecStatus.ABORTED
    draft.abort_reason = "; ".join(filter(None, (draft.abort_reason, "B_COMMON_CONSUMPTION_INCOMPLETE")))
    if draft.data_origin == DataOrigin.MEASURED:
        draft.data_origin = DataOrigin.DECLARED


def attach(draft, ctx, cfg, *, kind):
    """Existing registered executor _finish → unchanged EVD/judge path."""
    key = "runner_input" if kind == "runner-consumption" else "supply_reads_input"
    if key not in cfg:
        return draft
    from guard.models.objects import Observation
    from .base import DraftArtifact
    # Registration is not authentication/origin. File inputs default to EXAMPLE
    # and cannot upgrade a live draft or an existing weaker origin.
    origin = cfg[key].get("data_origin", "EXAMPLE") if type(cfg[key]) is dict else "EXAMPLE"
    draft.data_origin = DataOrigin.EXAMPLE if draft.data_origin == DataOrigin.EXAMPLE or origin != "DECLARED" else DataOrigin.DECLARED
    record, scope = None, None
    try:
        record, scope = load_input(cfg[key], environment_ref=ctx.env.ref().model_dump(mode="json"),
                                   target_sha256=sha256_hex(canonical_json(ctx.target)))
        if kind == "runner-consumption":
            required = set()
            for name in ("functional_probes_planned", "sampling_options_planned", "challenges_planned"):
                obs = draft.observations.get(name)
                if obs is not None and obs.collected and type(obs.value) is list:
                    required.update(obs.value)
            if ctx.check.id == "REQ-08":
                required.add("reused_input")
            projected = runner(record, scope, check_id=ctx.check.id, requests=cfg[key].get("requests"),
                               required_cases=sorted(required))
            expected_api = {}
            for name in ("challenge_outcomes", "sampling_challenge_outcomes"):
                obs = draft.observations.get(name)
                if obs is not None and obs.collected:
                    expected_api.update({case: "api_rejected" if str(code) in {"400", "413", "422"} else "api_validated"
                                         for case, code in obs.value.items() if str(code).isdigit()})
            state = draft.observations.get("reused_input_state")
            if ctx.check.id == "REQ-08" and state is not None and state.collected:
                expected_api["reused_input"] = "api_rejected" if state.value == "rejected" else "api_validated"
            for case, expected in expected_api.items():
                if case in projected["cases"] and projected["cases"][case]["api_state"] != expected:
                    projected["gaps"].append("B_RUNNER_API_RESULT_MISMATCH:" + case)
            if ctx.check.id == "REQ-10":
                # schema /1 contains neither min_tokens nor arbitrary V2 count/
                # structured-output values. A registered producer is not proof
                # of a field the producer never observed.
                projected["gaps"].extend("B_RUNNER_FIELD_UNSUPPORTED:" + field
                    for field in ctx.policy_values.get("allowed_sampling_options", [])
                    if field not in {"temperature", "top_p"})
            projected["complete"] = projected["complete"] and not projected["gaps"]
            if ctx.check.id == "REQ-10":
                draft.observations["runner_consumption_verified"] = Observation(collected=True, value=projected["complete"])
            elif ctx.check.id == "REQ-08":
                case = projected["cases"].get("reused_input", {})
                if (ctx.policy_values.get("reused_token_allowed") is False and projected["supported"]
                        and case.get("api_state") == "api_rejected"
                        and case.get("causal_order_verified") and case.get("identity_binding_verified")
                        and case.get("process_start_identity_observed")):
                    obs = draft.observations.get("reused_input_violations")
                    existing = list(obs.value or []) if obs is not None and obs.collected else []
                    code = "native_rejected_reused_input_consumed"
                    if code not in existing:
                        existing.append(code)
                    draft.observations["reused_input_violations"] = Observation(collected=True, value=existing)
            elif ctx.check.id == "REQ-09":
                obs = draft.observations.get("confirmed_input_violations")
                existing = list(obs.value or []) if obs is not None and obs.collected else []
                found = []
                if projected["supported"]:
                    for case, details in projected["cases"].items():
                        if not details["causal_order_verified"] or not details["identity_binding_verified"]:
                            continue
                        for row in details["runner_values"]:
                            value = row["max_token_id"] if case == "token_id" else row["prompt_tokens"]
                            limit = cfg.get("vocab_size") if case == "token_id" else ctx.policy_values.get("max_input_tokens")
                            if (case in {"token_id", "length"} and type(value) is int and type(limit) is int
                                    and (value >= limit if case == "token_id" else value > limit)):
                                found.append("runner_input_range_exceeded:" + case)
                if found:
                    draft.observations["confirmed_input_violations"] = Observation(collected=True, value=sorted(set(existing + found)))
        else:
            projected = supply_reads(record, scope, check_id=ctx.check.id)
        if ctx.check.id == "REQ-08":
            trials = draft.observations.get("reused_input_trials")
            if trials is not None and trials.collected:
                trials.value = [{**trial, "runner_metadata": projected} for trial in trials.value]
        if not projected["complete"]:
            _abort_incomplete(draft)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        record = None
        projected = {"complete": False, "supported": False, "gaps": ["B_COMMON_INPUT_UNAVAILABLE"], "producer_authenticated": False,
                     "input_error": {"type": type(exc).__name__, "message": str(exc)[:1024]}}
        if ctx.check.id == "REQ-10":
            draft.observations["runner_consumption_verified"] = Observation(collected=True, value=False)
        _abort_incomplete(draft)
    path = Path(ctx.workdir) / "b_common_consumption.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json({"schema": "guard-b-common-consumption/1", "kind": kind,
            "data_origin": draft.data_origin.value, "record": record, "scope": scope, "projection": projected}))
        draft.artifacts.append(DraftArtifact(name=path.name, path=path, collector="b-common-consumption/1"))
    except OSError:
        draft.cleanup_errors.append("B_COMMON_ARTIFACT_WRITE_UNAVAILABLE")
        _abort_incomplete(draft)
    draft.config["b_common_input"] = {"kind": kind, "data_origin": draft.data_origin.value,
        "record_sha256": sha256_hex(canonical_json(record)) if record is not None else None,
        "scope_sha256": sha256_hex(canonical_json(scope)) if scope is not None else None}
    return draft
