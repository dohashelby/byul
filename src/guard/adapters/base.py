"""엔진 어댑터 인터페이스 (SPEC §7.1, X8). 엔진마다 다른 부분을 이 한 곳에 모은다.

실제 구현(vllm.py)은 5단계에서 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from guard.models import Environment
from guard.models.objects import FactValue


class CommonOption(str, Enum):
    """엔진 중립 옵션 이름. 어댑터가 엔진 고유 옵션으로 번역한다."""

    REMOTE_CODE_EXECUTION = "REMOTE_CODE_EXECUTION"
    API_KEY_REQUIRED = "API_KEY_REQUIRED"


@dataclass(frozen=True)
class LaunchSpec:
    model: str
    port: int
    options: dict[str, Any] = field(default_factory=dict)
    image: str | None = None


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    inference: bool = False
    kind: str = "inference"  # inference | admin | info (info는 REQ-01 등 접근 권한 점검 대상에서 제외)
    mutating: bool = False  # 엔진 상태를 바꾸는 관리 경로(/sleep 등). 기본적으로 직통 요청을 보내지 않는다


@dataclass(frozen=True)
class ProbeRequest:
    """무해한 시험 요청 1개(키 없음). 키는 실행기가 붙인다."""

    method: str
    path: str
    body: dict | None = None


@dataclass(frozen=True)
class ContainerHandle:
    id: str
    base_url: str


@dataclass(frozen=True)
class EngineIdentity:
    name: str
    version: str | None
    image_digest: str | None = None
    source_commit: str | None = None


@dataclass(frozen=True)
class EngineEvent:
    type: str
    attrs: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class EngineAdapter(Protocol):
    name: str
    version: str

    def start_command(self, spec: LaunchSpec) -> list[str]: ...

    def public_routes(self, env: Environment) -> list[Route]: ...

    def routes_from_openapi(self, doc: dict) -> list[Route]: ...  # public_routes의 파싱 부분(실행기가 출발점별로 받아온 문서용)

    def probe_request(self, route: Route, model: str | None) -> ProbeRequest | None: ...  # None = 보내지 않음(상태 변경 경로)

    def detect_version(self, handle: ContainerHandle) -> EngineIdentity: ...

    def read_option(self, name: CommonOption, env: Environment) -> FactValue: ...

    def parse_log(self, line: str) -> EngineEvent | None: ...

    def ready(self, handle: ContainerHandle) -> bool: ...
