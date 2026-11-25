"""기존 실행기 확장 7개: 정상·위반·누락·지원 불일치·후속 오류와 선정→EVD→RES→RPT→저장 팩 재판정."""
from __future__ import annotations

from base64 import b64encode
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import (ipc_trust_scope, log_collection_gap, owned_capture, record_transport, registry, request_field_probe,
                             usage_report)
from guard.executors.api_transport import APITransportExecutor
from guard.executors.input_validation import InputValidationExecutor
from guard.executors.log_integrity import LogIntegrityExecutor
from guard.executors.unix_ipc import UnixIPCExecutor
from guard.models import ExecStatus, load_pack, sha256_hex
from guard.runtime.docker import CmdResult
from guard.store import Store
from guard.store.packs import get_pack, put_pack
from tests.run_binding_support import run_table, split
from tests.builders import completion_response, environment, policy

ROOT = Path(__file__).resolve().parents[1]
from tests.published_pack38 import original as published_pack38
PACK = published_pack38()
FIXTURE_TOKENS = [11, 12, 13]
CONTROL_TOKENS = [5, 6]


def target(tmp_path):
    value = {"lab": True, "run_id": "run", "engine": {"trusted_url": "http://engine:8000", "container": "engine"},
             "origins": {"trusted": {"network": "inside"}}}
    path = tmp_path / "target.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return value, path


def pipeline(tmp_path, cid, factory, values, *, extra_cfg=None, pack=PACK):
    _, path = target(tmp_path)
    store = Store(tmp_path / "store")
    env = environment(model={"id": "test"})
    ref = put_pack(store, pack.path)
    # @36: stage-2 run-preparation values (e.g. OBS-11 collection_session) come from [run_binding.<CHECK>].
    policy_values = split(pack.checks[cid], values)[0]
    pol = policy(ref, scope={"include": [cid]}, values={cid: policy_values})
    store.put(env); store.put(pol)
    plan = select(pack, [env], pol); store.put(plan)
    name = pack.checks[cid].executor
    key = name + ":sim"
    registry.EXECUTORS[key] = lambda cfg: factory({"target": str(path), **(extra_cfg or {})})
    try:
        evd, = run_plan(store, pack, plan, RunConfig(executors={name: "sim"}, adapter="vllm",
                                                     raw=run_table(pack.checks[cid], values)))
    finally:
        registry.EXECUTORS.pop(key)
    result, = judge_plan(store, pack, plan, env.created_at)
    report = build_report(store, pack, plan, env.created_at)
    assert cid in [row.check_id for row in report.by_check]
    saved = get_pack(store, ref)
    again, = judge_plan(store, saved, plan, env.created_at)
    assert again.verdicts == result.verdicts and saved.ref() == pack.ref()
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
                           {"code_selection_fields": ["code_revision"], "code_selection_allowed": allowed, "rejection_codes": ["400"]},
                           pack=load_pack(ROOT / 'reference/pack-seeds/PACK-BASELINE-33'))
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


def scope_snapshot(**options):
    result = usage_snapshot(**options)
    result["files"][0]["generation_sha256"] = "c" * 64
    return result


class UsageAppendDocker(HelperDocker):
    """Execute only the fixed read program on a fixture file with saved process metadata."""

    def __init__(self, path, before, after, monkeypatch, *, rotate=False):
        super().__init__([before, after])
        self.path, self.monkeypatch, self.rotate = path, monkeypatch, rotate
        self.snapshots = []

    def exec_bounded(self, container, argv, *, timeout, max_bytes=None, uid=None):
        assert argv[:3] == ["python3", "-c", usage_report._SCOPE_READER]
        assert argv[5:] == [str(self.path)]
        expected = usage_report._report_prefixes(self.snapshots[0], argv[5:]) if self.snapshots else {}
        assert json.loads(argv[4]) == expected
        raw = self.replies.pop(0)
        if self.rotate and self.snapshots:
            self.path.rename(self.path.with_suffix(".original"))
        if raw is not None:
            self.path.write_bytes(raw)

        def process_file(name, mode="r"):
            if name == "/proc/1/environ" and mode == "rb":
                return io.BytesIO(b"VLLM_NO_USAGE_STATS=0\0")
            assert name == "/proc/1/stat" and mode == "r"
            return io.StringIO("1 (fixture) " + "0 " * 19 + "7")

        stdout = io.StringIO()
        with self.monkeypatch.context() as fixture, redirect_stdout(stdout):
            fixture.setattr(sys, "argv", ["reader", *argv[3:]])
            exec(compile(usage_report._SCOPE_READER, "owned usage scope fixture", "exec"), {"open": process_file})
        self.calls.append(container)
        self.snapshots.append(json.loads(stdout.getvalue()))
        return SimpleNamespace(returncode=0, stdout_bytes=stdout.getvalue().encode(),
                               capture={"complete": True, "truncated": False, "timed_out": False})


USAGE = {"usage_allowed": False, "usage_report_paths": ["/root/usage.jsonl"], "usage_settings_fresh_process": True}


REAL_USAGE = usage_report.UsageReportExecutor


def usage_run(tmp_path, cid, docker, values, monkeypatch, sink=None, *, pack=PACK):
    probe = lambda n, rs, t: [completion_response(r) for r in rs]
    extra_cfg = _attach_sink_fixture(docker, sink, monkeypatch) if sink is not None else None
    monkeypatch.setattr(usage_report, "UsageReportExecutor", lambda c, docker=None: REAL_USAGE(c, container_probe=probe, docker=docker))
    return pipeline(tmp_path, cid, lambda cfg: usage_report.BoundaryEgressDispatch({**cfg, "usage_wait_seconds": 0}, docker=docker),
                    values, extra_cfg=extra_cfg, pack=pack)


def receipt(fields, sink="sink"):
    return {"schema": usage_report.RECEIPT_SCHEMA, "received_monotonic_ns": 1, "sink": sink, "fields": fields, "bytes": 8}


START = {"schema": usage_report.START_SCHEMA}


def _sink_fixture(rows, *, owner="a" * 64, inode=1, closed=True, reformatted=False):
    raw = b"\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() for row in rows)
    if reformatted:
        raw = b" " + raw
    if closed:
        raw += b"\n"
    return raw, {"device": 1, "inode": inode, "size": len(raw), "mtime_ns": len(raw), "ctime_ns": len(raw)}, owner


def _attach_sink_fixture(docker, captures, monkeypatch):
    snapshots = [_sink_fixture(rows) if type(rows) is list else rows for rows in captures]
    source = {"container": "fixture-sink", "image": "fixture@sha256:" + "b" * 64, "path": "/guard-usage/receipts.jsonl"}
    engine_exec = docker.exec_bounded
    inspected = 0

    def inspect_json(container, *, timeout):
        nonlocal inspected
        assert container == source["container"]
        _, _, owner = snapshots[inspected // 2]
        inspected += 1
        return {"Name": "/" + container, "Id": owner, "Image": "sha256:" + "c" * 64,
                "Config": {"Image": source["image"], "Labels": {"guard.run": "run"}},
                "State": {"Running": True, "StartedAt": "2026-11-19T00:00:00Z"}}

    def exec_bounded(container, argv, **kwargs):
        if container != source["container"]:
            return engine_exec(container, argv, **kwargs)
        assert inspected % 2 == 1
        assert argv == ["python3", "-c", owned_capture._FILE_READER, source["path"], str(owned_capture.MAX_BYTES)]
        assert kwargs["max_bytes"] == 2 * owned_capture.MAX_BYTES
        raw, identity, _ = snapshots[inspected // 2]
        packet = {"schema": "guard-container-file/2", "source_complete": True,
                  "file_identity_stable": True, "truncated": False, "nofollow_components": True, "regular_file": True,
                  "raw_base64": b64encode(raw).decode(), "raw_retained_bytes": len(raw), "raw_retained_sha256": sha256_hex(raw),
                  "file_before": identity, "file_after": identity, "path_after": identity}
        return SimpleNamespace(returncode=0, stdout=json.dumps(packet),
                               capture={"complete": True, "bounded_at_source": True, "truncated": False, "timed_out": False})

    monkeypatch.setattr(docker, "inspect_json", inspect_json, raising=False)
    monkeypatch.setattr(docker, "exec_bounded", exec_bounded)
    return {"usage_sink": source}


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
    _, result = usage_run(tmp_path, "OBS-05", HelperDocker([scope_snapshot(), scope_snapshot()]), ALLOWED, monkeypatch,
                          [[START], [START, receipt(fields, sink_name)]])
    assert result.verdicts.security.value == security


@pytest.mark.parametrize("after", [scope_snapshot(), scope_snapshot(sha="b" * 64)])
def test_obs05_no_sink_arrival_never_passes(tmp_path, monkeypatch, after):
    evd, result = usage_run(tmp_path, "OBS-05", HelperDocker([scope_snapshot(), after]),
                            ALLOWED, monkeypatch, [[START], [START]])
    assert evd.observations["usage_send_sensor_available"].value is True
    assert evd.observations["usage_report_observed"].collected is True
    assert evd.observations["usage_report_observed"].value is False
    assert result.verdicts.security.value == "UNKNOWN"


def test_obs05_denied_policy_preserves_unchanged_receipts_without_local_attribution(tmp_path, monkeypatch):
    old = usage_snapshot(fields=("historical_only",))
    values = {**ALLOWED, "usage_allowed": False, "allowed_usage_fields": [], "allowed_usage_sinks": []}
    evd, result = usage_run(tmp_path, "OBS-05", HelperDocker([old, deepcopy(old)]), values, monkeypatch,
                            [[START], [START]])
    assert evd.execution.status is ExecStatus.COMPLETED
    assert evd.observations["usage_policy_forbidden"].value is True
    assert evd.observations["usage_sink_receipts"].value == []
    assert evd.observations["usage_fields_seen"].value == []
    assert not evd.observations["usage_new_local_reports"].collected
    assert result.verdicts.security.value == "UNKNOWN"


@pytest.mark.parametrize("local_fields,security", [(("event",), "UNKNOWN"), (("event", "extra"), "FAIL")])
def test_obs05_changed_sink_prefix_keeps_only_qualified_local_fields(tmp_path, monkeypatch, local_fields, security):
    path = tmp_path / "usage.jsonl"
    before = b'{"event":0,"historical_only":"fixture-before"}\n'
    after = before + json.dumps(dict.fromkeys(local_fields, 1)).encode() + b"\n"
    sink = [[START, receipt(["event"])], [START, receipt(["changed"]), receipt(["unverified"], "other")]]
    evd, result = usage_run(tmp_path, "OBS-05", UsageAppendDocker(path, before, after, monkeypatch),
                            {**ALLOWED, "usage_report_paths": [str(path)]}, monkeypatch, sink)
    assert evd.observations["usage_send_sensor_available"].value is False
    assert evd.observations["usage_sink_receipts"].collected is False
    assert evd.observations["usage_report_observed"].collected is False
    assert evd.observations["usage_fields_seen"].value == list(local_fields)
    assert result.verdicts.security.value == security


@pytest.mark.parametrize("mode", ["append", "reformatted", "file-replaced", "owner-replaced", "unclosed"])
def test_obs05_sink_arrival_requires_the_same_closed_byte_prefix(tmp_path, monkeypatch, mode):
    baseline = [START, receipt(["event"])]
    new_receipt = {**receipt(["event"]), "received_monotonic_ns": 2}
    before = _sink_fixture(baseline)
    after = _sink_fixture([*baseline, new_receipt],
                          inode=2 if mode == "file-replaced" else 1,
                          owner=("d" if mode == "owner-replaced" else "a") * 64,
                          closed=mode != "unclosed", reformatted=mode == "reformatted")
    if mode == "reformatted":
        assert [json.loads(line) for line in after[0].splitlines()[:len(baseline)]] == baseline
    evd, result = usage_run(tmp_path, "OBS-05", HelperDocker([scope_snapshot(), scope_snapshot()]), ALLOWED,
                            monkeypatch, [before, after])
    received = evd.observations["usage_sink_receipts"]
    assert received.collected is (mode == "append")
    assert evd.observations["usage_report_observed"].collected is (mode == "append")
    assert result.verdicts.security.value == ("PASS" if mode == "append" else "UNKNOWN")
    if mode == "append":
        assert received.value == [new_receipt]


@pytest.mark.parametrize("timestamps,security", [
    ([2, 2], "PASS"),
    ([2, 3, 3], "PASS"),
    ([2, 1], "UNKNOWN"),
    ([2, 3, 2], "UNKNOWN"),
    ([2, -1], "UNKNOWN"),
])
def test_obs05_sink_receipts_follow_the_same_monotonic_clock(tmp_path, monkeypatch, timestamps, security):
    rows = [{**receipt(["event"]), "received_monotonic_ns": value} for value in timestamps]
    evd, result = usage_run(tmp_path, "OBS-05", HelperDocker([scope_snapshot(), scope_snapshot()]), ALLOWED,
                            monkeypatch, [[START, rows[0]], [START, *rows]])
    collected = security == "PASS"
    assert evd.observations["usage_sink_receipts"].collected is collected
    assert evd.observations["usage_report_observed"].collected is collected
    if collected:
        assert evd.observations["usage_sink_receipts"].value == rows[1:]
    assert result.verdicts.security.value == security


@pytest.mark.parametrize("mode", [
    "append", "zero-append", "created-empty", "new-file", "empty-before",
    "changed-prefix", "rotated-same-prefix", "truncated", "partial-before", "partial-after",
])
def test_obs05_new_fields_require_a_stable_and_complete_append(tmp_path, monkeypatch, mode):
    path = tmp_path / "usage.jsonl"
    historical = b'{"retired_field":"historical-value"}\n'
    ordinary = b'{"event":"fixture-current-value"}\n'
    before, after = {
        "append": (historical, historical + ordinary),
        "zero-append": (historical, historical),
        "created-empty": (None, b""),
        "new-file": (None, ordinary),
        "empty-before": (b"", ordinary),
        "changed-prefix": (historical, historical.replace(b"retired_field", b"rotated_field") + ordinary),
        "rotated-same-prefix": (historical, historical + ordinary),
        "truncated": (historical, historical + ordinary * (4194304 // len(ordinary) + 1)),
        "partial-before": (historical.rstrip(b"\n"), historical + ordinary),
        "partial-after": (historical, historical + ordinary.rstrip(b"\n")),
    }[mode]
    docker = UsageAppendDocker(path, before, after, monkeypatch, rotate=mode == "rotated-same-prefix")
    pack = load_pack(ROOT / "packs/PACK-BASELINE")
    values = {**ALLOWED, "usage_report_paths": [str(path)],
              "allowed_usage_fields": ["event", "received_independently"]}
    arrived = [receipt(["received_independently"])]
    sink = [[START], [START, *arrived]]
    evd, res = usage_run(tmp_path, "OBS-05", docker, values, monkeypatch, sink, pack=pack)
    assert len(docker.calls) == 2 and not docker.replies
    assert evd.observations["usage_sink_receipts"].value == arrived
    assert evd.observations["usage_send_sensor_available"].value is True
    completed = mode in {"append", "zero-append", "created-empty", "new-file", "empty-before"}
    appended = mode in {"append", "new-file", "empty-before"}
    assert evd.execution.status is (ExecStatus.COMPLETED if completed else ExecStatus.ABORTED)
    local = evd.observations["usage_new_local_reports"]
    assert local.collected is completed
    assert evd.observations["usage_fields_seen"].value == (["event", "received_independently"]
                                                         if appended else ["received_independently"])
    if appended:
        report, = local.value
        assert report["fields"] == ["event"] and report["sha256"] == sha256_hex(after)
        assert report["prefix_bytes"] == len(before or b"") and report["prefix_sha256"] == sha256_hex(before or b"")
        assert report["file_generation_sha256"] == docker.snapshots[1]["files"][0]["generation_sha256"]
    else:
        assert local.value == ([] if completed else None)
    assert res.verdicts.security.value == ("PASS" if completed else "UNKNOWN")
    for artifact in evd.artifacts:
        saved = (tmp_path / "store" / artifact.path).read_bytes()
        assert all(value not in saved for value in (b"retired_field", b"historical-value", b"fixture-current-value"))


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
    (handshake(OK, OK), "FAIL"), (handshake(GONE, GONE), "UNKNOWN"), (handshake(OK, GONE), "UNKNOWN"),
])
def test_plt09_handshake_results(tmp_path, reply, security):
    evd, result = pipeline(tmp_path, "PLT-09", lambda c: APITransportExecutor(c, docker=HelperDocker([reply])), TRANSPORT,
                           extra_cfg={"record_segments": [SEGMENT]})
    assert result.verdicts.security.value == security
    assert evd.execution.status.value == "COMPLETED"
    assert evd.observations["record_transport_segments_tested"].value == (
        [SEGMENT["name"]] if reply["expected"]["tcp"] and reply["wrong"]["tcp"] else [])
    assert evd.observations["record_transport_unreachable"].value == (
        [] if reply["expected"]["tcp"] and reply["wrong"]["tcp"] else [SEGMENT["name"]])
    if reply == handshake(OK, GONE):
        trial, = evd.observations["record_transport_trials"].value
        assert trial["state"] == "protected" and trial["wrong_peer_state"] == "unreachable"
    assert evd.observations["record_transport_sender_verified"].value is False


@pytest.mark.parametrize("wrong_peer,security", [(REFUSED_PEER, "UNKNOWN"), (OK, "FAIL")])
def test_plt09_expected_peer_rejection_is_not_an_unprotected_transfer(tmp_path, wrong_peer, security):
    docker = HelperDocker([handshake(REFUSED_PEER, wrong_peer)])
    evd, result = pipeline(tmp_path, "PLT-09", lambda c: APITransportExecutor(c, docker=docker), TRANSPORT,
                           extra_cfg={"record_segments": [SEGMENT]})
    assert evd.observations["record_transport_trials"].value[0]["state"] == "peer_rejected"
    assert evd.observations["record_transport_unprotected"].value == []
    assert evd.observations["record_transport_sender_verified"].value is False
    assert evd.normal_control.succeeded is None
    assert result.verdicts.security.value == security


@pytest.mark.parametrize("reply,expected_state,wrong_state,security", [
    (handshake(OK, {**REFUSED_PEER, "tls": "false"}), "protected", None, "UNKNOWN"),
    ({"expected": OK}, "protected", None, "UNKNOWN"),
    (handshake({**PLAIN, "tcp": 1}, REFUSED_PEER), None, "peer_rejected", "UNKNOWN"),
    (handshake({**GONE, "tcp": True}, REFUSED_PEER), None, "peer_rejected", "UNKNOWN"),
    (handshake({**OK, "version": None}, REFUSED_PEER), None, "peer_rejected", "UNKNOWN"),
    ({"expected": PLAIN}, "no_tls", None, "FAIL"),
])
def test_plt09_partial_receipt_preserves_only_valid_legs(tmp_path, reply, expected_state, wrong_state, security):
    docker = HelperDocker([reply])
    evd, result = pipeline(tmp_path, "PLT-09", lambda c: APITransportExecutor(c, docker=docker), TRANSPORT,
                           extra_cfg={"record_segments": [SEGMENT]})
    assert evd.data_origin.value == "EXAMPLE"
    assert evd.execution.status.value == "ABORTED"
    assert evd.execution.abort_reason == "RECORD_TRANSPORT_SEGMENT_UNAVAILABLE"
    trial, = evd.observations["record_transport_trials"].value
    assert trial["state"] == expected_state
    assert trial["wrong_peer_state"] == wrong_state
    assert trial["tls_version"] == ("TLSv1.3" if expected_state == "protected" else None)
    assert evd.observations["record_transport_segments_tested"].value == []
    assert evd.observations["record_transport_wrong_peer_accepted"].value == []
    assert evd.observations["record_transport_unreachable"].value == []
    assert evd.observations["record_transport_sender_verified"].value is False
    assert evd.normal_control.succeeded is None
    assert result.verdicts.security.value == security


def test_plt09_segment_mismatch_is_rejected_before_any_probe(tmp_path):
    docker = HelperDocker([])
    evd, _ = pipeline(tmp_path, "PLT-09", lambda c: APITransportExecutor(c, docker=docker), TRANSPORT,
                      extra_cfg={"record_segments": [{**SEGMENT, "name": "other"}]})
    assert evd.execution.status.value == "ABORTED" and docker.calls == []


@pytest.mark.parametrize("duplicate_name", ["engine-collector", "collector-sink"])
def test_plt09_requires_one_configured_sender_per_policy_segment(tmp_path, duplicate_name):
    docker = HelperDocker([])
    names = ["engine-collector", "collector-sink"]
    values = {"record_transport_segments": names, "record_tls_required_segments": names}
    segments = [SEGMENT, {**SEGMENT, "name": "collector-sink", "container": "collector"},
                {**SEGMENT, "name": duplicate_name, "container": "other"}]
    evd, result = pipeline(tmp_path, "PLT-09", lambda c: APITransportExecutor(c, docker=docker), values,
                           extra_cfg={"record_segments": segments})
    assert evd.execution.status.value == "ABORTED" and docker.calls == []
    assert result.verdicts.security.value == "UNKNOWN"


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


@pytest.mark.parametrize("row,normal,unverified,reasons", [
    (gap_row(arrived_at_ms=1098, clock_uncertainty_ms=2), ["access"], [], []),
    (gap_row(arrived_at_ms=1100, clock_uncertainty_ms=2, dependent_result="PASS"), [], ["access"], []),
    (gap_row(arrived_at_ms=1101, clock_uncertainty_ms=2, dependent_result="PASS"), [], ["access"], []),
    (gap_row(arrived_at_ms=1103, clock_uncertainty_ms=2, dependent_result="PASS"),
     [], [], ["dependent_test_passed_without_record"]),
    (gap_row(gap_marked_at_ms=1201, alert_at_ms=1303, clock_uncertainty_ms=2),
     [], ["access"], ["alert_not_raised_in_time"]),
    (gap_row(gap_marked_at_ms=1203, alert_at_ms=1299, clock_uncertainty_ms=2),
     [], ["access"], ["gap_not_marked_in_time"]),
    (gap_row(arrived_at_ms=1001, clock_uncertainty_ms=2), [], ["access"], []),
], ids=["arrival-upper-bound", "arrival-at-limit", "arrival-just-after-limit", "arrival-confirmed-late",
        "detection-uncertain-alert-late", "detection-late-alert-uncertain", "arrival-order-uncertain"])
def test_obs11_keeps_deadlines_uncertain_within_clock_error(row, normal, unverified, reasons):
    result = log_collection_gap.evaluate({"events": [row]}, GAP)
    assert result["normal"] == normal
    assert result["unverified"] == unverified
    assert result["violations"] == [{"record": "access", "reason": reason} for reason in reasons]


@pytest.mark.parametrize("row", [
    gap_row(arrived_at_ms=999), gap_row(gap_marked_at_ms=999), gap_row(alert_at_ms=999),
    gap_row(clock_uncertainty_ms=-1),
])
def test_obs11_impossible_event_chronology_is_unverified(row):
    result = log_collection_gap.evaluate({"events": [row]}, GAP)
    assert result == {"violations": [], "unverified": ["access"], "missing": [], "normal": []}


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


def test_obs11_unverified_timeline_preserves_an_independent_observed_violation(tmp_path, monkeypatch):
    env = environment(model={"id": "test"})
    packet = {"schema": log_collection_gap.SCHEMA, "session": GAP["collection_session"],
              "environment_ref": env.ref().model_dump(mode="json"),
              "events": [gap_row(arrived_at_ms=999), gap_row(gap_marked_at_ms=None)]}
    evidence, result = gap_run(tmp_path, monkeypatch, packet)
    assert evidence.observations["collection_records_unverified"].value == ["access"]
    assert evidence.observations["collection_normal_arrivals"].value == []
    assert evidence.observations["collection_gap_violations"].value == [
        {"record": "access", "reason": "gap_not_marked_in_time"}]
    assert result.verdicts.security.value == "FAIL"


def test_obs11_docker_retains_selected_status_timestamps_and_original_byte_hash(tmp_path):
    from guard.models import canonical_json, sha256_hex
    from tests.test_native_observations import CaptureWorld, IMAGE

    class Collector(CaptureWorld):
        def inspect_json(self, container, timeout):
            detail = super().inspect_json(container, timeout)
            detail["Config"]["Labels"]["guard.run"] = "run"
            return detail

    env = environment(model={"id": "test"})
    normal = gap_row(arrived_at_ms=1050)
    unverified = gap_row(arrived_at_ms=999)
    unselected = gap_row(record="EXAMPLE-unselected", alert_at_ms=9486)
    packet = {"schema": log_collection_gap.SCHEMA, "session": GAP["collection_session"],
              "environment_ref": env.ref().model_dump(mode="json"), "events": [normal, unverified, unselected]}
    collector = Collector(packet)

    def unexpected_probe(*args):
        pytest.fail("collector status receipt should not send requests")

    evidence, result = pipeline(tmp_path, "OBS-11",
        lambda cfg: LogIntegrityExecutor(cfg, container_probe=unexpected_probe, docker=collector),
        GAP, extra_cfg={"collection_status": {"container": "collector", "image": IMAGE}})
    artifact, = evidence.artifacts
    saved = (tmp_path / "store" / artifact.path).read_bytes()
    summary = json.loads(saved.splitlines()[-1])

    assert collector.calls == ["inspect", "read", "inspect"]
    assert summary["selected_record_timeline"] == [normal, unverified]
    assert summary["file_sha256"] == sha256_hex(json.dumps(packet).encode())
    assert summary["record_sha256"] == sha256_hex(canonical_json(packet)) != summary["file_sha256"]
    assert artifact.sha256 == sha256_hex(saved) and b"EXAMPLE-unselected" not in saved
    assert evidence.observations["collection_normal_arrivals"].value == ["access"]
    assert evidence.observations["collection_records_unverified"].value == ["access"]
    assert evidence.observations["collection_producer_authenticated"].value is False
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
