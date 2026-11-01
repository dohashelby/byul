"""PLT07 고정 공통 method를 통한 전용 CPU EXAMPLE 소비. 실제 socket I/O 없음."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import base64
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from guard.core.judge import judge_evidence
from guard.executors.kubernetes_boundary_egress import collect_kubernetes_boundary_egress, PROFILE
from guard.models import Verdict, canonical_json, sha256_hex
from guard.runtime import kubernetes_egress as common
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
from tests.builders import T0, baseline_pack, evidence, policy
from tests.test_kubernetes_egress_common import World, inputs, ACTOR, NONCE, PRIVATE


class EgressWorld(World):
    def __init__(self, target, receiver, mode):
        super().__init__(target, receiver, mode if mode in {
            "later-transport", "cleanup", "raw", "groups", "restart", "missing-sender", "receiver-command"} else "normal")
        self.case, self.initial = mode, None

    def runner(self, argv, **kw):
        result = super().runner(argv, **kw)
        if "exec" not in argv:
            return result
        request = json.loads(kw["input_bytes"])
        mode = request["mode"]
        after = mode == "receiver" and len(self.execs) == 3
        v = json.loads(result["stdout_bytes"])
        if mode == "receiver":
            f = v["file"]
            rows = [json.loads(row) for row in base64.b64decode(f["raw_base64"]).splitlines()]
            if not after:
                self.initial = deepcopy(rows[0])
            else:
                rows[0] = deepcopy(self.initial)
                if self.case in {"denied", "timeout", "transport-error"}:
                    rows = rows[:1]
                if self.case == "prefix": rows[0]["socket_inode"] += 1
                if self.case == "receipt-correlation": rows[-1]["correlation_sha256"] = "0" * 64
                if self.case == "receipt-peer": rows[-1]["peer"]["port"] += 1
                if self.case == "receipt-bytes": rows[-1]["received_bytes"] = 31
                if self.case == "duplicate": rows.append(deepcopy(rows[-1]))
                if self.case == "receiver-restart":
                    for row in rows: row["identity"]["start_time_ticks"] += 1
                    v["receiver_process"]["identity"]["start_time_ticks"] += 1
                if self.case == "receiver-command-after": v["receiver_process"]["command_sha256"] = "0" * 64
                if self.case == "receiver-listener": v["receiver_process"]["socket_inodes"] = [89]
                if self.case == "receiver-is-collector":
                    for k in ("context_before", "context_after"): v[k]["pid"] = 123
                if self.case == "reader-domain":
                    for k in ("context_before", "context_after"): v[k]["boot_sha256"] = "f" * 64
                if self.case == "new-reader":
                    for k in ("context_before", "context_after"):
                        v[k]["pid"] += 5
                        v[k]["start_ticks"] += 5
                if self.case == "missing-after": v["file"] = None
            raw = b"".join(canonical_json(row) + b"\n" for row in rows)
            f.update(raw_base64=base64.b64encode(raw).decode(), raw_retained_bytes=len(raw), raw_retained_sha256=sha256_hex(raw))
            for k in ("file_before", "file_after", "path_after"):
                f[k]["size"] = len(raw)
                if after and self.case == "file-replaced": f[k]["inode"] += 1
        else:
            s = v["sender"]
            if s is not None:
                if self.case in {"denied", "timeout", "transport-error"}:
                    s.update(connected=False, marker_sent=False, ack_verified=False, sent_bytes=0, received_bytes=0,
                        connected_at=None, marker_sent_at=None, outcome={"denied": "connect_refused", "timeout": "timeout",
                        "transport-error": "transport_error"}[self.case])
                if self.case == "completed": s.update(ack_verified=True, received_bytes=65, outcome="completed", deadline_guard_verified=True)
                if self.case == "ack-mismatch": s.update(outcome="ack_mismatch", received_bytes=65)
                if self.case == "no-final-clock": s.update(observed_at=None, deadline_guard_verified=False, outcome="source_clock_unavailable")
                if self.case == "sender-restart": s["identity_after"]["start_time_ticks"] += 1
                if self.case == "source-ip": s["local"]["ip"] = "10.42.0.99"
                if self.case == "sender-uid": s["identity_before"]["uid"] = s["identity_after"]["uid"] = 2999
                if self.case == "source-guard": s["connection_guard_verified"] = False
                if self.case == "clock-outside": s["connected_at"] = (datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat()
                if self.case == "gid":
                    for k in ("context_before", "context_after"): v[k]["gids"] = [2999] * 4
                if self.case == "actor-restart": v["context_after"]["start_ticks"] += 1
                if self.case == "missing-context": v["context_after"] = None
        result["stdout_bytes"] = canonical_json(v)
        return result


def context(monkeypatch, mode):
    env, target, receiver, values = inputs()
    # The common method fixture deliberately did not adjudicate source IP.
    # Bind its explicit sender endpoint to this dedicated fixture's actual
    # API Pod IP and refresh both declared target digests before collection.
    target["kubernetes"]["engine"]["pod_ip"] = "10.42.0.10"
    target["engine"]["trusted_url"] = f"http://10.42.0.10:{urlsplit(target['engine']['trusted_url']).port}"
    receiver["source_target_sha256"] = sha256_hex(canonical_json(target))
    values["source_target_sha256"] = receiver["source_target_sha256"]
    values["receiver_target_sha256"] = sha256_hex(canonical_json(receiver))
    world = EgressWorld(target, receiver, mode)
    monkeypatch.setattr(common.secrets, "token_hex", lambda n: NONCE)
    t = KubernetesHTTP(target, env, duration=30, runner=world.runner, profile=REQUEST_BINDING_PROFILE)
    check = baseline_pack().checks["PLT-07"].model_copy(update={"collection_profile": PROFILE})
    ctx = SimpleNamespace(check=check, env=env, target=target, cprobe=t, policy_values=values)
    return ctx, receiver, world


def consume(ctx, receiver):
    return collect_kubernetes_boundary_egress(ctx, receiver_target=receiver, actor_spec=deepcopy(ACTOR),
        connect_timeout=.1, total_timeout=10)


def judged(ctx, obs, normal):
    return judge_evidence(ctx.check, evidence(check_id="PLT-07", observations=obs, normal=normal),
        policy(baseline_pack().ref(), values={"PLT-07": ctx.policy_values}), T0)


@pytest.mark.parametrize("mode", ["normal", "completed", "ack-mismatch", "no-final-clock", "new-reader", "later-transport", "cleanup"])
def test_verified_connection_keeps_independent_fail_through_auxiliary_failure(monkeypatch, mode):
    ctx, receiver, world = context(monkeypatch, mode)
    before = deepcopy((ctx.target, ctx.policy_values, receiver, ACTOR))
    obs, normal, challenges, summary = consume(ctx, receiver)
    assert len(obs["forbidden_egress_successes"]["value"]) == 1
    trial, = obs["egress_trials"]["value"]
    assert trial["verified"] and trial["actor_groups"] == [55, 2000]
    assert trial["sender_identity"]["pid"] == 999 and trial["receiver_identity"]["pid"] == 123
    assert judged(ctx, obs, normal).verdicts.security == Verdict.FAIL
    assert judged(ctx, obs, normal).verdicts.functional == judged(ctx, obs, normal).verdicts.performance == Verdict.UNKNOWN
    assert normal == {"attempted": False, "succeeded": None, "detail": "EGRESS_NORMAL_CONTROL_UNSUPPORTED"}
    assert obs["egress_scope_complete"] == {"value": False, "collected": True}
    assert not summary["worker_subject_verified"] and all(not c["attempted"] for c in challenges)
    assert world.execs == ["receiver", "sender", "receiver"]
    assert (ctx.target, ctx.policy_values, receiver, ACTOR) == before
    serialized = json.dumps((obs, normal, summary))
    assert PRIVATE not in serialized and NONCE not in serialized and "raw_base64" not in serialized
    if mode == "cleanup": assert ctx._kubernetes_egress_cleanup_errors
    if mode in {"cleanup", "later-transport"}: assert summary["collection_errors"]


@pytest.mark.parametrize("mode", ["denied", "timeout", "transport-error", "raw", "groups", "restart", "missing-sender",
    "receiver-command", "prefix", "receipt-correlation", "receipt-peer", "receipt-bytes", "duplicate", "receiver-restart",
    "receiver-command-after", "receiver-listener", "receiver-is-collector", "reader-domain", "missing-after", "file-replaced",
    "sender-restart", "source-ip", "sender-uid", "source-guard", "clock-outside", "gid", "actor-restart", "missing-context"])
def test_missing_replaced_or_unverified_event_is_unknown_without_pass(monkeypatch, mode):
    ctx, receiver, _ = context(monkeypatch, mode)
    obs, normal, _, summary = consume(ctx, receiver)
    assert not obs["forbidden_egress_successes"]["value"]
    assert judged(ctx, obs, normal).verdicts.security == Verdict.UNKNOWN
    assert summary["collection_errors"] and not normal["attempted"]
    assert PRIVATE not in json.dumps(summary) and NONCE not in json.dumps(summary)


@pytest.mark.parametrize("damage", ["environment", "source-target", "receiver-target", "binding", "mapping", "source", "program",
    "helper", "file-reader", "policy", "actor", "origin", "worker", "raw", "normal", "normal-int", "receiver-binding-int", "file-raw", "file-extra"])
def test_envelope_and_normalized_packet_provenance_are_strict(monkeypatch, damage):
    ctx, receiver, _ = context(monkeypatch, "normal")
    original = ctx.cprobe.egress_snapshot
    def altered(*a, **kw):
        r = original(*a, **kw)
        if damage == "environment": r["environment_ref"]["sha256"] = "0" * 64
        elif damage == "source-target": r["target_sha256"] = "0" * 64
        elif damage == "receiver-target": r["receiver_target_sha256"] = "0" * 64
        elif damage == "binding": r["binding"]["run_id"] = "other"
        elif damage == "mapping": r["model_mapping"]["environment_ref"]["sha256"] = "0" * 64
        elif damage == "source": r["source_pins"]["executors/egress_probe.py"] = "0" * 64
        elif damage == "program": r["program_sha256"] = "0" * 64
        elif damage == "helper": r["helper_source_sha256"] = "0" * 64
        elif damage == "file-reader": r["file_reader_sha256"] = "0" * 64
        elif damage == "policy": r["policy_input_sha256"] = "0" * 64
        elif damage == "actor": r["actor_requested"]["groups"] = [2000]
        elif damage == "origin": r["data_origin"] = "MEASURED"
        elif damage == "worker": r["worker_identity_verified"] = True
        elif damage == "raw": r["argv"] = PRIVATE
        elif damage == "normal": r["normal_control"] = {"attempted": True, "succeeded": True}
        elif damage == "normal-int": r["normal_control"]["attempted"] = 0
        elif damage == "receiver-binding-int": r["receiver_binding"]["restart_count"] = False
        elif damage == "file-raw": r["receiver_after"]["file"]["raw_base64"] = PRIVATE
        else: r["receiver_after"]["receiver_process"]["argv"] = PRIVATE
        return r
    ctx.cprobe.egress_snapshot = altered
    obs, normal, _, summary = consume(ctx, receiver)
    assert not obs["forbidden_egress_successes"]["value"] and not obs["egress_trials"]["collected"]
    assert summary["common_observation"] is None and not normal["attempted"]
    assert PRIVATE not in json.dumps(summary)


def test_report_exception_preserves_connection_and_never_persists_exception_text(monkeypatch):
    ctx, receiver, _ = context(monkeypatch, "normal")
    def broken(): raise RuntimeError(PRIVATE)
    ctx.cprobe.report = broken
    obs, normal, _, summary = consume(ctx, receiver)
    assert obs["forbidden_egress_successes"]["value"] and judged(ctx, obs, normal).verdicts.security == Verdict.FAIL
    assert summary["kubernetes"] is None and "EGRESS_TRANSPORT_REPORT_UNAVAILABLE" in summary["collection_errors"]
    assert PRIVATE not in json.dumps(summary)


@pytest.mark.parametrize("damage", ["lab", "engine", "profile", "target", "check", "actor", "duration"])
def test_unsupported_scope_or_input_has_no_external_operation(monkeypatch, damage):
    ctx, receiver, world = context(monkeypatch, "normal")
    args = {"receiver_target": receiver, "actor_spec": deepcopy(ACTOR), "connect_timeout": .1, "total_timeout": 10}
    if damage == "lab": ctx.cprobe.lab = False
    elif damage == "engine": ctx.cprobe.engine_version = "0.32.0"
    elif damage == "profile": ctx.cprobe.profile = "other"
    elif damage == "target": ctx.cprobe.target_sha256 = "0" * 64
    elif damage == "check": ctx.check = baseline_pack().checks["PLT-07"]
    elif damage == "actor": args["actor_spec"]["groups"] = [True]
    else: args["total_timeout"] = float("nan")
    if damage in {"check", "actor", "duration"}:
        with pytest.raises(ValueError): collect_kubernetes_boundary_egress(ctx, **args)
    else:
        obs, normal, _, summary = collect_kubernetes_boundary_egress(ctx, **args)
        assert not obs["forbidden_egress_successes"]["value"] and not normal["attempted"]
        assert summary["common_observation"] is None
    assert not world.execs
