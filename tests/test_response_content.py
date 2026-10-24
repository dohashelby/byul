"""REQ-13 contracts with a fake transport; these are not real-engine claims."""
import json

import pytest

from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.content_common import leaves, redact_value, control_state, validate_content_settings, record_content
from guard.executors.response_content import ResponseContentExecutor
from guard.models import DataOrigin
from guard.store import Store
from tests.builders import baseline_pack, environment, policy

VALUES = {"allowed_response_fields": ["id", "model", "choices[].text", "choices[].message.role", "choices[].message.content",
          "choices[].finish_reason", "usage.prompt_tokens", "usage.completion_tokens", "usage.total_tokens"], "allowed_error_fields": ["error.message"],
          "forbidden_return_options": ["return_prompt_text", "return_token_ids"], "required_targets": ["engine"]}


def run_content(tmp_path, monkeypatch, probe, values=None, endpoint="/v1/chat/completions", target_extra=None):
    target = {"lab": True, "probe_image": "test", "engine": {"trusted_url": "http://engine:8000", "container": "engine"},
              "origins": {"trusted": {"network": "inside"}, "external": {"network": "outside"}}, **(target_extra or {})}
    path = tmp_path / "target.json"
    path.write_text(json.dumps(target), encoding="utf-8")
    config = {"target": str(path), "endpoint": endpoint}
    monkeypatch.setitem(registry.EXECUTORS, "response_content:test", lambda raw: ResponseContentExecutor(
        config, container_probe=probe, environ={}))
    store = Store(tmp_path / "store")
    pack = baseline_pack()
    env = environment(model={"id": "test"})
    pol = policy(pack.ref(), scope={"include": ["REQ-13"]}, values={"REQ-13": values or VALUES})
    store.put(env); store.put(pol)
    plan = select(pack, [env], pol); store.put(plan)
    evd, = run_plan(store, pack, plan, RunConfig(executors={"response_content": "test"}, adapter="vllm"))
    res, = judge_plan(store, pack, plan, env.created_at)
    return store, evd, res


def response_probe(*, leak=False, own_quote=False, extra=False, option_outcome=400, error_outcome=400,
                   truncated=False, failed_after=False, finish_reason="length"):
    def probe(network, requests, timeout):
        results = []
        for request in requests:
            body = request["body"]
            status = 200
            chat = "messages" in body
            choice = {"message": {"role": "assistant", "content": "ok"}} if chat else {"text": "ok"}
            choice["finish_reason"] = finish_reason
            value = {"id": "test", "model": "test", "choices": [choice],
                     "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
            option = any(key in body for key in ("return_prompt_text", "return_token_ids"))
            if isinstance(body["max_tokens"], str):
                status, value = error_outcome, {"error": {"message": "invalid max_tokens"}}
            elif option:
                status = option_outcome
                value = {"error": {"message": "option disabled"}} if status != 200 else value
            elif own_quote:
                own = body.get("prompt") or body["messages"][-1]["content"]
                if chat:
                    value["choices"][0]["message"]["content"] = own
                else:
                    value["choices"][0]["text"] = own
            if extra and status == 200:
                value["server_path"] = "/model"
            if leak and body.get("messages", [{}])[0].get("role") == "system":
                value = {"id": "test", "model": "test", "choices": [{"message": {"role": "assistant",
                         "content": body["messages"][0]["content"].split()[0]}, "finish_reason": "length"}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
                status = 200
            if failed_after and request["id"] == "after":
                status = 500
            raw = json.dumps(value)
            results.append({"outcome": status, "body": raw, "body_bytes": 1_000_000 if truncated else len(raw)})
        return results
    return probe


def test_leaf_paths_retain_array_fields_and_empty_values():
    assert leaves({"choices": [{"text": "a"}, {"text": "b"}], "error": None}) == {
        "choices[].text": ["a", "b"], "error": [None]}


def test_literal_dotted_array_and_empty_keys_do_not_alias_structural_paths():
    assert leaves({"a.b": "literal", "a": {"b": "nested"}, "choices[]": "literal-array",
                   "choices": ["real-array"], "": "empty"}) == {
        '["a.b"]': ["literal"], "a.b": ["nested"], '["choices[]"]': ["literal-array"],
        "choices[]": ["real-array"], '[""]': ["empty"]}


@pytest.mark.parametrize("body", [{}, {"model": "test", "choices": [], "usage": {}}, {"error": "not inference"}])
def test_http_200_alone_is_not_a_normal_control(body):
    assert control_state({"outcome": 200, "body": json.dumps(body)}, "test", "/v1/completions") != "pass"


@pytest.mark.parametrize("timeout", [0, -1, True, "30", float("inf"), float("nan"), 31])
def test_request_timeout_is_explicit_finite_and_bounded(timeout):
    with pytest.raises(ValueError):
        validate_content_settings({"request_timeout": timeout})


def test_timestamped_json_and_plain_credentials_are_redacted_structurally():
    raw = '2026-10-23T00:00:00Z {"credential":"hidden-value", "event":"ok"}\nAPI_KEY=other-secret\n'
    result = redact_value({"text": raw, "authorization": "Bearer private"}, [])
    assert "hidden-value" not in result["text"] and "other-secret" not in result["text"]
    assert result["authorization"] == "[REDACTED]"
    assert result["text"].startswith('2026-10-23T00:00:00Z {')


def test_probe_log_record_is_scrubbed_as_well_as_sample_artifacts():
    class Context:
        secrets = ["configured-role-key"]
        recorded = None
        def record(self, *args):
            self.recorded = args[-1]
    ctx = Context()
    record_content(ctx, "trusted", "engine", "normal", {"body_preview": 'API_KEY=opaque-secret',
        "body": '{"credential":"unconfigured-secret", "text":"configured-role-key"}'})
    assert "opaque-secret" not in ctx.recorded["body_preview"]
    assert "unconfigured-secret" not in ctx.recorded["body"]
    assert "configured-role-key" not in ctx.recorded["body"]


def test_chat_fields_private_context_and_blocked_options_pass(tmp_path, monkeypatch):
    store, evd, res = run_content(tmp_path, monkeypatch, response_probe())
    assert res.verdicts.security.value == "PASS", res.reasons
    assert evd.data_origin == DataOrigin.EXAMPLE
    assert evd.normal_control.succeeded is True
    assert len(evd.artifacts) == 2
    assert store.verify() == []


def test_quoting_callers_own_input_is_not_private_leak(tmp_path, monkeypatch):
    _, evd, res = run_content(tmp_path, monkeypatch, response_probe(own_quote=True))
    assert res.verdicts.security.value == "PASS", res.reasons
    assert evd.observations["private_markers_returned"].value == []


def test_completions_only_cannot_claim_private_system_pass(tmp_path, monkeypatch):
    _, evd, res = run_content(tmp_path, monkeypatch, response_probe(), endpoint="/v1/completions")
    assert res.verdicts.security.value == "UNKNOWN"
    assert any("private system" in x for x in evd.observations["response_coverage_gaps"].value)


@pytest.mark.parametrize("kwargs", [{"leak": True}, {"extra": True}, {"leak": True, "failed_after": True}])
def test_observed_leak_or_field_violation_keeps_fail(tmp_path, monkeypatch, kwargs):
    _, _, res = run_content(tmp_path, monkeypatch, response_probe(**kwargs))
    assert res.verdicts.security.value == "FAIL", res.reasons


@pytest.mark.parametrize("kwargs", [{"option_outcome": 200}, {"error_outcome": 500}, {"truncated": True}])
def test_ignored_options_server_error_or_truncated_body_is_unknown(tmp_path, monkeypatch, kwargs):
    _, _, res = run_content(tmp_path, monkeypatch, response_probe(**kwargs))
    assert res.verdicts.security.value == "UNKNOWN", res.reasons


def test_missing_gateway_is_unknown_not_silently_omitted(tmp_path, monkeypatch):
    _, evd, res = run_content(tmp_path, monkeypatch, response_probe(),
                              values={**VALUES, "required_targets": ["engine", "gateway"]})
    assert res.verdicts.security.value == "UNKNOWN"
    assert evd.observations["response_targets_tested"].value == ["engine"]


def test_unsupported_policy_option_aborts_before_requests(tmp_path, monkeypatch):
    def never(*args):
        raise AssertionError("must validate before requests")
    _, evd, res = run_content(tmp_path, monkeypatch, never,
                              values={**VALUES, "forbidden_return_options": ["invented_flag"]})
    assert evd.execution.status.value == "ABORTED"
    assert res.verdicts.security.value == "UNKNOWN"


def test_raw_private_leak_survives_unparsed_capture_limit(tmp_path, monkeypatch):
    base = response_probe(leak=True)
    def transport(network, requests, timeout):
        result = base(network, requests, timeout)
        if requests[0]["body"].get("messages", [{}])[0].get("role") == "system":
            # Establish a valid control first; only the private response hits
            # the capture ceiling, retaining its independently observed leak.
            result[0]["body_bytes"] = 1_000_000
        return result
    _, _, res = run_content(tmp_path, monkeypatch, transport)
    assert res.verdicts.security.value == "FAIL"


def test_truncated_initial_control_does_not_send_private_challenges(tmp_path, monkeypatch):
    base = response_probe(truncated=True)
    sent = []
    def transport(network, requests, timeout):
        sent.extend(request["id"] for request in requests)
        return base(network, requests, timeout)
    _, evd, res = run_content(tmp_path, monkeypatch, transport)
    assert res.verdicts.security.value == "UNKNOWN"
    assert evd.normal_control.succeeded is False
    assert sent == ["normal"]


def test_echo_flag_does_not_turn_unchanged_generated_self_quote_into_a_leak(tmp_path, monkeypatch):
    _, evd, res = run_content(tmp_path, monkeypatch, response_probe(own_quote=True),
                              values={**VALUES, "forbidden_return_options": ["echo"]})
    assert res.verdicts.security.value == "UNKNOWN"
    assert evd.observations["response_options_returned"].value == []


@pytest.mark.parametrize("finish_reason", ["error", "abort", "unknown", "", None])
def test_invalid_finish_reason_cannot_pass_normal_control(tmp_path, monkeypatch, finish_reason):
    _, evd, res = run_content(tmp_path, monkeypatch, response_probe(finish_reason=finish_reason))
    assert evd.normal_control.succeeded is False
    assert res.verdicts.security.value == "UNKNOWN", res.reasons


def test_nonstring_finish_reason_is_not_a_successful_control():
    result = {"outcome": 200, "body": json.dumps({"model": "test", "choices": [
        {"text": "ok", "finish_reason": ["stop"]}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})}
    assert control_state(result, "test", "/v1/completions") == "no_response"


def test_confirmed_leak_survives_later_probe_exception_and_exception_text_is_not_saved(tmp_path, monkeypatch):
    base = response_probe(leak=True)
    secret = "arbitrary-daemon-secret-do-not-persist"
    def transport(network, requests, timeout):
        if requests[0]["id"] == "after":
            raise TimeoutError(secret)
        return base(network, requests, timeout)
    store, evd, res = run_content(tmp_path, monkeypatch, transport)
    assert evd.execution.status.value == "COMPLETED"
    assert res.verdicts.security.value == "FAIL", res.reasons
    assert evd.observations["functional_probe_results"].value["engine:after"] == "no_response"
    assert secret not in evd.model_dump_json()
    for artifact in evd.artifacts:
        assert secret not in (store.root / artifact.path).read_text(encoding="utf-8")


@pytest.mark.parametrize("invalid", [None, {}, [], [None], ["bad"], [{}, {}], [{"outcome": []}]])
def test_malformed_probe_result_after_leak_is_unknown_collection_and_keeps_fail(tmp_path, monkeypatch, invalid):
    base = response_probe(leak=True)
    def transport(network, requests, timeout):
        return invalid if requests[0]["id"] == "after" else base(network, requests, timeout)
    _, evd, res = run_content(tmp_path, monkeypatch, transport)
    assert evd.execution.status.value == "COMPLETED"
    assert res.verdicts.security.value == "FAIL", res.reasons
    assert evd.observations["functional_probe_results"].value["engine:after"] == "no_response"


@pytest.mark.parametrize("prefix", ["", "2026-10-23T00:00:00Z "])
def test_truncated_credential_json_preview_is_redacted_without_parsing(prefix):
    raw = prefix + '{"api_key":"preview-secret", "unfinished":'
    redacted = redact_value({"body_preview": raw}, [])
    assert "preview-secret" not in redacted["body_preview"]
    assert "REDACTED" in redacted["body_preview"]


@pytest.mark.parametrize("representation", [
    'API_KEY="quoted-credential-value"',
    "api_key=['quoted-credential-value']",
    "Namespace(api_key=SecretStr('quoted-credential-value'))",
    "authorization='Bearer quoted-credential-value'",
])
@pytest.mark.parametrize("prefix", ["", "2026-10-23T00:00:00Z ", "[engine] INFO "])
def test_plain_quoted_list_and_wrapped_credentials_are_entirely_redacted(representation, prefix):
    raw = prefix + representation + "\n"
    nested = {"captures": [{"text": raw}], "json_string_copy": json.dumps(raw)}
    result = redact_value(nested, [])
    assert "quoted-credential-value" not in json.dumps(result)
    assert "REDACTED" in result["captures"][0]["text"]


def test_credential_assignment_redaction_does_not_treat_other_field_names_as_credentials():
    assert redact_value("api_key_enabled=false\nnormal event\n", []) == "api_key_enabled=false\nnormal event\n"
