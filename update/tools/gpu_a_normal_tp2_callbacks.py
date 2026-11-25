"""Stage and bind only reviewed passive callback assets for ordinary TP2."""
from itertools import islice
import os
from pathlib import Path
import re
import stat

from guard.collectors import engine_callbacks
from guard.models import canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from guard.update_validation import _plain_path
from tools.mvp_a_input_preflight import _read

from guard.collectors.normal_tp2_callbacks import (
    ANNOTATION, CODE, DATA, ERROR, GENERATIONS, KEY, MODES, SCHEMA, _FLAGS, _options, _mounts,
    attach_plan, comparison_plan, environment_field, plan_sha256, prepare, read_pinned_target,
    read_preparation, validate_plan, verify_mounted_pod, verify_pod, write_assets,
)


PAIR_PREPARATION_SCHEMA = 'byul-paired-normal-tp2-preparation/1'
DELIVERY_CHECK_SCHEMA = 'byul-paired-normal-tp2-delivery-check/1'
DELIVERY_ERROR = 'AWS_NORMAL_TP2_DELIVERY_UNVERIFIED'
_METADATA_MODES = frozenset({0o400, 0o444, 0o600, 0o644})
_HOST_PERMISSIONS = {'uid': 2000, 'gid': 0, 'code_directory_mode': '0500', 'code_file_mode': '0400',
                     'settings_mode': '0400', 'journal_directory_mode': '0700'}


def _delivery_stat(path, *, directory, owner=None, modes=None):
    value = _plain_path(path).lstat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected_type(value.st_mode) or not directory and value.st_nlink != 1
            or owner is not None and (value.st_uid, value.st_gid) != owner
            or modes is not None and stat.S_IMODE(value.st_mode) not in modes):
        raise ValueError(DELIVERY_ERROR)
    return value


def _delivery_identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)


def _delivery_bytes(path, expected, limit):
    data, _ = _read(_plain_path(path), max_bytes=limit)
    if type(expected) is not str or sha256_hex(data) != expected:
        raise ValueError(DELIVERY_ERROR)
    return data


def _delivery_side(root, row, release, holder):
    import yaml
    from guard.adapters.engine_provider import VllmProvider
    from tools import gpu_a_aws_lab as renderer

    expected_fields = {'side', 'version', 'image', 'profile', 'model_inventory_sha256',
                       'same_engine_arguments_as_held_tp2', 'shipped_asset_count', 'normal_tp2_callbacks',
                       'engine_yaml_sha256', 'probe_yaml_sha256', 'render_sha256', 'staged_template_sha256'}
    if (row.keys() != expected_fields or row['profile'] not in GENERATIONS
            or (row['version'], row['image']) != (release['version'], release['image'])
            or row['same_engine_arguments_as_held_tp2'] is not True
            or type(row['model_inventory_sha256']) is not str
            or not re.fullmatch('[0-9a-f]{64}', row['model_inventory_sha256'])):
        raise ValueError(DELIVERY_ERROR)
    plan = row['normal_tp2_callbacks']
    validate_plan(plan, attached=True)
    package = root / 'engine-callbacks'
    journal = root / 'lifecycle-data'
    code_files = {package / name for name in {*engine_callbacks._sources(), 'manifest.json', 'settings.json'}}
    code_dirs = {parent for file in code_files for parent in file.parents if parent.is_relative_to(package)}
    visible_files = {root / name for name in ('render.json', 'engine.yaml', 'probe.yaml')}
    template = root / Path(renderer.CHAT_TEMPLATE_PATH).name
    if row['profile'] == 'v2-template':
        visible_files.add(template)
    expected_names = {path.name for path in visible_files | {package, journal}}

    def local_state():
        state = {path: _delivery_stat(path, directory=True, owner=holder, modes={0o500, 0o700}) for path in code_dirs}
        state.update({path: _delivery_stat(path, directory=False, owner=holder, modes={0o400, 0o600})
                      for path in code_files})
        state.update({path: _delivery_stat(path, directory=False, owner=holder, modes=_METADATA_MODES)
                      for path in visible_files})
        for directory in (root, journal):
            state[directory] = _delivery_stat(directory, directory=True, owner=holder, modes={0o700})
        if ({child.name for child in islice(root.iterdir(), len(expected_names) + 1)} != expected_names
                or next(journal.iterdir(), None) is not None
                or (state[package].st_dev, state[package].st_ino) == (state[journal].st_dev, state[journal].st_ino)):
            raise ValueError(DELIVERY_ERROR)
        return state

    local = local_state()
    asset_plan, _, manifest, _ = read_preparation(package, plan['mode'])
    render = strict_json(_delivery_bytes(root / 'render.json', row['render_sha256'], 262144), limit=262144)
    engine_raw = _delivery_bytes(root / 'engine.yaml', row['engine_yaml_sha256'], 262144)
    probe_raw = _delivery_bytes(root / 'probe.yaml', row['probe_yaml_sha256'], 262144)
    deployment, service = yaml.safe_load_all(engine_raw)
    probe = yaml.safe_load(probe_raw)
    pod = deployment['spec']['template']
    engine, = (item for item in pod['spec']['containers'] if item['name'] == 'engine')
    launch, run_id = render['launch'], render['run_id']
    profile = VllmProvider().release_profile(row['version'])
    if (render.get('schema') != renderer.SCHEMA or render.get('normal_tp2') != renderer.NORMAL_TP2
            or render.get('generation') != row['profile'] or render.get('lab_namespace_owner_label_required') is not False
            or type(run_id) is not str or not renderer._RUN_ID.fullmatch(run_id)
            or (render.get('engine_version'), render.get('engine_image')) != (row['version'], row['image'])
            or render.get(KEY) != plan or any(plan[key] != value for key, value in asset_plan.items())
            or type(row['shipped_asset_count']) is not int or row['shipped_asset_count'] != len(manifest['assets'])
            or render.get('outputs_sha256') != {'engine.yaml': row['engine_yaml_sha256'], 'probe.yaml': row['probe_yaml_sha256']}
            or deployment['kind'] != 'Deployment' or service['kind'] != 'Service' or probe['kind'] != 'Pod'
            or any(obj['metadata']['namespace'] != renderer.NAMESPACE for obj in (deployment, service, probe))
            or any(obj['metadata']['labels']['guard.run'] != run_id for obj in (pod, probe))
            or pod['metadata']['labels']['byul.a/generation'] != row['profile']
            or engine['image'] != row['image'] or probe['spec']['containers'][0]['image'] != row['image']
            or launch['command'] != engine['command'] or launch['command'] != profile['launch']['command']
            or launch['runner'] != 'engine-default' or launch['engine'] != profile['launch']['engine']
            or launch['argv'] != engine['args'] or launch['env'] != profile['launch']['env']
            or launch['argv'][-len(renderer.NORMAL_TP2_ARGS):] != list(renderer.NORMAL_TP2_ARGS)
            or any(str(engine['resources'][key][renderer.GPU_RESOURCE]) != '2' for key in ('requests', 'limits'))):
        raise ValueError(DELIVERY_ERROR)
    verify_pod(render, pod, engine)
    if row['profile'] == 'v2-template':
        if row['staged_template_sha256'] != renderer.CHAT_TEMPLATE_SHA256 or render['chat_template']['sha256'] != row['staged_template_sha256']:
            raise ValueError(DELIVERY_ERROR)
        _delivery_bytes(template, row['staged_template_sha256'], 262144)
    elif row['staged_template_sha256'] is not None or 'chat_template' in render:
        raise ValueError(DELIVERY_ERROR)

    identities = {path: _delivery_identity(value) for path, value in local.items()}
    if {path: _delivery_identity(value) for path, value in local_state().items()} != identities:
        raise ValueError(DELIVERY_ERROR)

    def mode(path):
        return format(stat.S_IMODE(local[path].st_mode), '04o')

    return {
        'side': row['side'], 'version': row['version'], 'image': row['image'], 'run_id': run_id,
        'launch_args_sha256': sha256_hex(canonical_json(launch['argv'])),
        'render_sha256': row['render_sha256'], 'engine_yaml_sha256': row['engine_yaml_sha256'],
        'probe_yaml_sha256': row['probe_yaml_sha256'], 'staged_template_sha256': row['staged_template_sha256'],
        'model_inventory_sha256': row['model_inventory_sha256'],
        'plan_sha256': plan_sha256(plan), 'normal_tp2_callbacks': plan,
        'source': {
            'uid': holder[0], 'gid': holder[1], 'bundle_directory_mode': mode(root),
            'code_directory_modes': sorted({mode(path) for path in code_dirs}),
            'code_file_modes': sorted({mode(path) for path in code_files}), 'settings_mode': mode(package / 'settings.json'),
            'journal_directory_mode': mode(journal), 'journal_empty': True, 'file_count': len(code_files),
            'file_modes_sha256': sha256_hex(canonical_json({str(path.relative_to(root)): mode(path) for path in local})),
        },
        'expected_host': {**_HOST_PERMISSIONS, **render['host_directories']},
        'declared_runner_principal_verified': True, 'host_publication_verified': False,
    }, tuple(render[key] for key in ('model_id', 'model_revision', 'model_host_snapshot')), identities


def verify_delivery_pair(pair_path, pair_sha256):
    """Verify local custody and a separately pinned normal pair without publication."""
    import yaml
    from tools.mvp_versions import versions

    try:
        source = _plain_path(Path(pair_path))
        before = _delivery_stat(source, directory=False, modes=_METADATA_MODES)
        holder = (before.st_uid, before.st_gid)
        raw = _delivery_bytes(source, pair_sha256, 262144)
        pair = strict_json(raw, limit=262144)
        fields = {'schema', 'tested_source_tree', 'held_parameters_sha256', 'sides', 'local_render_only',
                  'provider_contacted', 'engine_started', 'live_scope_verified', 'new_actual_pairs'}
        releases = versions()
        if (type(pair) is not dict or pair.keys() != fields or pair['schema'] != PAIR_PREPARATION_SCHEMA
                or type(pair['tested_source_tree']) is not str or not re.fullmatch('[0-9a-f]{40}', pair['tested_source_tree'])
                or type(pair['held_parameters_sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', pair['held_parameters_sha256'])
                or pair['local_render_only'] is not True
                or any(pair[key] is not False for key in ('provider_contacted', 'engine_started', 'live_scope_verified'))
                or type(pair['new_actual_pairs']) is not int or pair['new_actual_pairs'] != 0
                or type(pair['sides']) is not list or len(pair['sides']) != len(releases)
                or any(type(row) is not dict for row in pair['sides'])):
            raise ValueError(DELIVERY_ERROR)
        rows = {row['side']: row for row in pair['sides']}
        if rows.keys() != releases.keys():
            raise ValueError(DELIVERY_ERROR)
        baseline, candidate = (_delivery_side(source.parent / side, rows[side], releases[side], holder) for side in releases)
        left, right = baseline[0], candidate[0]
        if (baseline[1] != candidate[1] or left['run_id'] == right['run_id']
                or left['model_inventory_sha256'] != right['model_inventory_sha256']
                or left['launch_args_sha256'] != right['launch_args_sha256']
                or left['staged_template_sha256'] != right['staged_template_sha256']
                or comparison_plan(left[KEY]) != comparison_plan(right[KEY])
                or any(left[KEY][key] == right[KEY][key]
                       for key in ('session_sha256', 'settings_sha256', 'manifest_sha256', 'mounts_sha256'))
                or any(_delivery_identity(_plain_path(path).lstat()) != identity
                       for path, identity in (baseline[2] | candidate[2]).items())
                or _delivery_identity(_delivery_stat(source, directory=False, owner=holder, modes=_METADATA_MODES))
                != _delivery_identity(before)):
            raise ValueError(DELIVERY_ERROR)
    except (AttributeError, KeyError, OSError, TypeError, ValueError, yaml.YAMLError):
        raise ValueError(DELIVERY_ERROR) from None
    return {'schema': DELIVERY_CHECK_SCHEMA, 'pair_sha256': pair_sha256,
            'prepared_source_tree': pair['tested_source_tree'], 'held_parameters_sha256': pair['held_parameters_sha256'],
            'sides': [left, right], 'local_input_verified': True, 'host_publication_verified': False,
            'ready_for_execution': False}


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=verify_delivery_pair.__doc__)
    parser.add_argument('--verify-pair', type=Path, required=True)
    parser.add_argument('--pair-sha256', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    source, output = _plain_path(args.verify_pair), _plain_path(args.out)
    if output.is_relative_to(source.parent):
        raise ValueError(DELIVERY_ERROR)
    receipt = verify_delivery_pair(source, args.pair_sha256)
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
        stream.write(canonical_json(receipt) + b'\n')


if __name__ == '__main__':
    main()
