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


def _entry(path: Path, cache_root: Path, current: Path, engine_uid: int) -> dict[str, Any]:
    info = os.lstat(path)
    problems: list[str] = []
    if stat.S_ISLNK(info.st_mode):
        problems.append("symlink")
    if info.st_uid != engine_uid:
        problems.append("owner_not_engine")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        problems.append("group_or_other_writable")
    relative = path.relative_to(cache_root)
    return {
        "path": str(path),
        "generation": list(relative.parts),
        "is_current": path == current,
        "owner_uid": info.st_uid,
        "mode": oct(stat.S_IMODE(info.st_mode)),
        "problems": problems,
    }


def check_cache(intent: EnforcementIntent, *, root_override: str | None = None) -> dict[str, Any]:
    """Report generation directories, ownership and permissions under the cache root.

    ``root_override`` lets a test or an operator point at a copy of the tree; the
    expected generation is resolved under it the same way.
    """

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
        "walk_errors": [],
        "limits": [
            "Directory metadata only; cache file contents and provenance are not verified.",
            "Other generations are reported, not deleted.",
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

    def walk(directory: Path, depth: int) -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as error:
            result["walk_errors"].append({"path": str(directory), "error": type(error).__name__})
            return
        for child in children:
            path = Path(child.path)
            if child.is_symlink() or not child.is_dir(follow_symlinks=False):
                if depth <= _DEPTH:
                    result["unexpected_entries"].append(
                        {"path": str(path), "kind": "symlink" if child.is_symlink() else "file"}
                    )
                continue
            if depth == _DEPTH:
                result["generations"].append(_entry(path, cache_root, current, assets.engine_uid))
            else:
                walk(path, depth + 1)

    walk(cache_root, 1)
    generations = result["generations"]
    result["other_generation_count"] = sum(not item["is_current"] for item in generations)
    current_entries = [item for item in generations if item["is_current"]]
    if not current_entries:
        result["state"] = "current_generation_missing"
    elif any(item["problems"] for item in generations) or result["walk_errors"]:
        result["state"] = "problems"
    elif result["other_generation_count"] or result["unexpected_entries"]:
        result["state"] = "other_generations_present"
    return result
