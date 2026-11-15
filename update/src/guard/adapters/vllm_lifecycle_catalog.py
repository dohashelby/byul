"""Reviewed native lifecycle rows, independent of release-specific source code."""
from __future__ import annotations

import json
from pathlib import Path
import re

from . import vllm_configured_lifecycle_sensor as template
from . import vllm_observer_abi as abi


def _catalog():
    data = json.loads((Path(__file__).with_name('assets') / 'vllm_native_release_rows.json').read_bytes())
    if data.get('schema') != 'guard-native-release-rows/1':
        raise ValueError('LIFECYCLE_CATALOG_INVALID')
    rows = data['profiles']
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
        if not points or not set(row['void_return_argument_ids']) <= points.keys():
            raise ValueError('LIFECYCLE_CATALOG_INVALID')
        selection = row.get('selection')
        if selection is not None and (type(selection) is not dict or selection.get('point') not in points
                or type(selection.get('request_id_argument')) is not int or not 1 <= selection['request_id_argument'] <= 8
                or selection['point'] in row['void_return_argument_ids']):
            raise ValueError('LIFECYCLE_CATALOG_INVALID')
    return {row['profile']: row for row in rows}


ROWS = _catalog()


def contains(profile):
    return type(profile) is str and profile in ROWS


def row(profile):
    if not contains(profile):
        raise ValueError('LIFECYCLE_PROFILE_UNAVAILABLE')
    return ROWS[profile]


def selection_point(profile):
    """The reviewed live-selection input point of a catalogued release, or None."""
    selection = row(profile).get('selection') if contains(profile) else None
    return selection['point'] if selection else None


SELECTION_POINTS = frozenset(r['selection']['point'] for r in ROWS.values() if r.get('selection'))


def runtime_spec(profile):
    selected = row(profile)
    catalog = abi.read_catalog()
    runtime_code = {}
    for runtime, platforms in selected['runtimes'].items():
        digest = catalog['runtimes'][runtime]['base_code_sha256']
        runtime_code[runtime] = {platform: digest for platform in platforms}
    selection = selected.get('selection')
    return {key: selected[key] for key in ('profile', 'schema', 'commit', 'sources', 'void_return_argument_ids')} | {
        'base_sha256': catalog['base']['sha256'], 'runtime_code': runtime_code} | (
        {'selection': {k: selection[k] for k in ('point', 'request_id_argument')}} if selection else {})


def render_source(profile):
    # Canonical ordering keeps the same selected row byte-identical when unrelated
    # release rows are added. This literal is data, not executable input.
    spec = json.loads(json.dumps(runtime_spec(profile), sort_keys=True))
    return Path(template.__file__).read_bytes() + (
        '\nSPEC = ' + repr(spec) + "\nif __name__ == '__main__':\n    main()\n").encode()


def matches(profile, engine, version, image, platform):
    selected = row(profile)
    if type(platform) is not str:
        return False
    modes = [part.strip().split('=', 1)[1] for part in platform.split(';')
             if part.strip().startswith('engine_requested=')]
    return (engine == selected['engine'] and version == selected['version'] and image in selected['images']
            and modes == [selected['engine_mode']])
