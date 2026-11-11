"""Separate read-only route audit; it does not widen runtime-event contracts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .models import SourceInfo


class RouteAuditActor(BaseModel):
    state: Literal["unattributed"] = "unattributed"
    principal_id: None = None


class RouteAuditEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: Literal["runtime-route-rejection/1.0"]
    event_id: str | None = None
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    recorded_at: str
    method: str = Field(max_length=32)
    path: str = Field(max_length=256)
    response_code: int = Field(ge=100, le=599)
    reason: str = Field(max_length=128)
    forwarded_to_engine: Literal[False]
    scope: dict[str, str | None] = Field(default_factory=dict)
    actor: RouteAuditActor = Field(default_factory=RouteAuditActor)


class RouteAuditStatus(BaseModel):
    state: Literal["ok", "partial", "error"]
    scope: Literal["process_lifetime"]
    attempted_count: int = Field(ge=0)
    recorded_count: int = Field(ge=0)
    overflow_count: int = Field(ge=0)
    write_failure_count: int = Field(ge=0)
    last_write_error_class: str | None
    last_write_failure_at: str | None
    last_recorded_at: str | None
    window_count: int = Field(ge=0)
    window_limit: int = Field(gt=0)
    window_seconds: int = Field(gt=0)
    window_started_at: str


class RouteAuditWindow(BaseModel):
    scope: Literal["journal_tail_bytes"]
    total: None = None
    returned: int = Field(ge=0, le=200)
    truncated: bool
    scanned_bytes: int = Field(ge=0)
    max_bytes: int = Field(gt=0)
    limit: int = Field(ge=1, le=200)
    corrupt_lines: int = Field(ge=0)
    read_error_class: str | None


class RouteAuditDocument(BaseModel):
    items: list[RouteAuditEntry] = Field(max_length=200)
    status: RouteAuditStatus
    window: RouteAuditWindow
    generated_at: str


class RouteAuditResponse(BaseModel):
    item: RouteAuditDocument | None
    state: Literal["ok", "not_configured", "unsupported", "invalid"]
    source: SourceInfo
