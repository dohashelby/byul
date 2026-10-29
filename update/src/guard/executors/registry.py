"""구현 이름 → 생성 함수. guard.toml의 [executors]가 CheckDef.executor를 여기 이름에 연결한다."""

from __future__ import annotations

from typing import Any, Callable

from guard.adapters.base import EngineAdapter
from guard.adapters.fake import FakeAdapter
from guard.adapters.vllm import VllmAdapter

from .base import Executor
from .container_config import ContainerConfigExecutor
from .kubernetes_container_config import KubernetesContainerConfigExecutor
from .direct_route import DirectRouteExecutor
from .fake import FakeExecutor
from .role_route import RoleRouteExecutor
from .credential_exposure import CredentialExposureExecutor
from .input_validation import InputValidationExecutor
from .sampling_validation import SamplingValidationExecutor
from .kubernetes_sampling import KubernetesSamplingExecutor
from .response_content import ResponseContentExecutor
from .log_analysis import LogAnalysisExecutor
from .template_validation import TemplateValidationExecutor
from .kubernetes_template_supply import KubernetesTemplateSupplyExecutor
from .request_binding import RequestBindingExecutor
from .kubernetes_request_binding import KubernetesRequestBindingExecutor
from .log_integrity import LogIntegrityExecutor
from .unix_ipc import UnixIPCExecutor
from .boundary_egress import BoundaryEgressExecutor
from .api_transport import APITransportExecutor

# 키: "<CheckDef.executor>:<구현>" (특정 실행기 전용) 또는 "<구현>" (공용). guard.toml [executors]의 값이 <구현>.
EXECUTORS: dict[str, Callable[[dict[str, Any]], Executor]] = {
    "fake": lambda cfg: FakeExecutor(cfg.get("fake", {}).get("scenario", "pass"), cfg.get("fake", {}).get("scenarios", {})),
    "direct_route:real": lambda cfg: DirectRouteExecutor(cfg.get("direct_route", {}), environ=cfg.get("_runtime_environ")),
    "role_route:real": lambda cfg: RoleRouteExecutor(cfg.get("role_route", {}), environ=cfg.get("_runtime_environ")),
    "container_config:real": lambda cfg: ContainerConfigExecutor(cfg.get("container_config", {})),
    "container_config:kubernetes": lambda cfg: KubernetesContainerConfigExecutor(cfg.get("container_config", {}), environ=cfg.get("_runtime_environ"), expected_target_sha256=cfg.get("_kubernetes_target_sha256")),
    "credential_exposure:real": lambda cfg: CredentialExposureExecutor(cfg.get("credential_exposure", {})),
    "input_validation:real": lambda cfg: InputValidationExecutor(cfg.get("input_validation", {})),
    "sampling_validation:real": lambda cfg: SamplingValidationExecutor(cfg.get("sampling_validation", {})),
    "sampling_validation:kubernetes": lambda cfg: KubernetesSamplingExecutor(cfg.get("sampling_validation", {}), environ=cfg.get("_runtime_environ")),
    "response_content:real": lambda cfg: ResponseContentExecutor(cfg.get("response_content", {}), environ=cfg.get("_runtime_environ")),
    "log_analysis:real": lambda cfg: LogAnalysisExecutor(cfg.get("log_analysis", {}), environ=cfg.get("_runtime_environ")),
    "template_validation:real": lambda cfg: TemplateValidationExecutor(cfg.get("template_validation", {}), environ=cfg.get("_runtime_environ")),
    "template_validation:kubernetes": lambda cfg: KubernetesTemplateSupplyExecutor(cfg.get("template_validation", {}), environ=cfg.get("_runtime_environ"), expected_target_sha256=cfg.get("_kubernetes_target_sha256")),
    "request_binding:real": lambda cfg: RequestBindingExecutor(cfg.get("request_binding", {}), environ=cfg.get("_runtime_environ")),
    "request_binding:kubernetes": lambda cfg: KubernetesRequestBindingExecutor(cfg.get("request_binding", {}), environ=cfg.get("_runtime_environ"), expected_target_sha256=cfg.get("_kubernetes_target_sha256")),
    "log_integrity:real": lambda cfg: LogIntegrityExecutor(cfg.get("log_integrity", {}), environ=cfg.get("_runtime_environ")),
    "unix_ipc:real": lambda cfg: UnixIPCExecutor(cfg.get("unix_ipc", {}), environ=cfg.get("_runtime_environ")),
    "boundary_egress:real": lambda cfg: BoundaryEgressExecutor(cfg.get("boundary_egress", {})),
    "api_transport:real": lambda cfg: APITransportExecutor(cfg.get("api_transport", {})),
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
