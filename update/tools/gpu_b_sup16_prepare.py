"""Freeze existing SUP16 assets and argv offline; never run an engine or writer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

if __name__ == '__main__':
    sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'src'), str(Path(__file__).resolve().parents[1])]

from guard.adapters.vllm_file_read_sensor import dependency
from guard.collectors.model_manifest import _load_manifest
from guard.executors.owned_capture import strict_json
from guard.models import canonical_json
from tools.lif14_stop_restart_prepare import reserve_preparation_output

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS = 'update/docs/gpu-b/consolidation-20261106/implementation-snapshots'
PINS = {
    'pair.py': 'ed9616091017c486cba9bb8e1b804969e6f1944c5fdbbd4b6c7408fa009bb519',
    'ordering.py': '2485ba44ce8398f833477b79a04c86624cc254316b566f399a4bc9b5f085e4bd',
    'stage.py': 'c61b7059419659419aa973c072095f5a0ca744c3a24072472ce63074ad9d6f94',
    'prepare_pair.py': 'c1aff2108495722676977c46638ad64ea9dc39a12270bb1e239ce74a383a158d',
    'source_owner.py': 'fb12dbe8d82d6bc027b613a477d6e1a432fa4693c46c6f1380cb38cecdf79fc9',
    'original-procedure.json': 'e48fefc221cbbbba3f365a21fba61c5b27cb94fe861299c8657dc6b4cdba5831',
}
SENSOR_SHA = 'e32c0a53e810735f9eaca1a648397af49ca259d264020fd0f99193c401b2697d'
GPU_IMAGE = 'vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b'
RUNTIME_MISSING = [
    'actual sealed ENV with supported image/runtime/model/GPU identity',
    'distinct actual normal/challenge targets and per-role prepared/session',
    'current policy validity window and policy authority',
    'actual Pod UID/container/PID/start, native read file and owner binding',
    'original deadline Ready, publication/exit/recovery and owned cleanup evidence',
]


def read(path, expected=None):
    path = Path(path).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError('SUP16_PREPARATION_INPUT_LINK')
    return dependency(path, expected)


def module(raw, path):
    # Call only pre-existing, exact-pinned preparation helpers, never writer.run.
    scope = {'__name__': '_sup16_fixed_preparation_asset', '__file__': str(path)}
    exec(compile(raw, str(path), 'exec'), scope)
    return scope


def source_context(root, commit, manifest_sha, owner_raw, owner_path):
    root = Path(root).absolute()
    if root != ROOT or re.fullmatch('[0-9a-f]{40}', commit or '') is None or re.fullmatch('[0-9a-f]{64}', manifest_sha or '') is None:
        raise ValueError('SUP16_PREPARATION_FIXED_SOURCE_REQUIRED')
    owner = module(owner_raw, owner_path)
    verified = owner['verify_source_owned'](root, commit, manifest_sha)
    # The original owner check protects product sources. Also pin this new tool
    # and the already reviewed public inputs without changing its allowed scope.
    repo, prefix, _, _ = owner['_git_context'](root)
    paths = ['update/tools/gpu_b_sup16_prepare.py', 'update/tools/gpu_b_sup16_pair_writer.py',
             SNAPSHOTS + '/sup16-write_normal_boot.py.txt', 'update/tools/lif14_stop_restart_prepare.py']
    owner['_git'](repo, prefix, ['ls-files', '--error-unmatch', *paths])
    owner['_git'](repo, prefix, ['diff', '--exit-code', 'HEAD', '--', *paths])
    return {'commit': commit, 'manifest_sha256': manifest_sha, 'verification': verified,
            'preparation_tool_sha256': hashlib.sha256(read(__file__)).hexdigest()}


def check_model(root, role, pair):
    """Reuse the fixed pair's inventory and SHA rules; only read existing bytes."""
    root = Path(root).absolute()
    receipt = strict_json(read(root / 'staging-receipt.json'), limit=262144)
    manifest_raw = read(root / 'reference-manifest.json')
    manifest = _load_manifest(strict_json(manifest_raw, limit=262144), None)
    partial = role == 'challenge'
    if (receipt.get('schema') != 'byul-sup16-staged-artifact/1' or receipt.get('role') != role
            or receipt.get('producer_sha256') != PINS['stage.py']
            or receipt.get('actual_partial_promoted_as_LAB_fault') is not partial
            or receipt.get('partial_source_sha256') != (pair['PARTIAL_SHA'] if partial else None)
            or hashlib.sha256(manifest_raw).hexdigest() != receipt.get('reference_manifest_sha256')):
        raise ValueError('SUP16_PREPARATION_ROLE_RECEIPT_MISMATCH')
    rows = receipt.get('actual_files')
    if (type(rows) is not list or len(rows) != len(pair['MODEL_FILES'])
            or any(type(r) is not dict or set(r) != {'path', 'bytes', 'sha256'} for r in rows)
            or {r['path'] for r in rows} != pair['MODEL_FILES']):
        raise ValueError('SUP16_PREPARATION_MODEL_INVENTORY_MISMATCH')
    expected = {r['path']: r for r in manifest['files']}
    if set(expected) != pair['MODEL_FILES']:
        raise ValueError('SUP16_PREPARATION_MODEL_INVENTORY_MISMATCH')
    for row in rows:
        if (type(row['path']) is not str or Path(row['path']).name != row['path']
                or type(row['bytes']) is not int or type(row['sha256']) is not str):
            raise ValueError('SUP16_PREPARATION_MODEL_ROW_INVALID')
        path = root / row['path']
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)) or not stat.S_ISREG(path.stat().st_mode):
            raise ValueError('SUP16_PREPARATION_MODEL_LINK_OR_TYPE')
        before = path.stat()
        digest = pair['digest'](path)
        after = path.stat()
        key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if key(before) != key(after) or before.st_size != row['bytes'] or digest != row['sha256']:
            raise ValueError('SUP16_PREPARATION_MODEL_BYTES_CHANGED')
        reference = expected[row['path']]
        if row['path'] == 'model.safetensors':
            if (row['sha256'], row['bytes']) != ((pair['PARTIAL_SHA'], pair['PARTIAL_BYTES']) if partial else (pair['REFERENCE_SHA'], pair['REFERENCE_BYTES'])):
                raise ValueError('SUP16_PREPARATION_FIXED_ROLE_ARTIFACT_REQUIRED')
            if (reference['sha256'], reference['bytes']) != (pair['REFERENCE_SHA'], pair['REFERENCE_BYTES']):
                raise ValueError('SUP16_PREPARATION_FULL_REFERENCE_REQUIRED')
        elif row != reference:
            raise ValueError('SUP16_PREPARATION_AUX_REFERENCE_MISMATCH')
    return {'role': role, 'root': str(root), 'actual_files': rows,
            'reference_manifest_sha256': receipt['reference_manifest_sha256'],
            'staging_receipt_sha256': hashlib.sha256(read(root / 'staging-receipt.json')).hexdigest(),
            'model_id': manifest['model_id'], 'inventory_sha256': manifest['inventory_sha256'],
            'worker_consumption_verified': False}


def commands(source, models):
    prepare = ['python3', '-I', '-B', 'ASSETS/prepare_pair.py', '--source-root', 'ACTUAL_SOURCE',
               '--source-commit', source['commit'], '--manifest-sha256', source['manifest_sha256'],
               '--tool-sha256', PINS['prepare_pair.py'], '--source-owner-helper', 'ASSETS/source_owner.py',
               '--record-producer', 'ASSETS/pair.py', '--record-producer-sha256', PINS['pair.py'],
               '--environment', '<actual-ENV.json>', '--normal-target', '<actual-normal.target.json>',
               '--challenge-target', '<actual-challenge.target.json>', '--model-manifest', '<actual-reference-manifest.json>',
               '--model-manifest-sha256', '<actual-reference-manifest-file-SHA>',
               '--validity-start', '<actual-policy-window-start>', '--validity-end', '<actual-policy-window-end>',
               '--kubectl-binary', '<actual-kubectl>', '--output', '<new-runtime-preparation-output>']
    writers = {}
    for role in ('normal', 'challenge'):
        writers[role] = ['python3', '-I', '-B', '/guard-prepare/pair.py', '--tool-sha256', PINS['pair.py'],
            '--binding', f'/guard-control/{role}.binding.json', '--binding-sha256', f'<actual-{role}-binding-SHA>',
            '--staging-receipt', '/models/staging-receipt.json', '--staging-receipt-sha256',
            models[role]['staging_receipt_sha256'] if role in models else f'<actual-{role}-staging-receipt-SHA>',
            '--staging-producer', '/guard-prepare/stage.py', '--staging-producer-sha256', PINS['stage.py'],
            '--ordering-helper', '/guard-prepare/ordering.py', '--native-recording',
            f'/guard-lifecycle/data/<actual-{role}-session>/pid-<actual-PID>-<actual-start_ticks>.jsonl']
    return {'executed': False, 'placeholders_are_not_runtime_values': True, 'prepare_pair': prepare,
            'writers': writers, 'order': ['normal-before', 'challenge-partial', 'recovered-normal'],
            'before_each_role': 'Existing installer/native17 load_prepared and model scope/owner checks; fresh role session and mount; never reuse an old sealed session.',
            'after_each_role': 'Preserve original stdout/stderr/native bytes/boot record; seal/capture through existing collector; export EVD/RES/RPT and exact Refs, restore and saved-pack rejudge when actually created.',
            'cleanup_and_deadlines': 'assets/original-procedure.json timing/owned cleanup only; old source/writer argv are historical and must not be executed. Existing S2 ownership/barrier/stop procedure applies.',
            'deadlines_seconds': {'asset_wait': 90, 'collection': 120, 'Pod': 600},
            'SUP15_150_second_deadline_is_not_SUP16': True,
            'no_post_exit_or_full_pair_support_added': True}


def prepare(args):
    root = Path(args.source_root).absolute()
    paths = {'pair.py': root / 'update/tools/gpu_b_sup16_pair_writer.py',
             'ordering.py': root / SNAPSHOTS / 'sup16-write_normal_boot.py.txt',
             'stage.py': Path(args.stage), 'prepare_pair.py': Path(args.prepare_pair),
             'source_owner.py': Path(args.source_owner), 'original-procedure.json': Path(args.procedure)}
    # Validate before reserving output. Missing inputs never become empty assets.
    raws = {name: read(path, PINS[name]) for name, path in paths.items()}
    source = source_context(root, args.source_commit, args.manifest_sha256, raws['source_owner.py'], paths['source_owner.py'])
    pair = module(raws['pair.py'], paths['pair.py'])
    ordering = module(raws['ordering.py'], paths['ordering.py'])
    sensor = root / 'update/src/guard/adapters/vllm_file_read_sensor.py'
    read(sensor, SENSOR_SHA)
    if pair['ORDERING_HELPER_SHA256'] != PINS['ordering.py'] or 'native17_ordering_from_raw' not in ordering:
        raise ValueError('SUP16_PREPARATION_ASSET_COMPATIBILITY_MISMATCH')
    models, missing = {}, list(RUNTIME_MISSING)
    for role in ('normal', 'challenge'):
        model_root = getattr(args, role + '_model', None)
        if model_root is None:
            missing.append(role + ' staged model directory')
        else:
            models[role] = check_model(model_root, role, pair)
    if len(models) == 2 and (models['normal']['root'] == models['challenge']['root']
            or any(models['normal'][k] != models['challenge'][k] for k in ('reference_manifest_sha256', 'model_id', 'inventory_sha256'))):
        raise ValueError('SUP16_PREPARATION_DISTINCT_ROLES_SAME_REFERENCE_REQUIRED')
    out = reserve_preparation_output(args.output, protected=[root, *paths.values(),
        *(Path(getattr(args, r + '_model')) for r in ('normal', 'challenge') if getattr(args, r + '_model', None))])
    assets = out / 'assets'
    assets.mkdir(mode=0o700)
    for name, raw in raws.items():
        with (assets / name).open('xb') as stream:
            stream.write(raw)
    command_plan = commands(source, models)
    result = {'schema': 'byul-sup16-static-preparation/1', 'check_id': 'SUP-16', 'version': 1,
              'source': source, 'assets': {n: {'sha256': PINS[n], 'bytes': len(v)} for n, v in raws.items()},
              'same_record_producer_for_both_roles': PINS['pair.py'], 'native17_sensor_sha256': SENSOR_SHA,
              'supported_engine': {'name': 'vllm', 'version': '0.31.0', 'image': GPU_IMAGE},
              'models': models, 'static_assets_prepared': True, 'both_model_copies_verified': len(models) == 2,
              'missing_runtime_inputs': missing, 'ready_for_execution': False,
              'producer_authenticated': False, 'worker_consumption_verified': False,
              'native_assets_generated': False, 'new_ENV_target_prepared_session_generated': False,
              'engine_GPU_AWS_or_writer_started': False, 'official': False}
    result['commands_sha256'] = hashlib.sha256(canonical_json(command_plan)).hexdigest()
    for name, value in [('commands.json', command_plan), ('preparation.json', result)]:
        with (out / name).open('xb') as stream:
            stream.write(canonical_json(value))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-root', 'stage', 'prepare-pair', 'source-owner', 'procedure', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('source-commit', 'manifest-sha256'):
        parser.add_argument('--' + name, required=True)
    for role in ('normal', 'challenge'):
        parser.add_argument('--' + role + '-model', type=Path)
    result = prepare(parser.parse_args())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
