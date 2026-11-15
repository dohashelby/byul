"""Bounded GPU launch/configuration contracts. No Docker daemon or model is executed.

2026-11-12 (M-A9): CPU images are out of scope; the former CPU launch cases
were converted to the gpu-bounded profile and CPU-only cases were removed.
"""
from copy import deepcopy
import json
from pathlib import Path
import tomllib

import pytest
from typer.testing import CliRunner

from guard import application
from guard.adapters.base import LaunchSpec
from guard.adapters.model_inventory import inventory
from guard.adapters.vllm import VllmAdapter
from guard.cli import app
from guard.executors import registry
from guard.executors.fake import FakeExecutor
from guard.models import utcnow
from guard.runtime.docker import CmdResult, ContainerLimits, Docker, DockerError
from guard.runtime import sandbox
from guard.store import Store
from tests.builders import baseline_pack, environment, policy


def launch(**options):
    return LaunchSpec(model="facebook/opt-125m", port=8000, image="gpu@sha256:" + "a" * 64,
                      options={"revision": "rev", "models_dir": Path("/srv/models/snapshot"),
                               "device": "gpu", "launch_profile": "gpu-bounded", "model_layout": "snapshot", **options})


GPU_RESOURCES = {"cpus": 4, "memory_bytes": 17179869184, "shm_bytes": 2147483648, "pids_limit": 512}


def test_bounded_gpu_uses_local_readonly_snapshot_and_image_entrypoint():
    plan = VllmAdapter().launch_plan(launch())
    assert plan.entrypoint is None
    assert plan.argv[:2] == ["--model", "/models"]
    assert plan.argv[plan.argv.index("--served-model-name") + 1] == "facebook/opt-125m"
    assert plan.argv[plan.argv.index("--gpu-memory-utilization") + 1] == "0.5"
    assert plan.argv[plan.argv.index("--tensor-parallel-size") + 1] == "1"
    assert plan.argv[plan.argv.index("--load-format") + 1] == "auto"
    assert "--enforce-eager" in plan.argv and "--disable-log-requests" not in plan.argv
    assert "--kv-cache-memory-bytes" not in plan.argv
    assert not any(key.startswith("VLLM_CPU") or key == "OMP_NUM_THREADS" for key in plan.env)
    assert plan.env["HF_HUB_OFFLINE"] == plan.env["TRANSFORMERS_OFFLINE"] == "1"
    assert plan.env["VLLM_NO_USAGE_STATS"] == plan.env["DO_NOT_TRACK"] == "1"
    assert plan.mounts[0].read_only and plan.mounts[0].target == "/models"
    # Shared memory and container bounds come from the provider's resources table.
    assert plan.gpus and plan.shm_size is None and plan.limits is None


@pytest.mark.parametrize("options", [
    {"max_model_len": True}, {"max_num_seqs": 0}, {"max_num_batched_tokens": 127},
    {"gpu_memory_utilization": 0}, {"gpu_memory_utilization": 1.5}, {"gpu_memory_utilization": True},
    {"enforce_eager": "true"}, {"tensor_parallel_size": 0}, {"dtype": "int8"}, {"dtype": []},
    {"device": "cpu"}, {"cpu_kv_cache_gib": 1}, {"kv_cache_memory_bytes": 67108864}, {"cpu_threads": 2},
    {"arbitrary_argv": ["--trust-remote-code"]},
])
def test_gpu_profile_rejects_invalid_unbounded_or_removed_cpu_options(options):
    with pytest.raises(ValueError):
        VllmAdapter().launch_plan(launch(**options))


def test_legacy_gpu_launch_is_unchanged_and_cpu_device_is_rejected():
    spec = LaunchSpec(model="model", port=8000, image="img", options={"revision": "rev", "models_dir": Path("/cache")})
    plan = VllmAdapter().launch_plan(spec)
    assert plan.argv == ["--model", "model", "--revision", "rev", "--served-model-name", "model",
                         "--host", "0.0.0.0", "--port", "8000"]
    assert plan.gpus and plan.entrypoint is None and plan.limits is None and plan.shm_size == "1g"
    cpu = LaunchSpec(model="model", port=8000, image="img", options={"revision": "rev", "models_dir": Path("/cache"),
                                                                    "device": "cpu", "cpu_kv_cache_gib": 2})
    with pytest.raises(ValueError):
        VllmAdapter().launch_plan(cpu)


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
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": "gpu@sha256:" + "a" * 64},
                      runtime={"gpu_count": 1, "gpu_model": "EXAMPLE-GPU", "driver": "EXAMPLE-DRIVER", "tp_size": 1},
                      model={"id": "facebook/opt-125m", "revision": "rev", "inventory_sha256": inventory(snapshot)["inventory_sha256"]})
    cfg = {"adapter": {"name": "vllm"}, "executors": {"input_validation": "real"},
           "validation": {"mode": "managed", "gateway_profile": "single"},
           "vllm": {"models_dir": str(snapshot), "model_layout": "snapshot", "launch_profile": "gpu-bounded",
                    "gpu_devices": ["0"], "resources": dict(GPU_RESOURCES)},
           "input_validation": {"vocab_size": 50272, "vocab_source": "synthetic unit fixture"}}
    pack = baseline_pack()
    pol = policy(pack.ref(), scope={"include": ["REQ-09"]}, values={"REQ-09": {
        "max_input_tokens": 127, "rejection_codes": ["400", "422"], "allowed_endpoints": ["POST /v1/completions"],
        "allowed_input_formats": ["token_ids"]}})
    return pack, env, pol, cfg


def test_managed_snapshot_pins_inventory_and_rejects_layout_cpu_or_inventory_mismatch(tmp_path):
    _, env, _, cfg = setup_inputs(tmp_path)
    managed = application._managed_config(env, cfg)
    assert Path(managed["models_dir"]).is_absolute() and managed["image"] == env.engine.image_digest
    plan = sandbox._vllm_plan(managed)
    assert plan.argv[1] == "/models" and plan.gpu_devices == ("0",) and plan.limits.to_dict() == GPU_RESOURCES
    bad = deepcopy(cfg)
    bad["vllm"]["tensor_parallel_size"] = 2
    with pytest.raises(ValueError, match="GPU_PARALLEL_LAYOUT_MISMATCH"):
        application._managed_config(env, bad)
    no_shm = deepcopy(cfg)
    del no_shm["vllm"]["resources"]["shm_bytes"]
    with pytest.raises(ValueError, match="shm_bytes"):
        application._managed_config(env, no_shm)
    cpu_env = env.model_copy(update={"runtime": env.runtime.model_copy(update={"gpu_count": 0})}).seal()
    with pytest.raises(ValueError, match="VLLM_GPU_REQUIRED"):
        application._managed_config(cpu_env, cfg)
    for resources in ({**GPU_RESOURCES, "privileged": True}, {**GPU_RESOURCES, "cpus": True},
                      {**GPU_RESOURCES, "pids_limit": -1}, {"cpus": 4, "memory_bytes": 17179869184}):
        unbounded = deepcopy(cfg)
        unbounded["vllm"]["resources"] = resources
        with pytest.raises(ValueError):
            application._managed_config(env, unbounded)
    (Path(cfg["vllm"]["models_dir"]) / "config.json").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="inventory differs"):
        application._managed_config(env, cfg)


# The former CPU-only entrypoint case was removed: GPU launches keep the image entrypoint.
@pytest.mark.parametrize("memory_bytes, entrypoint, ready", [(32 << 30, ["vllm", "serve"], True),
    (2 << 30, ["vllm", "serve"], False)])
def test_single_readiness_checks_capacity_without_starting_or_pulling(tmp_path, monkeypatch, memory_bytes, entrypoint, ready):
    pack, env, pol, cfg = setup_inputs(tmp_path)
    class Local:
        def __init__(self, *args): pass
        def daemon_info(self, **kwargs): return {"os": "linux", "architecture": "amd64"}
        def daemon_resources(self, **kwargs): return {"memory_bytes": memory_bytes, "cpus": 8}
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


def test_gpu_example_uses_bounded_gpu_launch_and_separates_policy_from_launch():
    cfg = tomllib.loads((Path(__file__).resolve().parents[1] / "examples/gpu-bounded.toml").read_text(encoding="utf-8"))
    assert cfg["vllm"]["launch_profile"] == "gpu-bounded" and cfg["vllm"]["gpu_devices"] == ["0"]
    assert cfg["vllm"]["resources"]["shm_bytes"] == 2147483648
    assert "max_input_tokens" not in cfg["vllm"] and set(cfg["executors"].values()) == {"real"}


@pytest.mark.parametrize("enabled", [False, True])
def test_credential_canary_is_fixed_opt_in_and_absent_when_disabled(enabled):
    calls = []
    class Recording:
        def run_detached(self, image, suffix, **kwargs):
            calls.append(kwargs)
            return "recording-engine"
    sandbox._start_engine(Recording(), "vllm", ["net"], [],
        {"model": "test", "revision": "rev", "image": "gpu", "models_dir": "/snapshot", "gpu": True,
         "gpu_devices": ["0"], "resources": dict(GPU_RESOURCES),
         "launch_profile": "gpu-bounded", "model_layout": "snapshot"}, credential_canary=enabled)
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


def test_bounded_gpu_launch_conditions_do_not_depend_on_host_snapshot_path():
    first = VllmAdapter().launch_plan(launch(models_dir=Path("/srv/first")))
    second = VllmAdapter().launch_plan(launch(models_dir=Path("/mnt/second")))
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
    candidate = baseline.model_copy(update={"id": "ENV-GPU-CANDIDATE", "engine": baseline.engine.model_copy(update={
        "image_digest": "gpu@sha256:" + "b" * 64})}).seal()
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
    assert len([s for s in rpt.limitations if "cleanup incomplete" in s and "guard.run=" in s]) == 1
    candidate_evd, = [e for e in st.iter_kind('EVD') if e.side.value == 'candidate']
    assert candidate_evd.execution.abort_reason == 'BASELINE_RESOURCE_RELEASE_UNVERIFIED'
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
def test_triton_cache_fix_is_limited_to_hardened_bounded_gpu_without_relaxing_tmp(engine, bounded, profile, triton):
    before = deepcopy(sandbox.ENGINE_PROFILES)
    cfg = {"launch_profile": "gpu-bounded"} if bounded else {}
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
    assert sandbox.ENGINE_PROFILES == before  # Later legacy launches must not inherit the bounded-only change.


def test_gpu_target_hash_conditions_record_effective_cache_environment_and_permissions(tmp_path, monkeypatch):
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
    cfg = {"model": "test", "revision": "rev", "image": "gpu@sha256:fixed", "models_dir": "/private/model",
           "gpu": True, "gpu_devices": ["0"], "resources": dict(GPU_RESOURCES),
           "launch_profile": "gpu-bounded", "model_layout": "snapshot"}
    target = sandbox.up(Recording(), "isolated", tmp_path / "run", "secret-not-persisted", engine="vllm", vllm=cfg,
                        wait_ready=False, engine_profile="hardened", credential_canary=True)
    conditions = target["comparison_config"]["launch"]
    assert conditions["environment"] == captured[0]["public_env"]
    assert conditions["environment"]["TRITON_CACHE_DIR"] == "/guard-cache/triton"
    assert conditions["environment"]["TMPDIR"] == "/guard-cache"
    assert conditions["environment"]["VLLM_NO_USAGE_STATS"] == "1"
    assert conditions["profile"] == "gpu-bounded-v1" and conditions["gpu_count"] == 1
    assert conditions["resources"] == GPU_RESOURCES
    assert conditions["environment"]["GUARD_TEST_CREDENTIAL"] == "EXAMPLE_NOT_A_SECRET"
    assert conditions["engine_permissions"] == {key: captured[0][key] for key in
        ("cap_drop", "read_only", "tmpfs", "user", "security_opt")}
    encoded = json.dumps(target["comparison_config"])
    assert "/private/model" not in encoded and "random-local-run" not in encoded
    assert "secret-not-persisted" not in (tmp_path / "run" / "target.json").read_text(encoding="utf-8")
