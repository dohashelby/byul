import hashlib
import json

from byul_platform.contracts import canonical_sha256
from byul_platform.sources.report_rules import archived_check, archived_manifest, explain_rules


def test_archived_definition_and_parameters_bind_display_to_the_recorded_check(tmp_path):
    check = {
        "id": "REQ-01", "satisfied_if": "정상 경로를 확인한다.",
        "judge": {"plugin": "rule_table", "params": {
            "pass_when_all": [{"obs": "outcomes", "op": "all_in", "value_policy": "allowed"}],
            "fail_when_any": [{"obs": "violations", "op": "nonempty"}],
        }},
    }

    def artifact(value):
        data = json.dumps(value).encode()
        sha = hashlib.sha256(data).hexdigest()
        (tmp_path / sha).write_bytes(data)
        return sha

    check_sha = artifact(check)
    pack_sha = artifact({"pack_id": "PACK-BASELINE", "revision": 39, "files_sha256": [
        {"path": "checks/REQ-01.yaml", "sha256": check_sha},
    ]})
    pack = {"kind": "PACK", "id": "PACK-BASELINE", "revision": 39, "sha256": pack_sha}
    files = archived_manifest(tmp_path, pack)
    archived = archived_check(tmp_path, files, "REQ-01")
    assert archived == check
    storage_result = {"judge": {"name": "rule_table", "version": "0.5.0",
                                "params_sha256": canonical_sha256(check["judge"]["params"])},
                      "verdicts": {"security": "PASS"}}
    evd = {"observations": {"outcomes": {"value": [200], "collected": True},
                            "violations": {"value": [], "collected": True}}}
    policy = {"values": {"REQ-01": {"allowed": [200]}}}
    assert explain_rules(archived, storage_result, evd, policy)["security"]["operators"] == {"outcomes": ["all_in"]}
    evd["observations"]["violations"]["value"] = ["recorded"]
    assert "security" not in explain_rules(archived, storage_result, evd, policy)
    storage_result["verdicts"]["security"] = "FAIL"
    assert explain_rules(archived, storage_result, evd, policy)["security"]["observations"] == ["violations"]
    evd["observations"]["violations"]["collected"] = False
    storage_result["verdicts"]["security"] = "UNKNOWN"
    trace = explain_rules(archived, storage_result, evd, {})["security"]
    assert trace["missing"] == ["violations"]
    assert trace["policy_missing"] == ["allowed"]
    storage_result["judge"]["params_sha256"] = "0" * 64
    assert explain_rules(archived, storage_result, evd, policy) == {}
    (tmp_path / check_sha).write_text("{}")
    assert archived_check(tmp_path, files, "REQ-01") == {}
