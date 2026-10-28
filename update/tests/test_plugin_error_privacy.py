"""EXAMPLE plugin failures through product storage; no engine or network."""
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from guard.core.judge import judge_plan
from guard.executors import registry as executors
from guard.judges import registry as judges
from guard.models import ExecStatus, Verdict, canonical_json
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import get_pack, put_pack
from tests.builders import PACK_DIR, T0, evidence
from tests.judging import OK, judge
from tests.test_run import assert_empty_aborted, case  # noqa: F401 - shared EXAMPLE fixture


CANARY = "EXAMPLE_PRIVATE_BODY_TOKEN_PATH_CANARY"


def failure(kind):
    calls = []
    if kind == "message":
        error = RuntimeError(CANARY)
    elif kind == "dynamic_type":
        error = type(CANARY, (RuntimeError,), {})(CANARY)
    else:
        class HostileString(RuntimeError):
            def __str__(self):
                calls.append(True)
                raise AssertionError(CANARY)
        error = HostileString(CANARY)
    return error, calls


def raise_error(error):
    def fail(*args, **kwargs):
        raise error
    return fail


@pytest.mark.parametrize("stage", ["constructor", "run"])
@pytest.mark.parametrize("kind", ["message", "dynamic_type", "hostile_string"])
def test_executor_fallback_never_formats_exception_or_saves_private_content(case, monkeypatch, stage, kind):
    error, calls = failure(kind)
    if stage == "constructor":
        monkeypatch.setitem(executors.EXECUTORS, "direct_route:unit", raise_error(error))
    else:
        case.executor.error = error
    evd = case.invoke()
    assert evd.execution.status == ExecStatus.ABORTED and evd.data_origin.value == "EXAMPLE"
    assert evd.execution.abort_reason == "EXECUTOR_FAILURE"
    assert not evd.artifacts and not evd.normal_control.attempted
    assert all(not c.attempted for c in evd.challenges)
    assert all(not o.collected and o.value is None for o in evd.observations.values())
    if stage == "constructor":
        assert evd.execution.executor is None
    else:
        assert_empty_aborted(evd)
    assert calls == []
    assert CANARY not in canonical_json(evd.to_dict()).decode()
    assert case.store.verify() == []


@pytest.mark.parametrize("kind", ["wrong_return_type", "invalid_origin"])
def test_invalid_output_is_empty_aborted_without_type_name_or_validation_input(case, kind):
    if kind == "wrong_return_type":
        case.executor.output = type(CANARY, (), {})()
    else:
        # Deliberately bypass model validation; this is invalid EXAMPLE output.
        case.executor.output = case.draft.model_copy(update={"data_origin": CANARY})
    if kind == "invalid_origin":
        with pytest.warns(UserWarning, match="Pydantic serializer warnings"):
            evd = case.invoke()
    else:
        evd = case.invoke()
    assert_empty_aborted(evd)
    assert evd.execution.abort_reason == "EXECUTOR_FAILURE"
    assert CANARY not in canonical_json(evd.to_dict()).decode()


@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit])
def test_executor_construction_cancellation_propagates_without_evidence(case, monkeypatch, failure_type):
    error = failure_type(CANARY)
    monkeypatch.setitem(executors.EXECUTORS, "direct_route:unit", raise_error(error))
    with pytest.raises(failure_type) as caught:
        case.invoke()
    assert caught.value is error and list(case.store.iter_kind("EVD")) == []


def broken_judge(error):
    return SimpleNamespace(name="rule_table", version="EXAMPLE-privacy", judge=raise_error(error))


@pytest.mark.parametrize("kind", ["message", "dynamic_type", "hostile_string"])
def test_judge_fallback_never_formats_exception_and_retains_unknown(monkeypatch, kind):
    error, calls = failure(kind)
    monkeypatch.setitem(judges.JUDGES, "rule_table", broken_judge(error))
    res = judge(evidence(observations=OK))
    assert set(res.verdicts.model_dump().values()) == {Verdict.UNKNOWN}
    assert res.reasons == ["JUDGE_PLUGIN_FAILURE"] and calls == []
    assert CANARY not in canonical_json(res.to_dict()).decode()


@pytest.mark.parametrize("output", [None, SimpleNamespace(verdicts={"security": CANARY})])
def test_invalid_judge_output_keeps_unknown_and_omits_validation_input(monkeypatch, output):
    plugin = SimpleNamespace(name="rule_table", version="EXAMPLE-privacy", judge=lambda *args: output)
    monkeypatch.setitem(judges.JUDGES, "rule_table", plugin)
    res = judge(evidence(observations=OK))
    assert set(res.verdicts.model_dump().values()) == {Verdict.UNKNOWN}
    assert res.reasons == ["JUDGE_PLUGIN_FAILURE"]
    assert CANARY not in canonical_json(res.to_dict()).decode()


@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit])
def test_judge_cancellation_is_not_converted_to_unknown(monkeypatch, failure_type):
    error = failure_type(CANARY)
    monkeypatch.setitem(judges.JUDGES, "rule_table", broken_judge(error))
    with pytest.raises(failure_type) as caught:
        judge(evidence(observations=OK))
    assert caught.value is error


@pytest.mark.parametrize("stage", ["executor", "judge"])
def test_private_error_is_absent_from_saved_report_bundle_and_exact_ref_replay(case, tmp_path, monkeypatch, stage):
    from guard.core.report import build_report
    error, _ = failure("dynamic_type")
    if stage == "executor":
        case.executor.error = error
    else:
        monkeypatch.setitem(judges.JUDGES, "rule_table", broken_judge(error))
        case.add_files(1)
    evd = case.invoke()
    res, = judge_plan(case.store, case.pack, case.plan, as_of=T0)
    assert set(res.verdicts.model_dump().values()) == {Verdict.UNKNOWN}
    assert evd.execution.status == (ExecStatus.ABORTED if stage == "executor" else ExecStatus.COMPLETED)
    assert evd.execution.abort_reason == ("EXECUTOR_FAILURE" if stage == "executor" else None)
    if stage == "judge":
        assert res.reasons == ["JUDGE_PLUGIN_FAILURE"] and len(evd.artifacts) == 1
    put_pack(case.store, PACK_DIR)
    rpt = build_report(case.store, case.pack, case.plan, as_of=T0)
    case.store.put(rpt)
    for obj in (evd, res, rpt):
        assert CANARY not in canonical_json(obj.to_dict()).decode()
    archive = tmp_path / "evidence.zip"
    export_bundle(case.store.root, archive)
    with ZipFile(archive) as bundle:
        assert all(CANARY.encode() not in bundle.read(name) for name in bundle.namelist())
    restore_bundle(archive, tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    pack = get_pack(restored, case.pack.ref())
    replayed, = judge_plan(restored, pack, restored.resolve(case.plan.ref()), as_of=T0)
    assert replayed.ref() == res.ref()
    assert restored.resolve(evd.ref()).to_dict() == evd.to_dict()
    assert restored.resolve(rpt.ref()).to_dict() == rpt.to_dict()
    assert restored.verify() == []
