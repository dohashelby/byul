"""시험용 가짜 어댑터. 엔진을 띄우지 않는다. FakeExecutor와 함께 파이프라인 시연에만 쓴다."""

from __future__ import annotations

from guard.models import Environment
from guard.models.objects import FactValue

from .base import CommonOption, ContainerHandle, EngineEvent, EngineIdentity, LaunchSpec, ProbeRequest, Route


class FakeAdapter:
    name = "fake"
    version = "0.1.0"

    def start_command(self, spec: LaunchSpec) -> list[str]:
        raise NotImplementedError("fake adapter does not start engines")

    def public_routes(self, env: Environment) -> list[Route]:
        return []

    def routes_from_openapi(self, doc: dict) -> list[Route]:
        return []

    def probe_request(self, route: Route, model: str | None, engine_version: str | None = None,
                      engine_image_digest: str | None = None) -> ProbeRequest | None:
        return None

    def detect_version(self, handle: ContainerHandle) -> EngineIdentity:
        raise NotImplementedError("fake adapter has no engine")

    def read_option(self, name: CommonOption, env: Environment) -> FactValue:
        return FactValue(value=None)  # 모른다

    def parse_log(self, line: str) -> EngineEvent | None:
        return None

    def ready(self, handle: ContainerHandle) -> bool:
        return False
