"""SQLite append-only 객체 저장소 + 증거 파일 내용 주소 저장 (SPEC §2.2, §4.5, INV-16).

쓸 때 해시·참조를 검증한다. 읽을 때 전체 재검증은 하지 않고 `verify()`로 일괄 검증한다(SPEC §9).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from guard.models import GuardObject, Ref, canonical_json, object_from_dict, sha256_hex

_SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    kind TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (id, revision)
);
CREATE TRIGGER IF NOT EXISTS objects_no_update BEFORE UPDATE ON objects
BEGIN SELECT RAISE(ABORT, 'append-only: UPDATE forbidden'); END;
CREATE TRIGGER IF NOT EXISTS objects_no_delete BEFORE DELETE ON objects
BEGIN SELECT RAISE(ABORT, 'append-only: DELETE forbidden'); END;
"""


class AppendOnlyError(Exception):
    pass


class IntegrityError(Exception):
    pass


@dataclass(frozen=True)
class StoredArtifact:
    path: str  # 저장소 루트 기준 상대 경로
    sha256: str
    bytes: int


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts").mkdir(exist_ok=True)
        self.db_path = self.root / "store.sqlite"
        with closing(self._connect()) as con, con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    # --- 객체 ---------------------------------------------------------------

    def put(self, obj: GuardObject) -> Ref:
        if not obj.hash_ok():
            raise IntegrityError(f"{obj.id} rev {obj.revision}: content_sha256 missing or wrong")
        for ref in obj.refs:
            self._check_ref(ref)
        body = canonical_json(obj.to_dict()).decode("utf-8")
        with closing(self._connect()) as con, con:
            row = con.execute(
                "SELECT content_sha256 FROM objects WHERE id=? AND revision=?", (obj.id, obj.revision)
            ).fetchone()
            if row is not None:
                if row[0] == obj.content_sha256:
                    return obj.ref()  # 같은 내용의 재저장은 무해
                raise AppendOnlyError(f"{obj.id} rev {obj.revision} already exists with different content")
            latest = con.execute("SELECT MAX(revision) FROM objects WHERE id=?", (obj.id,)).fetchone()[0] or 0
            if obj.revision != latest + 1:
                raise AppendOnlyError(f"{obj.id}: next revision must be {latest + 1}, got {obj.revision}")
            con.execute(
                "INSERT INTO objects (id, revision, kind, content_sha256, created_at, body) VALUES (?,?,?,?,?,?)",
                (obj.id, obj.revision, str(obj.kind), obj.content_sha256, obj.to_dict()["created_at"], body),
            )
        return obj.ref()

    def _check_ref(self, ref: Ref) -> None:
        if ref.kind == "PACK":
            return  # 팩은 객체 저장소 밖(파일). Ref의 해시는 팩 로더가 검증한다
        with closing(self._connect()) as con:
            row = con.execute(
                "SELECT content_sha256 FROM objects WHERE id=? AND revision=?", (ref.id, ref.revision)
            ).fetchone()
        if row is None:
            raise IntegrityError(f"ref not found: {ref.id} rev {ref.revision}")
        if row[0] != ref.sha256:
            raise IntegrityError(f"ref sha256 mismatch: {ref.id} rev {ref.revision}")

    def get(self, id_: str, revision: int) -> GuardObject:
        with closing(self._connect()) as con:
            row = con.execute("SELECT body FROM objects WHERE id=? AND revision=?", (id_, revision)).fetchone()
        if row is None:
            raise KeyError(f"{id_} rev {revision}")
        return object_from_dict(json.loads(row[0]))

    def resolve(self, ref: Ref) -> GuardObject:
        """정확한 revision을 읽고 해시를 확인한다. 최신 revision으로 바꿔 읽지 않는다."""
        obj = self.get(ref.id, ref.revision)
        if obj.content_sha256 != ref.sha256 or not obj.hash_ok():
            raise IntegrityError(f"ref sha256 mismatch: {ref.id} rev {ref.revision}")
        return obj

    def revisions(self, id_: str) -> list[int]:
        with closing(self._connect()) as con:
            return [r[0] for r in con.execute("SELECT revision FROM objects WHERE id=? ORDER BY revision", (id_,))]

    def latest(self, id_: str) -> GuardObject:
        """CLI 진입점용(사람이 ID만 줄 때). 객체 간 참조에는 쓰지 않는다."""
        revs = self.revisions(id_)
        if not revs:
            raise KeyError(id_)
        return self.get(id_, revs[-1])

    def iter_kind(self, kind: str) -> Iterator[GuardObject]:
        with closing(self._connect()) as con:
            rows = con.execute("SELECT body FROM objects WHERE kind=? ORDER BY created_at, id, revision", (kind,)).fetchall()
        for (body,) in rows:
            yield object_from_dict(json.loads(body))

    def find_latest(self, kind: str, pred: Callable[[GuardObject], bool]) -> list[GuardObject]:
        """조건에 맞는 객체들의 id별 최신 revision."""
        latest: dict[str, GuardObject] = {}
        for obj in self.iter_kind(kind):
            if pred(obj) and (obj.id not in latest or obj.revision > latest[obj.id].revision):
                latest[obj.id] = obj
        return list(latest.values())

    # --- 증거 파일 ------------------------------------------------------------

    def put_artifact(self, data: bytes) -> StoredArtifact:
        digest = sha256_hex(data)
        rel = f"artifacts/{digest}"
        path = self.root / rel
        if path.exists():
            if sha256_hex(path.read_bytes()) != digest:
                raise IntegrityError(f"artifact corrupted: {rel}")
        else:
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return StoredArtifact(path=rel, sha256=digest, bytes=len(data))

    # --- 일괄 검증 (guard verify) ---------------------------------------------

    def verify(self) -> list[str]:
        problems: list[str] = []
        with closing(self._connect()) as con:
            rows = con.execute("SELECT id, revision, content_sha256, body FROM objects").fetchall()
        known = {(r[0], r[1]): r[2] for r in rows}
        for id_, rev, sha, body in rows:
            try:
                obj = object_from_dict(json.loads(body))
            except Exception as exc:  # noqa: BLE001 - 검증 보고용
                problems.append(f"{id_} rev {rev}: unreadable ({exc})")
                continue
            if obj.content_sha256 != sha or not obj.hash_ok():
                problems.append(f"{id_} rev {rev}: content_sha256 mismatch")
            for ref in obj.refs:
                if ref.kind != "PACK" and known.get((ref.id, ref.revision)) != ref.sha256:
                    problems.append(f"{id_} rev {rev}: broken ref {ref.id} rev {ref.revision}")
            for art in getattr(obj, "artifacts", []) or []:
                p = self.root / art.path
                if not p.exists() or sha256_hex(p.read_bytes()) != art.sha256:
                    problems.append(f"{id_} rev {rev}: artifact {art.path} missing or changed")
        for p in sorted((self.root / "artifacts").iterdir()):
            if p.is_file() and sha256_hex(p.read_bytes()) != p.name:
                problems.append(f"artifact {p.name}: content does not match name")
        return problems
