"""점검 실행기 인터페이스 (SPEC §7.2). 실행기는 증거 초안만 만든다. 판정하지 않는다(INV-14)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import Field, model_validator

from guard.adapters.base import EngineAdapter
from guard.models import CheckDef, DataOrigin, Environment, ExecStatus
from guard.models.base import Strict
from guard.models.objects import Challenge, Comparability, NormalControl, Observation


class DraftArtifact(Strict):
    name: str
    path: Path  # workdir 안의 로컬 파일. run 코어가 내용 주소 저장소로 옮긴다
    collector: str


class EvidenceDraft(Strict):
    status: ExecStatus
    data_origin: DataOrigin
    normal_control: NormalControl
    challenges: list[Challenge] = Field(default_factory=list)
    observations: dict[str, Observation] = Field(default_factory=dict)
    artifacts: list[DraftArtifact] = Field(default_factory=list)
    comparability: Comparability = Field(default_factory=Comparability)
    config: dict[str, Any] = Field(default_factory=dict)  # config_sha256의 원본
    started_at: datetime | None = None
    finished_at: datetime | None = None
    abort_reason: str | None = None
    cleanup_errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _not_measured_unless_completed(self) -> "EvidenceDraft":
        if self.data_origin == DataOrigin.MEASURED and self.status != ExecStatus.COMPLETED:
            raise ValueError("unfinished execution cannot be MEASURED (INV-02)")
        return self


@runtime_checkable
class Executor(Protocol):
    name: str
    version: str

    def run(
        self,
        check: CheckDef,
        env: Environment,
        policy_values: dict,
        adapter: EngineAdapter | None,
        workdir: Path,
    ) -> EvidenceDraft: ...
