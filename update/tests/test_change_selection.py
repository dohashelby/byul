"""선정·이월의 입력 비교와 보수적 경계. 네트워크·엔진 없이 계약을 검증한다."""

from copy import deepcopy
from dataclasses import replace

import pytest

from guard.core.change_selection import input_diff, selection_triggers, source_image_relation
from guard.models.objects import Change
from guard.models import DataOrigin, PolicyStatus, Verdict
from tests.builders import T0, baseline_pack, environment, policy


def _change(categories, *, unmapped=False):
    return Change(id="CHG-INPUT", created_at=T0, data_origin="DECLARED",
                  baseline={"version": "1.0.0", "source_commit": "a" * 40},
                  candidate={"version": "1.0.1", "source_commit": "b" * 40},
                  extractor={"name": "manual", "version": "1"},
                  facts=[{"id": f"F{i}", "category": category, "target": f"file{i}",
                          "certainty": "PARTIAL", "unmapped": unmapped}
                         for i, category in enumerate(categories)]).seal()


def test_policy_exceptions_status_are_not_retest_triggers():
    pack = baseline_pack()
    old = policy(pack.ref())
    new = old.model_copy(update={"exceptions": [], "status": PolicyStatus.CONFIRMED, "confirmed_by": "EXAMPLE",
                                 "confirmed_at": T0}).seal()
    diff = input_diff(environment(), environment(), old, new, list(pack.checks))
    assert diff.policy_values == []
    assert diff.scope_added == []
    assert diff.policy_metadata_changed


def test_policy_value_scope_and_environment_changes_are_separate():
    pack = baseline_pack()
    old = policy(pack.ref(), scope={"include": ["REQ-01"], "exclude": []})
    new = policy(pack.ref(), values={"REQ-01": {"allowed_callers": ["new"]}},
                 scope={"include": ["REQ-01", "PLT-04"], "exclude": []})
    env1 = environment()
    env2 = environment({"gateway": {"value": True, "source": "cfg", "origin": "DECLARED"}},
                       runtime={"nodes": 2})
    diff = input_diff(env1, env2, old, new, list(pack.checks))
    assert diff.policy_values == ["REQ-01"]
    assert diff.scope_added == ["PLT-04"]
    assert diff.facts == ["gateway"]
    assert diff.runtime == ["runtime.nodes"]


def test_patch_always_diff_and_input_are_a_union():
    pack = baseline_pack()
    pack.change_selection = {"patch_always": ["REQ-09"], "engine_categories": {"AUTH": ["REQ-01"]},
                             "model_categories": {}, "model_common": [], "fact_triggers": {}}
    old = policy(pack.ref())
    new = policy(pack.ref(), values={**deepcopy(old.values), "PLT-04": {"x": True}})
    diff = input_diff(environment(), environment(), old, new, list(pack.checks))
    triggers, full = selection_triggers(pack, "ENGINE_PATCH", _change(["AUTH"]), diff)
    assert not full
    assert set(triggers) == {"REQ-09", "REQ-01", "PLT-04"}
    assert triggers["REQ-01"][0].refs == ["F0"]


def test_unmapped_patch_selects_whole_category_table():
    pack = baseline_pack()
    pack.change_selection = {"patch_always": ["REQ-09"],
                             "engine_categories": {"AUTH": ["REQ-01"], "CACHE": ["REQ-16"]},
                             "model_categories": {}, "model_common": [], "fact_triggers": {}}
    pol = policy(pack.ref())
    diff = input_diff(environment(), environment(), pol, pol, list(pack.checks))
    triggers, full = selection_triggers(pack, "ENGINE_PATCH", _change(["OTHER"], unmapped=True), diff)
    assert not full
    assert set(triggers) == {"REQ-09", "REQ-01", "REQ-16"}


def test_unmapped_environment_fact_forces_full_scope():
    pack = baseline_pack()
    pack.change_selection = {"patch_always": [], "engine_categories": {}, "model_categories": {},
                             "model_common": [], "fact_triggers": {}}
    pol = policy(pack.ref())
    env = environment({"r10_features": {"value": True, "origin": "DECLARED", "source": "cfg"}})
    diff = input_diff(environment(), env, pol, pol, list(pack.checks))
    _, full = selection_triggers(pack, "INPUT", None, diff)
    assert full


def test_model_read_subset_is_reference_only():
    pack = baseline_pack()
    pol = policy(pack.ref())
    before = environment(model={"id": "example/model", "revision": "fixed", "inventory_sha256": "a" * 64})
    after = environment(model={"id": "example/model", "revision": "fixed", "inventory_sha256": "a" * 64,
                               "files_read_by_engine": {"file_count": 3, "inventory_sha256": "b" * 64}})
    assert not input_diff(before, after, pol, pol).model_changed


@pytest.mark.parametrize("category", ["MODEL_TEMPLATE", "MODEL_WEIGHTS", "MODEL_CODE", "MODEL_TOKENIZER", "MODEL_ARCH"])
def test_each_model_category_selects_its_mapping_and_common(category):
    pack = baseline_pack()
    categories = {name: [cid] for name, cid in zip(
        ["MODEL_TEMPLATE", "MODEL_WEIGHTS", "MODEL_CODE", "MODEL_TOKENIZER", "MODEL_ARCH"],
        ["REQ-07", "SUP-03", "SUP-08", "REQ-08", "REQ-18"])}
    pack.change_selection = {"patch_always": [], "engine_categories": {}, "model_categories": categories,
                             "model_common": ["REQ-05"], "fact_triggers": {}}
    before = environment(model={"id": "example/model", "revision": "a"})
    after = environment(model={"id": "example/model", "revision": "b"})
    pol = policy(pack.ref())
    diff = input_diff(before, after, pol, pol)
    triggers, full = selection_triggers(pack, "MODEL", _change([category]), diff)
    assert set(triggers) == {"REQ-05", *categories[category]}
    assert not full


def test_model_id_change_does_not_trust_a_narrow_manifest_category():
    pack = baseline_pack()
    pack.change_selection = {"patch_always": [], "engine_categories": {},
                             "model_categories": {"MODEL_TEMPLATE": ["REQ-07"], "MODEL_CODE": ["SUP-08"]},
                             "model_common": ["REQ-05"], "fact_triggers": {}}
    pol = policy(pack.ref())
    diff = input_diff(environment(model={"id": "old"}), environment(model={"id": "new"}), pol, pol)
    triggers, full = selection_triggers(pack, "MODEL", _change(["MODEL_TEMPLATE"]), diff)
    assert set(triggers) == {"REQ-07", "SUP-08", "REQ-05"}
    assert not full


@pytest.mark.parametrize("field,value,expected", [
    (None, None, "CONFIRMED"),
    ("source_commit_status", "MISMATCH", "MISMATCH"),
    ("source_commit_status", "UNVERIFIED", "UNVERIFIED"),
    ("source_commit", None, "UNVERIFIED"),
    ("source_commit", "b" * 12, "UNVERIFIED"),
    ("source_commit", "c" * 40, "MISMATCH"),
    ("version", "1.0.2", "MISMATCH"),
    ("version", None, "UNVERIFIED"),
    ("image_digest", None, "UNVERIFIED"),
])
def test_source_relation_used_by_selection_and_report_is_same_metadata_check(field, value, expected):
    baseline = environment(engine={"name": "engine-x", "version": "1.0.0", "source_commit": "a" * 40,
                                   "image_digest": "image@sha256:" + "a" * 64})
    candidate = environment(engine={"name": "engine-x", "version": "1.0.1", "source_commit": "b" * 40,
                                    "image_digest": "image@sha256:" + "b" * 64})
    if field:
        candidate = candidate.model_copy(update={"engine": candidate.engine.model_copy(update={field: value})}).seal()
    assert source_image_relation(_change(["AUTH"]), [baseline, candidate]) == expected


def test_source_relation_does_not_promote_missing_or_model_analysis():
    assert source_image_relation(None, []) == "UNVERIFIED"
    change = _change(["MODEL_TEMPLATE"])
    change = change.model_copy(update={"extractor": change.extractor.model_copy(update={"name": "model_manifest"})}).seal()
    assert source_image_relation(change, [environment(), environment()]) == "UNVERIFIED"


@pytest.fixture
def reuse_case(tmp_path, monkeypatch):
    """진짜 append-only Store에 고정 입력/DECLARED 증거를 저장한 단위 계약 fixture."""
    from guard.core import change_selection as module
    from guard.core.judge import judge_evidence
    from guard.core.select import select
    from guard.executors.reuse import ExecutionPreview
    from guard.judges.base import JudgeOutput
    from guard.models import Evidence, Side
    from guard.models.objects import ExecutorInfo, PluginInfo
    from guard.store import Store

    class CurrentJudge:
        name = "reuse-contract"
        version = "2"

        @staticmethod
        def judge(check, evidence, params, policy_values):
            return JudgeOutput(verdicts={"security": "PASS", "functional": "PASS", "performance": "UNKNOWN"})

    monkeypatch.setitem(module.judges.JUDGES, "reuse-contract", CurrentJudge())
    pack = baseline_pack()
    check = pack.checks["PLT-04"]
    check.applies_when.always = True
    check.applies_when.all = []
    check.policy_params = []
    check.judge = check.judge.model_copy(update={"plugin": "reuse-contract", "params": {}})
    pack.change_selection = {"patch_always": ["REQ-09"], "engine_categories": {"AUTH": ["REQ-01"]},
                             "model_categories": {"MODEL_TEMPLATE": ["REQ-07"]}, "model_common": ["REQ-05"],
                             "fact_triggers": {}}
    env = environment(origin="DECLARED", engine={"name": "engine-x", "version": "1.0.0",
                      "source_commit": "a" * 40, "image_digest": "image@sha256:" + "a" * 64})
    pol = policy(pack.ref(), data_origin="DECLARED")
    store = Store(tmp_path / "store")
    store.put_many([env, pol])
    previous = select(pack, [env], pol)
    store.put(previous)
    preview = ExecutionPreview(ExecutorInfo(name="fixed-test", version="1", config_sha256="1" * 64),
                               PluginInfo(name="test-adapter", version="1"), "2" * 64, "3" * 64)
    evd = Evidence(id="EVD-REUSE", created_at=T0, data_origin="DECLARED", refs=[previous.ref(), env.ref()],
                   plan=previous.ref(), check_id=check.id, side=Side.single, environment=env.ref(),
                   execution={"status": "COMPLETED", "executor": preview.executor, "adapter": preview.adapter,
                              "started_at": T0, "finished_at": T0},
                   normal_control={"attempted": True, "succeeded": True},
                   challenges=[{"step": step, "attempted": True} for step in check.challenge_steps],
                   observations={obs.key: {"value": True, "collected": True} for obs in check.required_observations},
                   comparability={"fixture_sha256": preview.fixture_sha256, "config_sha256": preview.config_sha256}).seal()
    store.put(evd)
    result = judge_evidence(check, evd, pol, T0, plan=previous)
    store.put(result)
    monkeypatch.setattr(module, "get_pack", lambda *args: pack)
    monkeypatch.setattr(module, "preview_inputs", lambda *args: preview)
    return {"module": module, "store": store, "pack": pack, "env": env, "policy": pol,
            "previous": previous, "evidence": evd, "result": result, "preview": preview}


def _context(case, **kw):
    return case["module"].build_context(case["store"], case["previous"].ref(), case["pack"],
                                        kw.pop("envs", [case["env"]]), case["policy"], {}, as_of=T0, **kw)


def test_valid_pass_uses_exact_refs_without_creating_objects(reuse_case):
    case = reuse_case
    before = list(case["store"].iter_kind("RES"))
    context = _context(case)
    carried = context.carried_over["PLT-04"]
    assert carried.result == case["result"].ref()
    assert carried.evidence == case["evidence"].ref()
    assert list(case["store"].iter_kind("RES")) == before
    assert list(case["store"].iter_kind("EVD")) == [case["evidence"]]


def test_no_previous_plan_is_full_scope_without_store_writes(reuse_case):
    case = reuse_case
    context = case["module"].build_context(case["store"], None, case["pack"], [case["env"]], case["policy"], {})
    assert context.full_scope and context.change_type == "INITIAL"
    assert not context.carried_over
    assert len(list(case["store"].iter_kind("RES"))) == 1


@pytest.mark.parametrize("axis,value", [("security", "FAIL"), ("security", "UNKNOWN")])
def test_nonpass_result_is_reselected(reuse_case, axis, value):
    case = reuse_case
    verdicts = case["result"].verdicts.model_copy(update={axis: Verdict(value)})
    result = case["result"].model_copy(update={"revision": 2, "verdicts": verdicts}).seal()
    case["store"].put(result)
    assert "PLT-04" not in _context(case).carried_over


@pytest.mark.parametrize("part", ["executor_name", "executor_version", "executor_config", "adapter_version",
                                  "fixture", "comparison_config", "unknown_fixture", "unknown_config", "dynamic"])
def test_procedure_or_fixture_difference_is_never_assumed_equal(reuse_case, monkeypatch, part):
    case = reuse_case
    preview = case["preview"]
    if part == "dynamic":
        preview = None
    elif part.startswith("executor"):
        field = {"executor_name": "name", "executor_version": "version", "executor_config": "config_sha256"}[part]
        preview = replace(preview, executor=preview.executor.model_copy(update={field: "changed"}))
    elif part == "adapter_version":
        preview = replace(preview, adapter=preview.adapter.model_copy(update={"version": "changed"}))
    elif part in {"fixture", "unknown_fixture"}:
        preview = replace(preview, fixture_sha256="changed" if part == "fixture" else "")
    else:
        preview = replace(preview, config_sha256="changed" if part == "comparison_config" else None)
    monkeypatch.setattr(case["module"], "preview_inputs", lambda *args: preview)
    context = _context(case)
    assert "PLT-04" not in context.carried_over
    assert context.triggers["PLT-04"][0].type == "REUSE_REJECTED"


@pytest.mark.parametrize("part", ["check_revision", "definition_hash", "image", "example"])
def test_independent_reuse_eligibility_conditions(reuse_case, monkeypatch, part):
    case = reuse_case
    if part == "check_revision":
        case["pack"].checks["PLT-04"].revision += 1
    elif part == "definition_hash":
        old = deepcopy(case["pack"])
        target = next(entry for entry in old.manifest.files_sha256 if entry.path == "checks/PLT-04.yaml")
        target.sha256 = "f" * 64
        monkeypatch.setattr(case["module"], "get_pack", lambda *args: old)
    elif part == "image":
        case["env"] = case["env"].model_copy(update={"engine": case["env"].engine.model_copy(
            update={"image_digest": "image@sha256:" + "f" * 64})}).seal()
    else:
        evd = case["evidence"].model_copy(update={"revision": 2, "data_origin": DataOrigin.EXAMPLE}).seal()
        case["store"].put(evd)
    assert "PLT-04" not in _context(case).carried_over


def test_pack_revision_alone_does_not_prevent_reuse(reuse_case):
    case = reuse_case
    case["pack"].manifest.revision += 1
    assert "PLT-04" in _context(case).carried_over


def test_judge_version_change_appends_preliminary_result_only(reuse_case):
    case = reuse_case
    old_result = case["result"].model_copy(update={"revision": 2, "judge":
        case["result"].judge.model_copy(update={"version": "1"})}).seal()
    case["store"].put(old_result)
    context = _context(case)
    carried = context.carried_over["PLT-04"]
    assert carried.result not in (case["result"].ref(), old_result.ref())
    fresh = case["store"].resolve(carried.result)
    assert fresh.judge.version == "2" and fresh.evidence == case["evidence"].ref()
    assert case["store"].resolve(old_result.ref()) == old_result
    assert len(list(case["store"].iter_kind("EVD"))) == 1
    assert len(list(case["store"].iter_kind("RES"))) == 3


def test_read_only_preview_does_not_store_rejudgment(reuse_case):
    case = reuse_case
    old = case["result"].model_copy(update={"revision": 2, "judge":
        case["result"].judge.model_copy(update={"version": "1"})}).seal()
    case["store"].put(old)
    context = _context(case, read_only=True)
    assert "PLT-04" not in context.carried_over
    assert len(list(case["store"].iter_kind("RES"))) == 2


def test_rejudged_nonpass_is_stored_but_not_carried(reuse_case, monkeypatch):
    from guard.judges.base import JudgeOutput

    case = reuse_case
    plugin = case["module"].judges.JUDGES["reuse-contract"]
    monkeypatch.setattr(plugin, "version", "3")
    monkeypatch.setattr(plugin, "judge", lambda *args: JudgeOutput(verdicts={"security": "FAIL"}))
    context = _context(case)
    assert "PLT-04" not in context.carried_over
    latest = list(case["store"].iter_kind("RES"))[-1]
    assert latest.verdicts.security.value == "FAIL"
    assert latest.evidence == case["evidence"].ref()
    assert len(list(case["store"].iter_kind("EVD"))) == 1


def test_missing_prior_artifact_cannot_back_a_carried_pass(reuse_case):
    from guard.models.objects import Artifact

    case = reuse_case
    artifact = Artifact(name="missing", path="artifacts/" + "d" * 64, sha256="d" * 64,
                        bytes=12, collected_at=T0, collector="test")
    evd = case["evidence"].model_copy(update={"revision": 2, "artifacts": [artifact]}).seal()
    case["store"].put(evd)
    result = case["result"].model_copy(update={"revision": 2, "evidence": evd.ref(), "refs": [evd.ref()]}).seal()
    case["store"].put(result)
    context = _context(case)
    assert "PLT-04" not in context.carried_over
    assert context.triggers["PLT-04"][0].refs == ["PRIOR_ARTIFACT_MISSING_OR_CHANGED"]


def test_patch_requires_confirmed_source_image_and_baseline_image(reuse_case):
    case = reuse_case
    baseline = case["env"]
    candidate = baseline.model_copy(update={"id": "ENV-CANDIDATE", "engine": baseline.engine.model_copy(
        update={"version": "1.0.1", "source_commit": "b" * 40, "image_digest": "image@sha256:" + "b" * 64})}).seal()
    change = _change(["AUTH"])
    context = _context(case, envs=[baseline, candidate], change=change, mode="update")
    assert context.change_type == "ENGINE_PATCH" and not context.full_scope
    assert "PLT-04" in context.carried_over
    assert "REQ-01" in context.triggers and "REQ-09" in context.triggers
    unverified = candidate.model_copy(update={"engine": candidate.engine.model_copy(update={"source_commit": None})}).seal()
    assert _context(case, envs=[baseline, unverified], change=change, mode="update").full_scope
    wrong_start = baseline.model_copy(update={"engine": baseline.engine.model_copy(
        update={"image_digest": "image@sha256:" + "f" * 64})}).seal()
    assert "PLT-04" not in _context(case, envs=[wrong_start, candidate], change=change, mode="update").carried_over


@pytest.mark.parametrize("relation", ["match", "baseline_mismatch", "candidate_mismatch", "missing", "manual", "unknown_env"])
def test_model_manifest_target_must_match_before_narrowing_model_categories(reuse_case, relation):
    from guard.core.select import select
    from guard.models.objects import ModelInfo

    case = reuse_case
    pack = case["pack"]
    pack.change_selection["model_categories"] = {"MODEL_TEMPLATE": ["REQ-07"], "MODEL_WEIGHTS": ["SUP-03"]}
    pack.change_selection["model_common"] = ["REQ-05"]
    old = case["env"].model_copy(update={"id": "ENV-MODEL-BEFORE", "model": ModelInfo(
        id="EXAMPLE/model", revision="before", inventory_sha256="a" * 64)}).seal()
    case["store"].put(old)
    previous = select(pack, [old], case["policy"])
    case["store"].put(previous)
    case["previous"] = previous
    current = old.model_copy(update={"id": "ENV-MODEL-AFTER", "model": ModelInfo(
        id="EXAMPLE/model", revision="after", inventory_sha256=None if relation == "unknown_env" else "b" * 64)}).seal()
    change = _change(["MODEL_TEMPLATE"])
    analysis = {"baseline_inventory_sha256": "a" * 64, "candidate_inventory_sha256": "b" * 64}
    if relation == "baseline_mismatch":
        analysis["baseline_inventory_sha256"] = "f" * 64
    elif relation == "candidate_mismatch":
        analysis["candidate_inventory_sha256"] = "f" * 64
    change = change.model_copy(update={
        "extractor": change.extractor.model_copy(update={"name": "manual" if relation == "manual" else "model_manifest"}),
        "analysis": None if relation == "missing" else analysis,
    }).seal()
    context = _context(case, envs=[current], change=change)
    assert context.change_type == "MODEL" and not context.full_scope
    assert any(trigger.type == "MODEL_DIFF" for trigger in context.triggers["REQ-07"])
    assert any(trigger.type == "MODEL_COMMON" for trigger in context.triggers["REQ-05"])
    weights_triggers = context.triggers["SUP-03"]
    assert any(trigger.type == "MODEL_DIFF" for trigger in weights_triggers) == (relation != "match")
    assert any(trigger.type == "MODEL_MANIFEST_UNCONFIRMED" for trigger in weights_triggers) == (relation != "match")


@pytest.mark.parametrize("version", ["1.1.0", "0.9.0", "1.0.1rc1", None])
def test_full_engine_release_never_reuses_previous_pass(reuse_case, version):
    case = reuse_case
    candidate = case["env"].model_copy(update={"engine": case["env"].engine.model_copy(
        update={"version": version, "image_digest": "image@sha256:" + "b" * 64})}).seal()
    context = _context(case, envs=[case["env"], candidate], mode="update")
    assert context.full_scope and context.change_type == "ENGINE_RELEASE"
    assert not context.carried_over
