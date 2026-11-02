"""supply_boot 프로파일: 공급물을 바꾼 기동에서 실제로 읽고 실행한 것을 기준과 대조한다.

기록 행은 준비 절차가 소유 위치에 남긴 닫힌 형식의 사실이고 생산자는 인증되지 않는다. 기록이 스스로
드러낸 위반은 FAIL 근거지만, 기록만으로 충족을 주장하지 않는다. native 프로파일은 센서가 본 읽기 bytes로 판단한다.
"""
from __future__ import annotations

import re

from . import dynamic_code_cpu, plugin_loading

SCHEMA = "guard-supply-boot-record/1"
CHECK_PROFILES = {
    "SUP-05": "supply-boot-substitution-v1", "SUP-06": "supply-boot-worker-shard-v1", "SUP-16": "supply-boot-partial-artifact-v1",
    "SUP-07": "supply-boot-cache-tamper-v1", "SUP-15": "supply-boot-cache-reuse-v1", "SUP-09": "supply-boot-model-code-v1",
    "SUP-10": "supply-boot-plugin-v1", "SUP-11": "supply-boot-python-mode-v1", "SUP-12": "supply-boot-reader-v1",
    "LIF-01": "supply-boot-stage-budget-v1", "LIF-02": "supply-boot-fetch-retry-v1",
    "PLT-03": "supply-boot-fetch-scope-v1", "PLT-05": "supply-boot-credential-fallback-v1"}
NATIVE_CHECKS = frozenset({"SUP-05", "SUP-06", "SUP-16"})
ROLES = ("normal", "challenge")
COMMON_POLICY = {"supply_environment_sha256": "str", "supply_normal_target_sha256": "str", "supply_challenge_target_sha256": "str",
                 "supply_normal_prepared_sha256": "str", "supply_challenge_prepared_sha256": "str",
                 "supply_record_source_sha256": "str", "supply_validity_start": "str", "supply_validity_end": "str"}
EXTRA_POLICY = {
    "LIF-01": {"supply_stage_budget_ms": "dict[str,int]", "supply_stage_budget_bytes": "dict[str,int]", "supply_total_budget_ms": "int"},
    "LIF-02": {"supply_max_attempts": "int", "supply_max_fetch_bytes": "int"},
    "PLT-03": {"supply_allowed_objects": "list[str]"},
    "PLT-05": {"supply_allowed_identities": "list[str]"},
    "SUP-12": {"supply_allowed_readers": "list[str]"}}
OBSERVATIONS = {"supply_boot_sessions": "list", "supply_boot_change": "dict", "supply_boot_violations": "list",
                "supply_boot_missing": "list", "supply_boot_complete": "bool", "supply_boot_normal_verified": "bool",
                "supply_boot_change_applied": "bool", "supply_boot_record_authenticated": "bool",
                "supply_boot_native_consumption": "list"}
_ROWS = {
    "LIF-01": {"stage", "elapsed_ms", "peak_bytes", "stopped", "temporary_cleaned"},
    "LIF-02": {"attempt", "at_ms", "bytes", "outcome", "temporary_left"},
    "PLT-03": {"object_sha256", "identity_sha256", "outcome"},
    "PLT-05": {"case", "identity_sha256", "source", "outcome"},
    "SUP-06": {"rank", "required_missing", "ready_accepted"},
    "SUP-07": {"path_sha256", "writer_allowed", "modified", "consumed", "verified_before_use"},
    "SUP-12": {"fixture_sha256", "format", "reader", "outcome", "fallback", "side_effects"},
    "SUP-15": {"path_sha256", "relevant_change", "reused", "compatibility_verified"}}
_CHANGE = {"kind", "applied", "denied", "actor_sha256", "sensor_started_before_consumption"}
_SHA = re.compile(r"[0-9a-f]{64}")


def policy_types(check_id):
    return {**COMMON_POLICY, **EXTRA_POLICY.get(check_id, {})}


def normalize_policy(check_id, values):
    from .rank_failure_contract import stamp
    types = policy_types(check_id)
    try:
        if type(values) is not dict or set(values) != set(types):
            raise ValueError
        for key in COMMON_POLICY:
            if key.endswith("_sha256") and (type(values[key]) is not str or _SHA.fullmatch(values[key]) is None):
                raise ValueError
        start, end = stamp(values["supply_validity_start"]), stamp(values["supply_validity_end"])
        if not 0 < (end - start).total_seconds() <= 3600:
            raise ValueError
        for key, kind in EXTRA_POLICY.get(check_id, {}).items():
            value = values[key]
            if kind == "int" and (type(value) is not int or value < 1):
                raise ValueError
            if kind == "list[str]" and (type(value) is not list or not value or any(type(v) is not str or not v for v in value)):
                raise ValueError
            if kind == "dict[str,int]" and (type(value) is not dict or not value
                                            or any(type(v) is not int or isinstance(v, bool) or v < 0 for v in value.values())):
                raise ValueError
        return start, end
    except (KeyError, TypeError, ValueError):
        raise ValueError("SUPPLY_BOOT_POLICY_INVALID") from None


def validate_record(packet, *, check_id, role, environment_ref, target_sha256, source_sha256):
    keys = {"schema", "profile", "role", "environment_ref", "target_sha256", "source_sha256", "change", "rows"}
    if (type(packet) is not dict or set(packet) != keys or packet["schema"] != SCHEMA
            or packet["profile"] != CHECK_PROFILES[check_id] or packet["role"] != role
            or packet["environment_ref"] != environment_ref or packet["target_sha256"] != target_sha256
            or packet["source_sha256"] != source_sha256 or type(packet["rows"]) is not list or len(packet["rows"]) > 256):
        raise ValueError("SUPPLY_BOOT_RECORD_INVALID")
    change = packet["change"]
    if (type(change) is not dict or set(change) != _CHANGE or type(change["kind"]) is not str
            or any(type(change[k]) is not bool for k in ("applied", "denied", "sensor_started_before_consumption"))
            or type(change["actor_sha256"]) is not str or _SHA.fullmatch(change["actor_sha256"]) is None):
        raise ValueError("SUPPLY_BOOT_RECORD_INVALID")
    fields = _ROWS.get(check_id)
    if fields is not None and any(type(row) is not dict or set(row) != fields for row in packet["rows"]):
        raise ValueError("SUPPLY_BOOT_RECORD_INVALID")
    return packet


def _stage_budget(rows, values):
    violations, missing, total = [], [], 0
    budget_ms, budget_bytes = values["supply_stage_budget_ms"], values["supply_stage_budget_bytes"]
    seen = {row["stage"]: row for row in rows}
    for stage in budget_ms:
        row = seen.get(stage)
        if row is None:
            missing.append("stage_cost_unavailable:" + stage)
            continue
        total += row["elapsed_ms"]
        if (row["elapsed_ms"] > budget_ms[stage] or row["peak_bytes"] > budget_bytes.get(stage, row["peak_bytes"])) and not row["stopped"]:
            violations.append({"reason": "stage_budget_exceeded_without_stop", "stage": stage})
        if not row["temporary_cleaned"]:
            violations.append({"reason": "temporary_resources_left", "stage": stage})
    if total > values["supply_total_budget_ms"] and not any(row["stopped"] for row in rows):
        violations.append({"reason": "total_budget_exceeded_without_stop"})
    return violations, missing


def _fetch_retry(rows, values):
    violations = []
    if len(rows) > values["supply_max_attempts"]:
        violations.append({"reason": "retry_attempts_exceeded", "attempts": len(rows)})
    if sum(row["bytes"] for row in rows) > values["supply_max_fetch_bytes"]:
        violations.append({"reason": "retry_bytes_exceeded"})
    if any(row["temporary_left"] for row in rows[-1:]):
        violations.append({"reason": "temporary_file_left_after_stop"})
    return violations, [] if rows else ["fetch_attempts_unavailable"]


def _fetch_scope(rows, values):
    allowed = set(values["supply_allowed_objects"])
    violations = [{"reason": "unapproved_object_fetched", "object_sha256": r["object_sha256"]}
                  for r in rows if r["object_sha256"] not in allowed and r["outcome"] == "allowed"]
    tried = any(r["object_sha256"] not in allowed for r in rows)
    return violations, [] if tried else ["unapproved_object_not_attempted"]


def _fallback(rows, values):
    allowed = set(values["supply_allowed_identities"])
    violations = [{"reason": "unapproved_fallback_fetch", "case": r["case"]} for r in rows
                  if r["outcome"] == "fetched" and (r["identity_sha256"] not in allowed or r["source"] != "approved")]
    missing = [f"credential_case_unobserved:{case}" for case in ("expired", "missing") if case not in {r["case"] for r in rows}]
    return violations, missing


def _cache_tamper(rows, values):
    violations = [{"reason": "tampered_cache_consumed", "path_sha256": r["path_sha256"]} for r in rows
                  if r["modified"] and not r["writer_allowed"] and r["consumed"] and not r["verified_before_use"]]
    return violations, [] if any(r["modified"] or not r["writer_allowed"] for r in rows) else ["cache_tamper_not_attempted"]


def _cache_reuse(rows, values):
    violations = [{"reason": "incompatible_cache_reused", "path_sha256": r["path_sha256"]} for r in rows
                  if r["relevant_change"] and r["reused"] and not r["compatibility_verified"]]
    return violations, [] if any(r["relevant_change"] for r in rows) else ["relevant_change_not_attempted"]


def _reader(rows, values):
    allowed = set(values["supply_allowed_readers"])
    violations = []
    for row in rows:
        if row["outcome"] == "loaded" and row["reader"] not in allowed:
            violations.append({"reason": "unapproved_reader_used", "fixture_sha256": row["fixture_sha256"]})
        if row["side_effects"]:
            violations.append({"reason": "forbidden_side_effect", "fixture_sha256": row["fixture_sha256"]})
    return violations, [] if rows else ["reader_fixture_unobserved"]


def _worker_shard(rows, values):
    violations = [{"reason": "missing_shard_reported_ready", "rank": r["rank"]} for r in rows if r["required_missing"] and r["ready_accepted"]]
    return violations, [] if rows else ["rank_load_record_unavailable"]


def _plugin(rows, values):
    projected = plugin_loading.project(rows)
    missing = [] if projected["plugin_cpu_cases_complete"]["value"] else ["plugin_cases_incomplete"]
    return [{"reason": "unapproved_plugin_executed", "case": key}
            for key in projected["plugin_loading_confirmed_violations"]["value"] or []], missing


def _dynamic(check_id):
    def evaluate(rows, values):
        projected = dynamic_code_cpu.project(rows)
        key = {"SUP-09": "dynamic_code_confirmed_violations", "SUP-11": "dynamic_mode_confirmed_violations"}[check_id]
        return ([{"reason": "forbidden_code_imported", "case": case} for case in projected[check_id][key]["value"] or []],
                [] if projected["complete"] else ["dynamic_code_cases_incomplete"])
    return evaluate


EVALUATORS = {"LIF-01": _stage_budget, "LIF-02": _fetch_retry, "PLT-03": _fetch_scope, "PLT-05": _fallback,
              "SUP-06": _worker_shard, "SUP-07": _cache_tamper, "SUP-15": _cache_reuse, "SUP-12": _reader,
              "SUP-10": _plugin, "SUP-09": _dynamic("SUP-09"), "SUP-11": _dynamic("SUP-11")}


def evaluate_record(check_id, packet, values):
    evaluator = EVALUATORS.get(check_id)
    return evaluator(packet["rows"], values) if evaluator is not None else ([], [])


def native_consumption(analysis):
    """승인 기준과 실제 읽기 bytes를 비교한 결과. 전체 worker·tensor 확정이 아니다."""
    rows = [{"path_sha256": row["normalized_path_sha256"], "pid": row["pid"], "read_bytes": row["read_return_bytes"],
             "full_read": row["full_fd_read_return_observed"], "matches_approved": row["declared_reference_bytes_match_observed"],
             "mismatch": row["declared_reference_mismatch_observed"]} for row in analysis["candidates"]]
    unread = sum(group["declared_paths_without_candidate_count"] for group in analysis["groups"])
    return {"rows": rows, "unread_declared_paths": unread, "unjoined_reads": analysis["unjoined_read_count"],
            "open_failures": analysis["open_failure_count"], "missing": list(analysis["missing"])}


__all__ = ["CHECK_PROFILES", "NATIVE_CHECKS", "OBSERVATIONS", "ROLES", "SCHEMA", "evaluate_record", "native_consumption",
           "normalize_policy", "policy_types", "validate_record"]
