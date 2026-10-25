"""INV-07: 자동 판정·권고는 승인이 아니다. 코드가 approval을 채우지 않는다."""

import inspect

from guard.core import report as report_mod
from guard.core.report import build_report
from tests.builders import T0
from tests.report_setup import pipeline


def test_report_never_has_approval_even_if_all_pass(tmp_path):
    store, pack, plan = pipeline(tmp_path, include=["REQ-01"], status="CONFIRMED", confirmed_by="a", confirmed_at=T0)
    rpt = build_report(store, pack, plan, as_of=T0)
    (row,) = [r for r in rpt.by_check if r.check_id == "REQ-01"]
    assert row.single.security == "PASS"
    assert rpt.approval is None


def test_build_report_has_no_approval_input():
    params = inspect.signature(build_report).parameters
    assert not any("approv" in p for p in params)
    assert "approval=" not in inspect.getsource(report_mod).replace("approval=None", "")
