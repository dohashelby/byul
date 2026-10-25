"""SPEC §2.4 공통 enum. 데이터에는 영문 enum만 쓴다(한국어는 보고서 렌더링에서만)."""

from enum import Enum


class _StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class Kind(_StrEnum):
    ENV = "ENV"
    POL = "POL"
    CHG = "CHG"
    PLAN = "PLAN"
    EVD = "EVD"
    RES = "RES"
    RPT = "RPT"


class DataOrigin(_StrEnum):
    MEASURED = "MEASURED"
    DECLARED = "DECLARED"
    EXAMPLE = "EXAMPLE"


# 강한 것 → 약한 것
ORIGIN_STRENGTH = {DataOrigin.MEASURED: 2, DataOrigin.DECLARED: 1, DataOrigin.EXAMPLE: 0}


class Applicability(_StrEnum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNDETERMINED = "UNDETERMINED"
    CONFLICT = "CONFLICT"


class Verdict(_StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ExecStatus(_StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    NOT_RUN = "NOT_RUN"


class Comparison(_StrEnum):
    NO_CHANGE = "NO_CHANGE"
    REGRESSED = "REGRESSED"
    FIXED = "FIXED"
    STILL_FAILING = "STILL_FAILING"
    UNKNOWN = "UNKNOWN"
    NOT_COMPARABLE = "NOT_COMPARABLE"


class Gap(_StrEnum):
    NO_EXECUTOR = "NO_EXECUTOR"
    NO_POLICY_VALUE = "NO_POLICY_VALUE"
    NO_FIXTURE = "NO_FIXTURE"
    NO_ENV_FACT = "NO_ENV_FACT"
    ADAPTER_UNSUPPORTED = "ADAPTER_UNSUPPORTED"


class Mode(_StrEnum):
    audit = "audit"
    update = "update"


class Side(_StrEnum):
    baseline = "baseline"
    candidate = "candidate"
    single = "single"


class CondResult(_StrEnum):
    """적용 조건 하나의 평가 결과."""

    TRUE = "TRUE"
    FALSE = "FALSE"
    NULL = "NULL"  # 근거 없음·값 모름
    CONFLICT = "CONFLICT"


class PolicyStatus(_StrEnum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"


class ReviewStatus(_StrEnum):
    DRAFT = "DRAFT"
    REVIEWED = "REVIEWED"


class NotSelectedReason(_StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    POLICY_SCOPE = "POLICY_SCOPE"
    RETIRED = "RETIRED"


class ExceptionStatus(_StrEnum):
    APPROVED = "APPROVED"
    EXPIRED = "EXPIRED"
    UNAPPROVED = "UNAPPROVED"
