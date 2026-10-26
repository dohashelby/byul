"""공통 아티팩트 저장 실패의 오프라인 회귀. 실제 엔진·Docker·네트워크 실행 0.

모든 입력/실행기는 EXAMPLE이다. MEASURED enum을 주는 사례도 출처 상한의
메타모델을 검증하는 합성 입력이며 실제 측정이나 엔진 실증을 뜻하지 않는다.
기존 REQ-01 정의·rule_table·FakeAdapter와 실제 append-only Store를 재사용한다.
"""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.adapters.fake import FakeAdapter
from guard.core import change_selection
from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.base import DraftArtifact, EvidenceDraft
from guard.executors.reuse import ExecutionPreview
from guard.judges import policy_items
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, sha256_hex
from guard.models.objects import ExecutorInfo, Observation, PluginInfo
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import get_pack, put_pack
from tests.builders import PACK_DIR, ROUTES, T0, baseline_pack, environment, policy, req01_obs


ERROR_CANARY = "EXAMPLE_ARTIFACT_ERROR_CANARY"
PATH_CANARY = "EXAMPLE_ARTIFACT_PATH_CANARY"
DIAGNOSTIC = "ARTIFACT_PERSISTENCE_INCOMPLETE"


@pytest.fixture
def run_case(tmp_path, monkeypatch):
    """정상/위반 관측을 반환한 뒤 특정 첨부 I/O만 실패시킨다."""
    counter = []

    def run(*, fault=None, violation=True, origin=DataOrigin.EXAMPLE, artifact_count=3,
            draft_status=ExecStatus.COMPLETED, draft_reason=None, input_origin=DataOrigin.EXAMPLE):
        root = tmp_path / f"case-{len(counter)}"
        counter.append(root)
        root.mkdir()
        pack = baseline_pack()
        check = pack.checks["REQ-01"]
        env = environment({"gateway": {"value": True, "origin": input_origin, "source": "EXAMPLE-only"}}, origin=input_origin,
                          engine={"name": "EXAMPLE-memory-executor", "image_digest": "example.invalid/image@sha256:" + "a" * 64})
        pol = policy(pack.ref(), data_origin=input_origin, scope={"include": [check.id], "exclude": []})
        store = Store(root / "store")
        store.put_many([env, pol])
        plan = select(pack, [env], pol)
        store.put(plan)
        work = root / PATH_CANARY
        work.mkdir()
        payloads = [f'{{"origin":"EXAMPLE","artifact":{index}}}\n'.encode() for index in range(artifact_count)]
        paths = [work / f"EXAMPLE-{index}.json" for index in range(artifact_count)]
        for path, payload in zip(paths, payloads):
            path.write_bytes(payload)
        config = {"profile": "EXAMPLE-artifact-persistence", "version": 1}
        draft = EvidenceDraft(status=draft_status, data_origin=origin,
            normal_control={"attempted": True, "succeeded": True, "detail": "EXAMPLE normal control"},
            challenges=[{"step": step, "attempted": True, "observations": {"EXAMPLE": index}}
                        for index, step in enumerate(check.challenge_steps)],
            observations=req01_obs(direct={route: 200 for route in ROUTES} if violation else None),
            artifacts=[DraftArtifact(name=path.name, path=path, collector="EXAMPLE-artifacts/1") for path in paths],
            comparability={"fixture_sha256": sha256_hex(b"EXAMPLE fixture"),
                           "config_sha256": sha256_hex(b"EXAMPLE comparison config"),
                           "model_inventory_sha256": sha256_hex(b"EXAMPLE inventory"),
                           "gpu": "EXAMPLE GPU", "driver": "EXAMPLE driver"},
            config=config, started_at=T0, finished_at=T0 + timedelta(seconds=1),
            abort_reason=draft_reason, cleanup_errors=["EXAMPLE_EXISTING_CLEANUP_DIAGNOSTIC"])
        original_draft = draft.model_dump(mode="json")

        class ExampleExecutor:
            name, version = "EXAMPLE-artifact-executor", "1"
            calls = 0
            adapter = None

            def run(self, _check, _env, _values, adapter, _workdir):
                self.calls += 1
                self.adapter = adapter
                return draft

        producer = ExampleExecutor()
        original_read, original_put = Path.read_bytes, store.put_artifact
        events = {"read_faults": 0, "put_faults": 0, "successful_puts": []}

        def read_bytes(path):
            if fault == "read" and len(paths) > 1 and path == paths[1]:
                events["read_faults"] += 1
                raise OSError(5, ERROR_CANARY, str(path))
            return original_read(path)

        def put_artifact(data):
            if fault == "put" and len(payloads) > 1 and data == payloads[1]:
                events["put_faults"] += 1
                raise OSError(5, ERROR_CANARY, str(paths[1]))
            stored = original_put(data)
            events["successful_puts"].append(stored)
            return stored

        with monkeypatch.context() as scoped:
            scoped.setitem(registry.EXECUTORS, "direct_route:artifact-example", lambda _cfg: producer)
            scoped.setattr(Path, "read_bytes", read_bytes)
            scoped.setattr(store, "put_artifact", put_artifact)
            evd, = run_plan(store, pack, plan,
                RunConfig(executors={"direct_route": "artifact-example"}, adapter="fake"))
        assert producer.calls == 1 and isinstance(producer.adapter, FakeAdapter)
        assert draft.model_dump(mode="json") == original_draft  # 코어가 반환 초안을 고치지 않는다.
        result, = judge_plan(store, pack, plan, as_of=T0)
        return SimpleNamespace(root=root, store=store, pack=pack, check=check, env=env, policy=pol,
            plan=plan, draft=draft, evd=evd, result=result, paths=paths, payloads=payloads, events=events)

    return run


def assert_draft_metadata_preserved(case):
    draft, evd = case.draft, case.evd
    assert evd.observations == draft.observations
    assert evd.normal_control == draft.normal_control and evd.challenges == draft.challenges
    assert evd.comparability == draft.comparability
    assert evd.execution.executor == ExecutorInfo(name="EXAMPLE-artifact-executor", version="1",
                                                config_sha256=sha256_hex(canonical_json(draft.config)))
    assert evd.execution.adapter == PluginInfo(name=FakeAdapter.name, version=FakeAdapter.version)
    assert evd.execution.started_at == draft.started_at and evd.execution.finished_at == draft.finished_at
    assert evd.execution.cleanup_errors == draft.cleanup_errors
    assert case.store.resolve(evd.ref()).to_dict() == evd.to_dict()
    assert case.store.verify() == []


@pytest.mark.parametrize("fault", ["read", "put"])
def test_second_artifact_failure_keeps_prior_and_later_files_and_independent_failure(run_case, fault):
    case = run_case(fault=fault)
    assert case.evd.execution.status == ExecStatus.ABORTED
    assert case.evd.execution.abort_reason == DIAGNOSTIC
    assert case.evd.data_origin == DataOrigin.EXAMPLE
    assert_draft_metadata_preserved(case)
    assert case.events["read_faults"] + case.events["put_faults"] == 1
    assert [artifact.name for artifact in case.evd.artifacts] == [case.paths[0].name, case.paths[2].name]
    assert len(case.events["successful_puts"]) == 2
    for artifact, index in zip(case.evd.artifacts, (0, 2)):
        assert artifact.collector == "EXAMPLE-artifacts/1"
        assert artifact.path == f"artifacts/{sha256_hex(case.payloads[index])}"
        assert artifact.sha256 == sha256_hex(case.payloads[index]) and artifact.bytes == len(case.payloads[index])
        assert (case.store.root / artifact.path).read_bytes() == case.payloads[index]
    assert not (case.store.root / "artifacts" / sha256_hex(case.payloads[1])).exists()
    assert case.result.verdicts.security == Verdict.FAIL
    assert case.result.verdicts.functional == case.result.verdicts.performance == Verdict.UNKNOWN
    assert case.result.verification_level is None and case.result.data_origin == DataOrigin.EXAMPLE
    persisted = canonical_json(case.evd.to_dict()) + canonical_json(case.result.to_dict())
    for canary in (ERROR_CANARY, PATH_CANARY, str(case.paths[1])):
        assert canary.encode() not in persisted


@pytest.mark.parametrize("fault", ["read", "put"])
def test_passing_rule_candidates_remain_unknown_after_auxiliary_persistence_failure(run_case, fault):
    case = run_case(fault=fault, violation=False)
    raw = RuleTableJudge().judge(case.check, case.evd, case.check.judge.params, case.policy.values[case.check.id])
    assert raw.verdicts["security"] == raw.verdicts["functional"] == Verdict.PASS
    assert case.evd.execution.status == ExecStatus.ABORTED
    assert case.result.verdicts.security == case.result.verdicts.functional == case.result.verdicts.performance == Verdict.UNKNOWN
    assert_draft_metadata_preserved(case)


@pytest.mark.parametrize("origin,expected", [(DataOrigin.EXAMPLE, DataOrigin.EXAMPLE),
    (DataOrigin.DECLARED, DataOrigin.DECLARED), (DataOrigin.MEASURED, DataOrigin.DECLARED)])
def test_incomplete_artifacts_cap_synthetic_origin_without_upgrading_example(run_case, origin, expected):
    # The MEASURED enum is deliberately synthetic; this does not measure anything.
    case = run_case(fault="put", origin=origin)
    assert case.evd.data_origin == expected and case.evd.execution.status == ExecStatus.ABORTED
    assert case.result.verdicts.security == Verdict.FAIL
    # Incomplete execution has no verification level. The result's separate
    # origin also includes this case's EXAMPLE policy/plan, even for a D EVD.
    assert case.result.verification_level is None and case.result.data_origin == DataOrigin.EXAMPLE
    assert_draft_metadata_preserved(case)


@pytest.mark.parametrize("artifact_count", [0, 3])
@pytest.mark.parametrize("origin", [DataOrigin.EXAMPLE, DataOrigin.MEASURED])
def test_zero_or_successful_artifacts_leave_existing_draft_semantics_unchanged(run_case, artifact_count, origin):
    case = run_case(artifact_count=artifact_count, origin=origin, violation=False)
    assert case.evd.execution.status == case.draft.status == ExecStatus.COMPLETED
    assert case.evd.data_origin == case.draft.data_origin == origin
    assert case.evd.execution.abort_reason == case.draft.abort_reason is None
    assert len(case.evd.artifacts) == artifact_count and len(case.events["successful_puts"]) == artifact_count
    assert case.result.verdicts.security == case.result.verdicts.functional == Verdict.PASS
    assert_draft_metadata_preserved(case)


def test_existing_abort_reason_and_cleanup_metadata_survive_another_persistence_failure(run_case):
    case = run_case(fault="read", draft_status=ExecStatus.ABORTED, draft_reason="EXAMPLE_PRIOR_DIAGNOSTIC")
    assert case.evd.execution.status == ExecStatus.ABORTED
    assert "EXAMPLE_PRIOR_DIAGNOSTIC" in case.evd.execution.abort_reason and DIAGNOSTIC in case.evd.execution.abort_reason
    assert case.result.verdicts.security == Verdict.FAIL
    assert_draft_metadata_preserved(case)


@pytest.mark.parametrize("fault", ["read", "put"])
def test_export_restore_and_offline_rejudge_keep_the_same_partial_failure(run_case, fault):
    case = run_case(fault=fault)
    assert put_pack(case.store, PACK_DIR) == case.pack.ref()
    original_evd, original_res = case.evd.to_dict(), case.result.to_dict()
    archive = case.root / "EXAMPLE-evidence.zip"
    export_bundle(case.store.root, archive)
    destination = case.root / "restored"
    restore_bundle(archive, destination)
    restored = Store(destination)
    assert restored.verify() == []
    assert restored.resolve(case.evd.ref()).to_dict() == original_evd
    assert restored.resolve(case.result.ref()).to_dict() == original_res
    saved_pack = get_pack(restored, case.pack.ref())
    replay, = judge_plan(restored, saved_pack, restored.resolve(case.plan.ref()), as_of=T0)
    assert replay.ref() == case.result.ref() and replay.verdicts.security == Verdict.FAIL
    assert restored.revisions(case.result.id) == [case.result.revision]
    assert restored.resolve(case.evd.ref()).execution.status == ExecStatus.ABORTED
    assert [a.sha256 for a in restored.resolve(case.evd.ref()).artifacts] == [a.sha256 for a in case.evd.artifacts]


def test_incomplete_evidence_cannot_be_reused_even_with_preserved_metadata(run_case, monkeypatch):
    # Synthetic DECLARED inputs avoid EXAMPLE as a separate rejection cause.
    # Both cases still use the EXAMPLE executor and never measure an engine.
    case = run_case(fault="put", violation=False, origin=DataOrigin.MEASURED, input_origin=DataOrigin.DECLARED)
    assert case.evd.data_origin == DataOrigin.DECLARED and case.evd.execution.status == ExecStatus.ABORTED
    assert put_pack(case.store, PACK_DIR) == case.pack.ref()
    preview = ExecutionPreview(case.evd.execution.executor, case.evd.execution.adapter,
        case.evd.comparability.fixture_sha256, case.evd.comparability.config_sha256)
    monkeypatch.setattr(change_selection, "preview_inputs", lambda *_args: preview)
    # The real result is already UNKNOWN and ABORTED must be rejected before
    # procedure/preview or result-PASS reuse gates can make any inference.
    assert case.result.verdicts.security == Verdict.UNKNOWN and case.result.data_origin == DataOrigin.DECLARED
    before_evd = [value.to_dict() for value in case.store.iter_kind("EVD")]
    before_res = [value.to_dict() for value in case.store.iter_kind("RES")]
    context = change_selection.build_context(case.store, case.plan, case.pack, [case.env],
        case.policy, {}, as_of=T0, read_only=True)
    assert not context.full_scope and case.check.id not in context.carried_over
    rejected, = context.triggers[case.check.id]
    assert rejected.type == "REUSE_REJECTED" and rejected.refs == ["PRIOR_EVIDENCE_INELIGIBLE"]
    assert [value.to_dict() for value in case.store.iter_kind("EVD")] == before_evd
    assert [value.to_dict() for value in case.store.iter_kind("RES")] == before_res

    control = run_case(violation=False, origin=DataOrigin.MEASURED, input_origin=DataOrigin.DECLARED)
    assert put_pack(control.store, PACK_DIR) == control.pack.ref()
    control_preview = ExecutionPreview(control.evd.execution.executor, control.evd.execution.adapter,
        control.evd.comparability.fixture_sha256, control.evd.comparability.config_sha256)
    monkeypatch.setattr(change_selection, "preview_inputs", lambda *_args: control_preview)
    assert control.result.verdicts.security == Verdict.PASS
    allowed = change_selection.build_context(control.store, control.plan, control.pack, [control.env],
        control.policy, {}, as_of=T0, read_only=True)
    assert allowed.carried_over[control.check.id].evidence == control.evd.ref()
    assert allowed.carried_over[control.check.id].result == control.result.ref()


def test_attachment_required_policy_judge_does_not_promote_an_unattached_claim(run_case):
    # This existing pure judge requires an actual attachment hash. Keeping
    # observation metadata alone must not turn its missing premise into FAIL.
    case = run_case(fault="put")
    check = case.pack.checks["SUP-14"]
    values = {"required_checks": ["EXAMPLE-required"]}
    payload = {"version": "guard-policy-evidence/1", "parameter": "required_checks", "records": [{
        "item_id": "EXAMPLE-required", "case": "failed",
        "observations": {"trial_id": "EXAMPLE-trial", "target_scope": "EXAMPLE-scope",
            "check_outcome": "failed", "gate_outcome": "advanced", "exception_assessment": "none",
            "transition_detail": "EXAMPLE ordered transition"},
        "evidence": [{"sha256": sha256_hex(case.payloads[1]), "locator": "EXAMPLE line 1",
                      "basis": "EXAMPLE item match"}], "limitation": None}]}
    value = case.evd.model_copy(update={"check_id": check.id,
        "observations": {policy_items.OBSERVATION: Observation(value=payload, collected=True)}}).seal()
    ceilings, reasons, missing = policy_items.evaluate(check, value, values)
    assert ceilings["security"] == "UNKNOWN" and missing
    assert any("POLICY_EVIDENCE_INVALID" in reason for reason in reasons)
    linked = deepcopy(payload)
    linked["records"][0]["evidence"][0]["sha256"] = case.evd.artifacts[0].sha256
    attached = value.model_copy(update={"observations": {
        policy_items.OBSERVATION: Observation(value=linked, collected=True)}}).seal()
    confirmed, _, _ = policy_items.evaluate(check, attached, values)
    assert confirmed["security"] == "FAIL"

