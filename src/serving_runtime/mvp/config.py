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
    max_connections: int = 64
    request_header_timeout_s: float = 5.0
    request_body_timeout_s: float = 10.0
    data_dir: str = field(default_factory=_default_data_dir)
    environment: str | None = None
    run_id: str | None = None
    revocations_path: str | None = None
    baseline_path: str | None = None
    manifest_path: str | None = None
    collector_interval_s: float = 5.0
    collector_stale_after_s: float = 15.0
    asset_manifest_path: str | None = None
    asset_interval_s: float = 60.0
    lab_report_path: str | None = None
    observation_feeds: tuple[tuple[str, str], ...] = ()
    observation_heartbeats: tuple[tuple[str, str], ...] = ()
    observation_origin: str = "real"
    observation_environment: str | None = None
    observation_run_id: str | None = None
    observation_intent_path: str | None = None
    cilium_policy_status: bool = False
    cilium_timeout_s: float = 10.0

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
        if (
            not isinstance(self.max_connections, int)
            or isinstance(self.max_connections, bool)
            or self.max_connections <= 0
        ):
            raise ValueError("max_connections must be a positive integer")
        if self.request_header_timeout_s <= 0:
            raise ValueError("request_header_timeout_s must be positive")
        if self.request_body_timeout_s <= 0:
            raise ValueError("request_body_timeout_s must be positive")
        if not str(self.data_dir).strip():
            raise ValueError("data_dir must not be empty")
        for name in (
            "revocations_path",
            "baseline_path",
            "manifest_path",
            "asset_manifest_path",
            "lab_report_path",
            "observation_intent_path",
            "environment",
            "run_id",
            "observation_environment",
            "observation_run_id",
        ):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{name} must not be empty when configured")
                if name in {
                    "environment",
                    "run_id",
                    "observation_environment",
                    "observation_run_id",
                } and (
                    len(value) > 128
                    or any(ord(character) < 0x20 for character in value)
                ):
                    raise ValueError(f"{name} must be printable and at most 128 characters")
        if self.collector_interval_s <= 0:
            raise ValueError("collector_interval_s must be positive")
        if self.collector_stale_after_s <= 0:
            raise ValueError("collector_stale_after_s must be positive")
        if self.asset_interval_s <= 0:
            raise ValueError("asset_interval_s must be positive")
        if self.observation_origin not in {"synthetic", "real"}:
            raise ValueError("observation_origin must be synthetic or real")
        kinds = [kind for kind, _path in self.observation_feeds]
        if any(kind not in {"hubble", "tetragon"} for kind in kinds):
            raise ValueError("observation feed kind must be hubble or tetragon")
        if len(kinds) != len(set(kinds)):
            raise ValueError("only one observation feed per kind may be configured")
        if any(not str(path).strip() for _kind, path in self.observation_feeds):
            raise ValueError("observation feed path must not be empty")
        heartbeat_kinds = [kind for kind, _path in self.observation_heartbeats]
        if any(kind not in {"hubble", "tetragon"} for kind in heartbeat_kinds):
            raise ValueError("observation heartbeat kind must be hubble or tetragon")
        if len(heartbeat_kinds) != len(set(heartbeat_kinds)):
            raise ValueError("only one observation heartbeat per kind may be configured")
        if any(not str(path).strip() for _kind, path in self.observation_heartbeats):
            raise ValueError("observation heartbeat path must not be empty")
        if not set(heartbeat_kinds).issubset(kinds):
            raise ValueError("observation heartbeat requires a matching feed")
        if self.observation_intent_path and "tetragon" not in kinds:
            raise ValueError("observation_intent_path requires a tetragon feed")
        if not isinstance(self.cilium_policy_status, bool):
            raise ValueError("cilium_policy_status must be a boolean")
        if self.cilium_timeout_s <= 0:
            raise ValueError("cilium_timeout_s must be positive")


# A concise public name for callers that prefer the module-qualified Config.
Config = MVPConfig
