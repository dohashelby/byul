"""SUP-08 실행 판본 관측: 판본 지원표, 닫힌 요청 계약, 모듈 목록 스크립트, 분류와 DRAFT 팩 판정."""
from copy import deepcopy
import hashlib
import json
import os
import subprocess
import sys

import pytest

from guard.adapters import engine_profiles
from guard.executors import request_field_probe as probe
from guard.executors.registry import resolve_executor
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


def inv(*revisions, present=True, complete=True, marker_revisions=None):
    marked = revisions if marker_revisions is None else marker_revisions
    return {"schema": "guard-code-module-inventory/2", "present": present, "complete": complete,
            "markers_present": True,
            "markers": {rev: {"schema": "guard-code-import-marker/1", "revision": rev,
                              "module_sha256": "c" * 64} for rev in marked},
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


def test_only_input_validation_kubernetes_executor_is_exposed_on_old_releases():
    for version in ('0.7.0', '0.30.0'):
        assert resolve_executor('input_validation', 'kubernetes', engine='vllm', version=version) is not None
        assert resolve_executor('sampling_validation', 'kubernetes', engine='vllm', version=version) is None
    assert resolve_executor('input_validation', 'kubernetes', engine='vllm', version='0.31.0') is not None


@pytest.mark.parametrize("before,after,state,reason", [
    (inv(APPROVED), inv(APPROVED, UNAPPROVED), "consumed", None),
    (inv(), inv(APPROVED), "accepted_unverified", "APPROVED_EXECUTION_MARKER_MISSING"),
    (inv(APPROVED), inv(APPROVED), "ignored", None),
    (inv(APPROVED, UNAPPROVED), inv(APPROVED, UNAPPROVED), "accepted_unverified", "UNAPPROVED_REVISION_PRESENT_BEFORE_CHALLENGE"),
    (inv(), inv(), "accepted_unverified", "APPROVED_EXECUTION_MARKER_MISSING"),
    (inv(APPROVED), inv(APPROVED, UNAPPROVED, marker_revisions=[APPROVED]),
     "accepted_unverified", "UNAPPROVED_MODULE_CACHED_WITHOUT_EXECUTION_MARKER"),
    (inv(APPROVED, complete=False), inv(APPROVED), "accepted_unverified", "CODE_EXECUTION_OBSERVATION_UNAVAILABLE"),
    (inv(present=False), inv(APPROVED), "accepted_unverified", "CODE_EXECUTION_OBSERVATION_UNAVAILABLE"),
    (None, inv(APPROVED), "accepted_unverified", "CODE_EXECUTION_OBSERVATION_UNAVAILABLE")])
def test_module_classification(before, after, state, reason):
    got, detail = probe.classify_modules("accepted_unverified", before, after, [APPROVED])
    assert got == state
    if state == "consumed":
        assert detail["selected_revisions"] == [UNAPPROVED] and UNAPPROVED in detail["module_files"]
    if reason:
            assert detail["reason"] == reason


def test_rejection_and_missing_policy_are_never_refined():
    state, detail = probe.classify_modules("rejected", inv(APPROVED), inv(APPROVED, UNAPPROVED), [APPROVED])
    assert state == "consumed" and detail["selected_revisions"] == [UNAPPROVED]
    assert probe.classify_modules("rejected", inv(APPROVED), inv(APPROVED), [APPROVED])[0] == "rejected"
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
    if os.name != "nt":
        (tmp_path / "org/name/link").symlink_to(rev, target_is_directory=True)
    marker_root = tmp_path / "markers"
    marker_root.mkdir()
    marker = {"schema": "guard-code-import-marker/1", "revision": UNAPPROVED,
              "module_sha256": hashlib.sha256(b"x = 1\n").hexdigest()}
    (marker_root / f"{UNAPPROVED}.json").write_text(json.dumps(marker))
    out = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", kubernetes_http._MODULE_INVENTORY_SCRIPT,
                          str(tmp_path), "org/name", str(marker_root)], capture_output=True, check=True)
    document = json.loads(out.stdout)
    assert document["present"] is True and document["complete"] is True and list(document["revisions"]) == [UNAPPROVED]
    assert document["revisions"][UNAPPROVED] == {"processing_probe.py": {
        "bytes": 6, "sha256": hashlib.sha256(b"x = 1\n").hexdigest()}}
    assert not (rev / "__pycache__").exists()
    assert document["markers"][UNAPPROVED]["module_sha256"] == marker["module_sha256"]


def test_module_listing_accepts_transformers_encoded_repository_path(tmp_path):
    encoded = tmp_path / "local_hyphen_byul/sup08_hyphen_phi3v_hyphen_processor" / UNAPPROVED
    encoded.mkdir(parents=True)
    (encoded / "processing_probe.py").write_bytes(b"x = 1\n")
    argv = [sys.executable, "-I", "-S", "-B", "-c", kubernetes_http._MODULE_INVENTORY_SCRIPT,
            str(tmp_path), "local-byul/sup08-phi3v-processor", "-"]
    document = json.loads(subprocess.run(argv, capture_output=True, check=True).stdout)
    assert document["present"] is True and document["complete"] is True
    assert document["revisions"][UNAPPROVED]["processing_probe.py"]["sha256"] == hashlib.sha256(b"x = 1\n").hexdigest()
    plain = tmp_path / "local-byul/sup08-phi3v-processor" / UNAPPROVED
    plain.mkdir(parents=True)
    ambiguous = json.loads(subprocess.run(argv, capture_output=True, check=True).stdout)
    assert ambiguous["present"] is True and ambiguous["complete"] is False


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
    assert base.checks["SUP-08"].revision == 5 and base.manifest.revision == 34
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
            if challenged and self.consumes:
                self.revisions.add(UNAPPROVED)
            if challenged and self.rejects:
                out.append({**completion_response(r), "outcome": 400, "body": ""})
                continue
            out.append(completion_response(r))
        return out

    def engine_code_modules(self, root, repository, marker_root):
        assert (root, repository, marker_root) == ("/cache/modules/transformers_modules", "org/probe", "/cache/hf/byul-code-markers/a-run-1")
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
           "code_module_repository": "org/probe", "code_marker_root": "/cache/hf/byul-code-markers/a-run-1",
           "unapproved_revision": "refs/pr/1"}
    registry.EXECUTORS["input_validation:sim"] = lambda _: InputValidationExecutor(cfg, container_probe=engine)
    try:
        evd, = run_plan(store, pack, plan, RunConfig(executors={"input_validation": "sim"}, adapter="vllm"))
    finally:
        registry.EXECUTORS.pop("input_validation:sim")
    result, = judge_plan(store, pack, plan, env.created_at)
    report = build_report(store, pack, plan, env.created_at)
    return evd, result, report


@pytest.mark.parametrize("engine,security", [
    (dict(consumes=True), "FAIL"), (dict(consumes=True, rejects=True), "FAIL"),
    (dict(consumes=False), "PASS"), (dict(consumes=False, rejects=True), "PASS")])
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


def test_aws_generations_toggle_trust_remote_code():
    from tools import gpu_a_aws_lab as lab
    assert lab.GENERATIONS["v2-code-selection-trust"] == {"sensor": False, "args": ["--trust-remote-code"], "env": {}}
    assert lab.GENERATIONS["v2-code-selection-notrust"] == {"sensor": False, "args": [], "env": {}}


def _draft(block=True):
    draft = {"pack": {"id": "PACK-CODE-SELECTION", "revision": 1},
             "values": {"SUP-08": {"code_selection_fields": [FIELD], "code_selection_allowed": False,
                                    "rejection_codes": ["400", "422"], "approved_code_revisions": [APPROVED]}}}
    if block:
        draft["code_selection_observation"] = {"code_module_root": "/cache/hf/modules/transformers_modules",
            "code_module_repository": "org/probe", "code_marker_root": "/cache/hf/byul-code-markers/a-run-1",
            "unapproved_revision": "refs/pr/1", "control_image": IMAGE}
    return draft


def test_run_inputs_wire_module_observation_only_when_the_draft_declares_it(tmp_path):
    from tools import gpu_a_run_inputs as inputs
    target = {"kubernetes": {"context": "byul-aws-lab"},
              "code_marker_root": "/cache/hf/byul-code-markers/a-run-1"}
    policy, config = inputs.http_inputs("SUP-08", target, _draft(block=True), root=tmp_path)
    assert policy["pack"]["id"] == "PACK-CODE-SELECTION"
    assert policy["values"]["SUP-08"]["approved_code_revisions"] == [APPROVED]
    assert "vocab_source" not in config
    for line in ("code_module_root = '/cache/hf/modules/transformers_modules'", "code_module_repository = 'org/probe'",
                 "unapproved_revision = 'refs/pr/1'", "control_image = '" + IMAGE + "'",
                 "input_validation = 'kubernetes'"):
        assert line in config
    _, plain = inputs.http_inputs("SUP-08", target, _draft(block=False), root=tmp_path)
    assert "code_module_root" not in plain
    with pytest.raises(ValueError, match="RUN_INPUTS_CODE_MARKER_ROOT_MISMATCH"):
        inputs.http_inputs("SUP-08", {"kubernetes": {}}, _draft(block=True), root=tmp_path)


def test_run_inputs_reject_a_partial_observation_block(tmp_path):
    from tools import gpu_a_run_inputs as inputs
    draft = _draft(block=True); del draft["code_selection_observation"]["control_image"]
    with pytest.raises(ValueError, match="RUN_INPUTS_CODE_OBSERVATION_INVALID"):
        inputs.http_inputs("SUP-08", {"kubernetes": {}}, draft, root=tmp_path)


def test_poc_draft_file_has_markers_and_nested_field():
    import yaml
    draft = yaml.safe_load((ROOT / "docs/gpu-a/policy-code-selection-draft.yaml").read_text(encoding="utf-8"))
    assert draft["pack"]["id"] == "PACK-CODE-SELECTION"
    assert draft["values"]["SUP-08"]["code_selection_fields"] == [FIELD]
    block = draft["code_selection_observation"]
    assert block["code_module_root"] == "/cache/hf/modules/transformers_modules"
    assert set(block) == set(inputs_keys())
    for marker in ("REPLACE_WITH_APPROVED_PROCESSOR_COMMIT_SHA1", "REPLACE_WITH_TEST_MODEL_REPOSITORY_ID",
                   "REPLACE_WITH_UNAPPROVED_REVISION", "REPLACE_WITH_SMALL_IMAGE_DATA_URI", "REPLACE_WITH_RUN_MARKER_ROOT"):
        assert marker in (ROOT / "docs/gpu-a/policy-code-selection-draft.yaml").read_text(encoding="utf-8")


def inputs_keys():
    from tools import gpu_a_run_inputs as inputs
    return inputs._CODE_OBSERVATION_KEYS


@pytest.mark.parametrize("version,available", [("0.7.0", False), ("0.30.0", False), ("0.31.0", True)])
def test_request_input_checks_stay_on_reviewed_release(version, available):
    assert engine_profiles.supports("vllm", version, "request_input_checks") is available


@pytest.mark.parametrize("check_id,values", [
    ("REQ-09", {"max_input_tokens": 127, "rejection_codes": ["400"], "allowed_endpoints": ["POST /v1/completions"],
                "allowed_input_formats": ["token_ids"]}),
])
@pytest.mark.parametrize("version", ["0.7.0", "0.30.0"])
def test_old_release_row_does_not_open_other_input_checks(tmp_path, check_id, values, version):
    from guard.collectors.file import load_environment
    from guard.executors.kubernetes_input_validation import KubernetesInputValidationExecutor
    from tests.builders import baseline_pack
    from tests.test_gpu_a_run_inputs import build

    def no_kubectl(*args, **kwargs):
        raise AssertionError("gate must reject before kubectl")

    build(tmp_path, "v2-default")
    env = load_environment(tmp_path / "env.json")
    env = env.model_copy(update={"engine": env.engine.model_copy(update={"version": version})})
    executor = KubernetesInputValidationExecutor({"target": str(tmp_path / "target.json"), "vocab_size": 151936,
        "vocab_source": "EXAMPLE"}, runner=no_kubectl)
    with pytest.raises(ValueError, match="KUBERNETES_INPUT_CONFIGURATION_INVALID"):
        executor.run(baseline_pack().checks[check_id], env, values, object(), tmp_path / "work")
    # Control: the same policy and target on the reviewed release passes the gate and yields a draft.
    reviewed = env.model_copy(update={"engine": env.engine.model_copy(update={"version": "0.31.0"})})
    assert executor.run(baseline_pack().checks[check_id], reviewed, values, object(), tmp_path / "work") is not None
