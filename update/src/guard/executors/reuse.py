"""실행 전 확정할 수 있는 시험 입력만 재구성한다. 관측·요청·엔진 기동은 하지 않는다.

동적 fixture를 추측하지 않는다. 실행기가 입력을 확정할 수 없으면 None이고
선정기는 그 점검을 다시 실행한다. 실행기별 이름과 fixture 지식은 이 경계에 둔다.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from guard.models import canonical_json, sha256_hex
from guard.models.objects import ExecutorInfo, PluginInfo
from guard.runtime.comparison import execution_conditions

from . import registry


@dataclass(frozen=True)
class ExecutionPreview:
    executor: ExecutorInfo
    adapter: PluginInfo
    fixture_sha256: str
    config_sha256: str | None


def preview_inputs(check, env, values: dict, cfg: dict) -> ExecutionPreview | None:
    """기존 실행 골격과 같은 순수 fixture/config 계산을 쓴다. 실패는 이월 불가다."""
    impl = cfg.get("executors", {}).get(check.executor)
    factory = registry.resolve_executor(check.executor, impl) if impl else None
    adapter_name = cfg.get("adapter", {}).get("name")
    adapter_factory = registry.ADAPTERS.get(adapter_name)
    if factory is None or adapter_factory is None:
        return None
    try:
        executor = factory(cfg)
        adapter = adapter_factory()
        method = getattr(executor, "preview_reuse_inputs", None)
        if method is not None:
            # 비 HTTP 실행기는 자기 실행 설정과 고정 자료의 계산을 직접 제공한다.
            prepared = method(check, env, values, adapter)
            if prepared is None:
                return None
            fixture, config, comparison = prepared
        else:
            from .http_common import HttpCheckExecutor, mask
            if not isinstance(executor, HttpCheckExecutor):
                return None
            executor.validate_policy(check, values)
            target = json.loads(Path(executor.cfg["target"]).read_text(encoding="utf-8"))
            if target.get("lab"):
                return None
            keys = executor.keys()
            secrets = [value for value in keys.values() if value]
            config = mask(execution_conditions(target, {
                **executor.cfg,
                "request_timeout": float(executor.cfg.get("request_timeout", 3.0)),
                "allow_mutating_routes": bool(executor.cfg.get("allow_mutating_routes", False)),
            }, list(executor.roles())), secrets)
            comparison = sha256_hex(canonical_json(config)) if target.get("comparison_config") else None
            fixture = _http_fixture(executor, check, env, values, adapter, target, keys)
            if fixture is None:
                return None
            fixture = mask(fixture, secrets)
        return ExecutionPreview(
            executor=ExecutorInfo(name=executor.name, version=executor.version,
                                  config_sha256=sha256_hex(canonical_json(config))),
            adapter=PluginInfo(name=adapter.name, version=adapter.version),
            fixture_sha256=sha256_hex(canonical_json(fixture)), config_sha256=comparison,
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        # 선언만으로 동일성을 확정하지 않는다. 새 실행의 기존 오류 전파는 그대로 둔다.
        return None


def _http_fixture(executor, check, env, values, adapter, target, keys):
    if executor.name == "container_config":
        from .container_config import DEFAULT_SENTINELS, PROC_CMD, SENTINEL_CMD
        return {"sentinel_paths": list(dict.fromkeys(executor.cfg.get("sentinel_paths", DEFAULT_SENTINELS))),
                "proc_cmd": PROC_CMD, "sentinel_cmd": SENTINEL_CMD}
    if executor.name == "credential_exposure":
        from .credential_exposure import ENV_CMD
        fixture = {"env_command": ENV_CMD, "policy": values}
        if any(obs.key == "forbidden_mount_violations" for obs in check.required_observations):
            from .credential_candidates import MAX_OUTPUT_BYTES, METADATA_PROBE, PROFILE, PROBE_TIMEOUT
            fixture.update(candidate_profile=PROFILE,
                           candidate_probe_sha256=hashlib.sha256(METADATA_PROBE.encode()).hexdigest(),
                           candidate_probe_timeout=PROBE_TIMEOUT, candidate_output_limit=MAX_OUTPUT_BYTES)
        return fixture
    if executor.name in {"direct_route", "role_route"} and executor.cfg.get("comparison_routes") is not None:
        from .http_common import Ctx, RouteSet, probe_templates
        from .route_fixture import manifest, resolve_routes

        def no_request(*args, **kwargs):
            raise RuntimeError("reuse preview must not collect observations")

        ctx = Ctx(check=check, env=env, adapter=adapter, target=target, policy_values=values,
                  timeout=float(executor.cfg.get("request_timeout", 3.0)),
                  allow_mutating=bool(executor.cfg.get("allow_mutating_routes", False)),
                  keys=keys, secrets=[], cprobe=no_request, workdir=Path("."), started=None)
        # 명시한 고정 목록의 입력 manifest만 만든다. 등록 경로를 관측했다고 기록하지 않는다.
        rs = resolve_routes(ctx, RouteSet({}, [], True, None, None), executor.cfg["comparison_routes"])
        templates, skipped = probe_templates(ctx, rs.routes)
        roles = list(keys)
        mode = "direct" if executor.name == "direct_route" else "role"
        if mode == "role":
            admin = executor.cfg.get("admin_role", "admin")
            if admin not in keys:
                return None
            roles = [role for role in keys if role != admin] + [admin]
        return manifest(ctx, rs, templates, skipped, roles=roles, mode=mode)
    return None
