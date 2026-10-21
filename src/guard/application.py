"""Product update orchestration; core stays independent of engine lifecycle."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets

from guard.adapters.model_inventory import inventory
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan, validate_update_run_config
from guard.core.select import select
from guard.models import ExecStatus, Mode, Side
from guard.store.packs import put_pack
from guard.runtime.docker import Docker
from guard.runtime import sandbox


def merge_config(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for key, value in override.items():
        out[key] = merge_config(out.get(key, {}), value) if isinstance(value, dict) else deepcopy(value)
    return out


def run_config(cfg: dict) -> RunConfig:
    rc = RunConfig(executors=cfg.get("executors", {}), adapter=cfg.get("adapter", {}).get("name"), raw=cfg)
    rc.side_configs = {
        side: RunConfig(executors=c.get("executors", {}), adapter=c.get("adapter", {}).get("name"), raw=c)
        for side in ("baseline", "candidate")
        for c in [merge_config(cfg, cfg.get("sides", {}).get(side, {}))]
    } if cfg.get("sides") else {}
    return rc


def _managed_config(env, cfg):
    base = deepcopy(cfg.get("vllm", {}))
    if env.engine.name != "vllm" or not env.engine.version:
        raise ValueError("managed update needs an explicit vllm engine version")
    image = env.engine.image_digest
    if not image or not re.fullmatch(r".+@sha256:[0-9a-f]{64}", image):
        raise ValueError("managed update needs a repository@sha256 pinned image in each ENV")
    if not env.model.id or not env.model.revision or not env.model.inventory_sha256:
        raise ValueError("managed update needs pinned model id, revision and complete inventory")
    if env.runtime.gpu_count is None:
        raise ValueError("managed update needs an explicit gpu_count")
    model_dir = Path(base.get("models_dir", "")).expanduser()
    snapshot = model_dir / "hub" / ("models--" + env.model.id.replace("/", "--")) / "snapshots" / env.model.revision
    if not snapshot.is_dir():
        raise ValueError("model snapshot is missing; prepare the offline HF cache first")
    actual = inventory(snapshot)
    if not actual["file_count"] or actual["inventory_sha256"] != env.model.inventory_sha256:
        raise ValueError("model inventory differs from ENV")
    base.update(image=image, model=env.model.id, revision=env.model.revision,
                expected_version=env.engine.version, inventory_sha256=env.model.inventory_sha256,
                gpu=env.runtime.gpu_count > 0)
    return base


def _credentials(cfg, gateway_profile):
    cfg = deepcopy(cfg)
    cfg.setdefault("direct_route", {}).setdefault("roles", {"allowed": "GUARD_UPDATE_GENERAL"})
    cfg.setdefault("role_route", {}).setdefault("roles", {"general": "GUARD_UPDATE_GENERAL", "admin": "GUARD_UPDATE_ADMIN"})
    values = dict(os.environ)
    for section in ("direct_route", "role_route"):
        for name in cfg[section]["roles"].values():
            if not values.get(name):
                values[name] = secrets.token_urlsafe(24)
    allowed = next(iter(cfg["direct_route"]["roles"].values()))
    roles = cfg["role_route"]["roles"]
    keys = {"GUARD_ALLOWED_KEY": values[allowed]} if gateway_profile == "single" else {
        "GUARD_GENERAL_KEY": values[roles["general"]], "GUARD_ADMIN_KEY": values[roles["admin"]]}
    cfg["_runtime_environ"] = values  # In memory only, never persisted.
    return cfg, keys


def update_inputs(envs, cfg: dict):
    """Shared input checks; no containers, stores or output directories are created."""
    mode = cfg.get("update", {}).get("mode", "external")
    if mode not in {"external", "managed"}:
        raise ValueError("update.mode must be external or managed")
    side_cfgs = {s: merge_config(cfg, cfg.get("sides", {}).get(s, {})) for s in ("baseline", "candidate")}
    managed = {}
    for side, env in zip(("baseline", "candidate"), envs):
        c = side_cfgs[side]
        if mode == "managed":
            options = c.get("update", {})
            if (options.get("topology", "isolated") not in {"isolated", "exposed"}
                    or options.get("gateway_profile", "single") not in {"single", "role_open", "role_enforced"}
                    or options.get("engine_profile", "default") not in sandbox.ENGINE_PROFILES):
                raise ValueError(f"{side}: unsupported sandbox profile")
            if c.get("adapter", {}).get("name") != "vllm" or not c.get("executors") or any(v != "real" for v in c["executors"].values()):
                raise ValueError("managed update requires the vllm adapter and real executors")
            managed[side] = _managed_config(env, c)
    if mode == "external" and any("real" in c.get("executors", {}).values() for c in side_cfgs.values()):
        validate_update_run_config(run_config(cfg))
        for side, env in zip(("baseline", "candidate"), envs):
            c = side_cfgs[side]
            targets = {c.get(name, {}).get("target") for name, impl in c.get("executors", {}).items() if impl == "real"}
            for path in targets:
                target = json.loads(Path(path).read_text(encoding="utf-8"))
                actual = target.get("engine", {}).get("image_digest")
                if (target.get("lab") is not False or target.get("observed_version") != env.engine.version
                        or not actual or actual.split("@")[-1] != (env.engine.image_digest or "").split("@")[-1]):
                    raise ValueError(f"{side}: target identity differs from ENV; regenerate/verify target metadata")

    return mode, side_cfgs, managed


def execute_update(store, pack, envs, policy, cfg: dict, run_dir: Path, as_of: datetime):
    """Return (report, execution_failed). Never publish fake results as measured."""
    mode, side_cfgs, managed = update_inputs(envs, cfg)
    # No input or historical evidence is overwritten.
    run_dir.mkdir(parents=True, exist_ok=False)
    put_pack(store, pack.path)
    for e in envs:
        store.put(e)
    store.put(policy)
    plan = select(pack, envs, policy, mode=Mode.update)
    store.put(plan)
    (run_dir / "plan.txt").write_text(plan.id + "\n", encoding="utf-8")
    rc = RunConfig(executors=cfg.get("executors", {}))
    for side, c in side_cfgs.items():
        if mode == "managed":
            for name in c.get("executors", {}):
                c.setdefault(name, {})["target"] = str(run_dir / side / "target.json")
        rc.side_configs[side] = RunConfig(executors=c.get("executors", {}), adapter=c.get("adapter", {}).get("name"), raw=c)
    lifecycle_errors = []
    for side, env in zip((Side.baseline, Side.candidate), envs):
        side_cfg = rc.side_configs[side.value]
        if mode == "external":
            run_plan(store, pack, plan, rc, only_side=side)
            continue
        c = side_cfg.raw
        options = c.get("update", {})
        profile = options.get("gateway_profile", "single")
        c, keys = _credentials(c, profile)
        executed = False
        try:
            with Docker("u" + secrets.token_hex(6)) as docker:
                side_dir = run_dir / side.value
                target = sandbox.up(docker, options.get("topology", "isolated"), side_dir,
                                    keys.get("GUARD_ALLOWED_KEY"), engine="vllm", vllm=managed[side.value],
                                    gateway_profile=profile, gateway_keys=keys,
                                    engine_profile=options.get("engine_profile", "default"))
                if target.get("observed_version") != env.engine.version:
                    raise ValueError("observed engine version differs from ENV")
                seen = target.get("images", {}).get(managed[side.value]["image"], "")
                if seen.split("@")[-1] != env.engine.image_digest.split("@")[-1]:
                    raise ValueError("observed image digest differs from ENV")
                for name in side_cfg.executors:
                    c.setdefault(name, {})["target"] = str(side_dir / "target.json")
                side_cfg.raw = c
                run_plan(store, pack, plan, rc, only_side=side)
                executed = True
        except Exception as exc:
            # Error messages may contain engine secrets; retain type, not raw daemon output.
            if executed:
                # Do not replace completed evidence (including confirmed violations)
                # with empty ABORTED evidence because post-run teardown failed.
                lifecycle_errors.append(f"{side.value}: cleanup incomplete ({type(exc).__name__}); inspect target run_id")
            else:
                side_cfg.abort_reason = f"managed startup/execution failed: {type(exc).__name__}"
                run_plan(store, pack, plan, rc, only_side=side)
    judge_plan(store, pack, plan, as_of)
    report = build_report(store, pack, plan, as_of)
    if lifecycle_errors:
        report = report.model_copy(update={"limitations": [*report.limitations, *lifecycle_errors]}).seal()
    store.put(report)
    failed = any(e.execution.cleanup_errors or e.execution.status == ExecStatus.ABORTED
                 or (e.execution.status == ExecStatus.NOT_RUN and pack.checks[e.check_id].executor is not None)
                 for e in store.find_latest("EVD", lambda e: e.plan == plan.ref()))
    return report, failed or bool(lifecycle_errors)


def update_readiness(pack, envs, policy, cfg: dict) -> dict:
    """Read-only preparation check. It neither pulls images nor starts an engine."""
    from guard.executors import registry
    from guard.core.preflight import validate_pack

    checks = []
    def record(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    prepared = None
    try:
        validate_pack(pack)
        prepared = update_inputs(envs, cfg)
        plan = select(pack, envs, policy, mode=Mode.update)
        selected = [item for item in plan.items if item.selected]
        if not selected:
            raise ValueError("policy selected no checks")
        for item in selected:
            if item.gaps:
                raise ValueError(f"{item.check_id}: unresolved selection gaps {item.gaps}")
            for side, c in prepared[1].items():
                factory = registry.resolve_executor(item.executor, c.get("executors", {}).get(item.executor, ""))
                if factory is None or c.get("executors", {}).get(item.executor) != "real":
                    raise ValueError(f"{side}/{item.check_id}: real executor is required")
                if prepared[0] == "managed":
                    c, _ = _credentials(c, c.get("update", {}).get("gateway_profile", "single"))
                executor = factory(c)
                executor.validate_policy(pack.checks[item.check_id], policy.values.get(item.check_id, {}))
                executor.keys()
        record("inputs", True, "pack, selection, real executors, policy settings and model inventory checked")
    except Exception as exc:
        # Validation errors contain field descriptions; daemon output is never copied.
        record("inputs", False, str(exc) if isinstance(exc, ValueError) else type(exc).__name__)

    docker = Docker("readiness")
    try:
        server = docker.daemon_info(timeout=15)
        record("docker", server["os"] == "linux" and bool(server["architecture"]), "Linux Docker daemon and known architecture required")
    except Exception as exc:
        server = None
        record("docker", False, f"daemon unavailable ({type(exc).__name__}); start local Linux Docker")
    if prepared and server and prepared[0] == "managed":
        aliases = {"x86_64": "amd64", "aarch64": "arm64"}
        normalize = lambda s: aliases.get(s, s)
        arch = normalize(server["architecture"])
        images = {sandbox.NGINX_IMAGE, sandbox.PYTHON_IMAGE, *(v["image"] for v in prepared[2].values())}
        for image in sorted(images):
            try:
                info = docker.image_info(image, timeout=15)
                digest_ok = bool(info["digests"]) and ("@" not in image or image in info["digests"])
                record(f"image:{image}", info["os"] == "linux" and normalize(info["architecture"]) == arch and digest_ok,
                       "local image must match daemon architecture and have a repository digest; no automatic pull")
            except Exception as exc:
                record(f"image:{image}", False, f"local image unavailable ({type(exc).__name__})")
        for side, env in zip(("baseline", "candidate"), envs):
            if env.runtime.gpu_count == 0:
                cpu = env.runtime.cpu
                record(f"hardware:{side}", cpu is not None and cpu.system == "Linux" and normalize(cpu.architecture) == arch,
                       "explicit CPU profile must match daemon architecture; other profile fields remain declared")
            else:
                record(f"hardware:{side}", bool(env.runtime.gpu_model and env.runtime.driver),
                       "GPU and driver must be declared; actual device availability requires engine validation")
    if envs[0].engine.image_digest == envs[1].engine.image_digest:
        record("image-pair", False, "use different image digests for an update validation")
    return {"ready": all(c["ok"] for c in checks), "checks": checks,
            "limitations": ["Preparation only: no engine, security verdict or performance was measured.",
                            "CPU model/kernel/threads and GPU availability require verification on the test host."]}
