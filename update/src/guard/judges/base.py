"""판정 플러그인 인터페이스 (SPEC §7.2, X4). 순수 함수: 네트워크·파일 쓰기·시계 접근 금지(INV-14)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import Field

from guard.models import CheckDef, Evidence, Verdict
from guard.models.base import Strict

AXES = ("security", "functional", "performance")


class JudgeOutput(Strict):
    verdicts: dict[str, Verdict]  # 축별. 빠진 축은 코어가 UNKNOWN으로 둔다
    reasons: list[str] = Field(default_factory=list)
    missing_observations: list[str] = Field(default_factory=list)


@runtime_checkable
class Judge(Protocol):
    name: str
    version: str

    def judge(self, check: CheckDef, evidence: Evidence, params: dict, policy_values: dict) -> JudgeOutput: ...
