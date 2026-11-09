"""Per-execution technical binding, independent of enterprise policy values."""
from copy import deepcopy
import re

SCHEMA = 'guard-sensor-run-binding/2'
KEYS = frozenset({'sensor_environment_sha256', 'sensor_target_sha256',
                  'sensor_prepared_sha256', 'sensor_manifest_sha256'})
SUFFIX = '-run-binding-v2'


def values(binding):
    if (type(binding) is not dict or set(binding) != {'schema', *KEYS}
            or binding.get('schema') != SCHEMA
            or any(type(binding[k]) is not str or re.fullmatch('[0-9a-f]{64}', binding[k]) is None for k in KEYS)):
        raise ValueError('SENSOR_RUN_BINDING_INVALID')
    return {k: binding[k] for k in KEYS}


def migrate_pack(source, output):
    """Create a new pack revision; never overwrite a pack or saved policy."""
    from pathlib import Path
    import shutil, yaml
    from guard.models import load_pack
    source, output = Path(source), Path(output)
    original = load_pack(source)
    profiles = [c.collection_profile for c in original.checks.values() if c.executor == 'engine_sensor']
    if any(p is None or p.endswith(SUFFIX) for p in profiles):
        raise ValueError('SENSOR_PACK_MIGRATION_ALREADY_APPLIED_OR_UNSUPPORTED')
    if output.exists() or output.resolve().is_relative_to(source.resolve()):
        raise ValueError('SENSOR_PACK_MIGRATION_NEEDS_NEW_PATH')
    shutil.copytree(source, output)
    manifest_path = output / 'pack.yaml'
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest['revision'] = original.manifest.revision + 1
    for check_id, check in original.checks.items():
        if check.executor != 'engine_sensor':
            continue
        path = output / 'checks' / (check_id + '.yaml')
        doc = yaml.safe_load(path.read_text())
        doc['revision'] += 1
        doc['policy_params'] = [p for p in doc.get('policy_params', []) if p['key'] not in KEYS]
        doc['collection_profile'] = check.collection_profile + SUFFIX
        path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
    from hashlib import sha256
    manifest['files_sha256'] = [{'path': item['path'], 'sha256': sha256((output / item['path']).read_bytes()).hexdigest()}
                                for item in manifest['files_sha256']]
    manifest['review'] = {'status': 'DRAFT', 'reviewer': None, 'reviewed_at': None}
    manifest_path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False))
    return load_pack(output)
