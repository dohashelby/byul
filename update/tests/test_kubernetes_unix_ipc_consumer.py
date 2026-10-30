"""A2 전용 소비와 실제 A1 method의 CPU EXAMPLE 계약 회귀. 정식 등록/실증은 별도."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.core.judge import judge_evidence
from guard.executors.http_common import Ctx, HttpCheckExecutor
from guard.executors.kubernetes_unix_ipc import collect_kubernetes_unix_ipc, PROFILE
from guard.models import ExecStatus, Verdict, canonical_json
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
from tests.builders import T0, baseline_pack, evidence, policy
from tests.test_kubernetes_ipc_common import fixture as common_fixture
from tests.test_kubernetes_process_consumers import ProcessWorld, PRIVATE as NORMAL_PRIVATE
from tests.test_model_preparation_integration import full_inputs

PRIVATE = "EXAMPLE_A2_IPC_PRIVATE_492d"
VALUES = {"protected_unix_paths": ["/run/EXAMPLE-first.sock", "/run/EXAMPLE-later.sock"], "allowed_ipc_uids": ["1000"]}
ACTORS = [{"uid": 2000, "gid": 2000, "groups": [55, 2000]}, {"uid": 1000, "gid": 1000, "groups": [44, 1000]}]
KW = {"actor_specs": ACTORS, "connect_timeout": .1, "total_timeout": 3}


def packet(mode):
    p = common_fixture()
    p["actors"].reverse()
    for key in ("inspect_before", "inspect_after"):
        row = p[key]["packet"]["rows"][0]
        p[key]["packet"]["rows"] = [deepcopy(row), {**deepcopy(row), "index": 1,
            "identity": {**row["identity"], "inode": 21}, "socket_inode": "42"}]
    for actor, requested in zip(p["actors"], ACTORS):
        actor["requested"] = deepcopy(requested)
        for key in ("context_before", "context_after"):
            actor[key]["groups"] = list(requested["groups"])
        actor["connect_packet"]["groups"] = list(requested["groups"])
        row = actor["connect_packet"]["rows"][0]
        actor["connect_packet"]["rows"] = [deepcopy(row), {**deepcopy(row), "index": 1, "identity": {**row["identity"], "inode": 21}}]
        anchor = actor["path_anchors"][0]
        actor["path_anchors"] = [deepcopy(anchor), {**deepcopy(anchor), "index": 1, "identity": {**anchor["identity"], "inode": 21}}]
    a = p["actors"][0]
    if mode in {"normal", "fake-denial"}:
        a["connect_packet"]["rows"] = [{"index": i, "outcome": "denied"} for i in range(2)]
    if mode == "partial":
        a["connect_packet"]["rows"] = a["connect_packet"]["rows"][:1]
        a["path_anchors"] = a["path_anchors"][:1]
        a["diagnostics"] = ["IPC_ACTOR_DEADLINE"]
        a["child_exit_ok"] = False
    elif mode == "later-metadata":
        p["inspect_after"]["packet"]["rows"] = p["inspect_after"]["packet"]["rows"][:1]
        p["collector_after"] = None
        p["diagnostics"] = ["IPC_SNAPSHOT_DEADLINE"]
    elif mode == "later-unknown":
        a["connect_packet"]["rows"][1] = {"index": 1, "outcome": "unknown"}
    elif mode == "later-anchor":
        a["path_anchors"][1]["path_binding_stable"] = False
    elif mode == "actor-namespace":
        for key in ("context_before", "context_after"):
            a[key]["namespaces"]["mnt"] = "mnt:[99]"
        a["connect_packet"]["namespaces"]["mnt"] = "mnt:[99]"
    elif mode == "actor-is-collector":
        for key in ("context_before", "context_after"):
            a[key]["pid"] = p["collector_before"]["pid"]
    elif mode == "actor-credentials":
        for key in ("context_before", "context_after"):
            a[key]["uids"] = [2999] * 4
        a["connect_packet"]["uid"] = 2999
        a.update(credential_match_observed=False, context_stable=False)
    elif mode == "actor-restart":
        a["context_after"]["start_ticks"] += 1
        a["context_stable"] = False
    elif mode == "collector-restart":
        p["collector_after"]["start_ticks"] += 1
    elif mode == "owner-restart":
        for r in p["inspect_after"]["packet"]["rows"]:
            r["owners"][0]["start"] = "13"
    elif mode == "fake-denial":
        for anchor in a["path_anchors"]:
            anchor.update(kernel_connect_attempted=False, path_binding_stable=False)
    elif mode == "child-reap":
        a.update(child_reaped=False, child_exit_ok=False)
    elif mode == "invalid-groups":
        a["connect_packet"]["groups"] = [2000]
    elif mode == "invalid-code-runtime":
        p["compile_provenance"]["python"] = "3.13.1"
    elif mode == "invalid-raw":
        p["argv"] = PRIVATE
    return p


class IPCWorld(ProcessWorld):
    def __init__(self, target, mode="normal", normal_mode=None):
        super().__init__(target, normal_mode=normal_mode)
        self.mode, self.ipc_reads = mode, 0
    def runner(self, argv, **kwargs):
        supplied = json.loads(kwargs["input_bytes"]) if "input_bytes" in kwargs else {}
        if "exec" in argv and "actor_specs" in supplied:
            assert supplied == {"policy_values": VALUES, **KW}
            self.ipc_reads += 1
            self.after_exec = True
            result = {"returncode": 0, "stdout_bytes": canonical_json(packet(self.mode)),
                "stderr_bytes": PRIVATE.encode(), "capture": {"complete": True}}
            if self.mode in {"cleanup", "cleanup-incomplete"}:
                result["capture"].update(pipes_closed=False, read_errors=["child_reap"])
                result["capture"]["complete"] = self.mode != "cleanup-incomplete"
            return result
        result = super().runner(argv, **kwargs)
        if self.normal_mode == "wrong-id" and supplied.get("requests", [{}])[0].get("id") == "functional-control":
            rows = json.loads(result["stdout_bytes"])
            rows[0]["id"] = "unrelated-wire-response"
            result["stdout_bytes"] = canonical_json(rows)
        return result


def context(tmp_path, mode="normal", normal_mode=None):
    env, target = full_inputs()
    world = IPCWorld(target, mode, normal_mode)
    t = KubernetesHTTP(target, env, duration=30, runner=world.runner, profile=REQUEST_BINDING_PROFILE)
    check = baseline_pack().checks["PLT-02"].model_copy(update={"collection_profile": PROFILE})
    ctx = Ctx(check=check, env=env, adapter=VllmAdapter(), target=target, policy_values=deepcopy(VALUES),
        timeout=3, allow_mutating=False, keys={}, secrets=[], cprobe=t, workdir=tmp_path, started=T0)
    return ctx, world


def consume(ctx):
    return collect_kubernetes_unix_ipc(ctx, **deepcopy(KW))


def judged(ctx, observations, normal):
    pack = baseline_pack()
    return judge_evidence(ctx.check, evidence(check_id="PLT-02", observations=observations, normal=normal),
        policy(pack.ref(), values={"PLT-02": VALUES}), T0)


@pytest.mark.parametrize("mode,fail", [("normal", False), ("violation", True), ("partial", True),
    ("later-metadata", True), ("later-unknown", True), ("later-anchor", True), ("child-reap", True), ("cleanup", True)])
def test_bound_explicit_actor_facts_keep_independent_fail_through_later_partial_or_cleanup(tmp_path, mode, fail):
    before = deepcopy((VALUES, ACTORS))
    ctx, world = context(tmp_path, mode)
    obs, normal, _, summary = consume(ctx)
    assert world.ipc_reads == 1
    assert (VALUES, ACTORS) == before
    assert bool(obs["unix_ipc_violations"]["value"]) is fail
    assert obs["ipc_scope_complete"] == {"value": False, "collected": True}
    trials = obs["unix_ipc_trials"]["value"]
    assert trials and trials[0]["actor_groups"] == [55, 2000]
    if mode == "normal":
        assert trials[0]["denial_verified"] and trials[0]["outcome"] == "denied"
    if mode in {"partial", "later-metadata", "later-unknown", "later-anchor"}:
        assert len(obs["unix_ipc_violations"]["value"]) == 1 and trials[0]["connection_verified"]
    result = judged(ctx, obs, normal)
    assert result.verdicts.security == (Verdict.FAIL if fail else Verdict.UNKNOWN)
    assert result.verdicts.functional == result.verdicts.performance == Verdict.UNKNOWN
    assert not summary["worker_subject_verified"] and not summary["ipc_scope_complete"]
    assert PRIVATE not in json.dumps((obs, normal, summary))
    assert NORMAL_PRIVATE not in json.dumps((obs, normal, summary))
    assert all(p not in json.dumps(summary) for p in VALUES["protected_unix_paths"])
    if mode in {"normal", "violation"}:
        assert normal["succeeded"] and normal["attempted"]
        assert obs["functional_probe_results"]["value"] == {"POST /v1/completions": "pass"}
    if mode == "child-reap":
        assert "IPC_CHILD_CLEANUP_UNVERIFIED" in ctx._kubernetes_ipc_cleanup_errors


@pytest.mark.parametrize("mode", ["actor-namespace", "actor-is-collector", "actor-credentials", "actor-restart",
    "collector-restart", "owner-restart", "fake-denial", "cleanup-incomplete", "invalid-groups", "invalid-code-runtime", "invalid-raw"])
def test_unverified_actor_owner_source_or_untrusted_packet_is_unknown_without_rewriting(tmp_path, mode):
    ctx, world = context(tmp_path, mode)
    obs, normal, _, summary = consume(ctx)
    assert not obs["unix_ipc_violations"]["value"] and judged(ctx, obs, normal).verdicts.security == Verdict.UNKNOWN
    assert PRIVATE not in json.dumps((obs, normal, summary))
    if mode == "fake-denial":
        assert obs["unix_ipc_trials"]["value"][0]["outcome"] == "unknown"
        assert summary["common_observation"]["packet"]["actors"][0]["connect_packet"]["rows"][0]["outcome"] == "denied"


@pytest.mark.parametrize("normal_mode", ["exception", "wrong-model", "denied", "refused", "timeout", "partial", "wrong-id"])
def test_later_normal_failure_cannot_erase_previously_verified_violation(tmp_path, normal_mode):
    ctx, _ = context(tmp_path, "violation", normal_mode)
    obs, normal, _, summary = consume(ctx)
    assert obs["unix_ipc_violations"]["value"] and not normal["succeeded"]
    assert judged(ctx, obs, normal).verdicts.security == Verdict.FAIL
    assert PRIVATE not in json.dumps(summary)


@pytest.mark.parametrize("damage", ["environment", "target", "binding", "source", "program", "raw", "origin", "worker", "policy"])
def test_envelope_provenance_is_strict_and_invalid_record_never_reaches_normal_io(tmp_path, damage):
    ctx, world = context(tmp_path, "violation")
    original = ctx.cprobe.ipc_snapshot
    def capture(*a, **kw):
        r = original(*a, **kw)
        if damage == "environment": r["environment_ref"]["sha256"] = "0" * 64
        elif damage == "target": r["target_sha256"] = "0" * 64
        elif damage == "binding": r["binding"]["run_id"] = "other"
        elif damage == "source": r["original_probe_sha256"] = "0" * 64
        elif damage == "program": r["program_sha256"] = "0" * 64
        elif damage == "raw": r["environment_values"] = PRIVATE
        elif damage == "origin": r["data_origin"] = "MEASURED"
        elif damage == "worker": r["worker_identity_verified"] = True
        else: r["policy_input_sha256"] = "0" * 64
        return r
    ctx.cprobe.ipc_snapshot = capture
    obs, normal, _, summary = consume(ctx)
    assert not obs["unix_ipc_trials"]["collected"] and not obs["unix_ipc_violations"]["value"]
    assert summary["common_observation"] is None and not normal["attempted"] and not world.packets
    assert PRIVATE not in json.dumps(summary)


@pytest.mark.parametrize("damage", ["lab", "engine", "profile", "target", "check", "actors", "budget"])
def test_unsupported_contract_has_no_actor_or_normal_execution(tmp_path, damage):
    ctx, world = context(tmp_path, "violation")
    args = deepcopy(KW)
    if damage == "lab": ctx.cprobe.lab = False
    elif damage == "engine": ctx.cprobe.engine_version = "0.32.0"
    elif damage == "profile": ctx.cprobe.profile = "unreviewed"
    elif damage == "target": ctx.cprobe.target_sha256 = "0" * 64
    elif damage == "check": ctx.check = baseline_pack().checks["PLT-02"]
    elif damage == "actors": args["actor_specs"][0]["groups"] = [True]
    else: args["total_timeout"] = float("nan")
    if damage in {"check", "actors", "budget"}:
        with pytest.raises(ValueError): collect_kubernetes_unix_ipc(ctx, **args)
    else:
        obs, normal, _, _ = collect_kubernetes_unix_ipc(ctx, **args)
        assert not normal["attempted"] and not obs["unix_ipc_violations"]["value"]
    assert world.ipc_reads == 0 and not world.packets


@pytest.mark.parametrize("damage", ["dict", "extra", "id", "exception"])
def test_normal_projection_still_requires_exact_response_correspondence(tmp_path, damage):
    ctx, _ = context(tmp_path, "violation")
    original = ctx.cprobe.normal_requests
    def normal(*a, **kw):
        rows = original(*a, **kw)
        if damage == "dict": return rows[0]
        if damage == "extra": return rows + rows
        if damage == "id": rows[0]["id"] = "other"
        if damage == "exception": raise OSError(PRIVATE)
        return rows
    ctx.cprobe.normal_requests = normal
    obs, control, _, summary = consume(ctx)
    assert not control["succeeded"] and judged(ctx, obs, control).verdicts.security == Verdict.FAIL
    assert PRIVATE not in json.dumps(summary)


def test_post_collection_report_and_attachment_failures_keep_typed_observations(tmp_path, monkeypatch):
    ctx, _ = context(tmp_path, "violation")
    original = ctx.cprobe.report
    def report(): raise RuntimeError(PRIVATE)
    ctx.cprobe.report = report
    obs, normal, _, summary = consume(ctx)
    assert judged(ctx, obs, normal).verdicts.security == Verdict.FAIL and summary["kubernetes"] is None
    ctx.cprobe.report = original
    # Unit-only shared attachment boundary; no registry/CheckDef file or CLI is replaced.
    class Boundary(HttpCheckExecutor):
        requires_roles = False
        def collect(self, ctx): return consume(ctx)
    original_open = Path.open
    def failed(path, *a, **kw):
        if path.name == "probe_log.jsonl" and a and a[0] == "w": raise OSError(PRIVATE)
        return original_open(path, *a, **kw)
    monkeypatch.setattr(Path, "open", failed)
    draft = Boundary({})._finish(ctx)
    assert draft.status == ExecStatus.ABORTED and draft.observations["unix_ipc_violations"].value
    assert not draft.artifacts and PRIVATE not in json.dumps(draft.model_dump(mode="json"))
