"""기존 실행기 확장 7개: 정상·위반·누락·지원 불일치·후속 오류와 선정→EVD→RES→RPT→저장 팩 재판정."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import (ipc_trust_scope, log_collection_gap, record_transport, registry, request_field_probe,
                             usage_report)
from guard.executors.api_transport import APITransportExecutor
from guard.executors.boundary_egress import BoundaryEgressExecutor
from guard.executors.input_validation import InputValidationExecutor
from guard.executors.log_integrity import LogIntegrityExecutor
from guard.executors.unix_ipc import UnixIPCExecutor
from guard.models import load_pack
from guard.runtime.docker import CmdResult
from guard.store import Store
from guard.store.packs import get_pack, put_pack
from tests.builders import completion_response, environment, policy

ROOT = Path(__file__).resolve().parents[1]
PACK = load_pack(ROOT / "packs/PACK-BASELINE")
FIXTURE_TOKENS = [11, 12, 13]
CONTROL_TOKENS = [5, 6]


def target(tmp_path):
    value = {"lab": True, "run_id": "run", "engine": {"trusted_url": "http://engine:8000", "container": "engine"},
             "origins": {"trusted": {"network": "inside"}}}
    path = tmp_path / "target.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return value, path


def pipeline(tmp_path, cid, factory, values, *, extra_cfg=None):
    _, path = target(tmp_path)
    store = Store(tmp_path / "store")
    env = environment(model={"id": "test"})
    ref = put_pack(store, PACK.path)
    pol = policy(ref, scope={"include": [cid]}, values={cid: values})
    store.put(env); store.put(pol)
    plan = select(PACK, [env], pol); store.put(plan)
    name = PACK.checks[cid].executor
    key = name + ":sim"
    registry.EXECUTORS[key] = lambda cfg: factory({"target": str(path), **(extra_cfg or {})})
    try:
        evd, = run_plan(store, PACK, plan, RunConfig(executors={name: "sim"}, adapter="vllm"))
    finally:
        registry.EXECUTORS.pop(key)
    result, = judge_plan(store, PACK, plan, env.created_at)
    report = build_report(store, PACK, plan, env.created_at)
    assert cid in [row.check_id for row in report.by_check]
    saved = get_pack(store, ref)
    again, = judge_plan(store, saved, plan, env.created_at)
    assert again.verdicts == result.verdicts and saved.ref() == PACK.ref()
    return evd, result


def token_probe(challenge):
    def probe(net, requests, timeout):
        out = []
        for request in requests:
            reply = completion_response(request)
            body = json.loads(reply["body"]) if "body" in reply and reply["body"] else {}
            if "prompt_token_ids" in request["body"]:
                reply = challenge(request, reply, body)
            elif body:
                body["prompt_token_ids"] = CONTROL_TOKENS
                reply["body"] = json.dumps(body)
            out.append(reply)
        return out
    return probe


def consumed(request, reply, body):
    body["prompt_token_ids"] = request["body"]["prompt_token_ids"]
    reply["body"] = json.dumps(body)
    return reply


def ignored(request, reply, body):
    body["prompt_token_ids"] = CONTROL_TOKENS
    reply["body"] = json.dumps(body)
    return reply


def rejected(request, reply, body):
    return {**reply, "outcome": 400, "body": ""}


def silent(request, reply, body):
    return reply


REQ08 = {"reused_token_field": "prompt_token_ids", "rejection_codes": ["400"]}
CFG08 = {"reused_token_ids": FIXTURE_TOKENS, "vocab_size": 100, "vocab_source": "EXAMPLE fixture"}


@pytest.mark.parametrize("challenge,allowed,security,state", [
    (rejected, False, "PASS", "rejected"), (ignored, False, "PASS", "ignored"), (consumed, False, "FAIL", "consumed"),
    (silent, False, "UNKNOWN", "accepted_unverified"), (consumed, True, "UNKNOWN", "consumed"),
])
def test_req08_reused_tokens_are_decided_by_consumed_ids(tmp_path, challenge, allowed, security, state):
    evd, result = pipeline(tmp_path, "REQ-08", lambda c: InputValidationExecutor(c, container_probe=token_probe(challenge)),
                           {**REQ08, "reused_token_allowed": allowed}, extra_cfg=CFG08)
    assert evd.observations["reused_input_state"].value == state
    assert evd.execution.executor.version == request_field_probe.VERSION
    assert result.verdicts.security.value == security
    if security != "UNKNOWN":
        assert result.verdicts.functional.value == "PASS"


def test_req08_failed_control_sends_no_challenge_and_stays_unknown(tmp_path):
    calls = []
    def probe(net, requests, timeout):
        calls.extend(requests)
        return [{**completion_response(r), "outcome": 500, "body": ""} for r in requests]
    evd, result = pipeline(tmp_path, "REQ-08", lambda c: InputValidationExecutor(c, container_probe=probe),
                           {**REQ08, "reused_token_allowed": False}, extra_cfg=CFG08)
    assert len(calls) == 1 and evd.observations["reused_input_state"].collected is False
    assert result.verdicts.security.value == "UNKNOWN" and result.verdicts.functional.value == "FAIL"


def test_req08_requires_fixture_inside_vocabulary_before_any_request(tmp_path):
    def probe(*args): raise AssertionError("must not send")
    evd, _ = pipeline(tmp_path, "REQ-08", lambda c: InputValidationExecutor(c, container_probe=probe),
                      {**REQ08, "reused_token_allowed": False}, extra_cfg={**CFG08, "reused_token_ids": [100]})
    assert evd.execution.status.value == "ABORTED"


def selection_probe(status):
    def probe(net, requests, timeout):
        out = []
        for request in requests:
            challenged = request["body"].get("code_revision") == request_field_probe.UNAPPROVED_REVISION
            out.append({**completion_response(request), "outcome": status, "body": ""} if challenged else completion_response(request))
        return out
    return probe


@pytest.mark.parametrize("status,allowed,security", [(400, False, "PASS"), (200, False, "UNKNOWN"), (500, False, "UNKNOWN"), (400, True, "UNKNOWN")])
def test_sup08_rejection_before_import_is_the_only_automatic_pass(tmp_path, status, allowed, security):
    evd, result = pipeline(tmp_path, "SUP-08", lambda c: InputValidationExecutor(c, container_probe=selection_probe(status)),
                           {"code_selection_fields": ["code_revision"], "code_selection_allowed": allowed, "rejection_codes": ["400"]})
    assert result.verdicts.security.value == security
    assert evd.observations["code_selection_final_code_verified"].value is False


class HelperDocker:
    """고정 helper 실행과 소유 파일 읽기만 흉내 내는 EXAMPLE 대역."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def exec_bounded(self, container, argv, *, timeout, max_bytes=None, uid=None):
        self.calls.append(container)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        raw = json.dumps(reply).encode()
        return SimpleNamespace(returncode=0, stdout=raw.decode(), stdout_bytes=raw,
                               capture={"complete": True, "truncated": False, "timed_out": False})

    def cleanup(self, timeout=None):
        return []


def usage_snapshot(sha="a" * 64, start=7, fields=("event",), exists=True):
    row = {"path": "/root/usage.jsonl", "exists": exists, "readable": True}
    if exists:
        row.update(bytes=3, truncated=False, sha256=sha, lines=1, fields=list(fields))
    return {"files": [row], "settings": {"VLLM_NO_USAGE_STATS": "1"}, "process_start_ticks": start}


USAGE = {"usage_allowed": False, "usage_report_paths": ["/root/usage.jsonl"], "usage_settings_fresh_process": True}


REAL_USAGE = usage_report.UsageReportExecutor


def usage_run(tmp_path, cid, docker, values, monkeypatch, sink=None):
    probe = lambda n, rs, t: [completion_response(r) for r in rs]
    monkeypatch.setattr(REAL_USAGE, "_sink_rows", lambda self, ctx: sink.pop(0) if sink else None)
    monkeypatch.setattr(usage_report, "UsageReportExecutor", lambda c, docker=None: REAL_USAGE(c, container_probe=probe, docker=docker))
    return pipeline(tmp_path, cid, lambda cfg: BoundaryEgressExecutor({**cfg, "usage_wait_seconds": 0}, docker=docker), values)


def receipt(fields, sink="sink"):
    return {"schema": usage_report.RECEIPT_SCHEMA, "received_monotonic_ns": 1, "sink": sink, "fields": fields, "bytes": 8}


START = {"schema": usage_report.START_SCHEMA}


@pytest.mark.parametrize("after,sink,security", [
    (usage_snapshot(), [[START], [START]], "PASS"),
    (usage_snapshot(sha="b" * 64), [[START], [START]], "FAIL"),
    (usage_snapshot(), [[START], [START, receipt(["event"])]], "FAIL"),
    (usage_snapshot(), None, "UNKNOWN"),
])
def test_obs04_silence_needs_a_send_sensor(tmp_path, monkeypatch, after, sink, security):
    _, result = usage_run(tmp_path, "OBS-04", HelperDocker([usage_snapshot(), after]), USAGE, monkeypatch, deepcopy(sink))
    assert result.verdicts.security.value == security


def test_obs04_existing_old_file_is_not_a_new_report(tmp_path, monkeypatch):
    evd, _ = usage_run(tmp_path, "OBS-04", HelperDocker([usage_snapshot(), usage_snapshot()]), USAGE, monkeypatch)
    assert evd.observations["usage_new_local_reports"].value == []
    assert evd.observations["usage_send_sensor_available"].value is False


def test_obs04_process_replacement_keeps_facts_but_never_passes(tmp_path, monkeypatch):
    evd, result = usage_run(tmp_path, "OBS-04", HelperDocker([usage_snapshot(), usage_snapshot(start=8)]),
                            USAGE, monkeypatch, [[START], [START]])
    assert evd.execution.status.value == "ABORTED" and result.verdicts.security.value == "UNKNOWN"


def test_obs04_later_read_error_keeps_earlier_sink_fact(tmp_path, monkeypatch):
    evd, result = usage_run(tmp_path, "OBS-04", HelperDocker([usage_snapshot(), ValueError("late")]), USAGE, monkeypatch,
                            [[START], [START, receipt(["event"])]])
    assert evd.observations["usage_sink_receipts"].value and result.verdicts.security.value == "FAIL"


def test_obs04_unreadable_path_is_a_gap(tmp_path, monkeypatch):
    after = usage_snapshot()
    after["files"][0] = {"path": "/root/usage.jsonl", "exists": None, "readable": False}
    evd, result = usage_run(tmp_path, "OBS-04", HelperDocker([usage_snapshot(), after]), USAGE, monkeypatch, [[START], [START]])
    assert evd.observations["usage_new_local_reports"].collected is False and result.verdicts.security.value == "UNKNOWN"


ALLOWED = {**USAGE, "usage_allowed": True, "allowed_usage_fields": ["event"], "allowed_usage_sinks": ["sink"]}


@pytest.mark.parametrize("fields,sink_name,security", [(["event"], "sink", "PASS"), (["event", "prompt"], "sink", "FAIL"), (["event"], "other", "FAIL")])
def test_obs05_fields_and_sinks_are_compared_to_the_approved_lists(tmp_path, monkeypatch, fields, sink_name, security):
    _, result = usage_run(tmp_path, "OBS-05", HelperDocker([usage_snapshot(), usage_snapshot()]), ALLOWED, monkeypatch,
                          [[START], [START, receipt(fields, sink_name)]])
    assert result.verdicts.security.value == security


def test_obs05_no_report_observed_never_passes(tmp_path, monkeypatch):
    _, result = usage_run(tmp_path, "OBS-05", HelperDocker([usage_snapshot(), usage_snapshot()]), ALLOWED, monkeypatch, [[START], [START]])
    assert result.verdicts.security.value == "UNKNOWN"


def test_usage_sink_rejects_open_records():
    with pytest.raises(ValueError, match="USAGE_SINK_RECORD_INVALID"):
        usage_report.receipts([{**receipt(["event"]), "body": "raw"}])


def process(pid, comm, uid, shared=()):
    return {"pid": pid, "comm": comm, "uid": uid, "gid": uid, "namespaces": {"pid": "pid:[1]"}, "shared": list(shared), "fd_complete": True}


TRUST = {"worker_isolation_requirement": "independent-uid", "worker_trust_group_markers": ["EngineCore", "Worker"]}


def test_plt06_shared_uid_breaks_an_independent_boundary_and_hashes_resources():
    snapshot = {"processes": [process(1, "EngineCore", "1000", ["/dev/shm/x"]), process(2, "Worker_0", "1000", ["/dev/shm/x"])]}
    result = ipc_trust_scope.project(snapshot, TRUST)
    assert result["violations"] == [{"groups": ["EngineCore", "Worker"], "shared_uid": ["1000"]}]
    assert "/dev/shm/x" not in json.dumps(result)


def test_plt06_shared_trust_group_requirement_has_no_uid_violation():
    snapshot = {"processes": [process(1, "EngineCore", "1000"), process(2, "Worker_0", "1000")]}
    assert ipc_trust_scope.project(snapshot, {**TRUST, "worker_isolation_requirement": "shared-trust-group"})["violations"] == []


def test_plt06_unreadable_or_missing_group_is_incomplete_not_clean():
    snapshot = {"processes": [process(1, "EngineCore", "1000"), {"pid": 2, "fd_complete": False, "comm": None, "uid": None, "namespaces": None, "shared": []}]}
    result = ipc_trust_scope.project(snapshot, TRUST)
    assert result["missing"] == ["Worker"] and result["unreadable"] == 1


def test_plt06_collect_keeps_a_confirmed_violation_and_never_passes():
    docker = HelperDocker([{"processes": [process(1, "EngineCore", "1000"), process(2, "Worker_0", "1000")]}])
    ctx = SimpleNamespace(policy_values=TRUST, target={"engine": {"container": "engine"}}, check=PACK.checks["PLT-06"])
    obs, normal, _, _ = ipc_trust_scope.collect(ctx, docker)
    assert obs["worker_isolation_violations"]["value"] and obs["worker_trust_map_complete"]["value"]
    assert obs["worker_command_impact_verified"]["value"] is False and normal["succeeded"] is None


def test_plt06_collection_failure_is_not_collected():
    ctx = SimpleNamespace(policy_values=TRUST, target={"engine": {"container": "engine"}}, check=PACK.checks["PLT-06"])
    obs, _, _, summary = ipc_trust_scope.collect(ctx, HelperDocker([ValueError("x")]))
    assert obs["worker_isolation_violations"]["collected"] is False and summary["diagnostic_codes"]


SEGMENT = {"name": "engine-collector", "container": "engine", "host": "collector", "port": 4317,
           "server_name": "collector.test", "wrong_server_name": "other.test"}
TRANSPORT = {"record_transport_segments": ["engine-collector"], "record_tls_required_segments": ["engine-collector"]}


def handshake(expected, wrong):
    return {"expected": expected, "wrong": wrong}


OK = {"tcp": True, "tls": True, "version": "TLSv1.3"}
REFUSED_PEER = {"tcp": True, "tls": False, "reason": "peer_rejected"}
PLAIN = {"tcp": True, "tls": False, "reason": "no_tls"}
GONE = {"tcp": False, "tls": False, "reason": "unreachable"}


@pytest.mark.parametrize("reply,security", [
    (handshake(OK, REFUSED_PEER), "UNKNOWN"), (handshake(PLAIN, PLAIN), "FAIL"),
    (handshake(OK, OK), "FAIL"), (handshake(GONE, GONE), "UNKNOWN"),
])
def test_plt09_handshake_results(tmp_path, reply, security):
    evd, result = pipeline(tmp_path, "PLT-09", lambda c: APITransportExecutor(c, docker=HelperDocker([reply])), TRANSPORT,
                           extra_cfg={"record_segments": [SEGMENT]})
    assert result.verdicts.security.value == security
    assert evd.observations["record_transport_sender_verified"].value is False


def test_plt09_segment_mismatch_is_rejected_before_any_probe(tmp_path):
    docker = HelperDocker([])
    evd, _ = pipeline(tmp_path, "PLT-09", lambda c: APITransportExecutor(c, docker=docker), TRANSPORT,
                      extra_cfg={"record_segments": [{**SEGMENT, "name": "other"}]})
    assert evd.execution.status.value == "ABORTED" and docker.calls == []


def test_plt09_later_segment_error_keeps_the_earlier_failure(tmp_path):
    second = {**SEGMENT, "name": "collector-sink", "container": "collector"}
    values = {"record_transport_segments": ["engine-collector", "collector-sink"], "record_tls_required_segments": ["engine-collector", "collector-sink"]}
    evd, result = pipeline(tmp_path, "PLT-09", lambda c: APITransportExecutor(c, docker=HelperDocker([handshake(PLAIN, PLAIN), ValueError("x")])),
                           values, extra_cfg={"record_segments": [SEGMENT, second]})
    assert evd.execution.status.value == "ABORTED" and result.verdicts.security.value == "FAIL"


GAP = {"required_records": ["access"], "collection_deadline_ms": {"access": 100}, "max_clock_uncertainty_ms": {"access": 10},
       "detection_deadline_ms": {"access": 200}, "alert_deadline_ms": {"access": 300}, "collection_session": "ab" * 16}


def gap_row(**kw):
    return {"record": "access", "event_at_ms": 1000, "arrived_at_ms": None, "gap_marked_at_ms": 1100, "alert_at_ms": 1200,
            "dependent_result": "UNKNOWN", "clock_uncertainty_ms": 1, **kw}


@pytest.mark.parametrize("row,reasons", [
    (gap_row(arrived_at_ms=1050), []), (gap_row(), []),
    (gap_row(gap_marked_at_ms=None), ["gap_not_marked_in_time"]),
    (gap_row(alert_at_ms=1500), ["alert_not_raised_in_time"]),
    (gap_row(dependent_result="PASS"), ["dependent_test_passed_without_record"]),
])
def test_obs11_evaluate_deadlines_alerts_and_dependent_verdicts(row, reasons):
    result = log_collection_gap.evaluate({"events": [row]}, GAP)
    assert [v["reason"] for v in result["violations"]] == reasons


def test_obs11_clock_uncertainty_and_missing_record_are_not_clean():
    assert log_collection_gap.evaluate({"events": [gap_row(clock_uncertainty_ms=99)]}, GAP)["unverified"] == ["access"]
    assert log_collection_gap.evaluate({"events": [{**gap_row(), "record": "other"}]}, GAP)["missing"] == ["access"]


def test_obs11_record_shape_and_binding_are_closed():
    good = {"schema": log_collection_gap.SCHEMA, "session": GAP["collection_session"], "environment_ref": {"id": 1}, "events": [gap_row()]}
    log_collection_gap.validate_record(good, environment_ref={"id": 1}, session=GAP["collection_session"])
    for bad in ({**good, "extra": 1}, {**good, "environment_ref": {"id": 2}}, {**good, "events": [{**gap_row(), "raw": "x"}]}):
        with pytest.raises(ValueError, match="COLLECTION_GAP_RECORD_INVALID"):
            log_collection_gap.validate_record(bad, environment_ref={"id": 1}, session=GAP["collection_session"])


def gap_run(tmp_path, monkeypatch, packet):
    def read(docker, target_value, **kwargs):
        if packet is None:
            raise ValueError("missing")
        return packet, {"capture_sha256": "c" * 64}
    monkeypatch.setattr(log_collection_gap, "read_owned_json", read)
    probe = lambda n, rs, t: [completion_response(r) for r in rs]
    return pipeline(tmp_path, "OBS-11", lambda c: LogIntegrityExecutor(c, container_probe=probe, docker=object()), GAP,
                    extra_cfg={"collection_status": {"container": "collector", "image": "i@sha256:" + "0" * 64}})


def test_obs11_declared_late_detection_fails_and_clean_record_stays_unknown(tmp_path, monkeypatch):
    def packet(row):
        env = environment(model={"id": "test"})
        return {"schema": log_collection_gap.SCHEMA, "session": GAP["collection_session"],
                "environment_ref": env.ref().model_dump(mode="json"), "events": [row]}
    _, result = gap_run(tmp_path, monkeypatch, packet(gap_row(gap_marked_at_ms=None)))
    assert result.verdicts.security.value == "FAIL"
    (tmp_path / "clean").mkdir()
    _, result = gap_run(tmp_path / "clean", monkeypatch, packet(gap_row()))
    assert result.verdicts.security.value == "UNKNOWN"


def test_obs11_unavailable_status_record_is_unknown(tmp_path, monkeypatch):
    evd, result = gap_run(tmp_path, monkeypatch, None)
    assert evd.observations["collection_gap_violations"].collected is False and result.verdicts.security.value == "UNKNOWN"


@pytest.mark.parametrize("check_id,impl", [("REQ-08", "real"), ("SUP-08", "real"), ("OBS-04", "real"), ("OBS-05", "real"),
                                           ("PLT-06", "real"), ("PLT-09", "real"), ("OBS-11", "real")])
def test_extension_is_reachable_from_the_registered_factory(check_id, impl):
    name = PACK.checks[check_id].executor
    assert registry.resolve_executor(name, impl) is registry.EXECUTORS[f"{name}:{impl}"]
    assert getattr(PACK.checks[check_id], "collection_profile", None)
    assert PACK.checks[check_id].judge.params["pass_when_all"] and PACK.checks[check_id].judge.params["fail_when_any"]


def test_kubernetes_implementation_is_not_silently_substituted_for_new_profiles():
    from guard.executors.kubernetes_egress_product import KubernetesEgressProductExecutor
    executor = KubernetesEgressProductExecutor({"actor_spec": {}, "connect_timeout": 1, "total_timeout": 2})
    with pytest.raises(ValueError):
        executor.validate_policy(PACK.checks["OBS-04"], USAGE)
