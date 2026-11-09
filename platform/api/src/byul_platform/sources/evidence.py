"""evidence-index 파일 읽기. 증거 파일 내용은 읽지 않고 목록만 다룬다."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class IndexFile:
    path: str
    document: Any  # 검증 전 evidence-index 후보


class EvidenceSource:
    MAX_INDEX_BYTES = 32 * 1024 * 1024

    def __init__(self, paths: list[Path], mode: str) -> None:
        self.paths = list(dict.fromkeys(paths))
        self.mode = mode
        self.detail = ", ".join(str(p) for p in paths) or None

    def indexes(self) -> Iterator[IndexFile]:
        # Keep only the current parsed index in memory, and bound each read.
        for path in self.paths:
            try:
                with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise OSError("INDEX_NOT_REGULAR")
                    raw = stream.read(self.MAX_INDEX_BYTES + 1)
                if len(raw) > self.MAX_INDEX_BYTES:
                    yield IndexFile(str(path), {"_error": "INDEX_SIZE_LIMIT"})
                else:
                    yield IndexFile(str(path), json.loads(raw))
            except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
                yield IndexFile(str(path), {"_error": type(error).__name__})

    def path_error(self, index_path: str, relative: str) -> str | None:
        """Paths are relative to index.parent/files, never index.parent itself.

        This checks containment/type only; listing never hashes/reads payloads.
        Contract examples intentionally have no accompanying payload files.
        """
        parts = relative.split("/")
        if not relative or relative.startswith("/") or any(p in ("", ".", "..") for p in parts) or "\\" in relative:
            return "EVIDENCE_UNSAFE_PATH"
        root = Path(index_path).absolute().parent / "files"
        target = root.joinpath(*parts)
        try:
            if not target.resolve().is_relative_to(root.resolve()):
                return "EVIDENCE_PATH_ESCAPE"
            # Reject links even if their target remains inside files/.
            for candidate in (root, *(root.joinpath(*parts[:i]) for i in range(1, len(parts) + 1))):
                if candidate.is_symlink():
                    return "EVIDENCE_SYMLINK"
            if self.mode != "example" and not stat.S_ISREG(target.stat().st_mode):
                return "EVIDENCE_NOT_REGULAR"
        except (OSError, RuntimeError):
            return "EVIDENCE_UNAVAILABLE"
        return None
