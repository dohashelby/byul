"""API 응답 봉투. 계약 문서는 dict 그대로 싣되 OpenAPI에는 계약 스키마 참조로 나간다.

계약 문서가 아닌 값(요청 표 한 행, 저장 판본 메타 등)은 플랫폼 화면용 형식이며,
모두 계약 문서의 필드(특히 runtime-event의 native.record)에서 꺼낸 값만 담는다.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, WithJsonSchema


def _contract(name: str) -> Any:
    # OpenAPI를 만들 때 main.custom_openapi가 이 표식을 계약 스키마 $ref로 바꾼다.
    return Annotated[dict[str, Any], WithJsonSchema({"x-contract": name})]


EnvironmentDoc = _contract("Environment")
PolicyDoc = _contract("Policy")
VerificationResultDoc = _contract("VerificationResult")
RuntimeEventDoc = _contract("RuntimeEvent")
RuntimeStatusDoc = _contract("RuntimeStatus")
RevalidationRequestDoc = _contract("RevalidationRequest")
EvidenceItemDoc = _contract("EvidenceItem")  # evidence-index의 items[] 한 항목
JsonValue = Annotated[Any, WithJsonSchema({})]

SourceMode = Literal["example", "live", "file"]


class SourceInfo(BaseModel):
    """이 응답의 자료를 어디서 읽었는지. example이면 화면에 '예시 데이터' 띠를 띄운다."""

    name: str
    mode: SourceMode
    detail: str | None = None


class InvalidItem(BaseModel):
    """계약 검증에 실패한 항목. 버리지 않고 '형식 오류' 행으로 보여준다."""

    index: int
    errors: list[str]
    raw: JsonValue


class ErrorBody(BaseModel):
    code: str
    message: str


# --- runtime ---------------------------------------------------------------


class RequestRow(BaseModel):
    """요청 내역 표 한 행. 값은 전부 runtime-event의 native.record에서 꺼낸 것이다.

    지연시간·토큰 수·비용은 runtime이 기록하지 않으므로 필드가 없다.
    """

    event_id: str
    request_id: str
    recorded_at: str | None
    outcome: str
    decision_result: str | None
    model: str | None
    max_tokens: int | None
    reason_code: str | None
    rule_id: str | None
    http_status: int | None
    engine_http_status: int | None
    stream: bool | None
    delivery_attempted: bool | None
    backend_kind: str | None
    environment: str | None
    run_id: str | None
    target_ref: str | None
    data_origin: str
    origin_detail: str | None


class RuntimeEventList(BaseModel):
    items: list[RuntimeEventDoc]
    invalid: list[InvalidItem]
    source: SourceInfo
    truncated: bool = False


class RequestList(BaseModel):
    items: list[RequestRow]
    invalid: list[InvalidItem]
    source: SourceInfo
    truncated: bool = False


class RequestDetail(BaseModel):
    row: RequestRow | None
    event: RuntimeEventDoc | None
    event_invalid: InvalidItem | None = None
    evidence_state: str | None
    evidence_summary: str | None
    native_detail: JsonValue = None
    evidence: list[EvidenceItemDoc] = Field(default_factory=list)
    source: SourceInfo


class RequestCounts(BaseModel):
    allowed: int
    denied: int
    invalid: int
    engine_rejected: int
    error: int
    unknown: int


class RequestSummary(BaseModel):
    counts: RequestCounts
    total: int
    corrupt_lines: int | None
    generated_at: str | None
    source: SourceInfo


class RuntimeStatusResponse(BaseModel):
    item: RuntimeStatusDoc | None
    invalid: InvalidItem | None = None
    source: SourceInfo


class ComparisonRow(BaseModel):
    """구성 비교(/api/baseline)·대상 비교(/api/target)의 필드 한 줄."""

    field: str
    expected: JsonValue
    observed: JsonValue
    state: str
    source: str | None = None


class ComparisonTable(BaseModel):
    state: str | None
    rows: list[ComparisonRow]
    notes: list[str] = Field(default_factory=list)


class RuntimeComparisons(BaseModel):
    baseline: ComparisonTable
    target: ComparisonTable
    source: SourceInfo


class RevalidationList(BaseModel):
    items: list[RevalidationRequestDoc]
    invalid: list[InvalidItem]
    source: SourceInfo


# --- update ----------------------------------------------------------------


class VerificationSummaryItem(BaseModel):
    report_id: str
    created_at: str | None
    mode: str | None
    data_origin: str | None
    pack_review_status: str | None


class VerificationList(BaseModel):
    items: list[VerificationResultDoc]
    invalid: list[InvalidItem]
    source: SourceInfo


class VerificationDetail(BaseModel):
    item: VerificationResultDoc
    raw_checks: dict[str, JsonValue] | None = Field(
        default=None, description="RPT 원본의 by_check 행(check_id별). 예시 모드에는 원본이 없어 null."
    )
    raw_path: str | None = None
    source: SourceInfo


# --- evidence --------------------------------------------------------------


class EvidenceEntry(BaseModel):
    producer: str
    index_created_at: str
    index_path: str | None
    item: EvidenceItemDoc


class EvidenceList(BaseModel):
    items: list[EvidenceEntry]
    invalid: list[InvalidItem]
    source: SourceInfo


# --- store -----------------------------------------------------------------


class RevisionMeta(BaseModel):
    doc_id: str
    revision: int
    saved_at: str
    sha256: str
    status: str
    label: str | None = Field(default=None, description="정책은 policy_version, 환경은 null")
    target_ref: str | None = None


class EnvironmentRevision(BaseModel):
    meta: RevisionMeta
    document: EnvironmentDoc


class PolicyRevision(BaseModel):
    meta: RevisionMeta
    document: PolicyDoc


class RevisionList(BaseModel):
    items: list[RevisionMeta]
    source: SourceInfo


class FieldIssue(BaseModel):
    path: str
    message: str


class SaveRejected(BaseModel):
    code: Literal["SCHEMA_INVALID", "STATUS_NOT_ALLOWED", "BAD_REQUEST"]
    errors: list[FieldIssue]


class EnvironmentSaved(BaseModel):
    saved: EnvironmentRevision
    warnings: list[FieldIssue]


class PolicySaved(BaseModel):
    saved: PolicyRevision
    warnings: list[FieldIssue]


class DiffEntry(BaseModel):
    path: str
    change: Literal["added", "removed", "changed"]
    before: JsonValue = None
    after: JsonValue = None


class RevisionDiff(BaseModel):
    doc_id: str
    from_revision: int
    to_revision: int
    entries: list[DiffEntry]


# --- meta / overview -------------------------------------------------------


class Meta(BaseModel):
    contracts_version: str
    example_mode: bool
    sources: list[SourceInfo]
    runtime_connection: Literal["example", "connected", "disconnected", "unauthorized"]


class Overview(BaseModel):
    requests: RequestSummary | None
    requests_error: ErrorBody | None = None
    status: RuntimeStatusResponse | None
    status_error: ErrorBody | None = None
    latest_verification: VerificationResultDoc | None
    open_revalidation: int | None
    revalidation_error: ErrorBody | None = None
