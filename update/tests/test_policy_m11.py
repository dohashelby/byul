"""REQ-09 declared PASS consistency; all inputs are synthetic, not engine proof."""
from copy import deepcopy

import json
import pytest
from guard.collectors.manual import import_evidence
from guard.core.judge import judge_evidence, judge_plan
from guard.core.report import build_report
from guard.input_policy import OBSERVATIONS, POLICIES
from guard.judges.declared import DeclaredJudge
from guard.judges.rule_table import RuleTableJudge
from guard.models import Evidence
from guard.models.checkdef import JudgeSpec
from tests.builders import T0, baseline_pack, policy
from tests.test_policy_m2 import _manual_evidence
from tests.test_policy_m4 import snapshot, write
from tests.test_policy_m10 import fixture_evidence, manual_setup, values

AXES = ("security", "functional", "performance")


@pytest.fixture(scope="module")
def pack():
    return baseline_pack()


def change(evd, scenario):
    if scenario == "security_fail":
        evd.observations["accepted_challenges"].value = ["token_id"]
        evd.observations["challenge_outcomes"].value["token_id"] = "200"
        evd.observations["confirmed_input_violations"].value = ["EXAMPLE-confirmed-token-id-consumption"]
    elif scenario == "functional_fail":
        evd.observations["functional_probe_results"].value["after_token_id"] = "fail"
    elif scenario == "security_unknown":
        evd.observations["challenge_outcomes"].value["token_id"] = "timeout"
    elif scenario == "functional_unknown":
        evd.observations["functional_probe_results"].value["after_token_id"] = "no_response"
    elif scenario == "missing_security":
        evd.observations.pop("challenge_outcomes")
    elif scenario == "both_fail":
        change(evd, "security_fail"); change(evd, "functional_fail")


@pytest.mark.parametrize("scenario,blocked", [
    ("normal", set()), ("security_fail", {"security"}), ("functional_fail", {"functional"}),
    ("security_unknown", {"security"}), ("functional_unknown", {"functional"}),
    ("missing_security", {"security"}), ("both_fail", {"security", "functional"})])
@pytest.mark.parametrize("raw", ["PASS", "FAIL", "UNKNOWN"])
def test_axis_ceiling_and_pure_repeated_evaluation(pack, scenario, blocked, raw):
    c = pack.checks["REQ-09"]; vals = values(c)
    evd = fixture_evidence(c, dict.fromkeys(AXES, raw)); change(evd, scenario)
    before, pbefore, cbefore = evd.to_dict(), deepcopy(vals), c.model_dump(mode="json")
    result = DeclaredJudge().judge(c, evd, {}, vals)
    for axis in AXES:
        expected = "UNKNOWN" if raw == "PASS" and axis in blocked else raw
        assert result.verdicts[axis] == expected
    if raw == "PASS" and blocked:
        assert any(r.startswith("POLICY_CONTEXT: REQ09_MANUAL_") for r in result.reasons)
    if scenario == "missing_security":
        assert "challenge_outcomes" in result.missing_observations
    assert DeclaredJudge().judge(c, evd, {}, vals) == result
    assert evd.to_dict() == before and vals == pbefore and c.model_dump(mode="json") == cbefore


@pytest.mark.parametrize("issue", ["absent", "unsupported", "empty", "operator", "exception"])
@pytest.mark.parametrize("raw", ["PASS", "FAIL", "UNKNOWN"])
def test_unavailable_rule_table_never_erases_declared_fail(pack, monkeypatch, issue, raw):
    c = pack.checks["REQ-09"].model_copy(deep=True)
    if issue == "absent": c.judge = None
    elif issue == "unsupported": c.judge = JudgeSpec(plugin="declared")
    elif issue == "empty": c.judge = JudgeSpec(plugin="rule_table", params={})
    elif issue == "operator": c.judge.params["pass_when_all"][0]["op"] = "EXAMPLE-unsupported"
    else:
        def error(*args, **kwargs): raise RuntimeError("EXAMPLE-sensitive-dependency-detail")
        monkeypatch.setattr(RuleTableJudge, "judge", error)
    result = DeclaredJudge().judge(c, fixture_evidence(c, dict.fromkeys(AXES, raw)), {}, values(c))
    expected = "UNKNOWN" if raw == "PASS" else raw
    assert result.verdicts["security"] == result.verdicts["functional"] == expected
    assert result.verdicts["performance"] == raw  # No blanket propagation of performance UNKNOWN.
    assert "EXAMPLE-sensitive-dependency-detail" not in " ".join(result.reasons)


@pytest.mark.parametrize("kind", ["legacy", "partial", "other"])
def test_no_evaluation_for_legacy_partial_or_other_checks(pack, monkeypatch, kind):
    c = pack.checks["SUP-03" if kind == "other" else "REQ-09"].model_copy(deep=True)
    if kind == "legacy":
        c.policy_params = [p for p in c.policy_params if p.key not in POLICIES]
        c.required_observations = [o for o in c.required_observations if o.key not in OBSERVATIONS]
    elif kind == "partial": c.policy_params.pop()
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("legacy/partial/other must not evaluate the input rule table")
    monkeypatch.setattr(RuleTableJudge, "judge", forbidden)
    evd = (_manual_evidence(c, dict.fromkeys(AXES, "PASS")) if kind == "other" else fixture_evidence(c))
    result = DeclaredJudge().judge(c, evd, {}, {} if kind == "other" else values(c))
    assert result.verdicts["security"] == ("UNKNOWN" if kind == "partial" else "PASS")
    assert not calls


def test_default_declared_does_not_recurse_or_change_automatic_fail(pack):
    c = pack.checks["REQ-09"]; evd = fixture_evidence(c); change(evd, "security_fail")
    evd = evd.seal(); pol = policy(pack.ref(), values={c.id: values(c)})
    result = judge_evidence(c, evd, pol, T0)
    assert result.judge.name == "declared" and result.verdicts.security == "UNKNOWN"
    assert not any("judge plugin failure" in r for r in result.reasons)
    auto = evd.to_dict(); auto["execution"]["executor"].pop("default_judge")
    result = judge_evidence(c, Evidence.model_validate(auto).seal(), pol, T0)
    assert result.judge.name == "rule_table" and result.verdicts.security == "FAIL"


@pytest.mark.parametrize("version,mode", [(1, "audit"), (2, "audit"), (3, "audit"), (3, "update")])
@pytest.mark.parametrize("scenario", ["security_fail", "functional_fail"])
def test_real_manual_import_rejudge_history_sides_and_reports(tmp_path, pack, version, mode, scenario):
    st, plan, doc = manual_setup(tmp_path, pack, mode)
    doc["template"] = f"guard-manual-evidence/{version}"
    if version == 1: doc.pop("policy"); doc.pop("pack")
    path = write(tmp_path, doc); import_evidence(st, pack, path, now=T0)
    first = judge_plan(st, pack, plan, T0)
    assert all(r.verdicts.security == r.verdicts.functional == "PASS" for r in first)
    original = snapshot(st)
    # Make only the candidate inconsistent in update mode; baseline is independent.
    side = "candidate" if mode == "update" else "single"
    entry = next(e for e in doc["checks"] if e["check_id"] == "REQ-09" and e["side"] == side)
    obs = {o["key"]: o for o in entry["required_observations"]}
    if scenario == "security_fail":
        obs["accepted_challenges"]["value"] = ["token_id"]
        obs["challenge_outcomes"]["value"]["token_id"] = "200"
        obs["confirmed_input_violations"]["value"] = ["EXAMPLE-confirmed-token-id-consumption"]
    else: obs["functional_probe_results"]["value"]["after_token_id"] = "fail"
    previous = next(e for e in st.find_latest("EVD", lambda e: e.check_id == "REQ-09" and e.side == side))
    import_evidence(st, pack, write(tmp_path, doc), now=T0)
    latest = next(e for e in st.find_latest("EVD", lambda e: e.check_id == "REQ-09" and e.side == side))
    assert previous.content_sha256 != latest.content_sha256
    assert previous.execution.executor.config_sha256 == latest.execution.executor.config_sha256
    results = judge_plan(st, pack, plan, T0)
    axis = "security" if scenario == "security_fail" else "functional"
    for r in results:
        assert r.verdicts.model_dump()[axis] == ("UNKNOWN" if r.check_id == "REQ-09" and r.side == side else "PASS")
    rpt = build_report(st, pack, plan, T0)
    assert not rpt.official and any("REQ09_MANUAL_OBSERVATION_CONFLICT" in x for x in rpt.limitations)
    assert "REQ09_MANUAL_OBSERVATION_CONFLICT" in json.dumps(rpt.to_dict(), ensure_ascii=False)
    for row in rpt.by_check:
        if row.check_id == "REQ-09":
            assert row.evidence_source == "manual"
            if mode == "update": assert row.comparison == "NOT_COMPARABLE"
    for obj in original.values(): assert st.get(obj["id"], obj["revision"]).to_dict() == obj
    after = snapshot(st); judge_plan(st, pack, plan, T0)
    assert snapshot(st) == after and not st.verify()


def test_explicit_cpu_performance_and_existing_scope_gate(pack):
    from guard.models.objects import CpuProfile
    c = pack.checks["REQ-09"]; evd = fixture_evidence(c)
    evd.comparability.cpu = CpuProfile(architecture="EXAMPLE-x86_64", model="EXAMPLE-cpu",
        system="EXAMPLE-Linux", kernel="EXAMPLE-kernel", threads=2)
    pol = policy(pack.ref(), values={c.id: values(c)})
    result = judge_evidence(c, evd.seal(), pol, T0)
    assert result.verdicts.security == result.verdicts.functional == "PASS"
    assert result.verdicts.performance == "UNKNOWN"
    evd.observations["input_formats_tested"].value = ["text"]
    result = judge_evidence(c, evd.seal(), pol, T0)
    assert result.verdicts.security == result.verdicts.functional == "UNKNOWN"
