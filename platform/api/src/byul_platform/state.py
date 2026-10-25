"""앱이 쓰는 출처·저장소·계약을 한곳에 묶는다. 환경변수가 없는 출처는 예시 모드로 뜬다."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Request

from .config import Settings
from .contracts import Contracts, canonical_sha256
from .sources.evidence import EvidenceSource
from .sources.runtime import ExampleRuntimeSource, LiveRuntimeSource, RuntimeClient, RuntimeSource
from .sources.update import ExampleUpdateSource, ReportDirUpdateSource, UpdateSource
from .store import EnvironmentStore, FileDocumentStore, PolicyStore


@dataclass
class AppState:
    settings: Settings
    contracts: Contracts
    runtime: RuntimeSource
    update: UpdateSource
    evidence: EvidenceSource
    environments: EnvironmentStore
    policies: PolicyStore
    store_mode: str
    store_detail: str | None
    _cleanup: list[Any] = field(default_factory=list)

    @property
    def example_mode(self) -> bool:
        return "example" in (self.runtime.mode, self.update.mode, self.evidence.mode, self.store_mode)

    def source_infos(self) -> list[dict[str, Any]]:
        return [
            {"name": "runtime", "mode": self.runtime.mode, "detail": self.runtime.detail},
            {"name": "update", "mode": self.update.mode, "detail": self.update.detail},
            {"name": "evidence", "mode": self.evidence.mode, "detail": self.evidence.detail},
            {"name": "store", "mode": self.store_mode, "detail": self.store_detail},
        ]

    def source(self, name: str) -> dict[str, Any]:
        return next(s for s in self.source_infos() if s["name"] == name)

    def close(self) -> None:
        for item in self._cleanup:
            item()


def _seed_example_store(root: Path, examples: Path, envs: FileDocumentStore, pols: FileDocumentStore) -> None:
    env = json.loads((examples / "environment.json").read_text(encoding="utf-8"))
    envs.append(env["environment_id"], env["revision"], env, status="EXAMPLE", label=None)
    pol = json.loads((examples / "policy.json").read_text(encoding="utf-8"))
    pols.append(pol["policy_id"], 1, pol, status=pol["status"], label=pol["policy_version"])
    assert canonical_sha256(env) == pol["environment_ref"]["sha256"]


def build_state(settings: Settings) -> AppState:
    contracts = Contracts(settings.contracts_dir)
    examples = settings.examples_dir
    cleanup: list[Any] = []

    if settings.runtime_base_url:
        client = RuntimeClient(settings.runtime_base_url, settings.runtime_viewer_token)
        runtime: RuntimeSource = LiveRuntimeSource(client, settings.runtime_base_url)
        cleanup.append(client.close)
    else:
        runtime = ExampleRuntimeSource(examples)

    update: UpdateSource = (
        ReportDirUpdateSource(settings.update_reports_dir)
        if settings.update_reports_dir
        else ExampleUpdateSource(examples)
    )

    evidence = (
        EvidenceSource(list(settings.evidence_index_paths), "file")
        if settings.evidence_index_paths
        else EvidenceSource([examples / "evidence-index.json"], "example")
    )

    if settings.platform_data_dir:
        root = settings.platform_data_dir
        envs = FileDocumentStore(root, "environments")
        pols = FileDocumentStore(root, "policies")
        store_mode, store_detail = "file", str(root)
    else:
        # 예시 모드: 프로세스마다 임시 폴더를 만들고 예시 환경·정책을 1판본으로 넣는다. 종료하면 지운다.
        root = Path(tempfile.mkdtemp(prefix="byul-platform-example-"))
        cleanup.append(lambda: shutil.rmtree(root, ignore_errors=True))
        envs = FileDocumentStore(root, "environments")
        pols = FileDocumentStore(root, "policies")
        _seed_example_store(root, examples, envs, pols)
        store_mode, store_detail = "example", f"{root} (임시, 종료 시 삭제)"

    return AppState(
        settings=settings,
        contracts=contracts,
        runtime=runtime,
        update=update,
        evidence=evidence,
        environments=envs,
        policies=pols,
        store_mode=store_mode,
        store_detail=store_detail,
        _cleanup=cleanup,
    )


def get_state(request: Request) -> AppState:
    return request.app.state.byul


def partition(contracts: Contracts, schema: str, candidates: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """계약 검증으로 나눈다. 실패한 항목은 버리지 않고 invalid로 돌려준다."""
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for index, item in enumerate(candidates):
        errors = contracts.errors(schema, item)
        if errors:
            invalid.append({"index": index, "errors": [f"{e.path}: {e.message}" for e in errors], "raw": item})
        else:
            valid.append(item)
    return valid, invalid


def invalid_one(contracts: Contracts, schema: str, item: Any) -> dict[str, Any] | None:
    errors = contracts.errors(schema, item)
    if not errors:
        return None
    return {"index": 0, "errors": [f"{e.path}: {e.message}" for e in errors], "raw": item}
