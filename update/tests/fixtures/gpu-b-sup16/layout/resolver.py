"""Only render reviewed SUP16 init and resolve staged command arrays. No dispatch."""
from __future__ import annotations

import argparse
import ast
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

import guard
from guard.collectors.lifecycle import load_prepared
from guard.collectors.model_manifest import _load_manifest
from guard.collectors.model_read_scope import validate_binding
from guard.models import Environment, canonical_json
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_binding import validate_capture_target, verify_owned_pod
from guard.update_validation import reserve_output

PHASES = ('normal-before', 'challenge-partial', 'normal-recovered')
TEMPLATES = dict(zip(PHASES, (
    '64773f0c224b264920a6e006c8ccbff3c2784f59029c5e172438e7da00dfedd2',
    '95009e0b2e546b6bc9571144dec3ea5255cb4951b33af6401037c564ce1167d7',
    '3b3f363fbe753708bd55895c64c0d92fc81b162dad5ff82c75e34d8c491c5eca')))
INIT_TEMPLATE = '036ab9c5f9398e6b6c942dffaa706643b35110419d529bea158635b49cff61c1'
INIT_CODE = '47204eb83bd2031e333f732336e6e0b84116ec7bd343323c153f82f814d0e97c'
INIT_CONTAINER = 'aafb610f25fc2aa6d293292a484fcef9f143149335339544d15fd9358d92683a'
INIT_MOUNTS = '3749752738e61a34c2bb9fe8312dcd16d076960fd8db48f9fd1f6401bdd89f55'
POD_UID = 'c3f5c2202c838850d843893f6df98e5158f45f3ee89b2e8dd0c4058956bc53a9'
PINS = {'operating_tool': 'f3c6a33cdaa5480b7c04593a46118cdff9cdd029f1ace4c3cea4a56a437c29fe',
    'pair.py': 'ed9616091017c486cba9bb8e1b804969e6f1944c5fdbbd4b6c7408fa009bb519',
    'ordering.py': '2485ba44ce8398f833477b79a04c86624cc254316b566f399a4bc9b5f085e4bd',
    'stage.py': 'c61b7059419659419aa973c072095f5a0ca744c3a24072472ce63074ad9d6f94',
    'procedure': 'e48fefc221cbbbba3f365a21fba61c5b27cb94fe861299c8657dc6b4cdba5831',
    'capture': 'bb9a2839b2e2166af3f4d2309bbe5afe260a2eee2f49d05434913d2084eb4cb7',
    'source_owner': 'fb12dbe8d82d6bc027b613a477d6e1a432fa4693c46c6f1380cb38cecdf79fc9',
    'ready_controller': '45046718557d1d2b5a77f210f9a77c23a7d965af92c65ca1a1354b7e8b212fe4',
    'ready_source': 'c6bbc22da9eb8e512439f6ea9babfe929a146dce9327734dbf93f01bab420307'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, pin, limit=262144, *, derive_pin=False):
    if not (derive_pin and pin is None) and (type(pin) is not str or re.fullmatch('[a-f0-9]{64}', pin) is None):
        raise ValueError('SUP16_DELTA_FILE_SHA_REQUIRED')
    path = Path(path).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError('SUP16_DELTA_INPUT_LINK')
    path_before = path.lstat()
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise ValueError('SUP16_DELTA_FILE_BOUND')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        after, path_after = os.fstat(fd), path.lstat()
        # Windows descriptor/path APIs report ctime differently. Compare each
        # API's ctime to itself across the read, and cross-check stable identity.
        if (len(raw) != before.st_size or identity(before) != identity(after)
                or identity(before) != identity(path_before) or identity(before) != identity(path_after)
                or before.st_ctime_ns != after.st_ctime_ns or path_before.st_ctime_ns != path_after.st_ctime_ns
                or pin is not None and sha(raw) != pin):
            raise ValueError('SUP16_DELTA_FILE_BYTES_CHANGED')
        return raw
    finally:
        os.close(fd)


def load(pin, limit=262144):
    if type(pin) is not dict or set(pin) != {'path', 'file_sha256'}:
        raise ValueError('SUP16_DELTA_FILE_POINTER_REQUIRED')
    return strict_json(read(pin['path'], pin['file_sha256'], limit), limit=limit)


def write(path, value):
    with Path(path).open('xb') as stream:
        stream.write(canonical_json(value))


def reserve(output, protected):
    out = reserve_output(output, protected=[Path(p) for p in protected])
    out.chmod(0o700)
    return out


def render(a):
    if a.phase not in PHASES or a.template_sha256 != TEMPLATES[a.phase] or a.init_asset_sha256 != INIT_TEMPLATE or a.init_code_sha256 != INIT_CODE or a.mount_pin_sha256 != INIT_MOUNTS or (a.uid, a.gid) != (1000, 1000):
        raise ValueError('SUP16_DELTA_REVIEWED_INIT_PINS_REQUIRED')
    original = strict_json(read(a.template, a.template_sha256))
    approved = strict_json(read(a.init_asset, INIT_TEMPLATE))
    code = read(a.init_code, INIT_CODE)
    pod, = [x for x in approved['items'] if x['kind'] == 'Pod']
    cm, = [x for x in approved['items'] if x['kind'] == 'ConfigMap']
    init = pod['spec']['initContainers']
    if (sha(canonical_json(init)) != INIT_CONTAINER or sha(canonical_json(init[0]['volumeMounts'])) != INIT_MOUNTS
            or sha(canonical_json(pod['spec']['securityContext'])) != POD_UID
            or cm['data']['prepare_native_mountpoint.py'].encode() != code):
        raise ValueError('SUP16_DELTA_INIT_GRAPH_MISMATCH')
    value = deepcopy(original)
    current, = [x for x in value['items'] if x['kind'] == 'Pod']
    config, = [x for x in value['items'] if x['kind'] == 'ConfigMap']
    if current['spec'].get('initContainers') or sha(canonical_json(current['spec']['securityContext'])) != POD_UID or 'prepare_native_mountpoint.py' in config['data']:
        raise ValueError('SUP16_DELTA_BASE_TEMPLATE_MISMATCH')
    current['spec']['initContainers'] = deepcopy(init)
    config['data']['prepare_native_mountpoint.py'] = code.decode('utf-8')
    changed = []
    for container in current['spec']['containers']:
        for mount in container['volumeMounts']:
            if mount['name'] == 'native-data':
                if mount['mountPath'] not in {'/guard-lifecycle/data', '/guard-supply-boot/data'} or 'subPath' in mount or 'subPathExpr' in mount:
                    raise ValueError('SUP16_DELTA_DATA_MOUNT_MISMATCH')
                mount['subPath'] = 'private-data-root'
                changed.append([container['name'], mount['mountPath']])
    if sorted(changed) != [['engine', '/guard-lifecycle/data'], ['engine', '/guard-supply-boot/data'], ['preparation', '/guard-lifecycle/data']]:
        raise ValueError('SUP16_DELTA_DATA_MOUNT_SET_MISMATCH')
    out = reserve(a.output, [a.template, a.init_asset, a.init_code, __file__])
    write(out / 'template.json', value)
    receipt = {'schema': 'byul-sup16-init-render-delta/1', 'phase': a.phase,
        'base_template_sha256': a.template_sha256, 'approved_init_template_sha256': INIT_TEMPLATE,
        'init_code_sha256': INIT_CODE, 'init_container_sha256': INIT_CONTAINER, 'init_mounts_sha256': INIT_MOUNTS,
        'uid': 1000, 'gid': 1000, 'changes': ['exact approved init container', 'exact approved CM script', 'private-data-root subPath on existing three data mounts'],
        'template_sha256': sha(canonical_json(value)), 'deployed': False, 'actual_private_root_verified': False,
        'engine_sensor_policy_deadlines_changed': False, 'ready_for_execution': False}
    write(out / 'render-receipt.json', receipt)
    return receipt


def command(label, actor, argv, timeout, stdin=None):
    if not re.fullmatch('[a-z0-9-]{1,80}', label) or actor not in {'root', 'owner1000', 'preparation'} or not argv or any(type(v) is not str for v in argv) or type(timeout) not in (int, float) or not 0 < timeout <= 125:
        raise ValueError('SUP16_DELTA_COMMAND_SHAPE')
    return {'label': label, 'actor': actor, 'argv': argv, 'timeout': timeout, 'stdin': stdin}


def stdin_pin(path, pin):
    raw = read(path, pin, 3*1024**2)
    return {'path': str(Path(path).absolute()), 'bytes': len(raw), 'sha256': sha(raw)}


def actual_init(pod):
    init, = pod['spec']['initContainers']
    state, = pod['status']['initContainerStatuses']
    # The API may materialize these default fields. Never compare its entire
    # object bytes to a pre-admission template or ignore semantic extra fields.
    clean = deepcopy(init)
    for key, value in {'imagePullPolicy': 'IfNotPresent', 'terminationMessagePath': '/dev/termination-log',
                       'terminationMessagePolicy': 'File', 'resources': {}}.items():
        if key in clean:
            if clean.pop(key) != value:
                raise ValueError('SUP16_DELTA_ACTUAL_INIT_DEFAULT_MISMATCH')
    for mount in clean['volumeMounts']:
        if mount.get('readOnly') is False:
            mount.pop('readOnly')
    if (sha(canonical_json([clean])) != INIT_CONTAINER or state['name'] != init['name']
            or type(state.get('restartCount')) is not int or state['restartCount'] != 0
            or type(state.get('state', {}).get('terminated', {}).get('exitCode')) is not int
            or state['state']['terminated']['exitCode'] != 0
            or sha(canonical_json(pod['spec']['securityContext'])) != POD_UID):
        raise ValueError('SUP16_DELTA_ACTUAL_INIT_EXIT_REQUIRED')


def ready_config(path):
    if path is None:
        raise ValueError('SUP16_DELTA_REVIEWED_READY_SOURCE_REQUIRED')
    tree = ast.parse(read(path, PINS['ready_controller']))
    cfg, = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CONFIG' for t in n.targets)]
    if sha(cfg['health_probe_source'].encode()) != PINS['ready_source']:
        raise ValueError('SUP16_DELTA_REVIEWED_READY_SOURCE_REQUIRED')
    return cfg


def inputs(a):
    s = load({'path': str(a.inputs), 'file_sha256': a.inputs_sha256})
    if s.get('schema') != 'byul-sup16-role-command-inputs/1' or s.get('phase') not in PHASES:
        raise ValueError('SUP16_DELTA_INPUT_SCHEMA')
    root = Path(s['source_root']).absolute()
    if not Path(guard.__file__).resolve().is_relative_to(root / 'update/src/guard'):
        raise ValueError('SUP16_DELTA_INSTALLED_SOURCE_ORIGIN_MISMATCH')
    if re.fullmatch('[a-f0-9]{40}', s['source_commit']) is None:
        raise ValueError('SUP16_DELTA_SOURCE_COMMIT_REQUIRED')
    read(root / 'update/release/reviewed-source.json', s['source_manifest_sha256'])
    tool = root / 'update/tools/gpu_b_sup16_operating_prepare.py'
    read(tool, PINS['operating_tool'])
    ev = load(s['environment'])
    if ev.get('kind') != 'ENV':
        raise ValueError('SUP16_DELTA_SEALED_ENV_REQUIRED')
    env = Environment.model_validate(ev)
    if not env.hash_ok():
        raise ValueError('SUP16_DELTA_ENV_HASH')
    if (env.engine.name, env.engine.version, env.engine.image_digest) != ('vllm', '0.31.0',
            'vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b'):
        raise ValueError('SUP16_DELTA_RUNTIME_SUPPORT_MISMATCH')
    target, pod, ns = load(s['target']), load(s['pod']), load(s['namespace'])
    binding = validate_capture_target(target, env)
    if s['context'] != binding['context'] or pod.get('kind') != 'Pod' or ns.get('kind') != 'Namespace' or ns['metadata']['name'] != binding['engine']['namespace']:
        raise ValueError('SUP16_DELTA_SNAPSHOT_CONTEXT_MISMATCH')
    owner = verify_owned_pod(pod, ns['metadata'], binding['engine'], binding['run_id'], require_ready=False)
    actual_init(pod)
    manifest = _load_manifest(load(s['model_manifest']), None)
    if manifest.get('model_id') != env.model.id or manifest['inventory_sha256'] != env.model.inventory_sha256:
        raise ValueError('SUP16_DELTA_MODEL_REFERENCE_MISMATCH')
    previous = s['previous_roles']
    if type(previous) is not list or len(previous) != PHASES.index(s['phase']):
        raise ValueError('SUP16_DELTA_PREVIOUS_ROLE_COUNT_REQUIRED')
    prev = [load(p) for p in previous]
    for index, r in enumerate(prev):
        if (r.get('schema') != 'byul-sup16-role-preparation/1' or r['phase'] != PHASES[index]
                or r['source']['commit'] != s['source_commit'] or r['source']['manifest_sha256'] != s['source_manifest_sha256']
                or r['engine']['pod_uid'] == binding['engine']['pod_uid']
                or any(x['engine']['pod_uid'] == r['engine']['pod_uid'] for x in prev[:index])):
            raise ValueError('SUP16_DELTA_PREVIOUS_ROLE_BINDING_MISMATCH')
    role = 'challenge' if s['phase'] == 'challenge-partial' else 'normal'
    packet = Path(s['static_preparation']['path']).absolute().parent
    meta = load(s['static_preparation'])
    if (meta.get('schema') != 'byul-sup16-static-preparation/1' or meta.get('same_record_producer_for_both_roles') != PINS['pair.py']
            or meta['models']['normal']['reference_manifest_sha256'] != s['model_manifest']['file_sha256']
            or meta['models'][role]['staging_receipt_sha256'] != s['staging_receipt_sha256']):
        raise ValueError('SUP16_DELTA_STATIC_MODEL_PIN_MISMATCH')
    if re.fullmatch('[a-f0-9]{64}', s['staging_receipt_sha256']) is None:
        raise ValueError('SUP16_DELTA_STAGING_RECEIPT_PIN_REQUIRED')
    for key in ('role_output', 'capture_output'):
        if Path(s[key]).absolute().is_relative_to(root):
            raise ValueError('SUP16_DELTA_NEW_OUTPUT_REQUIRED')
        if Path(s[key]).exists() and (key == 'role_output' and a.stage == 'bind'
                or key == 'capture_output' and a.stage != 'writer'):
            raise ValueError('SUP16_DELTA_NEW_OUTPUT_REQUIRED')
    return s, env, target, binding, owner, root, tool, packet, role, prev


def role_result(a, s, env, target, role, prev):
    r = load({'path': str(a.role_preparation), 'file_sha256': a.role_preparation_sha256})
    directory = Path(a.role_preparation).absolute().parent
    if directory != Path(s['role_output']).absolute() or Path(a.role_preparation).name != 'role-preparation.json':
        raise ValueError('SUP16_DELTA_ROLE_OUTPUT_PATH_MISMATCH')
    expected = {'environment_sha256': s['environment']['file_sha256'], 'target_sha256': s['target']['file_sha256'], 'reference_manifest_sha256': s['model_manifest']['file_sha256']}
    if (r.get('schema') != 'byul-sup16-role-preparation/1' or r['phase'] != s['phase'] or r['role'] != role
            or r['source']['commit'] != s['source_commit'] or r['source']['manifest_sha256'] != s['source_manifest_sha256']
            or r['environment_ref'] != env.ref().model_dump(mode='json') or r['engine'] != target['kubernetes']['engine']
            or r['target_sha256'] != sha(canonical_json(target)) or r['input_files'] != expected
            or any(p['session'] == r['session'] for p in prev)):
        raise ValueError('SUP16_DELTA_ROLE_RECEIPT_MISMATCH')
    raw = read(directory / 'native17/manifest.json', r['prepared_manifest_file_sha256'])
    prepared = load_prepared(directory / 'native17/manifest.json')
    validate_binding(prepared, env, target)
    if prepared.get('profile') != 'vllm-native-lifecycle-metadata/17':
        raise ValueError('SUP16_DELTA_NATIVE17_PROFILE_REQUIRED')
    binding_raw = read(directory / (role + '.binding.json'), r['binding_file_sha256'])
    binding = strict_json(binding_raw)
    if sha(canonical_json(prepared)) != r['prepared_sha256'] or prepared['session'] != r['session'] or binding != {'role': role, 'session': r['session'], 'environment_ref': r['environment_ref'], 'target_sha256': r['target_sha256'], 'source_sha256': PINS['pair.py']}:
        raise ValueError('SUP16_DELTA_PREPARED_BINDING_MISMATCH')
    payload_path = directory / 'installer-payload.json'
    payload_raw = read(payload_path, None, 3*1024**2, derive_pin=True)
    payload = strict_json(payload_raw, limit=3*1024**2)
    if base64.b64decode(payload['manifest'], validate=True) != raw or set(payload['assets']) != set(prepared['assets']):
        raise ValueError('SUP16_DELTA_INSTALL_PAYLOAD_MISMATCH')
    for name, value in prepared['assets'].items():
        if base64.b64decode(payload['assets'][name], validate=True) != read(directory / 'native17' / name, value['sha256']):
            raise ValueError('SUP16_DELTA_INSTALL_ASSET_MISMATCH')
    return r, directory, binding, payload_path, sha(payload_raw)


def bind_commands(a):
    s, env, target, binding, owner, root, tool, packet, role, prev = inputs(a)
    stage = a.stage
    phase = s['phase']
    commands = []
    if stage == 'bind':
        dest = Path(s['role_output']).absolute()
        if dest.exists() or dest.is_relative_to(root):
            raise ValueError('SUP16_DELTA_NEW_ROLE_OUTPUT_REQUIRED')
        argv = [sys.executable, '-I', '-B', str(tool), 'bind-role', '--phase', phase,
            '--source-root', str(root), '--source-commit', s['source_commit'], '--manifest-sha256', s['source_manifest_sha256'],
            '--static-packet', str(packet), '--static-sha256', s['static_preparation']['file_sha256'],
            '--environment', s['environment']['path'], '--target', s['target']['path'], '--model-manifest', s['model_manifest']['path'],
            '--model-manifest-sha256', s['model_manifest']['file_sha256']]
        for previous in s['previous_roles']:
            argv.extend(['--previous-binding', previous['path']])
        argv.extend(['--output', str(dest)])
        commands.append(command('sup16-bind-' + phase, 'owner1000', argv, 45))
    else:
        if a.role_preparation is None or a.role_preparation_sha256 is None:
            raise ValueError('SUP16_DELTA_ACTUAL_ROLE_RECEIPT_REQUIRED')
        r, directory, actual_binding, payload, payload_sha = role_result(a, s, env, target, role, prev)
        if stage == 'publish':
            proc = strict_json(read(packet / 'assets/original-procedure.json', PINS['procedure']))
            pub, = [x for x in proc['sequence'] if x['step'] == 'publish_actual_binding']
            argv = [v.replace('normal.binding.json', role + '.binding.json') for v in pub['inside_preparation_argv']]
            argv[-1] = r['binding_file_sha256']
            commands.append(command('sup16-binding-' + phase, 'preparation', argv, 40,
                stdin_pin(directory / (role + '.binding.json'), r['binding_file_sha256'])))
            commands.append(command('sup16-installer-' + phase, 'preparation', ['python3', '-I', '-S', '-B',
                '/guard-prepare/native17_install.py', '--tool-sha256', 'b4b7e104997f8560ab4c1ef1301f87b0fa720585d81b64b99cbcd95a8a831d57',
                '--manifest-sha256', r['prepared_manifest_file_sha256']], 40, stdin_pin(payload, payload_sha)))
        elif stage in ('ready', 'normal'):
            cfg = ready_config(a.ready_controller)
            remaining = a.observation_remaining_seconds
            if type(remaining) not in (int, float) or not 0 < remaining <= 120:
                raise ValueError('SUP16_DELTA_ACTUAL_OBSERVATION_REMAINING_REQUIRED')
            if stage == 'ready':
                commands.append(command('sup16-ready-' + phase, 'preparation', ['python3', '-I', '-S', '-B', '-c',
                    cfg['health_probe_source'], str(min(5, remaining))], min(10, max(1, remaining))))
            else:
                if role != 'normal':
                    raise ValueError('SUP16_DELTA_NORMAL_CONTROL_ROLE_REQUIRED')
                commands.append(command('sup16-normal-' + phase, 'preparation',
                    cfg['normal_request_argv'] + [str(min(10, remaining))], min(15, max(1, remaining))))
        elif stage == 'capture':
            capture = Path(s['capture_helper']['path']).absolute()
            if s['capture_helper']['file_sha256'] != PINS['capture']:
                raise ValueError('SUP16_DELTA_CAPTURE_SOURCE_PIN_REQUIRED')
            read(capture, PINS['capture'])
            read(capture.parent / 'source_owner_verify.py', PINS['source_owner'])
            commands.append(command('sup16-capture-' + phase, 'owner1000', [sys.executable, '-I', '-B', str(capture),
                '--source-root', str(root), '--source-commit', s['source_commit'], '--manifest-sha256', s['source_manifest_sha256'],
                '--tool-sha256', PINS['capture'], '--environment', s['environment']['path'], '--target', s['target']['path'],
                '--prepared', str(directory / 'native17/manifest.json'), '--output', s['capture_output'], '--kubectl-binary', s['kubectl_binary'], '--execute'], 125))
        else:
            if any(getattr(a, k) is None for k in ('capture', 'capture_sha256', 'capture_receipt', 'capture_receipt_sha256', 'journal', 'journal_sha256', 'journal_container_path')):
                raise ValueError('SUP16_DELTA_ACTUAL_CAPTURE_JOURNAL_REQUIRED')
            cap = load({'path': str(a.capture), 'file_sha256': a.capture_sha256}, 32*1024**2)
            receipt = load({'path': str(a.capture_receipt), 'file_sha256': a.capture_receipt_sha256})
            # Original capture_session output has no ENV field. The existing
            # actual role receipt/prepared binding and recording owner connect
            # ENV/target; never invent an extra producer field. If supplied by
            # a future producer, an inconsistent optional field is rejected.
            if (cap.get('schema') != 'guard-lifecycle-session-capture/1' or cap.get('session') != r['session']
                    or 'environment_ref' in cap and cap['environment_ref'] != r['environment_ref']
                    or receipt.get('capture_executed') is not True or receipt.get('source') != s['source_commit'] or receipt.get('errors') != []):
                raise ValueError('SUP16_DELTA_CAPTURE_NOT_COMPLETED')
            match = re.fullmatch(r'/guard-lifecycle/data/' + r['session'] + r'/pid-([1-9][0-9]*)-([1-9][0-9]*)\.jsonl', a.journal_container_path)
            if match is None:
                raise ValueError('SUP16_DELTA_JOURNAL_SESSION_PATH_MISMATCH')
            pid, ticks = map(int, match.groups())
            recorded, = [x for x in cap['recordings'] if (x['pid'], x['start_ticks']) == (pid, ticks)]
            raw = read(a.journal, a.journal_sha256, 2*1024**2)
            provenance = (recorded.get('capture') or {}).get('provenance', {})
            expected_owner = dict(owner)
            expected_owner['started_at_sha256'] = sha(expected_owner.pop('started_at').encode('utf-8', 'surrogatepass'))
            if provenance.get('capture_sha256') != sha(raw) or provenance.get('bytes') != len(raw) or provenance.get('owner') != expected_owner or recorded.get('session_owner_match') is not True or recorded.get('listed_file_identity_match') is not True:
                raise ValueError('SUP16_DELTA_JOURNAL_CAPTURE_BINDING_MISMATCH')
            if recorded['capture'].get('records') != [json.loads(line) for line in raw.splitlines()]:
                raise ValueError('SUP16_DELTA_JOURNAL_CAPTURE_ROWS_MISMATCH')
            helper = read(packet / 'assets/ordering.py', PINS['ordering.py'])
            scope = {'__name__': '_sup16_fixed_ordering', '__file__': str(packet / 'assets/ordering.py')}
            exec(compile(helper, scope['__file__'], 'exec'), scope)
            ordering = scope['native17_ordering_from_raw'](raw, actual_binding)
            argv = ['python3', '-I', '-B', '/guard-prepare/pair.py', '--tool-sha256', PINS['pair.py'],
                '--binding', '/guard-control/' + role + '.binding.json', '--binding-sha256', r['binding_file_sha256'],
                '--staging-receipt', '/models/staging-receipt.json', '--staging-receipt-sha256', s['staging_receipt_sha256'],
                '--staging-producer', '/guard-prepare/stage.py', '--staging-producer-sha256', PINS['stage.py'],
                '--ordering-helper', '/guard-prepare/ordering.py', '--native-recording', a.journal_container_path]
            commands.append(command('sup16-writer-' + phase, 'preparation', argv, 30))
    protected = [__file__, a.inputs, root, s['role_output'], s['capture_output'],
        *[s[k]['path'] for k in ('environment', 'target', 'model_manifest', 'pod', 'namespace', 'static_preparation', 'capture_helper')],
        *[p['path'] for p in s['previous_roles']],
        *[getattr(a, key) for key in ('role_preparation', 'ready_controller', 'capture', 'capture_receipt', 'journal') if getattr(a, key) is not None]]
    out = reserve(a.output, protected)
    stage_pins = {}
    for key in (('role_preparation', 'capture', 'capture_receipt', 'journal') if stage == 'writer' else ('role_preparation',) if stage != 'bind' else ()):
        if getattr(a, key) is not None:
            stage_pins[key] = {'path': str(Path(getattr(a, key)).absolute()), 'file_sha256': getattr(a, key + '_sha256')}
    if stage in ('ready', 'normal'):
        stage_pins['ready_controller'] = {'path': str(Path(a.ready_controller).absolute()), 'file_sha256': PINS['ready_controller']}
    result = {'schema': 'byul-sup16-role-resolved-commands/1', 'phase': phase, 'stage': stage,
        'commands': commands, 'required_input_pins': s, 'source_commit_is_operator_supplied': True,
        'stage_input_pins': stage_pins,
        'outputs': {'command_file': str(out / 'commands.json'), 'operator_chosen_role_output': s['role_output'],
                    'operator_chosen_capture_output': s['capture_output']},
        'owner_from_supplied_snapshots': owner, 'live_identity_checked_by_helper': False,
        'ready_and_cleanup_must_be_verified_by_S2': True, 'ready_step_repetition_uses_original_S2_deadline': True,
        'S2_preserves_original_sequence': 'publish inside launcher asset90; original preparation observation window, Ready then normal-only control; original reader120 with outer125; actual closed journal before writer; never reset timers or overwrite failure/cleanup evidence',
        'supplied_remaining_seconds_is_not_a_new_deadline': True,
        'ordering_from_provided_journal': ordering if stage == 'writer' else None,
        'dispatched': False, 'GPU_engine_writer_model_AWS_started': False, 'full_product_pair_connected': False}
    write(out / 'commands.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    r = sub.add_parser('render')
    for name in ('template', 'init-asset', 'init-code', 'output'):
        r.add_argument('--' + name, type=Path, required=True)
    for name in ('template-sha256', 'init-asset-sha256', 'init-code-sha256', 'mount-pin-sha256', 'phase'):
        r.add_argument('--' + name, required=True)
    r.add_argument('--uid', type=int, required=True); r.add_argument('--gid', type=int, required=True)
    b = sub.add_parser('bind-commands')
    b.add_argument('--inputs', type=Path, required=True); b.add_argument('--inputs-sha256', required=True)
    b.add_argument('--stage', choices=('bind', 'publish', 'ready', 'normal', 'capture', 'writer'), required=True); b.add_argument('--output', type=Path, required=True)
    for name in ('role-preparation', 'ready-controller', 'capture', 'capture-receipt', 'journal'):
        b.add_argument('--' + name, type=Path)
    for name in ('role-preparation-sha256', 'capture-sha256', 'capture-receipt-sha256', 'journal-sha256', 'journal-container-path'):
        b.add_argument('--' + name)
    b.add_argument('--observation-remaining-seconds', type=float)
    a = p.parse_args(); print(json.dumps({'render': render, 'bind-commands': bind_commands}[a.action](a), sort_keys=True))


if __name__ == '__main__':
    main()
