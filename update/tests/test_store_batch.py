from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import sqlite3
from threading import Barrier

import pytest

from guard.core.judge import judge_evidence
from guard.core.report import build_report
from guard.core.select import select
from guard.models import DataOrigin, ExecStatus, SecurityFailureTag, Side
from guard.store import AppendOnlyError, IntegrityError, Store
from tests.builders import T0, baseline_pack, environment, evidence, policy


def test_batch_can_reference_an_earlier_object(tmp_path):
    store = Store(tmp_path)
    env = environment()
    pol = policy(refs=[env.ref()])
    assert store.put_many([env, pol]) == [env.ref(), pol.ref()]
    assert store.verify() == []


def test_invalid_later_object_rolls_back_prior_insert(tmp_path):
    store = Store(tmp_path)
    env = environment()
    invalid = policy().model_copy(update={"content_sha256":"invalid"})
    with pytest.raises(IntegrityError): store.put_many([env, invalid])
    assert list(store.iter_kind("ENV")) == [] and list(store.iter_kind("POL")) == []


def test_batch_failure_preserves_existing_revisions(tmp_path):
    store = Store(tmp_path)
    env = environment(); store.put(env)
    newer = env.model_copy(update={"revision":2,"data_origin":DataOrigin.DECLARED}).seal()
    bad = policy(refs=[env.ref().model_copy(update={"sha256":"wrong"})])
    with pytest.raises(IntegrityError): store.put_many([newer,bad])
    assert store.revisions(env.id) == [1] and store.resolve(env.ref()) == env


@pytest.fixture
def selected_evidence(tmp_path):
    store = Store(tmp_path)
    pack = baseline_pack()
    env = environment({"gateway": {"value": True, "origin": "EXAMPLE", "source": "test"}})
    pol = policy(pack.ref(), scope={"include": ["REQ-01"]})
    plan = select(pack, [env], pol)
    store.put_many([env, pol, plan])
    candidate = evidence().model_copy(update={
        "plan": plan.ref(), "environment": env.ref(), "refs": [plan.ref(), env.ref()],
    }).seal()
    return store, pack, pol, plan, candidate


@pytest.mark.parametrize("kind", ["EVD", "RES"])
def test_parallel_first_append_has_one_logical_id(selected_evidence, kind):
    store, pack, pol, plan, evd = selected_evidence
    first = evd
    if kind == "RES":
        store.put(evd)
        first = judge_evidence(pack.checks[evd.check_id], evd, pol, T0, plan=plan)
    second = first.model_copy(update={"id": f"{kind}-ALTERNATE"}).seal()
    barrier = Barrier(2)

    def append(obj):
        barrier.wait(timeout=5)
        try:
            return store.put(obj)
        except AppendOnlyError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        refs = list(executor.map(append, (first, second)))
    assert sum(ref is not None for ref in refs) == 1
    (saved,) = list(store.iter_kind(kind))
    updated = saved.model_copy(update={"revision": 2}).seal()
    assert store.put(updated) == updated.ref()
    assert store.verify() == []


def test_typed_evidence_lineage_is_validated_even_without_generic_refs(selected_evidence):
    store, pack, pol, plan, evd = selected_evidence
    with pytest.raises(IntegrityError, match="not selected"):
        store.put(evd.model_copy(update={"check_id": "REQ-03", "refs": []}).seal())
    with pytest.raises(IntegrityError, match="side environment"):
        store.put(evd.model_copy(update={"side": Side.candidate, "refs": []}).seal())
    store.put(evd)
    result = judge_evidence(pack.checks[evd.check_id], evd, pol, T0, plan=plan)
    with pytest.raises(IntegrityError, match="RES check and side"):
        store.put(result.model_copy(update={"side": Side.candidate}).seal())
    with pytest.raises(IntegrityError, match="ref identity"):
        store.resolve(evd.ref().model_copy(update={"kind": "RES"}))


def test_read_only_verify_reports_index_mismatch(tmp_path):
    store = Store(tmp_path)
    env = environment()
    body = env.to_dict()
    with sqlite3.connect(store.db_path) as con:
        con.execute("INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)", (
            env.id, env.revision, "POL", env.content_sha256, body["created_at"], json.dumps(body),
        ))
    before = store.db_path.read_bytes()
    assert any("database index differs" in problem for problem in Store.read_only(tmp_path).verify())
    assert store.db_path.read_bytes() == before


def test_an_object_identity_cannot_change_kind_between_revisions(tmp_path):
    store = Store(tmp_path)
    env = environment()
    store.put(env)
    with pytest.raises(AppendOnlyError, match="changes its object kind"):
        store.put(policy(id=env.id, revision=2))


def test_report_requires_exact_evidence_attempt_status_and_origin_even_without_result(selected_evidence):
    store, pack, _, plan, evd = selected_evidence
    evd = evd.model_copy(update={"execution": evd.execution.model_copy(update={"attempt_id": "ATTEMPT-current"})}).seal()
    store.put(evd)
    report = build_report(store, pack, plan, T0, attempt_id="ATTEMPT-current", evidence=[evd], judged=[])
    store.put(report)
    row_index = next(i for i, row in enumerate(report.by_check) if row.check_id == evd.check_id)
    row = report.by_check[row_index]
    assert row.single.result is None and row.single.evidence_ref == evd.ref()
    with pytest.raises(IntegrityError, match="execution attempt"):
        store.validate_object(report.model_copy(update={"execution_attempt_id": "ATTEMPT-other"}).seal())

    for changed in ({"execution_status": ExecStatus.ABORTED}, {"data_origin": DataOrigin.MEASURED}):
        rows = report.by_check.copy()
        rows[row_index] = row.model_copy(update={"single": row.single.model_copy(update=changed)})
        with pytest.raises(IntegrityError, match="status or origin"):
            store.validate_object(report.model_copy(update={"by_check": rows}).seal())


def test_report_cannot_change_the_recorded_result_judgment_time(selected_evidence):
    store, pack, pol, plan, evd = selected_evidence
    store.put(evd)
    result = judge_evidence(pack.checks[evd.check_id], evd, pol, T0, plan=plan)
    store.put(result)
    report = build_report(store, pack, plan, T0, evidence=[evd], judged=[result])
    with pytest.raises(IntegrityError, match="judgment times differ"):
        store.validate_object(report.model_copy(update={"as_of": T0 + timedelta(seconds=1)}).seal())


def test_binary_report_cannot_invent_confirmed_violation_without_result(selected_evidence):
    store, pack, _, plan, evd = selected_evidence
    report = build_report(store, pack, plan, T0, evidence=[], judged=[])
    index = next(index for index, row in enumerate(report.by_check) if row.check_id == evd.check_id)
    row = report.by_check[index]
    assert row.single.security_failure_tags == ["INSUFFICIENT_EVIDENCE"]
    rows = report.by_check.copy()
    rows[index] = row.model_copy(update={
        "single": row.single.model_copy(update={"security_failure_tags": [SecurityFailureTag.CONFIRMED_VIOLATION]}),
    })
    with pytest.raises(IntegrityError, match="unsupported verdict or tag"):
        store.validate_object(report.model_copy(update={"by_check": rows}).seal())
