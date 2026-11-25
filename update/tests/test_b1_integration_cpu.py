"""EXAMPLE proposal integration; actual request class runs separately."""
from functools import partial

import pytest

from guard.core.judge import judge_plan
from guard.core.report import build_report
from tools import b1_integration_cpu as archived


@pytest.mark.parametrize('scenario', archived.SCENARIOS)
def test_req10_archived_proposal_preserves_legacy_judgment_and_report(tmp_path, monkeypatch, scenario):
    monkeypatch.setattr(archived, "judge_plan", partial(judge_plan, binary_security=False))
    monkeypatch.setattr(archived, "build_report", partial(build_report, binary_security=False))
    outcomes={'none':'accepted','auto':'accepted','required':'accepted','named-only':'accepted','named-json':'rejected'}
    result=archived.run_case(tmp_path/scenario,scenario,outcomes)
    assert result['exact_ref_rejudge_preserved'] and result['origin']=='EXAMPLE'
