"""Pure CPU case-manifest checks. These tests never start Docker or a subprocess."""
from copy import deepcopy
import tomllib

import pytest

from tools.validate_cpu_cases import CHECKS, EXECUTORS, _proof, build_cases, compare_report, config_toml, validate_seed_inputs


def config():
    return {"adapter": {"name": "fake"}, "executors": {"other": "fake"},
            "vllm": {"launch_profile": "cpu-bounded", "model_layout": "snapshot", "models_dir": "C:\\models\\snapshot",
                     "max_model_len": 128, "cpu_threads": 2,
                     "resources": {"cpus": 2, "memory_bytes": 3221225472}},
            "input_validation": {"vocab_size": 50272, "vocab_source": "EXAMPLE fixture reference", "max_probe_tokens": 128},
            "direct_route": {"roles": {"allowed": "GUARD_CPU_GENERAL"}, "target": "do-not-reuse.json"},
            "sides": {"candidate": {"executors": {"input_validation": "fake"}}},
            "update": {"mode": "external"}}


def test_case_config_preserves_other_executor_source_tables_and_windows_paths():
    source = config()
    source['log_analysis'] = {'log_sources': [
        {'id': 'engine', 'kind': 'docker_logs', 'container': '@engine', 'role': 'engine'}]}
    for case in build_cases(source, 'PACK-BASELINE', 17):
        restored = tomllib.loads(config_toml(case['config']))
        assert restored == case['config']
        assert restored['vllm']['models_dir'] == source['vllm']['models_dir']


def environment():
    return {"data_origin": "DECLARED", "engine": {"name": "vllm", "version": "0.31.0",
            "image_digest": "example/cpu@sha256:" + "a" * 64},
            "model": {"id": "example-model", "revision": "pinned-example-revision", "inventory_sha256": "b" * 64},
            "runtime": {"gpu_count": 0, "cpu": {"system": "Linux", "architecture": "amd64", "threads": 2}},
            "facts": {"gateway": {"value": True, "origin": "DECLARED"}}}


def report(expected):
    return {"official": False, "policy_status": "DRAFT",
            "by_check": [{"check_id": cid, "selected": True,
                          "single": {"security": value, "functional": "PASS", "performance": "UNKNOWN"}}
                         for cid, value in expected.items()]}


def test_three_cases_have_exact_five_checks_and_only_real_executors():
    cases = build_cases(config(), "PACK-BASELINE", 17)
    assert len(cases) == 3
    assert [set(c["expected"].values()) if c["expected"] is not None else None for c in cases] == [{"FAIL"}, {"PASS"}, None]
    assert cases[2]["name"] == "short-timeout-observation"
    assert cases[2]["observation_contract"] == "short-timeout"
    assert cases[2]["legacy_hypothesis"] == dict.fromkeys(CHECKS, "UNKNOWN")
    for case in cases:
        assert tuple(case["legacy_hypothesis"]) == CHECKS
        assert case["policy"]["scope"] == {"include": list(CHECKS), "exclude": []}
        assert case["config"]["executors"] == dict.fromkeys(EXECUTORS, "real")
        assert case["config"]["adapter"]["name"] == "vllm"
        assert "sides" not in case["config"] and "update" not in case["config"]
        assert all("target" not in case["config"][e] and "container" not in case["config"][e] for e in EXECUTORS)


def test_generated_policies_are_explicit_examples_with_no_enterprise_approval():
    for case in build_cases(config(), "PACK-BASELINE", 17):
        policy = case["policy"]
        assert policy["data_origin"] == "EXAMPLE" and policy["status"] == "DRAFT"
        assert policy["confirmed_by"] is None and policy["confirmed_at"] is None
        assert policy["pack"] == {"id": "PACK-BASELINE", "revision": 17}
        assert policy["values"]["PLT-04"]["forbidden_env_names"] == ["GUARD_TEST_CREDENTIAL"]
        assert any("not proof of memory corruption" in s for s in case["limitations"])


def test_fail_configuration_uses_fixed_canary_and_deliberate_policy_boundary():
    case = build_cases(config(), "PACK-BASELINE", 17)[0]
    assert case["config"]["validation"] == {"mode": "managed", "topology": "exposed", "gateway_profile": "role_open",
                                           "engine_profile": "default", "credential_canary": True}
    assert case["policy"]["values"]["REQ-09"]["max_input_tokens"] == 8
    assert case["config"]["vllm"]["max_model_len"] == 128
    assert "env" not in case["config"]["vllm"]


def test_pass_and_unknown_use_identical_safe_profiles_except_actual_timeout():
    _, normal, unknown = build_cases(config(), "PACK-BASELINE", 17)
    assert normal["policy"] == unknown["policy"]
    assert normal["config"]["validation"] == unknown["config"]["validation"]
    assert normal["config"]["validation"]["credential_canary"] is False
    assert normal["policy"]["values"]["REQ-09"]["max_input_tokens"] == 127
    for executor in EXECUTORS:
        assert normal["config"][executor]["request_timeout"] == 30.0
        assert unknown["config"][executor]["request_timeout"] == 0.000001
        assert unknown["config"][executor]["request_timeout"] > 0


def test_source_config_is_not_modified_or_replaced_by_generated_cases():
    source = config()
    before = deepcopy(source)
    cases = build_cases(source, "PACK-BASELINE", 17)
    cases[0]["config"]["vllm"]["resources"]["cpus"] = 1
    cases[0]["policy"]["values"]["REQ-09"]["max_input_tokens"] = 100
    assert source == before
    assert cases[1]["config"]["vllm"]["resources"]["cpus"] == 2
    assert cases[1]["policy"]["values"]["REQ-09"]["max_input_tokens"] == 127


def test_config_writer_roundtrip_including_windows_paths_and_small_positive_timeout():
    for case in build_cases(config(), "PACK-BASELINE", 17):
        actual = tomllib.loads(config_toml(case["config"]))
        assert actual == case["config"]


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), {"bad": object()}])
def test_config_writer_rejects_unsupported_values(bad):
    with pytest.raises(ValueError):
        config_toml({"value": bad})


def test_matching_expected_and_actual_report_remains_nonofficial():
    expected = dict.fromkeys(CHECKS, "PASS")
    actual = report(expected)
    result = compare_report(actual, expected)
    assert result["matched"] is True
    assert result["actual"]["REQ-09"]["security"] == "PASS"


def test_mismatch_preserves_actual_outcome_instead_of_forcing_expectations():
    expected = dict.fromkeys(CHECKS, "PASS")
    actual = report(expected)
    actual["by_check"][0]["single"]["security"] = "UNKNOWN"
    before = deepcopy(actual)
    result = compare_report(actual, expected)
    assert result["matched"] is False
    assert result["actual"]["REQ-01"]["security"] == "UNKNOWN"
    assert actual == before


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "official", "confirmed"])
def test_invalid_report_scope_or_official_status_is_rejected(change):
    expected = dict.fromkeys(CHECKS, "PASS")
    actual = report(expected)
    if change == "missing":
        actual["by_check"].pop()
    elif change == "extra":
        actual["by_check"].append({"check_id": "REQ-10", "selected": True, "single": {"security": "PASS"}})
    elif change == "duplicate":
        actual["by_check"].append(deepcopy(actual["by_check"][0]))
    elif change == "official":
        actual["official"] = True
    else:
        actual["policy_status"] = "CONFIRMED"
    assert compare_report(actual, expected)["matched"] is False


def test_seed_identity_validation_does_not_modify_declarations():
    cfg, env = config(), environment()
    before = deepcopy((cfg, env))
    validate_seed_inputs(cfg, env)
    assert (cfg, env) == before


@pytest.mark.parametrize("profile", ["guard-chat-fixture-v1", None, ""])
def test_fixed_completion_matrix_rejects_any_explicit_chat_template_profile(profile):
    cfg, env = config(), environment()
    cfg["vllm"]["chat_template_profile"] = profile
    before = deepcopy((cfg, env))
    with pytest.raises(ValueError, match="completions-only"):
        validate_seed_inputs(cfg, env)
    assert (cfg, env) == before


@pytest.mark.parametrize("change", ["example", "unpinned-image", "no-inventory", "gpu", "cpu-host", "no-gateway", "wrong-thread"])
def test_seed_identity_cannot_be_invented_from_missing_or_inconsistent_fields(change):
    cfg, env = config(), environment()
    if change == "example":
        env["data_origin"] = "EXAMPLE"
    elif change == "unpinned-image":
        env["engine"]["image_digest"] = "example/cpu:latest"
    elif change == "no-inventory":
        env["model"]["inventory_sha256"] = None
    elif change == "gpu":
        env["runtime"]["gpu_count"] = 1
    elif change == "cpu-host":
        env["runtime"]["cpu"]["system"] = "Windows"
    elif change == "no-gateway":
        env["facts"]["gateway"] = False
    else:
        env["runtime"]["cpu"]["threads"] = 3
    with pytest.raises(ValueError):
        validate_seed_inputs(cfg, env)


def test_incomplete_proof_does_not_count_as_fifteen_measured_results():
    assert _proof([])["problems"]
    assert _proof([])["refs"] == []


def test_no_fixed_timing_verdict_is_only_scope_comparison_not_the_evidence_contract():
    actual = report(dict.fromkeys(CHECKS, "UNKNOWN"))
    actual["by_check"][2]["single"]["security"] = "PASS"
    comparison = compare_report(actual, None)
    assert comparison["matched"] is True
    assert comparison["expected_security"] is None
    assert "observation_contract" not in comparison
