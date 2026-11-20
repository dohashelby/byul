"""Exercise offline LIF-12 input derivation with the existing paired assembler."""

import json
from pathlib import Path
import socket
import subprocess

import pytest

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
    config, _, decisions = wiring.assemble(paths, policy_path, pack=pack, store=tmp_path / "store",
                                           retry_record_inputs=published)
    assert config["update"]["allowed_checks"] == ["LIF-12"]
    assert decisions["LIF-12"]["configuration_enabled"]
    for side, item in published.items():
        assert item["record_session"] == declarations[side]["record_session"]
        emitted = json.loads(Path(item["retry_scope"]).read_bytes())
        assert emitted["subjects"] == declarations[side]["subjects"]
        assert emitted["producer_source_sha256"] == declarations[side]["record_producer"]["sha256"]
        assert emitted["policy_sha256"] == sha256_hex(canonical_json(values))
        assert emitted["limits"] == values["retry_limits"]
