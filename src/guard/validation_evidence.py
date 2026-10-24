"""Read-only checks for saved validation stores and portable evidence bundles.

These helpers do not construct Store, connect to an engine, or repair evidence.
Checksums detect changed bytes; they do not authenticate the collector or host.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
from types import SimpleNamespace
import zipfile

from guard.models import Pack, Ref, canonical_json, sha256_hex
from guard.store.packs import get_pack

_DIGEST = re.compile(r"[0-9a-f]{64}")
_ARTIFACT = re.compile(r"artifacts/[0-9a-f]{64}")


def _unlinked(path: Path) -> None:
    """Reject symlinks and Windows junction/reparse paths before reading."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("saved evidence must not contain symlinks or reparse points")


def _directory(path: Path) -> None:
    _unlinked(path)
    if not path.is_dir():
        raise ValueError("saved evidence must be a regular directory")


def file_sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_objects(store: Path) -> list[dict]:
    """Read the closed database without creating WAL, journals or lock files.

    A nonempty WAL/journal is rejected rather than silently ignoring pending
    data with immutable mode. The caller must close/checkpoint its own store.
    """
    store = Path(store)
    _directory(store)
    db = store / "store.sqlite"
    if not db.is_file():
        raise ValueError("product store was not created")
    _unlinked(db)
    for pending in (db.with_name(db.name + "-wal"), db.with_name(db.name + "-journal")):
        if pending.exists() or pending.is_symlink():
            _unlinked(pending)
            if not pending.is_file() or pending.stat().st_size:
                raise ValueError("close/checkpoint the source store before read-only analysis")
    # Context managers on sqlite connections do not close the connection.
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as con:
        rows = con.execute("SELECT kind,id,revision,content_sha256,body FROM objects ORDER BY kind,id,revision")
        objects = []
        for kind, ident, revision, digest, body in rows:
            obj = json.loads(body)
            if (not isinstance(obj, dict)
                    or (obj.get("kind"), obj.get("id"), obj.get("revision"), obj.get("content_sha256"))
                    != (kind, ident, revision, digest)):
                raise ValueError("database index and stored object identity differ")
            objects.append(obj)
        return objects


def verify_store_files(store: Path, objects: list[dict]) -> Pack:
    """Check canonical artifact bytes and return the PLAN's original stored pack."""
    store = Path(store)
    _directory(store)
    if not isinstance(objects, list) or any(not isinstance(obj, dict) for obj in objects):
        raise ValueError("saved objects must be a list of object mappings")
    artifacts = store / "artifacts"
    _directory(artifacts)
    # Check the directory before get_pack, which reads content-addressed files.
    for path in artifacts.iterdir():
        _unlinked(path)
        if not path.is_file() or _DIGEST.fullmatch(path.name) is None or file_sha256(path) != path.name:
            raise ValueError("stored content-address file missing or changed")
    plans = [obj for obj in objects if obj.get("kind") == "PLAN"]
    if len(plans) != 1:
        raise ValueError("one original PLAN is required")
    inputs = plans[0].get("inputs")
    ref = Ref.model_validate(inputs.get("pack") if isinstance(inputs, dict) else None)
    if ref.kind != "PACK" or _DIGEST.fullmatch(ref.sha256) is None:
        raise ValueError("noncanonical stored pack reference")
    manifest_path = artifacts / ref.sha256
    if not manifest_path.is_file():
        raise ValueError("original stored pack manifest missing")
    manifest = json.loads(manifest_path.read_bytes())
    files = manifest.get("files_sha256") if isinstance(manifest, dict) else None
    if not isinstance(files, list) or any(
        not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str)
        or _DIGEST.fullmatch(entry["sha256"]) is None for entry in files
    ):
        raise ValueError("noncanonical stored pack file reference")
    pack = get_pack(SimpleNamespace(root=store), ref)
    for obj in objects:
        entries = obj.get("artifacts", [])
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError("saved artifacts must be a list of artifact mappings")
        for artifact in entries:
            name, digest, size = artifact.get("path"), artifact.get("sha256"), artifact.get("bytes")
            if (not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None
                    or name != f"artifacts/{digest}" or type(size) is not int or size < 0):
                raise ValueError("noncanonical original artifact path or byte count")
            path = store / name
            _unlinked(path)
            if not path.is_file() or path.stat().st_size != size or file_sha256(path) != digest:
                raise ValueError("original evidence artifact missing or changed")
        _verify_fixed_route_fixture(store, obj)
    return pack


def _verify_fixed_route_fixture(store: Path, obj: dict) -> None:
    """Tie the new opt-in collector's resolved inputs to its sealed EVD hash.

    Legacy evidence keeps its original fixture format and interpretation.
    This is an input-integrity check, never a verdict or collector attestation.
    """
    executor = obj.get("execution", {}).get("executor") or {}
    versions = {"direct_route": "0.7.0", "role_route": "0.5.0"}
    if (obj.get("kind") != "EVD" or executor.get("name") not in versions
            or versions[executor["name"]] != executor.get("version")):
        return
    if obj.get("execution", {}).get("status") != "COMPLETED":
        return
    logs = [a for a in obj.get("artifacts", []) if a.get("name") == "probe_log.jsonl"]
    if len(logs) != 1:
        raise ValueError("fixed route fixture requires one original probe log")
    try:
        lines = (store / logs[0]["path"]).read_text(encoding="utf-8").splitlines()
        summary = json.loads(lines[-1])
        fixture = summary["fixture"]
        if (summary.get("summary") is not True or fixture.get("protocol") != "fixed-route-fixture/1"
                or sha256_hex(canonical_json(fixture)) != obj["comparability"]["fixture_sha256"]):
            raise ValueError("fixed route fixture and original EVD input hash differ")
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ValueError("fixed route fixture attachment is incomplete") from exc


def inventory_tree(folder: Path) -> dict[str, dict]:
    """Inventory bytes without opening a writable database or following links."""
    folder = Path(folder)
    _directory(folder)
    result = {}
    for path in sorted(folder.rglob("*")):
        _unlinked(path)
        if path.is_file():
            result[path.relative_to(folder).as_posix()] = {
                "sha256": file_sha256(path), "bytes": path.stat().st_size,
            }
        elif not path.is_dir():
            raise ValueError("saved evidence contains a nonregular entry")
    return result


def verify_saved_bundle(bundle: Path, restored: Path) -> None:
    """Compare a bundle's manifest and original bytes with an existing restore.

    This does not extract files or create a destination. Pass the freshly
    restored store before adding any new judgments to that copy.
    """
    bundle, restored = Path(bundle), Path(restored)
    _unlinked(bundle)
    restored_files = inventory_tree(restored)
    with zipfile.ZipFile(bundle) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if (len(names) != len(set(names)) or len(names) > 100000 or "manifest.json" not in names
                or sum(info.file_size for info in infos) > 2 * 1024**3
                or archive.getinfo("manifest.json").file_size > 16 * 1024**2):
            raise ValueError("invalid original evidence bundle inventory/budget")
        for info in infos:
            file_type = stat.S_IFMT(info.external_attr >> 16)
            if file_type not in (0, stat.S_IFREG) or info.is_dir():
                raise ValueError("original evidence bundle contains a nonregular entry")
        manifest = json.loads(archive.read("manifest.json"))
        files = manifest.get("files") if isinstance(manifest, dict) else None
        if (not isinstance(files, dict) or manifest.get("format") != "guard-store-bundle/1"
                or "store.sqlite" not in files or set(files) != set(names) - {"manifest.json"}
                or set(restored_files) != set(files)):
            raise ValueError("original evidence bundle manifest mismatch")
        for name, meta in files.items():
            if name != "store.sqlite" and _ARTIFACT.fullmatch(name) is None:
                raise ValueError("unexpected original evidence bundle path")
            if (not isinstance(meta, dict) or type(meta.get("bytes")) is not int or meta["bytes"] < 0
                    or not isinstance(meta.get("sha256"), str) or _DIGEST.fullmatch(meta["sha256"]) is None):
                raise ValueError("invalid original evidence bundle file metadata")
            info = archive.getinfo(name)
            with archive.open(info) as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if (info.file_size != meta["bytes"] or digest != meta["sha256"]
                    or restored_files[name] != {"sha256": digest, "bytes": meta["bytes"]}):
                raise ValueError("original bundle/restored bytes differ")
