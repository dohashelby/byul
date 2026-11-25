"""Materialize PLT-12 copy declarations from an approved rule or an explicit EXAMPLE source."""

from __future__ import annotations

import argparse
from datetime import timedelta, timezone
from pathlib import Path

from guard.core.select import _in_scope
from guard.executors import credential_copy_contract as copy_contract, run_binding
from guard.models import DataOrigin, Environment, Policy, PolicyStatus, canonical_json, load_pack, sha256_hex
from guard.runtime.bounded import strict_json
from tools.mvp_a_input_preflight import _read
from tools.mvp_runtime_policy import ROOT, POLICY_MAX_BYTES, approved_rules, fill_runtime_values, seed_run_store
from tools.mvp_versions import versions

SELECTION_SCHEMA = 'guard-mvp-credential-copy-selection/1'
EXAMPLE_SOURCE_SCHEMA = 'byul-enterprise-mock-inputs/1'
CHECK = 'PLT-12'
COPY_IDENTITY_FIELDS = copy_contract.COPY_FIELDS.difference(copy_contract.DECISION_FIELDS)
COPY_RULE = {'rule': 'delete_all_owned_canary_credential_copies', 'deadline_from_start_ms': 30000,
             'retention_exception': None, 'require_bounded_owned_inventory': True}


def _approved_copy_rule(policy, pack):
    rules = approved_rules(policy, pack, CHECK)
    rule = rules.get(copy_contract.DECISION_KEY)
    if rule != COPY_RULE:
        raise ValueError('COPY_MATERIALIZATION_RULE_UNSUPPORTED')
    return rule


def _example_copy_rule(policy, pack, source):
    if (not policy.hash_ok() or policy.pack != pack.ref() or policy.status != PolicyStatus.DRAFT
            or policy.data_origin != DataOrigin.EXAMPLE or not _in_scope(CHECK, policy)
            or policy.confirmed_by is not None or policy.confirmed_at is not None):
        raise ValueError('COPY_EXAMPLE_POLICY_REQUIRED')
    if policy.source_document_sha256 != sha256_hex(source):
        raise ValueError('COPY_EXAMPLE_SOURCE_SHA256_MISMATCH')

    document = strict_json(source, limit=POLICY_MAX_BYTES)
    if (type(document) is not dict or document.get('schema') != EXAMPLE_SOURCE_SCHEMA
            or document.get('policy_status') != 'DRAFT' or document.get('data_origin') != 'EXAMPLE'
            or document.get('official') is not False
            or document.get('pack_ref') != pack.ref().model_dump(mode='json')):
        raise ValueError('COPY_EXAMPLE_SOURCE_INVALID')
    try:
        check = document['checks'][CHECK]
        rule = check['copy_rule']
        declared = check['policy_values']
        expected_selection = check['mock_enterprise_facts']['copy_selection']
        delay = declared['copy_max_record_delay_ms']
        placeholder = declared[copy_contract.DECISION_KEY]
        if (set(declared) != {'copy_max_record_delay_ms', copy_contract.DECISION_KEY}
                or type(delay) is not int or not 0 <= delay <= 600000 or type(placeholder) is not str
                or not placeholder.startswith('<') or not placeholder.endswith('>')
                or expected_selection['schema'] != SELECTION_SCHEMA):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError('COPY_EXAMPLE_SOURCE_INVALID') from None
    if canonical_json(rule) != canonical_json(COPY_RULE):
        raise ValueError('COPY_MATERIALIZATION_RULE_UNSUPPORTED')
    fixed = {key: value for key, value in policy.values.get(CHECK, {}).items() if key != copy_contract.DECISION_KEY}
    if canonical_json(fixed) != canonical_json({'copy_max_record_delay_ms': delay}):
        raise ValueError('COPY_EXAMPLE_POLICY_VALUES_MISMATCH')
    return COPY_RULE, expected_selection


def materialize(policy, pack, environments, selection, *, example_source=None):
    """Return an exact policy revision and inventory for explicitly declared owned copies."""
    expected_selection = None
    if example_source is None:
        rule = _approved_copy_rule(policy, pack)
    else:
        rule, expected_selection = _example_copy_rule(policy, pack, example_source)
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
    if expected_selection is not None:
        expected_copies = expected_selection.get('copies')
        if (selection['credential_family_id'] != expected_selection.get('credential_family_id')
                or type(expected_copies) is not list or len(expected_copies) != len(copies)
                or {canonical_json(row) for row in copies} != {canonical_json(row) for row in expected_copies}):
            raise ValueError('COPY_EXAMPLE_SELECTION_MISMATCH')
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


def prepare(policy_path, environment_paths, selection_path, expected_selection_sha256, output, *, store=None,
            example_source_path=None):
    sources = []

    def read(path):
        path = Path(path)
        inspected = _read(path)
        sources.append((path, inspected))
        return inspected[0]

    policy = Policy.model_validate(strict_json(read(policy_path), limit=POLICY_MAX_BYTES))
    selection_raw = read(selection_path)
    selection_sha256 = sha256_hex(selection_raw)
    if selection_sha256 != expected_selection_sha256:
        raise ValueError('COPY_SELECTION_SOURCE_CHANGED')
    selection = strict_json(selection_raw)
    environments = {side: Environment.model_validate(strict_json(read(path)))
                    for side, path in environment_paths.items()}
    pack = load_pack(ROOT / 'packs/PACK-BASELINE')
    if example_source_path is None:
        bound_policy, inventory = materialize(policy, pack, environments, selection)
    else:
        bound_policy, inventory = materialize(policy, pack, environments, selection,
                                              example_source=read(example_source_path))
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    for path, inspected in sources:
        try:
            unchanged = _read(path) == inspected
        except (OSError, ValueError):
            unchanged = False
        if not unchanged:
            raise ValueError('COPY_SELECTION_SOURCE_CHANGED')
    bound_policy = seed_run_store(store, policy, bound_policy)
    outputs = {'policy.json': canonical_json(bound_policy.to_dict()), 'copy-inventory.json': canonical_json(inventory)}
    manifest = {
        'base_policy_ref': policy.ref().model_dump(mode='json'),
        'policy_ref': bound_policy.ref().model_dump(mode='json'),
        'selection_sha256': selection_sha256,
        'environments': selection['environments'],
        'files': {name: sha256_hex(raw) for name, raw in outputs.items()},
        'data_origin': bound_policy.data_origin.value, 'actual_membership_verified': False, 'treatment_observed': False,
    }
    if example_source_path is not None:
        manifest.update(official=False, source_document_sha256=policy.source_document_sha256)
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
    parser.add_argument('--example-source', type=Path,
                        help='Exact EXAMPLE input document whose SHA-256 is pinned in the DRAFT policy')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--store', type=Path, help='Append the exact base and new POL to the prepared Guard run store')
    args = parser.parse_args()
    prepare(args.policy, {'baseline': args.baseline_environment, 'candidate': args.candidate_environment},
            args.selection, args.selection_sha256, args.output, store=args.store, example_source_path=args.example_source)


if __name__ == '__main__':
    main()
