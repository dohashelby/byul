"""Materialize PLT-12 copy declarations under the approved owned-lab policy."""

from __future__ import annotations

import argparse
from datetime import timedelta, timezone
from pathlib import Path

from guard.executors import credential_copy_contract as copy_contract, run_binding
from guard.models import DataOrigin, Environment, Policy, canonical_json, load_pack, sha256_hex
from guard.runtime.bounded import strict_json
from tools.mvp_a_input_preflight import _read
from tools.mvp_runtime_policy import ROOT, POLICY_MAX_BYTES, approved_rules, fill_runtime_values, seed_run_store
from tools.mvp_versions import versions

SELECTION_SCHEMA = 'guard-mvp-credential-copy-selection/1'
CHECK = 'PLT-12'
COPY_IDENTITY_FIELDS = copy_contract.COPY_FIELDS.difference(copy_contract.DECISION_FIELDS)


def _approved_copy_rule(policy, pack):
    rules = approved_rules(policy, pack, CHECK)
    rule = rules.get(copy_contract.DECISION_KEY)
    if (rule != {'rule': 'delete_all_owned_canary_credential_copies', 'deadline_from_start_ms': 30000,
                 'retention_exception': None, 'require_bounded_owned_inventory': True}):
        raise ValueError('COPY_MATERIALIZATION_RULE_UNSUPPORTED')
    return rule


def materialize(policy, pack, environments, selection):
    """Return an exact policy revision and inventory for explicitly declared owned copies."""
    rule = _approved_copy_rule(policy, pack)
    expected = versions()
    if (type(selection) is not dict or set(selection) != {'schema', 'base_policy_ref', 'environments',
            'started_at', 'credential_family_id', 'copies'} or selection['schema'] != SELECTION_SCHEMA
            or selection['base_policy_ref'] != policy.ref().model_dump(mode='json')
            or set(environments) != set(expected) or type(selection['environments']) is not dict
            or set(selection['environments']) != set(expected)):
        raise ValueError('COPY_SELECTION_BINDING_INVALID')
    for side, env in environments.items():
        engine = expected[side]
        if (not env.hash_ok() or env.data_origin == DataOrigin.EXAMPLE
                or (env.engine.name, env.engine.version, env.engine.image_digest) != (
                    engine['engine'], engine['version'], engine['image'])
                or selection['environments'][side] != env.ref().model_dump(mode='json')):
            raise ValueError('COPY_SELECTION_ENVIRONMENT_MISMATCH')
    if environments['baseline'].ref() == environments['candidate'].ref():
        raise ValueError('COPY_SELECTION_ENVIRONMENT_MISMATCH')

    start = copy_contract.stamp(selection['started_at']).astimezone(timezone.utc)
    deadline = start + timedelta(milliseconds=rule['deadline_from_start_ms'])
    if ((policy.valid_from is not None and start < policy.valid_from)
            or (policy.valid_until is not None and deadline > policy.valid_until)):
        raise ValueError('COPY_SELECTION_OUTSIDE_POLICY_VALIDITY')
    copies = selection['copies']
    if (type(copies) is not list or not 1 <= len(copies) <= copy_contract.MAX_COPIES
            or any(type(row) is not dict or set(row) != COPY_IDENTITY_FIELDS for row in copies)):
        raise ValueError('COPY_SELECTION_INVENTORY_INVALID')
    rows = [{**row, 'required_action': 'delete', 'deadline_at': deadline.isoformat(),
             'retention_exception': rule['retention_exception']} for row in copies]
    inventory = {'schema': copy_contract.INVENTORY_SCHEMA, 'credential_family_id': selection['credential_family_id'],
                 'copies': rows}
    # Validate membership shape before using copy IDs as dict keys in the policy.
    copy_contract.validate_inventory({**inventory, 'policy_sha256': policy.ref().sha256},
                                     expected_policy_sha256=policy.ref().sha256)
    treatments = copy_contract.treatments(inventory)
    policy = fill_runtime_values(policy, CHECK, {copy_contract.DECISION_KEY: treatments})
    inventory['policy_sha256'] = run_binding.policy_document_sha256(CHECK, 'copy_policy_sha256', policy.values[CHECK])
    copy_contract.validate_inventory(inventory, expected_policy_sha256=inventory['policy_sha256'])
    return policy, inventory


def prepare(policy_path, environment_paths, selection_path, expected_selection_sha256, output, *, store=None):
    policy_raw, _ = _read(policy_path)
    policy = Policy.model_validate(strict_json(policy_raw, limit=POLICY_MAX_BYTES))
    selection_raw, _ = _read(selection_path)
    selection_sha256 = sha256_hex(selection_raw)
    if selection_sha256 != expected_selection_sha256:
        raise ValueError('COPY_SELECTION_SOURCE_CHANGED')
    selection = strict_json(selection_raw)
    environments = {side: Environment.model_validate(strict_json(_read(path)[0]))
                    for side, path in environment_paths.items()}
    pack = load_pack(ROOT / 'packs/PACK-BASELINE')
    bound_policy, inventory = materialize(policy, pack, environments, selection)
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    bound_policy = seed_run_store(store, policy, bound_policy)
    outputs = {'policy.json': canonical_json(bound_policy.to_dict()), 'copy-inventory.json': canonical_json(inventory)}
    manifest = {
        'base_policy_ref': policy.ref().model_dump(mode='json'),
        'policy_ref': bound_policy.ref().model_dump(mode='json'),
        'selection_sha256': selection_sha256,
        'environments': selection['environments'],
        'files': {name: sha256_hex(raw) for name, raw in outputs.items()},
        'data_origin': 'DECLARED', 'actual_membership_verified': False, 'treatment_observed': False,
    }
    output.mkdir(exist_ok=False)
    for name, raw in outputs.items():
        with (output / name).open('xb') as stream:
            stream.write(raw)
    with (output / 'manifest.json').open('xb') as stream:
        stream.write(canonical_json(manifest))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--baseline-environment', type=Path, required=True)
    parser.add_argument('--candidate-environment', type=Path, required=True)
    parser.add_argument('--selection', type=Path, required=True, help='Owned, non-secret copy IDs and actual start time')
    parser.add_argument('--selection-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--store', type=Path, help='Append the exact base and new POL to the prepared Guard run store')
    args = parser.parse_args()
    prepare(args.policy, {'baseline': args.baseline_environment, 'candidate': args.candidate_environment},
            args.selection, args.selection_sha256, args.output, store=args.store)


if __name__ == '__main__':
    main()
