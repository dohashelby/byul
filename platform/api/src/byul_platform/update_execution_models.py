"""Platform-owned execution state. Security verdicts still come only from guard RPTs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LegacyUpdateRunRequest(BaseModel):
    """Previously saved file-based runs remain readable, but cannot be submitted."""
    model_config = ConfigDict(extra="forbid")

    mode: Literal["validate", "update"] = "validate"
    pack_path: str = Field(default="packs/PACK-BASELINE", min_length=1, max_length=4096)
    config_path: str = Field(default="examples/gpu-bounded.toml", min_length=1, max_length=4096)
    policy_path: str = Field(min_length=1, max_length=4096)
    environment_path: str | None = Field(default=None, max_length=4096)
    baseline_path: str | None = Field(default=None, max_length=4096)
    candidate_path: str | None = Field(default=None, max_length=4096)

    @field_validator("pack_path", "config_path", "policy_path", "environment_path", "baseline_path", "candidate_path")
    @classmethod
    def clean_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("파일 경로를 입력하세요. 제어 문자는 사용할 수 없습니다")
        return value

    @model_validator(mode="after")
    def check_sides(self) -> LegacyUpdateRunRequest:
        if self.mode == "validate":
            if not self.environment_path or self.baseline_path or self.candidate_path:
                raise ValueError("단일 환경 검증에는 환경 경로 하나가 필요합니다")
        elif not self.baseline_path or not self.candidate_path or self.environment_path:
            raise ValueError("두 판본 비교에는 기준·후보 환경 경로가 필요합니다")
        return self


class DocumentSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    revision: int = Field(ge=1, strict=True)


class UpdateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["validate", "update"] = "validate"
    policy: DocumentSelection
    environment: DocumentSelection | None = None
    baseline: DocumentSelection | None = None
    candidate: DocumentSelection | None = None

    @model_validator(mode="after")
    def check_sides(self) -> UpdateRunRequest:
        if self.mode == "validate":
            if not self.environment or self.baseline or self.candidate:
                raise ValueError("단일 검증에는 등록한 환경 판본 하나를 선택하세요")
        elif not self.baseline or not self.candidate or self.environment:
            raise ValueError("두 판본 비교에는 기준·후보 환경 판본을 선택하세요")
        elif self.baseline == self.candidate:
            raise ValueError("기준과 후보는 서로 다른 환경 판본이어야 합니다")
        return self


class SelectedDocumentRef(DocumentSelection):
    sha256: str


class PreparationCheck(BaseModel):
    model_config = ConfigDict(extra="allow")
    check: str
    ok: bool
    detail: str = ""


class PreparationIssue(BaseModel):
    model_config = ConfigDict(extra="allow")
    code: str
    cause: str
    action: str
    side: str | None = None
    check_id: str | None = None
    blocking: bool = True


class PreparationGuidance(BaseModel):
    model_config = ConfigDict(extra="allow")
    summary: str = ""
    issues: list[PreparationIssue] = Field(default_factory=list)


class UpdatePreparation(BaseModel):
    model_config = ConfigDict(extra="allow")
    ready: bool
    checks: list[PreparationCheck] = Field(default_factory=list)
    check_support: list[dict[str, Any]] = Field(default_factory=list)
    guidance: PreparationGuidance | None = None
    limitations: list[str] = Field(default_factory=list)


class UpdateRun(BaseModel):
    id: str
    request: UpdateRunRequest | LegacyUpdateRunRequest
    status: Literal["preparing", "ready", "blocked", "running", "completed", "failed", "interrupted"]
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    requested_by: str
    executed_by: str | None = None
    preparation: UpdatePreparation | None = None
    exit_code: int | None = None
    error: str | None = None
    report_ids: list[str] = Field(default_factory=list)
    log_tail: str = ""
    log_truncated: bool = False
    input_sha256: str
    source_refs: dict[str, SelectedDocumentRef] = Field(default_factory=dict)
    pack: dict[str, Any] | None = None


class UpdateRunList(BaseModel):
    items: list[UpdateRun]


class UpdateExecutionSettings(BaseModel):
    available: bool
    can_execute: bool
    unavailable_reason: str | None = None
    temporary: bool
