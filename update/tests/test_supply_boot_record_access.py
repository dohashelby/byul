"""supply_boot·record_access·REQ-02 복합 수집의 계약과 선정→EVD→RES→RPT→저장 팩 재판정. 모두 EXAMPLE 대역이다."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.collectors.native_model_file_reads import analyze
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import kubernetes_record_access_product as record_product
from guard.executors import kubernetes_route_record as route
from guard.executors import kubernetes_supply_boot as boot
from guard.executors import composite_collection, registry
from guard.executors import supply_boot_contract as contract
from guard.executors.base import EvidenceDraft
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex, utcnow
from guard.store import Store
from guard.store.packs import get_pack, put_pack
from tests.builders import environment, policy
from tests.test_native_model_file_reads import fixture as read_fixture

ROOT = Path(__file__).resolve().parents[1]
PACK = load_pack(ROOT / "packs/PACK-BASELINE")
SOURCE = "c" * 64
ACTOR = "d" * 64


def native(rows, unread=0, missing=()):
    return {"rows": rows, "unread_declared_paths": unread, "unjoined_reads": 0, "open_failures": 0, "missing": list(missing)}


def read_row(mismatch=False):
    return {"path_sha256": "a" * 64, "pid": 7, "read_bytes": 8, "full_read": True,
            "matches_approved": not mismatch, "mismatch": mismatch}


def change(applied=True, started_first=True):
    return {"kind": "file", "applied": applied, "denied": False, "actor_sha256": ACTOR, "sensor_started_before_consumption": started_first}


def base_values(env, check_id, extra=None):
    now = utcnow()
    values = {"supply_environment_sha256": env.ref().sha256, "supply_normal_target_sha256": "1" * 64, "supply_challenge_target_sha256": "2" * 64,
              "supply_normal_prepared_sha256": "3" * 64, "supply_challenge_prepared_sha256": "4" * 64, "supply_record_source_sha256": SOURCE,
              "supply_validity_start": (now - timedelta(minutes=1)).isoformat(), "supply_validity_end": (now + timedelta(minutes=5)).isoformat()}
    return {**values, **(extra or {})}


class FakeRoles:
    """역할별 세션 결과를 고정한다. 입력 대조·판정 코어는 실제 코드를 탄다."""

    def __init__(self, monkeypatch, parts):
        self.calls = []
        def collect(executor, check, env, values, role):
            self.calls.append(role)
            part = parts[role]
            if isinstance(part, Exception):
                row = {"role": role, "collected": False, "sealed": False, "record_sha256": None, "owner_binding_verified": False,
                       "native_files_covered": False, "diagnostic_codes": ["SUPPLY_BOOT_PREFLIGHT_UNAVAILABLE"]}
                return {"row": row, "packet": None, "native": None, "evaluated": None}
            return deepcopy(part)
        monkeypatch.setattr(boot.KubernetesSupplyBootExecutor, "_collect_role", collect)


def part(role, check_id, values, rows=(), nat=None, chg=None, covered=True):
    packet = {"schema": contract.SCHEMA, "profile": contract.CHECK_PROFILES[check_id], "role": role, "environment_ref": {},
              "target_sha256": "1" * 64, "source_sha256": SOURCE, "change": chg or change(applied=role == "challenge"), "rows": list(rows)}
    return {"row": {"role": role, "collected": True, "sealed": True, "record_sha256": "e" * 64, "owner_binding_verified": True,
                    "native_files_covered": covered, "diagnostic_codes": []},
            "packet": packet, "native": nat, "evaluated": contract.evaluate_record(check_id, packet, values)}


def pipeline(tmp_path, check_id, values, make):
    store = Store(tmp_path / "store")
    env = environment(model={"id": "test"})
    ref = put_pack(store, PACK.path)
    resolved = values(env) if callable(values) else values
    pol = policy(ref, scope={"include": [check_id]}, values={check_id: resolved})
    store.put(env); store.put(pol)
    plan = select(PACK, [env], pol); store.put(plan)
    name = PACK.checks[check_id].executor
    registry.EXECUTORS[name + ":sim"] = lambda cfg: make(env, resolved)
    try:
        evd, = run_plan(store, PACK, plan, RunConfig(executors={name: "sim"}, adapter="vllm"))
    finally:
        registry.EXECUTORS.pop(name + ":sim")
    result, = judge_plan(store, PACK, plan, env.created_at)
    assert check_id in [row.check_id for row in build_report(store, PACK, plan, env.created_at).by_check]
    again, = judge_plan(store, get_pack(store, ref), plan, env.created_at)
    assert again.verdicts == result.verdicts
    return evd, result


def run_boot(tmp_path, monkeypatch, check_id, extra, normal, challenge):
    def make(env, values):
        FakeRoles(monkeypatch, {"normal": normal(values) if callable(normal) else normal,
                                "challenge": challenge(values) if callable(challenge) else challenge})
        return boot.KubernetesSupplyBootExecutor({"normal": {}, "challenge": {}}, runner=object())
    return pipeline(tmp_path, check_id, lambda env: base_values(env, check_id, extra), make)


@pytest.mark.parametrize("check_id", ["SUP-05", "SUP-16"])
@pytest.mark.parametrize("challenge_native,security", [
    (native([read_row()]), "PASS"), (native([read_row(True)]), "FAIL"), (native([]), "PASS"),
])
def test_native_profiles_decide_from_bytes_actually_read(tmp_path, monkeypatch, check_id, challenge_native, security):
    evd, result = run_boot(tmp_path, monkeypatch, check_id, None,
        lambda v: part("normal", check_id, v, nat=native([read_row()])),
        lambda v: part("challenge", check_id, v, nat=challenge_native))
    assert evd.normal_control.succeeded is True
    assert result.verdicts.security.value == security


def test_change_not_applied_is_never_a_pass(tmp_path, monkeypatch):
    _, result = run_boot(tmp_path, monkeypatch, "SUP-05", None,
        lambda v: part("normal", "SUP-05", v, nat=native([read_row()])),
        lambda v: part("challenge", "SUP-05", v, nat=native([read_row()]), chg=change(applied=False)))
    assert result.verdicts.security.value == "UNKNOWN"


def test_instrumentation_started_after_consumption_is_missing_not_clean(tmp_path, monkeypatch):
    evd, result = run_boot(tmp_path, monkeypatch, "SUP-05", None,
        lambda v: part("normal", "SUP-05", v, nat=native([read_row()])),
        lambda v: part("challenge", "SUP-05", v, nat=native([read_row()]), chg=change(started_first=False)))
    assert "challenge:instrumentation_started_after_consumption" in evd.observations["supply_boot_missing"].value
    assert result.verdicts.security.value == "UNKNOWN"


def test_normal_session_failure_keeps_confirmed_challenge_violation(tmp_path, monkeypatch):
    evd, result = run_boot(tmp_path, monkeypatch, "SUP-05", None, RuntimeError("x"),
        lambda v: part("challenge", "SUP-05", v, nat=native([read_row(True)])))
    assert evd.execution.status.value == "ABORTED" and result.verdicts.security.value == "FAIL"
    assert evd.normal_control.succeeded is None


def test_both_sessions_unavailable_is_unknown_not_a_filled_wrapper(tmp_path, monkeypatch):
    evd, result = run_boot(tmp_path, monkeypatch, "SUP-16", None, RuntimeError("x"), RuntimeError("y"))
    assert evd.observations["supply_boot_violations"].collected is False and result.verdicts.security.value == "UNKNOWN"


def test_sessions_are_collected_separately_in_order(tmp_path, monkeypatch):
    seen = {}
    def make(env, values):
        seen["fake"] = FakeRoles(monkeypatch, {"normal": part("normal", "SUP-05", values, nat=native([read_row()])),
                                               "challenge": part("challenge", "SUP-05", values, nat=native([read_row()]))})
        return boot.KubernetesSupplyBootExecutor({"normal": {}, "challenge": {}}, runner=object())
    pipeline(tmp_path, "SUP-05", lambda env: base_values(env, "SUP-05"), make)
    assert seen["fake"].calls == ["normal", "challenge"]


def test_sup06_missing_shard_reported_ready_fails(tmp_path, monkeypatch):
    rows = [{"rank": 0, "required_missing": True, "ready_accepted": True}]
    fine = [{"rank": 0, "required_missing": False, "ready_accepted": True}]
    _, result = run_boot(tmp_path, monkeypatch, "SUP-06", None,
        lambda v: part("normal", "SUP-06", v, fine, nat=native([read_row()])),
        lambda v: part("challenge", "SUP-06", v, rows, nat=native([read_row()])))
    assert result.verdicts.security.value == "FAIL"


def test_real_native_analysis_output_reaches_the_consumer(tmp_path, monkeypatch):
    data, prepared, _ = read_fixture(tmp_path, monkeypatch)
    projected = contract.native_consumption(analyze(data, prepared))
    assert projected["rows"] and projected["rows"][0]["matches_approved"] is True
    assert projected["unread_declared_paths"] == 0


def test_real_native_mismatch_output_becomes_a_violation(tmp_path, monkeypatch):
    def tampered(scope):
        for row in scope["files"]:
            row["reference_sha256"] = "0" * 64
    data, prepared, _ = read_fixture(tmp_path, monkeypatch, scope_change=tampered)
    projected = contract.native_consumption(analyze(data, prepared))
    assert any(row["mismatch"] for row in projected["rows"])


VALUES = {"supply_stage_budget_ms": {"fetch": 10}, "supply_stage_budget_bytes": {"fetch": 10}, "supply_total_budget_ms": 10,
          "supply_max_attempts": 2, "supply_max_fetch_bytes": 10, "supply_allowed_objects": ["a"], "supply_allowed_identities": ["i"],
          "supply_allowed_readers": ["safetensors"]}
RECORD_CASES = [
    ("LIF-01", {"stage": "fetch", "elapsed_ms": 99, "peak_bytes": 1, "stopped": False, "temporary_cleaned": True},
     {"stage": "fetch", "elapsed_ms": 1, "peak_bytes": 1, "stopped": False, "temporary_cleaned": True}, "stage_budget_exceeded_without_stop"),
    ("LIF-01", {"stage": "fetch", "elapsed_ms": 1, "peak_bytes": 1, "stopped": True, "temporary_cleaned": False},
     {"stage": "fetch", "elapsed_ms": 99, "peak_bytes": 99, "stopped": True, "temporary_cleaned": True}, "temporary_resources_left"),
    ("PLT-03", {"object_sha256": "b", "identity_sha256": "i", "outcome": "allowed"},
     {"object_sha256": "b", "identity_sha256": "i", "outcome": "denied"}, "unapproved_object_fetched"),
    ("SUP-07", {"path_sha256": "p", "writer_allowed": False, "modified": True, "consumed": True, "verified_before_use": False},
     {"path_sha256": "p", "writer_allowed": False, "modified": True, "consumed": False, "verified_before_use": False}, "tampered_cache_consumed"),
    ("SUP-15", {"path_sha256": "p", "relevant_change": True, "reused": True, "compatibility_verified": False},
     {"path_sha256": "p", "relevant_change": True, "reused": True, "compatibility_verified": True}, "incompatible_cache_reused"),
    ("SUP-12", {"fixture_sha256": "f", "format": "pt", "reader": "pickle", "outcome": "loaded", "fallback": True, "side_effects": []},
     {"fixture_sha256": "f", "format": "pt", "reader": "pickle", "outcome": "rejected", "fallback": False, "side_effects": []}, "unapproved_reader_used"),
    ("SUP-12", {"fixture_sha256": "f", "format": "st", "reader": "safetensors", "outcome": "loaded", "fallback": False, "side_effects": ["file_open"]},
     {"fixture_sha256": "f", "format": "st", "reader": "safetensors", "outcome": "loaded", "fallback": False, "side_effects": []}, "forbidden_side_effect"),
]


@pytest.mark.parametrize("check_id,bad,good,reason", RECORD_CASES)
def test_record_rows_show_violation_and_clean_case(check_id, bad, good, reason):
    assert reason in [v["reason"] for v in contract.EVALUATORS[check_id]([bad], VALUES)[0]]
    assert contract.EVALUATORS[check_id]([good], VALUES)[0] == []


def test_retry_limits_and_fallback_identity():
    rows = [{"attempt": i, "at_ms": i, "bytes": 4, "outcome": "failed", "temporary_left": False} for i in range(3)]
    assert {v["reason"] for v in contract.EVALUATORS["LIF-02"](rows, VALUES)[0]} == {"retry_attempts_exceeded", "retry_bytes_exceeded"}
    fetched = {"case": "expired", "identity_sha256": "anon", "source": "cache", "outcome": "fetched"}
    found, missing = contract.EVALUATORS["PLT-05"]([fetched], VALUES)
    assert found[0]["reason"] == "unapproved_fallback_fetch" and missing == ["credential_case_unobserved:missing"]


def test_unattempted_challenges_are_missing_not_clean():
    assert contract.EVALUATORS["PLT-03"]([{"object_sha256": "a", "identity_sha256": "i", "outcome": "allowed"}], VALUES)[1]
    assert contract.EVALUATORS["SUP-15"]([], VALUES)[1] and contract.EVALUATORS["LIF-02"]([], VALUES)[1]


def test_plugin_and_dynamic_profiles_reuse_existing_projection():
    from guard.executors import dynamic_code_cpu, plugin_loading
    violation = {"case_id": "general-disabled", "status": "completed", "target_started": True,
                 "events": [{"plugin": "byul-unapproved", "phase": "import"}]}
    found, missing = contract.EVALUATORS["SUP-10"]([violation], VALUES)
    assert found[0]["case"] == "general-disabled" and missing == ["plugin_cases_incomplete"]
    case = next(c for c in dynamic_code_cpu.fixtures() if not c["trust"] and c["mode"] == "normal")
    row = {"case_id": case["case_id"], "case_identity": dynamic_code_cpu.identity(case), "events": [{"phase": "import"}],
           "event_collection_status": "completed", "outcome": "rejected_at_trust_gate",
           "before": {"outcome": "accepted"}, "after": {"outcome": "accepted"}}
    assert contract.EVALUATORS["SUP-09"]([row], VALUES)[0][0]["reason"] == "forbidden_code_imported"
    assert plugin_loading.GROUPS


def test_record_profile_keeps_fail_but_never_passes(tmp_path, monkeypatch):
    good = [{"object_sha256": "b", "identity_sha256": "i", "outcome": "denied"}]
    bad = [{"object_sha256": "b", "identity_sha256": "i", "outcome": "allowed"}]
    extra = {"supply_allowed_objects": ["a"]}
    _, result = run_boot(tmp_path, monkeypatch, "PLT-03", extra, lambda v: part("normal", "PLT-03", v, good), lambda v: part("challenge", "PLT-03", v, good))
    assert result.verdicts.security.value == "UNKNOWN"
    (tmp_path / "bad").mkdir()
    _, result = run_boot(tmp_path / "bad", monkeypatch, "PLT-03", extra, lambda v: part("normal", "PLT-03", v, good), lambda v: part("challenge", "PLT-03", v, bad))
    assert result.verdicts.security.value == "FAIL"


def good_packet(env_ref, digest, check_id="SUP-07"):
    return {"schema": contract.SCHEMA, "profile": contract.CHECK_PROFILES[check_id], "role": "normal", "environment_ref": env_ref,
            "target_sha256": digest, "source_sha256": SOURCE, "change": change(), "rows": []}


@pytest.mark.parametrize("damage", ["extra", "role", "environment", "target", "source", "profile", "row", "change"])
def test_boot_record_binding_is_closed(damage):
    packet = good_packet({"id": 1}, "t")
    if damage == "extra": packet["raw"] = "x"
    elif damage == "row": packet["rows"] = [{"raw": 1}]
    elif damage == "change": packet["change"]["applied"] = "yes"
    elif damage == "role": packet["role"] = "challenge"
    elif damage == "environment": packet["environment_ref"] = {"id": 2}
    elif damage == "target": packet["target_sha256"] = "u"
    elif damage == "source": packet["source_sha256"] = "u"
    elif damage == "profile": packet["profile"] = "other"
    with pytest.raises(ValueError, match="SUPPLY_BOOT_RECORD_INVALID"):
        contract.validate_record(packet, check_id="SUP-07", role="normal", environment_ref={"id": 1}, target_sha256="t", source_sha256=SOURCE)


def test_reader_allows_only_its_own_record_beyond_native_reads():
    reader = boot.SupplyBootReader.__new__(boot.SupplyBootReader)
    reader.binding, reader.session, reader.file_read_limit = {"engine": {"container": "engine"}}, "a" * 32, 1
    good = ["python3", "-c", boot._FILE_READER, boot.record_path("a" * 32), str(boot.MAX_BYTES)]
    assert reader._program("engine", good, 2 * boot.MAX_BYTES) == "file"
    with pytest.raises(Exception):
        reader._program("engine", [*good[:3], boot.record_path("b" * 32), good[4]], 2 * boot.MAX_BYTES)


@pytest.mark.parametrize("check_id", sorted(contract.CHECK_PROFILES))
def test_supply_boot_definition_and_policy_shape(check_id):
    check = PACK.checks[check_id]
    executor = registry.resolve_executor("supply_boot", "kubernetes")({"supply_boot": {}})
    assert isinstance(executor, boot.KubernetesSupplyBootExecutor) and check.collection_profile == contract.CHECK_PROFILES[check_id]
    assert {p.key for p in check.policy_params} == set(contract.policy_types(check_id))
    with pytest.raises(ValueError, match="SUPPLY_BOOT_POLICY_INVALID"):
        executor.validate_policy(check, {})


def test_expired_window_stops_before_any_session(tmp_path, monkeypatch):
    fake = FakeRoles(monkeypatch, {"normal": RuntimeError("x"), "challenge": RuntimeError("x")})
    env = environment(model={"id": "test"})
    values = base_values(env, "SUP-05")
    values["supply_validity_end"] = (utcnow() - timedelta(seconds=1)).isoformat()
    values["supply_validity_start"] = (utcnow() - timedelta(minutes=5)).isoformat()
    with pytest.raises(ValueError, match="SUPPLY_BOOT_POLICY_WINDOW_EXPIRED"):
        boot.KubernetesSupplyBootExecutor({"normal": {}, "challenge": {}}).run(PACK.checks["SUP-05"], env, values, None, tmp_path)
    assert fake.calls == []


def copy_row(**kw):
    return {"location_sha256": "a" * 64, "kind": "original", "grade": "internal", "encryption_required": False, "encryption_applied": False,
            "key_reference_sha256": None, "unauthorized_export": False, "unauthorized_decrypt": None, **kw}


FULL = [copy_row(), copy_row(kind="collector", location_sha256="b" * 64), copy_row(kind="backup", location_sha256="c" * 64)]


def test_inventory_detects_export_decrypt_and_plaintext():
    bad = [copy_row(unauthorized_export=True), copy_row(encryption_required=True), copy_row(unauthorized_decrypt=True)]
    reasons = {v["reason"] for v in record_product.evaluate_inventory({"copies": bad})[0]}
    assert reasons == {"unauthorized_export_succeeded", "required_encryption_absent", "unauthorized_decrypt_succeeded"}


def test_undecided_protection_and_missing_locations_are_incomplete():
    assert record_product.evaluate_inventory({"copies": FULL}) == ([], [])
    violations, missing = record_product.evaluate_inventory({"copies": [copy_row(encryption_required=None)]})
    assert not violations and any(m.startswith("encryption_required_undecided") for m in missing)
    assert "copy_location_unobserved:backup" in missing


@pytest.mark.parametrize("bad", [{"raw": 1}, {"kind": "elsewhere"}, {"key_reference_sha256": "key-value"}, {"unauthorized_export": "no"}])
def test_inventory_shape_is_closed(bad):
    packet = {"schema": record_product.INVENTORY_SCHEMA, "environment_ref": {}, "target_sha256": "t", "source_sha256": "s", "copies": [{**copy_row(), **bad}]}
    with pytest.raises(ValueError, match="RECORD_ACCESS_INVENTORY_INVALID"):
        record_product.validate_inventory(packet, environment_ref={}, target_sha256="t", source_sha256="s")


def access_capture(unauthorized=None, gaps=(), attempted=True):
    rows = [{"case_id": c, "role": r, "attempted": attempted, "identity_verified": True, "binding_verified": True, "read_verified": v,
             "returncode": 0, "byte_count": 12, "content_sha256": None, "gaps": []}
            for c, r, v in (("allowed-before", "authorized_reader", True), ("unauthorized", "unauthorized_reader", unauthorized),
                            ("allowed-after", "authorized_reader", True))]
    return {"rows": rows, "normal_control": {"attempted": True, "succeeded": True}, "unauthorized_read_verified": unauthorized,
            "cleanup": {"complete": True, "collection_gap": bool(gaps)}, "gaps": list(gaps)}


def access_run(tmp_path, monkeypatch, capture, inventory):
    (tmp_path / "target.json").write_text("{}"); (tmp_path / "subjects.json").write_text('{"policy_ref": {}}')
    def values(env):
        now = utcnow()
        return {"record_environment_sha256": env.ref().sha256, "record_target_sha256": sha256_hex(canonical_json({})),
                "record_subject_plan_sha256": sha256_hex(canonical_json({"policy_ref": {}})), "record_inventory_session": "a" * 32,
                "record_inventory_source_sha256": "b" * 64, "record_validity_start": (now - timedelta(minutes=1)).isoformat(),
                "record_validity_end": (now + timedelta(minutes=5)).isoformat()}
    def collect(*a, **k):
        if isinstance(capture, Exception):
            raise capture
        return deepcopy(capture)
    def read(self, env, target, values_, digest):
        if inventory is None:
            raise ValueError("gone")
        return {"copies": deepcopy(inventory)}
    monkeypatch.setattr(record_product, "capture_record_access", collect)
    monkeypatch.setattr(record_product.KubernetesRecordAccessExecutor, "_inventory", read)
    return pipeline(tmp_path, "OBS-06", values, lambda env, v: record_product.KubernetesRecordAccessExecutor(
        {"target": str(tmp_path / "target.json"), "subjects": str(tmp_path / "subjects.json")}, runner=object()))


def test_obs06_unauthorized_read_fails_even_when_inventory_is_missing(tmp_path, monkeypatch):
    evd, result = access_run(tmp_path, monkeypatch, access_capture(True), None)
    assert result.verdicts.security.value == "FAIL" and evd.observations["record_access_partial"].value is True


def test_obs06_log_reads_only_is_a_partial_observation(tmp_path, monkeypatch):
    evd, result = access_run(tmp_path, monkeypatch, access_capture(None), None)
    assert evd.observations["record_access_partial"].value is True and result.verdicts.security.value == "UNKNOWN"
    assert evd.observations["record_scope_complete"].value is False


def test_obs06_denied_read_and_clean_inventory_do_not_become_pass(tmp_path, monkeypatch):
    evd, result = access_run(tmp_path, monkeypatch, access_capture(None), FULL)
    assert evd.observations["record_scope_complete"].value is True and evd.observations["record_denial_verified"].value is False
    assert result.verdicts.security.value == "UNKNOWN"


def test_obs06_inventory_violation_survives_log_collection_failure(tmp_path, monkeypatch):
    evd, result = access_run(tmp_path, monkeypatch, RuntimeError("x"), [copy_row(encryption_required=True)])
    assert result.verdicts.security.value == "FAIL" and evd.execution.status.value == "ABORTED"
    assert evd.observations["record_access_rows"].collected is False


def draft(obs, status=ExecStatus.COMPLETED):
    return EvidenceDraft(status=status, data_origin=DataOrigin.EXAMPLE, normal_control={"attempted": True, "succeeded": None, "detail": "d"},
                         observations={k: {"value": v, "collected": True} for k, v in obs.items()}, started_at=utcnow(), finished_at=utcnow())


def test_merge_keeps_each_session_and_reports_the_failed_one():
    check = PACK.checks["REQ-02"]
    merged = composite_collection.merge_drafts(check, {"route_record": draft({"route_events": []})}, {"native_route": "SUBCOLLECTION_UNAVAILABLE"})
    assert merged.status == ExecStatus.ABORTED and merged.observations["route_events"].collected
    assert merged.observations["composite_subcollections"].value["failed"] == {"native_route": "SUBCOLLECTION_UNAVAILABLE"}


def route_values():
    now = utcnow()
    start, end = (now - timedelta(minutes=1)).isoformat(), (now + timedelta(minutes=1)).isoformat()
    return {"route_environment_sha256": "a" * 64, "route_target_sha256": "b" * 64, "route_session": "c" * 32, "route_file_sha256": "d" * 64,
            "route_validity_start": start, "route_validity_end": end, "route_native_environment_sha256": "a" * 64,
            "route_native_target_sha256": "b" * 64, "route_native_prepared_sha256": "e" * 64,
            "route_native_validity_start": start, "route_native_validity_end": end}


def composite(tmp_path, monkeypatch, *, record=None, session=None, cfg=None):
    from guard.executors.kubernetes_native_route import KubernetesNativeRouteExecutor as Native
    seen = []
    def fake(result):
        def run(self, check, env, values, adapter, workdir):
            seen.append((type(self).__name__, check.collection_profile, sorted(values)))
            if isinstance(result, Exception):
                raise result
            return result
        return run
    monkeypatch.setattr(route.KubernetesContainerConfigExecutor, "run", fake(record))
    monkeypatch.setattr(Native, "run", fake(session))
    executor = route.KubernetesRouteRecordExecutor(cfg if cfg is not None else {"target": "t", "native_route": {"target": "t", "prepared": "p"}})
    return executor.run(PACK.checks["REQ-02"], SimpleNamespace(), route_values(), object(), tmp_path), seen, executor


EVENT = {"auth_decision": "deny", "handler_outcome": "entered"}


def test_req02_runs_two_subcollections_with_their_own_policy_and_profile(tmp_path, monkeypatch):
    merged, seen, executor = composite(tmp_path, monkeypatch, record=draft({"route_events": [EVENT]}), session=draft({"native_route_trials": [1]}))
    assert [row[1] for row in seen] == [route.PROFILE, "kubernetes-native-route-observation-v1"]
    assert all(key.startswith("route_native") for key in seen[1][2]) and not any(key.startswith("route_native") for key in seen[0][2])
    assert merged.observations["route_bypass_claims"].value == [EVENT] and executor.version == "0.2.0"


def test_req02_native_failure_keeps_route_record_and_vice_versa(tmp_path, monkeypatch):
    merged, _, _ = composite(tmp_path, monkeypatch, record=draft({"route_events": []}), session=RuntimeError("x"))
    assert merged.status == ExecStatus.ABORTED and merged.observations["route_events"].collected
    merged, _, _ = composite(tmp_path, monkeypatch, record=RuntimeError("x"), session=draft({"native_route_trials": [1]}))
    assert merged.observations["native_route_trials"].collected and merged.observations["route_bypass_claims"].collected is False


def test_req02_requires_native_settings_and_keeps_old_profiles(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="KUBERNETES_ROUTE_CONFIGURATION_INVALID"):
        composite(tmp_path, monkeypatch, record=draft({}), session=draft({}), cfg={"target": "t"})
    assert "native_route:kubernetes" in registry.EXECUTORS


class FakeReader:
    """kubectl 경계만 대역으로 두고 _collect_role의 입력 대조·기록 검증·native 분기를 실제로 탄다."""
    fail_read = False

    def __init__(self, target, env, prepared, **kwargs):
        self.binding = {"engine": {"container": "engine", "image": "image"}}

    def snapshot(self):
        return {}

    def read_file(self, client, target, *, container, image, path):
        if self.fail_read:
            raise ValueError("gone")
        return self.raw, {"capture_sha256": "e" * 64, "owner_binding_verified": True}

    def seal_session(self):
        return {"sealed": True}

    def owner_identity(self):
        return None

    image_check = None

    def report(self):
        return {"parent_cleanup_unverified": True}


def role_inputs(tmp_path, monkeypatch, check_id, env):
    target = {"lab": True}
    (tmp_path / "target.json").write_text(json.dumps(target))
    digest = sha256_hex(canonical_json(target))
    prepared = {"session": "a" * 32}
    monkeypatch.setattr("guard.collectors.lifecycle.load_prepared", lambda path: prepared)
    values = base_values(env, check_id, {"supply_normal_target_sha256": digest, "supply_normal_prepared_sha256": sha256_hex(canonical_json(prepared))})
    monkeypatch.setattr(boot, "SupplyBootReader", FakeReader)
    executor = boot.KubernetesSupplyBootExecutor({"normal": {"target": str(tmp_path / "target.json"), "prepared": "p"}, "challenge": {}}, runner=object())
    return executor, values, digest


def test_collect_role_reads_record_and_native_through_real_binding(tmp_path, monkeypatch):
    env = environment(model={"id": "test"})
    executor, values, digest = role_inputs(tmp_path, monkeypatch, "SUP-05", env)
    packet = good_packet(env.ref().model_dump(mode="json"), digest, "SUP-05")
    FakeReader.raw = canonical_json(packet)
    monkeypatch.setattr(boot, "capture_session", lambda *a, **k: {"recorded_files_covered": True})
    monkeypatch.setattr("guard.collectors.native_model_file_reads.analyze",
        lambda captured, prepared: {"candidates": [], "groups": [], "unjoined_read_count": 0, "open_failure_count": 0, "missing": []})
    part_ = executor._collect_role(PACK.checks["SUP-05"], env, values, "normal")
    assert part_["row"]["collected"] and part_["row"]["sealed"] and part_["row"]["native_files_covered"]
    assert part_["native"]["rows"] == [] and part_["cleanup"] == "SUPPLY_BOOT_PARENT_CLEANUP_UNVERIFIED"


def test_collect_role_target_mismatch_never_reaches_the_reader(tmp_path, monkeypatch):
    env = environment(model={"id": "test"})
    executor, values, _ = role_inputs(tmp_path, monkeypatch, "SUP-05", env)
    def forbidden(*a, **k): raise AssertionError("no reader")
    monkeypatch.setattr(boot, "SupplyBootReader", forbidden)
    part_ = executor._collect_role(PACK.checks["SUP-05"], env, {**values, "supply_normal_target_sha256": "0" * 64}, "normal")
    assert part_["row"]["diagnostic_codes"] == ["SUPPLY_BOOT_INPUT_UNAVAILABLE"]


def test_collect_role_record_failure_still_collects_native(tmp_path, monkeypatch):
    env = environment(model={"id": "test"})
    executor, values, _ = role_inputs(tmp_path, monkeypatch, "SUP-05", env)
    monkeypatch.setattr(FakeReader, "fail_read", True)
    monkeypatch.setattr(boot, "capture_session", lambda *a, **k: {"recorded_files_covered": True})
    monkeypatch.setattr("guard.collectors.native_model_file_reads.analyze",
        lambda captured, prepared: {"candidates": [], "groups": [], "unjoined_read_count": 0, "open_failure_count": 0, "missing": []})
    part_ = executor._collect_role(PACK.checks["SUP-05"], env, values, "normal")
    assert part_["row"]["diagnostic_codes"] == ["SUPPLY_BOOT_RECORD_UNAVAILABLE"] and part_["native"] is not None
