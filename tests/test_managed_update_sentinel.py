"""Managed lifecycle contracts through real sandbox setup and a memory-only Docker.

All observations are EXAMPLE. No process, daemon, socket, model or engine is run.
"""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess

import pytest

from guard import application
from guard.executors import registry
from guard.executors.fake import FakeExecutor
from guard.runtime import sandbox
from guard.runtime.docker import DockerError
from guard.store import Store
from tests.builders import T0, policy
from tests.test_windows_cpu_launch import setup_inputs


def _inputs(tmp_path, profiles):
    pack, baseline, _, cfg = setup_inputs(tmp_path)
    baseline = baseline.model_copy(update={"id": "ENV-SENTINEL-BASELINE"}).seal()
    candidate = baseline.model_copy(update={
        "id": "ENV-SENTINEL-CANDIDATE",
        "engine": baseline.engine.model_copy(update={
            "version": "0.32.0", "image_digest": "cpu@sha256:" + "b" * 64}),
    }).seal()
    cfg = deepcopy(cfg)
    cfg.pop("validation")
    cfg["executors"] = {"container_config": "real"}
    cfg["update"] = {"mode": "managed", "topology": "isolated"}
    cfg["sides"] = {
        side: {"update": {"engine_profile": profile}}
        for side, profile in zip(("baseline", "candidate"), profiles)
    }
    pol = policy(pack.ref(), scope={"include": ["PLT-01"]}, values={"PLT-01": {
        "allowed_capabilities": [], "allowed_mounts": ["/models:ro"], "allowed_devices": [],
        "require_non_root": True, "allowed_exec_writable_paths": ["/guard-cache"]}})
    return pack, [baseline, candidate], pol, cfg


def _memory_runtime(monkeypatch, run_dir, *, failure=None, conflict=False):
    """Replace only transport/process boundaries, retaining up/_up/_start_engine."""
    events = []
    launches = []
    instances = []

    def prohibited(*args, **kwargs):
        raise AssertionError("Synthetic test must not execute processes or network")

    monkeypatch.setattr(subprocess, "run", prohibited)
    monkeypatch.setattr(subprocess, "Popen", prohibited)
    monkeypatch.setattr(socket, "create_connection", prohibited)

    class MemoryDocker:
        def __init__(self, run_id):
            self.run_id = run_id
            self.side = ("baseline", "candidate")[len(instances)]
            self.image = None
            self.version = None
            instances.append(self)

        def __enter__(self):
            events.append((self.side, "enter"))
            if conflict and self.side == "baseline":
                folder = run_dir / "baseline-sentinel"
                folder.mkdir()
                (folder / "existing.txt").write_bytes(b"EXAMPLE previous bytes")
            return self

        def __exit__(self, *exc_info):
            events.append((self.side, "cleanup"))
            if self.side == "baseline" and failure in {"cleanup", "startup_cleanup"}:
                if failure == "cleanup":
                    raise DockerError("private cleanup details")
                exc_info[1].add_note(
                    f"cleanup incomplete for guard.run={self.run_id}; inspect labelled containers/networks")
            return False

        def image_digest(self, image, **kwargs):
            return image if "@" in image else image + "@sha256:" + "c" * 64

        def network_create(self, name, **kwargs):
            return f"EXAMPLE-{self.run_id}-{name}"

        def run_detached(self, image, role, **kwargs):
            if role == "engine":
                self.image = image
                self.version = "0.31.0" if self.side == "baseline" else "0.32.0"
                mounts = kwargs.get("mounts", [])
                sentinels = [m for m in mounts if m.target == sandbox.SENTINEL_TARGET]
                for mount in sentinels:
                    assert mount.read_only is False and mount.source.is_dir()
                    assert not any(mount.source.iterdir())
                    # Simulate a probe's retained artifact. It must neither be
                    # deleted nor become a candidate-side input.
                    (mount.source / "synthetic-write.txt").write_text(self.side, encoding="utf-8")
                assert all(m.read_only for m in mounts if m.target != sandbox.SENTINEL_TARGET)
                launches.append((self.side, kwargs))
                events.append((self.side, "engine"))
                if self.side == "baseline" and failure in {"startup", "startup_cleanup"}:
                    raise DockerError("private startup details")
            return f"EXAMPLE-{self.run_id}-{role}"

        def ip(self, *args, **kwargs):
            return "192.0.2.10"

        def published_port(self, *args, **kwargs):
            return 1

        def is_running(self, *args, **kwargs):
            return True

    def probes(docker, network, requests, timeout, suffix):
        return [{"outcome": 200, **(
            {"body": json.dumps({"version": docker.version})} if req["id"] == "version" else {})}
                for req in requests]

    def capture(docker, folder, **kwargs):
        events.append((docker.side, "diagnostics"))
        return {"error_type": "EXAMPLE-only-no-log-collection"}

    def executor(raw):
        target = json.loads(Path(raw["container_config"]["target"]).read_text(encoding="utf-8"))
        scenario = {"default": "default_caps", "hardened": "pass",
                    "overprivileged": "overprivileged"}[target["engine_profile"]]
        return FakeExecutor(scenario)

    monkeypatch.setattr(application, "Docker", MemoryDocker)
    monkeypatch.setattr(sandbox, "probe", lambda *args, **kwargs: {"outcome": 401})
    monkeypatch.setattr(sandbox, "_probe_in", probes)
    monkeypatch.setattr(sandbox, "capture_diagnostics", capture)
    monkeypatch.setitem(registry.EXECUTORS, "container_config:real", executor)
    return events, launches


@pytest.mark.parametrize("profiles", [
    ("overprivileged", "overprivileged"),
    ("default", "hardened"),
    ("overprivileged", "hardened"),
])
def test_managed_update_connects_real_sentinel_guard_and_isolates_side_writes(tmp_path, monkeypatch, profiles):
    pack, envs, pol, cfg = _inputs(tmp_path, profiles)
    run_dir = tmp_path / "run"
    events, launches = _memory_runtime(monkeypatch, run_dir)
    store = Store(tmp_path / "store")

    report, failed = application.execute_update(store, pack, envs, pol, cfg, run_dir, T0)

    assert failed is False and report.official is False
    assert [side for side, _ in launches] == ["baseline", "candidate"]
    for (side, options), profile in zip(launches, profiles):
        mounts = [m for m in options["mounts"] if m.target == sandbox.SENTINEL_TARGET]
        if profile == "overprivileged":
            assert len(mounts) == 1
            assert mounts[0].source == (run_dir / f"{side}-sentinel").resolve()
            assert (mounts[0].source / "synthetic-write.txt").read_text(encoding="utf-8") == side
            assert options["cap_add"] == ["SYS_ADMIN"]
        else:
            assert not mounts and not (run_dir / f"{side}-sentinel").exists()
            if profile == "hardened":
                assert options["read_only"] is True and options["cap_drop"] == ["ALL"]
        target = json.loads((run_dir / side / "target.json").read_text(encoding="utf-8"))
        assert target["engine_profile"] == profile
        assert target["engine"]["container"] == f"EXAMPLE-{target['run_id']}-engine"
        assert all(target["run_id"] in network for network in options["networks"])
    assert len({json.loads((run_dir / side / "target.json").read_text(encoding="utf-8"))["run_id"]
                for side in ("baseline", "candidate")}) == 2
    evidence = list(store.iter_kind("EVD"))
    assert len(evidence) == 2
    assert all(e.data_origin.value == "EXAMPLE" and e.execution.status.value == "COMPLETED" for e in evidence)
    row = next(row for row in report.by_check if row.check_id == "PLT-01")
    assert row.baseline.security.value == ("PASS" if profiles[0] == "hardened" else "FAIL")
    assert row.candidate.security.value == ("PASS" if profiles[1] == "hardened" else "FAIL")
    assert [event for side, event in events if side == "baseline"][-2:] == ["diagnostics", "cleanup"]
    assert [event for side, event in events if side == "candidate"][-2:] == ["diagnostics", "cleanup"]
    assert not store.verify()


@pytest.mark.parametrize("failure", ["startup", "cleanup", "startup_cleanup"])
def test_managed_update_sentinel_failures_preserve_completed_violations_and_cleanup(tmp_path, monkeypatch, failure):
    pack, envs, pol, cfg = _inputs(tmp_path, ("overprivileged", "overprivileged"))
    run_dir = tmp_path / "run"
    events, launches = _memory_runtime(monkeypatch, run_dir, failure=failure)
    store = Store(tmp_path / "store")

    report, failed = application.execute_update(store, pack, envs, pol, cfg, run_dir, T0)

    assert failed is True
    assert [side for side, _ in launches] == ["baseline", "candidate"]
    evds = {e.side.value: e for e in store.iter_kind("EVD")}
    results = {r.side.value: r for r in store.iter_kind("RES")}
    assert evds["candidate"].execution.status.value == "COMPLETED"
    assert results["candidate"].verdicts.security.value == "FAIL"
    if failure == "cleanup":
        assert evds["baseline"].execution.status.value == "COMPLETED"
        assert results["baseline"].verdicts.security.value == "FAIL"
    else:
        assert evds["baseline"].execution.status.value == "ABORTED"
        assert results["baseline"].verdicts.security.value == "UNKNOWN"
    for side in ("baseline", "candidate"):
        assert (side, "cleanup") in events
        assert (run_dir / f"{side}-sentinel" / "synthetic-write.txt").exists()
    if failure in {"cleanup", "startup_cleanup"}:
        assert any("cleanup incomplete" in note for note in report.limitations)
    assert "private" not in json.dumps(report.to_dict())
    assert not store.verify()


def test_existing_sentinel_directory_is_preserved_and_that_side_aborts(tmp_path, monkeypatch):
    pack, envs, pol, cfg = _inputs(tmp_path, ("overprivileged", "overprivileged"))
    run_dir = tmp_path / "run"
    events, launches = _memory_runtime(monkeypatch, run_dir, conflict=True)
    store = Store(tmp_path / "store")

    report, failed = application.execute_update(store, pack, envs, pol, cfg, run_dir, T0)

    assert failed is True
    assert (run_dir / "baseline-sentinel" / "existing.txt").read_bytes() == b"EXAMPLE previous bytes"
    assert [side for side, _ in launches] == ["candidate"]
    evds = {e.side.value: e for e in store.iter_kind("EVD")}
    assert evds["baseline"].execution.status.value == "ABORTED"
    assert evds["candidate"].execution.status.value == "COMPLETED"
    assert ("baseline", "cleanup") in events and ("candidate", "cleanup") in events
    assert not report.official and not store.verify()


def test_existing_run_directory_is_never_reused(tmp_path, monkeypatch):
    pack, envs, pol, cfg = _inputs(tmp_path, ("overprivileged", "overprivileged"))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "previous.txt").write_bytes(b"EXAMPLE stored artifact")
    _memory_runtime(monkeypatch, run_dir)
    store = Store(tmp_path / "store")

    with pytest.raises(FileExistsError):
        application.execute_update(store, pack, envs, pol, cfg, run_dir, T0)

    assert (run_dir / "previous.txt").read_bytes() == b"EXAMPLE stored artifact"
    assert not list(store.iter_kind("EVD"))


def test_single_validation_retains_its_existing_sentinel_path(tmp_path, monkeypatch):
    pack, envs, pol, cfg = _inputs(tmp_path, ("overprivileged", "overprivileged"))
    cfg.pop("sides")
    cfg.pop("update")
    cfg["validation"] = {"mode": "managed", "engine_profile": "overprivileged"}
    run_dir = tmp_path / "run"
    _, launches = _memory_runtime(monkeypatch, run_dir)
    store = Store(tmp_path / "store")

    report, failed = application.execute_validation(store, pack, envs[0], pol, cfg, run_dir, T0)

    assert failed is False and not report.official
    _, options = launches[0]
    mount, = [m for m in options["mounts"] if m.target == sandbox.SENTINEL_TARGET]
    assert mount.source == (run_dir / "sentinel").resolve()
    evd, = list(store.iter_kind("EVD"))
    result, = list(store.iter_kind("RES"))
    assert evd.execution.status.value == "COMPLETED" and evd.data_origin.value == "EXAMPLE"
    assert result.verdicts.security.value == "FAIL"
    assert not store.verify()
