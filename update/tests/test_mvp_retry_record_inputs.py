"""Exercise offline LIF-12 input derivation with the existing paired assembler."""

import json
from pathlib import Path
import socket
import subprocess

import pytest

from guard.executors.retry_record_contract import TARGET_BINDING_KEY
from guard.models import Environment, Policy, canonical_json, sha256_hex
from tests.test_a1_retry_record_contract import example as retry_example
from tests.test_mvp_metadata_wiring import sources
from tools import mvp_metadata_wiring as wiring
from tools import mvp_retry_record_inputs as retry_inputs


@pytest.mark.parametrize("damage,reason", [
    (None, None),
    ("selection-pin", "MVP_RETRY_SELECTION_CHANGED"),
    ("foreign-env", "MVP_RETRY_ENVIRONMENT_MISMATCH"),
    ("changed-target", "MVP_RETRY_TARGET_MISMATCH"),
    ("wrong-run", "MVP_RETRY_TARGET_MISMATCH"),
    ("changed-source", "MVP_RETRY_PRODUCER_SOURCE_CHANGED"),
    ("late-source", "MVP_RETRY_INPUT_SOURCE_CHANGED"),
])
def test_materialized_retry_scopes_join_both_sides_without_collection(tmp_path, monkeypatch, sources, damage, reason):
    def forbidden(*args, **kwargs):
        pytest.fail("Retry input materialization must not contact an engine or execute a producer")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    pack, paths, policy_path = sources
    base = Policy.model_validate_json(policy_path.read_bytes())
    scope, template_policy, _, _ = retry_example()
    values = {key: template_policy[key] for key in ("retry_policy_mode", "retry_max_record_delay_ms")}
    values["retry_limits"] = scope["limits"]
    policy = base.model_copy(update={"values": {"LIF-12": values},
        "scope": base.scope.model_copy(update={"include": ["LIF-12"]})}).seal()
    policy_path.write_bytes(canonical_json(policy.to_dict()))

    declarations = {}
    for index, (side, files) in enumerate(paths.items(), 1):
        env = Environment.model_validate_json(files["environment"].read_bytes())
        target = json.loads(files["target"].read_bytes())
        producer_path = tmp_path / f"{side}-producer.txt"
        producer_path.write_bytes(b"# inert example file, inspected as bytes only\n")
        declarations[side] = {
            "environment_ref": env.ref().model_dump(mode="json"),
            "target_sha256": sha256_hex(canonical_json(target)),
            "run_id": target["kubernetes"]["run_id"],
            "record_session": f"{index:032x}",
            "record_producer": {"path": str(producer_path), "sha256": sha256_hex(producer_path.read_bytes())},
            "subjects": [item for item in scope["subjects"] if item["scenario"] == "NORMAL"],
            **{key: scope[key] for key in ("producer_id", "producer_code_sha256", "configuration_sha256",
                                           "native_source_sha256")},
        }

    candidate = declarations["candidate"]
    if damage == "foreign-env":
        candidate["environment_ref"] = declarations["baseline"]["environment_ref"]
    elif damage == "changed-target":
        target_path = paths["candidate"]["target"]
        target_path.write_bytes(canonical_json({**json.loads(target_path.read_bytes()), "observed_version": "stale"}))
    elif damage == "wrong-run":
        candidate["run_id"] = "different-example-run"
    elif damage == "changed-source":
        Path(candidate["record_producer"]["path"]).write_bytes(b"# different inert file bytes\n")
    elif damage == "late-source":
        original_validate = retry_inputs.retry.validate_scope
        baseline_image = wiring.versions()["baseline"]["image"].rsplit("@", 1)[1]
        producer = Path(declarations["baseline"]["record_producer"]["path"])

        def replace_after_validating(scope, **binding):
            checked = original_validate(scope, **binding)
            if checked["image_digest"] == baseline_image:
                producer.write_bytes(b"# changed after scope validation\n")
            return checked

        monkeypatch.setattr(retry_inputs.retry, "validate_scope", replace_after_validating)

    selection = tmp_path / "selection.json"
    selection.write_bytes(canonical_json({"schema": retry_inputs.SELECTION_SCHEMA,
        "policy_ref": policy.ref().model_dump(mode="json"), "sides": declarations}))
    pin = "0" * 64 if damage == "selection-pin" else sha256_hex(selection.read_bytes())
    output = tmp_path / "retry-inputs"
    if reason:
        with pytest.raises(ValueError, match=f"^{reason}$"):
            retry_inputs.prepare(paths, policy_path, selection, pin, output, pack=pack)
        assert not output.exists()
        return

    materialized = retry_inputs.prepare(paths, policy_path, selection, pin, output, pack=pack)
    published = json.loads((output / "retry-record-inputs.json").read_bytes())
    assert published == materialized
    config, bindings, decisions = wiring.assemble(paths, policy_path, pack=pack, store=tmp_path / "store",
                                                  retry_record_inputs=published)
    assert config["update"]["allowed_checks"] == ["LIF-12"]
    assert decisions["LIF-12"]["configuration_enabled"]
    for side, item in published.items():
        assert item["record_session"] == declarations[side]["record_session"]
        binding = {key: declarations[side][key] for key in ("environment_ref", "target_sha256")}
        assert item[TARGET_BINDING_KEY] == binding
        assert config["sides"][side]["check_config"]["LIF-12"][TARGET_BINDING_KEY] == binding
        scope_raw = Path(item["retry_scope"]).read_bytes()
        emitted = json.loads(scope_raw)
        assert item["input_sha256"] == {"retry_scope_sha256": sha256_hex(scope_raw),
                                         "retry_record_source_sha256": declarations[side]["record_producer"]["sha256"]}
        assert "input_sha256" not in config["sides"][side]["run_binding"]["LIF-12"]
        assert emitted["subjects"] == declarations[side]["subjects"]
        assert emitted["producer_source_sha256"] == declarations[side]["record_producer"]["sha256"]
        assert emitted["policy_sha256"] == sha256_hex(canonical_json(values))
        assert emitted["limits"] == values["retry_limits"]

    assembly = tmp_path / "prebound"
    assembly.mkdir()
    wiring._publish_prebound(assembly, config=config, bindings=bindings, checks=decisions, pack=pack,
                             preparations=None, record_checks={"LIF-12"})
    audited = wiring.readiness.audit(assembly / "input-sources.json", pack=pack)
    assert audited["file_binding_valid_sides"] == 2
    assert {row["side"]: row["record_inputs"] for row in audited["rows"] if row["check_id"] == "LIF-12"} == (
        decisions["LIF-12"]["prepared_record_input_sha256_by_side"])

    target_path = paths["candidate"]["target"]
    target = json.loads(target_path.read_bytes())
    target["kubernetes"]["probe"]["pod_uid"] = "2187f050-0a54-41ea-8c12-f03c406400cc"
    target_path.write_bytes(canonical_json(target))
    config, _, decisions = wiring.assemble(paths, policy_path, pack=pack, store=tmp_path / "store",
                                           retry_record_inputs=published)
    assert config["update"]["allowed_checks"] == []
    assert decisions["LIF-12"] == {
        "configuration_enabled": False, "sides": {"baseline": None, "candidate": "RETRY_CONFIGURATION_INVALID"}}
