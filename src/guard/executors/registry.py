"""구현 이름 → 생성 함수. guard.toml의 [executors]가 CheckDef.executor를 여기 이름에 연결한다."""

from __future__ import annotations

from typing import Any, Callable

from guard.adapters.base import EngineAdapter
from guard.adapters.fake import FakeAdapter
from guard.adapters.vllm import VllmAdapter

from .base import Executor
from .direct_route import DirectRouteExecutor
from .fake import FakeExecutor

# 키: "<CheckDef.executor>:<구현>" (특정 실행기 전용) 또는 "<구현>" (공용). guard.toml [executors]의 값이 <구현>.
EXECUTORS: dict[str, Callable[[dict[str, Any]], Executor]] = {
    "fake": lambda cfg: FakeExecutor(cfg.get("fake", {}).get("scenario", "pass")),
    "direct_route:real": lambda cfg: DirectRouteExecutor(cfg.get("direct_route", {})),
}

ADAPTERS: dict[str, Callable[[], EngineAdapter]] = {
    "fake": FakeAdapter,
    "vllm": VllmAdapter,
}


def resolve_executor(executor: str, impl: str) -> Callable[[dict[str, Any]], Executor] | None:
    return EXECUTORS.get(f"{executor}:{impl}") or EXECUTORS.get(impl)
