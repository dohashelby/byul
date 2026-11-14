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
from guard.adapters.engine_provider import get_provider
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan, validate_update_run_config
from guard.core.select import select
from guard.collectors.change import read_change, resolve_plan, save_change
from guard.core.change_selection import build_context
from guard.models import ExecStatus, Mode, Side, utcnow
from guard.store.packs import put_pack
from guard.runtime.docker import Docker
from guard.runtime import generation_readiness, sandbox
from guard.runtime.kubernetes_transition import KubernetesTransition


def merge_config(base: dict, override: dict) -> dict:
    from guard.configuration import merge_config as merge
    return merge(base, override)


def run_config(cfg: dict) -> RunConfig:
    rc = RunConfig(executors=cfg.get("executors", {}), adapter=cfg.get("adapter", {}).get("name"), raw=cfg, requested=deepcopy(cfg))
    rc.side_configs = {
        side: RunConfig(executors=c.get("executors", {}), adapter=c.get("adapter", {}).get("name"), raw=c)
        for side in ("baseline", "candidate")
        for c in [merge_config(cfg, cfg.get("sides", {}).get(side, {}))]
    } if cfg.get("sides") else {}
    return rc


def _managed_config(env, cfg):
    from guard.adapters.engine_provider import get_provider
    return get_provider(env.engine.name).prepare_managed(env, cfg)


def _baseline_ran_without_abort(evidence) -> bool:
    """A transition can continue past unrelated NOT_RUN checks, but not a failed run."""
    states = [item.execution.status for item in evidence]
    return ExecStatus.COMPLETED in states and ExecStatus.ABORTED not in states


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
            if c.get("adapter", {}).get("name") != env.engine.name or not c.get("executors") or any(v != "real" for v in c["executors"].values()):
                raise ValueError("managed update requires the matching engine adapter and real executors")
            if type(c.get("update", {}).get("credential_canary", False)) is not bool:
                raise ValueError("credential_canary must be a boolean")
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

    if any(c.get("executors",{}).get(name)=="kubernetes" for c in side_cfgs.values()
           for name in ("request_binding","input_validation")):
        if mode!="external" or cfg.get("update",{}).get("kubernetes_transition",True) is not True:
            raise ValueError("KUBERNETES_TRANSITION_REQUIRED")
        name=next(name for name in ("request_binding","input_validation")
                  if any(c.get("executors",{}).get(name)=="kubernetes" for c in side_cfgs.values()))
        paths=[c.get(name,{}).get("target") for c in side_cfgs.values()]
        if not all(paths) or Path(paths[0]).resolve()==Path(paths[1]).resolve():
            raise ValueError("KUBERNETES_TRANSITION_TARGETS_UNVERIFIED")
        try:
            KubernetesTransition(envs,side_cfgs,timeout=cfg.get("update",{}).get("transition_timeout",300),
                poll_interval=cfg.get("update",{}).get("transition_poll_interval",0.25),
                candidate_binding=cfg.get("update",{}).get("kubernetes_candidate_binding","prebound"))
        except Exception:
            raise ValueError("KUBERNETES_TRANSITION_CONFIGURATION_INVALID") from None
    return mode, side_cfgs, managed


def execute_update(store, pack, envs, policy, cfg: dict, run_dir: Path, as_of: datetime,
                   *, change=None, previous_plan=None):
    from guard.core.execution_recipe import validate_policy_time
    from guard.runtime.resource_lease import provider_leases
    validate_policy_time(policy)
    cfg = deepcopy(cfg)
    prepared = update_inputs(envs, cfg)
    with provider_leases(envs, [prepared[2].get(s, {}) for s in ('baseline', 'candidate')]) as leases:
        return _execute_update(store, pack, envs, policy, cfg, run_dir, as_of,
            change=change, previous_plan=previous_plan, prepared=prepared, leases=leases)


def _execute_update(store, pack, envs, policy, cfg: dict, run_dir: Path, as_of: datetime,
                   *, change=None, previous_plan=None, prepared=None, leases=()):
    """Return (report, execution_failed). Never publish fake results as measured."""
    from guard.core.execution_recipe import bind_recipe, validate_policy_time
    validate_policy_time(policy)
    mode, side_cfgs, managed = prepared
    change_input = read_change(change, pack, store)
    previous = resolve_plan(store, previous_plan)
    if change_input is not None or previous is not None:
        build_context(store, previous, pack, envs, policy, cfg,
                      change_input.change if change_input else None, mode=Mode.update, as_of=as_of, read_only=True)
    if any(c.get("update", {}).get("credential_canary", False) for c in side_cfgs.values()) and policy.status.value != "DRAFT":
        raise ValueError("credential_canary is only available with a DRAFT sandbox policy")
    # No input or historical evidence is overwritten.
    run_dir.mkdir(parents=True, exist_ok=False)
    put_pack(store, pack.path)
    for e in envs:
        store.put(e)
    store.put(policy)
    chg = save_change(store, change_input) if change_input is not None else None
    context = build_context(store, previous, pack, envs, policy, cfg, chg, mode=Mode.update,
                            as_of=as_of) if chg is not None or previous is not None else None
    plan = (select(pack, envs, policy, change=chg, mode=Mode.update, context=context)
            if context is not None else select(pack, envs, policy, mode=Mode.update))
    plan = bind_recipe(plan, cfg, envs)
    store.put(plan)
    (run_dir / "plan.txt").write_text(plan.id + "\n", encoding="utf-8")
    rc = RunConfig(executors=cfg.get("executors", {}), requested=deepcopy(cfg))
    for side, c in side_cfgs.items():
        if mode == "managed":
            for name in c.get("executors", {}):
                c.setdefault(name, {})["target"] = str(run_dir / side / "target.json")
        rc.side_configs[side] = RunConfig(executors=c.get("executors", {}), adapter=c.get("adapter", {}).get("name"), raw=c)
    lifecycle_errors = []
    diagnostic_refs = []
    kube_transition=None
    if any(c.get("executors",{}).get(name)=="kubernetes" for c in side_cfgs.values()
           for name in ("request_binding","input_validation")):
        kube_transition=KubernetesTransition(envs,side_cfgs,timeout=cfg.get("update",{}).get("transition_timeout",300),
            poll_interval=cfg.get("update",{}).get("transition_poll_interval",0.25),
                candidate_binding=cfg.get("update",{}).get("kubernetes_candidate_binding","prebound"))
    candidate_blocked = False
    for side, env in zip((Side.baseline, Side.candidate), envs):
        if not any(item.selected for item in plan.items):
            break  # 이월 전용 PLAN은 엔진을 기동하지 않는다.
        side_cfg = rc.side_configs[side.value]
        if side == Side.candidate and candidate_blocked:
            side_cfg.abort_reason = 'BASELINE_RESOURCE_RELEASE_UNVERIFIED'
            run_plan(store, pack, plan, rc, only_side=side)
            continue
        if mode == "external":
            if kube_transition is not None:
                try:
                    if side==Side.baseline:
                        kube_transition.prepare_baseline()
                    else:
                        previous_evidence=store.find_latest("EVD",lambda e:e.plan==plan.ref() and e.side==Side.baseline)
                        if not _baseline_ran_without_abort(previous_evidence):
                            raise ValueError("BASELINE_EXECUTION_FAILED")
                        kube_transition.candidate_ready()
                    side_cfg.raw["_kubernetes_target_sha256"]=kube_transition.transports[side.value].target_sha256
                except Exception:
                    side_cfg.abort_reason="KUBERNETES_TRANSITION_UNVERIFIED"
                    lifecycle_errors.append(f"{side.value}: KUBERNETES_TRANSITION_UNVERIFIED; operator recovery required; GPU release unverified")
            run_plan(store, pack, plan, rc, only_side=side)
            continue
        c = side_cfg.raw
        options = c.get("update", {})
        profile = options.get("gateway_profile", "single")
        c, keys = _credentials(c, profile)
        executed = False
        run_id = "u" + secrets.token_hex(6)
        for lease in leases: lease.note_owner(run_id)
        try:
            with Docker(run_id) as docker:
                side_dir = run_dir / side.value
                try:
                    sentinel_dir = _managed_sentinel_dir(run_dir, options.get("engine_profile", "default"), side.value)
                    from guard.adapters.engine_provider import get_provider
                    target = sandbox.up(docker, options.get("topology", "isolated"), side_dir,
                                        keys.get("GUARD_ALLOWED_KEY"), engine=env.engine.name, **get_provider(env.engine.name).sandbox_arguments(managed[side.value]),
                                        gateway_profile=profile, gateway_keys=keys,
                                        engine_profile=options.get("engine_profile", "default"),
                                        sentinel_dir=sentinel_dir,
                                        credential_canary=options.get("credential_canary", False))
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
                finally:
                    diagnostic_refs.append(_managed_diagnostic_reference(docker, side_dir, keys, side.value))
        except Exception as exc:
            # Error messages may contain engine secrets; retain type, not raw daemon output.
            if executed:
                # Do not replace completed evidence (including confirmed violations)
                # with empty ABORTED evidence because post-run teardown failed.
                lifecycle_errors.append(f"{side.value}: cleanup incomplete ({type(exc).__name__}); guard.run={run_id}")
                candidate_blocked |= side == Side.baseline
                for lease in leases: lease.mark_uncertain(run_id)
            else:
                lifecycle_errors.append(f"{side.value}: startup/execution interrupted ({type(exc).__name__}); sealed evidence retained; guard.run={run_id}")
                if _has_cleanup_note(exc, run_id):
                    lifecycle_errors.append(f"{side.value}: cleanup incomplete after startup/execution failure ({type(exc).__name__}); guard.run={run_id}")
                    candidate_blocked |= side == Side.baseline
                    for lease in leases: lease.mark_uncertain(run_id)
                side_cfg.abort_reason = f"managed startup/execution failed: {type(exc).__name__}"
                run_plan(store, pack, plan, rc, only_side=side)
    if kube_transition is not None:
        from guard.models import canonical_json
        try:
            stored=store.put_artifact(canonical_json(kube_transition.report()))
            diagnostic_refs.append(f"Kubernetes transition diagnostic (not EVD): artifacts/{stored.sha256}; GPU release unverified")
        except Exception:
            lifecycle_errors.append("KUBERNETES_TRANSITION_DIAGNOSTIC_STORE_UNAVAILABLE")
    judge_plan(store, pack, plan, as_of)
    report = build_report(store, pack, plan, as_of)
    if lifecycle_errors or diagnostic_refs:
        report = report.model_copy(update={"limitations": [*report.limitations, *lifecycle_errors, *diagnostic_refs]}).seal()
    store.put(report)
    failed = any(e.execution.cleanup_errors or e.execution.status == ExecStatus.ABORTED
                 or (e.execution.status == ExecStatus.NOT_RUN and pack.checks[e.check_id].executor is not None)
                 for e in store.find_latest("EVD", lambda e: e.plan == plan.ref()))
    return report, failed or bool(lifecycle_errors)


def _managed_sentinel_dir(run_dir: Path, engine_profile: str, side: str = "single") -> Path | None:
    """Create a fresh, run-owned writable probe directory; never reuse old data."""
    if not sandbox.ENGINE_PROFILES[engine_profile].get("sentinel_mount"):
        return None
    # The run root was created exclusively by this invocation. Keeping each
    # directory directly below it avoids sharing baseline/candidate writes and
    # preserves validation's existing run/sentinel location.
    name = "sentinel" if side == "single" else f"{side}-sentinel"
    folder = run_dir / name
    folder.mkdir(exist_ok=False)
    return folder


def _has_cleanup_note(exc: Exception, run_id: str) -> bool:
    """Recognize only Docker's fixed note for this locally generated run ID."""
    expected = f"cleanup incomplete for guard.run={run_id}; inspect labelled containers/networks"
    return expected in getattr(exc, "__notes__", [])


def _managed_diagnostic_reference(docker, folder: Path, keys: dict, side: str) -> str:
    """A separate diagnostic reference, never an EVD or a verdict input."""
    try:
        saved = sandbox.capture_diagnostics(docker, folder, secret_values=list(keys.values()), phase="run-final")
        if saved.get("path") == "managed-diagnostic.json" and re.fullmatch(r"[0-9a-f]{64}", saved.get("sha256", "")):
            detail = f"{side}: separate run diagnostic (not EVD): {side}/managed-diagnostic.json sha256={saved['sha256']}"
        else:
            detail = f"{side}: separate run diagnostic unavailable ({saved.get('error_type', 'UnknownError')})"
    except Exception as exc:
        detail = f"{side}: separate run diagnostic unavailable ({type(exc).__name__})"
    try:
        saved = generation_readiness.reference(folder)
        if saved is not None:
            detail += f"; separate startup generation (not EVD): {side}/{saved['path']} sha256={saved['sha256']}"
    except Exception as exc:
        detail += f"; separate startup generation unavailable ({type(exc).__name__})"
    return detail


def _validation_inputs(env, cfg: dict):
    """One managed engine, using the same launch contract as update."""
    effective = deepcopy(cfg)
    options = effective.get("validation", {})
    if type(options) is not dict or type(options.get("credential_canary", False)) is not bool:
        raise ValueError("validation settings must be a table and credential_canary a boolean")
    if options.get("mode", "managed") != "managed" or effective.get("sides"):
        raise ValueError("validate supports one managed environment without side overrides")
    if (options.get("topology", "isolated") not in {"isolated", "exposed"}
            or options.get("gateway_profile", "single") not in sandbox.GATEWAY_PROFILES
            or options.get("engine_profile", "default") not in sandbox.ENGINE_PROFILES):
        raise ValueError("unsupported validation sandbox profile")
    if (effective.get("adapter", {}).get("name") != env.engine.name or not effective.get("executors")
            or any(value != "real" for value in effective["executors"].values())):
        raise ValueError("validate requires the matching engine adapter and real executors")
    return effective, _managed_config(env, effective)


def execute_validation(store, pack, env, policy, cfg: dict, run_dir: Path, as_of: datetime):
    from guard.core.execution_recipe import validate_policy_time
    from guard.runtime.resource_lease import provider_leases
    validate_policy_time(policy)
    cfg = deepcopy(cfg)
    prepared = _validation_inputs(env, deepcopy(cfg))
    with provider_leases([env], [prepared[1]]) as leases:
        return _execute_validation(store, pack, env, policy, cfg, run_dir, as_of, prepared=prepared, leases=leases)


def _execute_validation(store, pack, env, policy, cfg: dict, run_dir: Path, as_of: datetime, *, prepared=None, leases=()):
    """Audit a single managed engine; retain evidence when teardown fails."""
    from guard.core.preflight import validate_pack
    from guard.core.execution_recipe import bind_recipe, validate_policy_time

    validate_policy_time(policy)
    validate_pack(pack)
    effective, managed = prepared
    options = effective.get("validation", {})
    if options.get("credential_canary", False) and policy.status.value != "DRAFT":
        raise ValueError("credential_canary is only available with a DRAFT sandbox policy")
    profile = options.get("gateway_profile", "single")
    effective, keys = _credentials(effective, profile)
    plan = bind_recipe(select(pack, [env], policy, mode=Mode.audit), cfg, [env])
    if not any(item.selected for item in plan.items):
        raise ValueError("validation policy selected no checks")
    run_dir.mkdir(parents=True, exist_ok=False)
    put_pack(store, pack.path)
    store.put(env)
    store.put(policy)
    store.put(plan)
    (run_dir / "plan.txt").write_text(plan.id + "\n", encoding="utf-8")
    target_dir = run_dir / "single"
    sentinel_dir = _managed_sentinel_dir(run_dir, options.get("engine_profile", "default"))
    for name in effective["executors"]:
        effective.setdefault(name, {})["target"] = str(target_dir / "target.json")
    rc = RunConfig(executors=effective["executors"], adapter=env.engine.name, raw=effective, requested=deepcopy(cfg))
    completed = False
    lifecycle_errors = []
    diagnostic_refs = []
    run_id = "v" + secrets.token_hex(6)
    for lease in leases: lease.note_owner(run_id)
    try:
        with Docker(run_id) as docker:
            try:
                from guard.adapters.engine_provider import get_provider
                target = sandbox.up(docker, options.get("topology", "isolated"), target_dir,
                                    keys.get("GUARD_ALLOWED_KEY"), engine=env.engine.name, **get_provider(env.engine.name).sandbox_arguments(managed),
                                    gateway_profile=profile, gateway_keys=keys,
                                    engine_profile=options.get("engine_profile", "default"), sentinel_dir=sentinel_dir,
                                    credential_canary=options.get("credential_canary", False))
                if target.get("observed_version") != env.engine.version:
                    raise ValueError("observed engine version differs from ENV")
                observed = target.get("images", {}).get(managed["image"], "")
                if observed.split("@")[-1] != env.engine.image_digest.split("@")[-1]:
                    raise ValueError("observed image digest differs from ENV")
                run_plan(store, pack, plan, rc)
                completed = True
            finally:
                diagnostic_refs.append(_managed_diagnostic_reference(docker, target_dir, keys, "single"))
    except Exception as exc:
        if completed:
            lifecycle_errors.append(f"single: cleanup incomplete ({type(exc).__name__}); guard.run={run_id}")
            for lease in leases: lease.mark_uncertain(run_id)
        else:
            lifecycle_errors.append(f"single: startup/execution interrupted ({type(exc).__name__}); sealed evidence retained; guard.run={run_id}")
            if _has_cleanup_note(exc, run_id):
                lifecycle_errors.append(f"single: cleanup incomplete after startup/execution failure ({type(exc).__name__}); guard.run={run_id}")
                for lease in leases: lease.mark_uncertain(run_id)
            rc.abort_reason = f"managed startup/execution failed: {type(exc).__name__}"
            run_plan(store, pack, plan, rc)
    judge_plan(store, pack, plan, as_of)
    report = build_report(store, pack, plan, as_of)
    if lifecycle_errors or diagnostic_refs:
        report = report.model_copy(update={"limitations": [*report.limitations, *lifecycle_errors, *diagnostic_refs]}).seal()
    store.put(report)
    failed = any(e.execution.cleanup_errors or e.execution.status == ExecStatus.ABORTED
                 or (e.execution.status == ExecStatus.NOT_RUN and pack.checks[e.check_id].executor is not None)
                 for e in store.find_latest("EVD", lambda e: e.plan == plan.ref()))
    return report, failed or bool(lifecycle_errors)


def update_readiness(pack, envs, policy, cfg: dict, *, single: bool = False,
                     change=None, previous_plan=None, source_store=None) -> dict:
    """Read-only preparation check. It neither pulls images nor starts an engine."""
    from guard.executors import registry
    from guard.core.preflight import validate_pack
    from guard.readiness_support import assess_check_support

    change_input = read_change(change, pack, source_store)
    previous = resolve_plan(source_store, previous_plan) if previous_plan is not None else None
    context = build_context(source_store, previous, pack, envs, policy, cfg,
                            change_input.change if change_input else None,
                            mode=Mode.audit if single else Mode.update, read_only=True
                            ) if change_input is not None or previous is not None else None
    checks = []
    def record(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    prepared = None
    check_support = []
    try:
        validate_pack(pack)
        if single:
            effective, managed = _validation_inputs(envs[0], cfg)
            prepared = ("managed", {"single": effective}, {"single": managed})
        else:
            prepared = update_inputs(envs, cfg)
        option_group = "validation" if single else "update"
        if any(c.get(option_group, {}).get("credential_canary", False) for c in prepared[1].values()) and policy.status.value != "DRAFT":
            raise ValueError("credential_canary is only available with a DRAFT sandbox policy")
        plan_mode = Mode.audit if single else Mode.update
        plan = (select(pack, envs, policy, change=change_input.change if change_input else None,
                       mode=plan_mode, context=context) if context is not None
                else select(pack, envs, policy, mode=plan_mode))
        selected = [item for item in plan.items if item.selected]
        if not selected:
            raise ValueError("policy selected no checks")
        for (side, c), env in zip(prepared[1].items(), envs):
            check_support.extend({"side": side, **row} for row in assess_check_support(
                pack, env, policy, c, selected_check_ids=[item.check_id for item in selected],
                managed=prepared[0] == "managed"))
        support_by_key = {(row["side"], row["check_id"]): row for row in check_support}
        for item in selected:
            if item.gaps:
                raise ValueError(f"{item.check_id}: unresolved selection gaps {item.gaps}")
            for side, c in prepared[1].items():
                selected_env = envs[0] if side in {'single', 'baseline'} else envs[-1]
                factory = registry.resolve_executor(item.executor, c.get("executors", {}).get(item.executor, ""), engine=selected_env.engine.name, version=selected_env.engine.version)
                kube=(c.get("executors",{}).get(item.executor)=="kubernetes" and prepared[0]=="external")
                if factory is None or c.get("executors", {}).get(item.executor) != "real" and not kube:
                    raise ValueError(f"{side}/{item.check_id}: real executor is required")
                if prepared[0] == "managed":
                    c, _ = _credentials(c, c.get("validation" if single else "update", {}).get("gateway_profile", "single"))
                from guard.configuration import for_check
                c = for_check(c, item.executor, item.check_id)  # [check_config.<CHECK>] (RB2-S)
                executor = factory(c)
                support = support_by_key[side, item.check_id]
                # These collectors distinguish a valid policy outside their
                # bounded profile from malformed input. Still validate launch
                # settings, budgets and fixtures; never shrink the policy.
                if (support.get("policy_valid") is True and support.get("policy_supported") is False
                        and hasattr(executor, "validate_configuration")):
                    executor.validate_configuration(pack.checks[item.check_id])
                else:
                    from guard.executors import run_binding
                    check_def, values = pack.checks[item.check_id], policy.values.get(item.check_id, {})
                    if run_binding.keys_for(check_def):
                        # Static preparation computes the same per-run binding guard run will compute.
                        _, values = run_binding.effective_values(
                            check_def, c.get(item.executor, {}), selected_env, values, side=side, attempt_id=None,
                            issued_at=utcnow(), valid_from=policy.valid_from, valid_until=policy.valid_until,
                            run_cfg=run_binding.run_config(c, item.check_id))
                    executor.validate_policy(check_def, values)
                executor.keys()
        record("inputs", True, "pack, selection, real executors, policy settings and model inventory checked")
    except Exception as exc:
        # Validation errors contain field descriptions; daemon output is never copied.
        record("inputs", False, str(exc) if isinstance(exc, ValueError) else type(exc).__name__)

    kube_requested=cfg.get("executors",{}).get("request_binding")=="kubernetes" or any(
        c.get("executors",{}).get("request_binding")=="kubernetes" for c in cfg.get("sides",{}).values())
    if kube_requested:
        record("kubernetes-static-binding",prepared is not None,"Exact target/ENV declarations checked; actual cluster, Pods, template and GPU remain unverified")
        if cfg.get("update",{}).get("kubernetes_candidate_binding")=="after_baseline":
            record("candidate-binding-deferred",True,"Only baseline target is selected; exact candidate ENV/generation will be required after baseline API termination, before candidate dispatch")
        if not single and envs[0].engine.image_digest==envs[1].engine.image_digest:
            record("image-pair",False,"use different image digests for an update validation")
        return {"ready":all(c["ok"] for c in checks),"checks":checks,"check_support":check_support,
            "limitations":["Static Kubernetes preparation only; no kubectl, Docker, engine or GPU execution.",
                "Existing inspected image/source limits remain; unreviewed GPU images are unsupported.",
                "Operator supplies exact running generations and controls termination/recovery. API termination does not prove GPU release."]}
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
                for side, env in zip(('single',) if single else ('baseline', 'candidate'), envs):
                    options = prepared[2][side]
                    expected = get_provider(env.engine.name).expected_image_entrypoint(options)
                    if options['image'] == image and expected is not None:
                        record(f'entrypoint:{image}', info.get('entrypoint') == expected,
                               'local image entrypoint must match the selected engine launch provider')
            except Exception as exc:
                record(f"image:{image}", False, f"local image unavailable ({type(exc).__name__})")
        for side, env in zip(("single",) if single else ("baseline", "candidate"), envs):
            if env.runtime.gpu_count == 0:
                cpu = env.runtime.cpu
                record(f"hardware:{side}", cpu is not None and cpu.system == "Linux" and normalize(cpu.architecture) == arch,
                       "explicit CPU profile must match daemon architecture; other profile fields remain declared")
            else:
                record(f"hardware:{side}", bool(env.runtime.gpu_model and env.runtime.driver),
                       "GPU and driver must be declared; actual device availability requires engine validation")
        bounded = {side: get_provider(env.engine.name).resource_limits(prepared[2][side])
                   for side, env in zip(('single',) if single else ('baseline', 'candidate'), envs)}
        bounded = {side: limits for side, limits in bounded.items() if limits is not None}
        if bounded:
            try:
                capacity = docker.daemon_resources(timeout=15)
                for side, limits in bounded.items():
                    memory, cpus = capacity.get("memory_bytes"), capacity.get("cpus")
                    ok = (type(memory) is int and memory >= limits.memory_bytes
                          and type(cpus) is int and cpus >= limits.cpus)
                    record(f"resources:{side}", ok,
                           "CPU/RAM bounds must fit Linux Docker capacity; current free memory, swap and competing containers remain unmeasured")
            except Exception as exc:
                record("resources", False, f"Linux Docker capacity unavailable ({type(exc).__name__}); check Docker Desktop/WSL2 memory allocation")
    if not single and envs[0].engine.image_digest == envs[1].engine.image_digest:
        record("image-pair", False, "use different image digests for an update validation")
    return {"ready": all(c["ok"] for c in checks), "checks": checks, "check_support": check_support,
            "limitations": ["Preparation only: no engine, security verdict or performance was measured.",
                            "ready describes input/host preparation; per-check static support is separate and does not remove checks or prove challenge completion.",
                            "CPU model/kernel/threads and GPU availability require verification on the test host.",
                            "On Windows, use Docker Desktop Linux containers and WSL2 integration; readiness does not start or reconfigure them.",
                            "Container RAM/shm/swap bounds are ceilings, not reservations or a guarantee that the model fits; preserve unrelated containers."]}


def validation_readiness(pack, env, policy, cfg: dict) -> dict:
    return update_readiness(pack, [env], policy, cfg, single=True)
