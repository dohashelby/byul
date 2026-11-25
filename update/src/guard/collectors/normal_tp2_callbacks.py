"""Bind approved passive callback sources and mounts to one launched owner."""
from itertools import islice
import os
from pathlib import Path, PurePosixPath
import re

from guard.collectors import engine_callbacks
from guard.models import canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from guard.update_validation import _plain_path

KEY = 'normal_tp2_callbacks'
ANNOTATION = 'byul.a/normal-tp2-callbacks-sha256'
SCHEMA = 'byul-normal-tp2-callback-assets/1'
MODES = {
    'worker-shards': frozenset({'worker_shards'}),
    'scoped-runner-consumption': frozenset({'scoped_runner_consumption'}),
    'worker-shards-and-scoped-runner-consumption': frozenset({'worker_shards', 'scoped_runner_consumption'}),
}
GENERATIONS = frozenset({'v2-default', 'v2-template'})
CODE, DATA = engine_callbacks.sensor.ROOT, engine_callbacks.sensor.DATA_ROOT
_FLAGS = {
    'lab_faults', 'lab_kernel_checks', 'request_attempts', 'generation_transitions', 'health_causal',
    'profiler_control', 'runner_consumption', 'supply_watch', 'worker_shards', 'transition_dispatches',
    'execution_links', 'execution_link_content_hashes', 'scoped_runner_consumption', 'baseline_slot_sampling',
    'baseline_piecewise_graph', 'baseline_attention_metadata', 'baseline_profiler_local',
}
_PINS = {'manifest_sha256', 'settings_sha256', 'assets_sha256', 'session_sha256'}
_VOLUME_PATHS = {'guard-callbacks': ('engine_callbacks', 'engine-callbacks', CODE, True),
                 'guard-data': ('lifecycle_data', 'lifecycle-data', DATA, False)}
ERROR = 'AWS_NORMAL_TP2_CALLBACKS_UNVERIFIED'


def _read_input(path, *, limit, allow_empty=False):
    from guard.executors.cache_timing_group import read_regular

    raw = read_regular(_plain_path(Path(path)), limit=limit)
    if not raw and not allow_empty:
        raise ValueError(ERROR)
    return raw


def _options(mode):
    if type(mode) is not str or mode not in MODES:
        raise ValueError(ERROR)
    return {**{flag: flag in MODES[mode] for flag in _FLAGS},
            'supply_roots': None, 'baseline_profiler_local_image': None}


def prepare(path, mode):
    """Use the existing producer with just the explicitly selected options."""
    _options(mode)
    engine_callbacks.prepare_assets(path, **dict.fromkeys(MODES[mode], True))
    return read_preparation(path, mode)


def read_preparation(path, mode):
    """Return one checked file snapshot, with secret bytes confined to custody."""
    try:
        root = _plain_path(Path(path))
        options = _options(mode)
        assets = engine_callbacks._sources()
        names = {*assets, 'manifest.json', 'settings.json'}
        layout = {}
        for name in names:
            part = Path(name)
            for leaf in (part, *(parent for parent in part.parents if parent != Path('.'))):
                layout.setdefault(leaf.parent, set()).add(leaf.name)
        for directory, expected in layout.items():
            current = _plain_path(root / directory)
            if {item.name for item in islice(current.iterdir(), len(expected) + 1)} != expected:
                raise ValueError(ERROR)
        files = {}
        for name in sorted(names):
            limit = len(assets[name]) if name in assets else 262144 if name == 'manifest.json' else 4096
            if limit == 0:
                # The shipped package initializer is exactly empty.
                files[name] = _read_input(root / name, limit=1, allow_empty=True)
            else:
                files[name] = _read_input(root / name, limit=limit)
        if any(files[name] != raw for name, raw in assets.items()):
            raise ValueError(ERROR)
        manifest = strict_json(files['manifest.json'], limit=262144)
        settings = strict_json(files['settings.json'], limit=4096)
        session, secret = manifest['session'], settings['secret']
        expected_assets = engine_callbacks._source_hashes(assets)
        if (type(session) is not str or not re.fullmatch('[0-9a-f]{32}', session)
                or type(secret) is not str or not re.fullmatch('[0-9a-f]{64}', secret)
                or canonical_json(manifest) != canonical_json({
                    'schema': engine_callbacks.SCHEMA, 'profile': engine_callbacks.sensor.PROFILE,
                    'session': session, 'engine_started': False, 'assets': expected_assets, **options,
                })
                or canonical_json(settings) != canonical_json({'session': session, 'secret': secret, **options})):
            raise ValueError(ERROR)
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        raise ValueError(ERROR) from None
    return ({'schema': SCHEMA, 'mode': mode, 'manifest_sha256': sha256_hex(files['manifest.json']),
             'settings_sha256': sha256_hex(files['settings.json']), 'assets_sha256': sha256_hex(canonical_json(expected_assets)),
             'session_sha256': sha256_hex(session.encode())}, files, manifest, settings)


def write_assets(root, files):
    if files.keys() != {*engine_callbacks._sources(), 'manifest.json', 'settings.json'}:
        raise ValueError(ERROR)
    directories = {root}
    root.mkdir(mode=0o700)
    for name, raw in files.items():
        path = root / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directories.update(parent for parent in path.parents if parent.is_relative_to(root))
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400), 'wb') as stream:
            stream.write(raw)
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        directory.chmod(0o500)


def validate_plan(plan, *, attached):
    pins = _PINS | ({'mounts_sha256'} if attached else set())
    fields = pins | {'schema', 'mode'}
    if (type(plan) is not dict or plan.keys() != fields or plan['schema'] != SCHEMA
            or any(type(plan[key]) is not str or not re.fullmatch('[0-9a-f]{64}', plan[key]) for key in pins)):
        raise ValueError(ERROR)
    _options(plan['mode'])


def _mounts(run_id, directories):
    if type(directories) is not dict or directories.keys() != {row[0] for row in _VOLUME_PATHS.values()}:
        raise ValueError(ERROR)
    mounts, volumes, roots = [], [], set()
    for name, (key, leaf, destination, read_only) in _VOLUME_PATHS.items():
        host = directories[key]
        if type(host) is not str or not re.fullmatch(r'(/[A-Za-z0-9][A-Za-z0-9._-]{0,63}){4,12}', host):
            raise ValueError(ERROR)
        path = PurePosixPath(host)
        if path.name != leaf or path.parent.name != run_id:
            raise ValueError(ERROR)
        roots.add(path.parent)
        mounts.append({'name': name, 'mountPath': destination, 'readOnly': read_only})
        volumes.append({'name': name, 'hostPath': {'path': host, 'type': 'Directory'}})
    if len(roots) != 1:
        raise ValueError(ERROR)
    return {'mounts': mounts, 'volumes': volumes, 'environment': {'name': 'PYTHONPATH', 'value': CODE}}


def attach_plan(plan, run_id, directories):
    validate_plan(plan, attached=False)
    return {**plan, 'mounts_sha256': sha256_hex(canonical_json(_mounts(run_id, directories)))}


def _overlaps(value, paths):
    part = PurePosixPath(value)
    return any(part.is_relative_to(path) or path.is_relative_to(part) for path in paths)


def verify_pod(render, pod, engine):
    """Verify the declared read-only Python root against a saved Pod spec."""
    if render['generation'] not in GENERATIONS:
        if KEY in render:
            raise ValueError(ERROR)
        return
    pod_spec = pod['spec']
    annotation = (pod['metadata'].get('annotations') or {}).get(ANNOTATION)
    code_and_data = tuple(map(PurePosixPath, (CODE, DATA)))
    mounts = [item for item in engine.get('volumeMounts', [])
              if item['name'] in _VOLUME_PATHS or _overlaps(item['mountPath'], code_and_data)]
    volumes = [item for item in pod_spec.get('volumes', []) if item['name'] in _VOLUME_PATHS]
    pythonpath = [entry for entry in engine.get('env', []) if entry['name'] == 'PYTHONPATH']
    if KEY not in render:
        if annotation is not None or mounts or volumes or any(CODE in entry.get('value', '') for entry in pythonpath):
            raise ValueError(ERROR)
        return
    plan = render[KEY]
    validate_plan(plan, attached=True)
    expected = _mounts(render['run_id'], render['host_directories'])
    host_paths = tuple(PurePosixPath(item['hostPath']['path']) for item in expected['volumes'])
    pod_security = pod_spec.get('securityContext', {})
    engine_security = engine.get('securityContext', {})
    reader = {**pod_security, **engine_security}
    if (annotation != plan_sha256(plan) or plan['mounts_sha256'] != sha256_hex(canonical_json(expected))
            or type(reader.get('runAsUser')) is not int or reader['runAsUser'] != 2000
            or type(reader.get('runAsGroup')) is not int or reader['runAsGroup'] != 0
            or reader.get('runAsNonRoot') is not True
            or engine_security.get('allowPrivilegeEscalation') is not False
            or engine_security.get('privileged', False) is not False
            or render['launch']['runner'] != 'engine-default' or engine.get('envFrom')
            or any(entry['name'] in {'PYTHONHOME', 'PYTHONUSERBASE', 'PYTHONSAFEPATH',
                                     'VLLM_USE_V2_MODEL_RUNNER', 'VLLM_SERVER_DEV_MODE',
                                     'VLLM_DISABLE_REQUEST_ID_RANDOMIZATION'} for entry in engine.get('env', []))
            or pythonpath != [expected['environment']]
            or sorted(volumes, key=lambda item: item['name']) != expected['volumes']
            or len(mounts) != len(_VOLUME_PATHS)
            or any(_overlaps(item['hostPath']['path'], host_paths) for item in pod_spec.get('volumes', [])
                   if item['name'] not in _VOLUME_PATHS and 'hostPath' in item)
            or any(mount['name'] in _VOLUME_PATHS for container in (
                       *pod_spec.get('containers', []), *pod_spec.get('initContainers', []),
                       *pod_spec.get('ephemeralContainers', [])) if container['name'] != engine['name']
                   for mount in container.get('volumeMounts', []))):
        raise ValueError(ERROR)
    for actual, saved in zip(sorted(mounts, key=lambda item: item['name']), expected['mounts']):
        if (actual.get('name') != saved['name'] or actual.get('mountPath') != saved['mountPath']
                or actual.get('readOnly', False) is not saved['readOnly']
                or actual.get('subPath') or actual.get('subPathExpr')
                or actual.get('mountPropagation') not in (None, 'None')):
            raise ValueError(ERROR)


def verify_mounted_pod(target, environment, pod):
    """Apply the prepared mount contract to one current owned Pod document."""
    try:
        labels = pod['metadata']['labels']
        generation = labels['byul.a/generation']
        fields = (environment.runtime.platform or '').split('; ')
        expected_launch = target['comparison_config']['launch']
        engine, = (item for item in pod['spec']['containers']
                   if item['name'] == target['kubernetes']['engine']['container'])
        volumes = {item['name']: item['hostPath']['path'] for item in pod['spec']['volumes']
                   if item['name'] in _VOLUME_PATHS}
        if (pod['kind'] != 'Pod' or generation not in GENERATIONS
                or [field for field in fields if field.startswith(('generation=', 'runner='))]
                != [f'generation={generation}', 'runner=engine-default']
                or engine['command'] != target['engine']['command'] or engine['args'] != expected_launch['argv']
                or expected_launch[KEY] != comparison_plan(target[KEY])):
            raise ValueError(ERROR)
        render = {'generation': generation, 'run_id': target['kubernetes']['run_id'],
                  'host_directories': {row[0]: volumes[name] for name, row in _VOLUME_PATHS.items()},
                  'launch': {'runner': 'engine-default'}, KEY: target[KEY]}
        verify_pod(render, pod, engine)
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ValueError(ERROR) from None


def plan_sha256(plan):
    validate_plan(plan, attached=True)
    return sha256_hex(canonical_json(plan))


def comparison_plan(plan):
    validate_plan(plan, attached=True)
    return {key: plan[key] for key in ('schema', 'mode', 'assets_sha256')}


def environment_field(plan):
    return KEY + '_sha256=' + sha256_hex(canonical_json(comparison_plan(plan)))


def read_pinned_target(path, expected_sha256):
    """Read one exact previously selected target for the existing collector."""
    if type(expected_sha256) is not str or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise ValueError(ERROR)
    try:
        raw = _read_input(path, limit=65536)
        if sha256_hex(raw) != expected_sha256:
            raise ValueError(ERROR)
        target = strict_json(raw, limit=65536)
        if type(target) is not dict or KEY not in target:
            raise ValueError(ERROR)
    except (OSError, TypeError, ValueError):
        raise ValueError(ERROR) from None
    return target
