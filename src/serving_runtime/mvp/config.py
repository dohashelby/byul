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
    """Values used by the gateway and bundled mock engine."""

    host: str = "127.0.0.1"
    gateway_port: int = 8080
    engine_port: int = 8088
    policy_path: str = "examples/mvp/policy.json"
    relay_timeout_s: float = 5.0
    data_dir: str = field(default_factory=_default_data_dir)

    def __post_init__(self) -> None:
        if self.relay_timeout_s <= 0:
            raise ValueError("relay_timeout_s must be positive")
        if not str(self.data_dir).strip():
            raise ValueError("data_dir must not be empty")


# A concise public name for callers that prefer the module-qualified Config.
Config = MVPConfig
