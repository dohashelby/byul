"""D8: unapproved non-default boolean declarations request revalidation only."""

import json
from unittest.mock import Mock

import pytest

from test_launch_settings import collected
from serving_runtime.gateway.app import GatewayApp
from serving_runtime.observation.collectors import load_declaration
from serving_runtime.policy.revalidation import RevalidationRequests, evaluate_config_drift
from serving_runtime.target.launch_settings import comparison_rows
from serving_runtime.target.target import TargetRegistry, load_target


def comparison(approved=None, args=None, engine="vllm", state="ok"):
    pods = collected(args, engine=engine or "vllm")
    pods["state"] = state
    rows = comparison_rows(approved, pods, engine=engine)
    return {"rows": rows}


def row_named(result, key="trust_remote_code"):
    return next(row for row in result["rows"] if row["field"] == "launch_settings." + key)


def request_context():
    return dict(observation_session_id="session", target_id="target", policy={})


@pytest.mark.parametrize("engine", ["vllm", "sglang"])
@pytest.mark.parametrize("approved", [None, {}, {"revision": None}, {"trust_remote_code": None}])
def test_unapproved_or_null_non_default_bool_requests_revalidation(engine, approved):
    result = comparison(approved, ["--trust-remote-code"], engine)
    conditions = evaluate_config_drift(result).conditions
    assert len(conditions) == 1
    row = row_named(result)
    assert row["state"] == ("unknown" if approved == {"trust_remote_code": None} else "unapproved")
    assert row["default"] is False and row["deviates_from_default"] is True
    assert conditions[0].scope == "kubernetes_spec:launch_settings.trust_remote_code"
    assert conditions[0].evidence == dict(
        field="launch_settings.trust_remote_code",
        expected=None,
        observed=True,
        source="kubernetes_spec",
        observed_at=row["observed_at"],
    )


@pytest.mark.parametrize("engine", ["vllm", "sglang"])
def test_explicit_false_approval_keeps_existing_drift_behavior(engine):
    result = comparison({"trust_remote_code": False}, ["--trust-remote-code"], engine)
    assert row_named(result)["state"] == "drift"
    conditions = evaluate_config_drift(result).conditions
    assert len(conditions) == 1 and conditions[0].evidence["expected"] is False


@pytest.mark.parametrize("engine", ["vllm", "sglang"])
@pytest.mark.parametrize("approved", [None, {}, {"trust_remote_code": None}])
def test_default_boolean_values_do_not_create_conditions(engine, approved):
    assert not evaluate_config_drift(comparison(approved, [], engine)).conditions


@pytest.mark.parametrize("approved", [None, {"trust_remote_code": None}])
def test_default_observation_records_conclusive_scope(approved):
    evaluation = evaluate_config_drift(comparison(approved))
    assert not evaluation.conditions
    assert evaluation.observed_origins["kubernetes_spec:launch_settings.trust_remote_code"] == "kubernetes_api"
    assert evaluation.observed_origins["kubernetes_spec:launch_settings.revision"] == "kubernetes_api"


@pytest.mark.parametrize(
    "engine,args,keys",
    [
        ("sglang", ["--trust-remote-code", "--enable-multimodal"], {"trust_remote_code", "enable_multimodal"}),
        ("vllm", ["--trust-request-mm-kwargs"], {"trust_request_mm_kwargs"}),
    ],
)
def test_other_applicable_boolean_switches(engine, args, keys):
    conditions = evaluate_config_drift(comparison(None, args, engine)).conditions
    assert len(conditions) == len(keys)
    assert {condition.evidence["field"] for condition in conditions} == {"launch_settings." + key for key in keys}


@pytest.mark.parametrize("engine", ["vllm", "sglang"])
def test_unapproved_string_revision_does_not_create_conditions(engine):
    result = comparison(None, ["--revision", "abc123"], engine)
    assert row_named(result, "revision")["state"] == "unapproved"
    assert not evaluate_config_drift(result).conditions


@pytest.mark.parametrize("approved", [None, {"trust_remote_code": None}])
@pytest.mark.parametrize("repair", ["approval", "default"])
def test_previous_request_clears_when_approved_or_back_at_default(tmp_path, approved, repair):
    requests = RevalidationRequests(tmp_path)
    result = comparison(approved, ["--trust-remote-code"])
    requests.observe_baseline(result, **request_context())
    assert requests.summary()["current_condition_count"] == 1
    requests.observe_baseline(result, **request_context())
    assert len(requests.store.read_all()[0]) == 1
    requests = RevalidationRequests(tmp_path)
    repaired = (
        comparison({"trust_remote_code": True}, ["--trust-remote-code"])
        if repair == "approval"
        else comparison(approved)
    )
    assert row_named(repaired)["state"] == (
        "match" if repair == "approval" else "unknown" if approved else "unapproved"
    )
    assert not evaluate_config_drift(repaired).conditions
    requests.observe_baseline(repaired, **request_context())
    assert requests.summary()["current_condition_count"] == 0
    records = requests.store.read_all()[0]
    assert len(records) == 2 and records[-1]["event"] == "condition_cleared"


@pytest.mark.parametrize("state", ["error", "stale", "disconnected"])
@pytest.mark.parametrize("approved", [None, {"trust_remote_code": None}])
def test_unknown_observation_never_creates_conditions(state, approved):
    result = comparison(approved, ["--trust-remote-code"], state=state)
    row = row_named(result)
    assert row["state"] == "unknown" and row["observed"] is None
    assert row.get("deviates_from_default") is None
    evaluation = evaluate_config_drift(result)
    assert not evaluation.conditions and not evaluation.observed_origins


@pytest.mark.parametrize("state", ["error", "stale", "disconnected"])
def test_failed_observation_does_not_clear_previous_condition(tmp_path, state):
    requests = RevalidationRequests(tmp_path)
    requests.observe_baseline(comparison(None, ["--trust-remote-code"]), **request_context())
    assert requests.summary()["current_condition_count"] == 1
    requests.observe_baseline(comparison(None, ["--trust-remote-code"], state=state), **request_context())
    assert requests.summary()["current_condition_count"] == 1
    assert len(requests.store.read_all()[0]) == 1


def test_evidence_shape_and_identity_ignore_time_and_approval_presence():
    missing = comparison(None, ["--trust-remote-code"])
    explicit_null = comparison({"trust_remote_code": None}, ["--trust-remote-code"])
    row_named(explicit_null)["observed_at"] = "2026-11-14T00:00:00Z"
    first, second = evaluate_config_drift(missing), evaluate_config_drift(explicit_null)
    assert len(first.conditions) == len(second.conditions) == 1
    a, b = first.conditions[0], second.conditions[0]
    assert set(a.evidence) == set(b.evidence) == {"field", "expected", "observed", "source", "observed_at"}
    assert a.fingerprint == b.fingerprint and a.scope == b.scope
    assert a.evidence["observed_at"] != b.evidence["observed_at"]


@pytest.mark.parametrize("engine", [None, "vllm", "sglang"])
def test_each_row_adds_only_default_metadata(engine):
    result = comparison(None, ["--trust-remote-code"], engine)
    fields = {
        "field",
        "expected",
        "observed",
        "source",
        "state",
        "note",
        "observed_at",
        "data_origin",
        "default",
        "deviates_from_default",
    }
    assert all(set(row) == fields for row in result["rows"])
    row = row_named(result)
    assert row["default"] is (None if engine is None else False)
    assert row["deviates_from_default"] is (None if engine is None else True)
    assert row_named(result, "revision")["deviates_from_default"] is None
    if engine is not None:
        inapplicable = "enable_multimodal" if engine == "vllm" else "trust_request_mm_kwargs"
        assert row_named(result, inapplicable)["state"] == "not_applicable"
        assert row_named(result, inapplicable)["default"] is None
        assert row_named(result, inapplicable)["deviates_from_default"] is None


def test_notes_distinguish_missing_registration_and_missing_key():
    for approved, state, note in [
        (None, "unapproved", "승인값 없음 (launch_settings 미등록)"),
        ({}, "unapproved", "승인값 없음"),
        ({"revision": None}, "unapproved", "승인값 없음"),
        ({"trust_remote_code": None}, "unknown", "승인값 확인 안 됨"),
        ({"trust_remote_code": False}, "drift", None),
        ({"trust_remote_code": True}, "match", None),
    ]:
        row = row_named(comparison(approved, ["--trust-remote-code"]))
        assert (row["state"], row["note"]) == (state, note)


def test_missing_engine_cannot_classify_default_deviation():
    result = comparison(None, ["--trust-remote-code"], engine=None)
    assert row_named(result).get("deviates_from_default") is None
    assert not evaluate_config_drift(result).conditions


@pytest.mark.parametrize("approved", [None, {"trust_remote_code": None}])
def test_registered_target_snapshot_reaches_gateway_revalidation(tmp_path, approved):
    target = tmp_path / "target.json"
    target.write_text(
        json.dumps(
            dict(
                schema_version="runtime-target/1.0",
                target_id="target",
                engine={"kind": "vllm", "version": None},
                model={"id": "model"},
                launch_settings=approved,
            )
        )
    )
    registration = load_target(target)
    assert registration.state == "ok" and registration.engine_kind == "vllm"
    app = object.__new__(GatewayApp)
    app._target = TargetRegistry(registration, data_dir=tmp_path, observation_session_id="session")
    pods = collected(["--trust-remote-code"])
    app._collectors = Mock()
    app._collectors.get.side_effect = lambda source: pods if source == "target_pods" else None
    app._baseline = load_declaration(None, kind="baseline")
    app._manifest = load_declaration(None, kind="manifest")
    app._revalidation = RevalidationRequests(tmp_path)
    app._revalidation_context = request_context
    app._observe_target_pods(pods["last_value"], "kubernetes_api")
    assert app.revalidation_status()["current_condition_count"] == 1
    row = row_named(app.baseline_status())
    assert row["default"] is False and row["deviates_from_default"] is True
    records = app._revalidation.store.read_all()[0]
    assert len(records) == 1 and records[0]["evidence"]["expected"] is None
