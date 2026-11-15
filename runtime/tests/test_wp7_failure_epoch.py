"""Version-scoped failure counts retain every historical cost entry."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_failure_epoch_and_cumulative_costs(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1] / "labtools/wp7"
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location("wp7_failure_epoch", root / "execute_from_mac.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ledger = tmp_path / "ledger.json"
    ledger.write_text("{}")
    artifact = tmp_path / "artifacts"
    artifact.mkdir()
    names = ["serving-gateway-rt-7741687d.tar", "byul-7741687d.bundle"]
    for name in names:
        (artifact / name).write_text("fixture")
    (artifact / "build.json").write_text(json.dumps({name: module.digest(artifact / name) for name in names}))
    args = SimpleNamespace(
        round=1, approval_file="approval", config="config", ledger=str(ledger), artifacts=str(artifact)
    )
    config = {"ssh_alias": "fixture", "remote_dir": "/home/ubuntu/fixture", "server_checkout": "/home/ubuntu/checkout"}
    history = {"runs": []}
    monkeypatch.setattr(module, "approval", lambda _: {"rounds": [1]})
    monkeypatch.setattr(module, "private_json", lambda path: history if Path(path) == ledger else config)

    class ReachedPrestart(Exception):
        pass

    monkeypatch.setattr(module, "budget", lambda _: (_ for _ in ()).throw(ReachedPrestart()))
    for baseline, seconds, expected in [
        ("f" * 40, 10, ReachedPrestart),
        (module.BASELINE, 10, "THREE_CONSECUTIVE_FAILURES_STOP"),
        ("f" * 40, 20000, "CUMULATIVE_COST_GUARD"),
    ]:
        history["runs"] = [
            {"product_baseline": baseline, "upper_bound_seconds": seconds, "results": {"R-04": "failed"}}
            for _ in range(3)
        ]
        if isinstance(expected, str):
            with pytest.raises(ValueError, match=expected):
                module.execute(args)
        else:
            with pytest.raises(expected):
                module.execute(args)
