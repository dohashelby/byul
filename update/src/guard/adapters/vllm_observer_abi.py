"""Reproduce exact observer code contracts without imports or cached bytecode.

The catalog binds immutable source bytes. Measuring a contract neither changes
the old observer's runtime policy nor registers a GPU producer.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import types

CATALOG = Path(__file__).with_name('assets') / 'vllm_observer_abi.json'
SCHEMA = 'guard-observer-abi/1'


def read_catalog(path=CATALOG):
    value = json.loads(Path(path).read_bytes())
    if value.get('schema') != SCHEMA or not isinstance(value.get('observers'), dict):
        raise ValueError('OBSERVER_ABI_CATALOG_INVALID')
    return value


def _source(directory, entry):
    name = entry['file']
    if not re.fullmatch(r'[a-z][a-z0-9_]*\.py', name):
        raise ValueError('OBSERVER_ABI_SOURCE_INVALID')
    path = Path(directory) / name
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 262144:
            raise ValueError('OBSERVER_ABI_SOURCE_INVALID')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(262145)
        after = os.fstat(descriptor)
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if (any(getattr(before, k) != getattr(after, k) for k in fields)
                or len(raw) != before.st_size or hashlib.sha256(raw).hexdigest() != entry['sha256']):
            raise ValueError('OBSERVER_ABI_SOURCE_MISMATCH')
        return raw
    finally:
        os.close(descriptor)


def normalized(code, filename):
    return code.replace(co_filename=filename, co_consts=tuple(
        normalized(value, filename) if type(value) is types.CodeType else value for value in code.co_consts))


def _module(directory, entry):
    raw = _source(directory, entry)
    name = '_observer_abi_' + entry['file'][:-3]
    module = types.ModuleType(name)
    module.__file__ = 'guard/adapters/' + entry['file']
    # Direct source compilation is deliberate: a cpython-312 pyc can have been
    # produced by a different patch release with the same cache tag.
    code = compile(raw, module.__file__, 'exec', dont_inherit=True, optimize=0)
    exec(code, module.__dict__)
    return module, code


def _nested(code, qualname):
    if code.co_qualname == qualname:
        return code
    for value in code.co_consts:
        if type(value) is types.CodeType:
            result = _nested(value, qualname)
            if result is not None:
                return result
    return None


def measure(directory, catalog=None):
    if sys.implementation.name != 'cpython' or sys.version_info[:2] != (3, 12) or sys.flags.optimize != 0:
        raise ValueError('OBSERVER_ABI_INTERPRETER_UNSUPPORTED')
    catalog = read_catalog() if catalog is None else catalog
    base, root_code = _module(directory, catalog['base'])
    contracts = {}
    for name, entry in sorted(catalog['observers'].items()):
        observer, code = _module(directory, entry)
        if 'nested_code' in entry:
            selected = _nested(code, entry['nested_code'])
            if selected is None:
                raise ValueError('OBSERVER_ABI_POINT_UNAVAILABLE')
            digest = base.code_digest(observer.relative_code(selected))
        elif 'dependency' in entry:
            dependency, _ = _module(directory, entry['dependency'])
            digest = observer.code_contract(base, dependency)
        else:
            digest = observer.code_contract(base)
        contracts[name] = digest
    return {'runtime': 'cpython-' + '.'.join(map(str, sys.version_info[:3])),
            'base_code_sha256': base.code_digest(normalized(root_code, 'guard/adapters/' + catalog['base']['file'])),
            'contracts': contracts, 'engine_executed': False, 'producer_registered': False}


def verify(directory, catalog=None):
    catalog = read_catalog() if catalog is None else catalog
    actual = measure(directory, catalog)
    expected = catalog.get('runtimes', {}).get(actual['runtime'])
    if (expected is None or actual['base_code_sha256'] != expected['base_code_sha256']
            or actual['contracts'] != expected['contracts']):
        raise ValueError('OBSERVER_ABI_CONTRACT_MISMATCH')
    return actual
