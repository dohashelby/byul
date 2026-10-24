"""Resolved fixture bytes and the sealed hash must agree; legacy is untouched."""
import json

import pytest

from guard.models import canonical_json, sha256_hex
from guard.validation_evidence import _verify_fixed_route_fixture


@pytest.mark.parametrize("name,version", [("direct_route", "0.7.0"), ("role_route", "0.5.0")])
def test_saved_resolved_fixture_is_tied_to_its_original_evidence(tmp_path, name, version):
    fixture = {"protocol": "fixed-route-fixture/1", "requests": {"GET /example": {"headers": {}, "body": None}}}
    path = tmp_path / "probe"
    path.write_bytes((json.dumps({"summary": True, "fixture": fixture}) + "\n").encode())
    obj = {"kind": "EVD", "execution": {"status": "COMPLETED", "executor": {"name": name, "version": version}},
           "artifacts": [{"name": "probe_log.jsonl", "path": path.name}],
           "comparability": {"fixture_sha256": sha256_hex(canonical_json(fixture))}}
    _verify_fixed_route_fixture(tmp_path, obj)
    obj["comparability"]["fixture_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="input hash differ"):
        _verify_fixed_route_fixture(tmp_path, obj)
    fixture["requests"]["GET /example"]["headers"]["X-Changed"] = "EXAMPLE"
    path.write_text(json.dumps({"summary": True, "fixture": fixture}), encoding="utf-8")
    with pytest.raises(ValueError, match="input hash differ"):
        _verify_fixed_route_fixture(tmp_path, obj)


@pytest.mark.parametrize("artifact", [[], [{"name": "other", "path": "unused"}]])
def test_new_completed_collector_requires_the_actual_fixture_attachment(tmp_path, artifact):
    obj = {"kind": "EVD", "execution": {"status": "COMPLETED",
           "executor": {"name": "direct_route", "version": "0.7.0"}}, "artifacts": artifact}
    with pytest.raises(ValueError, match="one original probe log"):
        _verify_fixed_route_fixture(tmp_path, obj)


@pytest.mark.parametrize("executor", [None, {"name": "direct_route", "version": "0.6.0"},
                                    {"name": "role_route", "version": "0.4.0"}])
def test_legacy_hashes_and_missing_manual_executor_are_not_reinterpreted(tmp_path, executor):
    _verify_fixed_route_fixture(tmp_path, {"kind": "EVD", "execution": {"status": "COMPLETED", "executor": executor}})
