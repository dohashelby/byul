"""INV-19: 이월은 검증된 기존 PASS의 정확한 Ref이며 새 실행·판정이 아니다."""

import pytest

from guard.core.change_selection import SelectionContext, classify_engine_change
from guard.core.select import select
from guard.models.objects import CarriedOver
from tests.builders import T0, baseline_pack, dummy_ref, environment, policy


@pytest.mark.parametrize("before,after,expected", [
    ("0.31.0", "0.32.0", "ENGINE_RELEASE"),
    ("0.32.0", "0.32.1", "ENGINE_PATCH"),
    ("0.10.1", "0.10.1.1", "ENGINE_PATCH"),
    ("0.32.1", "0.32.0", "ENGINE_RELEASE"),
    ("0.32.0", "0.32.0", "ENGINE_RELEASE"),
    ("0.32.0", "0.32.1rc1", "ENGINE_RELEASE"),
    (None, "0.32.1", "ENGINE_RELEASE"),
])
def test_only_confirmed_forward_patch_can_reduce_scope(before, after, expected):
    assert classify_engine_change(before, after) == expected


def test_carried_item_does_not_become_a_selected_execution():
    pack = baseline_pack()
    carried = CarriedOver(result=dummy_ref("RES"), evidence=dummy_ref("EVD"),
                          image_digest="image@sha256:" + "a" * 64, judged_at=T0,
                          reason="verified unchanged procedure")
    context = SelectionContext(change_type="INPUT", previous_plan=dummy_ref("PLAN"),
                               carried_over={"PLT-04": carried})
    plan = select(pack, [environment()], policy(pack.ref()), context=context)
    item = plan.item("PLT-04")
    assert not item.selected
    assert item.not_selected_reason.value == "CARRIED_OVER"
    assert item.carried_over.result == carried.result
    assert item.carried_over.evidence == carried.evidence
    assert carried.result in plan.refs and carried.evidence in plan.refs
    assert plan.inputs.previous_plan == context.previous_plan
    assert plan.change_type == "INPUT"


def test_evidenced_not_applicable_is_not_overridden_by_u1_change():
    pack = baseline_pack()
    env = environment({"gateway": {"value": False, "origin": "DECLARED",
                                    "source": "declared.yaml", "absence_evidence": "net-isolated"}})
    context = SelectionContext(change_type="ENGINE_RELEASE", previous_plan=dummy_ref("PLAN"))
    plan = select(pack, [env], policy(pack.ref()), context=context)
    assert plan.item("REQ-01").not_selected_reason.value == "NOT_APPLICABLE"
