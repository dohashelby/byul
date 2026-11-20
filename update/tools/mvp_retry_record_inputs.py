"""Materialize paired LIF-12 declarations from pinned, already selected local inputs."""

import argparse
from pathlib import Path

from guard.core.execution_recipe import validate_policy_time
from guard.core.select import _in_scope
from guard.executors import retry_record_contract as retry, run_binding
from guard.executors.owned_capture import MAX_BYTES
from guard.models import Environment, Policy, canonical_json, load_pack, sha256_hex
from guard.runtime.bounded import strict_json
from guard.update_validation import _plain_path, reserve_output
from tools.mvp_a_input_preflight import _read
from tools.mvp_metadata_wiring import _source, _transport, readiness, versions

CHECK = "LIF-12"
SELECTION_SCHEMA = "guard-mvp-retry-record-selection/1"
_IDENTITY = {"producer_id", "producer_code_sha256", "configuration_sha256", "native_source_sha256", "subjects"}
_SELECTION_FIELDS = {"environment_ref", "target_sha256", "run_id", "record_session", "record_producer"} | _IDENTITY
_POLICY_FIELDS = {retry.DECISION_KEY, "retry_policy_mode", "retry_max_record_delay_ms"}
_OUTPUT = "retry-record-inputs.json"


def prepare(inputs, policy_path, selection_path, expected_selection_sha256, output, *, pack):
    """Write two validated scope declarations and the existing assembler's input map."""
    policy = Policy.model_validate(_source(_plain_path(Path(policy_path)))[0])
    check = pack.checks[CHECK]
    values = policy.values.get(CHECK)
    if (not policy.hash_ok() or policy.pack != pack.ref() or policy.status != "CONFIRMED"
            or policy.data_origin == "EXAMPLE" or not _in_scope(CHECK, policy)
            or check.executor != "request_termination" or check.collection_profile != retry.PROFILE
            or run_binding.stages_for(check) != (1, 2, 3, 4)
            or type(values) is not dict or values.keys() != _POLICY_FIELDS
            or type(values["retry_policy_mode"]) is not str or values["retry_policy_mode"] not in retry.MODES
            or not retry.uint(values["retry_max_record_delay_ms"], 600000)):
        raise ValueError("MVP_RETRY_CONFIRMED_POLICY_REQUIRED")
    validate_policy_time(policy)
    limits = retry.validate_limits(values[retry.DECISION_KEY])
    policy_hash = run_binding.policy_document_sha256(CHECK, "retry_policy_sha256", values)

    raw, _ = _read(_plain_path(Path(selection_path)), max_bytes=2 * MAX_BYTES)
    if not retry.fingerprint(expected_selection_sha256) or sha256_hex(raw) != expected_selection_sha256:
        raise ValueError("MVP_RETRY_SELECTION_CHANGED")
    selected = strict_json(raw, limit=2 * MAX_BYTES)
    sides = set(readiness.SIDES)
    if (type(selected) is not dict or selected.keys() != {"schema", "policy_ref", "sides"}
            or selected["schema"] != SELECTION_SCHEMA or selected["policy_ref"] != policy.ref().model_dump(mode="json")
            or type(selected["sides"]) is not dict or selected["sides"].keys() != sides
            or type(inputs) is not dict or inputs.keys() != sides):
        raise ValueError("MVP_RETRY_SELECTION_INVALID")

    protected = [readiness.ROOT, Path(policy_path), Path(selection_path)]
    bindings, scopes = {}, {}
    releases = versions()
    for side in readiness.SIDES:
        declaration, paths = selected["sides"][side], inputs[side]
        if (type(declaration) is not dict or declaration.keys() != _SELECTION_FIELDS
                or not retry.opaque(declaration["record_session"])
                or type(paths) is not dict or paths.keys() != {"environment", "target"}):
            raise ValueError("MVP_RETRY_SELECTION_INVALID")
        env_path, target_path = (_plain_path(Path(paths[key])) for key in ("environment", "target"))
        env = Environment.model_validate(_source(env_path)[0])
        engine = releases[side]
        if (not env.hash_ok() or env.data_origin == "EXAMPLE"
                or declaration["environment_ref"] != env.ref().model_dump(mode="json")
                or (env.engine.name, env.engine.version, env.engine.image_digest)
                != (engine["engine"], engine["version"], engine["image"])):
            raise ValueError("MVP_RETRY_ENVIRONMENT_MISMATCH")
        target = strict_json(_read(target_path, max_bytes=65536)[0], limit=65536)
        transport = _transport(target, env)
        if (declaration["target_sha256"] != transport.target_sha256
                or declaration["run_id"] != transport.binding["run_id"]):
            raise ValueError("MVP_RETRY_TARGET_MISMATCH")

        producer = declaration["record_producer"]
        if (type(producer) is not dict or producer.keys() != {"path", "sha256"}
                or type(producer["path"]) is not str or not producer["path"] or not retry.fingerprint(producer["sha256"])):
            raise ValueError("MVP_RETRY_PRODUCER_PIN_INVALID")
        producer_path = _plain_path(Path(producer["path"]))
        producer_raw, _ = _read(producer_path, max_bytes=run_binding._MAX_PRODUCER_BYTES)
        if sha256_hex(producer_raw) != producer["sha256"]:
            raise ValueError("MVP_RETRY_PRODUCER_SOURCE_CHANGED")
        scope_fields = {
            "policy_sha256": policy_hash, "producer_source_sha256": producer["sha256"],
            "image_digest": env.engine.image_digest.rsplit("@", 1)[-1], "model_revision": env.model.revision,
            "model_inventory_sha256": env.model.inventory_sha256,
        }
        scope = {"schema": retry.SCOPE_SCHEMA, "run_id": transport.binding["run_id"], "limits": limits,
                 **scope_fields, **{key: declaration[key] for key in _IDENTITY}}
        retry.validate_scope(scope, policy_sha256=policy_hash, source_sha256=producer["sha256"],
            image_digest=scope_fields["image_digest"], model_revision=env.model.revision,
            model_inventory_sha256=env.model.inventory_sha256)
        scopes[side] = canonical_json(scope)
        if len(scopes[side]) > MAX_BYTES:
            raise ValueError("MVP_RETRY_SCOPE_TOO_LARGE")
        bindings[side] = {"record_session": declaration["record_session"], "record_producer": str(producer_path),
                          retry.TARGET_BINDING_KEY: {
                              "environment_ref": env.ref().model_dump(mode="json"),
                              "target_sha256": transport.target_sha256}}
        protected.extend((env_path, target_path, producer_path))

    directory = reserve_output(Path(output), protected=protected)
    directory.chmod(0o700)
    for side, scope in scopes.items():
        path = directory / f"{side}-retry-scope.json"
        with path.open("xb") as stream:
            if stream.write(scope) != len(scope):
                raise OSError("MVP_RETRY_OUTPUT_INCOMPLETE")
        bindings[side]["retry_scope"] = str(path)
    raw = canonical_json(bindings)
    with (directory / _OUTPUT).open("xb") as stream:
        if stream.write(raw) != len(raw):
            raise OSError("MVP_RETRY_OUTPUT_INCOMPLETE")
    return bindings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=readiness.ROOT / "packs/PACK-BASELINE")
    parser.add_argument("--policy", type=Path, required=True, help="Confirmed sealed policy with LIF-12 limits")
    parser.add_argument("--selection", type=Path, required=True,
        help="Pinned JSON with policy_ref and each side's environment_ref, target_sha256, run_id, record_session, "
             "record_producer (path and SHA256), producer identity, configuration and native-source SHA256, and subjects")
    parser.add_argument("--selection-sha256", required=True, help="Independent SHA256 of the exact selection file bytes")
    parser.add_argument("--output", type=Path, required=True,
        help="New directory for the scopes and retry-record-inputs.json with saved owner bindings")
    for side in readiness.SIDES:
        for role in ("environment", "target"):
            parser.add_argument(f"--{side}-{role}", type=Path, required=True,
                help=f"Saved {side} {role} JSON matching the pinned selection")
    args = parser.parse_args(argv)
    inputs = {side: {role: getattr(args, f"{side}_{role}") for role in ("environment", "target")}
              for side in readiness.SIDES}
    prepare(inputs, args.policy, args.selection, args.selection_sha256, args.output, pack=load_pack(args.pack))
    print(args.output / _OUTPUT)


if __name__ == "__main__":
    main()
