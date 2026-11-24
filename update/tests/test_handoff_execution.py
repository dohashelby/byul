"""Regressions at the attempt, decision time and CLI recovery boundaries."""
from datetime import timedelta
import json

from typer.testing import CliRunner

from guard.cli import app
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.models import canonical_json, new_id
from guard.models.objects import PolicyException, Report
from guard.store import Store
from tests.builders import T0
from tests.update_builders import build_update_case


def _saved_run(tmp_path):
    data, _, pack = build_update_case(tmp_path)
    report = Report.model_validate(data)
    store = Store(tmp_path / "store")
    plan_ref = next(ref for ref in report.inputs_manifest if ref.kind == "PLAN")
    return store, pack, store.resolve(plan_ref), report


def test_partial_rerun_does_not_compare_old_candidate(tmp_path):
    store, pack, plan, _ = _saved_run(tmp_path)
    before, = store.find_latest("EVD", lambda e: e.check_id == "REQ-01" and e.side == "baseline")
    attempt = new_id("ATTEMPT")
    fresh = before.model_copy(update={"revision": before.revision + 1,
        "execution": before.execution.model_copy(update={"attempt_id": attempt})}).seal()
    store.put(fresh)
    judge_plan(store, pack, plan, T0)
    report = build_report(store, pack, plan, None)
    row = next(row for row in report.by_check if row.check_id == "REQ-01")
    assert report.execution_attempt_id == attempt
    assert row.baseline.evidence_ref == fresh.ref()
    assert row.candidate.evidence_ref is None
    assert row.comparison == "NOT_COMPARABLE"


def test_cli_uses_original_exact_evidence_and_decision_time(tmp_path):
    store, pack, plan, original = _saved_run(tmp_path)
    later = judge_plan(store, pack, plan, T0 + timedelta(hours=1))
    assert all(result.as_of != original.as_of for result in later)
    row = next(row for row in original.by_check if row.check_id == "REQ-01")
    prior = store.resolve(row.baseline.result)
    newer = prior.model_copy(update={"revision": store.latest(prior.id).revision + 1,
        "judge": prior.judge.model_copy(update={"version": "different-evaluation"})}).seal()
    store.put(newer)
    source = tmp_path / "original.json"
    source.write_bytes(canonical_json(original.to_dict()))
    common = ["--store", str(store.root), "--plan", plan.id, "--from-report", str(source)]
    runner = CliRunner()
    result = runner.invoke(app, ["judge", *common])
    assert result.exit_code == 0, result.output + repr(result.exception)
    output = tmp_path / "replayed.json"
    result = runner.invoke(app, ["report", *common, "--json", str(output)])
    assert result.exit_code == 0, result.output + repr(result.exception)
    replayed = Report.model_validate(json.loads(output.read_bytes()))
    assert replayed.as_of == original.as_of
    assert newer.ref() not in replayed.inputs_manifest
    assert replayed.by_check == original.by_check


def test_pending_recovery_is_idempotent_and_does_not_dispatch(tmp_path):
    store, _, plan, _ = _saved_run(tmp_path)
    prior, = store.find_latest("EVD", lambda e: e.check_id == "REQ-01" and e.side == "candidate")
    attempt = new_id("ATTEMPT")
    fragment = prior.model_copy(update={"revision": prior.revision + 1,
        "execution": prior.execution.model_copy(update={"attempt_id": attempt})}).seal()
    folder = store.root / "pending-evidence" / attempt
    folder.mkdir(parents=True)
    (folder / (fragment.content_sha256 + ".json")).write_bytes(canonical_json(fragment.to_dict()))
    args = ["evidence", "recover", "--plan", plan.id, "--attempt", attempt, "--store", str(store.root)]
    for _ in range(2):
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 0, result.output + repr(result.exception)
        assert store.latest(prior.id).ref() == fragment.ref()
    assert not (store.root / "work").exists()


def test_policy_exception_has_inclusive_approval_and_exclusive_expiry():
    exception = PolicyException(check_id="REQ-01", reason="EXAMPLE", approved_by="EXAMPLE",
                                approved_at=T0, expires_at=T0 + timedelta(seconds=1))
    assert exception.unavailable_reason(T0 - timedelta(seconds=1)) == "approval not yet effective"
    assert exception.unavailable_reason(T0) is None
    assert exception.unavailable_reason(T0 + timedelta(seconds=1)) == "expired"
    assert exception.unavailable_reason(T0.replace(tzinfo=None)) == "unverified timezone"
