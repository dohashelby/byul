"""SPEC §2.2 모든 객체의 공통 필드."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import ORIGIN_STRENGTH, DataOrigin, Kind
from .hashing import object_sha256


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Ref(Strict):
    """항상 정확한 revision과 해시까지 고정한 참조."""

    kind: str
    id: str
    revision: int
    sha256: str


class GuardObject(Strict):
    kind: Kind
    id: str
    revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utcnow)
    data_origin: DataOrigin
    content_sha256: str = ""
    refs: list[Ref] = Field(default_factory=list)

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware (UTC)")
        return v.astimezone(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def compute_hash(self) -> str:
        return object_sha256(self.to_dict())

    def seal(self) -> Self:
        """해시를 채운 사본."""
        return self.model_copy(update={"content_sha256": self.compute_hash()})

    def hash_ok(self) -> bool:
        return bool(self.content_sha256) and self.content_sha256 == self.compute_hash()

    def ref(self) -> Ref:
        if not self.hash_ok():
            raise ValueError(f"{self.id} rev {self.revision}: not sealed")
        return Ref(kind=str(self.kind), id=self.id, revision=self.revision, sha256=self.content_sha256)


def weakest_origin(origins: Iterable[DataOrigin | None]) -> DataOrigin | None:
    """INV-12: 하나라도 null이면 null. 빈 목록도 근거 없음 → null."""
    items = list(origins)
    if not items or any(o is None for o in items):
        return None
    return min(items, key=lambda o: ORIGIN_STRENGTH[DataOrigin(o)])
