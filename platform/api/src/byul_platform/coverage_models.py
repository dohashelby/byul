"""선정 점검 범위의 조회 모델."""
from typing import Literal
from pydantic import BaseModel, Field
from .models import SourceInfo, RuntimeSourceWindow


class CheckPackLink(BaseModel):
    id: str
    revision: int
    retired: bool
    executor: str | None
    sha256: str


class CheckEvidenceRef(BaseModel):
    evidence_id: str
    sha256: str | None = None


class RuntimeCheckLink(BaseModel):
    framework_id: str
    question: str
    devs: list[str]
    sources: list[str]
    rules: list[str]
    condition: str
    limitation: str
    update: CheckPackLink
    observed_events: int
    evidence_refs: list[CheckEvidenceRef]
    observation_state: Literal["observed", "unobserved"]


class RuntimeCheckCoverage(BaseModel):
    mapping_version: str
    fixed_sha: str
    pack_revision: int
    selected_count: int
    excluded_count: int
    items: list[RuntimeCheckLink]
    truncated: bool
    source: SourceInfo
    source_windows: dict[str, RuntimeSourceWindow] = Field(default_factory=dict)
