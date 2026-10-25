"""SPEC §4 객체 7종의 고유 필드."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_serializer, model_validator

from .base import GuardObject, Ref, Strict
from .enums import (
    Applicability,
    Comparison,
    CondResult,
    DataOrigin,
    ExceptionStatus,
    ExecStatus,
    Gap,
    Kind,
    Mode,
    NotSelectedReason,
    PolicyStatus,
    ReviewStatus,
    Side,
    Verdict,
)

# --- 4.1 ENV -----------------------------------------------------------------

# 적용 판단에 쓰는 사실 키. 프레임워크 environment_template.json의 사실 키 전체.
REGISTERED_FACTS: frozenset[str] = frozenset(
    {
        "gateway",
        "prefix_cache",
        "prefix_cache_or_shared_cache",
        "shared_or_remote_weights",
        "dynamic_import",
        "alternate_sources_or_formats_or_derived_cache",
        "remote_acquisition_or_retry",
        "managed_or_external_fallback",
        "r10_features",
        "request_code_revision_selection",
    }
)


class FactValue(Strict):
    value: Any = None
    origin: DataOrigin | None = None
    source: str | None = None
    absence_evidence: str | None = None
    conflict: list[Any] | None = None

    @model_validator(mode="after")
    def _conflict_has_null_value(self) -> "FactValue":
        # 출처끼리 충돌하면 임의로 고르지 않는다
        if self.conflict and self.value is not None:
            raise ValueError("conflicting fact must have value: null")
        return self


def _drop_none(data: dict, keys: tuple[str, ...]) -> dict:
    """선택 필드가 비어 있으면 직렬화에서 뺀다(그 필드가 없던 기존 객체의 해시를 유지)."""
    for k in keys:
        if data.get(k) is None:
            data.pop(k, None)
    return data


class EngineInfo(Strict):
    name: str
    version: str | None = None
    source_commit: str | None = None
    image_digest: str | None = None
    # 고정 소스 commit과의 일치 확인 결과: MATCH | ABBREVIATED_MATCH | MISMATCH | UNVERIFIED
    source_commit_status: Literal["MATCH", "ABBREVIATED_MATCH", "MISMATCH", "UNVERIFIED"] | None = None
    expected_source_commit: str | None = None

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_none(handler(self), ("source_commit_status", "expected_source_commit"))


class AdapterInfo(Strict):
    name: str
    version: str


class CpuProfile(Strict):
    architecture: str = Field(min_length=1)
    model: str = Field(min_length=1)
    system: str = Field(min_length=1)
    kernel: str = Field(min_length=1)
    threads: int = Field(gt=0)


class RuntimeInfo(Strict):
    nodes: int = 1
    tp_groups: int = 1
    gpu_count: int | None = None
    gpu_model: str | None = None
    driver: str | None = None
    cpu: CpuProfile | None = None
    platform: str | None = None  # 실행 환경 설명. 예: "CPU, arm64, Docker Desktop on macOS"

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_none(handler(self), ("platform", "cpu"))

    @model_validator(mode="after")
    def _cpu_needs_zero_gpus(self):
        if self.cpu is not None and self.gpu_count != 0:
            raise ValueError("an explicit CPU profile needs gpu_count=0")
        return self


class FilesReadByEngine(Strict):
    """참고용: snapshot 중 엔진이 읽는 파일만의 inventory. 판정·비교 조건(INV-09)에는 쓰지 않는다."""

    file_count: int
    inventory_sha256: str


class ModelInfo(Strict):
    id: str | None = None
    revision: str | None = None
    inventory_sha256: str | None = None  # 비교용: 마운트한 snapshot 전체 (tools/model_inventory.py --scope all)
    files_read_by_engine: FilesReadByEngine | None = None  # 참고용 (엔진이 읽는 파일만)

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_none(handler(self), ("files_read_by_engine",))


class Environment(GuardObject):
    kind: Literal[Kind.ENV] = Kind.ENV
    engine: EngineInfo
    adapter: AdapterInfo | None = None
    runtime: RuntimeInfo = Field(default_factory=RuntimeInfo)
    model: ModelInfo = Field(default_factory=ModelInfo)
    facts: dict[str, FactValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _registered_facts_only(self) -> "Environment":
        unknown = set(self.facts) - REGISTERED_FACTS
        if unknown:
            raise ValueError(f"unregistered fact keys: {sorted(unknown)}")
        return self


# --- 4.2 POL -----------------------------------------------------------------


class Scope(Strict):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class PolicyException(Strict):
    check_id: str
    reason: str
    approved_by: str | None = None
    approved_at: datetime | None = None
    expires_at: datetime | None = None


class Policy(GuardObject):
    kind: Literal[Kind.POL] = Kind.POL
    status: PolicyStatus
    pack: Ref
    values: dict[str, dict[str, Any]] = Field(default_factory=dict)
    scope: Scope = Field(default_factory=Scope)
    exceptions: list[PolicyException] = Field(default_factory=list)
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None

    @model_validator(mode="after")
    def _confirmed_needs_who_when(self) -> "Policy":
        if self.status == PolicyStatus.CONFIRMED and not (self.confirmed_by and self.confirmed_at):
            raise ValueError("CONFIRMED policy needs confirmed_by and confirmed_at")
        return self


# --- 4.3 CHG -----------------------------------------------------------------


class VersionPoint(Strict):
    version: str
    source_commit: str
    image_digest: str | None = None


class Extractor(Strict):
    name: Literal["git_diff_ast", "advisory", "manual", "git_diff", "model_manifest"]
    version: str


class ChangeEvidence(Strict):
    path: str
    line: int | None = None
    sha256: str


class ChangeFact(Strict):
    id: str
    category: Literal["API_ROUTE", "CLI_OPTION", "ENV_VAR", "DEFAULT_VALUE", "DEPENDENCY", "ADVISORY", "AUTH", "OTHER", "STREAMING", "INPUT_PROCESSING", "TEMPLATE", "SAMPLING", "MODEL_SELECTION", "CACHE", "RUNNER_KERNEL", "DISTRIBUTED_IPC", "MODEL_LOADING", "REMOTE_CODE", "PLUGIN", "COMPILE_CACHE", "SCHEDULER", "STARTUP_HEALTH", "FAILURE", "PROFILER", "USAGE_STATS", "REPLAY", "OBSERVABILITY", "OUTPUT", "ERROR", "OUTPUT_PARSING", "IMAGE", "GENERIC", "MODEL_WEIGHTS", "MODEL_CODE", "MODEL_TEMPLATE", "MODEL_TOKENIZER", "MODEL_ARCH"]
    target: str
    before: Any = None
    after: Any = None
    certainty: Literal["CONFIRMED", "PARTIAL", "UNKNOWN"]
    evidence: list[ChangeEvidence] = Field(default_factory=list)
    related_checks: list[str] = Field(default_factory=list)
    unmapped: bool | None = None
    details: dict[str, Any] | None = None

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_none(handler(self), ("unmapped", "details"))


class Artifact(Strict):
    name: str
    path: str
    sha256: str
    bytes: int
    collected_at: datetime
    collector: str


class Change(GuardObject):
    kind: Literal[Kind.CHG] = Kind.CHG
    baseline: VersionPoint
    candidate: VersionPoint
    extractor: Extractor
    facts: list[ChangeFact] = Field(default_factory=list)
    reviewed_by: str | None = None
    artifacts: list[Artifact] | None = None
    analysis: dict[str, Any] | None = None

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_none(handler(self), ("artifacts", "analysis"))

    @model_validator(mode="after")
    def _unreviewed_max_partial(self) -> "Change":
        if self.reviewed_by is None and any(f.certainty == "CONFIRMED" for f in self.facts):
            raise ValueError("certainty is at most PARTIAL before human review")
        return self


# --- 4.4 PLAN ----------------------------------------------------------------


class PlanInputs(Strict):
    pack: Ref
    environments: list[Ref]
    policy: Ref
    change: Ref | None = None
    previous_plan: Ref | None = None

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_none(handler(self), ("previous_plan",))


class ConditionEval(Strict):
    environment: str | None = None  # 평가에 쓴 ENV id (update 모드에서 구별)
    fact: str
    expected: Any
    actual: Any = None
    origin: DataOrigin | None = None
    result: CondResult
    note: str | None = None


class SelectionTrigger(Strict):
    type: str
    refs: list[str] = Field(default_factory=list)


class CarriedOver(Strict):
    result: Ref
    evidence: Ref
    image_digest: str
    judged_at: datetime
    reason: str

    @model_validator(mode="after")
    def _exact_kinds(self):
        if self.result.kind != "RES" or self.evidence.kind != "EVD":
            raise ValueError("carried_over requires RES and EVD refs")
        return self


class PlanItem(Strict):
    check_id: str
    check_revision: int
    applicability: Applicability
    conditions: list[ConditionEval] = Field(default_factory=list)
    reason: str
    triggered_by_change: list[str] = Field(default_factory=list)
    selected: bool
    not_selected_reason: NotSelectedReason | None = None
    gaps: list[Gap] = Field(default_factory=list)
    priority: str
    executor: str | None = None
    triggers: list[SelectionTrigger] = Field(default_factory=list)
    carried_over: CarriedOver | None = None

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        data = _drop_none(handler(self), ("carried_over",))
        if not self.triggers:
            data.pop("triggers", None)
        return data

    @model_validator(mode="after")
    def _reason_iff_not_selected(self) -> "PlanItem":
        if self.selected == (self.not_selected_reason is not None):
            raise ValueError("not_selected_reason is required exactly when selected is false")
        if self.carried_over is not None and (self.selected or self.not_selected_reason != NotSelectedReason.CARRIED_OVER):
            raise ValueError("carried_over requires an unselected CARRIED_OVER item")
        if self.not_selected_reason == NotSelectedReason.CARRIED_OVER and self.carried_over is None:
            raise ValueError("CARRIED_OVER requires exact result/evidence refs")
        return self


class Plan(GuardObject):
    kind: Literal[Kind.PLAN] = Kind.PLAN
    mode: Mode
    inputs: PlanInputs
    items: list[PlanItem]
    inventory_complete: dict[str, bool]
    change_type: Literal["ENGINE_RELEASE", "ENGINE_PATCH", "MODEL", "INPUT", "INITIAL"] | None = None

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_none(handler(self), ("change_type",))

    def item(self, check_id: str) -> PlanItem:
        for it in self.items:
            if it.check_id == check_id:
                return it
        raise KeyError(check_id)


# --- 4.5 EVD -----------------------------------------------------------------


class PluginInfo(Strict):
    name: str
    version: str


class ExecutorInfo(PluginInfo):
    config_sha256: str
    # 이 실행기의 증거를 판정하는 기본 판정기(실행기 레지스트리 메타데이터에서 증거 생성 시 기록). 없으면 CheckDef.judge
    default_judge: str | None = None

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_none(handler(self), ("default_judge",))


class Operator(Strict):
    """사람이 입력한 증거의 입력자·시각(인증되지 않은 자기 신고)."""

    by: str = Field(min_length=1)
    at: datetime


class Execution(Strict):
    status: ExecStatus
    executor: ExecutorInfo | None = None
    adapter: PluginInfo | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    abort_reason: str | None = None
    operator: Operator | None = None
    override_reason: str | None = None
    cleanup_errors: list[str] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        data = _drop_none(handler(self), ("operator", "override_reason"))
        if not self.cleanup_errors:
            data.pop("cleanup_errors", None)
        return data


class NormalControl(Strict):
    attempted: bool
    succeeded: bool | None = None
    detail: str = ""


class Challenge(Strict):
    step: str
    attempted: bool
    observations: dict[str, Any] = Field(default_factory=dict)


class Observation(Strict):
    value: Any = None
    collected: bool

    @model_validator(mode="after")
    def _uncollected_has_no_value(self) -> "Observation":
        # 수집 실패는 값을 비우거나 추정하지 않는다
        if not self.collected and self.value is not None:
            raise ValueError("uncollected observation must not carry a value")
        return self


class Comparability(Strict):
    model_inventory_sha256: str | None = None
    fixture_sha256: str | None = None
    config_sha256: str | None = None
    gpu: str | None = None
    driver: str | None = None
    cpu: CpuProfile | None = None

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_none(handler(self), ("cpu",))

    @model_validator(mode="after")
    def _one_backend(self):
        if self.cpu is not None and (self.gpu is not None or self.driver is not None):
            raise ValueError("CPU and GPU comparison profiles cannot be mixed")
        return self


class Evidence(GuardObject):
    """run 단계의 유일한 산출물. 판정 필드가 없다(extra=forbid)."""

    kind: Literal[Kind.EVD] = Kind.EVD
    plan: Ref
    check_id: str
    side: Side
    environment: Ref
    execution: Execution
    normal_control: NormalControl
    challenges: list[Challenge] = Field(default_factory=list)
    observations: dict[str, Observation] = Field(default_factory=dict)
    artifacts: list[Artifact] = Field(default_factory=list)
    comparability: Comparability = Field(default_factory=Comparability)

    @model_validator(mode="after")
    def _not_measured_unless_completed(self) -> "Evidence":
        # INV-02: 실행하지 않은 시험을 MEASURED로 표시하지 않는다
        if self.data_origin == DataOrigin.MEASURED and self.execution.status != ExecStatus.COMPLETED:
            raise ValueError("evidence of an unfinished execution cannot be MEASURED")
        if self.data_origin == DataOrigin.MEASURED and self.execution.operator is not None:
            raise ValueError("human-entered evidence cannot be MEASURED (DECLARED only)")
        return self


# --- 4.6 RES -----------------------------------------------------------------


class JudgeInfo(PluginInfo):
    params_sha256: str


class Verdicts(Strict):
    security: Verdict
    functional: Verdict
    performance: Verdict


class ExceptionMark(Strict):
    check_id: str
    expires_at: datetime


class Result(GuardObject):
    kind: Literal[Kind.RES] = Kind.RES
    evidence: Ref
    check_id: str
    side: Side
    judge: JudgeInfo
    verdicts: Verdicts
    reasons: list[str] = Field(default_factory=list)
    missing_observations: list[str] = Field(default_factory=list)
    exception: ExceptionMark | None = None
    verification_level: DataOrigin | None = None


# --- 4.7 RPT -----------------------------------------------------------------


class SideResult(Strict):
    result: Ref | None = None
    security: Verdict
    functional: Verdict | None = None
    performance: Verdict | None = None
    verification_level: DataOrigin | None = None


class ExceptionDisplay(Strict):
    status: ExceptionStatus
    reason: str
    expires_at: datetime | None = None


class FunctionalScope(Strict):
    probed: int
    of: int | None
    label: str


class Replaced(Strict):
    """같은 PLAN·점검·side에서 최신 증거가 출처가 다른 이전 COMPLETED 증거를 대체함."""

    source: Literal["automated", "manual"]
    revision: int
    executor: str | None = None
    reason: str | None = None


class CheckRow(Strict):
    check_id: str
    purpose: str
    area: str
    question: str
    review_order: str
    impact: str | None = None
    applicability: Applicability
    selected: bool
    tested_while_undetermined: bool = False
    single: SideResult | None = None
    baseline: SideResult | None = None
    candidate: SideResult | None = None
    comparison: Comparison | None = None
    functional_comparison: Comparison | None = None
    comparison_reasons: list[str] | None = None
    gaps: list[Gap] = Field(default_factory=list)
    exception: ExceptionDisplay | None = None
    functional_scope: FunctionalScope | None = None  # "기능 확인 범위: N / 등록 경로 M (표본)"
    evidence_source: Literal["automated", "manual"] | None = None  # 판정에 쓰인 최신 증거의 출처(실행 완료 시)
    replaced: Replaced | None = None
    triggers: list[SelectionTrigger] = Field(default_factory=list)
    carried_over: CarriedOver | None = None
    previous_result: SideResult | None = None

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        data = _drop_none(handler(self), ("functional_scope", "evidence_source", "replaced",
                                          "functional_comparison", "comparison_reasons", "carried_over", "previous_result"))
        if not self.triggers:
            data.pop("triggers", None)
        return data


class EvidenceCount(Strict):
    manual: int = 0
    automated: int = 0
    not_run: int = 0


class AxisSummary(Strict):
    security: Verdict
    inventory_complete: bool | None = None


class NotSelected(Strict):
    check_id: str
    reason: str


class Approval(Strict):
    approved_by: str
    approved_at: datetime
    decision: Literal["ACCEPT", "REJECT"]
    note: str = ""


class Report(GuardObject):
    kind: Literal[Kind.RPT] = Kind.RPT
    mode: Mode
    official: bool
    inputs_manifest: list[Ref]
    pack_review_status: ReviewStatus
    policy_status: PolicyStatus
    by_check: list[CheckRow]
    by_purpose: dict[str, AxisSummary]
    by_area: dict[str, AxisSummary]
    not_selected: list[NotSelected] = Field(default_factory=list)
    manual_required: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    approval: Approval | None = None
    evidence_counts: dict[str, EvidenceCount] | None = None  # 영역별 수동 입력·자동 측정 건수

    change_summary: dict[str, Any] | None = None
    carry_counts: dict[str, int] | None = None

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_none(handler(self), ("evidence_counts", "change_summary", "carry_counts"))


OBJECT_CLASSES: dict[str, type[GuardObject]] = {
    "ENV": Environment,
    "POL": Policy,
    "CHG": Change,
    "PLAN": Plan,
    "EVD": Evidence,
    "RES": Result,
    "RPT": Report,
}


def object_from_dict(data: dict) -> GuardObject:
    return OBJECT_CLASSES[data["kind"]].model_validate(data)
