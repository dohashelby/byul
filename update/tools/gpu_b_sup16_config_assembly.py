"""Pure CONFIG assembly for d094/84df. No reads, writes, clock or operations.

Callers supply fixed bytes and references. This does not approve those pins,
observe guest ownership, create current proofs, or implement phase supervisors.
The returned kwargs cover only OuterLifecycle's static configuration fields.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath
import re

PHASES = ('normal-before', 'challenge-partial', 'normal-recovered')
ORIGINAL_BUDGETS = {'preflight': 420, 'prepare': 1800, 'observe': 1800,
                    'preserve': 300, 'quiesce': 360, 'archive': 300}
ROLE_KEYS = frozenset(('phase', 'code', 'outer_root', 'runtime', 'join_root',
    'namespace', 'run', 'pod', 'node', 'runtime_class', 'model_root', 'archive',
    'frame', 'render', 'source_files_nosite', 'staging_receipt_sha256'))
FRAME_KEYS = frozenset(('CODE', 'ROOT', 'RUNTIME', 'NS', 'RUN', 'POD', 'NODE', 'REPO', 'PY', 'K'))
RENDER_KEYS = frozenset(('template', 'template_sha256', 'init_asset', 'init_asset_sha256',
    'init_code', 'init_code_sha256', 'mount_pin_sha256', 'output'))
SHARED_KEYS = frozenset(('python', 'resolver', 'bridge', 'ready_controller', 'source_root',
    'source_commit', 'source_manifest_sha256', 'model_manifest', 'model',
    'static_preparation', 'capture_helper', 'kubectl_binary'))


def require(value, code):
    if not value:
        raise ValueError(code)


def keys(value, expected, label):
    require(type(value) is dict and set(value) == set(expected), 'SUP16_CFG_KEYS:' + label)


def text(value, label):
    require(type(value) is str and bool(value), 'SUP16_CFG_STRING:' + label)


def digest(value, count=64):
    require(type(value) is str and re.fullmatch('[0-9a-f]{' + str(count) + '}', value),
            'SUP16_CFG_DIGEST')


def path(value):
    text(value, 'path')
    parsed = PurePosixPath(value)
    require(parsed.is_absolute() and str(parsed) == value and '..' not in parsed.parts
            and not value.startswith('//'), 'SUP16_CFG_ABSOLUTE_GUEST_PATH')
    return parsed


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    require(type(raw) is bytes, 'SUP16_CFG_EXACT_BYTES_REQUIRED')
    def unique(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'SUP16_CFG_DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: require(False, 'SUP16_CFG_NONFINITE_JSON'))


def assemble(*, base_config_bytes, base_config_sha256, source_binding,
             roles, shared, aggregate, asset_bytes, asset_pins):
    """Return separate canonical configs/SHA and static OuterLifecycle kwargs.

    source_binding requires exact commit/tree/manifest/root/python from S2.
    roles contains only static metadata; no phase/proof/UID fields are accepted.
    asset_bytes and asset_pins are explicit same-key mappings by guest path.
    Pins {bytes,sha256,owner} describe supplied bytes, not observed guest files.
    aggregate {root,archive,run} is independent of all three role identities.
    Every missing field fails; nothing is filled from the host or a search.
    """
    digest(base_config_sha256)
    require(sha(base_config_bytes) == base_config_sha256, 'SUP16_CFG_BASE_PIN_CHANGED')
    base = parse(base_config_bytes)
    require(type(base) is dict, 'SUP16_CFG_BASE_OBJECT')
    for name in ('source', 'source_manifest_sha256', 'source_runtime_check',
                 'phase_budgets', 'model_root', 'model_reference', 'model_manifest_sha256'):
        require(name in base, 'SUP16_CFG_BASE_FIELD:' + name)
    keys(source_binding, ('commit', 'tree', 'manifest_sha256', 'root', 'python'), 'source_binding')
    for name in ('commit', 'tree'):
        digest(source_binding[name], 40)
    digest(source_binding['manifest_sha256'])
    root = path(source_binding['root'])
    require(path(source_binding['python']) == root / 'update/.venv/bin/python', 'SUP16_CFG_SOURCE_PYTHON')
    require(base['source'] == source_binding['commit'] and
            base['source_manifest_sha256'] == source_binding['manifest_sha256'], 'SUP16_CFG_SOURCE_BINDING')
    budgets = base['phase_budgets']
    require(type(budgets) is dict and budgets == ORIGINAL_BUDGETS and
            all(type(v) is int for v in budgets.values()), 'SUP16_CFG_ORIGINAL_SEPARATE_BUDGETS')
    text(base['source_runtime_check'], 'source_runtime_check')
    keys(roles, PHASES, 'roles')
    keys(shared, SHARED_KEYS, 'shared')
    keys(aggregate, ('root', 'archive', 'run'), 'aggregate')
    require(type(asset_bytes) is dict and type(asset_pins) is dict and
            set(asset_bytes) == set(asset_pins), 'SUP16_CFG_ASSET_MAPPING')
    for name, pin in asset_pins.items():
        path(name)
        keys(pin, ('bytes', 'sha256', 'owner'), 'asset_pin')
        digest(pin['sha256'])
        require(type(pin['bytes']) is int and pin['bytes'] > 0 and
                type(pin['owner']) is int and pin['owner'] in (0, 1000), 'SUP16_CFG_ASSET_TYPE')
        raw = asset_bytes[name]
        require(type(raw) is bytes and len(raw) == pin['bytes'] and sha(raw) == pin['sha256'],
                'SUP16_CFG_ASSET_BYTES_CHANGED:' + name)

    def asset(name, expected=None, owner=None):
        path(name)
        require(name in asset_pins, 'SUP16_CFG_FIXED_ASSET_REQUIRED:' + name)
        pin = asset_pins[name]
        require((expected is None or pin['sha256'] == expected) and
                (owner is None or pin['owner'] == owner), 'SUP16_CFG_ASSET_REFERENCE_MISMATCH:' + name)
        return parse(asset_bytes[name]) if name.endswith('.json') else None

    def pointer(value):
        keys(value, ('path', 'file_sha256'), 'pointer')
        digest(value['file_sha256'])
        return asset(value['path'], value['file_sha256'], 1000)

    require(shared['python'] == source_binding['python'] and shared['source_root'] == str(root)
            and shared['source_commit'] == source_binding['commit']
            and shared['source_manifest_sha256'] == source_binding['manifest_sha256'], 'SUP16_CFG_SHARED_SOURCE')
    for name in ('resolver', 'ready_controller', 'capture_helper'):
        asset(shared[name], owner=1000)
    asset(shared['bridge'], owner=0)
    path(shared['kubectl_binary'])
    model_manifest = pointer(shared['model_manifest'])
    require(shared['model_manifest']['file_sha256'] == base['model_manifest_sha256']
            and model_manifest == base['model_reference'], 'SUP16_CFG_MODEL_REFERENCE')
    keys(shared['model'], ('id', 'revision', 'inventory_sha256'), 'model')
    text(shared['model']['id'], 'model.id')
    digest(shared['model']['revision'], 40)
    digest(shared['model']['inventory_sha256'])
    require(shared['model']['id'] == model_manifest['model_id'] and
            shared['model']['inventory_sha256'] == model_manifest['inventory_sha256'], 'SUP16_CFG_SHARED_MODEL')
    static = pointer(shared['static_preparation'])
    require(type(static) is dict and static.get('schema') == 'byul-sup16-static-preparation/1'
            and type(static.get('models')) is dict, 'SUP16_CFG_STATIC_PREPARATION')
    require(all(type(static['models'].get(k)) is dict and
                'staging_receipt_sha256' in static['models'][k] for k in ('normal', 'challenge'))
            and static['models']['normal'].get('reference_manifest_sha256') == base['model_manifest_sha256'],
            'SUP16_CFG_STATIC_MODEL_FIELDS')
    aggregate_root, aggregate_alias = path(aggregate['root']), path(aggregate['archive'])
    text(aggregate['run'], 'aggregate.run')
    aliases, roots, namespaces, runs, outputs = [aggregate_alias], [], [], [aggregate['run']], []
    built, configs = {}, {}
    for phase in PHASES:
        spec = roles[phase]
        keys(spec, ROLE_KEYS, phase)
        require(spec['phase'] == phase and spec['runtime_class'] == 'nvidia', 'SUP16_CFG_ROLE_PHASE')
        keys(spec['frame'], FRAME_KEYS, 'frame')
        keys(spec['render'], RENDER_KEYS, 'render')
        for field in ('code', 'outer_root', 'runtime', 'join_root', 'model_root', 'archive'):
            path(spec[field])
        current_root = path(spec['outer_root'])
        require(current_root.is_relative_to(aggregate_root) and current_root != aggregate_root
                and path(spec['join_root']).is_relative_to(current_root)
                and path(spec['join_root']) != current_root, 'SUP16_CFG_ROLE_ROOT')
        for field in ('namespace', 'pod', 'node'):
            require(type(spec[field]) is str and len(spec[field]) <= 63 and
                    re.fullmatch('[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', spec[field]), 'SUP16_CFG_KUBE_NAME')
        text(spec['run'], 'role.run')
        frame = spec['frame']
        for left, right in (('CODE', 'code'), ('ROOT', 'outer_root'), ('RUNTIME', 'runtime'),
                            ('NS', 'namespace'), ('RUN', 'run'), ('POD', 'pod'), ('NODE', 'node')):
            require(frame[left] == spec[right], 'SUP16_CFG_FRAME_ROLE_MISMATCH:' + left)
        require(frame['REPO'] == str(root) and frame['PY'] == source_binding['python']
                and type(frame['K']) is list and len(frame['K']) == 3
                and frame['K'][0] == shared['kubectl_binary'] and frame['K'][1] == '--kubeconfig', 'SUP16_CFG_FRAME_SOURCE_KUBE')
        path(frame['K'][2])
        render = spec['render']
        for name in ('template', 'init_asset', 'init_code'):
            digest(render[name + '_sha256'])
            asset(render[name], render[name + '_sha256'], 1000)
        digest(render['mount_pin_sha256'])
        # The fixed renderer is an UID1000 caller. Keep CODE root-owned700;
        # reserve a distinct new sibling of the still-absent role runtime.
        # The runtime parent itself is an explicitly staged UID1000 root700.
        render_output = path(render['output'])
        runtime_path = path(spec['runtime'])
        require(render_output == runtime_path.parent / ('rendered-' + phase)
                and render_output != runtime_path,
                'SUP16_CFG_RENDER_DISTINCT_OWNER_RUNTIME_SIBLING')
        outputs.append(path(render['output']))
        support = spec['source_files_nosite']
        keys(support, ('path', 'owner', 'file_sha256'), 'source_files_nosite')
        require(type(support['owner']) is int and support['owner'] == 1000, 'SUP16_CFG_SOURCE_FILES_OWNER')
        asset(support['path'], support['file_sha256'], 1000)
        digest(spec['staging_receipt_sha256'])
        model_role = 'challenge' if phase == 'challenge-partial' else 'normal'
        require(model_role in static['models'] and
                static['models'][model_role]['staging_receipt_sha256'] == spec['staging_receipt_sha256'], 'SUP16_CFG_ROLE_STAGING')
        if phase != 'challenge-partial':
            require(spec['model_root'] == base['model_root'], 'SUP16_CFG_NORMAL_MODEL_ROOT')
        config = {name: deepcopy(base[name]) for name in ('source', 'source_manifest_sha256',
            'source_runtime_check', 'phase_budgets', 'model_reference', 'model_manifest_sha256')}
        config.update(model_root=spec['model_root'], staging_receipt_sha256=spec['staging_receipt_sha256'])
        raw = canonical(config)
        built[phase] = deepcopy(spec)
        built[phase].update(fixed_config=config, configuration_sha256=sha(raw))
        configs[phase] = raw
        aliases.append(path(spec['archive']))
        roots.append(current_root)
        namespaces.append(spec['namespace'])
        runs.append(spec['run'])
        outputs.extend((path(spec['code']), path(spec['runtime']), path(spec['join_root'])))
    require(len(set(aliases)) == 4 and not any(p.is_relative_to(aggregate_root) for p in aliases), 'SUP16_CFG_ARCHIVE_ALIAS_SEPARATION')
    require(len(set(roots)) == 3 and not any(a != b and (a.is_relative_to(b) or b.is_relative_to(a))
            for a in roots for b in roots), 'SUP16_CFG_ROLE_ROOT_SEPARATION')
    require(len(set(namespaces)) == 3 and len(set(runs)) == 4
            and len(set(outputs)) == len(outputs), 'SUP16_CFG_ROLE_STATE_SEPARATION')
    require(all(not output.is_relative_to(root) and not root.is_relative_to(output)
                and output != aggregate_root for output in outputs), 'SUP16_CFG_OUTPUT_SOURCE_SEPARATION')
    require(path(roles['challenge-partial']['model_root']) != path(base['model_root']), 'SUP16_CFG_CHALLENGE_MODEL_SEPARATE')
    owned = {phase: [path(roles[phase][k]) for k in ('code', 'runtime', 'outer_root')] for phase in PHASES}
    all_owned = [p for group in owned.values() for p in group]
    require(not any(a.is_relative_to(b) or b.is_relative_to(a)
                    for index, a in enumerate(all_owned) for b in all_owned[index+1:]),
            'SUP16_CFG_MUTABLE_ROOT_INTERFERENCE')
    require(not any(a.is_relative_to(p) for a in aliases for p in all_owned),
            'SUP16_CFG_ARCHIVE_IN_MUTABLE_SNAPSHOT')
    model_roots = [path(roles[phase]['model_root']) for phase in PHASES]
    require(not any(a.is_relative_to(b) or b.is_relative_to(a) for a in all_owned for b in model_roots),
            'SUP16_CFG_MUTABLE_MODEL_INTERFERENCE')
    aggregate_fixed = {name: deepcopy(base[name]) for name in
                       ('source', 'source_manifest_sha256', 'source_runtime_check', 'phase_budgets')}
    aggregate_raw = canonical(aggregate_fixed)
    aggregate_config = dict(phase='aggregate', outer_root=str(aggregate_root), run=aggregate['run'],
                            fixed_config=aggregate_fixed, configuration_sha256=sha(aggregate_raw))
    return {'outer_kwargs': dict(roles=built, shared=deepcopy(shared), aggregate_root=str(aggregate_root),
                aggregate_alias=str(aggregate_alias), aggregate_config=aggregate_config),
            'role_config_bytes': configs, 'aggregate_config_bytes': aggregate_raw,
            'asset_pins': deepcopy(asset_pins), 'source_binding': deepcopy(source_binding),
            'runtime_inputs_unbound': ['model_proof_pointers', 'global_preflight_pointer',
                'phase_guard_factory', 'assert_phase_terminal', 'stop_deadlines', 'aggregate_archive'],
            'gpu_ready': False, 'product_complete': False}
