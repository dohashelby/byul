"""Static support guidance uses declared inputs, never engine or store evidence."""
from copy import deepcopy

import pytest

from guard.adapters.vllm import CHAT_REVIEWED_IMAGE, CHAT_TEMPLATE_SHA256
from guard.readiness_support import assess_check_support
from tests.builders import baseline_pack, environment, policy


@pytest.fixture(scope="module")
def pack():
    return baseline_pack()


def declared_inputs(pack):
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": CHAT_REVIEWED_IMAGE},
                      runtime={"gpu_count": 0})
    pol = policy(pack.ref(), values={
        "REQ-07": {"allowed_template_sha256": [CHAT_TEMPLATE_SHA256], "max_rendered_input_tokens": 127,
                   "request_template_override_allowed": False},
        "REQ-11": {"result_binding": "originating_connection", "client_request_id_trust": "untrusted_correlation"},
        "OBS-09": {"collector_profile": "guard-vllm-native-info-v1", "required_trusted_fields": ["process", "level", "source"],
                   "client_request_id_trust": "untrusted_correlation"},
    })
    cfg = {"adapter": {"name": "vllm"},
           "executors": {"template_validation": "real", "request_binding": "real", "log_integrity": "real"},
           "vllm": {"launch_profile": "cpu-bounded", "chat_template_profile": "guard-text-chat-v1",
                    "logging_profile": "request-info-v1"}}
    return env, pol, cfg


def support(pack, *, check_ids=("REQ-07", "REQ-11", "OBS-09"), mutate=None, managed=True):
    env, pol, cfg = declared_inputs(pack)
    if mutate:
        env, pol, cfg = mutate(env, pol, cfg)
    return assess_check_support(pack, env, pol, cfg, selected_check_ids=check_ids, managed=managed)


def test_static_support_does_not_run_collectors_or_change_declared_inputs(pack, monkeypatch, tmp_path):
    import socket
    from guard.runtime.docker import Docker
    from guard.runtime import sandbox
    from guard.executors.request_binding import RequestBindingExecutor
    from guard.executors.template_validation import TemplateValidationExecutor
    from guard.executors.log_integrity import LogIntegrityExecutor

    def forbidden(*args, **kwargs):
        raise AssertionError("static guidance must not touch runtime resources")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(Docker, "__init__", forbidden)
    monkeypatch.setattr(sandbox, "up", forbidden)
    for cls in (RequestBindingExecutor, TemplateValidationExecutor, LogIntegrityExecutor):
        monkeypatch.setattr(cls, "collect", forbidden)
    env, pol, cfg = declared_inputs(pack)
    before = (env.model_dump(), pol.model_dump(), deepcopy(cfg))
    # Target paths are deliberately absent: support must not inspect a target.
    cfg["request_binding"] = {"target": str(tmp_path / "not-created" / "target.json")}
    expected_cfg = deepcopy(cfg)
    rows = assess_check_support(pack, env, pol, cfg, selected_check_ids=["REQ-07", "REQ-11", "OBS-09"])
    assert {row["status"] for row in rows} == {"SUPPORTED"}
    assert all(row["policy_valid"] is True and row["policy_supported"] is True for row in rows)
    assert (env.model_dump(), pol.model_dump(), cfg) == (before[0], before[1], expected_cfg)
    assert not list(tmp_path.iterdir())
    assert all("not a verdict" in row["limitations"][0] for row in rows)
    assert "no independent binding oracle" in " ".join(rows[1]["limitations"])


@pytest.mark.parametrize("change", ["other-digest", "other-version"])
def test_pinned_source_contracts_do_not_trust_version_alone(pack, change):
    def mutate(env, pol, cfg):
        engine = env.engine.model_copy(update={"image_digest": "image@sha256:" + "f" * 64} if change == "other-digest"
                                       else {"version": "0.32.0"})
        return env.model_copy(update={"engine": engine}), pol, cfg
    rows = support(pack, mutate=mutate)
    assert {row["status"] for row in rows} == {"UNSUPPORTED"}
    assert all(any("exact" in reason for reason in row["reasons"]) for row in rows)


def test_missing_pinned_identity_is_unknown_not_supported(pack):
    def mutate(env, pol, cfg):
        return env.model_copy(update={"engine": env.engine.model_copy(update={"image_digest": None})}), pol, cfg
    assert {row["status"] for row in support(pack, mutate=mutate)} == {"UNKNOWN"}


@pytest.mark.parametrize(("check_id", "key", "value"), [
    ("REQ-07", "request_template_override_allowed", True),
    ("REQ-07", "max_rendered_input_tokens", 4096),
    ("REQ-11", "result_binding", "enterprise_principal"),
    ("REQ-11", "client_request_id_trust", "authenticated_identity"),
    ("OBS-09", "collector_profile", "enterprise-siem"),
    ("OBS-09", "required_trusted_fields", ["process", "level", "source", "principal"]),
    ("OBS-09", "client_request_id_trust", "authenticated_identity"),
])
def test_valid_unsupported_policy_is_described_without_rewriting_or_dropping_other_checks(pack, check_id, key, value):
    def mutate(env, pol, cfg):
        values = deepcopy(pol.values)
        values[check_id][key] = value
        return env, pol.model_copy(update={"values": values}), cfg
    rows = support(pack, mutate=mutate)
    assert len(rows) == 3
    by_id = {row["check_id"]: row for row in rows}
    assert by_id[check_id]["status"] == "UNSUPPORTED"
    assert by_id[check_id]["policy_valid"] is True
    assert by_id[check_id]["policy_supported"] is False
    assert all(row["status"] == "SUPPORTED" for row in rows if row["check_id"] != check_id)
    assert any("policy" in reason or "version" in reason for reason in by_id[check_id]["reasons"])


@pytest.mark.parametrize(("key", "affected"), [("chat_template_profile", {"REQ-07", "REQ-11"}),
                                                 ("logging_profile", {"OBS-09"})])
def test_launch_profile_support_is_check_specific(pack, key, affected):
    def mutate(env, pol, cfg):
        del cfg["vllm"][key]
        return env, pol, cfg
    rows = support(pack, mutate=mutate)
    assert {row["check_id"] for row in rows if row["status"] == "UNSUPPORTED"} == affected


def test_policy_allowlist_mismatch_remains_a_possible_violation_not_a_rewritten_profile(pack):
    def mutate(env, pol, cfg):
        values = deepcopy(pol.values)
        values["REQ-07"]["allowed_template_sha256"] = ["a" * 64]
        return env, pol.model_copy(update={"values": values}), cfg
    rows = support(pack, check_ids=("REQ-07",), mutate=mutate)
    assert rows[0]["status"] == "SUPPORTED"
    assert "outside the unchanged policy allowlist" in " ".join(rows[0]["limitations"])


def test_external_target_launch_metadata_is_unconfirmed_without_reading_it(pack):
    rows = support(pack, managed=False)
    assert {row["status"] for row in rows} == {"UNKNOWN"}
    assert all("runtime verification" in " ".join(row["reasons"]) for row in rows)


def test_external_target_does_not_hide_known_policy_mismatch(pack):
    def mutate(env, pol, cfg):
        values = deepcopy(pol.values)
        values["REQ-11"]["result_binding"] = "enterprise_principal"
        return env, pol.model_copy(update={"values": values}), cfg
    row = support(pack, check_ids=("REQ-11",), mutate=mutate, managed=False)[0]
    assert row["status"] == "UNSUPPORTED" and row["policy_supported"] is False
    assert row["policy_valid"] is True


def test_manual_and_missing_policy_remain_distinct_from_automatic_support(pack):
    rows = support(pack, check_ids=("SUP-01", "REQ-09", "REQ-11"))
    assert rows[0]["executor"] is None and rows[0]["status"] == "UNSUPPORTED"
    assert "manual evidence import" in " ".join(rows[0]["reasons"])
    assert rows[1]["status"] == "UNKNOWN" and rows[1]["policy_valid"] is False
    assert rows[2]["status"] == "SUPPORTED"
    def mutate(env, pol, cfg):
        values = deepcopy(pol.values)
        del values["REQ-11"]["result_binding"]
        return env, pol.model_copy(update={"values": values}), cfg
    row = support(pack, check_ids=("REQ-11",), mutate=mutate)[0]
    assert row["status"] == "UNKNOWN" and "missing" in " ".join(row["reasons"])
    assert row["policy_valid"] is False and row["policy_supported"] is None


def test_runtime_gaps_for_existing_partial_collectors_are_visible(pack):
    from pathlib import Path
    import tomllib
    import yaml
    root = Path(__file__).resolve().parents[1]
    cfg = tomllib.loads((root / "examples/windows-cpu-binding-logs.toml").read_text(encoding="utf-8"))
    raw = yaml.safe_load((root / "examples/automation-binding-logs-policy.yaml").read_text(encoding="utf-8"))
    env, _, _ = declared_inputs(pack)
    pol = policy(pack.ref(), values=raw["values"])
    rows = assess_check_support(pack, env, pol, cfg, selected_check_ids=raw["scope"]["include"])
    assert len(rows) == 11
    assert {row["status"] for row in rows} == {"SUPPORTED"}
    by_id = {row["check_id"]: row for row in rows}
    assert "runner/count consumption remains unverified" in " ".join(by_id["REQ-10"]["limitations"])
    assert "exception_dump coverage is unverified" in " ".join(by_id["OBS-03"]["limitations"])
    assert "no independent binding oracle" in " ".join(by_id["REQ-11"]["limitations"])


def test_valid_token_input_scope_and_chat_private_scope_are_never_silently_reduced(pack):
    from pathlib import Path
    import tomllib
    import yaml
    root = Path(__file__).resolve().parents[1]
    cfg = tomllib.loads((root / "examples/windows-cpu-binding-logs.toml").read_text(encoding="utf-8"))
    raw = yaml.safe_load((root / "examples/automation-binding-logs-policy.yaml").read_text(encoding="utf-8"))
    env, _, _ = declared_inputs(pack)
    values = deepcopy(raw["values"])
    values["REQ-09"]["allowed_input_formats"] = ["token_ids", "text"]
    cfg["response_content"]["endpoint"] = "/v1/completions"
    pol = policy(pack.ref(), values=values)
    before = (deepcopy(values), deepcopy(cfg))
    rows = assess_check_support(pack, env, pol, cfg, selected_check_ids=["REQ-09", "REQ-13", "REQ-01"])
    assert [row["status"] for row in rows] == ["UNSUPPORTED", "UNSUPPORTED", "SUPPORTED"]
    assert (values, cfg) == before
    assert rows[0]["policy_valid"] is True and rows[0]["policy_supported"] is False
    assert rows[1]["policy_valid"] is True and rows[1]["policy_supported"] is True


@pytest.mark.parametrize(("check_id", "key", "value"), [
    ("REQ-07", "allowed_template_sha256", ["not-a-digest"]),
    ("REQ-07", "max_rendered_input_tokens", True),
    ("REQ-11", "result_binding", " "),
    ("OBS-09", "required_trusted_fields", ["process", "process"]),
])
def test_malformed_policy_never_becomes_valid_unsupported_scope(pack, check_id, key, value):
    def mutate(env, pol, cfg):
        values = deepcopy(pol.values)
        values[check_id][key] = value
        return env, pol.model_copy(update={"values": values}), cfg
    row = support(pack, check_ids=(check_id,), mutate=mutate)[0]
    assert row["policy_valid"] is False and row["policy_supported"] is None
    assert row["status"] == "UNKNOWN"


@pytest.mark.parametrize(("check_id", "executor", "changed_values", "bad_setting"), [
    ("REQ-09", "input_validation", {"allowed_input_formats": ["token_ids", "text"]}, {"max_probe_tokens": 1}),
    ("REQ-10", "sampling_validation", {"allowed_sampling_options": ["temperature", "future-option"]}, {"max_total_requests": 1000}),
    ("REQ-13", "response_content", {"forbidden_return_options": ["future-option"]}, {"request_timeout": 0}),
])
def test_unsupported_policy_keeps_execution_rejection_and_independent_config_validation(
        pack, check_id, executor, changed_values, bad_setting):
    from pathlib import Path
    import tomllib
    import yaml
    from guard.executors import registry
    root = Path(__file__).resolve().parents[1]
    cfg = tomllib.loads((root / "examples/windows-cpu-binding-logs.toml").read_text(encoding="utf-8"))
    raw = yaml.safe_load((root / "examples/automation-binding-logs-policy.yaml").read_text(encoding="utf-8"))
    values = {**raw["values"][check_id], **changed_values}
    factory = registry.resolve_executor(executor, "real")
    selected = factory(cfg)
    selected.validate_configuration(pack.checks[check_id])
    with pytest.raises(ValueError):
        selected.validate_policy(pack.checks[check_id], values)
    broken = deepcopy(cfg)
    broken[executor].update(bad_setting)
    with pytest.raises(ValueError):
        factory(broken).validate_configuration(pack.checks[check_id])


def log_inputs(pack):
    from pathlib import Path
    import tomllib
    import yaml
    root = Path(__file__).resolve().parents[1]
    cfg = tomllib.loads((root / "examples/windows-cpu-binding-logs.toml").read_text(encoding="utf-8"))
    raw = yaml.safe_load((root / "examples/automation-binding-logs-policy.yaml").read_text(encoding="utf-8"))
    env, _, _ = declared_inputs(pack)
    return env, policy(pack.ref(), values={"OBS-03": raw["values"]["OBS-03"]}), cfg


def log_executor(cfg):
    from guard.executors.log_analysis import LogAnalysisExecutor
    executor = object.__new__(LogAnalysisExecutor)
    executor.cfg = cfg["log_analysis"]
    return executor


@pytest.mark.parametrize(("key", "value"), [
    ("required_branches", ["normal", "error", "exception_dump", "audit"]),
    ("required_branches", ["normal", "error"]),
    ("forbidden_marker_contexts", ["caller_input", "enterprise_context"]),
])
def test_log_policy_scope_is_distinct_from_shape_and_preserves_execution_rejection(pack, key, value):
    env, pol, cfg = log_inputs(pack)
    pol.values["OBS-03"][key] = value
    before = deepcopy((pol.values, cfg))
    row, = assess_check_support(pack, env, pol, cfg, selected_check_ids=["OBS-03"])
    assert row["status"] == "UNSUPPORTED"
    assert row["policy_valid"] is True and row["policy_supported"] is False
    executor = log_executor(cfg)
    executor.validate_configuration(pack.checks["OBS-03"])
    with pytest.raises(ValueError):
        executor.validate_policy(pack.checks["OBS-03"], pol.values["OBS-03"])
    assert (pol.values, cfg) == before


@pytest.mark.parametrize(("key", "value"), [
    ("required_branches", []),
    ("required_sources", ["engine", "engine"]),
    ("allowed_log_fields", [""]),
    ("forbidden_marker_contexts", [" "]),
])
def test_log_malformed_policy_does_not_become_valid_unsupported(pack, key, value):
    env, pol, cfg = log_inputs(pack)
    pol.values["OBS-03"][key] = value
    row, = assess_check_support(pack, env, pol, cfg, selected_check_ids=["OBS-03"])
    assert row["status"] == "UNKNOWN"
    assert row["policy_valid"] is False and row["policy_supported"] is None
    with pytest.raises(ValueError):
        log_executor(cfg).validate_policy(pack.checks["OBS-03"], pol.values["OBS-03"])


def test_log_explicit_optional_empty_lists_remain_supported(pack):
    env, pol, cfg = log_inputs(pack)
    for key in ("forbidden_log_fields", "forbidden_marker_contexts"):
        pol.values["OBS-03"][key] = []
    row, = assess_check_support(pack, env, pol, cfg, selected_check_ids=["OBS-03"])
    assert row["status"] == "SUPPORTED"
    assert row["policy_valid"] is True and row["policy_supported"] is True
    log_executor(cfg).validate_policy(pack.checks["OBS-03"], pol.values["OBS-03"])


@pytest.mark.parametrize("settings", [
    {"request_timeout": 0},
    {"endpoint": "/unsupported"},
    {"log_sources": []},
    {"log_sources": [{"id": "engine", "container": "@engine", "kind": "docker_logs", "role": "copy"}]},
    {"log_sources": [{"id": "engine", "container": "@engine", "kind": "docker_logs"},
                     {"id": "engine", "container": "copy", "kind": "docker_logs"}]},
    {"log_sources": [{"id": "engine", "container": "@engine", "kind": "container_file", "path": "/proc/1/status"}]},
])
def test_log_unsupported_policy_cannot_bypass_invalid_settings_or_source_inventory(pack, settings):
    _, pol, cfg = log_inputs(pack)
    pol.values["OBS-03"]["required_branches"].append("audit")
    cfg["log_analysis"].update(settings)
    with pytest.raises(ValueError):
        log_executor(cfg).validate_configuration(pack.checks["OBS-03"])


def test_log_policy_must_still_reference_a_configured_local_engine_source(pack):
    _, pol, cfg = log_inputs(pack)
    cfg["log_analysis"]["log_sources"].append({"id": "copy", "container": "copy", "kind": "docker_logs", "role": "copy"})
    pol.values["OBS-03"]["required_sources"] = ["copy"]
    executor = log_executor(cfg)
    executor.validate_configuration(pack.checks["OBS-03"])
    with pytest.raises(ValueError, match="local engine source"):
        executor.validate_policy(pack.checks["OBS-03"], pol.values["OBS-03"])
