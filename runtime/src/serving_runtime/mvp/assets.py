"""Approved model/tokenizer asset manifest and on-disk integrity comparison.

This is a hash comparison of files on disk. It does not block writes and does
not prove which bytes the engine actually loaded into memory.
"""

from __future__ import annotations

from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import time
from typing import Any

from ..file_info import FileInfo, inspect_open_file
from .policy import DuplicateKeyError, _object_without_duplicates, _reject_json_constant

SCHEMA_VERSION = "runtime-asset-manifest/1.0"
_SHA256_CHARS = frozenset("0123456789abcdef")
_MAX_WALK_ERRORS = 50


class _SymlinkFound(ValueError):
    """A manifest entry or one of its root-relative parents is a symlink."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("file path must be a non-empty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(f"file path must stay inside root: {value}")
    return str(path)


def _root_lstat(root: Path, *, allow_missing: bool = False) -> os.stat_result | None:
    try:
        info = root.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"root must not be a symbolic link: {root}")
    return info


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Load and strictly validate an approved asset manifest."""

    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        raise ValueError(f"asset manifest could not be read: {error}") from error
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, DuplicateKeyError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise ValueError(f"asset manifest is not valid strict JSON: {error}") from error
    if not isinstance(document, dict) or set(document) != {"schema_version", "root", "files"}:
        raise ValueError("asset manifest must contain only schema_version, root, files")
    if document["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported asset manifest schema_version")
    root = document["root"]
    if not isinstance(root, str) or "\x00" in root or not Path(root).is_absolute():
        raise ValueError("root must be an absolute path")
    try:
        _root_lstat(Path(root), allow_missing=True)
    except OSError as error:
        raise ValueError(f"root could not be inspected: {type(error).__name__}") from error
    files = document["files"]
    if not isinstance(files, list) or not files:
        raise ValueError("files must be a non-empty list")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
            raise ValueError(f"files[{index}] must contain only path, sha256, size")
        relative = _safe_relative(item["path"])
        if relative in seen:
            raise ValueError(f"duplicate file path: {relative}")
        seen.add(relative)
        digest = item["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or not set(digest) <= _SHA256_CHARS:
            raise ValueError(f"files[{index}].sha256 must be a lowercase SHA-256 hex digest")
        size = item["size"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError(f"files[{index}].size must be a non-negative integer")
        normalized.append({"path": relative, "sha256": digest, "size": size})
    return {
        "schema_version": SCHEMA_VERSION,
        "root": root,
        "files": normalized,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _walk(
    root: Path,
) -> tuple[list[str], list[str], list[dict[str, str]], int]:
    """Return files, links, and bounded traversal errors without following links."""

    regular: list[str] = []
    links: list[str] = []
    walk_errors: list[dict[str, str]] = []
    walk_error_count = 0

    def record_error(error: OSError, relative: str | None = None) -> None:
        nonlocal walk_error_count
        walk_error_count += 1
        if len(walk_errors) >= _MAX_WALK_ERRORS:
            return
        if relative is None:
            filename = error.filename
            if isinstance(filename, (str, bytes)):
                try:
                    relative_path = Path(filename).relative_to(root).as_posix()
                except (TypeError, ValueError):
                    relative_path = "."
            else:
                relative_path = "."
        else:
            relative_path = relative
        walk_errors.append(
            {"path": relative_path or ".", "error": type(error).__name__}
        )

    _root_lstat(root)
    for directory, dirnames, filenames in os.walk(
        root, followlinks=False, onerror=record_error
    ):
        base = Path(directory)
        dirnames.sort()
        filenames.sort()
        for name in list(dirnames):
            path = base / name
            relative = path.relative_to(root).as_posix()
            try:
                mode = path.lstat().st_mode
            except OSError as error:
                record_error(error, relative)
                dirnames.remove(name)
                continue
            if stat.S_ISLNK(mode):
                links.append(relative)
                dirnames.remove(name)
        for name in filenames:
            path = base / name
            relative = path.relative_to(root).as_posix()
            try:
                mode = path.lstat().st_mode
            except OSError as error:
                record_error(error, relative)
                continue
            if stat.S_ISLNK(mode):
                links.append(relative)
            elif stat.S_ISREG(mode):
                regular.append(relative)
    return sorted(regular), sorted(links), walk_errors, walk_error_count


def _open_root(root: Path) -> int:
    info = _root_lstat(root)
    if info is None or not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(str(root))
    return os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _inspect_relative(root_descriptor: int, relative: str) -> FileInfo:
    """Hash a file via root-relative directory descriptors without following links."""

    relative = _safe_relative(relative)
    parts = relative.split("/")
    directory_descriptor = os.dup(root_descriptor)
    try:
        for part in parts[:-1]:
            info = os.stat(part, dir_fd=directory_descriptor, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise _SymlinkFound(relative)
            if not stat.S_ISDIR(info.st_mode):
                raise NotADirectoryError(relative)
            try:
                next_descriptor = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_descriptor,
                )
            except OSError as error:
                if error.errno == errno.ELOOP:
                    raise _SymlinkFound(relative) from error
                raise
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor

        info = os.stat(parts[-1], dir_fd=directory_descriptor, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise _SymlinkFound(relative)
        try:
            descriptor = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise _SymlinkFound(relative) from error
            raise
        try:
            return inspect_open_file(descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_descriptor)


def snapshot(root: str | Path) -> dict[str, Any]:
    """Observe current files under root. The result is an observation, not an approval."""

    root_path = Path(os.path.abspath(os.fspath(root)))
    try:
        root_info = _root_lstat(root_path)
    except OSError as error:
        raise ValueError(f"root could not be inspected: {type(error).__name__}") from error
    if root_info is None or not stat.S_ISDIR(root_info.st_mode):
        raise ValueError(f"root is not a directory: {root_path}")
    regular, _, walk_errors, walk_error_count = _walk(root_path)
    if walk_error_count:
        first = walk_errors[0]
        raise ValueError(
            "root traversal was incomplete: "
            f"{walk_error_count} error(s), first={first['path']}:{first['error']}"
        )
    files = []
    root_descriptor = _open_root(root_path)
    try:
        for relative in regular:
            info = _inspect_relative(root_descriptor, relative)
            files.append(
                {"path": relative, "sha256": info.sha256, "size": info.size_bytes}
            )
    finally:
        os.close(root_descriptor)
    if not files:
        raise ValueError("no regular files found under root")
    return {"schema_version": SCHEMA_VERSION, "root": str(root_path), "files": files}


def check(manifest: dict[str, Any]) -> dict[str, Any]:
    """Compare approved entries with files on disk."""

    started = time.monotonic()
    root = Path(manifest["root"])
    root_info = _root_lstat(root, allow_missing=True)
    items: list[dict[str, Any]] = []
    counts = {"match": 0, "changed": 0, "missing": 0, "unreadable": 0, "symlink": 0}
    root_descriptor: int | None = None
    if root_info is not None and stat.S_ISDIR(root_info.st_mode):
        try:
            root_descriptor = _open_root(root)
        except OSError:
            root_descriptor = None
    for entry in manifest["files"]:
        item: dict[str, Any] = {"path": entry["path"], "expected_sha256": entry["sha256"]}
        if root_info is None:
            item["state"] = "missing"
        elif root_descriptor is None:
            item["state"] = "unreadable"
            item["error"] = "NotADirectoryError" if not stat.S_ISDIR(root_info.st_mode) else "OSError"
        else:
            try:
                info = _inspect_relative(root_descriptor, entry["path"])
            except _SymlinkFound:
                item["state"] = "symlink"
            except FileNotFoundError:
                item["state"] = "missing"
            except (OSError, ValueError) as error:
                item["state"] = "unreadable"
                item["error"] = type(error).__name__
            else:
                item["actual_sha256"] = info.sha256
                item["actual_size"] = info.size_bytes
                same = info.sha256 == entry["sha256"] and info.size_bytes == entry["size"]
                item["state"] = "match" if same else "changed"
        counts[item["state"]] += 1
        items.append(item)
    if root_descriptor is not None:
        os.close(root_descriptor)
    expected = {entry["path"] for entry in manifest["files"]}
    if root_info is not None and stat.S_ISDIR(root_info.st_mode):
        regular, links, walk_errors, walk_error_count = _walk(root)
        extra = [path for path in regular if path not in expected]
        extra_links = [path for path in links if path not in expected]
        extra_file_count: int | None = len(extra)
        extra_symlink_count: int | None = len(extra_links)
    else:
        extra, extra_links, walk_errors = [], [], []
        extra_file_count = extra_symlink_count = walk_error_count = None
    return {
        "checked_at": _utc_now(),
        "duration_s": time.monotonic() - started,
        "root": str(root),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "total": len(items),
        "counts": counts,
        "extra_files": extra[:50],
        "extra_file_count": extra_file_count,
        "extra_symlinks": extra_links[:50],
        "extra_symlink_count": extra_symlink_count,
        "walk_errors": walk_errors,
        "walk_error_count": walk_error_count,
        "intact": (
            counts["match"] == len(items)
            and not extra
            and not extra_links
            and not walk_errors
        ),
        "items": items,
        "limit": "디스크 파일 해시 비교이며 쓰기 차단이나 엔진 메모리 로딩 증명이 아님",
    }
