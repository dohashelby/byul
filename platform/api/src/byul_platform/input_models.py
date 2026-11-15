"""입력 준비 상태. 시험 실행·판정·기업 승인을 대신하지 않는다."""
from typing import Any

from pydantic import BaseModel, Field

from .models import FieldIssue, PolicyDoc


class InputBinding(BaseModel):
    requirement: str
    field: str
    check_id: str
    key: str
    type: str = ""
    source: str = "values"


class InputVersion(BaseModel):
    engine: str
    version: str
    profiles: list[str]
    features: list[str]
    hidden_fields: list[str] = Field(default_factory=list)


class InputPack(BaseModel):
    id: str
    revision: int


class InputCatalog(BaseModel):
    pack: InputPack
    available: bool
    reason: str | None = None
    versions: list[InputVersion]
    bindings: list[InputBinding]
    parameters: dict[str, list[dict[str, Any]]]
    fixed_values: dict[str, dict[str, Any]]
    limitations: list[str]


class PolicyInputRequest(BaseModel):
    document: PolicyDoc


class PolicyInputPreview(BaseModel):
    document: PolicyDoc
    errors: list[FieldIssue]
    warnings: list[FieldIssue]
    gaps: list[FieldIssue]
    connected: list[str]
    pack_available: bool
    previous_policy: dict[str, Any] | None = None
    engine_executed: bool = False
