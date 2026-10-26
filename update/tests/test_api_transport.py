"""EXAMPLE owned API transport projection; no Docker/engine/network startup."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.core.run import RunConfig, run_plan
from guard.core.judge import judge_plan
from guard.core.select import PolicyValueError, select
from guard.executors import api_transport as api, stream_pressure, stream_probe
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, sha256_hex
from guard.store import Store
from tests.builders import T0, baseline_pack, policy
from tests.test_stream_pressure import inputs, owner, CONTAINER_ID


@pytest.fixture
def case(tmp_path, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return T0
    monkeypatch.setattr(api, "datetime", Clock)
    env, target, settings = inputs("http://172.18.0.2:8000")
    values = {"api_hop_scope": "guard_probe_to_owned_direct_api", "api_container_id": CONTAINER_ID,
        "api_network_id": "1" * 64, "api_endpoint_id": "2" * 64, "api_ip": "172.18.0.2", "api_port": 8000,
        "plaintext_forbidden": True, "validity_start": (T0 - timedelta(hours=1)).isoformat(),
        "validity_end": (T0 + timedelta(hours=1)).isoformat()}
    binding = {"network_id": values["api_network_id"], "endpoint_id": values["api_endpoint_id"],
               "container_id": CONTAINER_ID, "host": values["api_ip"]}
    state = SimpleNamespace(captures=0, cleaned=0, mutation=None, client_id="EXAMPLE-fresh-probe")
    def capture(_client, got_env, got_target, got_settings):
        state.captures += 1
        assert got_env == env and got_target == target
        assert 0 < got_settings["max_duration_seconds"] <= settings["max_duration_seconds"]
        rows = []
        for index, phase in enumerate(("before", "after")):
            rows.append({"phase": phase, "sample": {"owner": owner(),
                "timestamp": (T0 + timedelta(seconds=index)).isoformat(),
                "capture_hashes": {"transport_complete": True}, "complete": False,
                "observations": {"clock": {"basis": "linux-monotonic-time-namespace-v1",
                    "time_namespace_inode": 1, "sample_started_monotonic_ns": 1 + index * 2,
                    "sample_finished_monotonic_ns": 2 + index * 2}}}})
        value = {"schema": "guard-stream-pressure-capture/1", "profile": stream_pressure.PROFILE,
            "capture_executed": True, "engine_started": False,
            "environment_ref": env.ref().model_dump(mode="json"), "run_id": target["run_id"],
            "target_sha256": sha256_hex(canonical_json(target)),
            "settings_sha256": sha256_hex(canonical_json(got_settings)),
            "probe_sha256": hashlib.sha256(Path(stream_probe.__file__).read_bytes()).hexdigest(),
            "identity_stable": True, "owner_before": owner(), "owner_after": owner(),
            "network_binding_sha256": sha256_hex(canonical_json(binding)), "resource_samples": rows,
            "normal_response_schema_verified": {"before": True, "parallel": False, "after": False},
            "stages": [{"stage": "before", "responses": [{"id": "before", "outcome": 200,
                "capture_complete": True, "deadline_guard_verified": True, "stop_reason": "eof",
                "utf8_valid": True, "body_truncated": False}]}],
            "data_origin": "EXAMPLE", "resource_samples_complete": False,
            "read_procedure_verified": False, "collection_errors": ["PRESSURE_RESOURCE_SAMPLE_UNAVAILABLE"]}
        if state.mutation:
            state.mutation(value)
        return value
    def cleanup(**kwargs):
        state.cleaned += 1
        return getattr(state, "cleanup_errors", [])
    monkeypatch.setattr(stream_pressure, "create_pressure_client", lambda: SimpleNamespace(run_id=state.client_id, cleanup=cleanup))
    monkeypatch.setattr(stream_pressure, "_owner", lambda *_args: owner())
    monkeypatch.setattr(stream_pressure, "_network_binding", lambda *_args: deepcopy(binding))
    monkeypatch.setattr(stream_pressure, "capture_pressure", capture)
    target_path = tmp_path / "EXAMPLE-target.json"
    target_path.write_text(json.dumps(target), encoding="utf-8")
    cfg = {"target": str(target_path), "settings": settings}
    pack = baseline_pack()
    def run(raw_override=None):
        pol = policy(pack.ref(), scope={"include": ["PLT-08"]}, values={"PLT-08": values})
        store = Store(tmp_path / f"store-{state.captures}-{state.cleaned}")
        store.put_many([env, pol])
        plan = select(pack, [env], pol)
        store.put(plan)
        evd, = run_plan(store, pack, plan, RunConfig(executors={"api_transport": "real"},
            raw={"api_transport": cfg} if raw_override is None else raw_override))
        result, = judge_plan(store, pack, plan, as_of=T0)
        assert store.verify() == []
        return evd, result
    return SimpleNamespace(env=env, target=target, settings=settings, values=values, state=state, run=run, cfg=cfg, binding=binding)


def test_product_path_preserves_independent_plaintext_fail_and_example_origin(case):
    evd, result = case.run()
    assert evd.execution.status == ExecStatus.COMPLETED and evd.data_origin == DataOrigin.EXAMPLE
    assert result.verdicts.security == Verdict.FAIL
    assert result.verdicts.functional == result.verdicts.performance == Verdict.UNKNOWN
    assert case.state.captures == case.state.cleaned == 1
    assert evd.observations["api_transport_scope_complete"].value is False
    trial, = evd.observations["api_transport_trials"].value
    assert trial["cryptographic_peer_identity_verified"] is False
    assert trial["deployed_caller_path_verified"] is False
    assert trial["source_owner"] == owner()
    assert trial["initial_response"]["outcome"] == 200 and trial["initial_response"]["capture_complete"] is True
    assert len(trial["source_clock_edges"]) == 2
    assert evd.artifacts == []


@pytest.mark.parametrize("mode", ["allowed", "no_normal", "refused", "truncated", "incomplete", "no_guard"])
def test_allowed_denied_and_incomplete_are_unknown_never_pass(case, mode):
    if mode == "allowed":
        case.values["plaintext_forbidden"] = False
    else:
        def mutate(value):
            if mode == "no_normal":
                value["normal_response_schema_verified"]["before"] = False
            else:
                key, bad = {"refused": ("outcome", "refused"), "truncated": ("body_truncated", True),
                    "incomplete": ("capture_complete", False), "no_guard": ("deadline_guard_verified", False)}[mode]
                value["stages"][0]["responses"][0][key] = bad
        case.state.mutation = mutate
    evd, result = case.run()
    assert all(v == Verdict.UNKNOWN for v in result.verdicts.model_dump().values())
    assert evd.observations["forbidden_plaintext_successes"].value == []


@pytest.mark.parametrize("field", ["owner_after", "identity_stable", "probe_sha256", "target_sha256", "settings_sha256",
    "network_binding_sha256", "environment_ref", "run_id", "data_origin"])
def test_binding_mismatch_is_aborted_not_a_plaintext_fail(case, field):
    case.state.mutation = lambda value: value.update({field: None})
    evd, result = case.run()
    assert evd.execution.status == ExecStatus.ABORTED and result.verdicts.security == Verdict.UNKNOWN
    assert evd.execution.abort_reason == "API_TRANSPORT_BINDING_UNVERIFIED"


@pytest.mark.parametrize("field,bad", [("api_hop_scope", "deployed_caller"), ("api_ip", "127.0.0.1"),
    ("api_port", True), ("plaintext_forbidden", "true"), ("validity_start", "yesterday"),
    ("validity_end", T0.isoformat()), ("api_container_id", "EXAMPLE-other")])
def test_invalid_policy_never_reaches_runtime_or_http(case, field, bad):
    case.values[field] = bad
    if field in {"api_port", "plaintext_forbidden"}:
        with pytest.raises(PolicyValueError, match="PLT-08." + field + ": expected"):
            case.run()
        assert case.state.captures == case.state.cleaned == 0
        return
    evd, result = case.run()
    assert evd.execution.status in (ExecStatus.ABORTED, ExecStatus.NOT_RUN)
    assert result.verdicts.security == Verdict.UNKNOWN and case.state.captures == 0


def test_policy_target_mismatch_is_rejected_before_capture(case):
    case.values["api_endpoint_id"] = "3" * 64
    evd, result = case.run()
    assert evd.execution.status == ExecStatus.ABORTED
    assert case.state.captures == 0 and case.state.cleaned == 1
    assert result.verdicts.security == Verdict.UNKNOWN


def test_cleanup_failure_does_not_erase_verified_plaintext_operation(case):
    case.state.cleanup_errors = ["EXAMPLE_PRIVATE_ERROR_CANARY"]
    evd, result = case.run()
    assert result.verdicts.security == Verdict.FAIL
    assert evd.execution.cleanup_errors == ["API_TRANSPORT_PROBE_CLEANUP_INCOMPLETE"]
    assert "EXAMPLE_PRIVATE_ERROR_CANARY" not in canonical_json(evd.to_dict()).decode()


def test_cleanup_never_uses_the_target_owner_id(case):
    case.state.client_id = case.target["run_id"]
    evd, result = case.run()
    assert evd.execution.status == ExecStatus.ABORTED and result.verdicts.security == Verdict.UNKNOWN
    assert case.state.cleaned == case.state.captures == 0


@pytest.mark.parametrize("mode", ["missing", "source_owner", "clock_namespace", "clock_order", "outside_window", "transport_incomplete"])
def test_unverified_source_window_is_unknown(case, mode):
    def mutate(value):
        if mode == "missing":
            value["resource_samples"].pop()
            return
        sample = value["resource_samples"][1]["sample"]
        if mode == "source_owner":
            sample["owner"] = None
        elif mode == "clock_namespace":
            sample["observations"]["clock"]["time_namespace_inode"] = 2
        elif mode == "clock_order":
            sample["observations"]["clock"]["sample_started_monotonic_ns"] = 1
        elif mode == "outside_window":
            sample["timestamp"] = (T0 + timedelta(days=1)).isoformat()
        else:
            sample["capture_hashes"]["transport_complete"] = False
    case.state.mutation = mutate
    evd, result = case.run()
    assert evd.execution.status == ExecStatus.ABORTED and result.verdicts.security == Verdict.UNKNOWN


def test_extra_capture_detail_is_not_stored_as_body_secret_or_exception(case):
    def mutate(value):
        value["EXAMPLE_PRIVATE_FIELD"] = "EXAMPLE_PRIVATE_PAYLOAD_CANARY"
        value["stages"][0]["responses"][0].update(body="EXAMPLE_PRIVATE_PAYLOAD_CANARY",
            headers={"Authorization": "EXAMPLE_PRIVATE_PAYLOAD_CANARY"}, detail="EXAMPLE_PRIVATE_PAYLOAD_CANARY")
    case.state.mutation = mutate
    evd, result = case.run()
    assert result.verdicts.security == Verdict.FAIL
    assert "EXAMPLE_PRIVATE_PAYLOAD_CANARY" not in canonical_json(evd.to_dict()).decode()


def test_predecessor_definition_hashes_and_no_stream_changes():
    from tests.builders import ROOT, PACK_DIR
    old = json.loads((ROOT / "tests/fixtures/pack28-check-hashes.json").read_text())
    changed = {p.stem for p in (PACK_DIR / "checks").glob("*.yaml")
               if hashlib.sha256(p.read_bytes()).hexdigest() != old[p.stem]}
    assert changed == {"PLT-08"} and len(old) == 77
    check = baseline_pack().checks["PLT-08"]
    assert check.revision == 3 and baseline_pack().manifest.revision == 29
    assert {p.key: p.type for p in check.policy_params} == api.POLICY_TYPES
    assert check.judge.params["pass_when_all"] == []


@pytest.mark.parametrize("bad", [None, ["EXAMPLE_PRIVATE_CONFIG_CANARY"], "EXAMPLE_PRIVATE_CONFIG_CANARY"])
def test_invalid_configuration_has_fixed_diagnostic_without_raw_input(case, bad):
    evd, result = case.run({"api_transport": bad})
    assert evd.execution.status == ExecStatus.ABORTED and result.verdicts.security == Verdict.UNKNOWN
    assert evd.execution.abort_reason == "executor failure: ValueError: API_TRANSPORT_CONFIGURATION_INVALID"
    assert "EXAMPLE_PRIVATE_CONFIG_CANARY" not in canonical_json(evd.to_dict()).decode()
    assert case.state.captures == case.state.cleaned == 0
