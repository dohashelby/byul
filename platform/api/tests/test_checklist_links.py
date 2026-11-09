import hashlib
from pathlib import Path
import yaml

from byul_platform.config import REPO_ROOT
from byul_platform.sources.checklist_data import CHECKS
from byul_platform.sources.checklist_links import checks_for_record, coverage, evidence_refs


def test_all_selected_links_match_pack_and_do_not_add_exclusions():
    pack = REPO_ROOT / "update/packs/PACK-BASELINE"
    index = yaml.safe_load((pack / "id_map.yaml").read_text())
    mapping = {r["framework_id"]: r for area in index.values() for r in area}
    assert len(CHECKS) == len({r["framework_id"] for r in CHECKS}) == 37
    assert len(mapping.keys() - {r["framework_id"] for r in CHECKS}) == 40
    for row in CHECKS:
        link = row["update"]
        assert mapping[row["framework_id"]]["id"] == link["id"]
        raw = (pack / "checks" / (link["id"] + ".yaml")).read_bytes()
        check = yaml.safe_load(raw)
        assert check["framework_id"] == row["framework_id"]
        assert hashlib.sha256(raw).hexdigest() == link["sha256"]
        assert (check["revision"], check["executor"], check["retired"]) == (link["revision"], link["executor"], link["retired"])
    assert {r["update"]["id"] for r in CHECKS if r["update"]["retired"]} == {"OBS-12", "SUP-14", "LIF-15"}


def test_mapping_is_source_specific_and_does_not_make_verdicts():
    assert checks_for_record({"decision": {"rule_id": "authentication"}}, "gateway") == ["OBS-01", "REQ-04"]
    assert checks_for_record({"rule_id": "TG-SECRET-01"}, "tetragon") == ["PLT-04"]
    assert checks_for_record({"rule_id": "TG-SECRET-01"}, "gateway") == ["OBS-01"]
    assert checks_for_record({"related_checks": ["CHK-SUP-001", "SUP-01", "garbage"]}, "unknown") == []
    assert checks_for_record({"related_checks": ["CHK-REQ-004", "REQ-04"]}, "unknown") == ["REQ-04"]
    report = coverage([{"related_checks": ["REQ-04"], "outcome": "denied", "data_origin": "MEASURED"}])
    row = next(r for r in report["items"] if r["framework_id"] == "CHK-REQ-004")
    assert row["observed_events"] == 1 and row["observation_state"] == "observed"
    assert not any(k in row for k in ("verdict", "pass", "approved"))
    assert sum(r["observed_events"] for r in report["items"]) == 1


def test_refs_keep_exact_hash_and_drop_malformed_values():
    ref = {"evidence_id": "EVD-1@3", "sha256": "a" * 64}
    assert evidence_refs({"evidence_refs": [ref, ref, {"evidence_id": "x", "sha256": "oops"}, {"evidence_id": ""}, "x"]}) == [ref]
    assert evidence_refs({"evidence_refs": "secret"}) == []


def test_endpoint_reports_every_row_even_without_events(example_client):
    response = example_client.get("/api/runtime/checks")
    assert response.status_code == 200
    result = response.json()
    assert result["selected_count"] == len(result["items"]) == 37
    assert result["excluded_count"] == 40 and result["pack_revision"] == 33
    assert all("condition" in r and "limitation" in r and "observation_state" in r for r in result["items"])


def test_coverage_counts_and_windows_come_from_one_snapshot(example_client):
    import json
    from byul_platform.config import DEFAULT_CONTRACTS_DIR
    from byul_platform.sources.runtime import EventBatch
    event = json.loads((DEFAULT_CONTRACTS_DIR / "examples/runtime-events.jsonl").read_text().splitlines()[0])
    event["related_checks"] = ["REQ-04"]
    window = {"gateway": {"returned": 1, "total": 9, "limit": 1, "truncated": True}}
    calls = []
    def events():
        calls.append(1)
        return EventBatch([event], truncated=True, source_windows=window)
    example_client.app.state.byul.runtime.events = events
    result = example_client.get("/api/runtime/checks").json()
    assert len(calls) == 1 and result["source_windows"] == window
    assert next(r for r in result["items"] if r["update"]["id"] == "REQ-04")["observed_events"] == 1
