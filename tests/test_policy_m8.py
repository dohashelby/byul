"""M8: bool policy is review context, never proof of actual usage/replay state."""
from copy import deepcopy
import csv
import json

import pytest
import yaml

from guard.collectors.manual import build_template, import_evidence
from guard.collectors.policy_template import build_policy_template
from guard.core.judge import judge_evidence, judge_plan
from guard.core.report import build_report
from guard.core.select import PolicyValueError, select
from guard.judges.declared import DeclaredJudge
from guard.models import Evidence
from guard.models.checkdef import JudgeSpec, ObsSpec, PolicyParam
from guard.report.html import render_html
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import PACK_DIR, ROOT, T0, baseline_pack, environment, policy
from tests.test_policy_m2 import _manual_evidence
from tests.test_policy_m4 import fill, snapshot, write

FLAGS = {"OBS-04": ("usage_allowed", False), "OBS-05": ("usage_allowed", True),
         "OBS-07": ("replay_allowed", True), "OBS-08": ("replay_allowed", True)}
AXES = ("security", "functional", "performance")


@pytest.fixture(scope="module")
def pack():
    return baseline_pack()


def given(cid, value):
    return {FLAGS[cid][0]: value}


def test_declarations_catalog_and_blank_template_are_not_execution(pack):
    assert pack.manifest.revision == 21 and not pack.reviewed
    assert len(pack.checks) == 77
    assert sum(bool(c.policy_params) for c in pack.active_checks()) == 22
    assert sum(len(c.policy_params) for c in pack.active_checks()) == 49
    assert {c.id for c in pack.active_checks() if c.executor} == {"REQ-01", "REQ-03", "PLT-01", "PLT-04", "REQ-09", "REQ-10", "REQ-13", "OBS-03", "REQ-07", "REQ-11", "OBS-09"}
    for cid, (key, _) in FLAGS.items():
        check = pack.checks[cid]
        assert check.revision == 2 and check.executor is None and check.judge is None
        assert [(p.key, p.type, p.required) for p in check.policy_params] == [(key, "bool", True)]
    text = build_policy_template(pack)
    template = yaml.safe_load(text)
    assert all(template["values"][cid] == {} for cid in FLAGS)
    assert template["confirmed_by"] is None and template["confirmed_at"] is None
    assert template["status"] == "DRAFT" and template["data_origin"] == "DECLARED"
    assert template["scope"] == {"include": [], "exclude": []}
    rows = list(csv.DictReader((ROOT / "docs/policy_catalog.csv").open(encoding="utf-8", newline="")))
    assert len(rows) == 322 and sum(len(json.loads(r["check_ids"])) for r in rows) == 614
    assert {c: sum(r["classification"] == c for r in rows) for c in "abcd"} == dict(a=46, b=2, c=161, d=113)
    for pid, checks in (("P-DRAFT-USAGE-ALLOW", ("OBS-04", "OBS-05")), ("P-DRAFT-REPLAY-ALLOW", ("OBS-07", "OBS-08"))):
        row = next(r for r in rows if r["proposed_policy_id"] == pid)
        assert row["classification"] == "a" and row["proposed_shape"] == "bool"
        assert set(json.loads(row["current_param_paths"])) == {f"values.{cid}.{FLAGS[cid][0]}" for cid in checks}


@pytest.mark.parametrize("cid", FLAGS)
@pytest.mark.parametrize("bad", [None, 0, 1, "true", "false", [], {}, 1.0])
def test_flags_reject_coercion_even_outside_scope(pack, cid, bad):
    pol = policy(pack.ref(), scope={"include": ["SUP-03"]}, values={cid: given(cid, bad)})
    with pytest.raises(PolicyValueError, match=cid):
        select(pack, [environment()], pol)


@pytest.mark.parametrize("cid", FLAGS)
@pytest.mark.parametrize("flag", [True, False, "missing"])
def test_branch_matrix_only_caps_pass_and_does_not_mutate_inputs(pack, cid, flag):
    vals = {} if flag == "missing" else given(cid, flag)
    check = pack.checks[cid]
    evd = _manual_evidence(check, dict.fromkeys(AXES, "PASS"))
    before, before_vals = evd.to_dict(), deepcopy(vals)
    result = DeclaredJudge().judge(check, evd, {}, vals)
    active = type(flag) is bool and flag is FLAGS[cid][1]
    assert all(v == ("PASS" if active else "UNKNOWN") for v in result.verdicts.values())
    code = "POLICY_FLAG_UNRESOLVED" if flag == "missing" else ("POLICY_BRANCH_ACTIVE" if active else "POLICY_BRANCH_INACTIVE")
    assert any(code in r and r.startswith("POLICY_CONTEXT:") for r in result.reasons)
    assert DeclaredJudge().judge(check, evd, {}, vals) == result
    assert evd.to_dict() == before and vals == before_vals
    mixed = _manual_evidence(check, dict(security="FAIL", functional="UNKNOWN", performance="PASS"))
    mixed.artifacts = []
    mixed.observations["declared_observation_notes"].value = {}
    assert DeclaredJudge().judge(check, mixed, {}, vals).verdicts == dict(security="FAIL", functional="UNKNOWN", performance="UNKNOWN")


@pytest.mark.parametrize("cid", FLAGS)
@pytest.mark.parametrize("flag", [False, True])
def test_policy_flag_does_not_change_environment_applicability_or_scope(pack, cid, flag):
    env = environment()
    before = env.to_dict()
    plan = select(pack, [env], policy(pack.ref(), scope={"include": [cid]}, values={cid: given(cid, flag)}))
    item = plan.item(cid)
    assert item.selected and item.applicability == "UNDETERMINED"
    assert item.gaps == ["NO_EXECUTOR"] and not item.conditions
    assert not plan.item("SUP-03").selected
    assert env.to_dict() == before and not env.facts
    absent = select(pack, [env], policy(pack.ref(), scope={"include": [cid]}, values={}))
    assert "NO_POLICY_VALUE" in absent.item(cid).gaps


@pytest.mark.parametrize("cid", FLAGS)
@pytest.mark.parametrize("declaration", ["missing", "optional", "different_type"])
def test_old_and_other_declarations_keep_their_contract(pack, cid, declaration):
    check = pack.checks[cid].model_copy(deep=True)
    key = FLAGS[cid][0]
    check.policy_params = ([] if declaration == "missing" else
        [PolicyParam(key=key, type="str" if declaration == "different_type" else "bool", required=declaration != "optional")])
    vals = {} if declaration != "different_type" else {key: "EXAMPLE legacy context"}
    result = DeclaredJudge().judge(check, _manual_evidence(check, dict.fromkeys(AXES, "PASS")), {}, vals)
    assert all(v == "PASS" for v in result.verdicts.values())
    assert not any(r.startswith("POLICY_CONTEXT:") for r in result.reasons)


@pytest.mark.parametrize("cid", FLAGS)
def test_direct_judge_invalid_flag_is_fail_closed(pack, cid):
    check = pack.checks[cid]
    result = DeclaredJudge().judge(check, _manual_evidence(check, dict.fromkeys(AXES, "PASS")), {}, given(cid, "false"))
    assert all(v == "UNKNOWN" for v in result.verdicts.values())
    assert any("POLICY_FLAG_UNRESOLVED" in r and "=invalid" in r for r in result.reasons)


def test_flags_are_independent_and_never_copied_or_used_as_evidence(pack):
    pol = policy(pack.ref(), scope={"include": list(FLAGS)}, values={"OBS-04": given("OBS-04", False)})
    plan = select(pack, [environment()], pol)
    assert "NO_POLICY_VALUE" not in plan.item("OBS-04").gaps
    assert all("NO_POLICY_VALUE" in plan.item(cid).gaps for cid in FLAGS if cid != "OBS-04")
    check = pack.checks["SUP-03"]
    result = DeclaredJudge().judge(check, _manual_evidence(check, dict.fromkeys(AXES, "PASS")), {}, given("OBS-04", False))
    assert all(v == "PASS" for v in result.verdicts.values())
    assert not any(r.startswith("POLICY_CONTEXT:") for r in result.reasons)


@pytest.mark.parametrize("cid", FLAGS)
@pytest.mark.parametrize("gap", ["attachments", "notes", "normal", "challenge", "structured", "execution"])
def test_active_flag_cannot_bypass_existing_evidence_and_core_gates(pack, cid, gap):
    check = pack.checks[cid].model_copy(update={"judge": JudgeSpec(plugin="declared"),
                                              "required_observations": [ObsSpec(key="EXAMPLE_observed", type="bool")]})
    raw = _manual_evidence(check, dict.fromkeys(AXES, "PASS")).to_dict()
    raw["challenges"] = [{"step": s, "attempted": True} for s in check.challenge_steps]
    raw["observations"]["EXAMPLE_observed"] = {"value": True, "collected": True}
    pol = policy(pack.ref(), values={cid: given(cid, FLAGS[cid][1])})
    complete = judge_evidence(check, Evidence.model_validate(raw).seal(), pol, T0)
    assert all(v == "PASS" for v in complete.verdicts.model_dump().values())
    if gap == "attachments": raw["artifacts"] = []
    elif gap == "notes": raw["observations"]["declared_observation_notes"]["value"] = {}
    elif gap == "normal": raw["normal_control"]["succeeded"] = False
    elif gap == "challenge": raw["challenges"][0]["attempted"] = False
    elif gap == "structured": raw["observations"]["EXAMPLE_observed"] = {"value": None, "collected": False}
    else: raw["execution"]["status"] = "NOT_RUN"
    evd = Evidence.model_validate(raw).seal()
    result = judge_evidence(check, evd, pol, T0)
    assert all(v == "UNKNOWN" for v in result.verdicts.model_dump().values())
    negative = evd.model_copy(deep=True, update={"id": "EVD-EXAMPLE-FAIL"})
    negative.observations["declared_verdicts"].value["security"] = "FAIL"
    negative = negative.seal()
    assert judge_evidence(check, negative, pol, T0).verdicts.security == "FAIL"


@pytest.mark.parametrize("version,mode", [(1, "audit"), (2, "audit"), (3, "audit"), (3, "update")])
def test_manual_versions_report_side_history_and_idempotence(tmp_path, pack, version, mode):
    st = Store(tmp_path / "store")
    envs = [environment()]
    if mode == "update": envs.append(environment(id="ENV-CANDIDATE"))
    vals = {"OBS-04": given("OBS-04", False), "OBS-05": given("OBS-05", False),
            "OBS-07": given("OBS-07", True), "OBS-08": given("OBS-08", False)}
    pol = policy(pack.ref(), scope={"include": list(FLAGS)}, values=vals)
    st.put_many([*envs, pol]); put_pack(st, PACK_DIR)
    plan = select(pack, envs, pol, mode=mode); st.put(plan)
    blank = build_template(st, pack, plan)
    assert blank["template"] == "guard-manual-evidence/3"
    assert all("audit_evidence" not in e and "policy_evidence" not in e for e in blank["checks"])
    doc = fill(blank)
    doc["template"] = f"guard-manual-evidence/{version}"
    if version == 1: doc.pop("policy"); doc.pop("pack")
    if mode == "update":
        e = next(e for e in doc["checks"] if e["check_id"] == "OBS-08" and e["side"] == "candidate")
        e["verdicts"]["security"] = "FAIL"
    # Free text is still a declaration; report rendering must escape it.
    doc["checks"][0]["entered_by"] = "EXAMPLE <script>operator</script>"
    import_evidence(st, pack, write(tmp_path, doc), now=T0)
    for e in st.iter_kind("EVD"):
        assert e.execution.executor.version == "0.5.0" and e.data_origin == "DECLARED"
        assert "declared_audit_evidence" not in e.observations
    judge_plan(st, pack, plan, T0)
    before = snapshot(st)
    judge_plan(st, pack, plan, T0)
    assert snapshot(st) == before
    results = list(st.iter_kind("RES"))
    for result in results:
        expected = "PASS" if result.check_id in ("OBS-04", "OBS-07") else "UNKNOWN"
        if result.check_id == "OBS-08" and result.side == "candidate": expected = "FAIL"
        assert result.verdicts.security == expected
        assert result.judge.version == "0.13.0" and result.data_origin == "EXAMPLE"
    report = build_report(st, pack, plan, T0).to_dict()
    text = json.dumps(report, ensure_ascii=False)
    assert not report["official"] and "POLICY_CONTEXT:" in text
    assert "usage_allowed=false" in text and "replay_allowed=false" in text
    assert "POLICY_BRANCH_INACTIVE" in text and "인증되지 않은 자기 신고" in text
    assert all(any("not authenticated" in reason for reason in r.reasons) for r in results)
    html = render_html(report)
    assert "POLICY_CONTEXT:" in html and "usage_allowed=false" in html
    assert "<script>operator</script>" not in html
    # Rendering takes RPT, not all raw EVD notes or operator identifiers.
    untrusted = deepcopy(report)
    untrusted["limitations"].append("POLICY_CONTEXT: EXAMPLE <script>untrusted note</script>")
    escaped = render_html(untrusted)
    assert "<script>untrusted note</script>" not in escaped
    assert "&lt;script&gt;untrusted note&lt;/script&gt;" in escaped
    if mode == "update":
        row = next(r for r in report["by_check"] if r["check_id"] == "OBS-08")
        assert row["baseline"]["security"] == "UNKNOWN" and row["candidate"]["security"] == "FAIL"
        assert any("OBS-08/candidate:" in s and "POLICY_CONTEXT:" in s for s in report["limitations"])
    assert not st.verify()
