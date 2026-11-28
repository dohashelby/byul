"""Keep complete plan selection separate from the bounded execution path."""

from pathlib import Path

import pytest

from guard.configuration import REVIEW_ALL_SELECTED, REVIEWED_NORMAL_CASES, REVIEWED_NORMAL_OPTION
from guard.core import run
from guard.core.select import select
from guard.models import ExecStatus, Mode, Side, load_pack, new_id
from tests.builders import environment, policy


EXCLUDED = {"LIF-08", "LIF-11", "REQ-14", "REQ-16", "REQ-21", "SUP-06"}


def test_all_selected_keeps_unreviewed_checks_in_plan_and_stops_before_dispatch(monkeypatch):
    pack = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
    active = {check.id for check in pack.active_checks()}
    assert len(active) == 69
    selected = sorted(active - EXCLUDED)
    approved = policy(pack.ref(), scope={"include": selected, "exclude": sorted(EXCLUDED)})
    environments = [environment(id="ENV-BASELINE"), environment(id="ENV-CANDIDATE")]
    plan = select(pack, environments, approved, mode=Mode.update)
    assert len(selected) == 63
    assert {item.check_id for item in plan.items if item.selected} == set(selected)
    config = {"update": {REVIEWED_NORMAL_OPTION: True, REVIEW_ALL_SELECTED: True, "allowed_checks": selected}}

    def forbidden(*args, **kwargs):
        pytest.fail("an unreviewed check reached executor resolution")

    monkeypatch.setattr(run, "_executor_for_item", forbidden)
    for side, env in zip((Side.baseline, Side.candidate), environments, strict=True):
        for item in plan.items:
            if not item.selected or item.check_id in REVIEWED_NORMAL_CASES:
                continue
            evd = run._run_one(None, pack.checks[item.check_id], item, env, {},
                               run.RunConfig(raw=config, attempt_id=new_id("ATTEMPT")),
                               None, None, plan, {"id": new_id("EVD"), "plan": plan.ref(), "check_id": item.check_id,
                                                  "side": side, "environment": env.ref(),
                                                  "refs": [plan.ref(), env.ref()]},
                               allowed_checks=frozenset(selected))
            assert evd.hash_ok()
            assert evd.execution.status is ExecStatus.NOT_RUN
            assert evd.execution.abort_reason == "REVIEWED_NORMAL_PATH_UNAVAILABLE"
            assert not evd.normal_control.attempted and not any(row.attempted for row in evd.challenges)
