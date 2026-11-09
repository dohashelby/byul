"""SUP16 sequential TP1 templates and per-live-role inputs; no remote actions."""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'src'), str(Path(__file__).resolve().parents[1])]

from guard.collectors.file import load_environment
from guard.collectors.lifecycle import prepare_assets, load_prepared
from guard.collectors.model_read_scope import build_scope, validate_binding
from guard.models import canonical_json
from guard.runtime.kubernetes_binding import validate_capture_target
from tools import gpu_b_sup16_prepare as static

TEMPLATE_SHA = '0c6e9b028d50f69396eed959d533f089d9bdc84e3d98539a24e739cc65c47b80'
CAPTURE_SHA = 'bb9a2839b2e2166af3f4d2309bbe5afe260a2eee2f49d05434913d2084eb4cb7'
PHASES = {'normal-before': 'normal', 'challenge-partial': 'challenge', 'normal-recovered': 'normal'}
PAIR_BLOCKER = ('Current supply_boot.run reads two live role targets in one call. '
    'One-GPU sequential Pod deletion and the exited partial worker are not an archived-input backend. '
    'Do not replay a deleted normal Pod or injected collector results into a product factory.')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def document(path, expected=None):
    return static.strict_json(static.read(path, expected), limit=262144)


def packet(args):
    source = Path(args.static_packet)
    meta = document(source / 'preparation.json', args.static_sha256)
    if meta.get('schema') != 'byul-sup16-static-preparation/1' or meta.get('same_record_producer_for_both_roles') != static.PINS['pair.py']:
        raise ValueError('SUP16_OPERATING_STATIC_PACKET_MISMATCH')
    assets = {name: static.read(source / 'assets' / name, pin) for name, pin in static.PINS.items()}
    context = static.source_context(args.source_root, args.source_commit, args.manifest_sha256,
                                   assets['source_owner.py'], source / 'assets/source_owner.py')
    owner = static.module(assets['source_owner.py'], source / 'assets/source_owner.py')
    repo, prefix, _, _ = owner['_git_context'](args.source_root)
    name = 'update/tools/gpu_b_sup16_operating_prepare.py'
    owner['_git'](repo, prefix, ['ls-files', '--error-unmatch', name])
    owner['_git'](repo, prefix, ['diff', '--exit-code', 'HEAD', '--', name])
    context['operating_tool_sha256'] = sha(static.read(__file__))
    return meta, assets, context


def write(path, value):
    with Path(path).open('xb') as stream:
        stream.write(canonical_json(value))


def plan(args):
    meta, assets, context = packet(args)
    capture_raw = static.read(args.capture_helper, CAPTURE_SHA)
    original = document(args.pair_template, TEMPLATE_SHA)
    procedure = static.strict_json(assets['original-procedure.json'], limit=262144)
    publication, = [s for s in procedure['sequence'] if s['step'] == 'publish_actual_binding']
    config, = [i for i in original['items'] if i['kind'] == 'ConfigMap']
    pods = [i for i in original['items'] if i['kind'] == 'Pod']
    if len(pods) != 2:
        raise ValueError('SUP16_OPERATING_PAIR_TEMPLATE_CHANGED')
    config = deepcopy(config)
    config['data'].pop('write_pair_boot.py')
    config['data'].pop('sup16_stage_artifact.py')
    config['data'].update({n: assets[n].decode('utf-8') for n in ('pair.py', 'ordering.py', 'stage.py')})
    out = static.reserve_preparation_output(args.output, protected=[static.ROOT, args.static_packet, args.pair_template, args.capture_helper])
    operator_assets = out / 'operator-assets'
    operator_assets.mkdir(mode=0o700)
    for name, raw in [('capture_normal.py', capture_raw), ('source_owner_verify.py', assets['source_owner.py'])]:
        with (operator_assets / name).open('xb') as stream:
            stream.write(raw)
    plans = {}
    for phase, role in PHASES.items():
        pod, = [deepcopy(p) for p in pods if p['metadata']['name'] == 'sup16-' + role]
        pod['metadata']['name'] = 'sup16-' + phase
        # Keep original GPU/UID/mount/launcher/time bounds; never schedule the
        # original pair List concurrently on one GPU.
        engine, = [c for c in pod['spec']['containers'] if c['name'] == 'engine']
        if engine['resources']['limits']['nvidia.com/gpu'] != '1':
            raise ValueError('SUP16_OPERATING_TP1_TEMPLATE_CHANGED')
        value = {'apiVersion': 'v1', 'kind': 'List', 'items': [
            *[deepcopy(i) for i in original['items'] if i['kind'] == 'Namespace'], deepcopy(config), pod]}
        path = out / (phase + '.template.json')
        write(path, value)
        publish = deepcopy(publication)
        publish['inside_preparation_argv'] = [v.replace('normal.binding.json', role + '.binding.json') for v in publish['inside_preparation_argv']]
        publish['stdin'] = '<new-' + phase + '-inputs>/' + role + '.binding.json'
        previous_args = []
        for previous_phase in list(PHASES)[:list(PHASES).index(phase)]:
            previous_args.extend(['--previous-binding', '<new-' + previous_phase + '-inputs>/role-preparation.json'])
        plans[phase] = {'role': role, 'template_sha256': sha(path.read_bytes()),
            'apply': ['kubectl', '--context=<actual-context>', 'apply', '-f', '<rendered-' + phase + '.json>'],
            'bind': ['python3', '-I', '-B', 'SOURCE/update/tools/gpu_b_sup16_operating_prepare.py', 'bind-role',
                '--phase', phase, '--source-root', 'SOURCE', '--source-commit', context['commit'],
                '--manifest-sha256', context['manifest_sha256'], '--static-packet', 'STATIC_PACKET',
                '--static-sha256', args.static_sha256, '--environment', '<actual-' + phase + '-ENV.json>',
                '--target', '<actual-' + phase + '-target.json>', '--model-manifest', '<actual-reference-manifest.json>',
                '--model-manifest-sha256', '<actual-reference-manifest-SHA>',
                *previous_args, '--output', '<new-' + phase + '-inputs>'],
            'writer': static.commands(context, meta.get('models', {}))['writers'][role],
            'binding_publication': publish,
            'installer': ['python3', '-I', '-S', '-B', '/guard-prepare/native17_install.py', '--tool-sha256',
                sha(config['data']['native17_install.py'].encode()), '--manifest-sha256', '<actual-native-manifest-file-SHA>'],
            'installer_stdin': '<new-' + phase + '-inputs>/installer-payload.json',
            'writer_requires': 'Publish exact role binding and native assets in original asset90; use actual closed same-session PID/start journal after existing capture, never invented rows.',
            'capture': ['python3', '-I', '-B', 'OPERATOR_ASSETS/capture_normal.py', '--source-root', 'SOURCE',
                '--source-commit', context['commit'], '--manifest-sha256', context['manifest_sha256'],
                '--tool-sha256', CAPTURE_SHA, '--environment', '<actual-' + phase + '-ENV.json>',
                '--target', '<actual-' + phase + '-target.json>', '--prepared', '<new-' + phase + '-inputs>/native17/manifest.json',
                '--output', '<new-' + phase + '-capture>', '--kubectl-binary', '<actual-kubectl>', '--execute'],
            'capture_scope': 'One existing reader/120s, same live engine/owner. Native diagnostic only, not a paired product run. Command is emitted, not executed.',
            'cleanup_before_next': 'Export/verify exact raw bytes, source/inputs/identity/normal result/native+boot(if created)/stdout/stderr; S2 verifies UID+guard.run ownership and uses existing namespace cleanup/absence before next GPU allocation. No old resource deletion or timer reset.'}
    result = {'schema': 'byul-sup16-operating-plan/1', 'source': context, 'source_static_packet_sha256': args.static_sha256,
        'phase_order': list(PHASES), 'phases': plans, 'gpus': 1, 'tp': 1,
        'operator_assets': {'capture_normal.py': CAPTURE_SHA, 'source_owner_verify.py': static.PINS['source_owner.py']},
        'environment_delta_to_record': 'S2-observed g6.xlarge versus selected g6.2xlarge CPU/RAM/runtime; no assumed Ready improvement.',
        'new_observation': 'Fixed pair exact-LF/native17 path and original record preservation, distinct from old GB09/GB11 and normal-only replay.',
        'deadlines_seconds': {'asset_wait': 90, 'collection': 120, 'Pod': 600},
        'runtime_values_supplied': False, 'executed': False, 'ready_for_execution': False,
        'product_pair_blocker': PAIR_BLOCKER, 'product_factory_EVD_RES_RPT_executed': False,
        'product_followup': 'Only a supported live paired factory with actual ENV/POL/target/prepared may select/run/judge/report/export/restore/saved-pack rejudge. This plan adds no archived-role adapter.',
        'stop_handoff': 'S2 alone owns existing EC2 stop/backstop/cost protection. On any failed/uncertain cleanup preserve first error plus later errors; export exact evidence before owned cleanup; no force/reset.'}
    write(out / 'operating-plan.json', result)
    return result


def bind_role(args):
    meta, _, context = packet(args)
    env_raw = static.read(args.environment)
    env = load_environment(args.environment)
    if static.read(args.environment) != env_raw:
        raise ValueError('SUP16_OPERATING_ENV_INPUT_CHANGED')
    if (env.engine.name, env.engine.version, env.engine.image_digest) != ('vllm', '0.31.0', static.GPU_IMAGE):
        raise ValueError('SUP16_OPERATING_RUNTIME_SUPPORT_MISMATCH')
    target_raw = static.read(args.target)
    target = static.strict_json(target_raw, limit=65536)
    actual = validate_capture_target(target, env)
    role = PHASES[args.phase]
    raw_manifest = static.read(args.model_manifest, args.model_manifest_sha256)
    if args.model_manifest_sha256 != meta.get('models', {}).get('normal', {}).get('reference_manifest_sha256'):
        raise ValueError('SUP16_OPERATING_REFERENCE_CHANGED')
    scope = build_scope(env, target, args.model_manifest, '/models', nonsecret_model_weights=True, hash_model_bytes=True)
    if scope['manifest_bytes_sha256'] != args.model_manifest_sha256:
        raise ValueError('SUP16_OPERATING_REFERENCE_CHANGED')
    previous = [document(path) for path in args.previous_binding]
    for old in previous:
        if (old.get('schema') != 'byul-sup16-role-preparation/1' or old.get('phase') not in PHASES
                or old.get('source', {}).get('commit') != context['commit']):
            raise ValueError('SUP16_OPERATING_PREVIOUS_BINDING_MISMATCH')
        if old['engine']['pod_uid'] == actual['engine']['pod_uid']:
            raise ValueError('SUP16_OPERATING_FRESH_POD_GENERATION_REQUIRED')
    out = static.reserve_preparation_output(args.output, protected=[static.ROOT, args.static_packet,
        args.environment, args.target, args.model_manifest, *args.previous_binding])
    write(out / 'model-read-scope.json', scope)
    prepare_assets(out / 'native17', args.source_root, profile='vllm-native-lifecycle-metadata/17', model_read_scope=out / 'model-read-scope.json')
    prepared = load_prepared(out / 'native17/manifest.json')
    validate_binding(prepared, env, target)
    binding = {'role': role, 'session': prepared['session'], 'environment_ref': env.ref().model_dump(mode='json'),
        'target_sha256': sha(canonical_json(target)), 'source_sha256': static.PINS['pair.py']}
    write(out / (role + '.binding.json'), binding)
    write(out / 'target.json', target)
    raw = static.read(out / 'native17/manifest.json')
    write(out / 'installer-payload.json', {'manifest': base64.b64encode(raw).decode(),
        'assets': {n: base64.b64encode(static.read(out / 'native17' / n, v['sha256'])).decode() for n, v in prepared['assets'].items()}})
    receipt = {'schema': 'byul-sup16-role-preparation/1', 'source': context, 'phase': args.phase, 'role': role,
        'engine': actual['engine'], 'environment_ref': binding['environment_ref'], 'input_metadata_origin': env.data_origin.value,
        'input_files': {'environment_sha256': sha(env_raw), 'target_sha256': sha(target_raw),
            'reference_manifest_sha256': sha(raw_manifest)}, 'target_sha256': binding['target_sha256'],
        'prepared_sha256': sha(canonical_json(prepared)), 'prepared_manifest_file_sha256': sha(raw),
        'binding_file_sha256': sha(canonical_json(binding)), 'session': prepared['session'],
        'supplied_previous_phase_count': len(previous), 'live_identity_checked': False,
        'native_assets_created_from_supplied_files': True, 'engine_or_AWS_started': False,
        'ready_for_execution': False, 'product_pair_blocker': PAIR_BLOCKER,
        'next': 'S2 publishes this exact binding+installer payload to the observed preparation container inside the original90; existing live runtime probe/installer/capture must confirm actual generation. This receipt is not live ownership/admission or policy approval.'}
    write(out / 'role-preparation.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ('plan', 'bind-role'):
        p = commands.add_parser(action)
        for name in ('source-root', 'static-packet', 'output'):
            p.add_argument('--' + name, type=Path, required=True)
        for name in ('source-commit', 'manifest-sha256', 'static-sha256'):
            p.add_argument('--' + name, required=True)
        if action == 'plan':
            p.add_argument('--pair-template', type=Path, required=True)
            p.add_argument('--capture-helper', type=Path, required=True)
        else:
            p.add_argument('--phase', choices=PHASES, required=True)
            for name in ('environment', 'target', 'model-manifest'):
                p.add_argument('--' + name, type=Path, required=True)
            p.add_argument('--model-manifest-sha256', required=True)
            p.add_argument('--previous-binding', type=Path, action='append', default=[])
    args = parser.parse_args()
    print(json.dumps({'plan': plan, 'bind-role': bind_role}[args.action](args), sort_keys=True))


if __name__ == '__main__':
    main()
