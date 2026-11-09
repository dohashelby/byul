"""Read-only dashboard view of the host GPU collector snapshot (platform-only, not a shared contract)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import SourceInfo


class GpuRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=64)
    utilization_percent: float | None = Field(default=None, ge=0, le=100)
    memory_used_mib: float | None = Field(default=None, ge=0)
    memory_total_mib: float | None = Field(default=None, ge=0)
    temperature_c: float | None = Field(default=None, ge=0)
    power_w: float | None = Field(default=None, ge=0)


class GpuSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: Literal["runtime-gpu-status/1.0"] = "runtime-gpu-status/1.0"
    state: Literal["ok", "stale", "error", "missing", "not_configured"]
    fresh: bool = False
    collected_at: str | None = None
    reason: str | None = Field(default=None, max_length=64)
    gpus: list[GpuRow] = Field(default_factory=list, max_length=16)


class GpuStatusResponse(BaseModel):
    item: GpuSnapshot
    source: SourceInfo
