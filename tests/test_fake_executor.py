"""FakeExecutor: REQ-01에 대해 미리 정한 증거를 만든다. 실제 엔진·네트워크 없음."""

import pytest

from guard.executors.fake import SCENARIOS, FakeExecutor
from guard.models import DataOrigin
from tests.builders import baseline_pack, environment

PACK = baseline_pack()
REQ01 = PACK.checks["REQ-01"]


def _draft(scenario, tmp_path):
    return FakeExecutor(scenario).run(REQ01, environment(), {"allowed_callers": ["gateway-sa"]}, None, tmp_path)


def test_scenarios_exist():
    assert set(SCENARIOS) == {
        "pass", "direct_200", "not_attempted", "missing_obs",
        "refused_with_positive_control", "refused_without_positive_control", "partial_routes", "direct_200_on_invocations",
    }


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_drafts_are_example_and_cover_required_keys(scenario, tmp_path):
    d = _draft(scenario, tmp_path)
    assert d.data_origin == DataOrigin.EXAMPLE  # INV-02
    assert set(d.observations) == {o.key for o in REQ01.required_observations}
    assert [c.step for c in d.challenges] == REQ01.challenge_steps
    assert d.artifacts and all(a.path.exists() for a in d.artifacts)


def test_scenario_shapes(tmp_path):
    assert _draft("direct_200", tmp_path).observations["direct_outcomes"].value["/v1/completions"] == 200
    assert not any(c.attempted for c in _draft("not_attempted", tmp_path).challenges)
    assert _draft("missing_obs", tmp_path).observations["direct_outcomes"].collected is False
    assert _draft("refused_without_positive_control", tmp_path).observations["trusted_origin_reachable"].value is False
    assert _draft("partial_routes", tmp_path).observations["routes_tested"].value == ["/v1/completions"]


def test_unknown_check_raises(tmp_path):
    with pytest.raises(Exception):
        FakeExecutor("pass").run(PACK.checks["SUP-01"], environment(), {}, None, tmp_path)
