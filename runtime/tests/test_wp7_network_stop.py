"""Actual network delivery ends the round; missing control remains a nonpass."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "case,facts,result,expected",
    [
        ("AWS-04", {"direct_connected": True}, "failed", "RecoveryFailure"),
        ("AWS-05", {"engine_receiver_failed": False}, "failed", "RecoveryFailure"),
        ("AWS-05", {"engine_receiver_failed": True, "receiver_control_connected": False}, "unknown", "CheckFailure"),
        ("AWS-05", {"engine_receiver_failed": True, "receiver_control_connected": True}, "passed", None),
    ],
)
def test_actual_block_failure_stops_instead_of_continuing(tmp_path, monkeypatch, case, facts, result, expected):
    root = Path(__file__).resolve().parents[1] / "project-context/refactor-2026-11/wp7"
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location("wp7_network_stop", root / "server_checks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    live = SimpleNamespace(
        out=tmp_path,
        python="python",
        context="fixture",
        ns="fixture",
        c={"token_env_file": "private-env"},
        gateway="http://localhost",
        command_log=[],
        root=tmp_path,
    )

    def run(argv, **kwargs):
        parent = Path(argv[argv.index("--evidence-dir") + 1]) / "run"
        parent.mkdir()
        (parent / "report.json").write_text(
            json.dumps({"cases": [{"case_id": case, "facts": facts, "result": result}]})
        )
        return (0 if result == "passed" else 1), ""

    monkeypatch.setattr(module, "run", run)
    live.output_log = []
    if expected:
        with pytest.raises(getattr(module, expected)):
            module.Live.acceptance(live, [case])
    else:
        assert module.Live.acceptance(live, [case])[case]["result"] == "passed"
