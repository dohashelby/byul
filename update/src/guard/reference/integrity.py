"""Installed component pins; the expected hashes are release data, never HEAD."""
import hashlib
import json
from pathlib import Path


def verify_packaged(component):
    root = Path(__file__).resolve().parents[1]
    data = json.loads((Path(__file__).with_name('components.json')).read_bytes())
    if data.get('schema') != 'guard-source-cpu-components/2' or component not in data['components']:
        raise ValueError('SOURCE_CPU_COMPONENT_UNAVAILABLE')
    for relative, digest in data['components'][component].items():
        path = root / relative
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('SOURCE_CPU_COMPONENT_BYTES_MISMATCH')
    return data['components'][component]
