"""환경·정책 저장소. 저장은 항상 새 판본이고 기존 파일을 덮어쓰지 않는다.

인터페이스(EnvironmentStore, PolicyStore) 뒤에 두어 나중에 Postgres 구현으로 바꿀 수 있게 한다.
파일 구현은 `<root>/<kind>/<doc_id>/<revision 6자리>.json`에 판본마다 새 파일을 배타 생성(O_EXCL)한다.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .contracts import canonical_sha256

_DOC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class RevisionExists(FileExistsError):
    """같은 판본 파일이 이미 있음(덮어쓰기 금지)."""


class InvalidDocId(ValueError):
    pass


@dataclass(frozen=True)
class StoredRevision:
    doc_id: str
    revision: int
    saved_at: str
    sha256: str
    status: str
    label: str | None
    document: dict[str, Any]

    def meta(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "revision": self.revision,
            "saved_at": self.saved_at,
            "sha256": self.sha256,
            "status": self.status,
            "label": self.label,
            "target_ref": self.target_ref,
        }

    @property
    def target_ref(self) -> str | None:
        doc = self.document
        target = doc.get("target")
        value = target.get("target_ref") if isinstance(target, dict) else doc.get("target_ref")
        return value if isinstance(value, str) else None


class DocumentStore(Protocol):
    def list_latest(self) -> list[StoredRevision]: ...
    def revisions(self, doc_id: str) -> list[StoredRevision]: ...
    def get(self, doc_id: str, revision: int) -> StoredRevision | None: ...
    def next_revision(self, doc_id: str) -> int: ...
    def append(self, doc_id: str, revision: int, document: dict[str, Any], *, status: str, label: str | None) -> StoredRevision: ...


class EnvironmentStore(DocumentStore, Protocol):
    """byul-environment 판본 저장소."""


class PolicyStore(DocumentStore, Protocol):
    """byul-policy 판본 저장소."""


def now_utc() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class FileDocumentStore:
    def __init__(self, root: Path, kind: str) -> None:
        self.dir = root / kind
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _doc_dir(self, doc_id: str) -> Path:
        if not _DOC_ID.fullmatch(doc_id) or doc_id in (".", ".."):
            raise InvalidDocId(doc_id)
        return self.dir / doc_id

    @staticmethod
    def _read(path: Path) -> StoredRevision:
        data = json.loads(path.read_text(encoding="utf-8"))
        return StoredRevision(
            doc_id=data["doc_id"],
            revision=data["revision"],
            saved_at=data["saved_at"],
            sha256=data["sha256"],
            status=data["status"],
            label=data.get("label"),
            document=data["document"],
        )

    def revisions(self, doc_id: str) -> list[StoredRevision]:
        doc_dir = self._doc_dir(doc_id)
        if not doc_dir.is_dir():
            return []
        return [self._read(p) for p in sorted(doc_dir.glob("[0-9]*.json"))]

    def list_latest(self) -> list[StoredRevision]:
        out = []
        for doc_dir in sorted(p for p in self.dir.iterdir() if p.is_dir()):
            files = sorted(doc_dir.glob("[0-9]*.json"))
            if files:
                out.append(self._read(files[-1]))
        return out

    def get(self, doc_id: str, revision: int) -> StoredRevision | None:
        path = self._doc_dir(doc_id) / f"{revision:06d}.json"
        return self._read(path) if path.is_file() else None

    def next_revision(self, doc_id: str) -> int:
        existing = self.revisions(doc_id)
        return existing[-1].revision + 1 if existing else 1

    def append(
        self, doc_id: str, revision: int, document: dict[str, Any], *, status: str, label: str | None
    ) -> StoredRevision:
        doc_dir = self._doc_dir(doc_id)
        stored = StoredRevision(
            doc_id=doc_id,
            revision=revision,
            saved_at=now_utc(),
            sha256=canonical_sha256(document),
            status=status,
            label=label,
            document=document,
        )
        payload = json.dumps({**stored.meta(), "document": document}, ensure_ascii=False, indent=2).encode("utf-8")
        with self._lock:
            doc_dir.mkdir(parents=True, exist_ok=True)
            path = doc_dir / f"{revision:06d}.json"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                raise RevisionExists(str(path)) from None
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        return stored
