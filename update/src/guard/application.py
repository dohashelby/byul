"""Product update orchestration; core stays independent of engine lifecycle."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache, partial
import json
import os
from pathlib import Path
import re
import secrets

from guard.adapters.model_inventory import inventory
from guard.adapters.engine_provider import get_provider
from guard.configuration import (CILIUM_EGRESS_STATUS_SCOPE_ERROR, COLLECTION_STATUS_SNAPSHOT_SCOPE_ERROR,
                                 GATEWAY_RECORD_SNAPSHOT_OPTION,
                                 INSTALLED_PLUGIN_INVENTORY_SCOPE_ERROR,
                                 KUBERNETES_BOUND_CHECK_OPTIONS,
                                 NORMAL_COMPILE_CACHE_SCOPE_ERROR, NORMAL_FILE_READS_SCOPE_ERROR,
                                 NORMAL_GRAPH_CAPTURE_SCOPE_ERROR,
                                 NORMAL_REQUEST_LATENCY_SCOPE_ERROR, NORMAL_SERVING_READINESS_SCOPE_ERROR,
                                 NORMAL_SAFETENSORS_READER_SCOPE_ERROR,
                                 NORMAL_STARTUP_LOAD_SCOPE_ERROR,
                                 RETRY_RECORD_SNAPSHOT_SCOPE_ERROR,
                                 for_side, merge_config, validate_requested_config,
                                 bounded_metadata_scopes, cilium_egress_status_requested, collection_status_snapshot_requested, for_check,
                                 installed_plugin_inventory_requested,
                                 normal_compile_cache_requested,
                                 normal_file_reads_requested,
                                 normal_graph_capture_requested,
                                 normal_request_latency_requested,
                                 normal_safetensors_reader_requested,
                                 normal_serving_readiness_requested,
                                 normal_startup_load_requested,
                                 normal_token_input_requested, retry_record_snapshot_requested, update_allowed_checks)
from guard.core.judge import judge_plan_locked
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan_locked, validate_update_run_config
from guard.core.execution_recipe import configuration_sha256, execution_lock, validate_policy_time
from guard.core.select import select
from guard.executors import passive_api_hop
from guard.collectors.change import read_change, resolve_plan, save_change
from guard.core.change_selection import build_context
from guard.input_matrix_policy import PROFILE as INPUT_MATRIX_PROFILE
from guard.models import Evidence, ExecStatus, Mode, Side, new_id, sha256_hex, utcnow
from guard.models.objects import Artifact
from guard.store.packs import put_pack
from guard.runtime.docker import Docker
from guard.runtime import generation_readiness, sandbox
from guard.runtime.kubernetes_transition import KubernetesTransition


def _executor_config(effective: dict) -> RunConfig:
    return RunConfig(executors=effective.get("executors", {}),
                     adapter=effective.get("adapter", {}).get("name"), raw=effective)


def run_config(cfg: dict, *, side_settings: dict[str, dict] | None = None) -> RunConfig:
    validate_requested_config(cfg)
    rc = _executor_config(cfg)
    rc.requested = deepcopy(cfg)
    if cfg.get("sides"):
        if side_settings is None:
            side_settings = {side: for_side(cfg, side) for side in ("baseline", "candidate")}
        rc.side_configs = {side: _executor_config(effective) for side, effective in side_settings.items()}
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


def _sandbox_profile_supported(options):
    return (options.get("topology", "isolated") in {"isolated", "exposed"}
            and options.get("gateway_profile", "single") in sandbox.GATEWAY_PROFILES
            and options.get("engine_profile", "default") in sandbox.ENGINE_PROFILES)


def _kubernetes_transition(envs, side_configs, options):
    return KubernetesTransition(
        envs, side_configs, timeout=options.get("transition_timeout", 300),
        poll_interval=options.get("transition_poll_interval", 0.25),
        candidate_binding=options.get("kubernetes_candidate_binding", "prebound"),
    )


def _kubernetes_transition_configurations(side_cfgs):
    """The scoped REQ-09 observer uses a bound target without a lifecycle transition."""
    if any(normal_token_input_requested(c) for c in side_cfgs.values()):
        # Even an invalid request must reach the scope validator without
        # implicitly activating legacy input validation.
        side_cfgs = {side: {**c, "executors": {name: impl for name, impl in c.get("executors", {}).items()
                                              if name != "input_validation"}}
                     for side, c in side_cfgs.items()}
    names = tuple(name for name in ("request_binding", "input_validation")
                  if any(c.get("executors", {}).get(name) == "kubernetes" for c in side_cfgs.values()))
    return side_cfgs, names


def update_inputs(envs, cfg: dict):
    """Shared input checks; no containers, stores or output directories are created."""
    validate_requested_config(cfg)
    if len(envs) != 2:
        raise ValueError("UPDATE_REQUIRES_TWO_ENVIRONMENTS")
    mode = cfg.get("update", {}).get("mode", "external")
    if mode not in {"external", "managed"}:
        raise ValueError("update.mode must be external or managed")
    side_cfgs = {side: for_side(cfg, side) for side in ("baseline", "candidate")}
    managed = {}
    for side, env in zip(("baseline", "candidate"), envs):
        c = side_cfgs[side]
        if mode == "managed":
            options = c.get("update", {})
            if not _sandbox_profile_supported(options):
                raise ValueError(f"{side}: unsupported sandbox profile")
            if c.get("adapter", {}).get("name") != env.engine.name or not c.get("executors") or any(v != "real" for v in c["executors"].values()):
                raise ValueError("managed update requires the matching engine adapter and real executors")
            if type(c.get("update", {}).get("credential_canary", False)) is not bool:
                raise ValueError("credential_canary must be a boolean")
            managed[side] = _managed_config(env, c)
    if mode == "external" and any("real" in c.get("executors", {}).values() for c in side_cfgs.values()):
        validate_update_run_config(run_config(cfg, side_settings=side_cfgs))
        for side, env in zip(("baseline", "candidate"), envs):
            c = side_cfgs[side]
            targets = {c.get(name, {}).get("target") for name, impl in c.get("executors", {}).items() if impl == "real"}
            for path in targets:
                target = json.loads(Path(path).read_text(encoding="utf-8"))
                actual = target.get("engine", {}).get("image_digest")
                if (target.get("lab") is not False or target.get("observed_version") != env.engine.version
                        or not actual or actual.split("@")[-1] != (env.engine.image_digest or "").split("@")[-1]):
                    raise ValueError(f"{side}: target identity differs from ENV; regenerate/verify target metadata")

    transition_cfgs, transition_executors = _kubernetes_transition_configurations(side_cfgs)
    if transition_executors:
        options = cfg.get("update", {})
        if mode != "external" or options.get("kubernetes_transition", True) is not True:
            raise ValueError("KUBERNETES_TRANSITION_REQUIRED")
        paths = [c.get(transition_executors[0], {}).get("target") for c in side_cfgs.values()]
        if not all(paths) or Path(paths[0]).resolve()==Path(paths[1]).resolve():
            raise ValueError("KUBERNETES_TRANSITION_TARGETS_UNVERIFIED")
        try:
            _kubernetes_transition(envs, transition_cfgs, options)
        except Exception:
            raise ValueError("KUBERNETES_TRANSITION_CONFIGURATION_INVALID") from None
    return mode, side_cfgs, managed


def _update_config_for_execution(pack, cfg, allowed_checks):
    if allowed_checks is None:
        return cfg
    allowed_executors = {pack.checks[cid].executor for cid in allowed_checks}
    scoped = deepcopy(cfg)
    for settings in (scoped, *scoped.get("sides", {}).values()):
        if "executors" in settings:
            settings["executors"] = {name: impl for name, impl in settings["executors"].items()
                                      if name in allowed_executors}
    return scoped


def _validate_requested_normal_scopes(cfg, pack, allowed_checks):
    sides = cfg.get("sides", {})
    if (cilium_egress_status_requested(cfg) or installed_plugin_inventory_requested(cfg)
            or collection_status_snapshot_requested(cfg)
            or normal_compile_cache_requested(cfg)
            or normal_file_reads_requested(cfg)
            or normal_graph_capture_requested(cfg)
            or normal_request_latency_requested(cfg)
            or normal_safetensors_reader_requested(cfg)
            or normal_serving_readiness_requested(cfg)
            or normal_startup_load_requested(cfg)
            or normal_token_input_requested(cfg)
            or retry_record_snapshot_requested(cfg)
            or any(normal_token_input_requested(raw) for raw in sides.values())):
        bounded_metadata_scopes((merge_config(cfg, sides.get(side, {})) for side in ("baseline", "candidate")),
                                pack, allowed_checks)


def _bounded_kubernetes_targets(raw, selected):
    paths = {}
    for check_id, option in KUBERNETES_BOUND_CHECK_OPTIONS.items():
        name = selected.get(check_id)
        if raw.get("executors", {}).get(name) != "kubernetes":
            continue
        try:
            effective = for_check(raw, name, check_id).get(name, {})
        except ValueError:
            continue  # Per-check execution reports the malformed configuration.
        if type(effective) is not dict or effective.get(option) is not True:
            continue
        section = effective.get("normal") if check_id == "SUP-05" else effective
        target = section.get("target") if type(section) is dict else None
        paths[check_id] = Path(target) if isinstance(target, (str, Path)) else None
    return paths


def _pin_prebound_kubernetes_targets(raw, env, paths, target_snapshots):
    """Derive bounded target identity from the recipe's exact captured bytes."""
    hashes = raw["_kubernetes_target_sha256_by_check"] = dict.fromkeys(paths)
    for check_id, path in paths.items():
        content = target_snapshots.get(path)
        if content is None:
            continue
        try:
            gateway_snapshot = (check_id == "OBS-01"
                and for_check(raw, "required_event_record", check_id).get("required_event_record", {}).get(
                    GATEWAY_RECORD_SNAPSHOT_OPTION) is True)
            hashes[check_id] = _scoped_target_sha256(
                content, env, scoped_check=check_id, gateway_snapshot=gateway_snapshot)
        except ValueError:
            continue  # The scoped executor will withhold dispatch without a verified hash.


def _scoped_target_sha256(content, env, *, gateway_snapshot=False, scoped_check=None):
    from guard.models import canonical_json, sha256_hex
    from guard.runtime.bounded import strict_json
    from guard.runtime.kubernetes_http import KubernetesBindingError, _validate as validate_target_binding

    try:
        target = strict_json(content, limit=65536)
        if scoped_check == "SUP-05":
            from guard.runtime.kubernetes_binding import validate_capture_target
            validate_capture_target(target, env)
            return sha256_hex(canonical_json(target))
        binding, _ = validate_target_binding(target, env)
        if target["lab"] is False and target.get("environment_ref") == env.ref().model_dump(mode="json"):
            if gateway_snapshot:
                from guard.executors.kubernetes_gateway_observation import snapshot_owner
                snapshot_owner(target, binding)
            if scoped_check == "LIF-01":
                from guard.executors.kubernetes_normal_startup_load import normal_startup_load_transport
                if get_provider(env.engine.name).evidence_contract("startup_load_source", env) is None:
                    raise ValueError("LIF01_BOOT_SOURCE_UNQUALIFIED")
                normal_startup_load_transport(target, env, duration=1)
            elif scoped_check == "LIF-03":
                from guard.executors.kubernetes_normal_request_latency import normal_request_latency_transport
                normal_request_latency_transport(target, env, duration=1)
            elif scoped_check == "OBS-02":
                from guard.executors.kubernetes_normal_serving_readiness import normal_serving_readiness_transport
                normal_serving_readiness_transport(target, env, duration=1)
            elif scoped_check == "PLT-07":
                from guard.executors.kubernetes_passive_egress_policy import passive_egress_policy_transport
                passive_egress_policy_transport(target, env, duration=1)
            elif scoped_check == "REQ-09":
                from guard.executors.kubernetes_normal_token_input import normal_input_transports
                normal_input_transports(target, env, duration=1)
            elif scoped_check == "REQ-20":
                from guard.executors.kubernetes_normal_graph_capture import normal_graph_capture_transport
                if get_provider(env.engine.name).evidence_contract("graph_capture_source", env) is None:
                    raise ValueError("REQ20_CAPTURE_SOURCE_UNQUALIFIED")
                normal_graph_capture_transport(target, env, duration=1)
            elif scoped_check == "SUP-07":
                from guard.executors.kubernetes_normal_compile_cache import normal_compile_cache_transport
                if get_provider(env.engine.name).evidence_contract("compile_cache_source", env) is None:
                    raise ValueError("SUP07_COMPILE_CACHE_SOURCE_UNQUALIFIED")
                normal_compile_cache_transport(target, env, duration=1)
            elif scoped_check == "SUP-10":
                from guard.executors.kubernetes_installed_plugin_inventory import installed_plugin_inventory_transport
                installed_plugin_inventory_transport(target, env, duration=1)
            elif scoped_check == "SUP-12":
                from guard.executors.kubernetes_normal_safetensors_reader import normal_safetensors_reader_transport
                if get_provider(env.engine.name).evidence_contract("safetensors_reader_source", env) is None:
                    raise ValueError("SUP12_SAFETENSORS_READER_SOURCE_UNQUALIFIED")
                normal_safetensors_reader_transport(target, env, duration=1)
            return sha256_hex(canonical_json(target))
    except (KeyError, TypeError, ValueError, KubernetesBindingError):
        pass
    raise ValueError("KUBERNETES_SCOPED_TARGET_INVALID")


def _prepare_update_inputs(pack, envs, cfg):
    validate_requested_config(cfg)
    allowed_checks = update_allowed_checks(cfg, pack)
    _validate_requested_normal_scopes(cfg, pack, allowed_checks)
    prepared = update_inputs(envs, _update_config_for_execution(pack, cfg, allowed_checks))
    bounded_metadata_scopes(prepared[1].values(), pack, allowed_checks)
    return prepared, allowed_checks


def execute_update(store, pack, envs, policy, cfg: dict, run_dir: Path, as_of: datetime,
                   *, change=None, previous_plan=None):
    """Select and execute two sides under one attempt, then publish its report."""
    from guard.core.execution_recipe import bind_recipe
    validate_policy_time(policy)
    cfg = deepcopy(cfg)
    prepared, allowed_checks = _prepare_update_inputs(pack, envs, cfg)
    change_input = read_change(change, pack, store)
    previous = resolve_plan(store, previous_plan)
    preview = None
    if change_input is not None or previous is not None:
        preview = build_context(store, previous, pack, envs, policy, cfg,
                                change_input.change if change_input else None, mode=Mode.update, as_of=as_of, read_only=True)
    _validate_canary_policy(prepared[1].values(), policy, "update")
    # No input or historical evidence is overwritten.
    run_dir.mkdir(parents=True, exist_ok=False)
    put_pack(store, pack.path)
    for e in envs:
        store.put(e)
    store.put(policy)
    chg = save_change(store, change_input) if change_input is not None else None
    context = build_context(store, previous, pack, envs, policy, cfg, chg, mode=Mode.update,
                            as_of=as_of) if chg is not None or previous is not None else None
    if preview and context and preview.execution_recipe_sha256 != context.execution_recipe_sha256:
        raise ValueError('EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED')
    plan = (select(pack, envs, policy, change=chg, mode=Mode.update, context=context)
            if context is not None else select(pack, envs, policy, mode=Mode.update))
    passive_api_hop.validate_scope(pack, prepared[1].values(), allowed_checks, plan=plan,
                                  explicitly_requested=passive_api_hop.requested(cfg))
    plan = bind_recipe(plan, cfg, envs, from_selection_context=context is not None)
    store.put(plan)
    _record_plan(run_dir, plan.id)
    with execution_lock(store.root, plan.id):
        execution = _run_update_trial(store, pack, plan, envs, cfg, run_dir, prepared)
        return _finish_run(store, pack, plan, as_of, *execution)


def _validate_canary_policy(configs, policy, section):
    if any(c.get(section, {}).get("credential_canary", False) for c in configs) and policy.status.value != "DRAFT":
        raise ValueError("credential_canary is only available with a DRAFT sandbox policy")


def execute_saved_update(store, pack, plan, cfg: dict, run_dir: Path, *, replace_manual: bool = False):
    """Run an existing paired PLAN through the same lifecycle without judging it."""
    if plan.mode != Mode.update or plan.inputs.pack != pack.ref():
        raise ValueError("UPDATE_PLAN_REQUIRED")
    store.validate_object(plan)
    policy = store.resolve(plan.inputs.policy)
    validate_policy_time(policy)
    envs = [store.resolve(ref) for ref in plan.inputs.environments]
    cfg = deepcopy(cfg)
    prepared, allowed_checks = _prepare_update_inputs(pack, envs, cfg)
    passive_api_hop.validate_scope(pack, prepared[1].values(), allowed_checks, plan=plan,
                                  explicitly_requested=passive_api_hop.requested(cfg))
    _validate_canary_policy(prepared[1].values(), policy, "update")
    with execution_lock(store.root, plan.id):
        run_dir.mkdir(parents=True, exist_ok=False)
        _record_plan(run_dir, plan.id)
        evidence, _, errors, _, _ = _run_update_trial(
            store, pack, plan, envs, cfg, run_dir, prepared, replace_manual=replace_manual)
        return evidence, _execution_failed(pack, evidence, errors)


def _run_update_trial(store, pack, plan, envs, cfg, run_dir, prepared, *, replace_manual=False):
    from guard.runtime.resource_lease import provider_leases
    if not any(item.selected for item in plan.items):
        return [], None, [], [], []
    expected = plan.inputs.execution_recipe_sha256
    if expected is None and any(impl in {"real", "kubernetes"}
                                for side in prepared[1].values() for impl in side.get("executors", {}).values()):
        raise ValueError("EXECUTION_RECIPE_MISSING_RESELECT_REQUIRED")
    mode, side_cfgs, _ = prepared
    allowed_checks = update_allowed_checks(cfg, pack)
    target_paths, target_snapshots = {}, {}
    if mode == "external" and allowed_checks is not None:
        selected = {item.check_id: item.executor for item in plan.items if item.selected and item.check_id in allowed_checks}
        target_paths = {side: _bounded_kubernetes_targets(raw, selected) for side, raw in side_cfgs.items()}
        if cfg.get("update", {}).get("kubernetes_candidate_binding", "prebound") == "prebound":
            target_snapshots = {path: None for paths in target_paths.values() for path in paths.values() if path is not None}
    normal_file_hashes = {} if normal_file_reads_requested(cfg) else None
    if expected is not None and configuration_sha256(cfg, envs, plan=plan, target_snapshots=target_snapshots,
                                                      normal_file_hashes=normal_file_hashes) != expected:
        raise ValueError('EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED')
    options = [prepared[2].get(side, {}) for side in ('baseline', 'candidate')]
    with provider_leases(envs, options) as leases:
        return _run_update_sides(store, pack, plan, envs, cfg, run_dir, prepared, leases, replace_manual,
                                 target_paths, target_snapshots, normal_file_hashes)


def _record_plan(run_dir: Path, plan_id: str):
    (run_dir / "plan.txt").write_text(plan_id + "\n", encoding="utf-8")


def _record_attempt(run_dir: Path, attempt_id: str):
    with (run_dir / "attempt.txt").open("x", encoding="utf-8") as stream:
        stream.write(attempt_id + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _run_update_sides(store, pack, plan, envs, cfg, run_dir, prepared, leases, replace_manual,
                      target_paths, target_snapshots, normal_file_hashes):
    mode, side_cfgs, managed = prepared
    rc = RunConfig(executors=cfg.get("executors", {}), requested=deepcopy(cfg),
                   attempt_id=new_id('ATTEMPT'), replace_manual=replace_manual)
    _record_attempt(run_dir, rc.attempt_id)
    for side, c in side_cfgs.items():
        if mode == "managed":
            for name in c.get("executors", {}):
                c.setdefault(name, {})["target"] = str(run_dir / side / "target.json")
        rc.side_configs[side] = _executor_config(c)
        if normal_file_hashes is not None and "SUP-05" in target_paths.get(side, {}):
            normal = for_check(c, "supply_boot", "SUP-05")["supply_boot"]["normal"]
            rc.side_configs[side].normal_file_read_sha256 = {
                key: normal_file_hashes.get(normal[key]) for key in ("prepared", "reference_manifest")
            }
    # The common-input implementation only reads saved records. Its partial
    # baseline scope is independent of the candidate's separately bound input.
    offline_comparison = mode == "external" and all(
        set(c.executors.values()) == {"common-input"} for c in rc.side_configs.values()
    )
    result = _TrialResult()

    def dispatch(side):
        result.evidence.extend(run_plan_locked(store, pack, plan, rc, only_side=side))
    kube_transition = None
    transition_cfgs, transition_executors = _kubernetes_transition_configurations(side_cfgs)
    if transition_executors:
        kube_transition = _kubernetes_transition(envs, transition_cfgs, cfg.get("update", {}))
    candidate_blocked = None
    for side, env in zip((Side.baseline, Side.candidate), envs):
        side_cfg = rc.side_configs[side.value]
        if side == Side.candidate and candidate_blocked:
            side_cfg.abort_reason = candidate_blocked
            dispatch(side)
            continue
        if mode == "external":
            if kube_transition is not None:
                try:
                    if side==Side.baseline:
                        kube_transition.prepare_baseline()
                    else:
                        previous_evidence = [e for e in result.evidence if e.side == Side.baseline
                                             and e.execution.attempt_id == rc.attempt_id]
                        if not _baseline_ran_without_abort(previous_evidence):
                            raise ValueError("BASELINE_EXECUTION_FAILED")
                        kube_transition.candidate_ready()
                    side_cfg.kubernetes_target_pins = {
                        name:kube_transition.transports[side.value].target_sha256
                        for name in kube_transition.executors}
                except Exception:
                    side_cfg.abort_reason="KUBERNETES_TRANSITION_UNVERIFIED"
                    result.errors.append(f"{side.value}: KUBERNETES_TRANSITION_UNVERIFIED; operator recovery required; GPU release unverified")
            if target_paths.get(side.value):
                _pin_prebound_kubernetes_targets(side_cfg.raw, env, target_paths[side.value], target_snapshots)
            dispatch(side)
            if side == Side.baseline and any(
                e.execution.cleanup_errors or
                (e.execution.status == ExecStatus.ABORTED and not offline_comparison) for e in result.evidence
            ):
                candidate_blocked = 'BASELINE_EXECUTION_FAILED'
            continue
        options = side_cfg.raw.get("update", {})
        effective, keys = _credentials(side_cfg.raw, options.get("gateway_profile", "single"))
        side_cfg.runtime_environ = effective.pop("_runtime_environ", None)
        side_cfg.raw = effective
        release_unverified = _run_managed_side(
            store, pack, plan, rc, env, options, managed[side.value], keys, run_dir / side.value,
            "u" + secrets.token_hex(6), leases, result, only_side=side,
            prepare_sentinel=lambda: _managed_sentinel_dir(run_dir, options.get("engine_profile", "default"), side.value),
        )
        if side == Side.baseline and release_unverified:
            candidate_blocked = 'BASELINE_RESOURCE_RELEASE_UNVERIFIED'
        if side == Side.baseline and candidate_blocked is None and _execution_failed(pack, result.evidence, ()):
            candidate_blocked = 'BASELINE_EXECUTION_FAILED'
    if kube_transition is not None:
        from guard.models import canonical_json
        try:
            artifact = _diagnostic_artifact(store, "kubernetes-transition.json", canonical_json(kube_transition.report()))
            result.diagnostics.append(artifact)
            result.diagnostic_refs.append(f"Kubernetes transition diagnostic (not EVD): {artifact.path}; GPU release unverified")
        except Exception:
            result.errors.append("KUBERNETES_TRANSITION_DIAGNOSTIC_STORE_UNAVAILABLE")
    return result.execution(rc.attempt_id)


@dataclass
class _TrialResult:
    evidence: list[Evidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    diagnostic_refs: list[str] = field(default_factory=list)
    diagnostics: list[Artifact] = field(default_factory=list)

    def execution(self, attempt_id):
        return self.evidence, attempt_id, self.errors, self.diagnostic_refs, self.diagnostics


def _run_managed_side(store, pack, plan, rc, env, options, managed, keys, target_dir, run_id, leases,
                      result, *, prepare_sentinel, only_side=None) -> bool:
    """Launch and verify one owned environment, retaining evidence through cleanup."""
    from guard.adapters.engine_provider import get_provider

    side = only_side or Side.single
    completed = False
    engine_profile = options.get("engine_profile", "default")
    for lease in leases:
        lease.note_owner(run_id)
    try:
        with Docker(run_id) as docker:
            try:
                sentinel_dir = prepare_sentinel()
                target = sandbox.up(
                    docker, options.get("topology", "isolated"), target_dir, keys.get("GUARD_ALLOWED_KEY"),
                    engine=env.engine.name, **get_provider(env.engine.name).sandbox_arguments(managed),
                    gateway_profile=options.get("gateway_profile", "single"), gateway_keys=keys,
                    engine_profile=engine_profile, sentinel_dir=sentinel_dir,
                    credential_canary=options.get("credential_canary", False),
                )
                if target.get("observed_version") != env.engine.version:
                    raise ValueError("observed engine version differs from ENV")
                observed = target.get("images", {}).get(managed["image"], "")
                if observed.split("@")[-1] != env.engine.image_digest.split("@")[-1]:
                    raise ValueError("observed image digest differs from ENV")
                result.evidence.extend(run_plan_locked(store, pack, plan, rc, only_side=only_side))
                completed = True
            finally:
                result.diagnostic_refs.append(_managed_diagnostic_reference(
                    docker, target_dir, keys, side.value, store=store, diagnostics=result.diagnostics))
    except Exception as exc:
        # Keep the exception type as detail so engine credentials cannot enter the report.
        error_type = type(exc).__name__
        release_unverified = completed or _has_cleanup_note(exc, run_id)
        if completed:
            result.errors.append(f"{side.value}: cleanup incomplete ({error_type}); guard.run={run_id}")
        else:
            result.errors.append(
                f"{side.value}: startup/execution interrupted ({error_type}); sealed evidence retained; guard.run={run_id}")
            if release_unverified:
                result.errors.append(
                    f"{side.value}: cleanup incomplete after startup/execution failure ({error_type}); guard.run={run_id}")
        if release_unverified:
            for lease in leases:
                lease.mark_uncertain(run_id)
        if not completed:
            side_cfg = rc.side_configs[side.value] if only_side is not None else rc
            side_cfg.abort_reason = f"managed startup/execution failed: {error_type}"
            result.evidence.extend(run_plan_locked(store, pack, plan, rc, only_side=only_side))
        return release_unverified
    return False


def _execution_failed(pack, evidence, lifecycle_errors):
    return bool(lifecycle_errors) or any(
        e.execution.cleanup_errors or e.execution.status == ExecStatus.ABORTED
        or (e.execution.status == ExecStatus.NOT_RUN and pack.checks[e.check_id].executor is not None)
        for e in evidence)


def _finish_run(store, pack, plan, as_of, evidence, attempt_id, lifecycle_errors, diagnostic_refs, diagnostics):
    results = judge_plan_locked(store, pack, plan, as_of, evidence=evidence)
    report = build_report(store, pack, plan, as_of, attempt_id=attempt_id, evidence=evidence, judged=results)
    if lifecycle_errors or diagnostic_refs or diagnostics:
        report = report.model_copy(update={"limitations": [*report.limitations, *lifecycle_errors, *diagnostic_refs],
                                           "diagnostic_artifacts": diagnostics or None}).seal()
    store.put(report)
    return report, _execution_failed(pack, evidence, lifecycle_errors)


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


def _diagnostic_artifact(store, name: str, data: bytes) -> Artifact:
    stored = store.put_artifact(data)
    return Artifact(name=name, path=stored.path, sha256=stored.sha256, bytes=stored.bytes,
                    collected_at=utcnow(), collector="guard-lifecycle-diagnostic/1")


def _managed_diagnostic_reference(docker, folder: Path, keys: dict, side: str, *, store, diagnostics) -> str:
    """A separate diagnostic reference, never an EVD or a verdict input."""
    def preserve(saved, label):
        filename = saved["path"]
        if Path(filename).name != filename:
            raise ValueError("DIAGNOSTIC_PATH_INVALID")
        path = folder / filename
        if path.is_symlink():
            raise ValueError("DIAGNOSTIC_PATH_INVALID")
        data = path.read_bytes()
        if sha256_hex(data) != saved["sha256"]:
            raise ValueError("DIAGNOSTIC_HASH_MISMATCH")
        artifact = _diagnostic_artifact(store, f"{side}-{filename}", data)
        diagnostics.append(artifact)
        return f"{side}: {label} (not EVD): {artifact.path} sha256={artifact.sha256}"

    try:
        saved = sandbox.capture_diagnostics(docker, folder, secret_values=list(keys.values()), phase="run-final")
        if saved.get("path") == "managed-diagnostic.json" and re.fullmatch(r"[0-9a-f]{64}", saved.get("sha256", "")):
            detail = preserve(saved, "separate run diagnostic")
        else:
            detail = f"{side}: separate run diagnostic unavailable ({saved.get('error_type', 'UnknownError')})"
    except Exception as exc:
        detail = f"{side}: separate run diagnostic unavailable ({type(exc).__name__})"
    try:
        saved = generation_readiness.reference(folder)
        if saved is not None:
            detail += "; " + preserve(saved, "separate startup generation")
    except Exception as exc:
        detail += f"; separate startup generation unavailable ({type(exc).__name__})"
    return detail


def _validation_inputs(env, cfg: dict):
    """One managed engine, using the same launch contract as update."""
    validate_requested_config(cfg)
    if passive_api_hop.requested(cfg):
        raise ValueError("PASSIVE_API_HOP_EXECUTION_SCOPE_INVALID")
    if cilium_egress_status_requested(cfg):
        raise ValueError(CILIUM_EGRESS_STATUS_SCOPE_ERROR)
    if collection_status_snapshot_requested(cfg):
        raise ValueError(COLLECTION_STATUS_SNAPSHOT_SCOPE_ERROR)
    if retry_record_snapshot_requested(cfg):
        raise ValueError(RETRY_RECORD_SNAPSHOT_SCOPE_ERROR)
    if installed_plugin_inventory_requested(cfg):
        raise ValueError(INSTALLED_PLUGIN_INVENTORY_SCOPE_ERROR)
    if normal_compile_cache_requested(cfg):
        raise ValueError(NORMAL_COMPILE_CACHE_SCOPE_ERROR)
    if normal_file_reads_requested(cfg):
        raise ValueError(NORMAL_FILE_READS_SCOPE_ERROR)
    if normal_graph_capture_requested(cfg):
        raise ValueError(NORMAL_GRAPH_CAPTURE_SCOPE_ERROR)
    if normal_safetensors_reader_requested(cfg):
        raise ValueError(NORMAL_SAFETENSORS_READER_SCOPE_ERROR)
    if normal_request_latency_requested(cfg):
        raise ValueError(NORMAL_REQUEST_LATENCY_SCOPE_ERROR)
    if normal_serving_readiness_requested(cfg):
        raise ValueError(NORMAL_SERVING_READINESS_SCOPE_ERROR)
    if normal_startup_load_requested(cfg):
        raise ValueError(NORMAL_STARTUP_LOAD_SCOPE_ERROR)
    if normal_token_input_requested(cfg):
        raise ValueError("NORMAL_TOKEN_INPUT_EXECUTION_SCOPE_INVALID")
    effective = deepcopy(cfg)
    options = effective.get("validation", {})
    if type(options) is not dict or type(options.get("credential_canary", False)) is not bool:
        raise ValueError("validation settings must be a table and credential_canary a boolean")
    if options.get("mode", "managed") != "managed" or effective.get("sides"):
        raise ValueError("validate supports one managed environment without side overrides")
    if not _sandbox_profile_supported(options):
        raise ValueError("unsupported validation sandbox profile")
    if (effective.get("adapter", {}).get("name") != env.engine.name or not effective.get("executors")
            or any(value != "real" for value in effective["executors"].values())):
        raise ValueError("validate requires the matching engine adapter and real executors")
    return effective, _managed_config(env, effective)


def execute_validation(store, pack, env, policy, cfg: dict, run_dir: Path, as_of: datetime):
    """Audit a single managed engine; retain evidence when teardown fails."""
    from guard.core.preflight import validate_pack
    from guard.core.execution_recipe import bind_recipe
    from guard.runtime.resource_lease import provider_leases

    validate_policy_time(policy)
    validate_pack(pack)
    cfg = deepcopy(cfg)
    effective, managed = _validation_inputs(env, cfg)
    options = effective.get("validation", {})
    _validate_canary_policy((effective,), policy, "validation")
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
    _record_plan(run_dir, plan.id)
    with execution_lock(store.root, plan.id):
        with provider_leases([env], [managed]) as leases:
            execution = _run_validation_trial(store, pack, plan, env, cfg, run_dir, effective, managed, keys, leases)
        return _finish_run(store, pack, plan, as_of, *execution)


def _run_validation_trial(store, pack, plan, env, cfg, run_dir, effective, managed, keys, leases):
    options = effective.get("validation", {})
    target_dir = run_dir / "single"
    sentinel_dir = _managed_sentinel_dir(run_dir, options.get("engine_profile", "default"))
    for name in effective["executors"]:
        effective.setdefault(name, {})["target"] = str(target_dir / "target.json")
    rc = RunConfig(executors=effective["executors"], adapter=env.engine.name, raw=effective, requested=deepcopy(cfg),
                   attempt_id=new_id("ATTEMPT"), runtime_environ=effective.pop("_runtime_environ", None))
    _record_attempt(run_dir, rc.attempt_id)

    result = _TrialResult()
    _run_managed_side(
        store, pack, plan, rc, env, options, managed, keys, target_dir,
        "v" + secrets.token_hex(6), leases, result, prepare_sentinel=lambda: sentinel_dir,
    )
    return result.execution(rc.attempt_id)


def update_readiness(pack, envs, policy, cfg: dict, *, single: bool = False,
                     change=None, previous_plan=None, source_store=None) -> dict:
    """Read-only preparation check of the full plan and explicit static Kubernetes scope."""
    from guard.executors import registry, run_binding
    from guard.executors.kubernetes_initialization import KubernetesInitializationExecutor
    from guard.executors.kubernetes_installed_plugin_inventory import KubernetesInstalledPluginInventoryExecutor
    from guard.executors.kubernetes_ipc_product import KubernetesIPCProductExecutor
    from guard.executors.kubernetes_log_integrity import KubernetesLogIntegrityExecutor
    from guard.executors.kubernetes_normal_compile_cache import KubernetesNormalCompileCacheExecutor
    from guard.executors.kubernetes_normal_graph_capture import KubernetesNormalGraphCaptureExecutor
    from guard.executors.kubernetes_normal_request_latency import KubernetesNormalRequestLatencyExecutor
    from guard.executors.kubernetes_normal_safetensors_reader import KubernetesNormalSafetensorsReaderExecutor
    from guard.executors.kubernetes_normal_serving_readiness import KubernetesNormalServingReadinessExecutor
    from guard.executors.kubernetes_normal_startup_load import KubernetesNormalStartupLoadExecutor
    from guard.executors.kubernetes_normal_token_input import KubernetesNormalTokenInputExecutor
    from guard.executors.kubernetes_observation import KubernetesRequiredEventExecutor
    from guard.executors.kubernetes_passive_egress_policy import KubernetesPassiveEgressPolicyExecutor
    from guard.executors.kubernetes_record_access_product import KubernetesRecordAccessExecutor
    from guard.executors.kubernetes_request_termination import KubernetesRequestTerminationExecutor
    from guard.executors.kubernetes_response_content import KubernetesResponseContentExecutor
    from guard.executors.kubernetes_sampling import KubernetesSamplingExecutor
    from guard.executors.kubernetes_stream_buffer import KubernetesStreamBufferExecutor
    from guard.executors.kubernetes_supply_boot import KubernetesSupplyBootExecutor
    from guard.executors.lifecycle_transition import (LifecycleTransitionExecutor, REDEPLOY_CHECKS, REDEPLOY_SIDE_REUSE,
                                                      generation_identity, redeployment_side_reuse)
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
        check = {"check": name, "ok": bool(ok), "detail": detail}
        checks.append(check)
        return check

    def error_detail(exc):
        # Validation errors contain field descriptions; daemon output is never copied.
        return str(exc) if isinstance(exc, ValueError) else type(exc).__name__

    prepared = None
    execution_scope = None
    scope_exclusion = None
    check_support = []
    selected_ids = []

    def result(limitations):
        ready = all(c["ok"] for c in checks)
        sides = ("single",) if single else ("baseline", "candidate")
        required = {(side, check) for side in sides for check in selected_ids}
        supported = {(r["side"], r["check_id"]) for r in check_support if r["status"] == "SUPPORTED"}
        return {"ready": ready, "execution_ready": ready and required <= supported, "selected_checks": selected_ids,
                "checks": checks, "check_support": check_support, "limitations": limitations}

    kube_only = False
    transition_requested = False
    generation_preflight = set()
    try:
        validate_policy_time(policy)
        allowed_checks = None if single else update_allowed_checks(cfg, pack)
        prebound = not single and cfg.get("update", {}).get("kubernetes_candidate_binding", "prebound") == "prebound"
        execution_config = _update_config_for_execution(pack, cfg, allowed_checks)
        if not single and cfg.get("update", {}).get("mode", "external") == "external":
            requested = run_config(execution_config)
            sides = requested.side_configs.values() if requested.side_configs else (requested,)
            implementations = [impl for side in sides for impl in side.executors.values()]
            kube_only = all(impl == "kubernetes" for impl in implementations) and (
                bool(implementations) or allowed_checks is not None)
            if kube_only:
                effective_sides = ({side: config.raw for side, config in requested.side_configs.items()}
                                   or {"baseline": requested.raw})
                transition_requested = bool(_kubernetes_transition_configurations(effective_sides)[1])
        validate_pack(pack)
        if single:
            effective, managed = _validation_inputs(envs[0], cfg)
            prepared = ("managed", {"single": effective}, {"single": managed})
        else:
            _validate_requested_normal_scopes(cfg, pack, allowed_checks)
            prepared = update_inputs(envs, execution_config)
            bounded_metadata_scopes(prepared[1].values(), pack, allowed_checks)
        option_group = "validation" if single else "update"
        _validate_canary_policy(prepared[1].values(), policy, option_group)
        plan_mode = Mode.audit if single else Mode.update
        plan = (select(pack, envs, policy, change=change_input.change if change_input else None,
                       mode=plan_mode, context=context) if context is not None
                else select(pack, envs, policy, mode=plan_mode))
        selected = [item for item in plan.items if item.selected]
        if not selected:
            if not single and any(item.carried_over is not None for item in plan.items):
                record("inputs", True, "Carryover-only PLAN; no new trials are required")
                return result(["Only prior, eligible evidence is used; engine and Docker need not start."])
            raise ValueError("policy selected no checks")
        passive_hop = not single and passive_api_hop.validate_scope(
            pack, prepared[1].values(), allowed_checks, plan=plan,
            explicitly_requested=passive_api_hop.requested(cfg))
        executable = selected if allowed_checks is None else [item for item in selected if item.check_id in allowed_checks]
        if allowed_checks is not None:
            excluded_ids = [item.check_id for item in selected if item.check_id not in allowed_checks]
            if excluded_ids:
                scope_exclusion = record("execution-scope", False,
                    f"{len(excluded_ids)} selected checks are outside the configured update execution scope")
            if kube_only and prebound and not transition_requested:
                execution_scope = {"included_check_ids": [item.check_id for item in executable],
                                   "excluded_check_ids": excluded_ids}
        managed = prepared[0] == "managed"
        selected_ids = [item.check_id for item in selected]
        config_for_check = {} if managed else {side: cache(partial(for_check, c)) for side, c in prepared[1].items()}
        input_errors = []
        failed_support_sides = set()
        side_inputs = list(zip(prepared[1].items(), envs, strict=True))
        for (side, c), env in side_inputs:
            try:
                support_rows = assess_check_support(pack, env, policy, c, selected_check_ids=selected_ids,
                    managed=managed, allowed_checks=allowed_checks, passive_hop=passive_hop,
                    config_for_check=config_for_check.get(side))
            except Exception as exc:
                input_errors.append((side, error_detail(exc)))
                failed_support_sides.add(side)
                continue
            check_support.extend({"side": side, **row} for row in support_rows)
        support_by_key = {(row["side"], row["check_id"]): row for row in check_support}
        for item in executable:
            if item.gaps:
                input_errors.append((item.check_id, f"{item.check_id}: unresolved selection gaps {item.gaps}"))
                continue
            seen_generation_identities = set()
            for (side, side_cfg), selected_env in side_inputs:
                try:
                    c = side_cfg
                    if passive_hop and item.check_id == passive_api_hop.CHECK:
                        passive_api_hop.validate_policy(pack.checks[item.check_id], policy.values.get(item.check_id, {}))
                        passive_api_hop.read_target(c[item.executor]["target"], selected_env)
                        continue
                    implementation = c.get("executors", {}).get(item.executor, "")
                    factory = registry.resolve_executor(item.executor, implementation,
                        engine=selected_env.engine.name, version=selected_env.engine.version)
                    kube = implementation == "kubernetes" and prepared[0] == "external"
                    if factory is None or implementation != "real" and not kube:
                        raise ValueError(f"{side}/{item.check_id}: real executor is required")
                    if managed:
                        # Route role defaults must be in place before applying check overrides.
                        c, _ = _credentials(c, c.get(option_group, {}).get("gateway_profile", "single"))
                        c = for_check(c, item.executor, item.check_id)
                    else:
                        c = config_for_check[side](item.executor, item.check_id)
                    settings = c.get(item.executor, {})
                    check_def = pack.checks[item.check_id]
                    supplied_roles = (kube and run_binding.is_supply_record_binding(check_def)
                                      and cfg.get("update", {}).get("kubernetes_candidate_binding", "prebound") == "prebound")
                    paired_supply_roles = kube and (
                        item.check_id == "SUP-15" or supplied_roles
                        or run_binding.is_paired_supply_role_preflight(check_def, settings, cfg.get("update", {})))
                    run_cfg = run_binding.run_config(c, item.check_id) if supplied_roles else None
                    binding_options = {}
                    if supplied_roles and type(run_cfg) is dict and set(run_cfg) == run_binding.config_keys(check_def):
                        try:
                            digests = run_binding.supply_record_recipe_digests(settings, run_cfg)
                        except run_binding.RunBindingUnavailable:
                            raise
                        except (KeyError, OSError, TypeError, ValueError):
                            raise run_binding.RunBindingUnavailable("RUN_BINDING_UNAVAILABLE") from None
                        binding_options["expected_supply_record_digests"] = digests
                        c = {**c, "_supply_prepared_sha256_by_role": {role: digests[role] for role in ("normal", "challenge")}}
                    option = KUBERNETES_BOUND_CHECK_OPTIONS.get(item.check_id)
                    explicit_observer = (item.check_id in {"LIF-01", "LIF-03", "OBS-02", "OBS-04", "PLT-06", "PLT-07", "REQ-09",
                                                           "REQ-20", "SUP-05", "SUP-07", "SUP-10", "SUP-12"}
                                         and type(settings) is dict
                                         and option in settings)
                    normal_file_read = explicit_observer and item.check_id == "SUP-05"
                    expected_target = None
                    if kube and option and type(settings) is dict and settings.get(option) is True:
                        path = Path(settings["normal"]["target"] if normal_file_read else settings["target"])
                        if not path.is_file() or path.is_symlink():
                            raise ValueError("KUBERNETES_SCOPED_TARGET_INVALID")
                        with path.open("rb") as stream:
                            expected_target = _scoped_target_sha256(stream.read(65537), selected_env,
                                gateway_snapshot=settings.get(GATEWAY_RECORD_SNAPSHOT_OPTION) is True,
                                scoped_check=item.check_id)
                    executor = (registry.build_executor(item.executor, implementation, c,
                                    engine=selected_env.engine.name, version=selected_env.engine.version, factory=factory)
                                if explicit_observer else factory(c))
                    support = {} if side in failed_support_sides else support_by_key[side, item.check_id]
                    # These collectors distinguish a valid policy outside their
                    # bounded profile from malformed input. Still validate launch
                    # settings, budgets and fixtures; never shrink the policy.
                    # Input matrix validation checks the budget against the paired policy.
                    # Its inherited configuration check cannot enforce that constraint.
                    if (support.get("policy_valid") is True and support.get("policy_supported") is False
                            and getattr(check_def, "collection_profile", None) != INPUT_MATRIX_PROFILE
                            and hasattr(executor, "validate_configuration")):
                        executor.validate_configuration(check_def)
                    else:
                        values = policy.values.get(item.check_id, {})
                        if run_binding.keys_for(check_def):
                            # Static preparation computes the same per-run binding guard run will compute.
                            if not supplied_roles:
                                run_cfg = run_binding.run_config(c, item.check_id)
                            if normal_file_read:
                                binding_options["expected_target_sha256"] = expected_target
                            _, values = run_binding.effective_values(
                                check_def, settings, selected_env, values, side=side, attempt_id=None,
                                issued_at=utcnow(), valid_from=policy.valid_from, valid_until=policy.valid_until,
                                run_cfg=run_cfg, policy_ref=policy.ref(), **binding_options)
                        executor.validate_policy(check_def, values)
                        if normal_file_read:
                            executor.normal_file_read_inputs(selected_env, values)
                        if kube and expected_target is not None:
                            if (prebound and item.check_id == "REQ-09"
                                    and isinstance(executor, KubernetesNormalTokenInputExecutor)):
                                executor.prepared_normal_token_input(check_def, selected_env, values,
                                                                     expected_target_sha256=expected_target)
                            if (prebound and item.check_id == "REQ-10"
                                    and getattr(check_def, "collection_profile", None) is None
                                    and isinstance(executor, KubernetesSamplingExecutor)):
                                executor.prepared_normal_sampling(check_def, selected_env, values,
                                                                  expected_target_sha256=expected_target)
                                continue
                            if (prebound and item.check_id == "LIF-04" and run_binding.stages_for(check_def) == (1,)
                                    and isinstance(executor, KubernetesStreamBufferExecutor)):
                                executor.prepared_normal_stream(check_def, selected_env, values,
                                                                 expected_target_sha256=expected_target)
                                continue  # The validated normal stream uses no role keys.
                            if (prebound and item.check_id == "OBS-02"
                                    and isinstance(executor, KubernetesNormalServingReadinessExecutor)):
                                executor.prepared_normal_serving_readiness(check_def, selected_env, values,
                                                                           expected_target_sha256=expected_target)
                            if (prebound and item.check_id == "LIF-03"
                                    and isinstance(executor, KubernetesNormalRequestLatencyExecutor)):
                                executor.prepared_normal_request_latency(check_def, selected_env, values,
                                                                         expected_target_sha256=expected_target)
                            if (prebound and item.check_id == "LIF-01"
                                    and isinstance(executor, KubernetesNormalStartupLoadExecutor)):
                                executor.prepared_normal_startup_load(check_def, selected_env, values,
                                                                      expected_target_sha256=expected_target)
                            if (prebound and item.check_id == "SUP-07"
                                    and isinstance(executor, KubernetesNormalCompileCacheExecutor)):
                                executor.prepared_normal_compile_cache(check_def, selected_env, values,
                                                                       expected_target_sha256=expected_target)
                            if (prebound and item.check_id == "PLT-07"
                                    and isinstance(executor, KubernetesPassiveEgressPolicyExecutor)):
                                executor.prepared_passive_egress_policy(check_def, selected_env, values,
                                                                        expected_target_sha256=expected_target)
                            if (prebound and item.check_id == "SUP-10"
                                    and isinstance(executor, KubernetesInstalledPluginInventoryExecutor)):
                                executor.prepared_installed_plugin_inventory(check_def, selected_env, values,
                                                                             expected_target_sha256=expected_target)
                            if (prebound and item.check_id == "SUP-12"
                                    and isinstance(executor, KubernetesNormalSafetensorsReaderExecutor)):
                                executor.prepared_normal_reader(check_def, selected_env, values,
                                                                expected_target_sha256=expected_target)
                            if (prebound and item.check_id == "REQ-20"
                                    and isinstance(executor, KubernetesNormalGraphCaptureExecutor)):
                                executor.prepared_normal_graph_capture(check_def, selected_env, values,
                                                                       expected_target_sha256=expected_target)
                            if (prebound and item.check_id == "PLT-02" and isinstance(executor, KubernetesIPCProductExecutor)
                                    and not c.get("record_attestation") and not c.get("lab_activation")):
                                executor.prepared_protected_paths(check_def, selected_env, values,
                                                                  expected_target_sha256=expected_target)
                                continue  # The validated metadata reader has no role keys.
                            if item.check_id == "OBS-04":
                                executor.prepared_local_observation(check_def, selected_env, values,
                                                                    expected_target_sha256=expected_target)
                            elif item.check_id == "PLT-06":
                                executor.prepared_worker_trust(check_def, selected_env, values,
                                                               expected_target_sha256=expected_target)
                            elif (prebound and isinstance(executor, KubernetesRequiredEventExecutor)
                                  and run_binding.stages_for(check_def) == (1, 2, 3, 4)):
                                if item.check_id == "PLT-12":
                                    executor.prepared_copy_record_snapshot(check_def, selected_env, values,
                                                                           expected_target_sha256=expected_target)
                                elif (item.check_id == "OBS-01" and GATEWAY_RECORD_SNAPSHOT_OPTION not in settings
                                      and KUBERNETES_BOUND_CHECK_OPTIONS["PLT-12"] not in settings):
                                    executor.prepared_record_snapshot(check_def, selected_env, values,
                                                                      expected_target_sha256=expected_target)
                        if paired_supply_roles and isinstance(executor, KubernetesSupplyBootExecutor):
                            executor.prepared_supply_roles(check_def, selected_env, values)
                        if (kube and item.check_id in run_binding.INITIALIZATION_CHECKS
                                and isinstance(executor, KubernetesInitializationExecutor)
                                and {1, 2}.issubset(run_binding.stages_for(check_def))):
                            executor.prepared_inputs(check_def, selected_env, values)
                        if (kube and item.check_id == "OBS-11" and isinstance(executor, KubernetesLogIntegrityExecutor)
                                and 2 in run_binding.stages_for(check_def)):
                            executor.prepared_collection_status(check_def, selected_env, values,
                                                                expected_target_sha256=expected_target)
                        if (kube and isinstance(executor, KubernetesRecordAccessExecutor)
                                and run_binding.is_record_access_binding(check_def)):
                            executor.prepared_record_access(check_def, selected_env, values)
                        if (kube and prebound and item.check_id == "LIF-12" and not c.get("lab_activation")
                                and isinstance(executor, KubernetesRequestTerminationExecutor)
                                and run_binding.stages_for(check_def) == (1, 2, 3, 4)):
                            executor.prepared_retry_records(check_def, selected_env, values,
                                                            expected_target_sha256=expected_target)
                            continue  # The validated record reader has no role keys.
                        if (kube and isinstance(executor, LifecycleTransitionExecutor)
                                and run_binding.is_lifecycle_generation_binding(check_def)):
                            generation_preflight.add(item.check_id)
                            generations, _ = executor.prepared_generations(check_def, selected_env, values)
                            if item.check_id in REDEPLOY_CHECKS:
                                identities = {generation_identity(generation.target, generation.env, generation.prepared)
                                              for generation in generations.values()}
                                if redeployment_side_reuse(seen_generation_identities, identities):
                                    raise ValueError(REDEPLOY_SIDE_REUSE)
                                seen_generation_identities.update(identities)
                    if (kube and item.check_id == "REQ-13" and isinstance(executor, KubernetesResponseContentExecutor)
                            and cfg.get("update", {}).get("kubernetes_candidate_binding", "prebound") == "prebound"
                            and "gateway" in policy.values.get(item.check_id, {}).get("required_targets", ())):
                        executor.prepared_gateway(selected_env)
                        continue  # The declared caller key is resolved only at runtime.
                    executor.keys()
                except Exception as exc:
                    input_errors.append((f"{side}/{item.check_id}", error_detail(exc)))
        if len(input_errors) == 1:
            record("inputs", False, input_errors[0][1])
        elif input_errors:
            record("inputs", False, f"{len(input_errors)} preparation input errors are listed below")
            for label, detail in input_errors:
                record(f"inputs:{label}", False, detail.removeprefix(f"{label}: "))
        else:
            record("inputs", True, "pack, selection, real executors, policy settings and model inventory checked")
    except Exception as exc:
        record("inputs", False, error_detail(exc))

    if kube_only:
        limitations = ["Static Kubernetes preparation only; no kubectl, Docker, engine or GPU execution.",
                       "Existing inspected image/source limits remain; unreviewed GPU images are unsupported."]
        if generation_preflight & REDEPLOY_CHECKS:
            limitations.append("LIF-05/LIF-16 preparation checks declared generation files. The installed native producer, actual transition and admission require separate observations.")
        if "LIF-06" in generation_preflight:
            limitations.append("LIF-06 preparation checks declared generation files and sample selections. "
                               "The installed native producer and actual live transition require separate observations.")
        if transition_requested:
            record("kubernetes-static-binding", prepared is not None,
                   "Exact target/ENV declarations checked; actual cluster, Pods, template and GPU remain unverified")
            if prepared is not None and cfg.get("update", {}).get("kubernetes_candidate_binding") == "after_baseline":
                record("candidate-binding-deferred", True,
                       "Only baseline target is selected; exact candidate ENV/generation will be required after baseline API termination, before candidate dispatch")
            limitations.append("Operator supplies exact running generations and controls termination/recovery. API termination does not prove GPU release.")
        else:
            record("kubernetes-static-preparation", prepared is not None,
                   "External Kubernetes configuration inspected. Target identity, live Pods and GPU require runtime verification.")
        if envs[0].engine.image_digest == envs[1].engine.image_digest:
            record("image-pair", False, "use different image digests for an update validation")
        report = result(limitations)
        if execution_scope is not None:
            # This one row describes PLAN coverage. All preparation failures still apply to the subset.
            execution_scope["static_preparation_ready"] = bool(execution_scope["included_check_ids"]) and all(
                row["ok"] for row in checks if row is not scope_exclusion)
            report["execution_scope"] = execution_scope
        return report
    if prepared is None:
        return result(["Preparation stopped before host checks because the inputs could not be validated."])
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
    return result(["Preparation only: no engine, security verdict or performance was measured.",
                            "ready describes input/host preparation; per-check static support is separate and does not remove checks or prove challenge completion.",
                            "CPU model/kernel/threads and GPU availability require verification on the test host.",
                            "On Windows, use Docker Desktop Linux containers and WSL2 integration; readiness does not start or reconfigure them.",
                            "Container RAM/shm/swap bounds are ceilings, not reservations or a guarantee that the model fits; preserve unrelated containers."])


def validation_readiness(pack, env, policy, cfg: dict) -> dict:
    return update_readiness(pack, [env], policy, cfg, single=True)
