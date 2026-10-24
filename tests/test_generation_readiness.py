"""Explicit startup generation uses synthetic I/O, never an engine or Docker."""
from copy import deepcopy
import hashlib
import json

import pytest

from guard import application
from guard.executors import registry
from guard.executors.fake import FakeExecutor
from guard.runtime import generation_readiness as readiness
from guard.runtime import sandbox
from guard.runtime.docker import DockerError
from guard.store import Store
from tests.builders import T0, policy
from tests.test_managed_update_sentinel import _inputs, _memory_runtime
from tests.test_windows_cpu_launch import setup_inputs


def settings(**updates):
    return {"model": "EXAMPLE-model", "launch_profile": "cpu-bounded", "gpu": False,
            readiness.SETTING: 120, **updates}


def completion(model="EXAMPLE-model", text="EXAMPLE"):
    return {"outcome": 200, "phase": "response", "body": json.dumps({
        "model": model, "choices": [{"text": text, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})}


def test_unconfigured_readiness_keeps_legacy_and_static_profile_is_bounded():
    assert readiness.profile({}) is None
    cfg = settings()
    before = deepcopy(cfg)
    profile = readiness.profile(cfg)
    assert cfg == before
    assert profile == {
        "protocol": readiness.PROTOCOL, "origin": "trusted", "target": "engine", "max_requests": 1,
        "request_timeout_seconds": 120.0, "probe_process_timeout_seconds": 150.0,
        "request": {"method": "POST", "path": "/v1/completions",
                    "body": {"model": "EXAMPLE-model", "prompt": "ping", "max_tokens": 1, "temperature": 0}}}
    assert readiness.profile(settings(**{readiness.SETTING: 1}))["request_timeout_seconds"] == 1
    assert readiness.profile(settings(**{readiness.SETTING: 300}))["request_timeout_seconds"] == 300


@pytest.mark.parametrize("value", [True, False, None, 0, -1, .5, 301, "120", [], float("inf"), float("nan")])
def test_readiness_rejects_invalid_limits_before_any_operation(value):
    with pytest.raises(ValueError, match="between 1 and 300"):
        readiness.profile(settings(**{readiness.SETTING: value}))


@pytest.mark.parametrize("change", [{"gpu": True}, {"launch_profile": "legacy"}, {"model": ""}])
def test_readiness_rejects_other_profiles_or_absent_model(change):
    with pytest.raises(ValueError):
        readiness.profile(settings(**change))


@pytest.mark.parametrize("engine,wait_ready", [("vllm", False), ("stub", True)])
def test_readiness_rejects_other_lifecycle_modes_before_docker(tmp_path, monkeypatch, engine, wait_ready):
    monkeypatch.setattr(sandbox, "pull_images", lambda *args: pytest.fail("invalid readiness must not reach Docker"))
    with pytest.raises(ValueError, match="wait_ready"):
        sandbox._up(object(), "isolated", tmp_path / "run", "EXAMPLE-key", engine=engine,
                    vllm=settings(), wait_ready=wait_ready)
    assert not (tmp_path / "run").exists()


def test_readiness_sends_one_trusted_generation_and_preserves_redacted_utf8_bytes(tmp_path):
    conditions = readiness.profile(settings())
    calls = []
    secret = "EXAMPLE-private-gateway-key"

    def probe(network, requests, timeout):
        calls.append((network, deepcopy(requests), timeout))
        return [completion(text="한국어 " + secret)]

    saved = readiness.run(tmp_path, conditions, "http://192.0.2.2:8000", "EXAMPLE-trusted", probe,
                          secret_values=[secret])
    assert calls == [("EXAMPLE-trusted", [{"id": "generation-readiness", "method": "POST",
        "url": "http://192.0.2.2:8000/v1/completions", "body": conditions["request"]["body"],
        "want_body": True}], 120.0)]
    raw = (tmp_path / readiness.FILENAME).read_bytes()
    doc = json.loads(raw)
    assert b"\r\n" not in raw and raw.endswith(b"\n")
    assert "한국어" in raw.decode("utf-8") and secret not in raw.decode("utf-8")
    assert doc["attempts"] == 1 and doc["code"] == "GENERATION_READINESS_READY"
    assert doc["profile"] == conditions and doc["elapsed_seconds"] >= 0
    assert saved == {"path": readiness.FILENAME, "sha256": hashlib.sha256(raw).hexdigest()}
    assert "not an EVD" in doc["scope"]


@pytest.mark.parametrize("damage", ["status", "missing", "empty", "html", "model", "usage", "finish", "error", "conflict", "limit", "timeout"])
def test_bad_generation_is_retained_and_never_retried(tmp_path, damage):
    response = completion()
    body = json.loads(response["body"])
    if damage == "status": response["outcome"] = 500
    elif damage == "missing": response.pop("body")
    elif damage == "empty": response["body"] = "{}"
    elif damage == "html": response["body"] = "<html>EXAMPLE</html>"
    elif damage == "model": body["model"] = "EXAMPLE-other"
    elif damage == "usage": body["usage"]["completion_tokens"] = True
    elif damage == "finish": body["choices"][0]["finish_reason"] = None
    elif damage == "error": response["body"] = '{"error":{"message":"EXAMPLE rejected"}}'
    elif damage == "conflict": body["error"] = {"message": "EXAMPLE conflicting response"}
    elif damage == "limit": response["body_bytes"] = 1_000_000
    else: response = {"outcome": "error", "phase": "after_connect", "detail": "EXAMPLE timeout"}
    if damage in {"model", "usage", "finish", "conflict"}:
        response["body"] = json.dumps(body)
    calls = []

    def probe(*args):
        calls.append(args)
        return [response] if len(calls) == 1 else [completion()]

    with pytest.raises(DockerError, match="GENERATION_READINESS_NOT_READY"):
        readiness.run(tmp_path, readiness.profile(settings()), "http://EXAMPLE", "trusted", probe, secret_values=[])
    doc = json.loads((tmp_path / readiness.FILENAME).read_bytes())
    assert len(calls) == doc["attempts"] == 1
    assert doc["response"] and doc["code"] == "GENERATION_READINESS_NOT_READY"
    assert readiness.reference(tmp_path)["sha256"] == hashlib.sha256((tmp_path / readiness.FILENAME).read_bytes()).hexdigest()


@pytest.mark.parametrize("failure", [RuntimeError, TimeoutError])
def test_probe_exception_is_recorded_without_private_detail_or_retry(tmp_path, failure):
    calls = []

    def probe(*args):
        calls.append(args)
        raise failure("EXAMPLE-private-daemon-diagnostic")

    with pytest.raises(DockerError, match="GENERATION_READINESS_PROBE_ERROR"):
        readiness.run(tmp_path, readiness.profile(settings()), "http://EXAMPLE", "trusted", probe, secret_values=[])
    raw = (tmp_path / readiness.FILENAME).read_bytes()
    doc = json.loads(raw)
    assert doc["attempts"] == len(calls) == 1 and doc["error_type"] == failure.__name__
    assert b"EXAMPLE-private-daemon-diagnostic" not in raw


@pytest.mark.parametrize("response", [None, [], [{}], [None], [{}, {}]])
def test_invalid_probe_shape_cannot_claim_readiness(tmp_path, response):
    with pytest.raises(DockerError):
        readiness.run(tmp_path, readiness.profile(settings()), "http://EXAMPLE", "trusted",
                      lambda *args: response, secret_values=[])
    assert json.loads((tmp_path / readiness.FILENAME).read_bytes())["attempts"] == 1


def test_existing_readiness_record_is_never_overwritten_or_retried(tmp_path):
    path = tmp_path / readiness.FILENAME
    path.write_bytes(b"EXAMPLE retained failed preparation\r\n")
    with pytest.raises(FileExistsError):
        readiness.run(tmp_path, readiness.profile(settings()), "http://EXAMPLE", "trusted",
                      lambda *args: pytest.fail("old record must prevent sending"), secret_values=[])
    assert path.read_bytes() == b"EXAMPLE retained failed preparation\r\n"


def test_managed_cpu_accepts_explicit_option_without_changing_engine_launch(tmp_path):
    _, env, _, cfg = setup_inputs(tmp_path)
    legacy = application._managed_config(env, cfg)
    cfg["vllm"][readiness.SETTING] = 120
    prepared = application._managed_config(env, cfg)
    assert prepared[readiness.SETTING] == 120 and readiness.SETTING not in legacy
    assert sandbox._vllm_plan(prepared) == sandbox._vllm_plan(legacy)


def _generation_transport(monkeypatch, model, fail_side=None):
    original = sandbox._probe_in
    calls = []

    def probe(docker, network, requests, timeout, suffix):
        if requests[0]["id"] == "generation-readiness":
            calls.append((docker.side, network, deepcopy(requests), timeout))
            assert network.endswith("-engine")
            if docker.side == fail_side:
                return [{"outcome": "error", "phase": "after_connect", "detail": "EXAMPLE single timeout"}]
            return [completion(model, "EXAMPLE " + docker.side)]
        return original(docker, network, requests, timeout, suffix)

    monkeypatch.setattr(sandbox, "_probe_in", probe)
    return calls


def test_update_keeps_static_conditions_equal_and_does_not_replace_test_normal_control(tmp_path, monkeypatch):
    pack, envs, _, cfg = _inputs(tmp_path, ("hardened", "hardened"))
    cfg["vllm"][readiness.SETTING] = 120
    cfg["executors"] = {"direct_route": "real"}
    pol = policy(pack.ref(), scope={"include": ["REQ-01"]}, values={"REQ-01": {"allowed_callers": ["allowed"]}})
    run_dir = tmp_path / "run"
    _memory_runtime(monkeypatch, run_dir)
    calls = _generation_transport(monkeypatch, envs[0].model.id)

    class FailedNormal(FakeExecutor):
        def run(self, *args):
            draft = super().run(*args)
            draft.normal_control.succeeded = False
            return draft

    monkeypatch.setitem(registry.EXECUTORS, "direct_route:real", lambda raw: FailedNormal("pass"))
    store = Store(tmp_path / "store")
    report, failed = application.execute_update(store, pack, envs, pol, cfg, run_dir, T0)
    assert not failed and not report.official
    assert [call[0] for call in calls] == ["baseline", "candidate"]
    targets = [json.loads((run_dir / side / "target.json").read_text(encoding="utf-8")) for side in ("baseline", "candidate")]
    assert targets[0]["comparison_config"] == targets[1]["comparison_config"]
    static = targets[0]["comparison_config"]["generation_readiness"]
    assert static == readiness.profile(application._managed_config(envs[0], cfg))
    assert "elapsed_seconds" not in json.dumps(static) and "sha256" not in json.dumps(static)
    assert targets[0]["generation_readiness"]["sha256"] != targets[1]["generation_readiness"]["sha256"]
    evds = list(store.iter_kind("EVD"))
    assert len(evds) == 2 and all(not e.normal_control.succeeded for e in evds)
    assert all(r.verdicts.security.value == "UNKNOWN" for r in store.iter_kind("RES"))
    assert all(any(f"{side}/{readiness.FILENAME}" in line for line in report.limitations) for side in ("baseline", "candidate"))
    assert not store.verify()


@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_failed_generation_keeps_side_abort_and_original_record_without_retry(tmp_path, monkeypatch, side):
    pack, envs, pol, cfg = _inputs(tmp_path, ("hardened", "hardened"))
    cfg["vllm"][readiness.SETTING] = 120
    run_dir = tmp_path / "run"
    _memory_runtime(monkeypatch, run_dir)
    calls = _generation_transport(monkeypatch, envs[0].model.id, fail_side=side)
    store = Store(tmp_path / "store")
    report, failed = application.execute_update(store, pack, envs, pol, cfg, run_dir, T0)
    assert failed and [call[0] for call in calls] == ["baseline", "candidate"]
    evidence = next(e for e in store.iter_kind("EVD") if e.side.value == side)
    result = next(r for r in store.iter_kind("RES") if r.side.value == side)
    assert evidence.execution.status.value == "ABORTED" and result.verdicts.security.value == "UNKNOWN"
    assert not (run_dir / side / "target.json").exists()
    raw = (run_dir / side / readiness.FILENAME).read_bytes()
    doc = json.loads(raw)
    assert doc["attempts"] == 1 and doc["code"] == "GENERATION_READINESS_NOT_READY"
    assert any(f"{side}/{readiness.FILENAME}" in line and hashlib.sha256(raw).hexdigest() in line for line in report.limitations)
    assert not store.verify()
