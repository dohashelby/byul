"""구현 이름 → 생성 함수. guard.toml의 [executors]가 CheckDef.executor를 여기 이름에 연결한다."""

from __future__ import annotations

from typing import Any, Callable

from guard.adapters.base import EngineAdapter
from guard.adapters.fake import FakeAdapter
from guard.adapters.vllm import VllmAdapter

from .base import Executor
from .container_config import ContainerConfigExecutor
from .direct_route import DirectRouteExecutor
from .fake import FakeExecutor
from .role_route import RoleRouteExecutor
from .credential_exposure import CredentialExposureExecutor
from .input_validation import InputValidationExecutor

# 키: "<CheckDef.executor>:<구현>" (특정 실행기 전용) 또는 "<구현>" (공용). guard.toml [executors]의 값이 <구현>.
EXECUTORS: dict[str, Callable[[dict[str, Any]], Executor]] = {
    "fake": lambda cfg: FakeExecutor(cfg.get("fake", {}).get("scenario", "pass"), cfg.get("fake", {}).get("scenarios", {})),
    "direct_route:real": lambda cfg: DirectRouteExecutor(cfg.get("direct_route", {}), environ=cfg.get("_runtime_environ")),
    "role_route:real": lambda cfg: RoleRouteExecutor(cfg.get("role_route", {}), environ=cfg.get("_runtime_environ")),
    "container_config:real": lambda cfg: ContainerConfigExecutor(cfg.get("container_config", {})),
    "credential_exposure:real": lambda cfg: CredentialExposureExecutor(cfg.get("credential_exposure", {})),
    "input_validation:real": lambda cfg: InputValidationExecutor(cfg.get("input_validation", {})),
}

# 실행기 메타데이터: 이 실행기가 만든 증거를 판정하는 기본 판정기(없으면 CheckDef.judge).
# 증거를 만들 때 EVD.execution.executor.default_judge로 기록하고, 판정 코어는 그 값만 읽는다.
EXECUTOR_DEFAULT_JUDGES: dict[str, str] = {
    "manual-entry": "declared",  # 사람이 입력한 증거(guard evidence import)
}

ADAPTERS: dict[str, Callable[[], EngineAdapter]] = {
    "fake": FakeAdapter,
    "vllm": VllmAdapter,
}


def resolve_executor(executor: str, impl: str) -> Callable[[dict[str, Any]], Executor] | None:
    return EXECUTORS.get(f"{executor}:{impl}") or EXECUTORS.get(impl)
