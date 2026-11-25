"""Extract one saved normal SUP-06 worker record as an offline input fragment."""

import argparse
from pathlib import Path

from guard.collectors import engine_callbacks, gpu_common_inputs, worker_shard_producer
from guard.executors import common_consumption
from guard.executors.kubernetes_lifecycle_capture import validate_target
from guard.executors.worker_shard_archive import (
    CAPTURE_LIMIT, CAPTURE_SCHEMA, captured_process_epochs, expected_worker_ranks, mounted_source_expectation,
    verify_saved_pages,
)
from guard.models import Environment, canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.engine_http import ENGINE_CALLBACK_PROFILE, KubernetesBindingError, KubernetesHTTP
from guard.update_validation import _plain_path, reserve_output
from tools.mvp_a_input_preflight import _read
from tools.mvp_versions import versions

SELECTION_SCHEMA = 'guard-mvp-sup06-worker-shard-selection/1'
OUTPUT_SCHEMA = 'guard-mvp-sup06-worker-shard-input/1'
OUTPUT_NAME = 'worker-shard-input.json'
RECORD_NAME = 'worker-shard-load.json'
_LIMITS = {'environment': 262144, 'lifecycle_target': 65536, 'callback_target': 65536,
           'callback_prepared': 262144, 'capture': CAPTURE_LIMIT}
_INVALID = 'MVP_SUP06_WORKER_SHARD_SELECTION_INVALID'
_CHANGED = 'MVP_SUP06_WORKER_SHARD_SOURCE_CHANGED'


def _offline_runner(*args, **kwargs):
    raise AssertionError('Saved input validation must not start a runtime operation')


def prepare(selection_path, expected_selection_sha256, output, *, require_finalized=False):
    """Materialize an offline input and optionally verify every native writer's closure."""
    snapshots = []

    def capture(path, limit, minimum=1):
        path = _plain_path(Path(path))
        raw, identity = _read(path, min_bytes=minimum, max_bytes=limit)
        snapshots.append((path, sha256_hex(raw), identity, minimum, limit))
        return path, raw

    def document(path, digest, limit):
        if not gpu_common_inputs._check(digest, 'sha'):
            raise ValueError(_INVALID)
        path, raw = capture(path, limit)
        if sha256_hex(raw) != digest:
            raise ValueError(_CHANGED)
        return path, strict_json(raw, limit=limit)

    _, selection = document(selection_path, expected_selection_sha256, 65536)
    releases = versions()
    required = {'schema', 'side', 'files'}
    if (type(selection) is not dict or selection.keys() not in (required, required | {'page_archive'})
            or selection['schema'] != SELECTION_SCHEMA or type(selection['side']) is not str
            or selection['side'] not in releases or type(selection['files']) is not dict
            or selection['files'].keys() != _LIMITS.keys()):
        raise ValueError(_INVALID)
    archive = None
    if 'page_archive' in selection:
        directory = selection['page_archive']
        if type(directory) is not str or not directory:
            raise ValueError(_INVALID)
        archive = _plain_path(Path(directory))
    paths, documents = {}, {}
    for key, pointer in selection['files'].items():
        if (type(pointer) is not dict or pointer.keys() != {'path', 'sha256'}
                or type(pointer['path']) is not str or not pointer['path']):
            raise ValueError(_INVALID)
        paths[key], documents[key] = document(pointer['path'], pointer['sha256'], _LIMITS[key])

    env = Environment.model_validate(documents['environment'])
    release = releases[selection['side']]
    if (not env.hash_ok() or (env.engine.name, env.engine.version, env.engine.image_digest)
            != (release['engine'], release['version'], release['image'])):
        raise ValueError('MVP_SUP06_WORKER_SHARD_ENVIRONMENT_MISMATCH')
    target = documents['callback_target']
    if (type(target) is not dict or target.get('lab') is not False or target.get('synthetic', False) is not False
            or target.get('observed_version', env.engine.version) != env.engine.version):
        raise ValueError('MVP_SUP06_WORKER_SHARD_NORMAL_TARGET_REQUIRED')
    try:
        callback = KubernetesHTTP(target, env, duration=1, runner=_offline_runner, profile=ENGINE_CALLBACK_PROFILE)
    except KubernetesBindingError:
        raise ValueError('MVP_SUP06_WORKER_SHARD_OWNER_MISMATCH') from None
    lifecycle = validate_target(documents['lifecycle_target'], env)
    if any(callback.binding[key] != lifecycle[key] for key in ('context', 'run_id', 'engine')):
        raise ValueError('MVP_SUP06_WORKER_SHARD_OWNER_MISMATCH')

    prepared = documents['callback_prepared']
    asset_root = paths['callback_prepared'].parent
    assets = engine_callbacks._sources()
    if (paths['callback_prepared'].name != 'manifest.json' or type(prepared) is not dict
            or prepared.get('schema') != engine_callbacks.SCHEMA
            or prepared.get('profile') != engine_callbacks.sensor.PROFILE or prepared.get('worker_shards') is not True
            or prepared.get('lab_faults') is not False or prepared.get('lab_kernel_checks') is not False
            or prepared.get('assets') != {name: sha256_hex(raw) for name, raw in assets.items()}):
        raise ValueError('MVP_SUP06_WORKER_SHARD_ASSETS_INVALID')
    for name, expected in assets.items():
        _, raw = capture(asset_root / name, len(expected), minimum=len(expected))
        if raw != expected:
            raise ValueError('MVP_SUP06_WORKER_SHARD_ASSETS_INVALID')

    scope = {'environment_ref': env.ref().model_dump(mode='json'), 'target_sha256': callback.target_sha256,
             'prepared_sha256': sha256_hex(canonical_json(prepared)), 'session': prepared.get('session')}
    wrapper = documents['capture']
    if (type(wrapper) is not dict or wrapper.get('schema') != CAPTURE_SCHEMA
            or wrapper.get('session') != scope['session'] or type(wrapper.get('records')) is not dict):
        raise ValueError('MVP_SUP06_WORKER_SHARD_CAPTURE_INVALID')
    entry = wrapper['records'].get('worker-shard-load')
    if type(entry) is not dict or entry.keys() != {'record', 'validation'}:
        raise ValueError('MVP_SUP06_WORKER_SHARD_CAPTURE_INVALID')
    record = entry['record']
    if type(record) is not dict or record.get('schema') != gpu_common_inputs.SCHEMA:
        raise ValueError('MVP_SUP06_WORKER_SHARD_CAPTURE_INVALID')
    common_consumption.validate_record(record, scope, kind='worker-shard-load', check_id='SUP-06')
    epochs = captured_process_epochs(wrapper)
    owner = lifecycle['engine']
    generation = owner['pod_uid'], owner['container_id'].removeprefix('containerd://'), owner['restart_count']
    if (record['producer'] != worker_shard_producer.producer()
            or record['completeness']['source_ended'] is not False
            or any((row['pod_uid'], row['container_id'], row['restart_count']) != generation
                   or not epochs.get((row['worker_pid'], row['start_ticks']))
                   for row in record['bindings'])):
        raise ValueError('MVP_SUP06_WORKER_SHARD_PREFIX_MISMATCH')
    source_sha256 = {'target': selection['files']['callback_target']['sha256'],
                     'prepared': selection['files']['callback_prepared']['sha256']}
    mounted_source = mounted_source_expectation(target, prepared, callback.binding, source_sha256)
    witnesses = verify_saved_pages(wrapper, archive, document, record=record, epochs=epochs, pod=owner, scope=scope,
                                   version=env.engine.version, expected_ranks=expected_worker_ranks(env),
                                   mounted_source=mounted_source, require_finalized=require_finalized)
    record_raw = canonical_json(record)
    if len(record_raw) > common_consumption.MAX_INPUT_BYTES:
        raise ValueError('MVP_SUP06_WORKER_SHARD_CAPTURE_INVALID')

    for path, digest, identity, minimum, limit in snapshots:
        try:
            current = _read(_plain_path(path), min_bytes=minimum, max_bytes=limit)
        except (OSError, ValueError):
            raise ValueError(_CHANGED) from None
        if (sha256_hex(current[0]), current[1]) != (digest, identity):
            raise ValueError(_CHANGED)
    protected = [Path(__file__).resolve().parents[1], asset_root, *(snapshot[0] for snapshot in snapshots)]
    if archive is not None:
        protected.append(archive)
    directory = reserve_output(Path(output), protected=protected)
    directory.chmod(0o700)
    record_path = directory / RECORD_NAME
    receipt = {'schema': OUTPUT_SCHEMA, 'side': selection['side'], 'role': 'normal',
               'selection_sha256': expected_selection_sha256,
               'source_file_sha256': {key: pointer['sha256'] for key, pointer in selection['files'].items()},
               'worker_shard_input': {'path': str(record_path), 'sha256': sha256_hex(record_raw), 'scope': scope,
                                      'callback_target': str(paths['callback_target']),
                                      'callback_prepared': str(paths['callback_prepared']), 'data_origin': 'EXAMPLE'},
               'source_ended': False, 'owner_binding_verified': False, 'producer_authenticated': False,
               'ready_for_execution': False}
    if archive is not None:
        receipt['worker_shard_input']['capture_archive'] = {
            'path': str(paths['capture']), 'sha256': selection['files']['capture']['sha256'],
            'page_archive': str(archive)}
        receipt['worker_shard_input']['callback_source_sha256'] = source_sha256
    if witnesses is not None:
        receipt['native_rank_witnesses'] = {'capture_sha256': selection['files']['capture']['sha256'], **witnesses}
    for path, raw in ((record_path, record_raw), (directory / OUTPUT_NAME, canonical_json(receipt))):
        with path.open('xb') as stream:
            if stream.write(raw) != len(raw):
                raise OSError('MVP_SUP06_WORKER_SHARD_OUTPUT_INCOMPLETE')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', type=Path, required=True,
                        help='Saved side and independently pinned ENV, lifecycle and callback inputs')
    parser.add_argument('--selection-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True, help='New directory for the record and input fragment')
    parser.add_argument('--require-finalized', action='store_true',
                        help='Require an intact archive and verified journal closure for every expected rank')
    args = parser.parse_args(argv)
    prepare(args.selection, args.selection_sha256, args.output, require_finalized=args.require_finalized)
    print(args.output / OUTPUT_NAME)


if __name__ == '__main__':
    main()
