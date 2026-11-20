"""Materialize paired LIF-05 and LIF-16 configuration from selected saved declarations."""

import argparse
from pathlib import Path

from guard.core.execution_recipe import validate_policy_time
from guard.core.select import _in_scope
from guard.executors import lifecycle_transition as transition, run_binding
from guard.models import Environment, Policy, canonical_json, load_pack, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.update_validation import _plain_path, reserve_output
from tools import j02_run_config, mvp_live_readiness as readiness
from tools.mvp_a_input_preflight import _read
from tools.mvp_versions import versions

SELECTION_SCHEMA = 'guard-mvp-lifecycle-transition-selection/1'
CONFIG_NAME = 'guard.toml'
INDEX_NAME = 'readiness.json'
_CONFIG_INVALID = 'MVP_TRANSITION_SELECTION_INVALID'
_FILE_CHANGED = 'MVP_TRANSITION_DECLARATION_FILE_CHANGED'
_FILE_LIMITS = {'environment': 262144, 'target': 65536, 'prepared': 262144}


def _selected_file(pointer, limit, captured):
    if (type(pointer) is not dict or pointer.keys() != {'path', 'sha256'}
            or type(pointer['path']) is not str or not pointer['path'] or not transition._sha(pointer['sha256'])):
        raise ValueError(_CONFIG_INVALID)
    path = _plain_path(Path(pointer['path']))
    raw, identity = _read(path, max_bytes=limit)
    if sha256_hex(raw) != pointer['sha256']:
        raise ValueError(_FILE_CHANGED)
    captured.append((path, raw, identity, limit))
    return str(path), strict_json(raw, limit=limit)


def _generations(selection, release, captured):
    rows, manifest, environments = [], [], {}
    supplied = selection['generations']
    if type(supplied) is not list or not 1 <= len(supplied) <= 4:
        raise ValueError(_CONFIG_INVALID)
    expected = release['engine'], release['version'], release['image']
    for item in supplied:
        if (type(item) is not dict or item.keys() != {'id', *_FILE_LIMITS}
                or type(item['id']) is not str or item['id'] in environments):
            raise ValueError(_CONFIG_INVALID)
        key = item['id']
        row, declarations = {'id': key}, {}
        for role, limit in _FILE_LIMITS.items():
            row[role], declarations[role] = _selected_file(item[role], limit, captured)
        env = Environment.model_validate(declarations['environment'])
        target, prepared = declarations['target'], declarations['prepared']
        if (not env.hash_ok() or env.data_origin == 'EXAMPLE'
                or (env.engine.name, env.engine.version, env.engine.image_digest) != expected):
            raise ValueError('MVP_TRANSITION_GENERATION_ENVIRONMENT_MISMATCH')
        if (type(target) is not dict or target.get('lab') is not False or target.get('synthetic', False) is not False
                or target.get('environment_ref') != env.ref().model_dump(mode='json')
                or ('observed_version' in target and target['observed_version'] != env.engine.version)):
            raise ValueError('MVP_TRANSITION_GENERATION_TARGET_MISMATCH')
        if (type(prepared) is not dict
                or prepared.get('profile') not in (transition.generation_sensor.PROFILE, transition.endpoint_sensor.PROFILE)):
            raise ValueError('MVP_TRANSITION_PREPARED_PROFILE_UNAVAILABLE')
        environments[key] = env
        manifest.append(transition._manifest_row(key, env, target, prepared))
        rows.append(row)
    return rows, manifest, environments


def _selection(selection_path, expected_selection_sha256, captured):
    selection_path = _plain_path(Path(selection_path))
    raw, selected_identity = _read(selection_path, max_bytes=262144)
    if not transition._sha(expected_selection_sha256) or sha256_hex(raw) != expected_selection_sha256:
        raise ValueError('MVP_TRANSITION_SELECTION_CHANGED')
    selection = strict_json(raw, limit=262144)
    sides = set(readiness.SIDES)
    if (type(selection) is not dict or selection.keys() != {'schema', 'check_id', 'policy_ref', 'sides'}
            or selection['schema'] != SELECTION_SCHEMA or type(selection['check_id']) is not str
            or selection['check_id'] not in transition.REDEPLOY_CHECKS
            or type(selection['sides']) is not dict or selection['sides'].keys() != sides):
        raise ValueError(_CONFIG_INVALID)
    captured.append((selection_path, raw, selected_identity, 262144))
    return selection


def _prepared_sides(selection, check, releases, issued_at, captured):
    expected_binding = run_binding.config_keys(check)
    identities = set()
    for side in readiness.SIDES:
        selected = selection['sides'][side]
        if type(selected) is not dict or selected.keys() != {'environment_ref', 'generations', *expected_binding}:
            raise ValueError(_CONFIG_INVALID)
        rows, manifest, environments = _generations(selected, releases[side], captured)
        binding = {key: selected[key] for key in sorted(expected_binding)}
        phases = binding.get('phase_generation')
        before = phases.get('before') if type(phases) is dict else None
        if (type(before) is not str or before not in environments
                or environments[before].ref().model_dump(mode='json') != selected['environment_ref']):
            raise ValueError('MVP_TRANSITION_BEFORE_ENVIRONMENT_MISMATCH')
        env = environments[before]
        digest = sha256_hex(canonical_json(manifest))
        settings = {'generations': rows, transition.GENERATION_MANIFEST_PIN: digest}
        _, values = run_binding.compute(check, settings, env, side=side, attempt_id='mvp-offline-preparation',
                                       issued_at=issued_at, run_cfg=binding)
        if values['transition_inputs_sha256'] != digest:
            raise ValueError(_FILE_CHANGED)
        executor = transition.LifecycleTransitionExecutor(settings)
        executor.validate_policy(check, values)
        verified, verified_manifest = executor.prepared_generations(check, env, values)
        if verified_manifest != manifest:
            raise ValueError(_FILE_CHANGED)
        same_side = {transition.generation_identity(row.target, row.env, row.prepared) for row in verified.values()}
        if transition.redeployment_side_reuse(identities, same_side):
            raise ValueError(transition.REDEPLOY_SIDE_REUSE)
        identities.update(same_side)
        before_path = next(row['environment'] for row in rows if row['id'] == before)
        before_pointer = next(item['environment'] for item in selected['generations'] if item['id'] == before)
        yield side, {'executor': settings, 'run_binding': binding}, {**before_pointer, 'path': before_path}


def prepare(policy_path, selection_path, expected_selection_sha256, output, *, pack, store=None):
    """Validate one pinned selection and publish a new guard.toml and readiness index."""
    return prepare_many(policy_path, [(selection_path, expected_selection_sha256)], output, pack=pack, store=store)


def prepare_many(policy_path, selections, output, *, pack, store=None):
    """Publish one or two independently pinned selections for the same paired environments."""
    if (not isinstance(selections, (list, tuple)) or not 1 <= len(selections) <= len(transition.REDEPLOY_CHECKS)
            or any(not isinstance(pair, (list, tuple)) or len(pair) != 2 for pair in selections)):
        raise ValueError(_CONFIG_INVALID)
    captured, documents = [], {}
    for path, pin in selections:
        document = _selection(path, pin, captured)
        check_id = document['check_id']
        if check_id in documents:
            raise ValueError('MVP_TRANSITION_SELECTION_DUPLICATE')
        documents[check_id] = document

    policy_path = _plain_path(Path(policy_path))
    policy_raw, policy_identity = _read(policy_path, max_bytes=262144)
    policy = Policy.model_validate(strict_json(policy_raw, limit=262144))
    if (pack.manifest.pack_id != 'PACK-BASELINE' or pack.manifest.revision != 39
            or not policy.hash_ok() or policy.pack != pack.ref() or policy.status != 'CONFIRMED'
            or policy.data_origin == 'EXAMPLE'):
        raise ValueError('MVP_TRANSITION_CONFIRMED_POLICY_REQUIRED')
    validate_policy_time(policy)
    captured.append((policy_path, policy_raw, policy_identity, 262144))
    policy_pointer = {'path': str(policy_path), 'sha256': sha256_hex(policy_raw)}
    policy_ref = policy.ref().model_dump(mode='json')

    controls = transition.LifecycleTransitionExecutor.name
    spec = {'executors': {controls: 'kubernetes'}, 'sides': {side: {'checks': {}} for side in readiness.SIDES}}
    issued_at, releases = utcnow(), versions()
    inputs, primary_refs, prepared_roots = [], {}, []
    for check_id, selection in sorted(documents.items()):
        check = pack.checks.get(check_id)
        if (check is None or check.executor != controls or transition.PROFILES.get(check.collection_profile) != check_id
                or run_binding.stages_for(check) != (2,) or not _in_scope(check_id, policy)
                or selection['policy_ref'] != policy_ref):
            raise ValueError('MVP_TRANSITION_CONFIRMED_POLICY_REQUIRED')
        for side, entry, environment in _prepared_sides(selection, check, releases, issued_at, captured):
            env_ref = selection['sides'][side]['environment_ref']
            if primary_refs.setdefault(side, env_ref) != env_ref:
                raise ValueError('MVP_TRANSITION_PAIRED_ENVIRONMENT_MISMATCH')
            spec['sides'][side]['checks'][check_id] = entry
            prepared_roots.extend(Path(row['prepared']).parent for row in entry['executor']['generations'])
            inputs.append({'check_id': check_id, 'side': side, 'environment': environment, 'policy': policy_pointer})

    for path, original, identity, limit in captured:
        if _read(path, max_bytes=limit) != (original, identity):
            raise ValueError(_FILE_CHANGED)

    protected = [readiness.ROOT, *(path for path, _, _, _ in captured), *prepared_roots]
    if pack.path is not None:
        protected.append(pack.path)
    store_path = None if store is None else _plain_path(Path(store))
    directory = reserve_output(Path(output), protected=protected)
    directory.chmod(0o700)
    spec['store'] = str(directory / 'store' if store_path is None else store_path)
    config = j02_run_config.build_config(spec)
    config['update']['allowed_checks'] = sorted(documents)
    config_path = directory / CONFIG_NAME
    j02_run_config.write_config(config, config_path)
    pointer = {'path': str(config_path), 'sha256': sha256_hex(_read(config_path, max_bytes=262144)[0])}
    index = readiness.template(pack)
    index['inputs'] = [{**item, 'configuration': pointer} for item in inputs]
    with (directory / INDEX_NAME).open('xb') as stream:
        raw_index = canonical_json(index)
        if stream.write(raw_index) != len(raw_index):
            raise OSError('MVP_TRANSITION_OUTPUT_INCOMPLETE')
    return config, index


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack', type=Path, default=readiness.ROOT / 'packs/PACK-BASELINE')
    parser.add_argument('--policy', type=Path, required=True, help='Confirmed sealed policy for the selected checks')
    parser.add_argument('--selection', type=Path, required=True, action='append',
        help='JSON with check_id and policy_ref; per side, primary environment_ref, phase_generation, '
             'sample_offsets_ms and ordered generations with id and {path, sha256} for environment, target, prepared')
    parser.add_argument('--selection-sha256', required=True, action='append',
        help='Independent SHA-256 of the exact selection bytes, in selection order')
    parser.add_argument('--output', type=Path, required=True, help='New directory for guard.toml and readiness.json')
    parser.add_argument('--store', type=Path, help='Store path used in the emitted configuration')
    args = parser.parse_args(argv)
    if len(args.selection) != len(args.selection_sha256):
        parser.error('each --selection requires one --selection-sha256')
    selections = list(zip(args.selection, args.selection_sha256, strict=True))
    prepare_many(args.policy, selections, args.output, pack=load_pack(args.pack), store=args.store)
    print(args.output / CONFIG_NAME)
    print(args.output / INDEX_NAME)


if __name__ == '__main__':
    main()
