"""EXAMPLE reader doubles verify our collection/policy contract only."""
import json

import pytest

from guard.executors.serialization_decode import analyze_decoder_trial, collect_decoder_trial


class Rejected(Exception):
    pass


class Reader:
    def __init__(self, *, reject=False, effect=False, after_fails=False, before_fails=False, error=False):
        self.reject, self.effect, self.error = reject, effect, error
        self.after_fails, self.before_fails = after_fails, before_fails
        self.controls = self.calls = self.effects = 0

    def decode(self, frames):
        self.calls += 1
        if frames == [b"control"]:
            self.controls += 1
            if self.before_fails or (self.after_fails and self.controls == 2):
                raise TimeoutError("synthetic private diagnostic")
            return {"guard_control": True}
        self.effects += int(self.effect)
        if self.error:
            raise RuntimeError("synthetic private diagnostic")
        if self.reject:
            raise Rejected("synthetic private diagnostic")
        return {"opaque private decoded object": True}


def collect(reader, probe=True):
    return collect_decoder_trial(reader, b"private fixture", case="extension", control_payload=b"control",
        effect_probe=(lambda: {"callback": reader.effects}) if probe else None, rejection_types=(Rejected,))


def test_normal_rejection_and_after_control_complete_the_explicit_branch():
    trial = collect(Reader(reject=True))
    result = analyze_decoder_trial(trial, forbidden_effects=["callback"], expected_rejection=True)
    assert result["decoder_coverage_complete"] and not result["decoder_forbidden_effects_seen"]
    assert result["normal_controls"]


@pytest.mark.parametrize("changes", [{}, {"after_fails": True}, {"error": True}, {"reject": True}])
def test_observed_forbidden_callback_survives_later_error_or_rejection(changes):
    trial = collect(Reader(effect=True, **changes))
    result = analyze_decoder_trial(trial, forbidden_effects=["callback"], expected_rejection=True)
    assert result["decoder_forbidden_effects_seen"] == ["callback"]
    saved = json.dumps([trial, result])
    assert "private" not in saved and "diagnostic" not in saved


def test_policy_unset_collects_actual_effect_without_inventing_allowance():
    trial = collect(Reader(effect=True))
    result = analyze_decoder_trial(trial)
    assert trial["effect_counts"] == {"callback": 1}
    assert result["decoder_coverage_gaps"] == ["FORBIDDEN_EFFECT_POLICY_UNSET"]


def test_missing_measurement_is_unknown_and_does_not_fabricate_zero():
    trial = collect(Reader(reject=True), probe=False)
    assert trial["effect_counts"] == {}
    result = analyze_decoder_trial(trial, forbidden_effects=["callback"], expected_rejection=True)
    assert not result["decoder_coverage_complete"]
    assert "REQUIRED_EFFECT_NOT_MEASURED:callback" in result["decoder_coverage_gaps"]


def test_failed_initial_control_does_not_consume_challenge():
    reader = Reader(before_fails=True, effect=True)
    trial = collect(reader)
    assert not trial["attempted"] and reader.calls == 1 and reader.effects == 0


def test_counter_reset_cannot_claim_no_effects():
    values = iter([{"callback": 3}, {"callback": 0}])
    trial = collect_decoder_trial(Reader(), b"challenge", case="extension", control_payload=b"control",
                                 effect_probe=lambda: next(values))
    assert trial["effect_counts"] == {}
    assert "EFFECT_COUNTER_DISCONTINUITY" in trial["coverage_gaps"]


@pytest.mark.parametrize("effect,after_fails,expected", [(False, False, "UNKNOWN"),
    (False, True, "UNKNOWN"), (True, True, "FAIL")])
def test_proposed_existing_rule_table_preserves_fail_but_never_promotes_whole_check(effect, after_fails, expected):
    from pathlib import Path
    import yaml
    from guard.judges.rule_table import RuleTableJudge
    from tests.builders import baseline_pack, evidence

    params = yaml.safe_load((Path(__file__).parents[1] / "docs/b2-decoder/override-proposal.yaml").read_text())["judge"]["params"]
    facts = analyze_decoder_trial(collect(Reader(effect=effect, after_fails=after_fails)),
                                 forbidden_effects=["callback"])
    evd = evidence(check_id="SUP-12", observations={key: {"value": value, "collected": True} for key, value in facts.items()})
    out = RuleTableJudge().judge(baseline_pack().checks["SUP-12"], evd, params, {})
    assert out.verdicts["security"] == expected
