"""INV-14: run은 판정하지 않고 judge는 엔진에 접속하지 않는다. 판정 플러그인은 순수 함수다."""

import ast
import builtins
import socket
import time
from pathlib import Path

import pytest

from guard.judges.rule_table import RuleTableJudge
from guard.models import Evidence
from tests.builders import evidence
from tests.judging import OK, REQ01, policy

SRC = Path(__file__).resolve().parents[2] / "src/guard"


def _imports(path: Path) -> set[str]:
    mods = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_run_does_not_import_judges():
    assert not any(m.startswith("guard.judges") or m == "guard.core.judge" for m in _imports(SRC / "core/run.py"))


def test_evidence_model_has_no_verdict_fields():
    assert not {"verdicts", "verdict", "security"} & set(Evidence.model_fields)


def test_judge_side_does_not_import_network_or_executors():
    forbidden = ("httpx", "socket", "urllib", "requests", "subprocess", "guard.executors", "guard.adapters", "time")
    for f in [SRC / "core/judge.py", *(SRC / "judges").glob("*.py")]:
        bad = [m for m in _imports(f) if m.split(".")[0] in forbidden or m.startswith(forbidden)]
        assert bad == [], f"{f.name}: {bad}"


def _boom(*a, **k):
    raise AssertionError("side effect during judge")


@pytest.fixture
def no_io(monkeypatch):
    """네트워크·파일 접근 차단."""
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(builtins, "open", _boom)


@pytest.fixture
def no_side_effects(no_io, monkeypatch):
    """플러그인: 시계 접근까지 차단. (코어는 RES id·created_at을 만들 때만 시계를 쓴다)"""
    monkeypatch.setattr(time, "time", _boom)


def test_plugin_is_pure_and_deterministic(no_side_effects):
    evd = evidence(observations=OK)
    j = RuleTableJudge()
    a = j.judge(REQ01, evd, REQ01.judge.params, {})
    b = j.judge(REQ01, evd, REQ01.judge.params, {})
    assert a == b


def test_core_judge_has_no_side_effects(no_io):
    from guard.core.judge import judge_evidence
    from tests.builders import T0
    from tests.judging import PACK

    pol = policy(PACK.ref())
    r1 = judge_evidence(REQ01, evidence(observations=OK), pol, T0)
    r2 = judge_evidence(REQ01, evidence(observations=OK), pol, T0)
    assert r1.verdicts == r2.verdicts and r1.reasons == r2.reasons
