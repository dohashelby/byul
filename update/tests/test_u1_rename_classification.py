"""Rename 양쪽의 미분류·제외·원본 바이트와 집계 분모를 보존한다."""

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from guard.adapters.git_diff import extract_git_change
from guard.collectors.change import extracted_input, save_change
from guard.core.report import build_report
from guard.core.change_selection import SelectionContext
from guard.core.select import select
from guard.models import Mode
from guard.store import Store
from tests.builders import T0, baseline_pack, environment, policy


RAW = b"# original\r\nraise RuntimeError('never import source')\r\n"


def _source(tmp_path, before, after, raw=RAW):
    repo = tmp_path / "source"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout
    git("init")
    git("config", "user.name", "EXAMPLE source fixture")
    git("config", "user.email", "example@example.invalid")
    git("config", "core.autocrlf", "false")
    first = repo / before
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(raw)
    git("add", ".")
    git("commit", "-m", "before")
    git("tag", "v1.0.0")
    last = repo / after
    last.parent.mkdir(parents=True, exist_ok=True)
    if first == last:
        last.write_bytes(raw + b"# changed\r\n")
    else:
        first.rename(last)
    git("add", "-A")
    git("commit", "-m", "after")
    git("tag", "v1.0.1")
    rules = tmp_path / "rules.yaml"
    rules.write_text("exclude:\n  prefixes: [docs/]\nrules:\n"
                     "  - {prefix: known/, categories: [API_ROUTE]}\n"
                     "  - {prefix: vllm/entrypoints/, categories: [API_ROUTE]}\n"
                     "  - {prefix: cache/, categories: [CACHE]}\n"
                     "  - {prefix: parser/, categories: [OUTPUT_PARSING]}\n", encoding="utf-8")
    return extract_git_change(repo, "v1.0.0", "v1.0.1", rules_path=rules, pack=baseline_pack())


@pytest.mark.parametrize("before,after,unmapped,excluded,categories", [
    ("known/a.py", "unknown/b.py", {"unknown/b.py"}, set(), {"API_ROUTE"}),
    ("unknown/a.py", "known/b.py", {"unknown/a.py"}, set(), {"API_ROUTE"}),
    ("unknown/a.py", "other/b.py", {"unknown/a.py", "other/b.py"}, set(), set()),
    ("known/a.py", "cache/b.py", set(), set(), {"API_ROUTE", "CACHE"}),
    ("known/a.py", "docs/b.md", set(), {"docs/b.md"}, {"API_ROUTE"}),
    ("docs/a.md", "known/b.py", set(), {"docs/a.md"}, {"API_ROUTE"}),
    ("docs/a.md", "unknown/b.py", {"unknown/b.py"}, {"docs/a.md"}, set()),
    ("docs/a.md", "docs/b.md", set(), {"docs/a.md", "docs/b.md"}, set()),
    ("parser/a.py", "parser/b.py", set(), set(), {"OUTPUT_PARSING"}),
])
def test_each_rename_side_keeps_its_classification_and_hash(tmp_path, before, after, unmapped, excluded, categories):
    extracted = _source(tmp_path, before, after)
    artifact = json.loads(extracted.artifact_bytes)
    record, = artifact["changes"] + artifact["excluded"]
    assert artifact["schema"] == "guard.engine-source-diff/2"
    assert extracted.change.extractor.version == "0.2.0"
    assert record["status"] == "R100"
    assert record["before_sha256"] == record["after_sha256"] == hashlib.sha256(RAW).hexdigest()
    assert set(record["categories"]) == categories
    assert record["unmapped"] is bool(unmapped)
    by_side = {p["side"]: p for p in record["path_classifications"]}
    assert by_side["baseline"]["path"] == before and by_side["candidate"]["path"] == after
    assert {p["path"] for p in by_side.values() if p["unmapped"]} == unmapped
    assert {p["path"] for p in by_side.values() if p["excluded"]} == excluded
    analysis = extracted.change.analysis
    assert set(analysis["unmapped_files"]) == unmapped
    assert analysis["counts"]["classified"] == analysis["counts"]["non_excluded"] == int(len(excluded) != 2)
    assert analysis["counts"]["recognized"] + analysis["counts"]["unmapped"] == analysis["counts"]["non_excluded"]
    assert analysis["counts"]["partially_recognized"] == int(bool(unmapped and categories))
    assert analysis["path_counts"] == dict(changed_paths=2, excluded=len(excluded), non_excluded=2-len(excluded),
                                          recognized=2-len(excluded)-len(unmapped), unmapped=len(unmapped))
    assert {fact.category for fact in extracted.change.facts} == categories | ({"OTHER"} if unmapped else set())
    for fact in extracted.change.facts:
        assert fact.unmapped is (fact.category == "OTHER")
        assert fact.certainty == ("UNKNOWN" if fact.unmapped else "PARTIAL")
        if fact.category == "OTHER":
            assert fact.target in unmapped
        assert fact.target not in excluded
    if categories == {"OUTPUT_PARSING"}:
        assert analysis["checklist_gap_candidates"] == [before, after]
    assert not any(path in values for path in excluded for values in analysis["category_files"].values())


def test_both_unmapped_paths_survive_json_and_cas_without_execution(tmp_path):
    extracted = _source(tmp_path, "unknown/a.py", "other/b.py")
    pack = baseline_pack()
    env, pol = environment(), policy(pack.ref(), scope={"include": ["REQ-01"]})
    store = Store(tmp_path / "store")
    store.put_many([env, pol])
    change = save_change(store, extracted_input(extracted, pack))
    plan = select(pack, [env], pol, mode=Mode.audit, change=change,
                  context=SelectionContext("INITIAL", full_scope=True))
    store.put(plan)
    report = build_report(store, pack, plan, T0)
    unknown, = report.change_summary["unmapped"]
    assert unknown["paths"] == ["other/b.py", "unknown/a.py"]
    category, = report.change_summary["categories"]
    assert category["paths"] == unknown["paths"] and category["file_count"] == 2
    saved = report.to_dict()["change_summary"]
    assert saved["unmapped"][0]["paths"] == ["other/b.py", "unknown/a.py"]
    assert saved["categories"][0]["paths"] == saved["unmapped"][0]["paths"]
    assert report.official is False
    assert list(store.iter_kind("EVD")) == [] and list(store.iter_kind("RES")) == []
    assert not store.verify()


def test_modification_of_one_path_counts_once_but_hashes_both_sides(tmp_path):
    extracted = _source(tmp_path, "known/one.py", "known/one.py")
    record, = json.loads(extracted.artifact_bytes)["changes"]
    assert record["status"] == "M"
    assert record["before_sha256"] == hashlib.sha256(RAW).hexdigest()
    assert record["after_sha256"] == hashlib.sha256(RAW + b"# changed\r\n").hexdigest()
    assert len(record["path_classifications"]) == 2
    assert extracted.change.analysis["path_counts"] == dict(changed_paths=1, excluded=0,
        non_excluded=1, recognized=1, unmapped=0)


def test_route_provenance_keeps_both_rename_paths_and_source_sides(tmp_path):
    extracted = _source(tmp_path, "vllm/entrypoints/a.py", "vllm/entrypoints/b.py",
                        b"@router.get('/same')\ndef route(): pass\n")
    pack = baseline_pack()
    env, pol = environment(), policy(pack.ref(), scope={"include": ["REQ-01"]})
    store = Store(tmp_path / "store")
    store.put_many([env, pol])
    change = save_change(store, extracted_input(extracted, pack))
    plan = select(pack, [env], pol, change=change, context=SelectionContext("INITIAL", full_scope=True))
    store.put(plan)
    entries = build_report(store, pack, plan, T0).change_summary["entrypoints"]
    assert {(e["source_path"], e["source_side"]) for e in entries} == {
        ("vllm/entrypoints/a.py", "baseline"), ("vllm/entrypoints/b.py", "candidate")}
    assert all(stage["observed"] is not True for entry in entries for stage in entry["stages"])


@pytest.mark.parametrize("metadata", ["not-a-list", [{"path": "incomplete"}], [None, 7]])
def test_incomplete_optional_manual_metadata_keeps_legacy_report(tmp_path, metadata):
    from tests.test_update_selection_report import _setup, _with_change
    store, pack, _, _, initial = _setup(tmp_path)
    plan = _with_change(store, pack, initial)
    change = store.resolve(plan.inputs.change)
    facts = [change.facts[0].model_copy(update={"details": {
        **change.facts[0].details, "path_classifications": metadata}}), *change.facts[1:]]
    newer = change.model_copy(update={"revision": 2, "facts": facts,
                                      "analysis": {"path_counts": {"incomplete": 1}}}).seal()
    store.put(newer)
    current = plan.model_copy(update={"revision": 2,
        "inputs": plan.inputs.model_copy(update={"change": newer.ref()}), "refs": [*plan.refs, newer.ref()]}).seal()
    store.put(current)
    report = build_report(store, pack, current, T0)
    assert report.change_summary["entrypoints"][0]["source_path"] == "entrypoint/server.py"
    summary = report.to_dict()["change_summary"]
    entry = summary["entrypoints"][0]
    assert entry["source_path"] == "entrypoint/server.py"
    assert all("paths" not in fact for fact in summary["unmapped"])
