"""Verified, portable store bundles. No work directories or input configuration files.

Checksums detect corruption; they do not authenticate the author of a bundle.
The source database is read through SQLite's backup API, never modified.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import zipfile

from guard.models import canonical_json
from .packs import get_pack
from .sqlite import Store, IntegrityError

_ARTIFACT = re.compile(r"artifacts/[0-9a-f]{64}")
DEFAULT_MAX_BYTES = 2 * 1024**3


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _allowed(name: str) -> bool:
    return name == "store.sqlite" or _ARTIFACT.fullmatch(name) is not None


def verify_complete(store: Store) -> None:
    """Also verify all referenced pack snapshots and canonical artifact paths."""
    for evidence in store.iter_kind("EVD"):
        for artifact in evidence.artifacts:
            if artifact.path != f"artifacts/{artifact.sha256}" or not _ARTIFACT.fullmatch(artifact.path):
                raise IntegrityError("non-canonical evidence artifact path")
            path = store.root / artifact.path
            if path.is_file() and path.stat().st_size != artifact.bytes:
                raise IntegrityError("artifact byte count differs from evidence")
    problems = store.verify()
    if problems:
        raise IntegrityError("store verification failed: " + "; ".join(problems))
    refs = {}
    for kind in ("ENV", "POL", "CHG", "PLAN", "EVD", "RES", "RPT"):
        for obj in store.iter_kind(kind):
            for ref in obj.refs:
                if ref.kind == "PACK":
                    refs[(ref.id, ref.revision, ref.sha256)] = ref
    for ref in refs.values():
        get_pack(store, ref)


def export_bundle(source: Path, output: Path) -> dict:
    source, output = Path(source).resolve(), Path(output).resolve()
    if not (source / "store.sqlite").is_file():
        raise ValueError("source store.sqlite does not exist")
    if output.exists() or output.is_relative_to(source):
        raise ValueError("output must be a new file outside the source store")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="guard-export-") as tmp:
        root = Path(tmp) / "store"
        root.mkdir()
        with closing(sqlite3.connect((source / "store.sqlite").as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(root / "store.sqlite")) as dst:
                src.backup(dst)
        (root / "artifacts").mkdir()
        artifacts = source / "artifacts"
        if artifacts.is_symlink():
            raise IntegrityError("artifact directory must not be a symlink")
        for path in sorted(artifacts.iterdir()):
            name = "artifacts/" + path.name
            if path.is_symlink() or not path.is_file() or not _allowed(name):
                raise IntegrityError("unexpected entry in artifact directory")
            shutil.copyfile(path, root / name)
        verify_complete(Store(root))
        files = [root / "store.sqlite", *sorted((root / "artifacts").iterdir())]
        manifest = {"format": "guard-store-bundle/1", "files": {
            path.relative_to(root).as_posix(): {"sha256": _digest(path), "bytes": path.stat().st_size}
            for path in files}}
        archive_path = Path(tmp) / "bundle.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", canonical_json(manifest))
            for path in files:
                archive.write(path, path.relative_to(root).as_posix())
        # Exclusive creation preserves any existing output, even if it appeared mid-export.
        with output.open("xb") as target, archive_path.open("rb") as stream:
            shutil.copyfileobj(stream, target)
    return manifest


def restore_bundle(bundle: Path, destination: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    destination = Path(destination).resolve()
    if destination.exists() or max_bytes < 1:
        raise ValueError("destination must not exist and max_bytes must be positive")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="guard-restore-", dir=destination.parent) as tmp:
        root = Path(tmp) / "store"
        root.mkdir()
        with zipfile.ZipFile(bundle) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or len(names) > 100000:
                raise IntegrityError("duplicate or excessive archive entries")
            if "manifest.json" not in names or any(n != "manifest.json" and not _allowed(n) for n in names):
                raise IntegrityError("unexpected archive path")
            if archive.getinfo("manifest.json").file_size > 16 * 1024**2:
                raise IntegrityError("manifest is too large")
            if sum(i.file_size for i in infos) > max_bytes:
                raise IntegrityError("bundle exceeds uncompressed byte budget")
            manifest = json.loads(archive.read("manifest.json"))
            files = manifest.get("files", {})
            if (manifest.get("format") != "guard-store-bundle/1" or not isinstance(files, dict)
                    or set(files) != set(names) - {"manifest.json"} or "store.sqlite" not in files):
                raise IntegrityError("invalid manifest inventory")
            for name, expected in files.items():
                info = archive.getinfo(name)
                if not isinstance(expected, dict) or info.file_size != expected.get("bytes"):
                    raise IntegrityError("archive size differs from manifest")
                # Never extractall: only explicitly permitted regular files are written.
                path = root / name
                path.parent.mkdir(exist_ok=True)
                with archive.open(info) as src, path.open("xb") as target:
                    shutil.copyfileobj(src, target)
                if _digest(path) != expected.get("sha256"):
                    raise IntegrityError("archive checksum mismatch")
        verify_complete(Store(root))
        root.rename(destination)
    return manifest
