"""SPEC §3 CheckDef(팩에 담긴 정의)와 팩 매니페스트."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import Ref, Strict
from .enums import ReviewStatus
from .hashing import canonical_json, sha256_hex


class Condition(Strict):
    """적용 조건 하나: 사실 키와 기대값."""

    fact: str
    eq: Any


class AppliesWhen(Strict):
    always: bool = False
    all: list[Condition] = Field(default_factory=list)
    prose: str | None = None

    @model_validator(mode="after")
    def _always_alone(self) -> "AppliesWhen":
        if self.always and self.all:
            raise ValueError("applies_when.always cannot be combined with conditions")
        return self


class PolicyParam(Strict):
    key: str
    type: str
    required: bool = True


class ObsSpec(Strict):
    key: str
    type: str


class JudgeSpec(Strict):
    plugin: str
    params: dict[str, Any] = Field(default_factory=dict)


class FunctionalProbe(Strict):
    """기능 축 표본 요청 1개(허용 키, Gateway 경유). 본문 문자열의 "{model}"은 실행 시 ENV 모델 이름으로 바꾼다."""

    method: str
    path: str
    body: dict[str, Any] | None = None
    expect: list[int] = Field(min_length=1)

    @property
    def id(self) -> str:
        return f"{self.method.upper()} {self.path}"


class FunctionalScopeSpec(Strict):
    """보고서의 "기능 확인 범위: N / 등록 경로 M (표본)" 표시용: 어느 관측을 셀지."""

    probed_obs: str
    of_obs: str


class Legacy(Strict):
    q_ids: list[str] = Field(default_factory=list)
    scenario_ids: list[str] = Field(default_factory=list)
    subtest_ids: list[str] = Field(default_factory=list)


class CheckDef(BaseModel):
    # 원문 보존용 필드가 늘 수 있어 extra는 허용, 핵심 필드만 엄격히
    model_config = ConfigDict(extra="allow")

    id: str = Field(pattern=r"^(SUP|REQ|PLT|LIF|OBS)-\d{2}$")
    revision: int = Field(ge=1)
    retired: bool = False
    area: Literal["SUP", "REQ", "PLT", "LIF", "OBS"]
    purpose: str
    secondary_purposes: list[str] = Field(default_factory=list)
    question: str
    review_order: str
    impact: Literal["HIGH", "MEDIUM", "LOW"] | None = None
    applies_when: AppliesWhen
    policy_params: list[PolicyParam] = Field(default_factory=list)
    normal_control: str
    challenge_steps: list[str]
    required_observations: list[ObsSpec] = Field(default_factory=list)
    required_observations_prose: list[str] = Field(default_factory=list)
    satisfied_if: str
    fail_criteria: str
    unknown_criteria: str
    remediation: str | None = None
    executor: str | None = None
    judge: JudgeSpec | None = None
    functional_probes: list[FunctionalProbe] = Field(default_factory=list)
    functional_scope: FunctionalScopeSpec | None = None
    legacy: Legacy = Field(default_factory=Legacy)

    @model_validator(mode="after")
    def _area_matches_id(self) -> "CheckDef":
        if not self.id.startswith(self.area + "-"):
            raise ValueError(f"{self.id}: area {self.area} mismatch")
        return self


class PackFile(Strict):
    path: str
    sha256: str


class PackReview(Strict):
    status: ReviewStatus
    reviewer: str | None = None
    reviewed_at: datetime | None = None


class PackManifest(Strict):
    pack_id: str = Field(pattern=r"^PACK-[A-Z0-9_-]+$")
    revision: int = Field(ge=1)
    engines: list[str]
    checks: list[str]
    files_sha256: list[PackFile]
    review: PackReview


class Pack(BaseModel):
    manifest: PackManifest
    checks: dict[str, CheckDef]
    path: Path

    def ref(self) -> Ref:
        return Ref(
            kind="PACK",
            id=self.manifest.pack_id,
            revision=self.manifest.revision,
            sha256=sha256_hex(canonical_json(self.manifest.model_dump(mode="json"))),
        )

    @property
    def reviewed(self) -> bool:
        return self.manifest.review.status == ReviewStatus.REVIEWED

    def active_checks(self) -> list[CheckDef]:
        return [self.checks[c] for c in self.manifest.checks if not self.checks[c].retired]


class PackIntegrityError(ValueError):
    pass


def load_pack(path: Path) -> Pack:
    """팩을 읽고 매니페스트의 파일 해시를 검증한다."""
    path = Path(path)
    manifest = PackManifest.model_validate(yaml.safe_load((path / "pack.yaml").read_text(encoding="utf-8")))
    listed = {f.path for f in manifest.files_sha256}
    actual = {p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file() and p.name != "pack.yaml"}
    if listed != actual:
        raise PackIntegrityError(f"pack files differ from manifest: {sorted(listed ^ actual)}")
    for f in manifest.files_sha256:
        if sha256_hex((path / f.path).read_bytes()) != f.sha256:
            raise PackIntegrityError(f"sha256 mismatch: {f.path}")
    checks = {}
    for cid in manifest.checks:
        checks[cid] = CheckDef.model_validate(
            yaml.safe_load((path / "checks" / f"{cid}.yaml").read_text(encoding="utf-8"))
        )
    return Pack(manifest=manifest, checks=checks, path=path)
