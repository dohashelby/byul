"""환경변수 설정. 비밀값(조회 토큰)은 여기서만 읽고 로그·응답에 내지 않는다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# platform/api/src/byul_platform/config.py → byul/
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONTRACTS_DIR = REPO_ROOT / "contracts"
DEFAULT_WEB_DIST = REPO_ROOT / "platform" / "web" / "dist"


def _path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def _paths(name: str) -> list[Path]:
    value = os.environ.get(name, "").strip()
    return [Path(p).expanduser() for p in value.split(os.pathsep) if p.strip()] if value else []


@dataclass(frozen=True)
class Settings:
    contracts_dir: Path = DEFAULT_CONTRACTS_DIR
    web_dist: Path = DEFAULT_WEB_DIST
    examples_override: Path | None = None
    runtime_base_url: str | None = None
    runtime_viewer_token: str | None = field(default=None, repr=False)
    update_reports_dir: Path | None = None
    platform_data_dir: Path | None = None
    evidence_index_paths: tuple[Path, ...] = ()
    host: str = "127.0.0.1"
    port: int = 8700

    @property
    def examples_dir(self) -> Path:
        """예시 모드가 읽는 폴더. BYUL_EXAMPLES_DIR로 데모 자료(byul_platform.demo) 폴더를 줄 수 있다."""
        return self.examples_override or self.contracts_dir / "examples"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            contracts_dir=_path("BYUL_CONTRACTS_DIR") or DEFAULT_CONTRACTS_DIR,
            examples_override=_path("BYUL_EXAMPLES_DIR"),
            web_dist=_path("BYUL_WEB_DIST") or DEFAULT_WEB_DIST,
            runtime_base_url=os.environ.get("RUNTIME_BASE_URL", "").strip() or None,
            runtime_viewer_token=os.environ.get("RUNTIME_VIEWER_TOKEN", "").strip() or None,
            update_reports_dir=_path("UPDATE_REPORTS_DIR"),
            platform_data_dir=_path("PLATFORM_DATA_DIR"),
            evidence_index_paths=tuple(_paths("EVIDENCE_INDEX_PATHS")),
            host=os.environ.get("BYUL_PLATFORM_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=int(os.environ.get("BYUL_PLATFORM_PORT", "8700")),
        )
