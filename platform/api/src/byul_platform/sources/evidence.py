"""evidence-index 파일 읽기. 증거 파일 내용은 읽지 않고 목록만 다룬다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class IndexFile:
    path: str
    document: Any  # 검증 전 evidence-index 후보


class EvidenceSource:
    def __init__(self, paths: list[Path], mode: str) -> None:
        self.paths = paths
        self.mode = mode
        self.detail = ", ".join(str(p) for p in paths) or None

    def indexes(self) -> list[IndexFile]:
        out: list[IndexFile] = []
        for path in self.paths:
            try:
                out.append(IndexFile(str(path), json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                out.append(IndexFile(str(path), {"_unreadable": str(path), "_error": type(error).__name__}))
        return out
