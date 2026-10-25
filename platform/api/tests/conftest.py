from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from byul_platform.config import DEFAULT_CONTRACTS_DIR, Settings
from byul_platform.contracts import Contracts
from byul_platform.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLES = DEFAULT_CONTRACTS_DIR / "examples"


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def example_events() -> list[dict]:
    lines = (EXAMPLES / "runtime-events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.fixture(scope="session")
def contracts() -> Contracts:
    return Contracts(DEFAULT_CONTRACTS_DIR)


@pytest.fixture
def example_client(tmp_path: Path) -> Iterator[TestClient]:
    """환경변수 없이 뜬 예시 모드. 화면 정적 파일은 없는 폴더를 가리킨다."""
    app = create_app(Settings(web_dist=tmp_path / "no-dist"))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def store_client(tmp_path: Path) -> Iterator[TestClient]:
    """파일 저장소(PLATFORM_DATA_DIR)를 쓰는 앱."""
    app = create_app(Settings(platform_data_dir=tmp_path / "data", web_dist=tmp_path / "no-dist"))
    with TestClient(app) as client:
        yield client
