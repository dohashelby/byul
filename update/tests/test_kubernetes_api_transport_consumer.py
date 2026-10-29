"""Proposed dedicated profile on the real common method; formal pack pending A1."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.core.judge import judge_evidence
from guard.executors.api_transport import collect_kubernetes_api_transport
from guard.executors.http_common import Ctx
from guard.executors.kubernetes_api_transport import KubernetesAPITransportExecutor
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, sha256_hex, utcnow
from guard.runtime import kubernetes_api_hop as hop, kubernetes_http
from guard.runtime.kubernetes_http import REQUEST_BINDING_PROFILE
from tests.builders import T0, baseline_pack, evidence, policy
from tests.test_a1_kubernetes_api_hop import setup as common_setup
from tests.test_kubernetes_functional_control import response
from tests.test_kubernetes_request_binding import env
from tests.test_kubernetes_sampling import PRIVATE


def profile_check():
    # Unit contract only. No file-backed pack or selection gate is replaced.
    return baseline_pack().checks["PLT-08"].model_copy(update={"collection_profile": hop.PROFILE})


def inputs(tmp_path):
    world, _, values = common_setup()
    world.target["lab"] = False
    values["api_target_sha256"] = sha256_hex(canonical_json(world.target))
    path = tmp_path / "target.json"
    path.write_bytes(canonical_json(world.target))
    return world, values, {"target": str(path)}


def run(tmp_path, world, values, cfg, *, runner=None):
    return KubernetesAPITransportExecutor(cfg, runner=runner or world.runner).run(
        profile_check(), env(), values, VllmAdapter(), tmp_path / "work")


def judged(draft):
    pack = baseline_pack()
    return judge_evidence(profile_check(), evidence(check_id="PLT-08", status=draft.status,
        observations={name: obs.model_dump() for name, obs in draft.observations.items()},
        normal=draft.normal_control.model_dump(), attempted=False), policy(pack.ref()), T0)


@pytest.mark.parametrize("forbidden,expected", [(True, Verdict.FAIL), (False, Verdict.UNKNOWN)])
def test_live_hop_uses_existing_violation_meaning_without_whole_api_or_ipc_pass(tmp_path, forbidden, expected):
    world, values, cfg = inputs(tmp_path)
    values["plaintext_forbidden"] = forbidden
    original = deepcopy(values)
    draft = run(tmp_path, world, values, cfg)
    assert values == original and draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert draft.normal_control.attempted and draft.normal_control.succeeded
    trial, = draft.observations["api_transport_trials"].value
    assert trial["available"] and trial["normal_response_schema_verified"]
    assert trial["profile"] == hop.PROFILE and trial["binding_verified_before_after"]
    assert draft.observations["forbidden_plaintext_successes"].value == (
        ["owned_kubernetes_direct_api_plaintext"] if forbidden else [])
    assert draft.observations["api_transport_scope_complete"].value is False
    assert trial["internal_IPC_TP_scope_complete"] is False and trial["deployed_caller_path_verified"] is False
    assert len(world.execs) == 1 and world.execs[0]["url"].endswith("/v1/completions")
    assert all(not c.attempted for c in draft.challenges)
    result = judged(draft)
    assert result.verdicts.security == expected
    assert result.verdicts.functional == result.verdicts.performance == Verdict.UNKNOWN
    assert PRIVATE not in json.dumps(draft.model_dump(mode="json"))
    assert all(PRIVATE not in a.path.read_text(encoding="utf-8") for a in draft.artifacts)


@pytest.mark.parametrize("damage", ["denied", "refused", "timeout", "partial", "wrong-model", "bad-body", "replacement"])
def test_no_complete_bound_normal_response_means_no_plaintext_success(tmp_path, damage):
    world, values, cfg = inputs(tmp_path)
    def mutate(rows):
        row = rows[0]
        if damage == "denied": row["outcome"] = 403
        elif damage in {"refused", "timeout"}: row["outcome"] = damage
        elif damage == "partial": row["capture_complete"] = False
        elif damage in {"wrong-model", "bad-body"}:
            value = json.loads(response()); value["model"] = "other-model"
            text = PRIVATE if damage == "bad-body" else json.dumps(value)
            row.update(body=text, body_bytes=len(text.encode()), body_sha256=sha256_hex(text.encode()))
    world.damage = mutate
    if damage == "replacement":
        def replace(which, value, after):
            if which == "engine" and after: value["metadata"]["uid"] = "replaced"
        world.mutate = replace
    draft = run(tmp_path, world, values, cfg)
    assert draft.status == ExecStatus.COMPLETED and not draft.normal_control.succeeded
    assert draft.normal_control.attempted and draft.observations["forbidden_plaintext_successes"].value == []
    trial, = draft.observations["api_transport_trials"].value
    assert trial["binding_verified_before_after"] is (damage != "replacement")
    assert judged(draft).verdicts.security == Verdict.UNKNOWN
    assert PRIVATE not in json.dumps(draft.model_dump(mode="json"))


def test_window_expiry_keeps_initial_response_without_adopting_a_policy_violation(tmp_path, monkeypatch):
    world, values, cfg = inputs(tmp_path)
    first = utcnow(); times = iter([first, first + timedelta(minutes=2)])
    monkeypatch.setattr(hop, "utcnow", lambda: next(times))
    draft = run(tmp_path, world, values, cfg)
    trial, = draft.observations["api_transport_trials"].value
    assert trial["initial_response"]["outcome"] == 200 and trial["normal_response_schema_verified"]
    assert not trial["available"] and not trial["policy_window_verified"]
    assert not draft.normal_control.succeeded and draft.observations["forbidden_plaintext_successes"].value == []
    assert judged(draft).verdicts.security == Verdict.UNKNOWN


def test_parent_cleanup_gap_does_not_remove_a_complete_bound_plaintext_fact(tmp_path):
    world, values, cfg = inputs(tmp_path)
    def runner(argv, **kwargs):
        value = world.runner(argv, **kwargs)
        if "exec" in argv: value["capture"].update(pipes_closed=False, read_errors=["child_reap"])
        return value
    draft = run(tmp_path, world, values, cfg, runner=runner)
    assert draft.observations["forbidden_plaintext_successes"].value == ["owned_kubernetes_direct_api_plaintext"]
    assert draft.cleanup_errors == ["KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED"]
    trial, = draft.observations["api_transport_trials"].value
    assert not trial["parent_cleanup_verified"] and trial["available"]
    assert judged(draft).verdicts.security == Verdict.FAIL


def test_attachment_failure_aborts_but_preserves_existing_positive_fact(tmp_path, monkeypatch):
    world, values, cfg = inputs(tmp_path)
    original = Path.open
    def open_file(path, *args, **kwargs):
        if path.name == "probe_log.jsonl" and args and args[0] == "w": raise OSError(PRIVATE)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_file)
    draft = run(tmp_path, world, values, cfg)
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    assert not draft.artifacts and draft.abort_reason == "ARTIFACT_CAPTURE_INCOMPLETE"
    assert judged(draft).verdicts.security == Verdict.FAIL
    assert PRIVATE not in json.dumps(draft.model_dump(mode="json"))


@pytest.mark.parametrize("damage", ["missing", "bool-port", "environment", "target", "binding", "expired"])
def test_invalid_policy_or_binding_is_rejected_before_remote_io(tmp_path, damage):
    world, values, cfg = inputs(tmp_path)
    if damage == "missing": values.pop("plaintext_forbidden")
    elif damage == "bool-port": values["api_port"] = True
    elif damage in {"environment", "target", "binding"}: values["api_" + damage + "_sha256"] = "0" * 64
    else:
        values["validity_start"] = (utcnow() - timedelta(minutes=2)).isoformat()
        values["validity_end"] = (utcnow() - timedelta(minutes=1)).isoformat()
    with pytest.raises(ValueError): run(tmp_path, world, values, cfg)
    assert not world.calls and not world.execs


def test_unchanged_pack31_profile_cannot_silently_activate_the_new_executor(tmp_path):
    world, values, cfg = inputs(tmp_path)
    with pytest.raises(ValueError, match="^KUBERNETES_API_TRANSPORT_PROFILE_UNSUPPORTED$"):
        KubernetesAPITransportExecutor(cfg, runner=world.runner).run(
            baseline_pack().checks["PLT-08"], env(), values, VllmAdapter(), tmp_path / "work")
    assert not world.calls and not world.execs


def test_dictionary_or_lookalike_is_not_a_live_common_transport(tmp_path):
    world, transport, values = common_setup()
    fake = SimpleNamespace(profile=REQUEST_BINDING_PROFILE, target_sha256=sha256_hex(canonical_json(world.target)),
                           api_hop_snapshot=lambda *_: {"available": True})
    ctx = Ctx(check=profile_check(), env=env(), adapter=VllmAdapter(), target=world.target,
        policy_values=values, timeout=3, allow_mutating=False, keys={}, secrets=[], cprobe=transport,
        workdir=tmp_path, started=T0)
    ctx.cprobe = fake
    with pytest.raises(ValueError, match="^KUBERNETES_API_TRANSPORT_COMMON_INTERFACE_UNAVAILABLE$"):
        collect_kubernetes_api_transport(ctx)


def test_example_origin_is_kept_even_in_default_runner_slot(tmp_path, monkeypatch):
    world, values, cfg = inputs(tmp_path)
    monkeypatch.setattr(kubernetes_http, "capture_pipes", world.runner)
    draft = KubernetesAPITransportExecutor(cfg).run(
        profile_check(), env(), values, VllmAdapter(), tmp_path / "work")
    assert draft.data_origin == DataOrigin.EXAMPLE and draft.normal_control.succeeded
