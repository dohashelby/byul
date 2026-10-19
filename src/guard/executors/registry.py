"""구현 이름 → 생성 함수. guard.toml의 [executors]가 CheckDef.executor를 여기 이름에 연결한다."""

from __future__ import annotations

from typing import Any, Callable

from guard.adapters.base import EngineAdapter
from guard.adapters.fake import FakeAdapter

from .base import Executor
from .fake import FakeExecutor

EXECUTORS: dict[str, Callable[[dict[str, Any]], Executor]] = {
    "fake": lambda cfg: FakeExecutor(cfg.get("fake", {}).get("scenario", "pass")),
}

ADAPTERS: dict[str, Callable[[], EngineAdapter]] = {
    "fake": FakeAdapter,
}
