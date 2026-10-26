"""Only synthetic metadata; never engine/Docker/network or production verdicts."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.analyze_template_consumption import MAX_BYTES, MAX_EVENTS, analyze_example, main, parse_example


FIXTURE = Path(__file__).parent / "fixtures/template-consumption-example.json"


def example():
    return parse_example(FIXTURE.read_bytes())


def analyze(value):
    # The wire path and direct pure path must have the same result.
    result = analyze_example(value)
    assert analyze_example(parse_example(json.dumps(value).encode())) == result
    assert result["security"] == result["functional"] == result["performance"] == "UNKNOWN"
    assert result["data_origin"] == "EXAMPLE" and result["official"] is False
    assert result["actual_consumption_measured"] is result["template_consumer_verified"] is False
    assert result["independent_sensor_verified"] is result["challenge_exercised"] is False
    return result


@pytest.mark.parametrize("approved", [True, False])
@pytest.mark.parametrize("cache", ["hit", "miss"])
def test_complete_example_chain_is_record_consistency_only(approved, cache):
    value = example()
    value["approval"]["allowed_raw_sha256"] = ["3" * 64] if approved else []
    value["events"][1]["cache_status"] = cache
    original = deepcopy(value)
    result = analyze(value)
    assert result["data_consistency"] == "consistent" and result["gaps"] == []
    assert result["requests"][0]["declared_raw_approval_membership"] is approved
    assert value == original


def test_raw_bytes_and_decoded_template_are_distinct_not_interchangeable():
    value = example()
    assert value["events"][0]["raw_sha256"] != value["events"][0]["decoded_sha256"]
    value["approval"]["allowed_raw_sha256"] = [value["events"][0]["decoded_sha256"]]
    result = analyze(value)
    assert result["data_consistency"] == "consistent"
    assert result["requests"][0]["declared_raw_approval_membership"] is False


@pytest.mark.parametrize("stage", ["loader", "renderer"])
def test_open_or_read_before_parse_failure_does_not_complete_consumption(stage):
    value = example()
    if stage == "loader":
        value["events"][0]["outcome"] = "parse_error"
        code = "READ_OR_PARSE_FAILED"
    else:
        value["events"][2].update(render_result="parse_error", rendered_input_sha256=None)
        code = "TEMPLATE_APPLY_NOT_SUCCESSFUL"
    result = analyze(value)
    assert result["data_consistency"] == "inconsistent" and code in result["gaps"]


@pytest.mark.parametrize("mutate,code", [
    (lambda v: v["events"][1].update(selected_sha256="7" * 64), "READ_SELECTION_DIGEST_MISMATCH"),
    (lambda v: v["events"][1].update(cache_status="hit", cache_generation="EXAMPLE-old-cache"), "CACHE_GENERATION_MISMATCH"),
    (lambda v: v["events"][2].update(render_result="bypassed"), "TEMPLATE_APPLY_NOT_SUCCESSFUL"),
    (lambda v: v["events"][2].update(applied_sha256="7" * 64), "SELECTED_APPLIED_DIGEST_MISMATCH"),
    (lambda v: v["events"][3].update(rendered_input_sha256="7" * 64), "RENDER_HANDOFF_DIGEST_MISMATCH"),
    (lambda v: v["events"][3].update(selection_id="EXAMPLE-unrelated-selection"), "SELECTION_HANDOFF_MISMATCH"),
    (lambda v: v["events"][1].update(load_id="EXAMPLE-before-capture-load"), "SUCCESSFUL_READ_NOT_BEFORE_SELECTION"),
])
def test_selection_cache_apply_and_handoff_counterexamples(mutate, code):
    value = example()
    mutate(value)
    result = analyze(value)
    assert result["data_consistency"] == "inconsistent" and code in result["gaps"]


def test_cache_hit_without_startup_read_is_incomplete_even_if_hashes_match():
    value = example()
    value["events"].pop(0)
    value["events"][0]["cache_status"] = "hit"
    result = analyze(value)
    assert {"SUCCESSFUL_READ_NOT_BEFORE_SELECTION", "SOURCE_GAP_OR_SEQUENCE"} <= set(result["gaps"])
    assert result["requests"][0]["declared_raw_approval_membership"] is None


@pytest.mark.parametrize("field,new", [
    ("process_start", "EXAMPLE-restarted-same-pid"),
    ("container_sha256", "8" * 64), ("image_sha256", "8" * 64),
    ("model_generation", "EXAMPLE-model-2"), ("tokenizer_generation", "EXAMPLE-tokenizer-2"),
    ("model_snapshot_sha256", "8" * 64), ("tokenizer_snapshot_sha256", "8" * 64),
    ("renderer_generation", "EXAMPLE-renderer-2"),
])
def test_restart_pid_reuse_model_and_tokenizer_replacement_are_not_same_instance(field, new):
    value = example()
    # PID stays 101; its reuse cannot hide a different process start/generation.
    value["events"][1]["context"][field] = new
    result = analyze(value)
    assert "INSTANCE_OR_GENERATION_CHANGED" in result["gaps"]
    assert "LOAD_SELECTION_INSTANCE_MISMATCH" in result["gaps"]


@pytest.mark.parametrize("field", ["nonce", "request_id", "subrequest_id", "input_sha256"])
def test_swapped_request_part_cannot_use_another_requests_chain(field):
    value = example()
    other = {"nonce": "EXAMPLE-nonce-2", "request_id": "EXAMPLE-request-2",
             "subrequest_id": "EXAMPLE-subrequest-2", "input_sha256": "9" * 64}
    value["requests"].append(other)
    value["events"][3][field] = other[field]
    result = analyze(value)
    assert "REQUEST_BINDING_MISMATCH" in result["gaps"]


def two_requests():
    value = example()
    request = {"nonce": "EXAMPLE-nonce-2", "request_id": "EXAMPLE-request-2",
               "subrequest_id": "EXAMPLE-subrequest-2", "input_sha256": "9" * 64}
    value["requests"].append(request)
    for index, event in enumerate(deepcopy(value["events"][1:])):
        event.update(request)
        event.update(event_id=f"EXAMPLE-second-{index}", elapsed_ms=50 + index * 10,
                     selection_id="EXAMPLE-selection-2")
        event["seq"] += 2 if event["kind"] != "engine_handoff" else 1
        value["events"].append(event)
    value["collection"]["sources"][1]["last_seq"] = 4
    value["collection"]["sources"][2]["last_seq"] = 2
    return value


def test_two_planned_request_chains_can_share_one_observed_startup_load():
    result = analyze(two_requests())
    assert result["data_consistency"] == "consistent" and len(result["requests"]) == 2


def test_complete_handoff_request_bindings_swapped_between_two_chains_are_rejected():
    value = two_requests()
    # Preserve the internally plausible ID bundles; exchange which request
    # receives each handoff. Selection/order still must agree end to end.
    value["events"][3].update(value["requests"][1])
    value["events"][6].update(value["requests"][0])
    result = analyze(value)
    assert result["data_consistency"] == "inconsistent"
    assert {"SELECTION_HANDOFF_MISMATCH", "REQUEST_CHAIN_INCOMPLETE_OR_REORDERED"} <= set(result["gaps"])


@pytest.mark.parametrize("mutate,code", [
    (lambda v: v["events"].append(deepcopy(v["events"][-1])), "DUPLICATE_EVENT"),
    (lambda v: v["events"].reverse(), "EVENT_OUT_OF_ORDER"),
    (lambda v: v["events"].pop(), "REQUEST_CHAIN_INCOMPLETE_OR_REORDERED"),
    (lambda v: v["events"][2].update(seq=3), "SOURCE_GAP_OR_SEQUENCE"),
    (lambda v: v["events"][2].update(elapsed_ms=1001), "EVENT_OUTSIDE_WINDOW"),
    (lambda v: v["collection"].update(closed=False), "COLLECTION_NOT_CLOSED"),
    (lambda v: v["collection"]["sources"][0].update(complete=False), "SOURCE_GAP_OR_SEQUENCE"),
    (lambda v: v["events"][1].update(source_id="EXAMPLE-loader"), "SOURCE_ROLE_MISMATCH"),
])
def test_duplicate_reordered_partial_time_and_source_gap_counterexamples(mutate, code):
    value = example()
    mutate(value)
    result = analyze(value)
    assert result["data_consistency"] == "inconsistent" and code in result["gaps"]


def test_selected_before_read_is_inconsistent_without_relying_on_timestamps():
    value = example()
    value["events"][0], value["events"][1] = value["events"][1], value["events"][0]
    for index, event in enumerate(value["events"]):
        event["elapsed_ms"] = index * 10
    assert "SUCCESSFUL_READ_NOT_BEFORE_SELECTION" in analyze(value)["gaps"]


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(data_origin="MEASURED"),
    lambda v: v["source_review"].update(source_commit="a" * 40),
    lambda v: v["collection"].update(end_ms=300001),
    lambda v: v["events"][0].update(seq=True),
    lambda v: v["events"][0].update(raw_bytes=8193),
    lambda v: v["events"][0].update(decoded_bytes=101),
    lambda v: v["events"][0].update(decoded_sha256="3" * 64),
    lambda v: v["events"].extend([deepcopy(v["events"][0])] * MAX_EVENTS),
    lambda v: v["events"][0].update(source_kind="file_open_only"),
    lambda v: v["events"][0].update(raw_sha256="EXAMPLE-invalid"),
    lambda v: v["requests"].append(deepcopy(v["requests"][0])),
    lambda v: v["approval"]["allowed_raw_sha256"].append("3" * 64),
])
def test_unsupported_or_unbounded_input_is_rejected(mutate):
    value = example()
    mutate(value)
    with pytest.raises(ValueError):
        parse_example(json.dumps(value).encode())


@pytest.mark.parametrize("field", ["template", "tokens", "body", "kwargs", "host_path", "api_projection", "signature"])
def test_raw_payloads_and_unimplemented_authentication_fields_are_rejected_without_echo(field):
    value = example()
    secret = "EXAMPLE-private-value-never-echo"
    value["events"][0][field] = secret
    with pytest.raises(ValueError) as caught:
        parse_example(json.dumps(value).encode())
    assert secret not in str(caught.value) and field not in str(caught.value)


@pytest.mark.parametrize("payload", [b'{"schema":1,"schema":2}', b'{"x":NaN}', b'\xff', b'[]', b'{'])
def test_malformed_or_duplicate_key_wire_input_is_rejected(payload):
    with pytest.raises(ValueError):
        parse_example(payload)


def test_byte_budget_applies_before_json_parsing():
    with pytest.raises(ValueError, match="input_byte_limit"):
        parse_example(b" " * (MAX_BYTES + 1))


def test_cli_is_read_only_and_exit_zero_is_not_security_pass(tmp_path, capsys):
    path = tmp_path / "example.json"
    original = FIXTURE.read_bytes()
    path.write_bytes(original)
    assert main([str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data_consistency"] == "consistent" and result["security"] == "UNKNOWN"
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_cli_errors_never_echo_private_input_or_paths(tmp_path, capsys):
    path = tmp_path / "EXAMPLE-private-host-path.json"
    path.write_text('{"body":"EXAMPLE-private-body"}', encoding="utf-8")
    assert main([str(path)]) == 2
    text = capsys.readouterr().out
    assert json.loads(text)["error"] == "invalid_example_input"
    assert str(path) not in text and "EXAMPLE-private-body" not in text


def test_diagnostic_tool_does_not_replace_product_observation_only_contract():
    import yaml
    from tools.analyze_template_consumption import ANALYSIS_SCHEMA
    root = Path(__file__).resolve().parents[1]
    definition = yaml.safe_load((root / "packs/PACK-BASELINE/checks/SUP-03.yaml").read_text(encoding="utf-8"))
    assert definition["judge"]["params"]["pass_when_all"] == definition["judge"]["params"]["fail_when_any"] == []
    assert definition["judge"]["params"]["axes"]["functional"] == {"pass_when_all": [], "fail_when_any": []}
    assert definition["collection_profile"] == "fixed-template-supply-v1"
    assert ANALYSIS_SCHEMA not in json.dumps(definition)
