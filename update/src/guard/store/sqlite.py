"""SQLite append-only 객체 저장소 + 증거 파일 내용 주소 저장 (SPEC §2.2, §4.5, INV-16).

쓸 때 해시·참조를 검증한다. 읽을 때 전체 재검증은 하지 않고 `verify()`로 일괄 검증한다(SPEC §9).
"""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import os
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from guard.models import GuardObject, Ref, canonical_json, object_from_dict, sha256_hex
from guard.models.objects import Evidence, Plan, Result

from .integrity import (IntegrityError, attachments, file_sha256, object_references, ref_key,
                        result_policy, validate_relationships, verify_attachment)

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


_OBJECT_COLUMNS = "id, revision, kind, content_sha256, created_at, body"


def _schema_statement(sql: str) -> str:
    return " ".join(sql.replace(" IF NOT EXISTS", "").split()).rstrip(";")


_EXPECTED_SCHEMA = {_schema_statement(sql) for sql in re.split(r";\s*(?=CREATE)", _SCHEMA.strip())}


def _read_object(row) -> GuardObject:
    ident, revision, kind, digest, created_at, body = row
    data = json.loads(body)
    obj = object_from_dict(data)
    if (obj.id, obj.revision, str(obj.kind), obj.content_sha256, data.get("created_at")) != (
        ident, revision, kind, digest, created_at
    ):
        raise IntegrityError(f"{ident} rev {revision}: database index differs from object body")
    return obj


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
        if getattr(self, "_read_only", False):
            options = "?mode=ro&immutable=1" if self._immutable else "?mode=ro"
            connection = sqlite3.connect(self.db_path.resolve().as_uri() + options, uri=True)
            connection.execute("PRAGMA trusted_schema = OFF")
            return connection
        return sqlite3.connect(self.db_path)

    @classmethod
    def read_only(cls, root: Path, *, immutable: bool = False):
        """기존 보존본만 읽는다. 준비 검사에서는 디렉토리나 DB를 만들지 않는다."""
        obj = cls.__new__(cls)
        obj.root = Path(root)
        obj.db_path = obj.root / "store.sqlite"
        if not obj.db_path.is_file():
            raise ValueError("previous inputs require an existing object store")
        obj._read_only = True
        obj._immutable = immutable
        return obj

    # --- 객체 ---------------------------------------------------------------

    def put(self, obj: GuardObject) -> Ref:
        return self.put_many([obj])[0]

    def put_many(self, objects: list[GuardObject]) -> list[Ref]:
        """Append a batch in one transaction; any failure rolls back every object."""
        if not objects:
            return []
        with closing(self._connect()) as con, con:
            con.execute("BEGIN IMMEDIATE")
            return [self._put(con, obj) for obj in objects]

    def _put(self, con: sqlite3.Connection, obj: GuardObject) -> Ref:
        self._validate_object(con, obj, artifacts=True)
        body = canonical_json(obj.to_dict()).decode("utf-8")
        row = con.execute(
            "SELECT content_sha256 FROM objects WHERE id=? AND revision=?", (obj.id, obj.revision)
        ).fetchone()
        if row is not None:
            if row[0] == obj.content_sha256:
                return obj.ref()  # 같은 내용의 재저장은 무해
            raise AppendOnlyError(f"{obj.id} rev {obj.revision} already exists with different content")
        previous = con.execute(
            "SELECT revision, kind FROM objects WHERE id=? ORDER BY revision DESC LIMIT 1", (obj.id,)
        ).fetchone()
        latest = previous[0] if previous is not None else 0
        if obj.revision != latest + 1:
            raise AppendOnlyError(f"{obj.id}: next revision must be {latest + 1}, got {obj.revision}")
        if previous is not None and previous[1] != str(obj.kind):
            raise AppendOnlyError(f"{obj.id}: revision changes its object kind")
        self._check_slot(con, obj, latest)
        con.execute(
            "INSERT INTO objects (id, revision, kind, content_sha256, created_at, body) VALUES (?,?,?,?,?,?)",
            (obj.id, obj.revision, str(obj.kind), obj.content_sha256, obj.to_dict()["created_at"], body),
        )
        return obj.ref()

    def validate_object(self, obj: GuardObject, *, artifacts: bool = True) -> None:
        """Check a sealed object and its exact references without changing the store."""
        with closing(self._connect()) as con:
            con.execute("BEGIN")
            self._validate_object(con, obj, artifacts=artifacts)

    def _validate_object(self, con: sqlite3.Connection, obj: GuardObject, *, artifacts: bool) -> None:
        if not obj.hash_ok():
            raise IntegrityError(f"{obj.id} rev {obj.revision}: content_sha256 missing or wrong")
        resolved = {}

        def resolve(ref):
            key = ref_key(ref)
            if key not in resolved:
                try:
                    resolved[key] = self._resolve(con, ref)
                except KeyError as exc:
                    raise IntegrityError(f"ref not found: {ref.id} rev {ref.revision}") from exc
            return resolved[key]

        for ref in object_references(obj):
            if ref.kind != "PACK":
                resolve(ref)
        validate_relationships(obj, resolve)
        if artifacts:
            for artifact in attachments(obj):
                verify_attachment(self.root, artifact)
        self._check_change_artifact_refs(obj)

    @staticmethod
    def _slot(obj, resolve):
        if isinstance(obj, Evidence):
            return "EVD", ref_key(obj.plan), obj.check_id, obj.side
        if isinstance(obj, Result):
            evidence = resolve(obj.evidence)
            if not isinstance(evidence, Evidence):
                raise IntegrityError("RES logical slot requires its stored EVD")
            plan = resolve(evidence.plan)
            if not isinstance(plan, Plan):
                raise IntegrityError("RES logical slot requires the EVD's stored PLAN")
            policy = result_policy(obj, plan)
            return "RES", obj.evidence.id, ref_key(policy), getattr(obj, "evaluation_mode", None)
        return None

    def _check_slot(self, con: sqlite3.Connection, obj: GuardObject, latest: int) -> None:
        if not isinstance(obj, (Evidence, Result)):
            return

        def resolve(ref):
            return self._resolve(con, ref)

        slot = self._slot(obj, resolve)
        if latest and slot != self._slot(self._get(con, obj.id, latest), resolve):
            raise AppendOnlyError(f"{obj.id}: revision changes its logical slot")
        if isinstance(obj, Evidence):
            rows = con.execute(
                f"SELECT {_OBJECT_COLUMNS} FROM objects WHERE kind='EVD' AND id != ? "
                "AND json_extract(body, '$.plan.id') = ? AND json_extract(body, '$.check_id') = ? "
                "AND json_extract(body, '$.side') = ?",
                (obj.id, obj.plan.id, obj.check_id, obj.side),
            )
        else:
            rows = con.execute(
                f"SELECT {_OBJECT_COLUMNS} FROM objects WHERE kind='RES' AND id != ? "
                "AND json_extract(body, '$.evidence.id') = ?",
                (obj.id, obj.evidence.id),
            )
        if any(self._slot(_read_object(row), resolve) == slot for row in rows):
            raise AppendOnlyError(f"{obj.kind} logical slot already has another ID")

    def get(self, id_: str, revision: int) -> GuardObject:
        with closing(self._connect()) as con:
            return self._get(con, id_, revision)

    @staticmethod
    def _get(con, id_: str, revision: int) -> GuardObject:
        row = con.execute(f"SELECT {_OBJECT_COLUMNS} FROM objects WHERE id=? AND revision=?", (id_, revision)).fetchone()
        if row is None:
            raise KeyError(f"{id_} rev {revision}")
        return _read_object(row)

    def resolve(self, ref: Ref) -> GuardObject:
        """정확한 revision을 읽고 해시를 확인한다. 최신 revision으로 바꿔 읽지 않는다."""
        with closing(self._connect()) as con:
            return self._resolve(con, ref)

    @classmethod
    def _resolve(cls, con, ref: Ref) -> GuardObject:
        obj = cls._get(con, ref.id, ref.revision)
        if ref_key(ref) != (str(obj.kind), obj.id, obj.revision, obj.content_sha256) or not obj.hash_ok():
            raise IntegrityError(f"ref identity or sha256 mismatch: {ref.id} rev {ref.revision}")
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
            rows = con.execute(f"SELECT {_OBJECT_COLUMNS} FROM objects WHERE kind=? ORDER BY created_at, id, revision", (kind,)).fetchall()
        for row in rows:
            yield _read_object(row)

    def find_latest(self, kind: str, pred: Callable[[GuardObject], bool]) -> list[GuardObject]:
        """조건에 맞는 객체들의 id별 최신 revision."""
        latest: dict[str, GuardObject] = {}
        for obj in self.iter_kind(kind):
            if pred(obj) and (obj.id not in latest or obj.revision > latest[obj.id].revision):
                latest[obj.id] = obj
        return list(latest.values())

    # --- 증거 파일 ------------------------------------------------------------

    def put_artifact(self, data: bytes) -> StoredArtifact:
        if getattr(self, "_read_only", False):
            raise IntegrityError("read-only object store")
        digest = sha256_hex(data)
        rel = f"artifacts/{digest}"
        path = self.root / rel
        if path.is_symlink() or path.parent.is_symlink():
            raise IntegrityError('artifact symlink refused')
        if path.exists():
            if not path.is_file() or file_sha256(path) != digest:
                raise IntegrityError(f"artifact corrupted: {rel}")
        else:
            staging = self.root / 'artifact-staging'
            staging.mkdir(exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=staging, delete=False) as stream:
                tmp = Path(stream.name)
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            try:
                try: os.link(tmp, path)
                except FileExistsError:
                    if path.is_symlink() or not path.is_file() or file_sha256(path) != digest:
                        raise IntegrityError('artifact publication conflict')
            finally:
                tmp.unlink(missing_ok=True)
        return StoredArtifact(path=rel, sha256=digest, bytes=len(data))

    # --- 일괄 검증 (guard verify) ---------------------------------------------

    @staticmethod
    def _check_change_artifact_refs(obj) -> None:
        # A missing optional field identifies legacy, unverified source links.
        if str(obj.kind) != "CHG" or getattr(obj, "artifacts", None) is None:
            return
        addresses = {(art.path, art.sha256) for art in obj.artifacts}
        for fact in obj.facts:
            for evidence in fact.evidence:
                if (evidence.path, evidence.sha256) not in addresses:
                    raise IntegrityError("CHG fact evidence is not preserved by its artifacts")

    @staticmethod
    def _check_schema(con: sqlite3.Connection) -> None:
        actual_schema = {_schema_statement(sql) for (sql,) in con.execute(
            "SELECT sql FROM sqlite_schema WHERE name IN ('objects', 'objects_no_update', 'objects_no_delete')"
        ) if sql is not None}
        if actual_schema != _EXPECTED_SCHEMA:
            raise IntegrityError("Guard object store schema or append-only triggers are missing or changed")
        if con.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise IntegrityError("SQLite quick_check failed")

    def validate_schema(self) -> None:
        """Verify database structure before choosing a subset of stored objects."""
        with closing(self._connect()) as con:
            self._check_schema(con)

    def verify(self, *, require_packs: bool = False) -> list[str]:
        """Verify existing schema, sealed rows and their artifact/reference graph."""
        problems: list[str] = []
        try:
            with closing(self._connect()) as con:
                con.execute("BEGIN")
                self._check_schema(con)
                rows = con.execute(f"SELECT {_OBJECT_COLUMNS} FROM objects ORDER BY id, revision").fetchall()
        except IntegrityError as exc:
            return [str(exc)]
        except sqlite3.Error:
            return ["Guard object store database is unreadable"]

        objects = {}
        revisions: dict[str, int] = {}
        kinds: dict[str, str] = {}
        for row in rows:
            ident, rev = row[:2]
            if rev != revisions.get(ident, 0) + 1:
                problems.append(f"{ident} rev {rev}: revision history has a gap")
            revisions[ident] = rev
            try:
                obj = _read_object(row)
                if not obj.hash_ok():
                    raise IntegrityError("content_sha256 mismatch")
            except (IntegrityError, KeyError, TypeError, ValueError) as exc:
                problems.append(f"{ident} rev {rev}: {exc if isinstance(exc, IntegrityError) else 'unreadable object'}")
            else:
                if kinds.setdefault(ident, str(obj.kind)) != str(obj.kind):
                    problems.append(f"{ident} rev {rev}: revision changes its object kind")
                objects[(str(obj.kind), obj.id, obj.revision, obj.content_sha256)] = obj

        def resolve(ref):
            obj = objects.get(ref_key(ref))
            if obj is None:
                raise IntegrityError(f"broken ref {ref.id} rev {ref.revision}")
            return obj

        slots, ids, packs = {}, {}, {}
        verified_artifacts = set()
        for obj in objects.values():
            try:
                for ref in object_references(obj):
                    if ref.kind == "PACK":
                        packs[ref_key(ref)] = ref
                    else:
                        resolve(ref)
                validate_relationships(obj, resolve)
                self._check_change_artifact_refs(obj)
                for artifact in attachments(obj):
                    key = artifact.path, artifact.sha256, artifact.bytes
                    if key not in verified_artifacts:
                        verify_attachment(self.root, artifact)
                        verified_artifacts.add(key)
                slot = self._slot(obj, resolve)
                if slot is not None and (slots.setdefault(slot, obj.id) != obj.id or ids.setdefault(obj.id, slot) != slot):
                    raise IntegrityError("conflicting object IDs or revisions for one logical slot")
            except (IntegrityError, OSError) as exc:
                problems.append(f"{obj.id} rev {obj.revision}: {exc if isinstance(exc, IntegrityError) else 'unreadable artifact'}")

        artifact_dir = self.root / "artifacts"
        already_verified = {path for path, _, _ in verified_artifacts}
        try:
            if artifact_dir.is_symlink() or not artifact_dir.is_dir():
                raise IntegrityError("artifact directory is missing or is not a regular directory")
            for path in sorted(artifact_dir.iterdir()):
                if path.is_symlink() or not path.is_file():
                    problems.append("artifact directory contains a nonregular entry")
                elif f"artifacts/{path.name}" not in already_verified and file_sha256(path) != path.name:
                    problems.append(f"artifact {path.name}: content does not match name")
        except (IntegrityError, OSError) as exc:
            problems.append(str(exc) if isinstance(exc, IntegrityError) else "artifact directory is unreadable")
        if require_packs and not problems:
            from .packs import get_pack

            for ref in packs.values():
                try:
                    get_pack(self, ref)
                except (LookupError, OSError, ValueError):
                    problems.append(f"pack {ref.id} rev {ref.revision} missing or changed")
        return problems
