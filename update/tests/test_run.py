"""Run boundary regressions with EXAMPLE in-memory executors and local files.

Malformed executor output remains an executor failure. Only artifact persistence
errors after a valid draft may preserve its independent facts. No engine,
Docker daemon or network is used here.
"""
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.core import run as module
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.base import DraftArtifact, EvidenceDraft
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex
from guard.store import Store
from tests.builders import T0, baseline_pack, environment, policy, req01_obs


DIAGNOSTIC = "ARTIFACT_PERSISTENCE_INCOMPLETE"


@pytest.fixture
def case(tmp_path, monkeypatch):
    pack = baseline_pack()
    check = pack.checks["REQ-01"]
    env = environment({"gateway": {"value": True, "origin": "EXAMPLE", "source": "EXAMPLE fixture"}})
    pol = policy(pack.ref(), scope={"include": [check.id], "exclude": []})
    store = Store(tmp_path / "store")
    store.put_many([env, pol])
    plan = select(pack, [env], pol)
    store.put(plan)
    assert plan.item(check.id).selected is True
    draft = EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
        normal_control={"attempted": True, "succeeded": True, "detail": "EXAMPLE control"},
        challenges=[{"step": step, "attempted": True} for step in check.challenge_steps],
        observations=req01_obs(), config={"profile": "EXAMPLE-run-boundary", "parameter": 3},
        comparability={"fixture_sha256": sha256_hex(b"EXAMPLE fixed fixture")},
        started_at=T0, finished_at=T0 + timedelta(seconds=1))

    class ExampleExecutor:
        name, version = "EXAMPLE-run-boundary", "1"
        output, error = draft, None

        def run(self, *args):
            if self.error is not None:
                raise self.error
            return self.output

    executor = ExampleExecutor()
    monkeypatch.setitem(registry.EXECUTORS, "direct_route:unit", lambda cfg: executor)
    cfg = RunConfig(executors={"direct_route": "unit"})
    def invoke():
        result, = run_plan(store, pack, plan, cfg)
        return result
    def add_files(count):
        rows = []
        for index in range(count):
            path = tmp_path / f"EXAMPLE-{index}.bin"
            path.write_bytes(f"EXAMPLE bytes {index}\r\n".encode())
            rows.append(DraftArtifact(name=path.name, path=path, collector="EXAMPLE-run-boundary/1"))
        draft.artifacts = rows
        return rows
    return SimpleNamespace(store=store, pack=pack, plan=plan, env=env, draft=draft,
                           executor=executor, invoke=invoke, add_files=add_files)


def assert_empty_aborted(evd):
    assert evd.execution.status == ExecStatus.ABORTED
    assert evd.data_origin == DataOrigin.EXAMPLE
    assert evd.normal_control.attempted is False and evd.normal_control.succeeded is None
    assert evd.challenges and all(not row.attempted for row in evd.challenges)
    assert evd.observations and all(not row.collected and row.value is None for row in evd.observations.values())
    assert evd.artifacts == []
    assert DIAGNOSTIC not in evd.execution.abort_reason
    assert evd.execution.executor.config_sha256 == sha256_hex(b"")


def test_original_executor_exception_keeps_the_existing_empty_aborted_boundary(case):
    case.executor.error = RuntimeError("EXAMPLE original executor failure")
    evd = case.invoke()
    assert_empty_aborted(evd)
    assert evd.execution.abort_reason == "executor failure: RuntimeError: EXAMPLE original executor failure"
    assert evd.execution.executor.name == case.executor.name
    assert evd.execution.executor.version == case.executor.version


@pytest.mark.parametrize("kind", ["mapping", "none", "normal_control", "artifact_mapping", "unfinished_measured", "invalid_observation"])
def test_malformed_draft_is_not_repaired_or_reclassified_as_artifact_persistence(case, kind):
    if kind == "mapping":
        case.executor.output = {"status": "COMPLETED"}
    elif kind == "none":
        case.executor.output = None
    elif kind == "normal_control":
        case.executor.output = case.draft.model_copy(update={"normal_control": {"attempted": True, "succeeded": True}})
    elif kind == "artifact_mapping":
        row, = case.add_files(1)
        case.executor.output = case.draft.model_copy(update={"artifacts": [row.model_dump()]})
    elif kind == "unfinished_measured":
        # Deliberately bypass construction to simulate invalid plugin output.
        # This is a schema counterexample, never a claim of actual measurement.
        case.executor.output = case.draft.model_copy(update={"status": ExecStatus.ABORTED, "data_origin": DataOrigin.MEASURED})
    else:
        observations = dict(case.draft.observations)
        key = next(iter(observations))
        observations[key] = observations[key].model_copy(update={"collected": False})
        case.executor.output = case.draft.model_copy(update={"observations": observations})
    evd = case.invoke()
    assert_empty_aborted(evd)
    assert evd.execution.abort_reason.startswith("executor failure:")


def test_artifact_metadata_creation_failure_preserves_valid_draft_and_later_references(case, monkeypatch):
    rows = case.add_files(3)
    before = case.draft.model_dump(mode="json")
    original, calls = case.store.put_artifact, []
    def put(data):
        calls.append(data)
        stored = original(data)
        if len(calls) == 2:
            return SimpleNamespace(path=stored.path, sha256=stored.sha256, bytes="EXAMPLE_INVALID_METADATA_CANARY")
        return stored
    monkeypatch.setattr(case.store, "put_artifact", put)
    evd = case.invoke()
    assert len(calls) == 3
    assert evd.execution.status == ExecStatus.ABORTED and evd.data_origin == DataOrigin.EXAMPLE
    assert evd.execution.abort_reason == DIAGNOSTIC
    assert [row.name for row in evd.artifacts] == [rows[0].name, rows[2].name]
    assert evd.observations == case.draft.observations
    assert evd.normal_control == case.draft.normal_control and evd.challenges == case.draft.challenges
    assert evd.comparability == case.draft.comparability
    assert evd.execution.executor.config_sha256 == sha256_hex(canonical_json(case.draft.config))
    assert evd.execution.started_at == case.draft.started_at and evd.execution.finished_at == case.draft.finished_at
    assert case.draft.model_dump(mode="json") == before
    raw = json.dumps(evd.to_dict())
    assert "EXAMPLE_INVALID_METADATA_CANARY" not in raw and str(rows[1].path) not in raw
    assert rows[1].name not in raw
    assert case.store.verify() == []


@pytest.mark.parametrize("stage", ["executor", "read", "put", "metadata"])
@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit])
def test_base_exception_is_not_swallowed_by_executor_or_artifact_error_boundaries(case, monkeypatch, stage, failure_type):
    failure = failure_type("EXAMPLE cancellation")
    row, = case.add_files(1)
    def stop(*args, **kwargs):
        raise failure
    if stage == "executor":
        case.executor.error = failure
    elif stage == "read":
        original = Path.read_bytes
        def read(path):
            if path == row.path:
                raise failure
            return original(path)
        monkeypatch.setattr(Path, "read_bytes", read)
    elif stage == "put":
        monkeypatch.setattr(case.store, "put_artifact", stop)
    else:
        monkeypatch.setattr(module, "Artifact", stop)
    with pytest.raises(failure_type) as caught:
        case.invoke()
    assert caught.value is failure
    assert list(case.store.iter_kind("EVD")) == []
