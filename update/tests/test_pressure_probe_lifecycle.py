"""EXAMPLE argv/probe transports; no Docker daemon or engine is used."""
import json
import time
from types import SimpleNamespace

import pytest

from guard.executors.http_common import docker_stream_probe
from guard.executors import stream_probe
from guard.runtime.docker import CmdResult, Docker, DockerError


@pytest.mark.parametrize("pull_never", [False, True])
def test_run_once_can_forbid_implicit_pull_without_changing_legacy_argv(pull_never):
    calls = []
    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return CmdResult(0, "EXAMPLE", "")
    client = Docker("pressure-example", runner=runner)
    client.run_once("example.invalid/probe@sha256:" + "a" * 64, "probe", network="EXAMPLE-network",
                    timeout=1, command=["python", "-V"], pull_never=pull_never)
    argv, kwargs = calls[0]
    assert ("--pull=never" in argv) is pull_never
    assert argv[:4] == ["docker", "run", "--rm", "-i"]
    assert argv[-2:] == ["python", "-V"]
    assert kwargs["timeout"] == 1
    assert not any(arg == "pull" for arg in argv)


def test_pressure_stream_transport_passes_pull_never_and_keeps_secrets_in_stdin():
    calls = []
    class World:
        def run_once(self, *args, **kwargs):
            calls.append((args, kwargs))
            return CmdResult(0, json.dumps([{"id": "normal", "outcome": 200}]), "PRIVATE_STDERR")
    requests = [{"id": "normal", "url": "http://EXAMPLE-engine:8000/v1/completions", "mode": "json",
                 "headers": {"Authorization": "Bearer PRIVATE_AUTH"}, "body": {"model": "EXAMPLE-model"}}]
    result = docker_stream_probe(World(), "EXAMPLE-local-probe", pull_never=True)("EXAMPLE-network", requests, 1)
    assert result == [{"id": "normal", "outcome": 200}]
    args, kwargs = calls[0]
    assert kwargs["pull_never"] is True
    assert "PRIVATE_AUTH" not in str(args) + str(kwargs["command"])
    assert "PRIVATE_AUTH" in kwargs["input"].decode()
    assert kwargs["mounts"][0].read_only is True


def test_default_stream_transport_does_not_add_a_new_kwarg_to_legacy_runners():
    calls = []
    class World:
        def run_once(self, *args, **kwargs):
            calls.append(kwargs)
            return CmdResult(0, '[{"id":"normal"}]', "")
    docker_stream_probe(World(), "EXAMPLE-probe")("EXAMPLE-network",
        [{"id": "normal", "url": "http://EXAMPLE-engine:8000/"}], 1)
    assert "pull_never" not in calls[0]


def _guard():
    return {"basis": "linux-monotonic-time-namespace-v1", "time_namespace_inode": 777,
            "deadline_monotonic_ns": 105_000_000_000}


def test_pressure_transport_caps_docker_wait_and_blocks_http_after_fake_startup_delay(monkeypatch):
    calls, source_time = [], {"ns": 100_000_000_000}
    monkeypatch.setattr(stream_probe.time, "monotonic_ns", lambda: source_time["ns"])
    monkeypatch.setattr(stream_probe, "_clock", lambda base: {
        "basis": _guard()["basis"], "time_namespace_inode": 777,
        "batch_started_monotonic_ns": round(base * 1_000_000_000)})
    monkeypatch.setattr(stream_probe.socket, "create_connection", lambda *a, **kw: pytest.fail("late startup sent HTTP"))
    class World:
        def run_once(self, *args, **kwargs):
            calls.append(kwargs)
            spec = json.loads(kwargs["input"])
            assert spec["deadline_guard"] == _guard()
            # Docker startup advances only the source clock. The parent clock
            # may use an unrelated host/VM epoch and is not compared with it.
            source_time["ns"] = 110_000_000_000
            values = stream_probe.collect(spec["requests"], spec["timeout"], deadline_guard=spec["deadline_guard"])
            return CmdResult(0, json.dumps(values), "PRIVATE_DAEMON_DIAGNOSTIC")
    requests = [{"id": "normal", "url": "http://EXAMPLE-engine:8000/",
                 "headers": {"Authorization": "Bearer PRIVATE_AUTH"}}]
    result, = docker_stream_probe(World(), "EXAMPLE-probe", pull_never=True,
        parent_deadline=time.monotonic() + 5, deadline_guard=_guard())("EXAMPLE-network", requests, 1)
    assert 0 < calls[0]["timeout"] <= 5  # No legacy +60-second setup allowance.
    assert json.loads(calls[0]["input"])["timeout"] <= 1
    assert result["stop_reason"] == "source_deadline" and result["deadline_guard_verified"] is False
    assert "request_sent_ms" not in result and "PRIVATE_" not in json.dumps(result)


def test_pressure_parent_deadline_expiry_prevents_docker_creation(monkeypatch):
    import guard.executors.http_common as module
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 10.0))
    class World:
        def run_once(self, *args, **kwargs):
            pytest.fail("expired parent budget created a probe")
    probe = docker_stream_probe(World(), "EXAMPLE-probe", parent_deadline=9.0, deadline_guard=_guard())
    with pytest.raises(DockerError, match="parent deadline expired"):
        probe("EXAMPLE-network", [{"id": "normal", "url": "http://EXAMPLE-engine:8000/"}], 1)


@pytest.mark.parametrize("options", [
    {"parent_deadline": 1.0}, {"deadline_guard": _guard()},
    {"parent_deadline": True, "deadline_guard": _guard()},
    {"parent_deadline": float("nan"), "deadline_guard": _guard()},
    {"parent_deadline": 1.0, "deadline_guard": {**_guard(), "unknown": "PRIVATE_GUARD_VALUE"}},
])
def test_partial_or_invalid_pressure_deadline_options_are_rejected(options):
    with pytest.raises(ValueError):
        docker_stream_probe(object(), "EXAMPLE-probe", **options)
