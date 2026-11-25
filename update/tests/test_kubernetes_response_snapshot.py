"""Retained REQ-13 fixtures use the Runtime writer and bounded loopback response capture."""
from datetime import timedelta
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

from guard.executors import kubernetes_response_snapshot as snapshot, stream_probe
from guard.executors.kubernetes_gateway_observation import principal_sha256, snapshot_record
from guard.executors.kubernetes_response_content import KubernetesResponseContentExecutor
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex
from guard.runtime.kubernetes_binding import verify_owned_pod
from guard.runtime.kubernetes_http import BINDING_PROBE_SHA256, GATEWAY_EVENT_PROFILE, KubernetesHTTP
from tests.test_mvp_gateway_preparation import END, MODEL, NOW, START, _gateway_owner, gateway_pair, sources
from tests.test_stream_probe import _response, _server
from tools import mvp_gateway_preparation as gateway


VALUES = {"allowed_response_fields": ["id", "model", "choices[].message.role", "choices[].message.content",
    "choices[].finish_reason", "usage.prompt_tokens", "usage.completion_tokens", "usage.total_tokens"],
    "allowed_error_fields": ["error.message"], "forbidden_return_options": ["return_token_ids"],
    "required_targets": ["gateway"]}
PRIVATE = "EXAMPLE response text must never enter the evidence"


def _pointer(path, value):
    raw = value if isinstance(value, bytes) else canonical_json(value)
    path.write_bytes(raw)
    return {"path": str(path), "sha256": sha256_hex(raw)}


def _capture_ordinary_response(body, fixture, request_id, *, incomplete):
    request_id_header = b"X-Request-ID: " + request_id.encode("ascii") + b"\r\n"

    def handler(connection, *_):
        if incomplete:
            length = str(len(body) + 1).encode("ascii")
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                               + length + b"\r\n" + request_id_header + b"Connection: close\r\n\r\n" + body)
        else:
            _response(connection, body, extra=request_id_header)

    with _server(handler) as (url, _):
        result, = stream_probe.collect([{"id": "req13-normal", "method": "POST", "mode": "json",
                                         "url": url + "/v1/chat/completions", "body": fixture}], 2)
    assert result["outcome"] == 200
    assert result["stop_reason"] == ("incomplete_body" if incomplete else "eof")
    assert result["response_headers"]["x-request-id"] == request_id
    assert result["body"].encode("utf-8") == body
    return result


def snapshot_inputs(tmp_path, gateway_pair, side, guard_policy_sha256, *, probe_capture=None, recorded_at=None,
                    recorded_request=None):
    """Build an owned fixture with a Runtime final and optional loopback producer capture."""
    from serving_runtime.gateway.request_records import RequestRecords
    from serving_runtime.gateway.validator import Decision

    directory = tmp_path / ("req13-retained-" + side)
    directory.mkdir()
    env, target_path, _, prepared, owner = _gateway_owner(gateway_pair, side)
    bound = gateway.bind_gateway(target_path, env, prepared, **owner)
    target = _pointer(directory / "target.json", bound)
    run_id = bound["kubernetes"]["run_id"]
    policy_sha = prepared["runtime_policy_sha256"]
    target_id = prepared["runtime_target_id"]
    session = ("a" if side == "baseline" else "b") * 32
    request_id = "fixture-req13-" + side
    principal = "fixture-allowed-" + side
    actor_sha, request_sha = principal_sha256(principal), sha256_hex(request_id.encode())
    writer_policy = SimpleNamespace(target_id=target_id, schema_version="runtime-mvp/1.1",
        profile=None, policy_id="fixture-policy", policy_version="1", cache_isolation=None)
    state = SimpleNamespace(policy=writer_policy, sha256=policy_sha, path="fixture-policy.json")
    writer = RequestRecords(directory, session, lambda: state, "vllm", environment=gateway.GATEWAY_ENV, run_id=run_id,
        target_context=lambda: {"target_id": target_id, "registration": "ok"})
    writer.clear_context(snapshot.ROUTE)
    writer.bind_context(state)
    writer.set_auth({"state": "authenticated", "principal_id": principal})
    recorded_request = recorded_request or {}
    decision = Decision("ALLOWED", 200, "ALLOWED", None, "allowed",
                        recorded_request.get("model", MODEL), recorded_request.get("max_tokens", 1),
                        n=recorded_request.get("n"))
    final_options = {"stream": recorded_request["stream"]} if "stream" in recorded_request else {}
    final = writer.build("final", request_id, decision, attempted=True, backend_receipt="not_available",
                         response="completed", error_code=None, final_outcome="completed", **final_options)
    final["recorded_at"] = recorded_at or (NOW + timedelta(seconds=1)).isoformat()
    assert writer.append(final)
    journal = directory / "gateway_records.jsonl"
    journal_raw = journal.read_bytes()
    source = {"environment": gateway.GATEWAY_ENV, "target_id": target_id,
              "runtime_policy_sha256": policy_sha, "request_sha256": request_sha}
    _, row_sha, recorded_at = snapshot_record(journal_raw, source=source, run_id=run_id,
        values={"observation_session": session, "observation_allowed_actor_sha256": actor_sha})
    gateway_owner = verify_owned_pod(owner["pod"], owner["namespace"]["metadata"], bound["kubernetes_gateway"], run_id)
    common = {"environment_ref": env.ref().model_dump(mode="json"), "request_sha256": request_sha,
              "gateway_target_sha256": target["sha256"], "engine_owner_sha256": gateway._owner_sha256(bound["kubernetes"]["engine"]),
              "gateway_owner_sha256": sha256_hex(canonical_json(gateway_owner))}
    ordinary = {"model": MODEL, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1, "stream": False}
    transport = KubernetesHTTP({key: value for key, value in bound.items() if key != "kubernetes_gateway"},
                               env, duration=1, profile=GATEWAY_EVENT_PROFILE)
    body = canonical_json({"id": request_id, "model": MODEL, "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": PRIVATE}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}})
    if probe_capture is None:
        capture = {"body_sha256": sha256_hex(body), "body_bytes": len(body), "capture_complete": True,
                   "eof": True, "utf8_valid": True, "body_truncated": False, "content_type": "application/json",
                   "outcome": 200, "response_headers": {"x-request-id": request_id}}
    else:
        assert probe_capture in {"complete", "incomplete"}
        capture = _capture_ordinary_response(body, ordinary, request_id, incomplete=probe_capture == "incomplete")
    probe_source = Path(stream_probe.__file__).read_text(encoding="utf-8")
    assert sha256_hex(probe_source.encode("utf-8")) == BINDING_PROBE_SHA256
    assert repr(probe_source) in transport.source
    response = {**common, **{key: capture[key] for key in (
        "body_sha256", "body_bytes", "capture_complete", "eof", "utf8_valid", "body_truncated", "content_type")},
        "status": capture["outcome"], "request_id": capture["response_headers"]["x-request-id"],
        "route": snapshot.ROUTE, "run_sha256": sha256_hex(run_id.encode()),
        "observed_started_ns": int(NOW.timestamp()) * 10**9, "observed_finished_ns": int(NOW.timestamp()) * 10**9 + 100_000_000,
        "parent_cleanup_verified": True, "probe_source_sha256": transport.source_sha256,
        "request_fixture_sha256": sha256_hex(canonical_json(ordinary)),
        "probe_owner_sha256": gateway._owner_sha256(bound["kubernetes"]["probe"])}
    audit = {**common, "event": "ACCESS_ALLOWED", "owner_before_after_verified": True,
        "producer_sha256": gateway.PRODUCER_SHA256, "runtime_policy_sha256": policy_sha, "journal_bytes": len(journal_raw),
        "journal_sha256": sha256_hex(journal_raw), "selected_row_sha256": row_sha, "recorded_at": recorded_at,
        "actor_sha256": actor_sha, "session": session, "independent_denied_event_observed": False}
    manifest = _pointer(directory / "manifest.json", {"schema": snapshot.MANIFEST_SCHEMA,
        "body": _pointer(directory / "response.bin", body), "response_receipt": _pointer(directory / "response.json", response),
        "audit_receipt": _pointer(directory / "audit.json", audit),
        "journal": {"path": str(journal), "sha256": sha256_hex(journal_raw)}})
    cfg = {"target": target["path"], snapshot.OPTION: True, snapshot.INPUT: {
        **manifest, "guard_policy_sha256": guard_policy_sha256, "actor_sha256": actor_sha, "record_session": session,
        "runtime_policy_sha256": policy_sha, "request_sha256": request_sha, "runtime_target_id": target_id,
        "valid_from": START, "valid_until": END}}
    return env, cfg, {"expected_target_sha256": target["sha256"], "expected_target_file_sha256": target["sha256"],
                      "expected_manifest_sha256": manifest["sha256"], "expected_policy_sha256": guard_policy_sha256,
                      "expected_policy_validity": (None, None)}


def _forbidden(*args, **kwargs):
    pytest.fail("retained response verification cannot dispatch or start a process")


def _check():
    return load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE").checks["REQ-13"]


def test_retained_response_owner_clock_keeps_nanosecond_order():
    assert snapshot._epoch_ns("2026-11-19T00:00:00.000000001Z") == snapshot._epoch_ns("2026-11-19T01:00:00+01:00") + 1


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("at_expiry", [False, True])
def test_retained_response_qualifies_the_original_final_at_nanosecond_boundaries(
        tmp_path, gateway_pair, monkeypatch, side, at_expiry):
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    final_time = "2026-11-19T00:00:00.000000900Z"
    env, cfg, pins = snapshot_inputs(tmp_path, gateway_pair, side, "e" * 64, recorded_at=final_time)
    selection = cfg[snapshot.INPUT]
    manifest_path = Path(selection["path"])
    manifest = json.loads(manifest_path.read_bytes())
    response_path = Path(manifest["response_receipt"]["path"])
    response = json.loads(response_path.read_bytes())
    started = int(NOW.timestamp()) * 10**9
    response["observed_started_ns"] = started if at_expiry else started + 100
    response["observed_finished_ns"] = started + 500
    manifest["response_receipt"] = _pointer(response_path, response)
    selection.update(_pointer(manifest_path, manifest))
    pins["expected_manifest_sha256"] = selection["sha256"]
    if at_expiry:
        selection["valid_until"] = final_time

    executor = KubernetesResponseContentExecutor(cfg, runner=_forbidden, **pins)
    work = tmp_path / "evidence"
    if at_expiry:
        with pytest.raises(ValueError, match="^KUBERNETES_RESPONSE_SNAPSHOT_UNVERIFIED$"):
            executor.run(_check(), env, VALUES, None, work)
        assert not work.exists()
    else:
        draft = executor.run(_check(), env, VALUES, None, work)
        assert draft.status is ExecStatus.COMPLETED and draft.data_origin is DataOrigin.EXAMPLE
        artifact, = draft.artifacts
        proof = json.loads(artifact.path.read_bytes())["snapshot"]
        assert proof["recorded_at"] == NOW.isoformat()
        journal = Path(manifest["journal"]["path"]).read_bytes()
        assert json.loads(journal)["recorded_at"] == final_time
        assert proof["journal_sha256"] == sha256_hex(journal) == manifest["journal"]["sha256"]


@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_retained_owned_response_projects_fields_without_private_values(tmp_path, gateway_pair, monkeypatch, side):
    env, cfg, pins = snapshot_inputs(tmp_path, gateway_pair, side, "e" * 64, probe_capture="complete")
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    executor = KubernetesResponseContentExecutor(cfg, runner=_forbidden, **pins)
    draft = executor.run(_check(), env, VALUES, None, tmp_path / "evidence")
    assert draft.status is ExecStatus.COMPLETED and draft.data_origin is DataOrigin.EXAMPLE
    assert draft.normal_control.succeeded is True
    obs = draft.observations
    assert obs["response_fields_seen"].value == ["choices[].finish_reason", "choices[].message.content",
        "choices[].message.role", "id", "model", "object", "usage.completion_tokens", "usage.prompt_tokens", "usage.total_tokens"]
    assert obs["response_cases_tested"].value == ["gateway:normal"]
    assert obs["response_coverage_complete"].value is False
    assert not any(challenge.attempted for challenge in draft.challenges)
    for key in ("error_fields_seen", "response_options_returned", "response_options_tested", "private_markers_returned",
                "functional_probes_planned", "functional_probe_results"):
        assert not obs[key].collected and obs[key].value is None
    artifact, = draft.artifacts
    recorded = artifact.path.read_bytes()
    assert PRIVATE.encode() not in recorded and ("fixture-req13-" + side).encode() not in recorded
    assert ("fixture-allowed-" + side).encode() not in recorded
    proof = json.loads(recorded)["snapshot"]
    manifest = json.loads(Path(cfg[snapshot.INPUT]["path"]).read_bytes())
    assert proof["capture"]["original_body_sha256"] == manifest["body"]["sha256"]
    assert proof["manifest_sha256"] == pins["expected_manifest_sha256"]
    assert proof["target_file_sha256"] == pins["expected_target_file_sha256"]
    assert proof["recorded_at"] == (NOW + timedelta(seconds=1)).isoformat()
    assert proof["valid_from"] == START and proof["valid_until"] == END


@pytest.mark.parametrize("damage", ["owner", "actor", "target-bytes", "body-bytes", "legacy-capture", "missing-policy-pin"])
def test_retained_response_requires_original_independent_scope_and_capture(tmp_path, gateway_pair, damage):
    env, cfg, pins = snapshot_inputs(tmp_path, gateway_pair, "baseline", "e" * 64)
    if damage in {"owner", "legacy-capture"}:
        declaration = cfg[snapshot.INPUT]
        manifest_path = Path(declaration["path"])
        manifest = json.loads(manifest_path.read_bytes())
        response_path = Path(manifest["response_receipt"]["path"])
        receipt = json.loads(response_path.read_bytes())
        if damage == "owner":
            receipt["gateway_owner_sha256"] = "0" * 64
        else:
            del receipt["eof"]
        manifest["response_receipt"] = _pointer(response_path, receipt)
        declaration.update(_pointer(manifest_path, manifest))
        pins["expected_manifest_sha256"] = declaration["sha256"]
    elif damage == "actor":
        cfg[snapshot.INPUT]["actor_sha256"] = "f" * 64
    elif damage == "target-bytes":
        target = Path(cfg["target"])
        target.write_bytes(target.read_bytes() + b"\n")
    elif damage == "body-bytes":
        manifest = json.loads(Path(cfg[snapshot.INPUT]["path"]).read_bytes())
        body = Path(manifest["body"]["path"])
        body.write_bytes(body.read_bytes() + b"\n")
    else:
        pins["expected_policy_sha256"] = None
    executor = KubernetesResponseContentExecutor(cfg, runner=_forbidden, **pins)
    with pytest.raises(ValueError, match="^KUBERNETES_RESPONSE_SNAPSHOT_UNVERIFIED$"):
        executor.run(_check(), env, VALUES, None, tmp_path / "evidence")
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("recorded_request", [
    pytest.param({}, id="writer-defaults"),
    pytest.param({"model": "EXAMPLE-other-model"}, id="model"),
    pytest.param({"max_tokens": 2}, id="budget"),
    pytest.param({"max_tokens": True}, id="typed-budget"),
    pytest.param({"n": 1}, id="explicit-n"),
    pytest.param({"stream": True}, id="stream"),
    pytest.param({"stream": 0}, id="typed-stream"),
])
def test_retained_response_requires_the_runtime_final_to_match_the_ordinary_fixture(
        tmp_path, gateway_pair, monkeypatch, side, recorded_request):
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    env, cfg, pins = snapshot_inputs(tmp_path, gateway_pair, side, "e" * 64, recorded_request=recorded_request)
    executor = KubernetesResponseContentExecutor(cfg, runner=_forbidden, **pins)
    workdir = tmp_path / "evidence"
    if recorded_request:
        with pytest.raises(ValueError, match="^KUBERNETES_RESPONSE_SNAPSHOT_UNVERIFIED$"):
            executor.run(_check(), env, VALUES, None, workdir)
        assert not workdir.exists()
    else:
        draft = executor.run(_check(), env, VALUES, None, workdir)
        assert draft.status is ExecStatus.COMPLETED and draft.data_origin is DataOrigin.EXAMPLE


def test_incomplete_probe_response_remains_unqualified(tmp_path, gateway_pair):
    env, cfg, pins = snapshot_inputs(tmp_path, gateway_pair, "baseline", "e" * 64, probe_capture="incomplete")
    executor = KubernetesResponseContentExecutor(cfg, runner=_forbidden, **pins)
    with pytest.raises(ValueError, match="^KUBERNETES_RESPONSE_SNAPSHOT_UNVERIFIED$"):
        executor.run(_check(), env, VALUES, None, tmp_path / "evidence")
    assert not (tmp_path / "evidence").exists()


def test_unmodeled_response_names_remain_unqualified(tmp_path, gateway_pair):
    env, cfg, pins = snapshot_inputs(tmp_path, gateway_pair, "baseline", "e" * 64)
    declaration = cfg[snapshot.INPUT]
    manifest_path = Path(declaration["path"])
    manifest = json.loads(manifest_path.read_bytes())
    response_path = Path(manifest["response_receipt"]["path"])
    receipt = json.loads(response_path.read_bytes())
    body_path = Path(manifest["body"]["path"])
    body = json.loads(body_path.read_bytes())
    body["fixture_extension"] = None
    raw = canonical_json(body)
    manifest["body"] = _pointer(body_path, raw)
    receipt.update(body_sha256=manifest["body"]["sha256"], body_bytes=len(raw))
    manifest["response_receipt"] = _pointer(response_path, receipt)
    declaration.update(_pointer(manifest_path, manifest))
    pins["expected_manifest_sha256"] = declaration["sha256"]
    executor = KubernetesResponseContentExecutor(cfg, runner=_forbidden, **pins)
    with pytest.raises(ValueError, match="^KUBERNETES_RESPONSE_SNAPSHOT_FIELDS_UNQUALIFIED$"):
        executor.run(_check(), env, VALUES, None, tmp_path / "evidence")
    assert not (tmp_path / "evidence").exists()
