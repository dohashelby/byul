"""REQ-02 합성 증거 계약. 모든 사건/출처/신원은 EXAMPLE이며 엔진은 실행하지 않는다."""
from copy import deepcopy
import hashlib
import json

import pytest

from tools import analyze_route_rewrite as contract


def _ref(kind):
    return {"kind": kind, "id": f"{kind}-EXAMPLE", "revision": 1, "sha256": "a" * 64}


def _world(*, bypass=False):
    """proxy/handler 신뢰 수집원을 가정한 EXAMPLE. 실제 수집원 구현이 아니다."""
    inputs = {"schema": contract.SCHEMA, "data_origin": "EXAMPLE", "run_id": "EXAMPLE-run",
              "refs": {key: _ref(kind) for key, kind in
                       (("pack", "PACK"), ("plan", "PLAN"), ("environment", "ENV"), ("policy", "POL"))},
              "check": {"id": "REQ-02", "revision": 2, "sha256": "b" * 64},
              "policy_values": {"operation": "text_completion", "allowed_principals": ["allowed"],
                                "allowed_external_paths": ["/v1/completions", "/tenant/v1/completions"]},
              "fixture": {"external_origin": "http://EXAMPLE-proxy:8080", "endpoint": dict(contract.ENDPOINT),
                          "handler_id": "EXAMPLE-completion-handler",
                          "source_identities": {"proxy_auth": "EXAMPLE-proxy-instance",
                                                "engine_handler": "EXAMPLE-engine-instance"}}, "trials": []}
    for index, case in enumerate(("normal_base", "normal_prefix", "challenge")):
        inputs["trials"].append({"id": f"EXAMPLE-trial-{index}", "case": case,
            "principal": "untrusted" if case == "challenge" else "allowed",
            "external_path": "/v1/completions" if case == "normal_base" else "/tenant/v1/completions",
            "request_sha256": hashlib.sha256(f"EXAMPLE-request-{index}".encode()).hexdigest(),
            "proxy_request_id": f"EXAMPLE-server-ingress-{index}",
            "upstream_request_id": f"EXAMPLE-private-upstream-{index}",
            "client_request_id": "EXAMPLE-caller-controlled-same-ID"})
        inputs["trials"][-1]["external_url"] = inputs["fixture"]["external_origin"] + inputs["trials"][-1]["external_path"]
    capture = {"schema": contract.SCHEMA, "data_origin": "EXAMPLE", "run_id": inputs["run_id"],
               "inputs_sha256": contract.validate_example_inputs(inputs), "sources": {}, "events": []}
    for source, identity in inputs["fixture"]["source_identities"].items():
        capture["sources"][source] = {"data_origin": "EXAMPLE", "identity_before": identity,
                                      "identity_after": identity, "complete": True}
    for index, trial in enumerate(inputs["trials"]):
        challenge = trial["case"] == "challenge"
        forwarded = not challenge or bypass
        proxy = {"data_origin": "EXAMPLE", "source": "proxy_auth", "run_id": inputs["run_id"],
                 "source_identity": inputs["fixture"]["source_identities"]["proxy_auth"],
                 "event_id": f"EXAMPLE-proxy-{index}", "proxy_request_id": trial["proxy_request_id"],
                 "upstream_request_id": trial["upstream_request_id"], "request_sha256": trial["request_sha256"],
                 "principal": trial["principal"], "external_method": "POST", "external_path": trial["external_path"],
                 "external_url": trial["external_url"],
                 "auth_method": "POST", "auth_path": "/public" if challenge and bypass else trial["external_path"],
                 "auth_operation": "public_read" if challenge and bypass else "text_completion",
                 "auth_decision": "allow" if forwarded else "deny", "rewritten_method": "POST",
                 "rewritten_path": "/v1/completions", "dispatch": "forwarded" if forwarded else "blocked",
                 "client_request_id": trial["client_request_id"], "http_status": 200 if forwarded else 403}
        capture["events"].append(proxy)
        if forwarded:
            capture["events"].append({"data_origin": "EXAMPLE", "source": "engine_handler",
                "run_id": inputs["run_id"], "source_identity": inputs["fixture"]["source_identities"]["engine_handler"],
                "event_id": f"EXAMPLE-engine-{index}", "proxy_request_id": trial["proxy_request_id"],
                "upstream_request_id": trial["upstream_request_id"], "request_sha256": trial["request_sha256"],
                "method": "POST", "path": "/v1/completions", "operation": "text_completion",
                "handler_id": "EXAMPLE-completion-handler", "execution": "entered", "normal_generation": True,
                "normal_generation_basis": "structured_probe_response", "client_request_id": trial["client_request_id"]})
    return inputs, capture


def _bytes(capture):
    return json.dumps(capture, ensure_ascii=False).encode("utf-8")


def _analyze(inputs, capture):
    return contract.analyze_example_capture(_bytes(capture), inputs=inputs)


def _challenge(capture, source):
    return next(event for event in capture["events"] if event["source"] == source and event["event_id"].endswith("-2"))


def test_normal_prefix_and_explicit_denial_link_without_connecting_product():
    inputs, capture = _world()
    result = _analyze(inputs, capture)
    assert result["example_chain_state"] == "COMPLETE_WITHOUT_BYPASS"
    assert result["example_coverage_complete"] and result["example_normal_controls_verified"]
    assert result["example_linked_trials"] == [trial["id"] for trial in inputs["trials"]]
    assert result["example_bypass_trials"] == [] and result["gaps"] == []
    assert result["data_origin"] == "EXAMPLE"
    assert result["guard_check_status"] == {"check_id": "REQ-02", "connected": False, "security": "UNKNOWN"}
    assert result["product_connected"] is False and result["trusted_collection_implemented"] is False


def test_exact_example_auth_rewrite_and_final_handler_chain_finds_only_example_bypass():
    inputs, capture = _world(bypass=True)
    result = _analyze(inputs, capture)
    assert result["example_chain_state"] == "BYPASS_OBSERVED"
    assert result["example_bypass_trials"] == ["EXAMPLE-trial-2"]
    detail = result["example_trial_details"][-1]
    assert detail == {"trial": "EXAMPLE-trial-2", "auth_path": "/public", "auth_operation": "public_read",
                      "auth_decision": "allow", "final_operation": "text_completion",
                      "handler_entered": True, "policy_allows": False}
    assert result["guard_check_status"]["security"] == "UNKNOWN"
    assert "FAIL" not in json.dumps(result)  # Synthetic diagnosis is never a Guard FAIL.


@pytest.mark.parametrize("missing", ["proxy_auth", "engine_handler"])
def test_http_200_and_rewrite_config_do_not_replace_missing_source_event(missing):
    inputs, capture = _world(bypass=True)
    capture["http_status"] = 200
    capture["config"] = "all paths rewrite to /v1/completions"
    capture["events"].remove(_challenge(capture, missing))
    result = _analyze(inputs, capture)
    assert result["example_chain_state"] == "INCONCLUSIVE"
    assert result["example_bypass_trials"] == []
    assert result["example_coverage_complete"] is False


@pytest.mark.parametrize("key", ["external_url", "auth_path", "auth_method", "auth_operation", "principal", "rewritten_path", "dispatch"])
def test_partial_authorization_and_rewrite_chain_cannot_confirm_bypass(key):
    inputs, capture = _world(bypass=True)
    del _challenge(capture, "proxy_auth")[key]
    assert _analyze(inputs, capture)["example_bypass_trials"] == []


def test_equal_client_ids_and_forged_client_fields_are_never_used_for_binding():
    inputs, capture = _world()
    for event in capture["events"]:
        event.update(client_request_id="EXAMPLE-same-for-everyone",
                     client_fields={"principal": "allowed", "auth_decision": "allow", "execution": "entered"})
    result = _analyze(inputs, capture)
    assert result["example_chain_state"] == "COMPLETE_WITHOUT_BYPASS"
    assert result["example_bypass_trials"] == []


def test_another_external_authority_is_not_the_same_probe_even_with_the_same_path():
    inputs, capture = _world(bypass=True)
    _challenge(capture, "proxy_auth")["external_url"] = "http://EXAMPLE-other-proxy:8080/tenant/v1/completions"
    assert _analyze(inputs, capture)["example_bypass_trials"] == []


@pytest.mark.parametrize("swap_key", ["proxy_request_id", "upstream_request_id", "request_sha256"])
def test_server_binding_exchange_cannot_be_repaired_by_equal_client_id(swap_key):
    inputs, capture = _world(bypass=True)
    engine = [event for event in capture["events"] if event["source"] == "engine_handler"]
    engine[0][swap_key], engine[2][swap_key] = engine[2][swap_key], engine[0][swap_key]
    result = _analyze(inputs, capture)
    assert result["example_bypass_trials"] == []
    assert not result["example_coverage_complete"]


@pytest.mark.parametrize("key,value", [("handler_id", "EXAMPLE-unknown-handler"), ("path", "/v1/responses"),
                                      ("operation", "admin_operation"), ("execution", "http_200")])
def test_unsupported_or_unknown_handler_never_confirms_execution(key, value):
    inputs, capture = _world(bypass=True)
    _challenge(capture, "engine_handler")[key] = value
    result = _analyze(inputs, capture)
    assert result["example_bypass_trials"] == []
    assert any("FINAL_HANDLER_UNVERIFIED_OR_UNSUPPORTED" in gap for gap in result["gaps"])


def test_handler_rejection_is_distinct_from_actual_execution_even_with_http_200():
    inputs, capture = _world(bypass=True)
    _challenge(capture, "engine_handler")["execution"] = "rejected"
    assert _analyze(inputs, capture)["example_bypass_trials"] == []


def test_engine_log_absence_is_insufficient_without_explicit_proxy_block():
    inputs, capture = _world()
    del _challenge(capture, "proxy_auth")["dispatch"]
    result = _analyze(inputs, capture)
    assert result["example_chain_state"] == "INCONCLUSIVE"


def test_duplicate_events_and_conflicting_engine_dispatch_are_ambiguous():
    inputs, capture = _world(bypass=True)
    capture["events"].append(deepcopy(_challenge(capture, "engine_handler")))
    result = _analyze(inputs, capture)
    assert "DUPLICATE_EVENT_ID" in result["gaps"] and result["example_bypass_trials"] == []
    inputs, capture = _world(bypass=True)
    _challenge(capture, "proxy_auth")["dispatch"] = "blocked"
    assert _analyze(inputs, capture)["example_bypass_trials"] == []


def test_distinct_duplicate_handler_events_are_not_silently_collapsed():
    inputs, capture = _world(bypass=True)
    extra = deepcopy(_challenge(capture, "engine_handler"))
    extra["event_id"] = "EXAMPLE-other-handler-event"
    capture["events"].append(extra)
    assert _analyze(inputs, capture)["example_bypass_trials"] == []


def test_later_gap_keeps_an_independently_complete_example_bypass():
    inputs, capture = _world(bypass=True)
    capture["events"] = [event for event in capture["events"] if event["event_id"] != "EXAMPLE-engine-0"]
    capture["sources"]["engine_handler"]["complete"] = False
    result = _analyze(inputs, capture)
    assert result["example_chain_state"] == "BYPASS_OBSERVED"
    assert result["example_bypass_trials"] == ["EXAMPLE-trial-2"]
    assert not result["example_normal_controls_verified"] and not result["example_coverage_complete"]


@pytest.mark.parametrize("key", ["normal_generation", "normal_generation_basis"])
def test_http_success_without_structured_generation_does_not_complete_normal_control(key):
    inputs, capture = _world()
    event = next(event for event in capture["events"] if event["event_id"] == "EXAMPLE-engine-0")
    del event[key]
    assert _analyze(inputs, capture)["example_chain_state"] == "INCONCLUSIVE"


@pytest.mark.parametrize("source", contract.SOURCE_NAMES)
def test_changed_or_missing_source_identity_invalidates_its_events(source):
    inputs, capture = _world(bypass=True)
    capture["sources"][source]["identity_after"] = "EXAMPLE-restarted-instance"
    result = _analyze(inputs, capture)
    assert result["example_bypass_trials"] == []
    assert any("SOURCE_IDENTITY_UNVERIFIED" in gap for gap in result["gaps"])


@pytest.mark.parametrize("origin", ["MEASURED", "DECLARED", None])
@pytest.mark.parametrize("location", ["inputs", "capture", "event", "source"])
def test_real_declared_or_unlabelled_material_cannot_be_promoted(origin, location):
    inputs, capture = _world()
    selected = {"inputs": inputs, "capture": capture, "event": capture["events"][0],
                "source": capture["sources"]["proxy_auth"]}[location]
    selected["data_origin"] = origin
    with pytest.raises(ValueError):
        _analyze(inputs, capture)


def test_ordinary_server_log_and_duplicate_json_keys_are_rejected():
    inputs, _ = _world()
    for payload in (b'127.0.0.1 "POST /v1/completions HTTP/1.1" 200\n',
                    b'{"schema":"a","schema":"b"}', b'{"x":NaN}', b'\xff', b'{}\n{}'):
        with pytest.raises(ValueError):
            contract.analyze_example_capture(payload, inputs=inputs)


@pytest.mark.parametrize("kind", ["pack", "plan", "environment", "policy"])
def test_different_exact_ref_is_not_a_replay_of_the_same_capture(kind):
    inputs, capture = _world(bypass=True)
    inputs["refs"][kind]["revision"] += 1
    result = _analyze(inputs, capture)
    assert result["example_chain_state"] == "INCONCLUSIVE"
    assert result["gaps"] == ["CAPTURE_INPUT_BINDING_MISMATCH"]


@pytest.mark.parametrize("field", ["revision", "sha256"])
def test_different_definition_revision_or_bytes_hash_cannot_reuse_capture(field):
    inputs, capture = _world()
    inputs["check"][field] = 3 if field == "revision" else "c" * 64
    assert _analyze(inputs, capture)["gaps"] == ["CAPTURE_INPUT_BINDING_MISMATCH"]


def test_policy_values_are_explicit_and_separate_from_technical_fixture():
    inputs, capture = _world()
    inputs["fixture"]["allowed_principals"] = ["untrusted"]
    with pytest.raises(ValueError, match="single safe"):
        _analyze(inputs, capture)
    inputs, capture = _world()
    inputs["policy_values"]["allowed_principals"] = '["allowed"]'
    with pytest.raises(ValueError, match="allowed_principals"):
        _analyze(inputs, capture)
    inputs, capture = _world()
    del inputs["policy_values"]["allowed_principals"]
    with pytest.raises(ValueError, match="no policy defaults"):
        _analyze(inputs, capture)
    inputs, capture = _world()
    inputs["policy_values"]["operation"] = "admin_operation"
    with pytest.raises(ValueError, match="explicitly name"):
        _analyze(inputs, capture)


def test_unplanned_event_prevents_complete_scope_without_erasing_exact_example_bypass():
    inputs, capture = _world(bypass=True)
    event = deepcopy(_challenge(capture, "engine_handler"))
    event.update(event_id="EXAMPLE-unplanned", upstream_request_id="EXAMPLE-unplanned-upstream")
    capture["events"].append(event)
    result = _analyze(inputs, capture)
    assert result["example_bypass_trials"] == ["EXAMPLE-trial-2"]
    assert not result["example_coverage_complete"] and "UNPLANNED_SOURCE_EVENT" in result["gaps"]


@pytest.mark.parametrize("field", ["proxy_request_id", "upstream_request_id", "auth_decision"])
def test_malformed_event_values_leave_gap_instead_of_accepting_the_event(field):
    inputs, capture = _world(bypass=True)
    _challenge(capture, "proxy_auth")[field] = {"client_field": "not-a-source-identifier"}
    result = _analyze(inputs, capture)
    assert result["example_bypass_trials"] == [] and result["example_coverage_complete"] is False


def test_explicit_empty_policy_is_never_filled_from_observed_principals():
    inputs, capture = _world()
    inputs["policy_values"]["allowed_principals"] = []
    capture["inputs_sha256"] = contract.validate_example_inputs(inputs)
    result = _analyze(inputs, capture)
    assert result["example_bypass_trials"] == ["EXAMPLE-trial-0", "EXAMPLE-trial-1"]
    assert not result["example_normal_controls_verified"]
    assert result["guard_check_status"]["security"] == "UNKNOWN"


def test_capture_bounds_and_ambiguous_ledger_are_rejected():
    inputs, capture = _world()
    with pytest.raises(ValueError, match="bounded capture"):
        contract.analyze_example_capture(b" " * (contract.MAX_CAPTURE_BYTES + 1), inputs=inputs)
    capture["events"] = [deepcopy(capture["events"][0]) for _ in range(contract.MAX_EVENTS + 1)]
    assert len(_bytes(capture)) < contract.MAX_CAPTURE_BYTES
    with pytest.raises(ValueError, match="bounded event"):
        _analyze(inputs, capture)
    inputs, capture = _world()
    inputs["trials"][2]["upstream_request_id"] = inputs["trials"][0]["upstream_request_id"]
    with pytest.raises(ValueError, match="ambiguous probe ledger"):
        _analyze(inputs, capture)


def test_saved_pack_definition_and_exact_ref_keep_example_replay_stable(tmp_path):
    from guard.store import Store
    from guard.store.packs import get_pack, put_pack
    from tests.builders import PACK_DIR

    store = Store(tmp_path / "store")
    ref = put_pack(store, PACK_DIR)
    saved = get_pack(store, ref)
    file_entry = next(file for file in saved.manifest.files_sha256 if file.path == "checks/REQ-02.yaml")
    inputs, capture = _world(bypass=True)
    inputs["refs"]["pack"] = ref.model_dump(mode="json")
    inputs["check"] = {"id": "REQ-02", "revision": saved.checks["REQ-02"].revision, "sha256": file_entry.sha256}
    capture["inputs_sha256"] = contract.validate_example_inputs(inputs)
    before = _analyze(inputs, capture)
    restored_inputs = json.loads(json.dumps(inputs))
    back = get_pack(store, ref)
    assert back.ref().model_dump(mode="json") == restored_inputs["refs"]["pack"]
    assert back.checks["REQ-02"].executor is None and back.checks["REQ-02"].judge is None
    assert _analyze(restored_inputs, json.loads(_bytes(capture))) == before
    assert before["guard_check_status"]["security"] == "UNKNOWN"


def test_cli_writes_only_new_explicit_example_diagnostic_and_refuses_overwrite(tmp_path, capsys):
    inputs, capture = _world(bypass=True)
    source, events, output = (tmp_path / name for name in ("inputs.json", "capture.json", "diagnostic.json"))
    source.write_text(json.dumps(inputs), encoding="utf-8")
    events.write_bytes(_bytes(capture))
    argv = ["--inputs", str(source), "--capture", str(events), "--output", str(output)]
    assert contract.main(argv) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["example_chain_state"] == "BYPASS_OBSERVED"
    assert result["guard_check_status"]["security"] == "UNKNOWN"
    previous = output.read_bytes()
    assert contract.main(argv) == 2 and output.read_bytes() == previous
    assert "EXAMPLE 진단 거부" in capsys.readouterr().err


def test_cli_refuses_real_input_and_does_not_write_diagnostic(tmp_path, capsys):
    inputs, capture = _world()
    capture["data_origin"] = "MEASURED"
    source, events, output = (tmp_path / name for name in ("inputs.json", "capture.json", "diagnostic.json"))
    source.write_text(json.dumps(inputs), encoding="utf-8")
    events.write_bytes(_bytes(capture))
    assert contract.main(["--inputs", str(source), "--capture", str(events), "--output", str(output)]) == 2
    assert not output.exists() and "EXAMPLE 진단 거부" in capsys.readouterr().err


@pytest.mark.parametrize("failure", ["missing_inputs", "missing_capture", "existing_output"])
def test_cli_os_errors_never_echo_private_host_paths(failure, tmp_path, capsys):
    inputs, capture = _world()
    source, events, output = (tmp_path / name for name in
                             ("EXAMPLE-private-policy-path.json", "EXAMPLE-private-capture-path.json",
                              "EXAMPLE-private-output-path.json"))
    if failure != "missing_inputs":
        source.write_text(json.dumps(inputs), encoding="utf-8")
    if failure != "missing_capture":
        events.write_bytes(_bytes(capture))
    if failure == "existing_output":
        output.write_bytes(b"EXAMPLE-existing-private-diagnostic")
    argv = ["--inputs", str(source), "--capture", str(events), "--output", str(output)]
    assert contract.main(argv) == 2
    displayed = capsys.readouterr()
    assert displayed.out == ""
    assert displayed.err == "REQ-02 EXAMPLE 진단 거부: invalid_example_input_or_output\n"
    assert not any(str(path) in displayed.err or path.name in displayed.err for path in (source, events, output))
    if failure == "existing_output":
        assert output.read_bytes() == b"EXAMPLE-existing-private-diagnostic"
    else:
        assert not output.exists()
