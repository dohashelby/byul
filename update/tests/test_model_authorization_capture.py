"""REQ-05: EXAMPLE runtime records/transport, no engine startup or confirmed final model."""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors.model_authorization import capture, validate_inputs
from guard.executors.owned_capture import read_owned_jsonl
from guard.models import new_id
from tests.builders import PACK_DIR, baseline_pack, environment, policy
from tests.test_native_observations import CaptureWorld
from tests.test_template_validation import IMAGE

GW = "example.invalid/runtime@sha256:" + "b" * 64
SOURCE = {"container": "EXAMPLE-gateway", "image": GW, "path": "/logs/gateway_records.jsonl"}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def inputs():
    pack = baseline_pack()
    env = environment(id=new_id("ENV"), engine={"name": "vllm", "image_digest": IMAGE},
                      model={"id": "EXAMPLE-model"})
    pol = policy(pack.ref(), id=new_id("POL"), values={"REQ-05": {"principal_model_grants": [
        {"principal_sha256": digest("EXAMPLE-allowed"), "model_sha256": digest(env.model.id)}]}})
    context = {"schema": "guard-fixed-model-authorization-context/1", "run_id": "EXAMPLE-run",
        "environment_ref": env.ref().model_dump(mode="json"), "policy_ref": pol.ref().model_dump(mode="json"),
        "runtime_policy": {"policy_id": "EXAMPLE-runtime", "policy_version": "1", "sha256": "8" * 64},
        "observation_session_id": "EXAMPLE-session", "target_id": "EXAMPLE-target", "instance_id": "EXAMPLE-instance",
        "window": {"start": "2026-10-26T00:00:00Z", "end": "2026-10-26T00:01:00Z"},
        "cases": [{"case_id": name, "request_id": str(index) * 32, "principal_sha256": digest(principal),
                   "requested_model_sha256": digest(env.model.id)}
                  for index, (name, principal) in enumerate(
                      [("normal", "EXAMPLE-allowed"), ("denied", "EXAMPLE-denied")], 1)]}
    target = {"run_id": "EXAMPLE-run", "engine": {"image_digest": IMAGE}}
    return env, pol, context, target


def runtime_records(context):
    records = []
    for case in context["cases"]:
        allowed = case["case_id"] == "normal"
        records.append({"schema_version": "runtime-mvp-record/1.0", "phase": "final",
            "request_id": case["request_id"], "observation_session_id": context["observation_session_id"],
            "run_id": context["run_id"], "target_id": context["target_id"],
            "target": {"instance_id": context["instance_id"]}, "recorded_at": "2026-10-26T00:00:30Z",
            "policy": {**context["runtime_policy"], "path": "/PRIVATE/policy"},
            "auth": {"state": "authenticated", "principal_id": "EXAMPLE-allowed" if allowed else "EXAMPLE-denied",
                     "credential": "PRIVATE_CREDENTIAL"},
            "request": {"model": "EXAMPLE-model", "context": {"body": "PRIVATE_PROMPT"}},
            "decision": {"result": "ALLOWED" if allowed else "DENIED", "source": "gateway",
                         "http_status": 200 if allowed else 403, "message": "PRIVATE_MESSAGE"},
            "delivery": {"attempted": allowed}, "data_origin": "external_backend", "backend_kind": "external"})
    return records


class RuntimeWorld(CaptureWorld):
    def __init__(self, raw, change=None):
        super().__init__({}, change)
        self.raw = raw

    def inspect_json(self, *args, **kwargs):
        row = super().inspect_json(*args, **kwargs)
        row["Config"]["Image"] = GW
        return row

    def exec_bounded(self, *args, **kwargs):
        result = super().exec_bounded(*args, **kwargs)
        packet = json.loads(result.stdout)
        packet.update(raw_base64=base64.b64encode(self.raw).decode(), raw_retained_bytes=len(self.raw),
                      raw_retained_sha256="0" * 64 if self.change == "hash" else hashlib.sha256(self.raw).hexdigest())
        packet["file_before"]["size"] = len(self.raw)
        packet["file_after"] = packet["path_after"] = deepcopy(packet["file_before"])
        result.stdout = json.dumps(packet)
        return result


def world_for(records, change=None):
    return RuntimeWorld(b"\n".join(json.dumps(r).encode() for r in records) + b"\n", change)


def test_actual_owned_jsonl_path_projects_auth_metadata_without_creating_engine_verdict():
    env, pol, context, target = inputs()
    world = world_for(runtime_records(context))
    result = capture(world, env, pol, context, target, SOURCE)
    assert world.calls == ["inspect", "read", "inspect"]
    assert result["environment_ref"] == env.ref().model_dump(mode="json")
    assert result["policy_ref"] == pol.ref().model_dump(mode="json")
    assert result["runtime_policy"]["sha256"] != result["policy_ref"]["sha256"]
    assert all(c["metadata_linked"] for c in result["cases"])
    assert [c["requested_model_authorized_by_declared_policy"] for c in result["cases"]] == [True, False]
    assert result["security"] == result["functional"] == result["performance"] == "UNKNOWN"
    assert not result["final_model_verified"] and not result["actual_consumption_verified"]
    assert not result["product_connected"] and not result["producer_authenticated"] and not result["official"]
    assert result["data_origin"] == "EXAMPLE" and result["capture"]["producer_authenticated"] is False
    serialized = json.dumps(result)
    assert "PRIVATE_" not in serialized and "EXAMPLE-allowed" not in serialized and "EXAMPLE-model" not in serialized
    pack = baseline_pack()
    assert pack.manifest.revision == 31 and pack.checks["REQ-05"].executor is pack.checks["REQ-05"].judge is None


@pytest.mark.parametrize("change", [
    lambda r: r.update(run_id="other"),
    lambda r: r.update(observation_session_id="other"),
    lambda r: r["target"].update(instance_id="other"),
    lambda r: r.update(target_id="other"),
    lambda r: r["policy"].update(sha256="9" * 64),
    lambda r: r["policy"].update(policy_version="2"),
    lambda r: r["auth"].update(principal_id="EXAMPLE-denied"),
    lambda r: r["auth"].update(state="not_configured"),
    lambda r: r["request"].update(model="other"),
    lambda r: r.update(recorded_at="2026-10-26T00:02:00Z"),
    lambda r: r.update(recorded_at="PRIVATE_BAD_DATE"),
    lambda r: r["delivery"].update(attempted=1),
])
def test_wrong_actor_model_policy_generation_or_window_is_not_linked(change):
    env, pol, context, target = inputs()
    records = runtime_records(context)
    change(records[0])
    result = capture(world_for(records), env, pol, context, target, SOURCE)
    assert result["cases"][0]["metadata_linked"] is False
    assert result["cases"][0]["diagnostic_codes"] == ["GATEWAY_RECORD_UNVERIFIED"]
    assert result["cases"][1]["metadata_linked"] is True
    assert result["security"] == "UNKNOWN" and "PRIVATE_" not in json.dumps(result)


@pytest.mark.parametrize("mode", ["started-only", "missing", "duplicate"])
def test_missing_or_ambiguous_final_is_never_selected_by_last_record(mode):
    env, pol, context, target = inputs()
    records = runtime_records(context)
    if mode == "started-only": records[0]["phase"] = "started"
    elif mode == "missing": records.pop(0)
    else: records.append(deepcopy(records[0]))
    result = capture(world_for(records), env, pol, context, target, SOURCE)
    assert not result["cases"][0]["metadata_linked"] and result["cases"][1]["metadata_linked"]
    assert result["cases"][0]["diagnostic_codes"] == [
        "GATEWAY_FINAL_DUPLICATE" if mode == "duplicate" else "GATEWAY_FINAL_MISSING"]


def test_independent_gateway_conflict_survives_other_missing_case_but_is_not_engine_fail():
    env, pol, context, target = inputs()
    records = runtime_records(context)[1:]
    records[0]["decision"].update(result="ALLOWED", http_status=200)
    records[0]["delivery"]["attempted"] = True
    result = capture(world_for(records), env, pol, context, target, SOURCE)
    assert result["cases"][1]["diagnostic_codes"] == ["DECLARED_MODEL_GRANT_CONFLICT"]
    assert result["cases"][0]["diagnostic_codes"] == ["GATEWAY_FINAL_MISSING"]
    assert result["security"] == "UNKNOWN" and result["final_model_verified"] is False


def test_declared_denial_with_relay_is_separate_from_denial_proof():
    env, pol, context, target = inputs()
    records = runtime_records(context)
    records[1]["delivery"]["attempted"] = True
    result = capture(world_for(records), env, pol, context, target, SOURCE)
    assert result["cases"][1]["diagnostic_codes"] == ["DECLARED_DENIAL_WITH_RELAY_ATTEMPT"]
    assert result["security"] == "UNKNOWN"


def test_fake_backend_is_example_even_if_it_claims_external_data_and_inputs_are_declared():
    env, pol, context, target = inputs()
    env = environment(id=env.id, origin="DECLARED", engine=env.engine, model=env.model)
    pol = policy(pol.pack, id=pol.id, data_origin="DECLARED", values=pol.values)
    context.update(environment_ref=env.ref().model_dump(mode="json"), policy_ref=pol.ref().model_dump(mode="json"))
    records = runtime_records(context)
    records[0]["backend_kind"] = "mock"
    result = capture(world_for(records), env, pol, context, target, SOURCE)
    assert result["data_origin"] == "EXAMPLE" and not result["producer_authenticated"]


@pytest.mark.parametrize("mutate", [
    lambda c: c["environment_ref"].update(revision=999),
    lambda c: c["policy_ref"].update(sha256="0" * 64),
    lambda c: c["cases"].append(deepcopy(c["cases"][0])),
    lambda c: c["cases"][1].update(principal_sha256=c["cases"][0]["principal_sha256"]),
    lambda c: c["cases"][0].update(requested_model_sha256="0" * 64),
    lambda c: c["window"].update(end="2026-10-26T00:30:00Z"),
    lambda c: c.update(body="PRIVATE_BODY"),
])
def test_invalid_or_expanded_input_fails_before_owned_source_is_read(mutate):
    env, pol, context, target = inputs()
    mutate(context)
    world = world_for([])
    with pytest.raises(ValueError):
        capture(world, env, pol, context, target, SOURCE)
    assert world.calls == []


def test_policy_grants_are_required_and_never_filled_from_observed_records():
    env, pol, context, target = inputs()
    pol = policy(pol.pack, id=new_id("POL"), values={})
    context["policy_ref"] = pol.ref().model_dump(mode="json")
    world = world_for(runtime_records(context))
    with pytest.raises(ValueError):
        capture(world, env, pol, context, target, SOURCE)
    assert world.calls == []


@pytest.mark.parametrize("change", ["unowned", "restart", "hash", "partial", "pipe"])
def test_owned_source_failures_do_not_yield_a_capture(change):
    env, pol, context, target = inputs()
    world = world_for(runtime_records(context), change)
    with pytest.raises(ValueError):
        capture(world, env, pol, context, target, SOURCE)


@pytest.mark.parametrize("raw", [
    b'{"a":1,"a":2}\n', b'{"a":NaN}\n', b'[]\n', b'{}\n\n{}\n', b'{}\n \n',
    b'{}\n' * 65, b'{"x":"' + b"x" * 16384 + b'"}\n', b"\xff\n",
], ids=["duplicate", "nan", "array", "blank", "whitespace", "records", "line-bound", "encoding"])
def test_jsonl_reuses_file_transport_and_rejects_ambiguous_or_unbounded_records(raw):
    with pytest.raises(ValueError):
        read_owned_jsonl(RuntimeWorld(raw), {"run_id": "EXAMPLE-run"}, **SOURCE)


def test_jsonl_crlf_bytes_are_hashed_without_normalizing_source():
    raw = b'{"x":1}\r\n{"x":2}\r\n'
    rows, provenance = read_owned_jsonl(RuntimeWorld(raw), {"run_id": "EXAMPLE-run"}, **SOURCE)
    assert rows == [{"x": 1}, {"x": 2}] and provenance["capture_sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.fixture
def cli_input(tmp_path, monkeypatch):
    env, pol, context, target = inputs()
    for name, data in [("env", env.to_dict()), ("policy", pol.to_dict()), ("context", context), ("target", target)]:
        (tmp_path / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    # Default pytest basetemp can be inside the checkout. Isolate only this command's output boundary.
    monkeypatch.setattr("guard.cli.__file__", str(tmp_path / "repo/update/src/guard/cli.py"))
    output = tmp_path / "capture.json"
    args = ["capture-model-authorization", "--pack", str(PACK_DIR),
        "--env", str(tmp_path / "env.json"), "--policy", str(tmp_path / "policy.json"),
        "--context", str(tmp_path / "context.json"), "--target", str(tmp_path / "target.json"),
        "--gateway-container", SOURCE["container"], "--gateway-image", GW, "--gateway-path", SOURCE["path"],
        "--output", str(output)]
    return args, output, context, tmp_path


def test_cli_default_does_not_construct_docker_or_create_output(cli_input, monkeypatch):
    args, output, _, _ = cli_input
    monkeypatch.setattr("guard.runtime.docker.Docker", lambda *_: pytest.fail("unexpected Docker"))
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0 and not output.exists(), result.output + repr(result.exception)
    assert json.loads(result.output)["ready_to_execute"] is False


def test_product_cli_capture_has_exact_refs_no_private_payload_and_no_store(cli_input, monkeypatch):
    args, output, context, folder = cli_input
    world = world_for(runtime_records(context))
    monkeypatch.setattr("guard.runtime.docker.Docker", lambda *_: world)
    before = {p.name: p.read_bytes() for p in folder.glob("*.json")}
    result = CliRunner().invoke(app, [*args, "--execute"])
    assert result.exit_code == 0, result.output + repr(result.exception)
    actual = json.loads(output.read_text(encoding="utf-8"))
    assert actual["policy_ref"] == context["policy_ref"] and actual["environment_ref"] == context["environment_ref"]
    assert not actual["product_connected"] and actual["security"] == "UNKNOWN"
    assert "PRIVATE_" not in output.read_text(encoding="utf-8") and not (folder / ".guard").exists()
    assert all((folder / p).read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize("broken", ["seal", "json", "existing", "outside-scope", "transport"])
def test_cli_expected_failures_have_fixed_code_and_preserve_existing_files(cli_input, monkeypatch, broken):
    args, output, context, folder = cli_input
    world = world_for(runtime_records(context), "pipe" if broken == "transport" else None)
    monkeypatch.setattr("guard.runtime.docker.Docker", lambda *_: world)
    if broken == "seal":
        data = json.loads((folder / "env.json").read_text(encoding="utf-8"))
        data["sha256"] = "0" * 64
        (folder / "env.json").write_text(json.dumps(data), encoding="utf-8")
    elif broken == "json":
        (folder / "context.json").write_text("PRIVATE_BROKEN", encoding="utf-8")
    elif broken == "existing":
        output.write_bytes(b"EXAMPLE original")
    elif broken == "outside-scope":
        context["cases"][0]["requested_model_sha256"] = digest("other-model")
        (folder / "context.json").write_text(json.dumps(context), encoding="utf-8")
    result = CliRunner().invoke(app, [*args, "--execute"])
    assert result.exit_code == 2 and result.output == "MODEL_AUTHORIZATION_CAPTURE_FAILED\n"
    assert not output.exists() if broken != "existing" else output.read_bytes() == b"EXAMPLE original"
    assert broken == "transport" or world.calls == []


def test_cli_refuses_repository_output_before_docker_or_any_file_write(cli_input, monkeypatch):
    args, output, _, folder = cli_input
    (folder / "repo").mkdir()
    forbidden = folder / "repo/capture.json"
    args[args.index("--output") + 1] = str(forbidden)
    monkeypatch.setattr("guard.runtime.docker.Docker", lambda *_: pytest.fail("unexpected Docker"))
    result = CliRunner().invoke(app, [*args, "--execute"])
    assert result.exit_code == 2 and result.output == "MODEL_AUTHORIZATION_CAPTURE_FAILED\n"
    assert not output.exists() and not forbidden.exists()


def test_unexpected_programming_error_is_not_normalized_as_a_capture_failure(cli_input, monkeypatch):
    args, output, _, _ = cli_input
    error = RuntimeError("EXAMPLE programming error")
    def broken(*args, **kwargs):
        raise error
    monkeypatch.setattr("guard.collectors.model_authorization.capture", broken)
    monkeypatch.setattr("guard.runtime.docker.Docker", lambda *_: object())
    result = CliRunner().invoke(app, [*args, "--execute"])
    assert result.exception is error and "MODEL_AUTHORIZATION_CAPTURE_FAILED" not in result.output
    assert not output.exists()


def test_claimed_final_model_or_http_body_is_not_promoted_to_engine_evidence():
    env, pol, context, target = inputs()
    records = runtime_records(context)
    records[1]["decision"].update(result="ALLOWED", http_status=200)
    records[1].update(final_model_verified=True, response={"model": "EXAMPLE-model", "error": "PRIVATE_ERROR"})
    result = capture(world_for(records), env, pol, context, target, SOURCE)
    assert not result["actual_consumption_verified"] and result["security"] == "UNKNOWN"
    assert "PRIVATE_" not in json.dumps(result) and "final_model_verified" not in result["cases"][1]["gateway"]
