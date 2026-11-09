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
    bundle_runtime_dir: Path = REPO_ROOT / "runtime"
    bundle_runtime_python: Path | None = None
    operation_tokens: tuple[dict, ...] = field(default=(), repr=False)
    operation_lab_self_approval: bool = False
    operation_lease_seconds: int = 600
    operation_monitor_seconds: int = 86400
    host: str = "127.0.0.1"
    port: int = 8700
    # 로그인. 직접 만든 Settings(시험·라이브러리)는 꺼짐, 실행(from_env)은 기본 켜짐.
    login_enabled: bool = False
    accounts_file: Path | None = None
    require_password_change: bool = False
    session_idle_seconds: int = 7200
    session_max_seconds: int = 43200
    cookie_secure: bool = False

    @property
    def examples_dir(self) -> Path:
        """예시 모드가 읽는 폴더. BYUL_EXAMPLES_DIR로 데모 자료(byul_platform.demo) 폴더를 줄 수 있다."""
        return self.examples_override or self.contracts_dir / "examples"

    @classmethod
    def from_env(cls) -> Settings:
        from .operations import load_tokens

        return cls(
            operation_tokens=load_tokens(os.environ.get("BYUL_OPERATION_TOKENS", "[]")),
            operation_lab_self_approval=os.environ.get("BYUL_OPERATION_LAB_SELF_APPROVAL") == "1",
            operation_monitor_seconds=int(os.environ.get("BYUL_OPERATION_MONITOR_SECONDS", "86400")),
            contracts_dir=_path("BYUL_CONTRACTS_DIR") or DEFAULT_CONTRACTS_DIR,
            examples_override=_path("BYUL_EXAMPLES_DIR"),
            web_dist=_path("BYUL_WEB_DIST") or DEFAULT_WEB_DIST,
            runtime_base_url=os.environ.get("RUNTIME_BASE_URL", "").strip() or None,
            runtime_viewer_token=os.environ.get("RUNTIME_VIEWER_TOKEN", "").strip() or None,
            update_reports_dir=_path("UPDATE_REPORTS_DIR"),
            platform_data_dir=_path("PLATFORM_DATA_DIR"),
            evidence_index_paths=tuple(_paths("EVIDENCE_INDEX_PATHS")),
            bundle_runtime_dir=_path("BYUL_BUNDLE_RUNTIME_DIR") or REPO_ROOT / "runtime",
            bundle_runtime_python=_path("BYUL_BUNDLE_RUNTIME_PYTHON"),
            host=os.environ.get("BYUL_PLATFORM_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=int(os.environ.get("BYUL_PLATFORM_PORT", "8700")),
            login_enabled=os.environ.get("BYUL_LOGIN_DISABLED") != "1",
            accounts_file=_path("BYUL_ACCOUNTS_FILE"),
            require_password_change=os.environ.get("BYUL_REQUIRE_PASSWORD_CHANGE") == "1",
            session_idle_seconds=int(os.environ.get("BYUL_SESSION_IDLE_SECONDS", "7200")),
            session_max_seconds=int(os.environ.get("BYUL_SESSION_MAX_SECONDS", "43200")),
            cookie_secure=os.environ.get("BYUL_COOKIE_SECURE") == "1",
        )
