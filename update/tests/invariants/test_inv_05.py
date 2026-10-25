"""INV-05: 기본 audit·정식 update는 전체 범위, 증분 선정은 승인된 U1 계약으로만 줄인다.

기존 변경 분석의 추가·기본 범위 보존 사례는 update 모드 회귀로 그대로 유지한다.
"""

from guard.core.select import select
from guard.models.objects import Change
from tests.builders import T0, baseline_pack, environment, policy

D = {"origin": "DECLARED", "source": "env.yaml"}


def _change(facts):
    return Change(
        id="CHG-TEST", created_at=T0, data_origin="DECLARED",
        baseline={"version": "1.0.0", "source_commit": "a" * 40},
        candidate={"version": "1.1.0", "source_commit": "b" * 40},
        extractor={"name": "manual", "version": "0"}, facts=facts,
    ).seal()


def _envs():
    return [environment({"gateway": {"value": True, **D}}, id="ENV-A"),
            environment({"gateway": {"value": True, **D}}, id="ENV-B")]


def test_no_impact_change_does_not_remove_baseline_checks():
    pack = baseline_pack()
    pol = policy(pack.ref())
    without = select(pack, _envs(), pol, mode="update")
    with_none = select(pack, _envs(), pol, change=_change([]), mode="update")
    sel = lambda p: {i.check_id for i in p.items if i.selected}  # noqa: E731
    assert sel(with_none) == sel(without)
    baseline_first = {c.id for c in pack.active_checks() if c.review_order == "reference_baseline_first"}
    assert baseline_first <= sel(with_none)


def test_change_only_adds_and_annotates():
    pack = baseline_pack()
    pol = policy(pack.ref())
    envs = [environment({"gateway": {"value": False, **D, "absence_evidence": "EVD-1#net"}}, id="ENV-A"),
            environment({"gateway": {"value": False, **D, "absence_evidence": "EVD-1#net"}}, id="ENV-B")]
    before = select(pack, envs, pol, mode="update")
    assert not before.item("REQ-01").selected
    chg = _change([{"id": "F1", "category": "API_ROUTE", "target": "/v1/x", "certainty": "PARTIAL",
                    "related_checks": ["REQ-01"]}])
    after = select(pack, envs, pol, change=chg, mode="update")
    item = after.item("REQ-01")
    assert item.selected and item.triggered_by_change == ["F1"]
    assert {i.check_id for i in before.items if i.selected} <= {i.check_id for i in after.items if i.selected}
