"""SUP-08 실행 판본 관측: 판본 지원표, 닫힌 요청 계약, 모듈 목록 스크립트, 분류와 DRAFT 팩 판정."""
from copy import deepcopy
import hashlib
import json
import subprocess
import sys

import pytest

from guard.adapters import engine_profiles
from guard.executors import request_field_probe as probe
from guard.executors.kubernetes_input_validation import _contract
from guard.judges.rule_table import RuleTableJudge
from guard.models import Evidence, Verdict, load_pack
from guard.runtime import kubernetes_http
from guard.runtime.kubernetes_http import INPUT_VALIDATION_PROFILE, ONLINE_INPUT_PROFILE, KubernetesBindingError
from tests.builders import ROOT, evidence
from tests.test_kubernetes_input_validation import CHAT, request, transport
from tools.build_code_selection_pack import derive

APPROVED = "a" * 40
UNAPPROVED = "b" * 40
IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
FIELD = "mm_processor_kwargs.code_revision"


def inv(*revisions, present=True):
    return {"schema": "guard-code-module-inventory/1", "present": present,
            "revisions": {rev: {"processing_probe.py": {"bytes": 1, "sha256": "c" * 64}} for rev in revisions}}


@pytest.mark.parametrize("version,feature,available", [
    ("0.7.0", "native_input", True), ("0.30.0", "native_input", True), ("0.31.0", "native_input", True),
    ("0.32.0", "native_input", False), ("0.30.0", "online_input", False), ("0.31.0", "online_input", True)])
def test_catalog_adds_only_input_validation_for_older_releases(version, feature, available):
    assert engine_profiles.supports("vllm", version, feature) is available


def test_transport_release_gate_reads_the_catalog():
    engine_profiles.require_transport("vllm", "0.30.0", INPUT_VALIDATION_PROFILE)
    with pytest.raises(ValueError):
        engine_profiles.require_transport("vllm", "0.30.0", ONLINE_INPUT_PROFILE)
    source = (ROOT / "src/guard/runtime/kubernetes_http.py").read_text(encoding="utf-8")
    assert 'env.engine.version != "0.31.0"' not in source


@pytest.mark.parametrize("before,after,state,reason", [
    (inv(APPROVED), inv(APPROVED, UNAPPROVED), "consumed", None),
    (inv(), inv(APPROVED), "ignored", None),
    (inv(APPROVED), inv(APPROVED), "ignored", None),
    (inv(UNAPPROVED), inv(UNAPPROVED, APPROVED), "accepted_unverified", "UNAPPROVED_REVISION_PRESENT_BEFORE_CHALLENGE"),
    (inv(), inv(), "accepted_unverified", "NO_APPROVED_MODULE_OBSERVED"),
    (inv(present=False), inv(APPROVED), "accepted_unverified", None),
    (None, inv(APPROVED), "accepted_unverified", None)])
def test_module_classification(before, after, state, reason):
    got, detail = probe.classify_modules("accepted_unverified", before, after, [APPROVED])
    assert got == state
    if state == "consumed":
        assert detail["selected_revisions"] == [UNAPPROVED] and UNAPPROVED in detail["module_files"]
    if reason:
        assert detail == {"reason": reason}


def test_rejection_and_missing_policy_are_never_refined():
    assert probe.classify_modules("rejected", inv(), inv(UNAPPROVED), [APPROVED]) == ("rejected", None)
    assert probe.classify_modules("accepted_unverified", inv(), inv(UNAPPROVED), None) == ("accepted_unverified", None)


def test_request_helpers_build_nested_fields_and_image_control():
    assert probe.nested_fields([FIELD], "x") == {"mm_processor_kwargs": {"code_revision": "x"}}
    with pytest.raises(ValueError):
        probe.nested_fields(["a.b", "a.c"], "x")
    body = probe.control_body(CHAT, {"control_image": IMAGE})
    assert body["messages"][-1]["content"][1] == {"type": "image_url", "image_url": {"url": IMAGE}}
    assert CHAT["messages"][-1]["content"] == "hello" and probe.control_body(CHAT, {}) is CHAT
    for bad in ({"control_image": "https://example.invalid/x.png"}, {"unapproved_revision": "../x"},
                {"code_module_root": "/x"}):
        with pytest.raises(ValueError):
            probe.validate_configuration(load_pack(ROOT / "packs/PACK-BASELINE").checks["SUP-08"], bad)


def test_policy_requires_commit_shaped_approvals():
    check = load_pack(ROOT / "packs/PACK-BASELINE").checks["SUP-08"]
    base = {"code_selection_fields": [FIELD], "code_selection_allowed": False, "rejection_codes": ["400"]}
    probe.validate_policy(check, {**base, "approved_code_revisions": [APPROVED]})
    for bad in ([], ["main"], [APPROVED, APPROVED]):
        with pytest.raises(ValueError):
            probe.validate_policy(check, {**base, "approved_code_revisions": bad})


def test_kubernetes_contract_carries_nested_fixture_and_image(tmp_path):
    check = load_pack(ROOT / "packs/PACK-BASELINE").checks["SUP-08"]
    cfg = {"control_image": IMAGE, "unapproved_revision": "refs/pr/1"}
    values = {"code_selection_fields": [FIELD], "code_selection_allowed": False, "rejection_codes": ["400"]}
    contract = _contract(check, values, cfg, "test-model")
    assert contract["fields"] == {"mm_processor_kwargs": {"code_revision": "refs/pr/1"}}
    assert contract["control"]["messages"][-1]["content"][1]["image_url"]["url"] == IMAGE
    t = transport(tmp_path)
    contract["control"]["model"] = t.api_model
    t.declare_input_contract(contract)
    chat = "func|POST_/v1/chat/completions"
    control = contract["control"]
    t._input_validation_request(request(control, rid=chat))
    t._input_validation_request(request({**control, **contract["fields"]}, rid=chat))
    with pytest.raises(KubernetesBindingError):
        t._input_validation_request(request({**control, "mm_processor_kwargs": {"code_revision": "main"}}, rid=chat))


def test_module_listing_is_profile_gated_and_bounded(tmp_path):
    t = transport(tmp_path)
    for root, repo in (("relative", "org/name"), ("/x/../y", "org/name"), ("/x", "org/../name"), ("/x", "a/b/c")):
        with pytest.raises(KubernetesBindingError):
            t.engine_code_modules(root, repo)
    t.profile = ONLINE_INPUT_PROFILE
    with pytest.raises(KubernetesBindingError):
        t.engine_code_modules("/x", "org/name")


def test_module_listing_script_reads_names_and_hashes_only(tmp_path):
    rev = tmp_path / "org/name" / UNAPPROVED
    rev.mkdir(parents=True)
    (rev / "processing_probe.py").write_bytes(b"x = 1\n")
    (rev / "notes.txt").write_bytes(b"skip")
    (tmp_path / "org/name/link").symlink_to(rev)
    out = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", kubernetes_http._MODULE_INVENTORY_SCRIPT,
                          str(tmp_path), "org/name"], capture_output=True, check=True)
    document = json.loads(out.stdout)
    assert document["present"] is True and list(document["revisions"]) == [UNAPPROVED]
    assert document["revisions"][UNAPPROVED] == {"processing_probe.py": {
        "bytes": 6, "sha256": hashlib.sha256(b"x = 1\n").hexdigest()}}
    assert not (rev / "__pycache__").exists()


@pytest.fixture(scope="module")
def code_pack(tmp_path_factory):
    out = tmp_path_factory.mktemp("pack") / "PACK-CODE-SELECTION"
    summary = derive(ROOT / "packs/PACK-BASELINE", out)
    assert summary["unchanged_definition_bytes"] == 76 and summary["changed"] == ["checks/SUP-08.yaml"]
    return load_pack(out)


def _judge(check, trials, *, normal=True):
    resolved = [t["field"] for t in trials if t["state"] in {"rejected", "ignored", "consumed"}]
    violations = [{"field": t["field"], "selected_revisions": [UNAPPROVED]} for t in trials if t["state"] == "consumed"]
    obs = {"code_selection_trials": trials, "code_selection_forbidden": True, "code_selection_fields_planned": [FIELD],
           "code_selection_rejected_fields": [t["field"] for t in trials if t["state"] == "rejected"],
           "code_selection_ignored_fields": [t["field"] for t in trials if t["state"] == "ignored"],
           "code_selection_resolved_fields": resolved,
           "code_selection_unverified_fields": [t["field"] for t in trials if t["field"] not in resolved],
           "code_selection_confirmed_violations": violations, "code_selection_final_code_verified": len(resolved) == len(trials),
           "functional_probes_planned": ["before", "after-0"], "functional_probe_results": {"before": "pass", "after-0": "pass"}}
    value = evidence(check_id=check.id, observations={k: {"value": v, "collected": True} for k, v in obs.items()},
                     normal={"attempted": True, "succeeded": normal, "detail": "EXAMPLE"}).to_dict()
    value["challenges"] = [{"step": step, "attempted": True} for step in check.challenge_steps]
    stored = Evidence.model_validate(value).seal()
    return RuleTableJudge().judge(check, stored, check.judge.params, {"approved_code_revisions": [APPROVED]})


@pytest.mark.parametrize("state,verdict", [
    ("consumed", Verdict.FAIL), ("ignored", Verdict.PASS), ("rejected", Verdict.PASS),
    ("accepted_unverified", Verdict.UNKNOWN)])
def test_derived_pack_judges_observed_module_selection(code_pack, state, verdict):
    check = code_pack.checks["SUP-08"]
    assert check.revision == 5 and code_pack.manifest.pack_id == "PACK-CODE-SELECTION"
    out = _judge(check, [{"field": FIELD, "outcome": "200" if state != "rejected" else "400", "state": state}])
    assert out.verdicts["security"] == verdict
    if verdict is Verdict.FAIL:
        assert any("code_selection_confirmed_violations" in reason and UNAPPROVED in reason for reason in out.reasons)


def test_baseline_pack_is_untouched(code_pack):
    base = load_pack(ROOT / "packs/PACK-BASELINE")
    assert base.checks["SUP-08"].revision == 4 and base.manifest.revision == 33
    assert {k: deepcopy(v.model_dump()) for k, v in base.checks.items() if k != "SUP-08"} == \
           {k: deepcopy(v.model_dump()) for k, v in code_pack.checks.items() if k != "SUP-08"}


class FakeEngine:
    """EXAMPLE engine: HTTP answers plus a module listing that changes only when the challenge is consumed."""

    def __init__(self, *, consumes, rejects=False):
        self.consumes, self.rejects, self.revisions, self.bodies = consumes, rejects, {APPROVED}, []

    def __call__(self, net, requests, timeout):
        from tests.builders import completion_response
        out = []
        for r in requests:
            self.bodies.append(deepcopy(r["body"]))
            challenged = "mm_processor_kwargs" in r["body"]
            if challenged and self.rejects:
                out.append({**completion_response(r), "outcome": 400, "body": ""})
                continue
            if challenged and self.consumes:
                self.revisions.add(UNAPPROVED)
            out.append(completion_response(r))
        return out

    def engine_code_modules(self, root, repository):
        assert (root, repository) == ("/cache/modules/transformers_modules", "org/probe")
        return inv(*sorted(self.revisions))


def _pipeline(tmp_path, pack, engine):
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.core.run import RunConfig, run_plan
    from guard.core.select import select
    from guard.executors import registry
    from guard.executors.input_validation import InputValidationExecutor
    from guard.store import Store
    from guard.store.packs import put_pack
    from tests.builders import environment, policy
    from tests.test_executor_extensions import target

    _, path = target(tmp_path)
    store = Store(tmp_path / "store")
    env = environment(model={"id": "test"})
    ref = put_pack(store, pack.path)
    values = {"code_selection_fields": [FIELD], "code_selection_allowed": False, "rejection_codes": ["400"],
              "approved_code_revisions": [APPROVED]}
    pol = policy(ref, scope={"include": ["SUP-08"]}, values={"SUP-08": values})
    store.put(env); store.put(pol)
    plan = select(pack, [env], pol); store.put(plan)
    cfg = {"target": str(path), "control_image": IMAGE, "code_module_root": "/cache/modules/transformers_modules",
           "code_module_repository": "org/probe", "unapproved_revision": "refs/pr/1"}
    registry.EXECUTORS["input_validation:sim"] = lambda _: InputValidationExecutor(cfg, container_probe=engine)
    try:
        evd, = run_plan(store, pack, plan, RunConfig(executors={"input_validation": "sim"}, adapter="vllm"))
    finally:
        registry.EXECUTORS.pop("input_validation:sim")
    result, = judge_plan(store, pack, plan, env.created_at)
    report = build_report(store, pack, plan, env.created_at)
    return evd, result, report


@pytest.mark.parametrize("engine,security", [
    (dict(consumes=True), "FAIL"), (dict(consumes=False), "PASS"), (dict(consumes=False, rejects=True), "PASS")])
def test_pipeline_reports_consumed_revision_as_fail(tmp_path, code_pack, engine, security):
    fake = FakeEngine(**engine)
    evd, result, report = _pipeline(tmp_path, code_pack, fake)
    assert result.verdicts.security.value == security and result.verdicts.functional.value == "PASS"
    challenge = [b for b in fake.bodies if "mm_processor_kwargs" in b]
    assert challenge == [{**challenge[0], "mm_processor_kwargs": {"code_revision": "refs/pr/1"}}]
    assert all(b["messages"][-1]["content"][1]["image_url"]["url"] == IMAGE for b in fake.bodies)
    violations = evd.observations["code_selection_confirmed_violations"].value
    if security == "FAIL":
        assert violations[0]["selected_revisions"] == [UNAPPROVED] and violations[0]["field"] == FIELD
        assert any(UNAPPROVED in reason for reason in result.reasons)
    else:
        assert violations == []
    assert "SUP-08" in [row.check_id for row in report.by_check]
