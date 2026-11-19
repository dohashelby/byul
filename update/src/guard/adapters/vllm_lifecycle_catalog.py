"""Reviewed native lifecycle rows, independent of release-specific source code."""
from __future__ import annotations

import json
from pathlib import Path
import re

from . import vllm_configured_lifecycle_sensor as template
from . import vllm_observer_abi as abi
from . import vllm_baseline_startup_composition as startup
from . import vllm_baseline_startup as startup_native
from . import vllm_baseline_profiler_composition as direct_profiler
from . import vllm_baseline_profiler as profiler_native
from . import vllm_baseline_stream_composition as baseline_stream
from . import vllm_approved_file_read_composition as file_read


def _catalog():
    data = json.loads((Path(__file__).with_name('assets') / 'vllm_native_release_rows.json').read_bytes())
    if data.get('schema') != 'guard-native-release-rows/1':
        raise ValueError('LIFECYCLE_CATALOG_INVALID')
    rows = data['profiles']
    baseline = next((row for row in rows if row.get('profile') == baseline_stream.BASE_PROFILE), None)
    if len({row['profile'] for row in rows}) != len(rows):
        raise ValueError('LIFECYCLE_CATALOG_INVALID')
    for row in rows:
        points = {}
        for module, source in row['sources'].items():
            if (re.fullmatch(r'[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+', module) is None
                    or re.fullmatch(r'[0-9a-f]{64}', source['sha256']) is None
                    or type(source['bytes']) is not int or source['bytes'] <= 0):
                raise ValueError('LIFECYCLE_CATALOG_INVALID')
            for cls, methods in source['classes'].items():
                for method in methods:
                    point = cls + '.' + method
                    if point in points:
                        raise ValueError('LIFECYCLE_POINT_AMBIGUOUS')
                    points[point] = module
        if (not points and not {'startup', 'baseline_profiler', 'approved_file_read'} & row.keys()
                or not set(row['void_return_argument_ids']) <= points.keys()):
            raise ValueError('LIFECYCLE_CATALOG_INVALID')
        if 'approved_file_read' in row and (row['approved_file_read'] is not True
                or row['engine'] != 'vllm' or row['engine_mode'] != 'V1'
                or row['profile'] != file_read.PROFILES.get(row['version'])
                or row['schema'] != row['profile'].replace('vllm-native-lifecycle-metadata', 'guard-native-lifecycle-events')
                or row['runtimes'] != {file_read.RUNTIME: ['posix']} or points
                or set(row['sources']) != {file_read.LOADER, file_read.WEIGHTS}
                or {'request_binding', 'startup', 'baseline_profiler', 'baseline_stream'} & row.keys()):
            raise ValueError('LIFECYCLE_FILE_READ_CATALOG_INVALID')
        if 'startup' in row and (row['startup'] != 'baseline-v1'
                or row['profile'] != startup.STARTUP_PROFILE or row['schema'] != startup.STARTUP_SCHEMA
                or row.get('startup_image') not in row['images'] or 'request_binding' in row
                or row['sources'] != {name: {'sha256': source['sha256'], 'bytes': source['bytes'], 'classes': {}}
                                      for name, source in startup_native.ROWS.items()}
                or row['void_return_argument_ids'] != []):
            raise ValueError('LIFECYCLE_STARTUP_CATALOG_INVALID')
        if 'baseline_profiler' in row and (row['baseline_profiler'] != 'worker-direct-torch'
                or row['profile'] != direct_profiler.PROFILER_PROFILE or row['schema'] != direct_profiler.PROFILER_SCHEMA
                or row.get('profiler_image') not in row['images'] or 'request_binding' in row or 'startup' in row
                or row['sources'] != {profiler_native.WORKER: {'sha256': profiler_native.SOURCE['sha256'],
                    'bytes': profiler_native.SOURCE['bytes'], 'classes': {}}} or row['void_return_argument_ids'] != []):
            raise ValueError('LIFECYCLE_PROFILER_CATALOG_INVALID')
        if 'baseline_stream' in row and (baseline is None or row['baseline_stream'] != 'uvicorn-0.34-wheel-source'
                or row['profile'] != baseline_stream.PROFILE or row['schema'] != baseline_stream.SCHEMA
                or row['version'] != baseline['version'] or row['commit'] != baseline['commit']
                or row['images'] != baseline['images'] or row['runtimes'] != baseline['runtimes']
                or row.get('request_binding') != baseline.get('request_binding')
                or row['void_return_argument_ids'] != baseline['void_return_argument_ids']
                or {name: source for name, source in row['sources'].items() if name not in baseline_stream.SOURCE_ROWS}
                   != baseline['sources']
                or any(row['sources'].get(name) != source
                       for name, source in baseline_stream.SOURCE_LAYOUT.items())):
            raise ValueError('LIFECYCLE_STREAM_CATALOG_INVALID')
        binding = row.get('request_binding')
        if binding is not None and (type(binding) is not dict or set(binding) != {
                'handler_point', 'selection_point', 'caller_point', 'input_point',
                'engine_attribute', 'processor_attribute', 'native_prefix', 'entry_path', 'entry_method', 'native_selection_point'}
                or any(type(v) is not str or not v or len(v) > 128 for v in binding.values())
                or not {binding.get(k) for k in ('handler_point', 'selection_point', 'native_selection_point', 'caller_point', 'input_point')} <= points.keys()):
            raise ValueError('LIFECYCLE_CATALOG_INVALID')
    return {row['profile']: row for row in rows}


ROWS = _catalog()


def contains(profile):
    return type(profile) is str and profile in ROWS


def row(profile):
    if not contains(profile):
        raise ValueError('LIFECYCLE_PROFILE_UNAVAILABLE')
    return ROWS[profile]


def runtime_spec(profile):
    selected = row(profile)
    catalog = abi.read_catalog()
    runtime_code = {}
    for runtime, platforms in selected['runtimes'].items():
        digest = catalog['runtimes'][runtime]['base_code_sha256']
        runtime_code[runtime] = {platform: digest for platform in platforms}
    return {key: selected[key] for key in ('profile', 'schema', 'commit', 'sources', 'void_return_argument_ids')} | {
        **({'request_binding': selected['request_binding']} if 'request_binding' in selected else {}),
        **({'startup_image': selected['startup_image']} if 'startup' in selected else {}),
        **({'profiler_image': selected['profiler_image']} if 'baseline_profiler' in selected else {}),
        'base_sha256': catalog['base']['sha256'], 'runtime_code': runtime_code}


def request_binding(profile):
    return contains(profile) and 'request_binding' in row(profile)


def startup_observation(profile):
    return contains(profile) and row(profile).get('startup') == 'baseline-v1'


def profiler_observation(profile):
    return contains(profile) and row(profile).get('baseline_profiler') == 'worker-direct-torch'


def stream_observation(profile):
    return contains(profile) and row(profile).get('baseline_stream') == 'uvicorn-0.34-wheel-source'


def approved_file_read(profile):
    return contains(profile) and row(profile).get('approved_file_read') is True


def approved_file_read_matches(env, prepared):
    profile = prepared.get('profile')
    return (approved_file_read(profile)
            and matches(profile, env.engine.name, env.engine.version, env.engine.image_digest, env.runtime.platform)
            and env.engine.source_commit in {None, row(profile)['commit']})


def startup_assets(profile):
    dependencies = {filename: (logical, digest) for logical, (filename, digest) in startup.DEPENDENCIES.items()} if startup_observation(profile) or profiler_observation(profile) else {}
    return dependencies | (direct_profiler.EXTRA_ASSETS if profiler_observation(profile) else {})


def stream_assets(profile):
    return {'guard_stream_buffer_extension.py': ('vllm_stream_buffer_sensor', baseline_stream.EXTENSION_SHA256)} if stream_observation(profile) else {}


def approved_file_read_assets(profile):
    return {file_read.PRIMITIVE_FILE: ('vllm_file_read_sensor', file_read.PRIMITIVE_SHA256)} if approved_file_read(profile) else {}


def asset_names(profile):
    return {'guard_lifecycle_sensor.py', 'guard_lifecycle_base.py', 'sitecustomize.py', 'settings.json'} | (
        {'request-binding-key.json'} if request_binding(profile) else set()) | set(startup_assets(profile)) | set(stream_assets(profile)) | (
        {'model-read-scope.json', *approved_file_read_assets(profile)} if approved_file_read(profile) else set())


def render_source(profile, *, request_key_sha256=None, model_read_scope_sha256=None):
    # Canonical ordering keeps the same selected row byte-identical when unrelated
    # release rows are added. This literal is data, not executable input.
    spec = json.loads(json.dumps(runtime_spec(profile), sort_keys=True))
    extension = b''
    if request_binding(profile):
        if type(request_key_sha256) is not str or re.fullmatch('[0-9a-f]{64}', request_key_sha256) is None:
            raise ValueError('REQUEST_BINDING_KEY_PIN_REQUIRED')
        spec['request_key_sha256'] = request_key_sha256
        extension = b'\n' + Path(__file__).with_name('vllm_fresh_request_binding.py').read_bytes()
    elif request_key_sha256 is not None:
        raise ValueError('REQUEST_BINDING_PROFILE_REQUIRED')
    if startup_observation(profile):
        extension += b'\n' + Path(startup.__file__).read_bytes()
    if profiler_observation(profile):
        extension += b'\n' + Path(startup.__file__).read_bytes() + b'\n' + Path(direct_profiler.__file__).read_bytes()
    if stream_observation(profile):
        extension += b'\n' + Path(baseline_stream.__file__).read_bytes()
    if approved_file_read(profile):
        if type(model_read_scope_sha256) is not str or re.fullmatch('[0-9a-f]{64}', model_read_scope_sha256) is None:
            raise ValueError('APPROVED_FILE_READ_SCOPE_PIN_REQUIRED')
        spec['model_read_scope_sha256'] = model_read_scope_sha256
        extension += b'\n' + Path(file_read.__file__).read_bytes()
    elif model_read_scope_sha256 is not None:
        raise ValueError('APPROVED_FILE_READ_PROFILE_REQUIRED')
    return Path(template.__file__).read_bytes() + extension + (
        '\nSPEC = ' + repr(spec) + "\nif __name__ == '__main__':\n    main()\n").encode()


def matches(profile, engine, version, image, platform):
    selected = row(profile)
    if type(platform) is not str:
        return False
    modes = [part.strip().split('=', 1)[1] for part in platform.split(';')
             if part.strip().startswith('engine_requested=')]
    return (engine == selected['engine'] and version == selected['version'] and image in selected['images']
            and modes == [selected['engine_mode']])
