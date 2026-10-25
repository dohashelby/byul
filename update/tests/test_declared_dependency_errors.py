"""Dependency failures must not erase declared verdicts or leak diagnostics."""
from copy import deepcopy

import pytest

from guard.core.judge import judge_evidence
from guard.judges import declared
from guard.models.objects import ExecutorInfo
from tests.builders import T0, baseline_pack, policy
from tests.test_policy_m4 import unit as policy_unit
from tests.test_policy_m7 import unit as audit_unit

AXES = ("security", "functional", "performance")
PRIVATE_DIAGNOSTIC = "EXAMPLE-private-dependency-diagnostic"


@pytest.fixture(scope="module")
def pack():
    return baseline_pack()


@pytest.mark.parametrize("dependency", ["evaluate", "evaluate_audit"])
@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
@pytest.mark.parametrize("claimed", [
    dict.fromkeys(AXES, "FAIL"),
    dict.fromkeys(AXES, "UNKNOWN"),
    dict.fromkeys(AXES, "PASS"),
    dict(security="FAIL", functional="PASS", performance="UNKNOWN"),
])
def test_dependency_error_preserves_non_pass_and_redacts_reason(
    pack, monkeypatch, dependency, error_type, claimed
):
    if dependency == "evaluate":
        check = pack.checks["SUP-14"]
        evd, values = policy_unit(check, verdicts=claimed)
        code, missing = "POLICY_ITEMS_EVALUATION_ERROR", "declared_policy_evidence"
    else:
        check = pack.checks["OBS-11"]
        evd, values = audit_unit(pack, verdicts=claimed)
        code, missing = "AUDIT_COLLECTION_EVALUATION_ERROR", "declared_audit_evidence"
    evd.execution.executor = ExecutorInfo(
        name="manual-entry", version="test", config_sha256="0" * 64, default_judge="declared"
    )
    evd = evd.seal()
    pol = policy(pack.ref(), values={check.id: values})
    before = deepcopy((check.model_dump(), evd.to_dict(), values))
    calls = []

    def unavailable(*args):
        calls.append(args[0].id)
        raise error_type(PRIVATE_DIAGNOSTIC)

    monkeypatch.setattr(declared, dependency, unavailable)
    expected = {axis: "UNKNOWN" if verdict == "PASS" else verdict for axis, verdict in claimed.items()}
    direct = declared.DeclaredJudge().judge(check, evd, {}, values)
    stored_result = judge_evidence(check, evd, pol, T0)

    assert direct.verdicts == expected
    assert stored_result.verdicts.model_dump() == expected
    for output in (direct, stored_result):
        assert any(code in reason for reason in output.reasons)
        assert missing in output.missing_observations
        assert PRIVATE_DIAGNOSTIC not in repr(output)
    assert calls == [check.id, check.id]
    assert (check.model_dump(), evd.to_dict(), values) == before
