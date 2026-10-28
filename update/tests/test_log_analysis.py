"""OBS-03 source/coverage contracts. All subjects here are explicitly fake."""
import json
import base64
import hashlib

import pytest

from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.log_analysis import LogAnalysisExecutor
from guard.executors.log_analysis_contract import LEGACY_JSON_PROFILE, STRICT_JSON_PROFILE, events
from guard.runtime.docker import CmdResult
from guard.store import Store
from tests.builders import baseline_pack, environment, policy

VALUES = {"allowed_log_fields": ["request_id", "event", "exception"], "forbidden_log_fields": ["prompt", "credential"],
          "forbidden_marker_contexts": ["caller_input", "error_input"], "required_sources": ["engine"],
          "required_branches": ["normal", "error", "exception_dump"]}


class Source:
    def __init__(self, *, leak=False, dump=True, plain=False, unreadable=False, replaced=False, driver="json-file"):
        self.leak, self.dump, self.plain = leak, dump, plain
        self.unreadable, self.replaced, self.driver = unreadable, replaced, driver
        self.events, self.calls, self.inspect_calls = [], [], 0

    def probe(self, network, requests, timeout):
        results = []
        for request in requests:
            body = request["body"]
            is_error = isinstance(body["max_tokens"], str)
            event = {"request_id": request["headers"]["X-Request-Id"], "event": "error" if is_error else "normal"}
            if is_error and self.dump:
                event["exception"] = "redacted traceback"
            if self.leak:
                event["prompt"] = body.get("prompt") or body["messages"][-1]["content"]
            self.events.append(event)
            body_out = {"error": {"message": "invalid max_tokens"}} if is_error else {
                "model": "test", "choices": [{"text": "ok", "finish_reason": "length"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
            results.append({"outcome": 400 if is_error else 200, "body": json.dumps(body_out)})
        return results

    def inspect_json(self, container, *, timeout):
        self.inspect_calls += 1
        return {"Id": "replaced" if self.replaced and self.inspect_calls > 1 else "original",
                "Image": "sha256:test", "State": {"StartedAt": "2026-10-23T00:00:00Z"},
                "HostConfig": {"LogConfig": {"Type": self.driver, "Config": {"max-file": "3"}}}}

    def logs_window(self, container, *, since, until, timeout):
        self.calls.append((container, since, until))
        if self.unreadable:
            return CmdResult(1, "", "permission denied")
        lines = [json.dumps(event) for event in self.events]
        if self.plain:
            lines = ["unstructured log line"]
        return CmdResult(0, "\n".join(lines), "")

    def exec(self, container, argv, *, timeout):
        raw = "\n".join(json.dumps(e) for e in self.events).encode()
        return CmdResult(0, json.dumps({"schema": "guard-container-file/2", "raw_base64": base64.b64encode(raw).decode(),
                        "raw_retained_bytes": len(raw), "raw_retained_sha256": hashlib.sha256(raw).hexdigest(),
                        "file_identity_stable": True, "source_complete": True, "nofollow_components": True,
                        "regular_file": True, "truncated": False, "size": len(raw), "mtime_ns": 1000}), "")


class AmbiguousSource(Source):
    """EXAMPLE malformed log rows; independent complete leak events stay intact."""

    def __init__(self, kind, **options):
        super().__init__(**options)
        self.kind = kind

    def logs_window(self, container, *, since, until, timeout):
        result = super().logs_window(container, since=since, until=until, timeout=timeout)
        linked = json.dumps(self.events[0]["request_id"])
        rows = {
            "request": '{"request_id":' + linked + ',"request_id":"EXAMPLE-unrelated","prompt":"EXAMPLE hidden field"}',
            "nested": '{"context":{"request_id":' + linked + ',"request_id":"EXAMPLE-unrelated"},"event":"normal"}',
            "same-value": '{"request_id":' + linked + ',"event":"normal","event":"normal"}',
            "escaped-key": '{"request_id":' + linked + ',"event":"normal","ev\\u0065nt":"normal"}',
            "nan": '{"request_id":"EXAMPLE-unrelated","event":NaN}',
            "infinity": '{"request_id":"EXAMPLE-unrelated","event":Infinity}',
            "overflow": '{"request_id":"EXAMPLE-unrelated","event":1e400}',
        }
        return CmdResult(0, result.stdout + "\n" + rows[self.kind], result.stderr)


def run_log(tmp_path, monkeypatch, source, values=None, source_cfg=None):
    target = {"lab": True, "probe_image": "test", "engine": {"trusted_url": "http://engine:8000", "container": "engine"},
              "origins": {"trusted": {"network": "inside"}}}
    path = tmp_path / "target.json"
    path.write_text(json.dumps(target), encoding="utf-8")
    config = {"target": str(path), "log_sources": source_cfg or [{"id": "engine", "kind": "docker_logs", "container": "engine"}]}
    monkeypatch.setitem(registry.EXECUTORS, "log_analysis:test", lambda raw: LogAnalysisExecutor(
        config, container_probe=source.probe, docker=source, environ={}))
    store = Store(tmp_path / "store")
    pack = baseline_pack()
    env = environment(model={"id": "test"})
    pol = policy(pack.ref(), scope={"include": ["OBS-03"]}, values={"OBS-03": values or VALUES})
    store.put(env); store.put(pol)
    plan = select(pack, [env], pol); store.put(plan)
    evd, = run_plan(store, pack, plan, RunConfig(executors={"log_analysis": "test"}, adapter="vllm"))
    res, = judge_plan(store, pack, plan, env.created_at)
    return store, evd, res


def test_linked_structured_normal_error_and_actual_dump_can_pass(tmp_path, monkeypatch):
    source = Source()
    store, evd, res = run_log(tmp_path, monkeypatch, source)
    assert res.verdicts.security.value == "PASS", res.reasons
    assert evd.data_origin.value == "EXAMPLE"
    assert source.calls[0][1] <= source.calls[0][2]
    assert evd.observations["log_sources_tested"].value == {"engine": ["error", "exception_dump", "normal"]}
    assert len(evd.artifacts) == 2
    assert store.verify() == []


def test_http_4xx_without_trial_linked_dump_is_unknown(tmp_path, monkeypatch):
    _, evd, res = run_log(tmp_path, monkeypatch, Source(dump=False))
    assert res.verdicts.security.value == "UNKNOWN"
    assert any("exception_dump" in g for g in evd.observations["log_coverage_gaps"].value)


@pytest.mark.parametrize("options", [{"plain": True}, {"unreadable": True}, {"replaced": True}, {"driver": "none"}])
def test_missing_unstructured_or_unstable_log_source_is_unknown(tmp_path, monkeypatch, options):
    _, _, res = run_log(tmp_path, monkeypatch, Source(**options))
    assert res.verdicts.security.value == "UNKNOWN", res.reasons


def test_observed_sensitive_marker_keeps_fail_despite_missing_copy(tmp_path, monkeypatch):
    _, _, res = run_log(tmp_path, monkeypatch, Source(leak=True), values={**VALUES, "required_sources": ["engine", "collector"]})
    assert res.verdicts.security.value == "FAIL", res.reasons


def test_completions_cannot_claim_private_system_log_coverage(tmp_path, monkeypatch):
    _, _, res = run_log(tmp_path, monkeypatch, Source(),
                         values={**VALUES, "forbidden_marker_contexts": ["private_system"]})
    assert res.verdicts.security.value == "UNKNOWN"


def test_regular_dump_file_is_captured_with_actual_source_metadata(tmp_path, monkeypatch):
    _, evd, res = run_log(tmp_path, monkeypatch, Source(), source_cfg=[{
        "id": "engine", "kind": "container_file", "container": "engine", "path": "/logs/dump.json"}])
    assert res.verdicts.security.value == "PASS", res.reasons
    assert evd.observations["log_coverage_complete"].value is True


def test_different_container_is_not_engine_evidence(tmp_path, monkeypatch):
    _, _, res = run_log(tmp_path, monkeypatch, Source(), source_cfg=[{
        "id": "engine", "kind": "docker_logs", "container": "other-engine"}])
    assert res.verdicts.security.value == "UNKNOWN"


def test_managed_engine_token_resolves_to_actual_target_without_changing_config(tmp_path, monkeypatch):
    source = Source()
    _, _, res = run_log(tmp_path, monkeypatch, source, source_cfg=[{
        "id": "engine", "kind": "docker_logs", "container": "@engine"}])
    assert res.verdicts.security.value == "PASS", res.reasons
    assert source.calls[0][0] == "engine"


def test_cannot_omit_basic_exception_branch_to_get_pass(tmp_path, monkeypatch):
    source = Source()
    _, evd, res = run_log(tmp_path, monkeypatch, source,
                           values={**VALUES, "required_branches": ["normal", "error"]})
    assert evd.execution.status.value == "ABORTED"
    assert res.verdicts.security.value == "UNKNOWN"
    assert source.events == []


def test_collector_only_evidence_does_not_replace_local_source(tmp_path, monkeypatch):
    source = Source()
    _, evd, res = run_log(tmp_path, monkeypatch, source,
                          values={**VALUES, "required_sources": ["collector"]}, source_cfg=[{
                              "id": "collector", "kind": "docker_logs", "container": "collector", "role": "copy"}])
    assert evd.execution.status.value == "ABORTED"
    assert res.verdicts.security.value == "UNKNOWN"
    assert source.events == []


def test_confirmed_log_leak_survives_later_normal_probe_exception(tmp_path, monkeypatch):
    source = Source(leak=True)
    base = source.probe
    def transport(network, requests, timeout):
        if requests[0]["id"] == "after":
            raise TimeoutError("opaque-http-secret-never-save")
        return base(network, requests, timeout)
    source.probe = transport
    store, evd, res = run_log(tmp_path, monkeypatch, source)
    assert evd.execution.status.value == "COMPLETED"
    assert res.verdicts.security.value == "FAIL", res.reasons
    assert evd.observations["functional_probe_results"].value["after"] == "no_response"
    for artifact in evd.artifacts:
        assert "opaque-http-secret-never-save" not in (store.root / artifact.path).read_text(encoding="utf-8")


def test_collector_exception_message_cannot_enter_observations_or_artifacts(tmp_path, monkeypatch):
    class Unreadable(Source):
        def inspect_json(self, container, *, timeout):
            raise RuntimeError("arbitrary-daemon-secret-never-save")
    store, evd, res = run_log(tmp_path, monkeypatch, Unreadable())
    assert evd.execution.status.value == "COMPLETED"
    assert res.verdicts.security.value == "UNKNOWN"
    assert "engine: LOG_SOURCE_CAPTURE_UNAVAILABLE" in evd.observations["log_coverage_gaps"].value
    assert "RuntimeError" not in evd.model_dump_json()
    assert "arbitrary-daemon-secret-never-save" not in evd.model_dump_json()
    for artifact in evd.artifacts:
        assert "arbitrary-daemon-secret-never-save" not in (store.root / artifact.path).read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["request", "nested", "same-value", "escaped-key", "nan", "infinity", "overflow"])
def test_ambiguous_json_cannot_complete_new_log_collection(tmp_path, monkeypatch, kind):
    store, evd, res = run_log(tmp_path, monkeypatch, AmbiguousSource(kind))
    assert evd.execution.status.value == "COMPLETED"
    assert evd.execution.executor.version == "0.3.0"
    assert evd.observations["log_coverage_complete"].value is False
    assert res.verdicts.security.value == "UNKNOWN", res.reasons
    sample_artifact = next(item for item in evd.artifacts if item.name == "log_samples.json")
    sample = json.loads((store.root / sample_artifact.path).read_bytes())["capture"]
    assert sample["schema"] == "guard-log-analysis-capture/4"
    assert sample["analysis_profile"] == STRICT_JSON_PROFILE


@pytest.mark.parametrize("kind", ["request", "nested", "nan"])
def test_independent_complete_leak_remains_fail_when_another_event_is_ambiguous(tmp_path, monkeypatch, kind):
    store, evd, res = run_log(tmp_path, monkeypatch, AmbiguousSource(kind, leak=True))
    assert evd.observations["log_coverage_complete"].value is False
    assert "prompt" in evd.observations["log_fields_seen"].value
    assert "caller_input" in evd.observations["log_marker_contexts_seen"].value
    item = next(artifact for artifact in evd.artifacts if artifact.name == "log_samples.json")
    assert json.loads((store.root / item.path).read_bytes())["capture"]["analysis"]["log_violations"]
    assert res.verdicts.security.value == "FAIL", res.reasons


@pytest.mark.parametrize("text", ['{"event":1,"event":2}', '{"event":{"key":1,"key":2}}',
                                 '{"event":1,"ev\\u0065nt":2}', '{"event":NaN}',
                                 '{"event":Infinity}', '{"event":-Infinity}', '{"event":1e400}'])
def test_strict_event_parser_rejects_ambiguous_rows_without_hiding_complete_neighbors(text):
    neighboring = '{"request_id":"EXAMPLE-trial","event":"normal"}'
    found, complete = events(text + "\n" + neighboring, profile=STRICT_JSON_PROFILE)
    assert complete is False
    assert found == [{"event": {"request_id": "EXAMPLE-trial", "event": "normal"}, "timestamp": None}]
    legacy, legacy_complete = events(text, profile=LEGACY_JSON_PROFILE)
    assert legacy_complete is True and len(legacy) == 1


def test_strict_parser_keeps_valid_finite_json_and_timestamped_events():
    text = '2026-10-26T00:00:00Z {"event":{"value":1.5,"list":[0,false,null,"NaN"]}}'
    found, complete = events(text, profile=STRICT_JSON_PROFILE)
    assert complete is True
    assert found == [{"timestamp": "2026-10-26T00:00:00Z",
                      "event": {"event": {"value": 1.5, "list": [0, False, None, "NaN"]}}}]


def test_unknown_json_profile_is_not_inferred_from_data():
    with pytest.raises(ValueError, match="unsupported log analysis profile"):
        events('{"event":"normal"}', profile="guard-log-json/future")


def test_rejected_row_persistence_discards_raw_values_and_keeps_decode_gap_signal():
    from guard.executors.log_analysis_contract import REJECTED_JSON_LINE, persistable_log_text

    raw = '{"secret":"EXAMPLE-never-retain","secret":"safe","event":"\ufffd"}\n'
    persisted, rejected = persistable_log_text(raw)
    assert persisted == REJECTED_JSON_LINE + "\ufffd\n" and rejected == 1
    assert "EXAMPLE-never-retain" not in persisted
    assert events(persisted, profile=STRICT_JSON_PROFILE) == ([], False)
