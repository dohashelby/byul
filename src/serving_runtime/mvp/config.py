"""Configuration for the local serving runtime MVP."""

from dataclasses import dataclass, field
import os
from pathlib import Path


def _default_data_dir() -> str:
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return str(base / "serving-runtime" / "mvp")


@dataclass(frozen=True)
class MVPConfig:
    """Values used by the gateway and the selected backend mode."""

    host: str = "127.0.0.1"
    gateway_port: int = 8080
    engine_port: int = 8088
    backend: str = "mock"
    backend_config_path: str | None = None
    policy_path: str = "examples/mvp/policy.json"
    relay_timeout_s: float = 5.0
    stream_idle_timeout_s: float = 30.0
    request_deadline_s: float = 300.0
    max_concurrent_requests: int = 2
    data_dir: str = field(default_factory=_default_data_dir)
    revocations_path: str | None = None
    baseline_path: str | None = None
    manifest_path: str | None = None
    collector_interval_s: float = 5.0
    collector_stale_after_s: float = 15.0
    asset_manifest_path: str | None = None
    asset_interval_s: float = 60.0

    def __post_init__(self) -> None:
        if self.backend not in {"mock", "vllm"}:
            raise ValueError("backend must be mock or vllm")
        if self.backend == "vllm" and (
            self.backend_config_path is None or not str(self.backend_config_path).strip()
        ):
            raise ValueError("backend_config_path is required for the vllm backend")
        if self.backend == "mock" and self.backend_config_path is not None:
            raise ValueError("backend_config_path is only valid for the vllm backend")
        if self.relay_timeout_s <= 0:
            raise ValueError("relay_timeout_s must be positive")
        if self.stream_idle_timeout_s <= 0:
            raise ValueError("stream_idle_timeout_s must be positive")
        if self.request_deadline_s <= 0:
            raise ValueError("request_deadline_s must be positive")
        if (
            not isinstance(self.max_concurrent_requests, int)
            or isinstance(self.max_concurrent_requests, bool)
            or self.max_concurrent_requests <= 0
        ):
            raise ValueError("max_concurrent_requests must be a positive integer")
        if not str(self.data_dir).strip():
            raise ValueError("data_dir must not be empty")
        for name in ("revocations_path", "baseline_path", "manifest_path", "asset_manifest_path"):
            value = getattr(self, name)
            if value is not None and not str(value).strip():
                raise ValueError(f"{name} must not be empty when configured")
        if self.collector_interval_s <= 0:
            raise ValueError("collector_interval_s must be positive")
        if self.collector_stale_after_s <= 0:
            raise ValueError("collector_stale_after_s must be positive")
        if self.asset_interval_s <= 0:
            raise ValueError("asset_interval_s must be positive")


# A concise public name for callers that prefer the module-qualified Config.
Config = MVPConfig
