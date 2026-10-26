"""INV-13: inventory_complete는 목적의 모든 CheckDef가 적용 판단을 받았고(UNDETERMINED 제외) 팩이 REVIEWED일 때만 true."""

from guard.core.select import select
from guard.models.checkdef import PackReview
from tests.builders import baseline_pack, environment, policy

D = {"origin": "DECLARED", "source": "env.yaml"}


def _purpose_fixture():
    """불변식의 그룹을 고정한다. 원본 체크리스트의 목적 이름 변경과 독립적이다.

    REQ-01은 gateway 사실로 적용 판단되고 REQ-02는 prose만 있어 미정이다.
    두 점검이 같은 목적에 속해야 '하나라도 미정이면 미완결'을 검증한다.
    """
    pack = baseline_pack()
    for cid in ("REQ-01", "REQ-02"):
        pack.checks[cid] = pack.checks[cid].model_copy(update={"purpose": "요청·대상 접근 권한", "secondary_purposes": []})
    return pack


def _reviewed(pack):
    m = pack.manifest.model_copy(update={"review": PackReview(status="REVIEWED", reviewer="r", reviewed_at="2026-10-19T00:00:00Z")})
    return pack.model_copy(update={"manifest": m})


def test_draft_pack_never_complete():
    pack = baseline_pack()
    plan = select(pack, [environment({"gateway": {"value": True, **D}})], policy(pack.ref()))
    assert plan.inventory_complete and not any(plan.inventory_complete.values())


def test_undetermined_check_makes_purpose_incomplete_even_if_reviewed():
    pack = _reviewed(_purpose_fixture())
    plan = select(pack, [environment({"gateway": {"value": True, **D}})], policy(pack.ref()))
    # 같은 목적의 REQ-02는 prose뿐이라 UNDETERMINED → 이 목적은 false
    assert plan.inventory_complete["요청·대상 접근 권한"] is False


def test_reviewed_pack_with_all_judged_purpose_is_complete():
    pack = _reviewed(_purpose_fixture())
    # 목적 하나를 REQ-01 하나만 남긴 팩으로 축소
    checks = {"REQ-01": pack.checks["REQ-01"]}
    m = pack.manifest.model_copy(update={"checks": ["REQ-01"]})
    small = pack.model_copy(update={"manifest": m, "checks": checks})
    plan = select(small, [environment({"gateway": {"value": True, **D}})], policy(small.ref()))
    assert plan.inventory_complete == {"요청·대상 접근 권한": True}
    undetermined = select(small, [environment({})], policy(small.ref()))
    assert undetermined.inventory_complete == {"요청·대상 접근 권한": False}
