"""INV-17: DRAFT 정책으로 만든 보고서는 '공식 아님'. 검토 전 팩도 마찬가지."""

from guard.core.report import build_report
from guard.models.checkdef import PackReview
from guard.models import DataOrigin
from tests.builders import T0, baseline_pack
from tests.report_setup import pipeline


def _reviewed_pack():
    pack = baseline_pack()
    m = pack.manifest.model_copy(update={"review": PackReview(status="REVIEWED", reviewer="r", reviewed_at=T0)})
    return pack.model_copy(update={"manifest": m})


def test_draft_policy_not_official(tmp_path):
    store, pack, plan = pipeline(tmp_path, include=["REQ-01"], pack=_reviewed_pack())
    rpt = build_report(store, pack, plan, as_of=T0)
    assert rpt.official is False and rpt.policy_status == "DRAFT"
    assert any("DRAFT" in l for l in rpt.limitations)


def test_unreviewed_pack_not_official(tmp_path):
    store, pack, plan = pipeline(tmp_path, include=["REQ-01"], status="CONFIRMED", confirmed_by="a", confirmed_at=T0)
    rpt = build_report(store, pack, plan, as_of=T0)
    assert rpt.official is False and rpt.pack_review_status == "DRAFT"
    assert any("검토 전 팩" in l for l in rpt.limitations)


def test_example_data_never_official(tmp_path):
    store, pack, plan = pipeline(tmp_path, include=["REQ-01"], pack=_reviewed_pack(),
                                 status="CONFIRMED", confirmed_by="a", confirmed_at=T0)
    rpt = build_report(store, pack, plan, as_of=T0)
    assert rpt.data_origin == DataOrigin.EXAMPLE
    assert rpt.official is False
    assert any("EXAMPLE" in l for l in rpt.limitations)
