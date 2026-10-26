"""실제 파일 수집 경계와 센서 함수; 엔진·Docker·기업 생산자는 EXAMPLE 대역이다."""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from guard.adapters.template_read_sensor import SensorStatus, observe_template_call, read_template
from guard.executors.native_observations import validate_capture
from guard.executors.owned_capture import read_owned_json, strict_json
from guard.models import DataOrigin
from tests.builders import environment
from tests.test_template_supply_approval import SupplyWorld, run, judge, summary, CID, IMAGE_ID
from tests.test_template_validation import IMAGE
from tools.capture_route_observations import capture, main


def native_record(env, run_id="EXAMPLE-run", image=IMAGE, kind="template"):
    if kind == "template":
        events = [{"kind": "template_read", "raw_sha256": "1" * 64, "decoded_sha256": "2" * 64,
                   "raw_bytes": 20, "decoded_bytes": 19, "loader_sha256": "3" * 64,
                   "process_id": 1, "buffer_returned": True, "selection_observed": False,
                   "apply_observed": False, "generate_handoff_observed": False, "worker_consumption_verified": False}]
    else:
        events = [{"event_id": "EXAMPLE-event", "producer": "EXAMPLE-proxy",
                   "ingress_id": "EXAMPLE-ingress", "upstream_id": "EXAMPLE-upstream",
                   "request_sha256": "4" * 64, "principal_sha256": "5" * 64,
                   "external_method": "POST", "external_path": "/tenant/v1/completions",
                   "auth_method": "POST", "auth_path": "/tenant/v1/completions",
                   "auth_operation": "text_completion", "auth_decision": "allow",
                   "rewritten_method": "POST", "rewritten_path": "/v1/completions",
                   "handler_id": "EXAMPLE-handler", "handler_operation": "text_completion",
                   "handler_outcome": "entered"}]
    return {"schema": f"guard-{kind}-native-observations/1", "run_id": run_id,
            "environment_ref": env.ref().model_dump(mode="json"), "image": image, "events": events}


class CaptureWorld:
    def __init__(self, value, change=None):
        self.value, self.change, self.calls = value, change, []
    def inspect_json(self, container, timeout):
        self.calls.append("inspect")
        row = {"Id": CID, "Name": "/" + container, "Image": IMAGE_ID,
               "Config": {"Image": IMAGE, "Labels": {"guard.run": "EXAMPLE-run"}},
               "State": {"Running": True, "StartedAt": "2026-10-26T00:00:00Z"}}
        if self.change == "restart" and self.calls.count("inspect") > 1:
            row["State"]["StartedAt"] = "2026-10-26T00:00:01Z"
        if self.change == "unowned":
            row["Config"]["Labels"] = {}
        return row
    def exec_bounded(self, *args, **kwargs):
        self.calls.append("read")
        raw = json.dumps(self.value).encode()
        packet = {"schema": "guard-container-file/2", "source_complete": True,
                  "file_identity_stable": True, "truncated": False, "nofollow_components": True,
                  "regular_file": True, "raw_base64": base64.b64encode(raw).decode(),
                  "raw_retained_bytes": len(raw), "raw_retained_sha256": hashlib.sha256(raw).hexdigest(),
                  "file_before": {"device": 1, "inode": 2, "size": len(raw), "mtime_ns": 1, "ctime_ns": 1}}
        packet["file_after"] = packet["path_after"] = deepcopy(packet["file_before"])
        if self.change == "hash":
            packet["raw_retained_sha256"] = "0" * 64
        if self.change == "partial":
            packet["source_complete"] = False
        return SimpleNamespace(returncode=0, stdout=json.dumps(packet), capture={
            "complete": self.change != "pipe", "bounded_at_source": True, "timed_out": False, "truncated": False})


def test_owned_file_capture_preserves_provenance_without_authentication():
    env = environment(engine={"name": "vllm", "image_digest": IMAGE})
    packet = native_record(env)
    world = CaptureWorld(packet)
    value, provenance = read_owned_json(world, {"run_id": "EXAMPLE-run"}, container="EXAMPLE-engine",
                                       image=IMAGE, path="/guard-observations/template.json")
    assert value == packet and world.calls == ["inspect", "read", "inspect"]
    assert provenance["producer_authenticated"] is provenance["record_authenticity_verified"] is False
    assert provenance["capture_sha256"] == hashlib.sha256(json.dumps(packet).encode()).hexdigest()


@pytest.mark.parametrize("change", ["restart", "unowned", "hash", "partial", "pipe"])
def test_failed_transport_owner_or_identity_is_never_admitted(change):
    world = CaptureWorld({}, change)
    with pytest.raises(ValueError):
        read_owned_json(world, {"run_id": "EXAMPLE-run"}, container="EXAMPLE-engine",
                        image=IMAGE, path="/guard-observations/template.json")
    assert change != "unowned" or "read" not in world.calls


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(run_id="unrelated"),
    lambda p: p["environment_ref"].update(revision=999),
    lambda p: p["events"][0].update(worker_consumption_verified=True),
    lambda p: p["events"][0].update(raw_bytes=True),
    lambda p: p["events"][0].update(template_body="PRIVATE_BODY"),
])
def test_template_capture_rejects_wrong_context_or_unbounded_claims(mutation):
    env = environment()
    packet = native_record(env)
    mutation(packet)
    with pytest.raises(ValueError):
        validate_capture(packet, kind="template", env_ref=env.ref().model_dump(mode="json"),
                         run_id="EXAMPLE-run", image=IMAGE)


@pytest.mark.parametrize("origin, expected", [
    (DataOrigin.EXAMPLE, "EXAMPLE"),
    (DataOrigin.DECLARED, "DECLARED"),
    (DataOrigin.MEASURED, "DECLARED"),
])
def test_route_capture_preserves_source_ceiling_without_a_product_verdict(origin, expected):
    env = environment(origin=origin)
    value = native_record(env, kind="route")
    result = capture(CaptureWorld(value), {"run_id": "EXAMPLE-run"}, env,
                     container="EXAMPLE-gateway", image=IMAGE, path="/guard-observations/route.json")
    # The former unconditional DECLARED expectation promoted the EXAMPLE builder input.
    # File transport can neither upgrade examples nor authenticate a measured producer.
    assert result["record"] == value and result["data_origin"] == expected
    assert result["environment_ref"] == env.ref().model_dump(mode="json")
    assert result["capture"]["capture_sha256"] == hashlib.sha256(json.dumps(value).encode()).hexdigest()
    assert result["product_connected"] is False and result["security"] == "UNKNOWN"
    assert result["capture"]["producer_authenticated"] is False
    assert result["summary"]["independent_source_verified"] is False
    assert result["summary"]["actual_consumption_verified"] is False


@pytest.fixture
def external_capture_dir():
    # Successful evidence publication must remain outside every real checkout,
    # including when pytest's configured basetemp is inside this repository.
    with tempfile.TemporaryDirectory(prefix="guard-route-capture-") as folder:
        yield Path(folder)


def test_route_cli_execute_keeps_example_origin_in_saved_attachment(external_capture_dir, monkeypatch, capsys):
    tmp_path = external_capture_dir
    env = environment(origin=DataOrigin.EXAMPLE)
    packet = native_record(env, kind="route")
    source, target, output = (tmp_path / name for name in ("env.json", "target.json", "route.json"))
    source.write_text(json.dumps(env.to_dict()), encoding="utf-8")
    target.write_text('{"run_id":"EXAMPLE-run"}', encoding="utf-8")
    world = CaptureWorld(packet)
    monkeypatch.setattr("tools.capture_route_observations.Docker", lambda _: world)
    monkeypatch.setattr("tools.capture_route_observations.REPO", tmp_path / "repo")
    assert main(["--env", str(source), "--target", str(target), "--container", "EXAMPLE-gateway",
                 "--image", IMAGE, "--path", "/guard-observations/route.json", "--output", str(output),
                 "--execute"]) == 0
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["data_origin"] == "EXAMPLE" and saved["record"] == packet
    assert saved["security"] == "UNKNOWN" and saved["product_connected"] is False
    assert saved["capture"]["producer_authenticated"] is False
    assert world.calls == ["inspect", "read", "inspect"]
    assert json.loads(capsys.readouterr().out) == {
        "captured": True, "engine_started": False, "product_connected": False, "security": "UNKNOWN"}


@pytest.mark.parametrize("change", [
    lambda p: p["events"].append(deepcopy(p["events"][0])),
    lambda p: p["events"][0].update(secret="PRIVATE_SECRET"),
    lambda p: p["events"][0].update(principal_sha256="operator-name"),
    lambda p: p["events"][0].update(handler_outcome="success"),
])
def test_route_native_contract_does_not_accept_raw_or_ambiguous_producer_claims(change):
    env = environment()
    packet = native_record(env, kind="route")
    change(packet)
    with pytest.raises(ValueError):
        capture(CaptureWorld(packet), {"run_id": "EXAMPLE-run"}, env,
                container="EXAMPLE-gateway", image=IMAGE, path="/guard-observations/route.json")


def test_route_cli_default_never_creates_docker_or_output(external_capture_dir, monkeypatch, capsys):
    tmp_path = external_capture_dir
    env = environment()
    source = tmp_path / "env.json"
    source.write_text(json.dumps(env.to_dict()), encoding="utf-8")
    target = tmp_path / "target.json"
    target.write_text('{"run_id":"EXAMPLE-run"}', encoding="utf-8")
    monkeypatch.setattr("tools.capture_route_observations.Docker", lambda *_: pytest.fail("unexpected Docker query"))
    output = tmp_path / "new.json"
    # Explicit external directory because repository basetemp is not a valid evidence destination.
    monkeypatch.setattr("tools.capture_route_observations.REPO", tmp_path / "repo")
    args = ["--env", str(source), "--target", str(target), "--container", "EXAMPLE-gateway",
            "--image", IMAGE, "--path", "/guard-observations/route.json", "--output", str(output)]
    assert main(args) == 0 and not output.exists()
    result = json.loads(capsys.readouterr().out)
    assert result["ready_to_execute"] is result["capture_executed"] is False


def test_actual_read_sensor_hashes_returned_buffer_not_later_path_contents(tmp_path):
    loader = tmp_path / "loader.py"
    loader.write_bytes(b"# EXAMPLE reviewed source")
    path = tmp_path / "template.jinja"
    original = b"EXAMPLE\r\n{{ messages }}\r\n"
    path.write_bytes(original)
    events = []
    def emit(event):
        events.append(event)
        path.write_bytes(b"EXAMPLE replaced after successful read")
    text = read_template(path, reviewed_loader_path=loader,
                         reviewed_loader_sha256=hashlib.sha256(loader.read_bytes()).hexdigest(), emit=emit)
    assert text == original.decode().replace("\r\n", "\n")
    assert events[0]["raw_sha256"] == hashlib.sha256(original).hexdigest()
    assert events[0]["decoded_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert events[0]["generate_handoff_observed"] is events[0]["worker_consumption_verified"] is False
    assert "messages" not in json.dumps(events) and str(path) not in json.dumps(events)


def _select(*, chat_template):
    return chat_template


def _apply(*, chat_template):
    raise RuntimeError("PRIVATE_NATIVE_ERROR")


def test_native_synchronous_selection_and_error_are_preserved_without_exception_text():
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    events = []
    result = observe_template_call(_select, {"chat_template": "EXAMPLE"}, stage="selection",
                                   reviewed_source_sha256=digest, emit=events.append)
    assert result == "EXAMPLE" and events[0]["returned_sha256"] == hashlib.sha256(b"EXAMPLE").hexdigest()
    with pytest.raises(RuntimeError, match="PRIVATE_NATIVE_ERROR"):
        observe_template_call(_apply, {"chat_template": "EXAMPLE"}, stage="apply",
                              reviewed_source_sha256=digest, emit=events.append)
    assert events[1]["outcome"] == "error" and "PRIVATE_NATIVE_ERROR" not in json.dumps(events)


def test_source_pin_mismatch_blocks_before_native_call_or_template_read(tmp_path):
    source = tmp_path / "source.py"
    source.write_bytes(b"# EXAMPLE")
    with pytest.raises(ValueError, match="SOURCE_MISMATCH"):
        read_template(tmp_path / "does-not-exist", reviewed_loader_path=source,
                      reviewed_loader_sha256="0" * 64, emit=lambda _: pytest.fail("event emitted"))


@pytest.mark.parametrize("raw", [b'{"same":1,"same":2}', b'{"value":NaN}', b"\xff", b"x" * 65537],
                         ids=["duplicate", "nan", "invalid-utf8", "over-budget"])
def test_strict_capture_json_boundary(raw):
    with pytest.raises(ValueError):
        strict_json(raw)


@pytest.mark.parametrize("invalid", [False, True])
def test_sup03_optional_native_capture_is_attached_without_changing_verdict_ceiling(tmp_path, invalid):
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE},
                      model={"id": "fixture-model"})
    packet = native_record(env)
    if invalid:
        packet["events"][0]["template_body"] = "PRIVATE_TEMPLATE"
    class NativeWorld(SupplyWorld):
        def exec_bounded(self, *args, **kwargs):
            return CaptureWorld(packet).exec_bounded(*args, **kwargs)
    draft, _ = run(tmp_path, world=NativeWorld(), cfg={"native_observation_path": "/guard-observations/template.json"})
    assert judge(draft).verdicts["security"].value == "UNKNOWN"
    assert draft.observations["template_consumer_verified"].value is False
    native = [a for a in draft.artifacts if a.name == "template_native_observations.json"]
    if invalid:
        assert native == []
        assert summary(draft)["native_observations"]["captured"] is False
    else:
        assert len(native) == 1
        value = json.loads(native[0].path.read_text(encoding="utf-8"))["capture"]
        assert value["record"] == packet and value["summary"]["actual_consumption_verified"] is False
        assert value["capture"]["producer_authenticated"] is False
    assert all("PRIVATE_TEMPLATE" not in a.path.read_text(encoding="utf-8") for a in draft.artifacts)


def test_optional_native_capture_has_separate_executor_version():
    from guard.executors.template_validation import TemplateValidationExecutor
    assert TemplateValidationExecutor({}, docker=object()).version == "0.4.0"
    assert TemplateValidationExecutor({"native_observation_path": "/guard-observations/template.json"},
                                     docker=object()).version == "0.5.0"


@pytest.mark.parametrize("broken", ["yaml", "target-list", "existing-output"])
def test_route_cli_expected_input_errors_are_fixed_and_never_echo_private_paths(tmp_path, monkeypatch, capsys, broken):
    envfile = tmp_path / "PRIVATE-env.json"
    envfile.write_text(json.dumps(environment().to_dict()), encoding="utf-8")
    target = tmp_path / "PRIVATE-target.json"
    target.write_text('{"run_id":"example-run"}', encoding="utf-8")
    output = tmp_path / "PRIVATE-output.json"
    monkeypatch.setattr("tools.capture_route_observations.REPO", tmp_path / "repo")
    monkeypatch.setattr("tools.capture_route_observations.Docker", lambda *_: pytest.fail("unexpected Docker"))
    if broken == "yaml":
        envfile.write_text("engine: [PRIVATE-broken", encoding="utf-8")
    elif broken == "target-list":
        target.write_text("[]", encoding="utf-8")
    else:
        output.write_bytes(b"original")
    assert main(["--env", str(envfile), "--target", str(target), "--container", "example-producer",
                 "--image", IMAGE, "--path", "/guard-observations/route.json", "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert captured.out == "NATIVE_ROUTE_CAPTURE_FAILED\n" and captured.err == ""
    assert not output.exists() if broken != "existing-output" else output.read_bytes() == b"original"


def test_native_exception_survives_telemetry_emitter_failure():
    def broken_emit(_):
        raise OSError("PRIVATE_EMITTER_ERROR")
    with pytest.raises(RuntimeError, match="PRIVATE_NATIVE_ERROR"):
        observe_template_call(_apply, {"chat_template": "EXAMPLE"}, stage="apply",
            reviewed_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), emit=broken_emit)


def test_handle_and_path_metadata_views_are_each_checked_without_cross_api_time_assumption(tmp_path, monkeypatch):
    import os
    from guard.adapters import template_read_sensor as sensor
    source = tmp_path / "source.py"
    source.write_bytes(b"# EXAMPLE source")
    template = tmp_path / "template.txt"
    template.write_bytes(b"EXAMPLE")
    native = os.fstat
    def handle_view(fd):
        item = native(fd)
        return SimpleNamespace(**{key: getattr(item, key) for key in
            ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode")}, st_ctime_ns=item.st_ctime_ns + 1)
    monkeypatch.setattr(sensor.os, "fstat", handle_view)
    events = []
    assert read_template(template, reviewed_loader_path=source,
        reviewed_loader_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), emit=events.append) == "EXAMPLE"
    assert len(events) == 1


def _return_once(*, chat_template, calls, result):
    calls.append("native")
    return result


def _raise_once(*, chat_template, calls, error):
    calls.append("native")
    raise error


def _broken_emitter(_):
    raise OSError("PRIVATE_EMITTER_ERROR")


@pytest.mark.parametrize("stage", ["selection", "apply"])
def test_successful_native_result_identity_and_single_call_survive_emit_failure(stage, caplog):
    calls, expected, status = [], object(), SensorStatus()
    actual = observe_template_call(_return_once, {"chat_template": "EXAMPLE", "calls": calls, "result": expected},
        stage=stage, reviewed_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        emit=_broken_emitter, status=status)
    assert actual is expected and calls == ["native"]
    assert status.snapshot() == {"schema": "guard-template-sensor-emission/1", "attempted_events": 1,
        "failed_events": 1, "emission_complete": False, "diagnostic_codes": ["TEMPLATE_SENSOR_EMIT_FAILED"]}
    assert [r.getMessage() for r in caplog.records] == ["TEMPLATE_SENSOR_EMIT_FAILED"]
    assert all(r.exc_info is None for r in caplog.records)
    assert "PRIVATE_" not in caplog.text


def test_legacy_sensor_caller_gets_fixed_diagnostic_without_status_argument(caplog):
    actual = observe_template_call(_select, {"chat_template": "EXAMPLE"}, stage="selection",
        reviewed_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), emit=_broken_emitter)
    assert actual == "EXAMPLE" and "TEMPLATE_SENSOR_EMIT_FAILED" in caplog.text
    assert "PRIVATE_" not in caplog.text


def test_sensor_read_returns_same_crlf_buffer_when_emitter_fails(tmp_path, caplog):
    loader, template = tmp_path / "loader.py", tmp_path / "template"
    loader.write_bytes(b"# EXAMPLE source")
    raw = b"EXAMPLE\r\n{{ messages }}\r"
    template.write_bytes(raw)
    events, status = [], SensorStatus()
    def partial_emit(event):
        events.append(event)
        template.write_bytes(b"EXAMPLE replaced")
        _broken_emitter(event)
    actual = read_template(template, reviewed_loader_path=loader,
        reviewed_loader_sha256=hashlib.sha256(loader.read_bytes()).hexdigest(), emit=partial_emit, status=status)
    assert actual == "EXAMPLE\n{{ messages }}\n"
    assert events[0]["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert events[0]["decoded_sha256"] == hashlib.sha256(actual.encode()).hexdigest()
    assert status.snapshot()["emission_complete"] is False and status.failed_events == 1
    assert "PRIVATE_" not in caplog.text and str(template) not in caplog.text


def test_native_exception_object_is_preserved_when_both_native_and_emitter_fail(caplog):
    calls, status, error = [], SensorStatus(), RuntimeError("PRIVATE_NATIVE_ERROR")
    with pytest.raises(RuntimeError) as caught:
        observe_template_call(_raise_once, {"chat_template": "EXAMPLE", "calls": calls, "error": error},
            stage="apply", reviewed_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            emit=_broken_emitter, status=status)
    assert caught.value is error and calls == ["native"]
    assert status.failed_events == 1 and status.snapshot()["emission_complete"] is False
    assert "PRIVATE_" not in caplog.text and all(r.exc_info is None for r in caplog.records)


def test_deferred_result_is_not_consumed_or_replaced_on_emitter_failure():
    consumed, calls, status = [], [], SensorStatus()
    def deferred():
        consumed.append(True)
        yield "EXAMPLE"
    result = deferred()
    try:
        actual = observe_template_call(_return_once, {"chat_template": "EXAMPLE", "calls": calls, "result": result},
            stage="apply", reviewed_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            emit=_broken_emitter, status=status)
        assert actual is result and calls == ["native"] and consumed == []
        assert status.snapshot()["emission_complete"] is False
    finally:
        result.close()


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_emitter_control_flow_interrupts_are_recorded_and_propagated(interrupt):
    error, status, calls = interrupt(), SensorStatus(), []
    def stop(_):
        raise error
    with pytest.raises(interrupt) as caught:
        observe_template_call(_return_once, {"chat_template": "EXAMPLE", "calls": calls, "result": "EXAMPLE"},
            stage="apply", reviewed_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            emit=stop, status=status)
    assert caught.value is error and calls == ["native"]
    assert status.failed_events == 1 and status.snapshot()["emission_complete"] is False


def test_status_stays_incomplete_after_later_success_even_if_logging_also_fails(monkeypatch):
    import guard.adapters.template_read_sensor as sensor
    monkeypatch.setattr(sensor._LOG, "warning", _broken_emitter)
    status = SensorStatus()
    args = dict(stage="selection", reviewed_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                status=status)
    assert observe_template_call(_select, {"chat_template": "EXAMPLE"}, emit=_broken_emitter, **args) == "EXAMPLE"
    assert observe_template_call(_select, {"chat_template": "EXAMPLE"}, emit=lambda _: None, **args) == "EXAMPLE"
    assert status.attempted_events == 2 and status.failed_events == 1
    assert status.snapshot()["emission_complete"] is False
    assert SensorStatus().snapshot()["emission_complete"] is False


@pytest.mark.parametrize("invalid", ["source", "file-changed", "decode"])
def test_read_precondition_failures_are_not_swallowed_as_telemetry_errors(tmp_path, monkeypatch, invalid):
    import guard.adapters.template_read_sensor as sensor
    loader, template = tmp_path / "loader.py", tmp_path / "template"
    loader.write_bytes(b"# EXAMPLE source")
    template.write_bytes(b"\xff" if invalid == "decode" else b"EXAMPLE")
    source_hash, status, events = hashlib.sha256(loader.read_bytes()).hexdigest(), SensorStatus(), []
    if invalid == "source":
        source_hash = "0" * 64
    if invalid == "file-changed":
        original, calls = sensor.os.fstat, []
        def changed(fd):
            actual = original(fd)
            calls.append(True)
            return actual if len(calls) == 1 else SimpleNamespace(st_dev=actual.st_dev, st_ino=actual.st_ino,
                st_size=actual.st_size + 1, st_mtime_ns=actual.st_mtime_ns, st_ctime_ns=actual.st_ctime_ns)
        monkeypatch.setattr(sensor.os, "fstat", changed)
    with pytest.raises((ValueError, UnicodeDecodeError)):
        read_template(template, reviewed_loader_path=loader, reviewed_loader_sha256=source_hash,
                      emit=events.append, status=status)
    assert events == [] and status.attempted_events == 0 and not status.snapshot()["emission_complete"]


def test_native_source_mismatch_and_unsupported_calls_still_block_before_invocation():
    calls, status = [], SensorStatus()
    with pytest.raises(ValueError, match="SOURCE_MISMATCH"):
        observe_template_call(_return_once, {"chat_template": "EXAMPLE", "calls": calls, "result": "EXAMPLE"},
            stage="selection", reviewed_source_sha256="0" * 64, emit=_broken_emitter, status=status)
    with pytest.raises(ValueError, match="CALL_UNSUPPORTED"):
        observe_template_call(_return_once, {}, stage="generate", reviewed_source_sha256="0" * 64,
                              emit=_broken_emitter, status=status)
    assert calls == [] and status.attempted_events == 0


@pytest.mark.parametrize("failed", [False, True])
def test_sup03_sensor_delivery_status_is_evidence_metadata_not_consumption_or_pass(tmp_path, failed):
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE},
                      model={"id": "fixture-model"})
    packet = native_record(env)
    status = SensorStatus(attempted_events=2 if failed else 1, failed_events=1 if failed else 0)
    packet["sensor_status"] = status.snapshot()
    class NativeWorld(SupplyWorld):
        def exec_bounded(self, *args, **kwargs):
            return CaptureWorld(packet).exec_bounded(*args, **kwargs)
    draft, _ = run(tmp_path, world=NativeWorld(), cfg={"native_observation_path": "/guard-observations/template.json"})
    actual = summary(draft)["native_observations"]
    assert actual["reported_sensor_status"] == status.snapshot()
    assert actual["collection_errors"] == (["TEMPLATE_SENSOR_EMIT_FAILED"] if failed else [])
    assert actual["actual_consumption_verified"] is actual["producer_authenticated"] is False
    assert judge(draft).verdicts["security"].value == "UNKNOWN"
    assert draft.observations["template_consumer_verified"].value is False
    artifact, = [a for a in draft.artifacts if a.name == "template_native_observations.json"]
    saved = json.loads(artifact.path.read_text(encoding="utf-8"))
    assert saved["capture"]["record"]["sensor_status"] == status.snapshot()


@pytest.mark.parametrize("change", [
    {"attempted_events": True}, {"attempted_events": 65}, {"failed_events": -1},
    {"failed_events": 2}, {"emission_complete": True}, {"diagnostic_codes": ["PRIVATE_ERROR"]},
    {"template": "PRIVATE_TEMPLATE"}, {"attempted_events": 3, "failed_events": 0, "emission_complete": True,
                                     "diagnostic_codes": []},
])
def test_native_capture_refuses_false_completion_or_unbounded_sensor_status(change):
    env, status = environment(), SensorStatus(attempted_events=1, failed_events=1)
    packet = native_record(env)
    packet["sensor_status"] = {**status.snapshot(), **change}
    with pytest.raises(ValueError):
        validate_capture(packet, kind="template", env_ref=env.ref().model_dump(mode="json"),
                         run_id="EXAMPLE-run", image=IMAGE)


def test_legacy_capture_does_not_gain_a_delivery_claim_and_empty_capture_stays_invalid():
    env = environment()
    packet = native_record(env)
    result = validate_capture(packet, kind="template", env_ref=env.ref().model_dump(mode="json"),
                              run_id="EXAMPLE-run", image=IMAGE)
    assert "reported_sensor_status" not in result and result["actual_consumption_verified"] is False
    packet.update(events=[], sensor_status=SensorStatus(attempted_events=1, failed_events=1).snapshot())
    with pytest.raises(ValueError):
        validate_capture(packet, kind="template", env_ref=env.ref().model_dump(mode="json"),
                         run_id="EXAMPLE-run", image=IMAGE)
