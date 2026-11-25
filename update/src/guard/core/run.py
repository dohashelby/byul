"""run: PLAN → EVD들. 실행기를 부르고 증거를 모은다. **판정하지 않는다**(INV-14).

- 미선정 점검은 EVD를 만들지 않는다(INV-10).
- 선정됐지만 실행할 수 없으면 NOT_RUN, 실행기가 실패하면 ABORTED EVD를 남긴다(INV-10, INV-15).
- 실행하지 않은 증거는 MEASURED가 될 수 없다(INV-02).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any

from guard.collectors import check_observations
from guard.executors import passive_api_hop, registry
from guard.executors.base import EvidenceDraft
from guard.configuration import (CILIUM_EGRESS_STATUS_SCOPE_ERROR, COLLECTION_STATUS_SNAPSHOT_SCOPE_ERROR,
                                 INSTALLED_PLUGIN_INVENTORY_SCOPE_ERROR,
                                 KUBERNETES_BOUND_CHECK_OPTIONS, cilium_egress_status_requested,
                                 NORMAL_COMPILE_CACHE_SCOPE_ERROR, normal_compile_cache_requested,
                                 NORMAL_FILE_READS_SCOPE_ERROR, normal_file_reads_requested,
                                 NORMAL_GRAPH_CAPTURE_SCOPE_ERROR, normal_graph_capture_requested,
                                 NORMAL_REQUEST_LATENCY_SCOPE_ERROR, installed_plugin_inventory_requested,
                                 NORMAL_SAFETENSORS_READER_SCOPE_ERROR, normal_safetensors_reader_requested,
                                 NORMAL_SERVING_READINESS_SCOPE_ERROR, normal_request_latency_requested,
                                 NORMAL_STARTUP_LOAD_SCOPE_ERROR, normal_startup_load_requested,
                                 RETRY_RECORD_SNAPSHOT_SCOPE_ERROR, UPDATE_CHECK_OUTSIDE_SCOPE,
                                 bounded_metadata_scopes, collection_status_snapshot_requested, merge_config,
                                 normal_serving_readiness_requested,
                                 normal_token_input_requested, retry_record_snapshot_requested, staged_observer_executors,
                                 update_allowed_checks)
from guard.models import (
    ORIGIN_STRENGTH,
    CheckDef,
    DataOrigin,
    Environment,
    Evidence,
    ExecStatus,
    Gap,
    Mode,
    Pack,
    Plan,
    Policy,
    Side,
    canonical_json,
    new_id,
    sha256_hex,
    utcnow,
)
from guard.models.objects import Artifact, Execution, ExecutorInfo, PlanItem, PluginInfo
from guard.store import Store


@dataclass
class RunConfig:
    executors: dict[str, str] = field(default_factory=dict)  # CheckDef.executor → 구현 이름
    adapter: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)  # guard.toml 전체 (플러그인 설정)
    replace_manual: bool = False  # 사람이 입력한 최신 증거를 새 실행 기록으로 대체할지(기본: 대체하지 않음)
    side_configs: dict[str, RunConfig] = field(default_factory=dict)
    abort_reason: str | None = None
    requested: dict | None = None
    attempt_id: str | None = None
    runtime_environ: dict[str, str] | None = field(default=None, repr=False)
    kubernetes_target_pins: dict[str, str] | None = None
    check_abort_reasons: dict[str, str] = field(default_factory=dict)  # Runtime preflight failures for this side.
    normal_file_read_sha256: dict[str, str | None] = field(default_factory=dict)  # Private PLAN input snapshots.


def _not_run_origin(plan: Plan) -> DataOrigin:
    # 실행하지 않은 기록은 많아야 DECLARED. 입력이 더 약하면 그쪽
    return min(DataOrigin(plan.data_origin), DataOrigin.DECLARED, key=lambda o: ORIGIN_STRENGTH[o])


def _sides(plan: Plan, envs: list[Environment]) -> list[tuple[Side, Environment]]:
    if plan.mode == Mode.audit:
        return [(Side.single, envs[0])]
    return [(Side.baseline, envs[0]), (Side.candidate, envs[1])]


def _empty_evidence_parts(check: CheckDef) -> dict[str, Any]:
    return {
        "normal_control": {"attempted": False, "succeeded": None, "detail": ""},
        "challenges": [{"step": s, "attempted": False} for s in check.challenge_steps],
        "observations": {o.key: {"value": None, "collected": False} for o in check.required_observations},
    }


def _executor_for_item(item: PlanItem, cfg: RunConfig, env=None, *, allowed_checks=None):
    if allowed_checks is not None and item.check_id not in allowed_checks:
        return None, UPDATE_CHECK_OUTSIDE_SCOPE
    if Gap.NO_EXECUTOR in item.gaps:
        return None, "NO_EXECUTOR: check has no automated executor (manual test required)"
    if item.executor not in cfg.executors:
        return None, f"executor {item.executor!r} not configured in guard.toml [executors]"
    factory = registry.resolve_executor(item.executor, cfg.executors[item.executor], engine=env.engine.name if env else None, version=env.engine.version if env else None)
    if factory is None:
        return None, f"executor implementation {cfg.executors[item.executor]!r} not available"
    if Gap.NO_POLICY_VALUE in item.gaps:
        return None, "NO_POLICY_VALUE: required policy values missing"
    return factory, None


def _blocked_reason(item: PlanItem, cfg: RunConfig, env=None, *, allowed_checks=None) -> str | None:
    return _executor_for_item(item, cfg, env, allowed_checks=allowed_checks)[1]


def _normalize(check: CheckDef, draft: EvidenceDraft) -> dict[str, Any]:
    """CheckDef가 요구하는 키·단계만 남기고, 빠진 것은 미수집·미시도로 채운다(추정하지 않음)."""
    parts = _empty_evidence_parts(check)
    by_step = {c.step: c for c in draft.challenges}
    parts["challenges"] = [
        by_step[s].model_dump() if s in by_step else {"step": s, "attempted": False} for s in check.challenge_steps
    ]
    for key in parts["observations"]:
        if key in draft.observations:
            parts["observations"][key] = draft.observations[key].model_dump()
    parts["normal_control"] = draft.normal_control.model_dump()
    return parts


def validate_update_run_config(cfg: RunConfig) -> None:
    effective = [cfg.side_configs.get(side, cfg) for side in ("baseline", "candidate")]
    real_names = {name for c in effective for name, impl in c.executors.items() if impl == "real"}
    if real_names and set(cfg.side_configs) != {"baseline", "candidate"}:
        raise ValueError("real update runs require baseline and candidate side configurations")
    for name in real_names:
        if any(c.executors.get(name) != "real" for c in effective):
            raise ValueError(f"{name}: both update sides must use real executors")
        paths = [c.raw.get(name, {}).get("target") for c in effective]
        if not all(paths) or Path(paths[0]).resolve() == Path(paths[1]).resolve():
            raise ValueError(f"{name}: update sides need different target files")


def run_plan(store: Store, pack: Pack, plan: Plan, cfg: RunConfig, *, only_side: Side | None = None) -> list[Evidence]:
    from .execution_recipe import execution_lock
    if plan.mode == Mode.update:
        validate_update_run_config(cfg)
        effective = cfg.side_configs.values() if cfg.side_configs else (cfg,)
        if only_side is not None or any({"real", "kubernetes"} & set(c.executors.values()) for c in effective):
            raise ValueError("PAIRED_EXECUTION_REQUIRES_COORDINATOR")
    with execution_lock(store.root, plan.id):
        return run_plan_locked(store, pack, plan, cfg, only_side=only_side)


def run_plan_locked(store: Store, pack: Pack, plan: Plan, cfg: RunConfig, *,
                    only_side: Side | None = None) -> list[Evidence]:
    """Dispatch under the PLAN lock held by the caller across lifecycle transitions."""
    from .execution_recipe import configuration_sha256, validate_policy_time
    requested = cfg.requested if cfg.requested is not None else cfg.raw
    if plan.mode != Mode.update:
        configurations = (requested, cfg.raw, *(side.raw for side in cfg.side_configs.values()))
        if any(cilium_egress_status_requested(config) for config in configurations):
            raise ValueError(CILIUM_EGRESS_STATUS_SCOPE_ERROR)
        if any(installed_plugin_inventory_requested(config) for config in configurations):
            raise ValueError(INSTALLED_PLUGIN_INVENTORY_SCOPE_ERROR)
        if any(normal_request_latency_requested(config) for config in configurations):
            raise ValueError(NORMAL_REQUEST_LATENCY_SCOPE_ERROR)
        if any(normal_serving_readiness_requested(config) for config in configurations):
            raise ValueError(NORMAL_SERVING_READINESS_SCOPE_ERROR)
        if any(normal_startup_load_requested(config) for config in configurations):
            raise ValueError(NORMAL_STARTUP_LOAD_SCOPE_ERROR)
        if any(normal_compile_cache_requested(config) for config in configurations):
            raise ValueError(NORMAL_COMPILE_CACHE_SCOPE_ERROR)
        if any(normal_file_reads_requested(config) for config in configurations):
            raise ValueError(NORMAL_FILE_READS_SCOPE_ERROR)
        if any(normal_graph_capture_requested(config) for config in configurations):
            raise ValueError(NORMAL_GRAPH_CAPTURE_SCOPE_ERROR)
        if any(normal_safetensors_reader_requested(config) for config in configurations):
            raise ValueError(NORMAL_SAFETENSORS_READER_SCOPE_ERROR)
        if any(normal_token_input_requested(config) for config in configurations):
            raise ValueError("NORMAL_TOKEN_INPUT_EXECUTION_SCOPE_INVALID")
        if any(retry_record_snapshot_requested(config) for config in configurations):
            raise ValueError(RETRY_RECORD_SNAPSHOT_SCOPE_ERROR)
        if any(collection_status_snapshot_requested(config) for config in configurations):
            raise ValueError(COLLECTION_STATUS_SNAPSHOT_SCOPE_ERROR)
    policy = store.resolve(plan.inputs.policy)
    validate_policy_time(policy)
    environments = [store.resolve(r) for r in plan.inputs.environments]
    software_digests: dict[Path, tuple[bytes, str]] = {}

    expected_recipe = plan.inputs.execution_recipe_sha256

    def verify_recipe(**captures):
        if expected_recipe is None:
            return
        if configuration_sha256(
            requested, environments, plan=plan, software_digests=software_digests, **captures,
        ) != expected_recipe:
            raise ValueError('EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED')

    verify_recipe()
    cfg.attempt_id = cfg.attempt_id or new_id('ATTEMPT')
    return _run_plan(store, pack, plan, cfg, environments, policy, verify_recipe=verify_recipe, only_side=only_side)


def recover_pending_locked(store: Store, plan: Plan, attempt_id: str) -> list[Evidence]:
    if re.fullmatch(r"ATTEMPT-[A-Za-z0-9][A-Za-z0-9-]{0,95}", attempt_id) is None:
        raise ValueError("ATTEMPT_ID_INVALID")
    pending = store.root / 'pending-evidence' / attempt_id
    if pending.is_symlink():
        raise ValueError('PENDING_EVIDENCE_BINDING_INVALID')
    recovered = []
    for path in sorted(pending.glob('*.json')):
        if path.is_symlink() or not path.is_file():
            raise ValueError('PENDING_EVIDENCE_BINDING_INVALID')
        saved = Evidence.model_validate_json(path.read_bytes())
        if saved.plan != plan.ref() or saved.execution.attempt_id != attempt_id or not saved.hash_ok() or path.stem != saved.content_sha256:
            raise ValueError('PENDING_EVIDENCE_BINDING_INVALID')
        recovered.append(saved)
    store.put_many(recovered)
    return recovered


def _run_plan(store: Store, pack: Pack, plan: Plan, cfg: RunConfig, envs: list[Environment], policy: Policy, *,
              verify_recipe: Callable[..., None], only_side: Side | None = None) -> list[Evidence]:
    if plan.inputs.pack != pack.ref():
        raise ValueError("plan was made with a different pack revision")
    requested = cfg.requested if cfg.requested is not None else cfg.raw
    allowed_checks = None
    if plan.mode == Mode.update:
        allowed_checks = update_allowed_checks(requested, pack)
        validate_update_run_config(cfg)
        sides = ("baseline", "candidate")
        actual = bounded_metadata_scopes((cfg.side_configs.get(side, cfg).raw for side in sides), pack, allowed_checks)
        expected = bounded_metadata_scopes(
            (merge_config(requested, requested.get("sides", {}).get(side, {})) for side in sides), pack, allowed_checks)
        if actual != expected:
            raise ValueError("OBSERVATION_RECORD_SNAPSHOT_SCOPE_INVALID")
    staged_observers = (staged_observer_executors(merge_config(requested, requested.get("sides", {}).get("candidate", {})))
                        if plan.mode == Mode.update else set())
    passive_hop = passive_api_hop.validate_run_scope(pack, plan, cfg, allowed_checks)
    bound_external_update = (plan.mode == Mode.update and plan.inputs.execution_recipe_sha256 is not None
                             and requested.get('update', {}).get('mode', 'external') == 'external')
    prebound_external_update = (bound_external_update
        and requested.get('update', {}).get('kubernetes_candidate_binding', 'prebound') == 'prebound')
    record_targets = {}
    if prebound_external_update:
        for side_name, scope in zip(sides, expected, strict=True):
            for check_id in ("LIF-12", "OBS-11"):
                if check_id not in scope:
                    continue
                path = scope[check_id][0].get("target")
                if isinstance(path, (str, Path)) and path:
                    record_targets[side_name, check_id] = Path(path)

    plan_ref = plan.ref()
    recover_pending_locked(store, plan, cfg.attempt_id)
    existing = {
        (e.check_id, e.side): e for e in store.find_latest("EVD", lambda e: e.plan == plan_ref)
    }

    out: list[Evidence] = []
    decoded_by_side = {}
    source_refs = {}
    for item in plan.items:
        if not item.selected:
            continue  # INV-10
        check = pack.checks[item.check_id]
        for side, env in _sides(plan, envs):
            if only_side is not None and side != only_side:
                continue
            record_reads = {}
            transition_manifests = {} if bound_external_update else None
            initialization_digests = {} if bound_external_update else None
            response_route_digests = {} if prebound_external_update else None
            supply_record_digests = {} if prebound_external_update else None
            record_access_digests = {} if prebound_external_update else None
            missing_route_prepared = set()
            rank_common_files = {}
            record_path = record_targets.get((side.value, item.check_id))
            record_target_snapshots = {record_path: None} if record_path is not None else None
            retry_snapshot_digests = {} if record_path is not None and item.check_id == "LIF-12" else None
            verify_recipe(record_reads=record_reads, target_snapshots=record_target_snapshots,
                          transition_manifests=transition_manifests, initialization_digests=initialization_digests,
                          response_route_digests=response_route_digests, supply_record_digests=supply_record_digests,
                          missing_native_route_prepared_sides=missing_route_prepared, rank_common_files=rank_common_files,
                          record_access_digests=record_access_digests, retry_snapshot_digests=retry_snapshot_digests)
            side_cfg = cfg.side_configs.get(side.value, cfg)
            adapter_factory = registry.adapter_factory(side_cfg.adapter)
            adapter = adapter_factory() if adapter_factory else None
            adapter_info = PluginInfo(name=adapter.name, version=adapter.version) if adapter else None
            prev = existing.get((check.id, side))
            if cfg.abort_reason or side_cfg.abort_reason or side_cfg.check_abort_reasons.get(check.id):
                if prev is not None and prev.execution.status == ExecStatus.COMPLETED and prev.execution.attempt_id == cfg.attempt_id:
                    out.append(prev)
                    continue  # A later operation failed; this completed fragment stays visible.
            if prev is not None and prev.execution.operator is not None and not cfg.replace_manual:
                out.append(prev)  # 사람이 입력한 증거를 말 없이 덮지 않는다(SPEC §4.5 증거 대체 규칙)
                continue
            ident = {"id": prev.id, "revision": prev.revision + 1} if prev else {"id": new_id("EVD"), "revision": 1}
            environment_ref = env.ref()
            common = dict(
                **ident,
                plan=plan_ref,
                check_id=check.id,
                side=side,
                environment=environment_ref,
                refs=[plan_ref, environment_ref],
            )
            side_cfg.attempt_id = cfg.attempt_id
            with check_observations.bind(record_reads, decoded_by_side.setdefault(side, {})):
                evd = _run_one(store, check, item, env, policy.values.get(check.id, {}), side_cfg, adapter, adapter_info, plan, common,
                               policy_validity=(policy.valid_from, policy.valid_until), allowed_checks=allowed_checks,
                               passive_hop=passive_hop and check.id == passive_api_hop.CHECK,
                               source_ref=source_refs.get(side),
                               staged_observer=side == Side.candidate and item.executor in staged_observers,
                               transition_manifests=transition_manifests, initialization_digests=initialization_digests,
                               prebound_external_update=prebound_external_update,
                               response_route_digests=response_route_digests, supply_record_digests=supply_record_digests,
                               native_route_prepared_absent=side.value in missing_route_prepared,
                               rank_common_files=rank_common_files.get(side.value),
                               record_access_digests=record_access_digests,
                               record_target_bytes=record_target_snapshots[record_path] if record_target_snapshots else None,
                               retry_snapshot_files=retry_snapshot_digests.get(side.value) if retry_snapshot_digests else None)
            try:
                store.put(evd)
            except Exception:
                # Keep the complete sealed result if the object index cannot
                # accept it. Recovery never fabricates an empty replacement.
                pending = Path(store.root) / 'pending-evidence' / cfg.attempt_id
                pending.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(dir=pending, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(canonical_json(evd.to_dict()))
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, pending / (evd.content_sha256 + '.json'))
                finally:
                    temporary.unlink()
                raise
            out.append(evd)
            if passive_hop and check.id == passive_api_hop.SOURCE_CHECK and evd.execution.status == ExecStatus.COMPLETED:
                source_refs[side] = evd.ref()
    return out


def _run_one(store, check, item, env, policy_values, cfg, adapter, adapter_info, plan, common,
             policy_validity=(None, None), *, allowed_checks=None, passive_hop=False, source_ref=None, staged_observer=False,
             transition_manifests=None, initialization_digests=None, response_route_digests=None,
             supply_record_digests=None, native_route_prepared_absent=False, rank_common_files=None,
             record_access_digests=None, record_target_bytes=None, retry_snapshot_files=None,
             prebound_external_update=False) -> Evidence:
    binding = None

    def stopped(status: ExecStatus, reason: str, executor_info: ExecutorInfo | None = None) -> Evidence:
        return Evidence(
            **common,
            data_origin=_not_run_origin(plan),
            execution=Execution(status=status, executor=executor_info, adapter=adapter_info, abort_reason=reason,
                                attempt_id=cfg.attempt_id, run_binding=binding),
            **_empty_evidence_parts(check),
        ).seal()

    factory, reason = _executor_for_item(item, cfg, env, allowed_checks=allowed_checks)
    if reason:
        return stopped(ExecStatus.NOT_RUN, reason)
    abort_reason = cfg.abort_reason or cfg.check_abort_reasons.get(check.id)
    if abort_reason:
        return stopped(ExecStatus.ABORTED, abort_reason)
    impl_name = cfg.executors[item.executor]
    from guard.configuration import for_check
    try:
        raw = for_check(cfg.raw, item.executor, check.id)  # [check_config.<CHECK>] (RB2-S)
    except ValueError:
        return stopped(ExecStatus.NOT_RUN, "CHECK_CONFIG_INVALID")
    option = KUBERNETES_BOUND_CHECK_OPTIONS.get(check.id)
    check_hashes = (raw or {}).get("_kubernetes_target_sha256_by_check")
    section = (raw or {}).get(item.executor)
    if (response_route_digests is not None and check.id == 'REQ-13' and check.executor == 'response_content'
            and cfg.executors[item.executor] == 'kubernetes' and type(section) is dict):
        from guard.executors.kubernetes_response_content import GATEWAY_TARGET_REQUIRED, ROUTE_TARGET_CHANGED
        side = common['side'].value
        if 'route_target' in section or side in response_route_digests:
            expected = response_route_digests.get(side)
            if expected is None or 'route_target' not in section:
                return stopped(ExecStatus.NOT_RUN, ROUTE_TARGET_CHANGED)
            raw = {**raw, '_response_route_target_sha256': expected}
        elif (type(policy_values.get('required_targets')) is list
                and 'gateway' in policy_values['required_targets']):
            return stopped(ExecStatus.NOT_RUN, GATEWAY_TARGET_REQUIRED)
    retry_target = None
    locked_record_target = check.id in {"LIF-12", "OBS-11"} and type(section) is dict and section.get(option) is True
    if locked_record_target:
        if check.id == "LIF-12":
            from guard.executors.kubernetes_retry_records import snapshot_target_sha256
            unavailable = "RUN_BINDING_RETRY_SNAPSHOT_TARGET_UNAVAILABLE"
        else:
            from guard.executors.kubernetes_log_integrity import snapshot_target_sha256
            unavailable = "RUN_BINDING_COLLECTION_STATUS_TARGET_UNAVAILABLE"
        try:
            target_digest = snapshot_target_sha256(record_target_bytes, env)
        except ValueError:
            return stopped(ExecStatus.NOT_RUN, unavailable)
        if check.id == "LIF-12":
            if retry_snapshot_files is None:
                return stopped(ExecStatus.NOT_RUN, "RUN_BINDING_RETRY_SNAPSHOT_FILES_UNAVAILABLE")
            retry_target = target_digest
        key = "_kubernetes_target_sha256_by_executor"
        hashes = raw.get(key)
        raw = {**raw, key: {**(hashes if type(hashes) is dict else {}), item.executor: target_digest}}
    elif (option and type(section) is dict and section.get(option) is True
            and type(check_hashes) is dict and check.id in check_hashes):
        # The public paired update derives this pin from the recipe's own
        # snapshot. Share the normal factory interface with staged launches.
        key = "_kubernetes_target_sha256_by_executor"
        hashes = raw.get(key)
        raw = {**raw, key: {**(hashes if type(hashes) is dict else {}), item.executor: check_hashes[check.id]}}
    if passive_hop:
        if source_ref is None:
            return stopped(ExecStatus.NOT_RUN, "PASSIVE_API_HOP_SOURCE_NOT_COLLECTED")
        try:
            passive_api_hop.validate_policy(check, policy_values)
            target = passive_api_hop.read_target(raw[item.executor]["target"], env)
            hashes = raw.get("_kubernetes_target_sha256_by_executor")
            expected = hashes.get(item.executor) if type(hashes) is dict else None
            draft = passive_api_hop.draft_passive_api_hop(
                store, plan_ref=plan.ref(), side=common["side"], attempt_id=cfg.attempt_id,
                source_ref=source_ref, target=target, expected_target_sha256=expected,
            )
        except passive_api_hop.PassiveApiHopUnavailable as exc:
            return stopped(ExecStatus.NOT_RUN, str(exc))
        binding = passive_api_hop.execution_binding(
            draft, side=common["side"], environment_ref=env.ref(), attempt_id=cfg.attempt_id)
        return Evidence(
            **{**common, "refs": [*common["refs"], source_ref]},
            data_origin=draft.data_origin,
            execution=Execution(
                status=draft.status, adapter=adapter_info, attempt_id=cfg.attempt_id,
                executor=ExecutorInfo(name=item.executor, version=passive_api_hop.VERSION,
                                      config_sha256=sha256_hex(canonical_json(draft.config))),
                started_at=draft.started_at, finished_at=draft.finished_at, run_binding=binding,
            ),
            comparability=draft.comparability,
            **_normalize(check, draft),
        ).seal()
    from guard.executors import run_binding
    from guard.executors.lifecycle_transition import GENERATION_MANIFEST_PIN, REDEPLOY_CHECKS, REDEPLOY_SIDE_REUSE
    transition = check.executor == 'lifecycle_transition' and cfg.executors[item.executor] == 'kubernetes'
    pinned_redeployment = (transition and check.id in REDEPLOY_CHECKS and type(section) is dict
                           and GENERATION_MANIFEST_PIN in section)
    if pinned_redeployment and (not prebound_external_update or 2 not in run_binding.stages_for(check)):
        return stopped(ExecStatus.NOT_RUN, 'RUN_BINDING_TRANSITION_PAIRED_PLAN_REQUIRED')
    if run_binding.keys_for(check):
        # Per-run technical binding (guard-run-binding/1, /2): computed by guard, never a policy value.
        try:
            target_options = {}
            if (check.id in {'LIF-01', 'LIF-03', 'OBS-02', 'PLT-07', 'REQ-20', 'SUP-05', 'SUP-07', 'SUP-10', 'SUP-12'}
                    and type(section) is dict and option in section):
                hashes = raw.get("_kubernetes_target_sha256_by_executor")
                expected_target = hashes.get(item.executor) if type(hashes) is dict else None
                if expected_target is None:
                    check_label = check.id.replace('-', '')
                    raise run_binding.RunBindingUnavailable(f"RUN_BINDING_{check_label}_NORMAL_TARGET_UNAVAILABLE")
                target_options["expected_target_sha256"] = expected_target
                if check.id == 'SUP-05':
                    if (set(cfg.normal_file_read_sha256) != {'prepared', 'reference_manifest'}
                            or any(digest is None for digest in cfg.normal_file_read_sha256.values())):
                        raise run_binding.RunBindingUnavailable('RUN_BINDING_SUP05_NORMAL_FILES_UNAVAILABLE')
                    target_options["expected_normal_file_sha256"] = cfg.normal_file_read_sha256
            if (transition_manifests is not None and (check.id == 'LIF-06' or check.id in REDEPLOY_CHECKS)
                    and transition and 2 in run_binding.stages_for(check)):
                expected_manifest = transition_manifests.get(common["side"].value + ":" + check.id)
                if expected_manifest == REDEPLOY_SIDE_REUSE:
                    raise run_binding.RunBindingUnavailable("RUN_BINDING_" + REDEPLOY_SIDE_REUSE)
                if expected_manifest is None:
                    raise run_binding.RunBindingUnavailable('RUN_BINDING_TRANSITION_GENERATIONS_UNAVAILABLE')
                target_options["expected_transition_manifest_sha256"] = expected_manifest
                if pinned_redeployment:
                    if section[GENERATION_MANIFEST_PIN] != expected_manifest:
                        raise run_binding.RunBindingUnavailable('RUN_BINDING_TRANSITION_GENERATIONS_CHANGED')
                    raw = {**raw, '_transition_generation_manifest_sha256': expected_manifest}
            if (initialization_digests is not None and check.id in run_binding.INITIALIZATION_CHECKS
                    and check.executor == 'initialization' and cfg.executors[item.executor] == 'kubernetes'
                    and {1, 2}.issubset(run_binding.stages_for(check))):
                expected_files = initialization_digests.get(common['side'].value + ':' + check.id)
                if expected_files is None:
                    raise run_binding.RunBindingUnavailable('RUN_BINDING_INITIALIZATION_FILES_UNAVAILABLE')
                target_options['expected_initialization_digests'] = expected_files
                raw = {**raw, '_initialization_prepared_sha256': expected_files['prepared']}
            if (supply_record_digests is not None and cfg.executors[item.executor] == 'kubernetes'
                    and run_binding.is_supply_record_binding(check)):
                expected = supply_record_digests.get(common['side'].value + ':' + check.id)
                if expected is None:
                    raise run_binding.RunBindingUnavailable('RUN_BINDING_SUPPLY_RECORD_FILES_UNAVAILABLE')
                target_options['expected_supply_record_digests'] = expected
                raw = {**raw, '_supply_prepared_sha256_by_role': {role: expected[role] for role in ('normal', 'challenge')}}
            if (record_access_digests is not None and cfg.executors[item.executor] == 'kubernetes'
                    and run_binding.is_record_access_binding(check)
                    and item.check_revision >= run_binding.OBS06_STAGE4_MIN_CHECK_REVISION):
                expected = record_access_digests.get(common['side'].value)
                if expected is None:
                    raise run_binding.RunBindingUnavailable('RUN_BINDING_RECORD_ACCESS_FILES_UNAVAILABLE')
                target_options['expected_record_access_digests'] = expected
            if native_route_prepared_absent and check.id == 'REQ-02' and cfg.executors[item.executor] == 'kubernetes':
                target_options['native_route_prepared_absent'] = True
            if (check.id == 'LIF-08' and cfg.executors[item.executor] == 'common-input'
                    and plan.inputs.execution_recipe_sha256 is not None and 1 in run_binding.stages_for(check)):
                if rank_common_files is None:
                    raise run_binding.RunBindingUnavailable('RUN_BINDING_RANK_COMMON_FILES_UNAVAILABLE')
                raw = {**raw, '_rank_common_files': rank_common_files}
                target_options['expected_rank_prepared_file'] = rank_common_files['prepared']
            computed_binding, policy_values = run_binding.effective_values(
                check, raw.get(item.executor, {}), env, policy_values, side=str(common["side"].value),
                attempt_id=cfg.attempt_id, issued_at=utcnow(), valid_from=policy_validity[0], valid_until=policy_validity[1],
                run_cfg=run_binding.run_config(cfg.raw, check.id), policy_ref=plan.inputs.policy, **target_options)
            if retry_target is not None:
                if policy_values.get("retry_target_sha256") != retry_target:
                    raise run_binding.RunBindingUnavailable("RUN_BINDING_RETRY_SNAPSHOT_TARGET_CHANGED")
                if any(policy_values.get(key) != digest for key, digest in retry_snapshot_files.items()):
                    raise run_binding.RunBindingUnavailable("RUN_BINDING_RETRY_SNAPSHOT_FILES_CHANGED")
            binding = computed_binding
        except ValueError as exc:
            code = str(exc) if str(exc).startswith("RUN_BINDING_") else "RUN_BINDING_UNAVAILABLE"
            return stopped(ExecStatus.NOT_RUN, code)

    if staged_observer:
        # The candidate file did not exist at selection. Only a trusted
        # coordinator can supply the observed generation's target digest.
        pins = raw.get("_kubernetes_target_sha256_by_executor", {})
        pin = pins.get(item.executor) if type(pins) is dict else None
        if not isinstance(pin, str) or re.fullmatch(r"[0-9a-f]{64}", pin) is None:
            return stopped(ExecStatus.NOT_RUN, "KUBERNETES_STAGED_TARGET_UNVERIFIED")

    started = utcnow()
    info_stub: ExecutorInfo | None = None
    try:
        from guard.adapters.engine_provider import PROVIDERS
        provider = PROVIDERS.get(env.engine.name)
        if provider is not None: provider.validate_environment(env, item.executor, impl_name)
        if cfg.runtime_environ is not None or cfg.kubernetes_target_pins is not None:
            raw = dict(raw)
            if cfg.runtime_environ is not None:
                raw["_runtime_environ"] = cfg.runtime_environ
            if cfg.kubernetes_target_pins is not None:
                raw["_kubernetes_target_sha256_by_executor"] = {
                    **cfg.kubernetes_target_pins, **raw.get("_kubernetes_target_sha256_by_executor", {}),
                }
        with registry.environment_binding(env):
            executor = registry.build_executor(item.executor, impl_name, raw, factory=factory)
        info_stub = ExecutorInfo(name=executor.name, version=executor.version, config_sha256=sha256_hex(b""))
        workdir = Path(store.root) / "work" / plan.id / cfg.attempt_id / check.id / str(common["side"])
        draft = executor.run(check, env, policy_values, adapter, workdir)
        if not isinstance(draft, EvidenceDraft):
            raise TypeError(f"executor returned {type(draft).__name__}, not EvidenceDraft")
        executor_info = ExecutorInfo(
            name=executor.name, version=executor.version, config_sha256=sha256_hex(canonical_json(draft.config))
        )
        # Keep invalid executor output in the existing empty-ABORTED boundary.
        # Validate before artifact I/O, without repairing or replacing the draft.
        parts = _normalize(check, draft)
        artifact_inputs = [(a.name, a.path, a.collector) for a in draft.artifacts]
        EvidenceDraft.model_validate(draft.model_dump())
        from guard.models.artifacts import model_closure_sha256
        if draft.comparability.model_inventory_sha256 == env.model.inventory_sha256:
            draft = draft.model_copy(update={'comparability': draft.comparability.model_copy(update={
                'model_inventory_sha256': model_closure_sha256(env.model)})})
        artifacts = []
        artifact_failed = False
        for name, path, collector in artifact_inputs:
            try:
                stored = store.put_artifact(Path(path).read_bytes())
                artifact = Artifact(name=name, path=stored.path, sha256=stored.sha256, bytes=stored.bytes,
                                    collected_at=utcnow(), collector=collector)
            except Exception:  # File/store/metadata errors must not erase independent collected facts.
                artifact_failed = True
            else:
                artifacts.append(artifact)
        status, origin, abort_reason = draft.status, draft.data_origin, draft.abort_reason
        if artifact_failed:
            status = ExecStatus.ABORTED
            origin = min(draft.data_origin, DataOrigin.DECLARED, key=lambda o: ORIGIN_STRENGTH[o])
            # Never retain artifact exception text, filenames, paths or traces.
            code = "ARTIFACT_PERSISTENCE_INCOMPLETE"
            abort_reason = f"{abort_reason}; {code}" if abort_reason else code
        return Evidence(
            **common,
            data_origin=origin,
            execution=Execution(
                status=status,
                executor=executor_info,
                adapter=adapter_info,
                started_at=draft.started_at or started,
                finished_at=draft.finished_at,
                abort_reason=abort_reason,
                cleanup_errors=draft.cleanup_errors,
                attempt_id=cfg.attempt_id,
                run_binding=binding,
            ),
            artifacts=artifacts,
            comparability=draft.comparability,
            **parts,
        ).seal()
    except Exception:  # noqa: BLE001 - INV-15: 실패를 삼키지 않고 ABORTED 증거로 남긴다
        # Exception names/text can contain request content, credentials or paths.
        return stopped(ExecStatus.ABORTED, "EXECUTOR_FAILURE", info_stub)
