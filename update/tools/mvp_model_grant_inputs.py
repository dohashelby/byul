"""Bind the existing REQ-05 grant to a declared paired gateway/model inventory."""

import argparse
from pathlib import Path
import re

from guard.collectors.model_mapping import validate_model_mapping
from guard.models import DataOrigin, Environment, Policy, canonical_json, load_pack, sha256_hex
from guard.runtime.bounded import strict_json
from tools.mvp_a_input_preflight import _read
from tools.mvp_runtime_policy import ROOT, POLICY_MAX_BYTES, approved_rules, fill_runtime_values, seed_run_store
from tools.mvp_versions import versions

CHECK = 'REQ-05'
GRANT = 'selected_model_grants'
SCHEMA = 'guard-mvp-gateway-model-inventory/1'
MAPPING_FIELDS = {'environment_ref', 'target_sha256', 'gateway_policy_sha256', 'principal_sha256', 'served_model_name'}
FIXTURE = ROOT / 'docs/dev-1/ma8-poc-fixture-contract.json'


def materialize(policy, pack, environments, targets, inventory):
    """Return a sealed policy revision. Input bindings do not authenticate a gateway."""
    rules = approved_rules(policy, pack, CHECK)
    rule = rules[GRANT]
    if (set(rule) != {'rule', 'require_exact_inventory_and_identity_mapping', 'reject_unapproved_observed_model',
                      'approved_models'}
            or rule['rule'] != 'allow_only_preapproved_gateway_principal_and_fixed_model_revisions'
            or rule['require_exact_inventory_and_identity_mapping'] is not True
            or rule['reject_unapproved_observed_model'] is not True):
        raise ValueError('MVP_MODEL_GRANT_RULE_UNSUPPORTED')
    expected = versions()
    if (type(inventory) is not dict or set(inventory) != {'schema', 'base_policy_ref', 'sides'}
            or inventory['schema'] != SCHEMA or inventory['base_policy_ref'] != policy.ref().model_dump(mode='json')
            or type(inventory['sides']) is not dict or set(environments) != set(expected)
            or set(targets) != set(expected) or set(inventory['sides']) != set(expected)):
        raise ValueError('MVP_MODEL_GRANT_INVENTORY_INVALID')
    model_ids = {(env.model.id, env.model.revision, env.model.inventory_sha256) for env in environments.values()}
    if len(model_ids) != 1:
        raise ValueError('MVP_MODEL_GRANT_PAIR_MISMATCH')
    (model, revision, inventory_sha256), = model_ids
    approved = next((row for row in rule['approved_models']
                     if (model, revision) == (row['repository'], row['revision'])), None)
    if approved is None or type(inventory_sha256) is not str or not re.fullmatch('[0-9a-f]{64}', inventory_sha256):
        raise ValueError('MVP_MODEL_GRANT_MODEL_UNAPPROVED')
    if approved.get('must_match_existing_fixture_inventory') is True:
        fixture = strict_json(FIXTURE.read_bytes())['fixture']
        if (model, revision, inventory_sha256) != (fixture['model_id'], fixture['model_snapshot_id'],
                                                    fixture['model_inventory_sha256']):
            raise ValueError('MVP_MODEL_GRANT_FIXTURE_MISMATCH')
    principal = policy.values[CHECK]['allowed_principal_sha256']
    for side, env in environments.items():
        expected_side, row, target = expected[side], inventory['sides'][side], targets[side]
        gateway = env.facts.get('gateway')
        if (not env.hash_ok() or env.data_origin == DataOrigin.EXAMPLE
                or (env.engine.name, env.engine.version, env.engine.image_digest) != (
                    expected_side['engine'], expected_side['version'], expected_side['image'])
                or gateway is None or gateway.value is not True or gateway.conflict or not gateway.source
                or gateway.origin not in {DataOrigin.MEASURED, DataOrigin.DECLARED}):
            raise ValueError('MVP_MODEL_GRANT_GATEWAY_ENVIRONMENT_REQUIRED')
        env_ref = env.ref().model_dump(mode='json')
        if (type(target) is not dict or target.get('environment_ref') != env_ref
                or type(row) is not dict or set(row) != MAPPING_FIELDS or row['environment_ref'] != env_ref
                or row['target_sha256'] != sha256_hex(canonical_json(target)) or row['principal_sha256'] != principal
                or type(row['gateway_policy_sha256']) is not str
                or not re.fullmatch('[0-9a-f]{64}', row['gateway_policy_sha256'])):
            raise ValueError('MVP_MODEL_GRANT_MAPPING_MISMATCH')
        mapping = validate_model_mapping(env, target.get('model_mapping'))
        if row['served_model_name'] != mapping['served_model_name']:
            raise ValueError('MVP_MODEL_GRANT_ALIAS_MISMATCH')
    grant = f'{principal}:{sha256_hex(model.encode())}:{sha256_hex(revision.encode())}'
    return fill_runtime_values(policy, CHECK, {GRANT: [grant]})


def prepare(policy_path, environment_paths, target_paths, inventory_path, expected_inventory_sha256, output, *, store=None):
    policy_raw = _read(policy_path)[0]
    policy = Policy.model_validate(strict_json(policy_raw, limit=POLICY_MAX_BYTES))
    environments = {side: Environment.model_validate(strict_json(_read(path)[0]))
                    for side, path in environment_paths.items()}
    targets = {side: strict_json(_read(path)[0]) for side, path in target_paths.items()}
    inventory_raw = _read(inventory_path)[0]
    if sha256_hex(inventory_raw) != expected_inventory_sha256:
        raise ValueError('MVP_MODEL_GRANT_INVENTORY_CHANGED')
    pack = load_pack(ROOT / 'packs/PACK-BASELINE')
    bound = materialize(policy, pack, environments, targets, strict_json(inventory_raw))
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    bound = seed_run_store(store, policy, bound)
    with output.open('xb') as stream:
        stream.write(canonical_json(bound.to_dict()))
    return bound.ref()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', type=Path, required=True)
    for side in ('baseline', 'candidate'):
        parser.add_argument(f'--{side}-environment', type=Path, required=True)
        parser.add_argument(f'--{side}-target', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--inventory-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--store', type=Path, help='Append the exact base and new POL to the prepared Guard run store')
    args = parser.parse_args()
    prepare(args.policy, {'baseline': args.baseline_environment, 'candidate': args.candidate_environment},
            {'baseline': args.baseline_target, 'candidate': args.candidate_target}, args.inventory,
            args.inventory_sha256, args.output, store=args.store)


if __name__ == '__main__':
    main()
