"""run: PLAN → EVD들. 실행기를 부르고 증거를 모은다. **판정하지 않는다**(INV-14).

- 미선정 점검은 EVD를 만들지 않는다(INV-10).
- 선정됐지만 실행할 수 없으면 NOT_RUN, 실행기가 실패하면 ABORTED EVD를 남긴다(INV-10, INV-15).
- 실행하지 않은 증거는 MEASURED가 될 수 없다(INV-02).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from guard.executors import registry
from guard.executors.base import EvidenceDraft
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


def _blocked_reason(item: PlanItem, cfg: RunConfig) -> str | None:
    if Gap.NO_EXECUTOR in item.gaps:
        return "NO_EXECUTOR: check has no automated executor (manual test required)"
    if item.executor not in cfg.executors:
        return f"executor {item.executor!r} not configured in guard.toml [executors]"
    if registry.resolve_executor(item.executor, cfg.executors[item.executor]) is None:
        return f"executor implementation {cfg.executors[item.executor]!r} not available"
    if Gap.NO_POLICY_VALUE in item.gaps:
        return "NO_POLICY_VALUE: required policy values missing"
    return None


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
    if plan.inputs.pack != pack.ref():
        raise ValueError("plan was made with a different pack revision")
    envs = [store.resolve(r) for r in plan.inputs.environments]
    policy = store.resolve(plan.inputs.policy)
    if plan.mode == Mode.update:
        validate_update_run_config(cfg)

    plan_ref = plan.ref()
    existing = {
        (e.check_id, e.side): e for e in store.find_latest("EVD", lambda e: e.plan == plan_ref)
    }

    out: list[Evidence] = []
    for item in plan.items:
        if not item.selected:
            continue  # INV-10
        check = pack.checks[item.check_id]
        for side, env in _sides(plan, envs):
            if only_side is not None and side != only_side:
                continue
            side_cfg = cfg.side_configs.get(side.value, cfg)
            adapter = registry.ADAPTERS[side_cfg.adapter]() if side_cfg.adapter in registry.ADAPTERS else None
            adapter_info = PluginInfo(name=adapter.name, version=adapter.version) if adapter else None
            prev = existing.get((check.id, side))
            if prev is not None and prev.execution.operator is not None and not cfg.replace_manual:
                out.append(prev)  # 사람이 입력한 증거를 말 없이 덮지 않는다(SPEC §4.5 증거 대체 규칙)
                continue
            ident = {"id": prev.id, "revision": prev.revision + 1} if prev else {"id": new_id("EVD"), "revision": 1}
            common = dict(
                **ident,
                plan=plan_ref,
                check_id=check.id,
                side=side,
                environment=env.ref(),
                refs=[plan_ref, env.ref()],
            )
            evd = _run_one(store, check, item, env, policy.values.get(check.id, {}), side_cfg, adapter, adapter_info, plan, common)
            store.put(evd)
            out.append(evd)
    return out


def _run_one(store, check, item, env, policy_values, cfg, adapter, adapter_info, plan, common) -> Evidence:
    def stopped(status: ExecStatus, reason: str, executor_info: ExecutorInfo | None = None) -> Evidence:
        return Evidence(
            **common,
            data_origin=_not_run_origin(plan),
            execution=Execution(status=status, executor=executor_info, adapter=adapter_info, abort_reason=reason),
            **_empty_evidence_parts(check),
        ).seal()

    reason = _blocked_reason(item, cfg)
    if reason:
        return stopped(ExecStatus.NOT_RUN, reason)
    if cfg.abort_reason:
        return stopped(ExecStatus.ABORTED, cfg.abort_reason)

    impl_name = cfg.executors[item.executor]
    started = utcnow()
    info_stub: ExecutorInfo | None = None
    try:
        executor = registry.resolve_executor(item.executor, impl_name)(cfg.raw)
        info_stub = ExecutorInfo(name=executor.name, version=executor.version, config_sha256=sha256_hex(b""))
        workdir = Path(store.root) / "work" / plan.id / check.id / str(common["side"])
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
            ),
            artifacts=artifacts,
            comparability=draft.comparability,
            **parts,
        ).seal()
    except Exception:  # noqa: BLE001 - INV-15: 실패를 삼키지 않고 ABORTED 증거로 남긴다
        # Exception names/text can contain request content, credentials or paths.
        return stopped(ExecStatus.ABORTED, "EXECUTOR_FAILURE", info_stub)
