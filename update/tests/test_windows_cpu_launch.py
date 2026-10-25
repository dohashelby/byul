"""CPU launch/configuration contracts. No Docker daemon or model is executed."""
from copy import deepcopy
import json
from pathlib import Path
import tomllib

import pytest
from typer.testing import CliRunner

from guard import application
from guard.adapters.base import LaunchSpec
from guard.adapters.model_inventory import inventory
from guard.adapters.vllm import CpuLaunchOptions, VllmAdapter
from guard.cli import app
from guard.executors import registry
from guard.executors.fake import FakeExecutor
from guard.models import utcnow
from guard.runtime.docker import CmdResult, ContainerLimits, Docker, DockerError
from guard.runtime import sandbox
from guard.store import Store
from tests.builders import baseline_pack, environment, policy


def launch(**options):
    return LaunchSpec(model="facebook/opt-125m", port=8000, image="cpu@sha256:" + "a" * 64,
                      options={"revision": "rev", "models_dir": Path("C:/models/snapshot"),
                               "device": "cpu", "launch_profile": "cpu-bounded", "model_layout": "snapshot", **options})


def test_bounded_cpu_uses_local_readonly_snapshot_and_clears_image_kv_default():
    plan = VllmAdapter().launch_plan(launch())
    assert plan.entrypoint == "/usr/bin/env"
    assert plan.argv[:5] == ["-u", "VLLM_CPU_KVCACHE_SPACE", "vllm", "serve", "/models"]
    assert plan.argv[plan.argv.index("--served-model-name") + 1] == "facebook/opt-125m"
    assert plan.argv[plan.argv.index("--kv-cache-memory-bytes") + 1] == "67108864"
    assert plan.argv[plan.argv.index("--load-format") + 1] == "auto"
    assert "--enforce-eager" in plan.argv and "--disable-log-requests" not in plan.argv
    assert "VLLM_CPU_KVCACHE_SPACE" not in plan.env
    assert plan.env["HF_HUB_OFFLINE"] == plan.env["TRANSFORMERS_OFFLINE"] == "1"
    assert plan.env["OMP_NUM_THREADS"] == "2" and plan.env["VLLM_CPU_OMP_THREADS_BIND"] == "nobind"
    assert plan.mounts[0].read_only and plan.mounts[0].target == "/models"
    assert not plan.gpus and plan.shm_size is None
    assert plan.limits.to_dict() == {"cpus": 2, "memory_bytes": 3221225472, "memory_swap_bytes": 5368709120,
                                   "shm_bytes": 536870912, "pids_limit": 256}


@pytest.mark.parametrize("options", [
    {"max_model_len": True}, {"max_num_seqs": 0}, {"max_num_batched_tokens": 127},
    {"kv_cache_memory_bytes": "64m"}, {"enforce_eager": "true"}, {"cpu_threads": 3},
    {"dtype": "auto"}, {"dtype": []}, {"device": "gpu"}, {"cpu_kv_cache_gib": 1},
    {"arbitrary_argv": ["--trust-remote-code"]}, {"resources": {"privileged": True}},
    {"resources": {"cpus": True}}, {"resources": {"memory_bytes": 1}},
    {"resources": {"shm_bytes": 4000000000}}, {"resources": {"pids_limit": -1}},
])
def test_cpu_profile_rejects_invalid_or_unbounded_options(options):
    with pytest.raises(ValueError):
        VllmAdapter().launch_plan(launch(**options))


def test_legacy_launch_and_gib_cache_remain_unchanged():
    spec = LaunchSpec(model="model", port=8000, image="img", options={"revision": "rev", "models_dir": Path("/cache"),
                                                                    "device": "cpu", "cpu_kv_cache_gib": 2})
    plan = VllmAdapter().launch_plan(spec)
    assert plan.argv[:4] == ["--model", "model", "--revision", "rev"]
    assert plan.env["VLLM_CPU_KVCACHE_SPACE"] == "2"
    assert plan.entrypoint is None and plan.limits is None and plan.shm_size == "1g"


@pytest.mark.parametrize("values", [{"cpus": float("nan")}, {"memory_bytes": True},
    {"memory_swap_bytes": 1024}, {"memory_bytes": 2048, "memory_swap_bytes": 1024},
    {"memory_bytes": 2048, "shm_bytes": 4096}, {"pids_limit": 0}, {"cpuset": "all"}])
def test_container_limits_are_strict_before_any_docker_invocation(values):
    with pytest.raises(ValueError):
        ContainerLimits.from_mapping(values)


def test_resource_bounds_entrypoint_and_log_reader_preserve_arguments_and_failure():
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        return CmdResult(7 if argv[1] == "logs" else 0, "", "reader unavailable" if argv[1] == "logs" else "")
    docker = Docker("cpu-contract", runner=runner)
    docker.run_detached("cpu", "engine", networks=["net"], publish=[8000], command=["-u", "OLD", "vllm", "serve", "/models"],
                        entrypoint="/usr/bin/env", limits=ContainerLimits(cpus=2, memory_bytes=3221225472,
                            memory_swap_bytes=5368709120, shm_bytes=536870912, pids_limit=256), timeout=5)
    args = calls[0]
    for flag, value in (("--cpus", "2"), ("--memory", "3221225472"), ("--memory-swap", "5368709120"),
                        ("--shm-size", "536870912"), ("--pids-limit", "256"), ("--entrypoint", "/usr/bin/env")):
        assert args[args.index(flag) + 1] == value
    assert args[args.index("-p") + 1] == "127.0.0.1::8000" and "--gpus" not in args
    logs = docker.logs_window("engine", since="2026-10-23T00:00:00Z", until="2026-10-23T00:00:01Z", timeout=5)
    assert logs.returncode == 7 and logs.stdout == "" and logs.stderr == "reader unavailable"
    assert calls[-1] == ["docker", "logs", "--timestamps", "--since", "2026-10-23T00:00:00Z", "--until", "2026-10-23T00:00:01Z", "engine"]
    with pytest.raises(ValueError):
        docker.run_detached("cpu", "engine", networks=[], limits=ContainerLimits(shm_bytes=1024), shm_size="1g", timeout=5)


def setup_inputs(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "config.json").write_text('{"vocab_size":50272}', encoding="utf-8")
    # Deliberately a synthetic file for inventory tests; never passed to an engine.
    (snapshot / "pytorch_model.bin").write_bytes(b"synthetic inventory fixture")
    cpu = {"architecture": "x86_64", "model": "test CPU", "system": "Linux", "kernel": "test", "threads": 2}
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": "cpu@sha256:" + "a" * 64},
                      runtime={"gpu_count": 0, "cpu": cpu},
                      model={"id": "facebook/opt-125m", "revision": "rev", "inventory_sha256": inventory(snapshot)["inventory_sha256"]})
    cfg = {"adapter": {"name": "vllm"}, "executors": {"input_validation": "real"},
           "validation": {"mode": "managed", "gateway_profile": "single"},
           "vllm": {"models_dir": str(snapshot), "model_layout": "snapshot", "launch_profile": "cpu-bounded"},
           "input_validation": {"vocab_size": 50272, "vocab_source": "synthetic unit fixture"}}
    pack = baseline_pack()
    pol = policy(pack.ref(), scope={"include": ["REQ-09"]}, values={"REQ-09": {
        "max_input_tokens": 127, "rejection_codes": ["400", "422"], "allowed_endpoints": ["POST /v1/completions"],
        "allowed_input_formats": ["token_ids"]}})
    return pack, env, pol, cfg


def test_managed_snapshot_pins_inventory_and_rejects_thread_or_inventory_mismatch(tmp_path):
    _, env, _, cfg = setup_inputs(tmp_path)
    managed = application._managed_config(env, cfg)
    assert Path(managed["models_dir"]).is_absolute() and managed["image"] == env.engine.image_digest
    assert sandbox._vllm_plan(managed).argv[4] == "/models"
    bad = deepcopy(cfg)
    bad["vllm"]["cpu_threads"] = 1
    with pytest.raises(ValueError, match="matching cpu_threads"):
        application._managed_config(env, bad)
    (Path(cfg["vllm"]["models_dir"]) / "config.json").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="inventory differs"):
        application._managed_config(env, cfg)


@pytest.mark.parametrize("memory_bytes, entrypoint, ready", [(8 << 30, ["vllm", "serve"], True),
    (2 << 30, ["vllm", "serve"], False), (8 << 30, ["bash"], False)])
def test_single_readiness_checks_capacity_without_starting_or_pulling(tmp_path, monkeypatch, memory_bytes, entrypoint, ready):
    pack, env, pol, cfg = setup_inputs(tmp_path)
    class Local:
        def __init__(self, *args): pass
        def daemon_info(self, **kwargs): return {"os": "linux", "architecture": "amd64"}
        def daemon_resources(self, **kwargs): return {"memory_bytes": memory_bytes, "cpus": 4}
        def image_info(self, image, **kwargs):
            return {"os": "linux", "architecture": "amd64", "digests": [image if "@" in image else image + "@sha256:" + "c" * 64],
                    "entrypoint": entrypoint}
    monkeypatch.setattr(application, "Docker", Local)
    data = application.validation_readiness(pack, env, pol, cfg)
    assert data["ready"] is ready, data
    assert not any(row["check"] == "image-pair" for row in data["checks"])
    assert not (tmp_path / "store").exists() and not (tmp_path / "target.json").exists()


@pytest.mark.parametrize("stage", ["success", "startup", "cleanup", "version", "startup_cleanup"])
def test_single_managed_validation_retains_completed_violations_on_cleanup_failure(tmp_path, monkeypatch, stage):
    pack, env, _, cfg = setup_inputs(tmp_path)
    cfg["executors"] = {"direct_route": "real"}
    cfg["direct_route"] = {"roles": {"allowed": "TEST_GENERAL"}}
    pol = policy(pack.ref(), scope={"include": ["REQ-01"]}, values={"REQ-01": {"allowed_callers": ["allowed"]}})
    events = []
    class Isolated:
        def __init__(self, run_id):
            self.run_id = run_id
            events.append(("create", run_id))
        def __enter__(self): return self
        def __exit__(self, *args):
            events.append(("cleanup", None))
            if stage == "cleanup": raise DockerError("private credential text")
            if stage == "startup_cleanup":
                args[1].add_note(f"cleanup incomplete for guard.run={self.run_id}; inspect labelled containers/networks")
    def up(docker, topology, folder, *args, **kwargs):
        if stage in {"startup", "startup_cleanup"}: raise DockerError("private credential text")
        folder.mkdir()
        (folder / "target.json").write_text("{}", encoding="utf-8")
        return {"observed_version": "wrong" if stage == "version" else env.engine.version,
                "images": {env.engine.image_digest: env.engine.image_digest}}
    monkeypatch.setattr(application, "Docker", Isolated)
    monkeypatch.setattr(application.sandbox, "up", up)
    # Explicit injected test double verifies orchestration only, never engine validation.
    monkeypatch.setitem(registry.EXECUTORS, "direct_route:real", lambda raw: FakeExecutor("direct_200"))
    st = Store(tmp_path / "store")
    rpt, failed = application.execute_validation(st, pack, env, pol, cfg, tmp_path / "run", utcnow())
    assert len(events) == 2 and events[1][0] == "cleanup"
    evd, = list(st.iter_kind("EVD"))
    res, = list(st.iter_kind("RES"))
    if stage in {"startup", "version", "startup_cleanup"}:
        assert evd.execution.status.value == "ABORTED" and res.verdicts.security.value == "UNKNOWN"
    else:
        assert evd.execution.status.value == "COMPLETED" and res.verdicts.security.value == "FAIL"
    assert failed is (stage != "success")
    assert "private credential text" not in json.dumps(rpt.to_dict())
    if stage in {"cleanup", "startup_cleanup"}:
        assert any("cleanup incomplete" in reason for reason in rpt.limitations)
        assert any("guard.run=" in reason for reason in rpt.limitations)
    assert not st.verify()


def test_windows_example_uses_literal_paths_and_separates_policy_from_launch():
    cfg = tomllib.loads((Path(__file__).resolve().parents[1] / "examples/windows-cpu.toml").read_text(encoding="utf-8"))
    assert cfg["vllm"]["models_dir"] == "C:\\guard-models\\opt-125m-snapshot"
    assert cfg["vllm"]["resources"]["shm_bytes"] == 536870912
    assert "max_input_tokens" not in cfg["vllm"] and set(cfg["executors"].values()) == {"real"}


@pytest.mark.parametrize("enabled", [False, True])
def test_credential_canary_is_fixed_opt_in_and_absent_when_disabled(enabled):
    calls = []
    class Recording:
        def run_detached(self, image, suffix, **kwargs):
            calls.append(kwargs)
            return "recording-engine"
    sandbox._start_engine(Recording(), "vllm", ["net"], [],
        {"model": "test", "revision": "rev", "image": "cpu", "models_dir": "/snapshot", "gpu": False,
         "launch_profile": "cpu-bounded", "model_layout": "snapshot"}, credential_canary=enabled)
    values = calls[0]["public_env"]
    if enabled:
        assert values["GUARD_TEST_CREDENTIAL"] == "EXAMPLE_NOT_A_SECRET"
    else:
        assert "GUARD_TEST_CREDENTIAL" not in values


def test_canary_rejects_non_boolean_and_confirmed_policy_before_engine_creation(tmp_path):
    pack, env, pol, cfg = setup_inputs(tmp_path)
    cfg["validation"]["credential_canary"] = "true"
    with pytest.raises(ValueError, match="boolean"):
        application._validation_inputs(env, cfg)
    cfg["validation"]["credential_canary"] = True
    from guard.models.enums import PolicyStatus
    confirmed = pol.model_copy(update={"status": PolicyStatus.CONFIRMED}).seal()
    with pytest.raises(ValueError, match="DRAFT"):
        application.execute_validation(Store(tmp_path / "store"), pack, env, confirmed, cfg, tmp_path / "run", utcnow())
    assert not (tmp_path / "run").exists()


def test_bounded_cpu_launch_conditions_do_not_depend_on_host_snapshot_path():
    first = VllmAdapter().launch_plan(launch(models_dir=Path("C:/first")))
    second = VllmAdapter().launch_plan(launch(models_dir=Path("D:/second")))
    assert first.argv == second.argv and first.env == second.env and first.limits == second.limits


def test_prepare_validation_missing_input_creates_no_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["prepare-validation", "--pack", "absent", "--env", "absent", "--policy", "absent"])
    assert result.exit_code == 1 and json.loads(result.output)["ready"] is False
    assert not (tmp_path / ".guard").exists()


def test_requested_image_digest_wins_over_other_repository_digest():
    requested = "official/cpu@sha256:" + "b" * 64
    other = "mirror/cpu@sha256:" + "a" * 64
    def runner(argv, **kwargs):
        return CmdResult(0, json.dumps([other, requested]), "")
    docker = Docker("digest-contract", runner=runner)
    assert docker.image_digest(requested, timeout=5) == requested
    assert docker.image_digest("tag:latest", timeout=5) == other


def test_update_retains_startup_and_cleanup_failures_for_both_sides(tmp_path, monkeypatch):
    pack, baseline, _, cfg = setup_inputs(tmp_path)
    candidate = baseline.model_copy(update={"id": "ENV-CPU-CANDIDATE", "engine": baseline.engine.model_copy(update={
        "image_digest": "cpu@sha256:" + "b" * 64})}).seal()
    cfg["executors"] = {"direct_route": "real"}
    cfg["direct_route"] = {"roles": {"allowed": "TEST_GENERAL"}}
    cfg["update"] = {"mode": "managed", "gateway_profile": "single"}
    pol = policy(pack.ref(), scope={"include": ["REQ-01"]}, values={"REQ-01": {"allowed_callers": ["allowed"]}})
    class FailsBoth:
        def __init__(self, run_id): self.run_id = run_id
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb):
            exc.add_note(f"cleanup incomplete for guard.run={self.run_id}; inspect labelled containers/networks")
    def fails(*args, **kwargs):
        raise DockerError("sensitive daemon output")
    monkeypatch.setattr(application, "Docker", FailsBoth)
    monkeypatch.setattr(application.sandbox, "up", fails)
    st = Store(tmp_path / "store")
    rpt, failed = application.execute_update(st, pack, [baseline, candidate], pol, cfg, tmp_path / "run", utcnow())
    assert failed
    assert len(list(st.iter_kind("EVD"))) == 2
    assert all(e.execution.status.value == "ABORTED" for e in st.iter_kind("EVD"))
    assert len([s for s in rpt.limitations if "cleanup incomplete" in s and "guard.run=" in s]) == 2
    assert "sensitive daemon output" not in json.dumps(rpt.to_dict())


def test_cleanup_notes_are_bound_to_local_run_id_and_never_echoed():
    exc = RuntimeError("private text")
    exc.add_note("cleanup incomplete for guard.run=other-id; inspect labelled containers/networks")
    exc.add_note("raw daemon secret")
    assert not application._has_cleanup_note(exc, "local-id")


@pytest.mark.parametrize("engine, bounded, profile, triton", [
    ("vllm", True, "hardened", "/guard-cache/triton"),
    ("vllm", True, "default", None),
    ("vllm", True, "tmp_exec", None),
    ("vllm", False, "hardened", None),
    ("stub", False, "hardened", None),
])
def test_triton_cache_fix_is_limited_to_hardened_bounded_cpu_without_relaxing_tmp(engine, bounded, profile, triton):
    before = deepcopy(sandbox.ENGINE_PROFILES)
    cfg = {"launch_profile": "cpu-bounded"} if bounded else {}
    prof = sandbox._engine_profile(engine, cfg, profile)
    assert prof.get("env", {}).get("TRITON_CACHE_DIR") == triton
    if triton:
        assert prof["env"]["TMPDIR"] == "/guard-cache"
        # tempfile needs an existing writable executable mount, not a missing child.
        assert any(mount.split(":", 1)[0] == prof["env"]["TMPDIR"] and ",exec," in mount for mount in prof["tmpfs"])
    else:
        assert "TMPDIR" not in prof.get("env", {})
    if profile == "hardened":
        assert prof["user"] == "1000:1000" and prof["read_only"] is True
        assert prof["cap_drop"] == ["ALL"] and prof["security_opt"] == ["no-new-privileges:true"]
        assert "/tmp:rw,noexec,nosuid,size=2g,mode=1777" in prof["tmpfs"]
        assert "/guard-cache:rw,exec,nosuid,size=2g,mode=1777" in prof["tmpfs"]
    assert sandbox.ENGINE_PROFILES == before  # Later legacy launches must not inherit the CPU-only change.


def test_cpu_target_hash_conditions_record_effective_cache_environment_and_permissions(tmp_path, monkeypatch):
    captured = []
    class Recording:
        run_id = "random-local-run"
        def network_create(self, name, **kwargs): return "random-" + name
        def run_detached(self, image, suffix, **kwargs):
            if suffix == "engine": captured.append(kwargs)
            return "random-" + suffix
        def ip(self, name, net, **kwargs): return "172.20.0.2"
        def published_port(self, *args, **kwargs): return 32001
        def image_digest(self, image, **kwargs): return image
    monkeypatch.setattr(sandbox, "pull_images", lambda docker: {sandbox.NGINX_IMAGE: "nginx@sha256:fixed",
                                                               sandbox.PYTHON_IMAGE: "python@sha256:fixed"})
    monkeypatch.setattr(sandbox, "probe", lambda *args, **kwargs: {"outcome": 401})
    cfg = {"model": "test", "revision": "rev", "image": "cpu@sha256:fixed", "models_dir": "C:/private/model",
           "gpu": False, "launch_profile": "cpu-bounded", "model_layout": "snapshot"}
    target = sandbox.up(Recording(), "isolated", tmp_path / "run", "secret-not-persisted", engine="vllm", vllm=cfg,
                        wait_ready=False, engine_profile="hardened", credential_canary=True)
    conditions = target["comparison_config"]["launch"]
    assert conditions["environment"] == captured[0]["public_env"]
    assert conditions["environment"]["TRITON_CACHE_DIR"] == "/guard-cache/triton"
    assert conditions["environment"]["TMPDIR"] == "/guard-cache"
    assert conditions["environment"]["VLLM_CPU_OMP_THREADS_BIND"] == "nobind"
    assert conditions["environment"]["GUARD_TEST_CREDENTIAL"] == "EXAMPLE_NOT_A_SECRET"
    assert conditions["engine_permissions"] == {key: captured[0][key] for key in
        ("cap_drop", "read_only", "tmpfs", "user", "security_opt")}
    encoded = json.dumps(target["comparison_config"])
    assert "C:/private/model" not in encoded and "random-local-run" not in encoded
    assert "secret-not-persisted" not in (tmp_path / "run" / "target.json").read_text(encoding="utf-8")
