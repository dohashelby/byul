"""Prepared assets for the engine callback sensor. Prepares files, starts nothing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import secrets

from guard.adapters import vllm_engine_callback_sensor as sensor

SCHEMA = 'guard-engine-callback-assets/1'
PACKAGE = 'guard_engine_callbacks'
MODULES = ('vllm_cache_callback', 'vllm_comm_buffer_callback', 'vllm_distributed_callback', 'vllm_engine_callback_sensor', 'vllm_graph_replay_callback', 'vllm_kernel_bounds_callback',
    'vllm_lifecycle_sensor', 'vllm_rank_failure_sensor', 'vllm_weights_lifetime_callback', 'vllm_worker_generation_contract',
    'vllm_worker_startup_contract')
SITECUSTOMIZE = (f'from {PACKAGE} import vllm_engine_callback_sensor\n'
    'vllm_engine_callback_sensor.install()\n')


def _sources():
    root = Path(sensor.__file__).parent
    values = {f'{PACKAGE}/{name}.py': (root / f'{name}.py').read_bytes() for name in MODULES}
    values[f'{PACKAGE}/__init__.py'] = b''
    values['sitecustomize.py'] = SITECUSTOMIZE.encode()
    return values


def prepare_assets(output, *, lab_faults=False, lab_kernel_checks=False):
    output = Path(output).absolute()
    if output.exists():
        raise ValueError('ENGINE_CALLBACK_OUTPUT_EXISTS')
    (output / PACKAGE).mkdir(parents=True)
    values = _sources(); session = secrets.token_hex(16)
    for name, data in values.items():
        (output / name).write_bytes(data)
    (output / 'settings.json').write_text(json.dumps({'session': session, 'secret': secrets.token_hex(32), 'lab_faults': lab_faults, 'lab_kernel_checks': lab_kernel_checks}), encoding='utf-8')
    manifest = {'schema': SCHEMA, 'profile': sensor.PROFILE, 'session': session, 'engine_started': False, 'lab_faults': lab_faults, 'lab_kernel_checks': lab_kernel_checks,
        'assets': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(values.items())}}
    (output / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
    return manifest


def load_prepared(path):
    """Accept only assets built from this product's exact callback sources."""
    root = Path(path)
    root = root if root.is_dir() else root.parent
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('schema') != SCHEMA or manifest.get('profile') != sensor.PROFILE
            or manifest.get('assets') != {n: hashlib.sha256(d).hexdigest() for n, d in sorted(_sources().items())}
            or any(hashlib.sha256((root / n).read_bytes()).hexdigest() != d for n, d in manifest['assets'].items())):
        raise ValueError('ENGINE_CALLBACK_PREPARED_INVALID')
    return manifest


def load_secret(path):
    root = Path(path)
    root = root if root.is_dir() else root.parent
    return bytes.fromhex(json.loads((root / 'settings.json').read_text(encoding='utf-8'))['secret'])
