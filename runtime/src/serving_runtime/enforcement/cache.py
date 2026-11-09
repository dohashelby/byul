"""SP1 local cache generation check (CACHE-01/02) without following links."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import stat
from typing import Any

from .intent import EnforcementIntent


SCHEMA = "runtime-cache-check/1.0"
_DEPTH = 4  # engine / image_id / model_revision / compile_profile


def _entry(path: Path, cache_root: Path, current: Path, engine_uid: int, *, info: os.stat_result | None = None) -> dict[str, Any]:
    info = os.lstat(path) if info is None else info
    problems: list[str] = []
    if stat.S_ISLNK(info.st_mode):
        problems.append("symlink")
    if info.st_uid != engine_uid:
        problems.append("owner_not_engine")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        problems.append("group_or_other_writable")
    if path == current:
        if not info.st_mode & stat.S_IWUSR:
            problems.append("engine_owner_write_missing")
        if not info.st_mode & stat.S_IXUSR:
            problems.append("engine_owner_execute_missing")
    relative = path.relative_to(cache_root)
    return {
        "path": str(path),
        "generation": list(relative.parts),
        "is_current": path == current,
        "owner_uid": info.st_uid,
        "mode": oct(stat.S_IMODE(info.st_mode)),
        "problems": problems,
    }


def _ancestor(path: Path, engine_uid: int, *, info: os.stat_result | None = None) -> dict[str, Any] | None:
    """Problems of the cache root or an intermediate generation directory.

    Whoever can write here can create, rename or replace the generation below,
    so these directories need the same protection as the generation leaf.
    """

    info = os.lstat(path) if info is None else info
    problems: list[str] = []
    if info.st_uid not in (0, engine_uid):
        problems.append("owner_not_engine_or_root")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        problems.append("group_or_other_writable")
    if info.st_uid == engine_uid:
        if not info.st_mode & stat.S_IXUSR:
            problems.append("engine_traverse_denied")
    elif not info.st_mode & stat.S_IXOTH:
        if info.st_mode & stat.S_IXGRP:
            problems.append("engine_traverse_unverified")
        else:
            problems.append("engine_traverse_denied")
    if not problems:
        return None
    return {
        "path": str(path),
        "owner_uid": info.st_uid,
        "mode": oct(stat.S_IMODE(info.st_mode)),
        "problems": problems,
    }


def check_cache(intent: EnforcementIntent, *, root_override: str | None = None, max_entries: int | None = None) -> dict[str, Any]:
    """Report generation directories, ownership and permissions under the cache root.

    ``root_override`` lets a test or an operator point at a copy of the tree; the
    expected generation is resolved under it the same way.
    """

    if max_entries is not None and (type(max_entries) is not int or max_entries <= 0):
        raise ValueError("cache metadata entry bound must be positive")
    visited = 0
    assets = intent.assets
    if assets is None:
        raise ValueError("cache check needs a runtime-enforcement-intent/1.1 assets section")
    cache_root = Path(root_override or assets.cache_root)
    current = Path(assets.cache_generation.path(str(cache_root)))
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "cache_root": str(cache_root),
        "current_generation": str(current),
        "engine_uid": assets.engine_uid,
        "state": "ok",
        "generations": [],
        "other_generation_count": 0,
        "unexpected_entries": [],
        "ancestor_problems": [],
        "walk_errors": [],
        "limits": [
            "Directory metadata only; cache file contents and provenance are not verified.",
            "Other generations are reported, not deleted.",
            "Directories above the cache root are not checked.",
        ],
    }
    try:
        root_info = os.lstat(cache_root)
    except FileNotFoundError:
        result["state"] = "cache_root_missing"
        return result
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        result["state"] = "cache_root_not_directory"
        return result

    def check_ancestor(path: Path, info: os.stat_result) -> None:
        try:
            problem = _ancestor(path, assets.engine_uid, info=info)
        except OSError as error:
            result["walk_errors"].append({"path": str(path), "error": type(error).__name__})
            return
        if problem is not None:
            result["ancestor_problems"].append(problem)

    def walk(directory: Path, descriptor: int, depth: int) -> None:
        nonlocal visited
        try:
            with os.scandir(descriptor) as entries:
                children = []
                for child in entries:
                    if max_entries is not None and visited >= max_entries:
                        result["walk_errors"].append({"path": str(directory), "error": "EntryBoundExceeded"})
                        return
                    visited += 1
                    children.append(child)
            children.sort(key=lambda item: item.name)
        except OSError as error:
            result["walk_errors"].append({"path": str(directory), "error": type(error).__name__})
            return
        for child in children:
            path = directory / child.name
            child_descriptor = -1
            try:
                listed = child.stat(follow_symlinks=False)
                if not stat.S_ISDIR(listed.st_mode):
                    result["unexpected_entries"].append(
                        {"path": str(path), "kind": "symlink" if stat.S_ISLNK(listed.st_mode) else "file"})
                    continue
                child_descriptor = os.open(child.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                opened = os.fstat(child_descriptor)
                if (listed.st_dev, listed.st_ino) != (opened.st_dev, opened.st_ino):
                    raise OSError("cache directory changed during scan")
                if depth == _DEPTH:
                    result["generations"].append(_entry(path, cache_root, current, assets.engine_uid, info=opened))
                else:
                    check_ancestor(path, opened)
                    walk(path, child_descriptor, depth + 1)
            except OSError as error:
                result["walk_errors"].append({"path": str(path), "error": type(error).__name__})
            finally:
                if child_descriptor >= 0:
                    os.close(child_descriptor)

    root_descriptor = os.open(cache_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened_root = os.fstat(root_descriptor)
        if (root_info.st_dev, root_info.st_ino) != (opened_root.st_dev, opened_root.st_ino):
            raise OSError("cache root changed during scan")
        check_ancestor(cache_root, opened_root)
        walk(cache_root, root_descriptor, 1)
    finally:
        os.close(root_descriptor)
    generations = result["generations"]
    result["other_generation_count"] = sum(not item["is_current"] for item in generations)
    current_entries = [item for item in generations if item["is_current"]]
    if result["walk_errors"]:
        result["state"] = "incomplete"
    elif not current_entries:
        result["state"] = "current_generation_missing"
    elif (
        any(item["problems"] for item in generations)
        or result["ancestor_problems"]
        or result["walk_errors"]
    ):
        result["state"] = "problems"
    elif result["other_generation_count"] or result["unexpected_entries"]:
        result["state"] = "other_generations_present"
    return result
