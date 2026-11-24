"""Verified, portable store bundles. No work directories or input configuration files.

Checksums detect corruption; they do not authenticate the author of a bundle.
The source database is read through SQLite's backup API, never modified.
"""
from __future__ import annotations

from contextlib import closing
import ctypes
import errno
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import zipfile

from guard.models import GuardObject, Ref, canonical_json
from .integrity import attachments, file_sha256, object_references, ref_key
from .packs import get_pack
from .sqlite import Store, IntegrityError

_ARTIFACT = re.compile(r"artifacts/[0-9a-f]{64}")
DEFAULT_MAX_BYTES = 2 * 1024**3


def _allowed(name: str) -> bool:
    return name == "store.sqlite" or _ARTIFACT.fullmatch(name) is not None


def verify_complete(store: Store) -> None:
    """Check the existing object store, including its saved pack snapshots."""
    problems = store.verify(require_packs=True)
    if problems:
        raise IntegrityError("store verification failed: " + "; ".join(problems))


def _publish_directory(source: Path, destination: Path) -> None:
    """Publish a verified directory in one operation without replacing a path."""
    if os.name == "nt":
        source.rename(destination)  # Windows rename fails when the destination exists.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    source_name, destination_name = os.fsencode(source), os.fsencode(destination)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        at_fdcwd, rename_noreplace = -100, 1
        rename = libc.renameat2
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        result = rename(at_fdcwd, source_name, at_fdcwd, destination_name, rename_noreplace)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        rename_excl = 4
        rename = libc.renamex_np
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        result = rename(source_name, destination_name, rename_excl)
    else:
        raise OSError(errno.ENOTSUP, "atomic directory publication is unavailable", destination)
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


def _backup_database(source: Path, destination: Path) -> None:
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)


def _report_graph(snapshot: Store, report_ref: Ref) -> tuple[list[GuardObject], list[Ref]]:
    """Return transitive dependencies before each dependent, including history."""
    ready = {}
    visiting = set()
    packs = {}
    pending: list[tuple[Ref, GuardObject | None]] = [(report_ref, None)]
    while pending:
        ref, obj = pending.pop()
        key = ref_key(ref)
        if obj is not None:
            visiting.remove(key)
            ready[key] = obj
            continue
        if ref.kind == "PACK":
            packs[key] = ref
            continue
        if key in ready:
            continue
        if key in visiting:
            raise IntegrityError("report dependency graph has a cycle")
        try:
            obj = snapshot.resolve(ref)
            previous = snapshot.get(obj.id, obj.revision - 1).ref() if obj.revision > 1 else None
        except (KeyError, ValueError) as exc:
            raise IntegrityError("report dependency or its prior revision is missing or invalid") from exc
        visiting.add(key)
        pending.append((ref, obj))
        pending.extend((dependency, None) for dependency in object_references(obj))
        if previous is not None:
            pending.append((previous, None))
    return list(ready.values()), list(packs.values())


def _copy_artifacts(source: Path, root: Path, names) -> None:
    artifacts = source / "artifacts"
    if artifacts.is_symlink():
        raise IntegrityError("artifact directory must not be a symlink")
    for name in names:
        if _ARTIFACT.fullmatch(name) is None:
            raise IntegrityError("unexpected entry in artifact directory")
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise IntegrityError("required artifact is missing or is not a regular file")
        shutil.copyfile(path, root / name)


def _stage_report(source: Path, root: Path, report_ref: Ref) -> None:
    snapshot = root.parent / "snapshot"
    snapshot.mkdir()
    _backup_database(source / "store.sqlite", snapshot / "store.sqlite")
    snapshot_store = Store.read_only(snapshot, immutable=True)
    snapshot_store.validate_schema()
    objects, packs = _report_graph(snapshot_store, report_ref)
    artifacts = {artifact.path for obj in objects for artifact in attachments(obj)}
    source_store = Store.read_only(source)
    for ref in packs:
        pack = get_pack(source_store, ref)
        artifacts.add(f"artifacts/{ref.sha256}")
        artifacts.update(f"artifacts/{entry.sha256}" for entry in pack.manifest.files_sha256)
    _copy_artifacts(source, root, sorted(artifacts))
    Store(root).put_many(objects)


def export_bundle(source: Path, output: Path, *, report_ref: Ref | None = None) -> dict:
    """Export the full store or the exact dependency closure of a stored report."""
    source, output = Path(source).resolve(), Path(output)
    output = output.parent.resolve() / output.name
    if report_ref is not None and report_ref.kind != "RPT":
        raise ValueError("report_ref must be an exact RPT reference")
    if not (source / "store.sqlite").is_file():
        raise ValueError("source store.sqlite does not exist")
    if output.exists() or output.is_symlink() or output.is_relative_to(source):
        raise ValueError("output must be a new file outside the source store")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".guard-export-", dir=output.parent) as tmp:
        root = Path(tmp) / "store"
        root.mkdir()
        (root / "artifacts").mkdir()
        if report_ref is None:
            _backup_database(source / "store.sqlite", root / "store.sqlite")
            _copy_artifacts(source, root, (f"artifacts/{path.name}" for path in sorted((source / "artifacts").iterdir())))
        else:
            _stage_report(source, root, report_ref)
        verify_complete(Store.read_only(root, immutable=True))
        files = [root / "store.sqlite", *sorted((root / "artifacts").iterdir())]
        manifest = {"format": "guard-store-bundle/1", "files": {
            path.relative_to(root).as_posix(): {"sha256": file_sha256(path), "bytes": path.stat().st_size}
            for path in files}}
        archive_path = Path(tmp) / "bundle.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", canonical_json(manifest))
            for path in files:
                archive.write(path, path.relative_to(root).as_posix())
        # The complete archive is published exclusively on the same filesystem.
        os.link(archive_path, output)
    return manifest


def restore_bundle(bundle: Path, destination: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    destination = Path(destination)
    destination = destination.parent.resolve() / destination.name
    if destination.exists() or destination.is_symlink() or max_bytes < 1:
        raise ValueError("destination must not exist and max_bytes must be positive")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="guard-restore-", dir=destination.parent) as tmp:
        root = Path(tmp) / "store"
        root.mkdir()
        (root / "artifacts").mkdir()
        with zipfile.ZipFile(bundle) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or len(names) > 100000:
                raise IntegrityError("duplicate or excessive archive entries")
            if "manifest.json" not in names or any(n != "manifest.json" and not _allowed(n) for n in names):
                raise IntegrityError("unexpected archive path")
            if any(info.is_dir() or stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG) for info in infos):
                raise IntegrityError("unexpected nonregular archive entry")
            if archive.getinfo("manifest.json").file_size > 16 * 1024**2:
                raise IntegrityError("manifest is too large")
            if sum(i.file_size for i in infos) > max_bytes:
                raise IntegrityError("bundle exceeds uncompressed byte budget")
            manifest = json.loads(archive.read("manifest.json"))
            files = manifest.get("files") if isinstance(manifest, dict) else None
            if (not isinstance(files, dict) or manifest.get("format") != "guard-store-bundle/1"
                    or set(files) != set(names) - {"manifest.json"} or "store.sqlite" not in files):
                raise IntegrityError("invalid manifest inventory")
            for name, expected in files.items():
                info = archive.getinfo(name)
                if (not isinstance(expected, dict) or type(expected.get("bytes")) is not int
                        or info.file_size != expected["bytes"]):
                    raise IntegrityError("archive size differs from manifest")
                # Never extractall: only explicitly permitted regular files are written.
                path = root / name
                path.parent.mkdir(exist_ok=True)
                with archive.open(info) as src, path.open("xb") as target:
                    shutil.copyfileobj(src, target)
                if file_sha256(path) != expected.get("sha256"):
                    raise IntegrityError("archive checksum mismatch")
        verify_complete(Store.read_only(root, immutable=True))
        _publish_directory(root, destination)
    return manifest
