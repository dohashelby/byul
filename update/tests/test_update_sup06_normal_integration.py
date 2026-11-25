"""Exercise paired ordinary SUP-06 preparation and fail-closed public execution."""

from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from guard import application
from guard.executors import sup06_normal_worker_shards as native
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex, utcnow
from guard.store import Store
from tests.builders import policy
from tests.test_gpu_a_normal_tp2_callbacks import _environment, _owned_pods, _stage, _target

PACK = Path(__file__).resolve().parents[1] / "packs" / "PACK-BASELINE"


@pytest.fixture
def owned_pair(tmp_path):
    pack = load_pack(PACK)
    pol = policy(pack.ref(), scope={"include": ["SUP-06"]}, values={})
    cfg = {"adapter": {"name": "vllm"}, "executors": {"supply_boot": "kubernetes"},
           "update": {"mode": "external", "kubernetes_candidate_binding": "prebound", "allowed_checks": ["SUP-06"]},
           "sides": {}}
    envs = []
    for side in ("baseline", "candidate"):
        root = tmp_path / side
        root.mkdir()
        staging, record, _ = _stage(root, side, "worker-shards")
        namespace, engine, probe = _owned_pods(staging)
        env = _environment(record, namespace, engine)
        target = _target(env, record, namespace, engine, probe)
        path = root / "target.json"
        path.write_bytes(canonical_json(target))
        cfg["sides"][side] = {"check_config": {"SUP-06": {
            "normal_worker_shards_only": True,
            "target": str(path),
            "prepared": str(staging / "engine-callbacks/manifest.json"),
        }}}
        envs.append(env)
    return pack, pol, cfg, envs


def test_public_readiness_binds_two_distinct_owned_startup_inputs(owned_pair, monkeypatch):
    pack, pol, cfg, envs = owned_pair

    def no_dispatch(*args, **kwargs):
        raise AssertionError("readiness must not dispatch")

    monkeypatch.setattr(subprocess, "Popen", no_dispatch)
    ready = application.update_readiness(pack, envs, pol, cfg)
    assert ready["ready"], ready
    candidate = cfg["sides"]["candidate"]["check_config"]["SUP-06"]
    cases = []
    shared = deepcopy(cfg)
    shared["sides"]["candidate"] = deepcopy(shared["sides"]["baseline"])
    cases.append(shared)
    missing = deepcopy(cfg)
    missing["sides"]["candidate"]["check_config"]["SUP-06"]["prepared"] = candidate["prepared"] + ".absent"
    cases.append(missing)
    unapproved = deepcopy(cfg)
    unapproved["sides"]["candidate"]["check_config"]["SUP-06"]["worker_shard_input"] = {"path": "untrusted"}
    cases.append(unapproved)
    for invalid in cases:
        report = application.update_readiness(pack, envs, pol, invalid)
        assert not report["ready"], report


def test_public_update_does_not_promote_missing_startup_terminal(owned_pair, tmp_path, monkeypatch):
    pack, pol, cfg, envs = owned_pair
    attempts = []

    def missing_capture(transport, scope, options, archive, epochs=None):
        attempts.append((transport, scope, epochs))
        raise ValueError("CALLBACK_WORKER_TERMINAL_UNVERIFIED")

    monkeypatch.setattr(native, "_capture", missing_capture)
    store = Store(tmp_path / "store")
    report, _ = application.execute_update(store, pack, envs, pol, cfg, tmp_path / "update", utcnow())
    row = next(item for item in report.by_check if item.check_id == "SUP-06")
    result = store.resolve(row.baseline.result)
    evidence = store.resolve(result.evidence)
    assert len(attempts) == 1
    assert evidence.execution.status == ExecStatus.ABORTED
    assert evidence.data_origin == DataOrigin.EXAMPLE
    assert evidence.execution.abort_reason == "CALLBACK_WORKER_TERMINAL_UNVERIFIED"
    assert not any(step.attempted for step in evidence.challenges)
    assert not evidence.observations["supply_boot_normal_verified"].value
    selected = cfg["sides"]["baseline"]["check_config"]["SUP-06"]
    assert evidence.execution.run_binding["files"][native.TARGET_FILE] == sha256_hex(Path(selected["target"]).read_bytes())
    assert evidence.execution.run_binding["files"][native.PREPARED_FILE] == sha256_hex(Path(selected["prepared"]).read_bytes())
