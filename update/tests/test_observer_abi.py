"""Reproducible interpreter contracts; no engine or producer registration."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys

import pytest

from guard.adapters import vllm_observer_abi as abi

DIRECTORY = Path(abi.__file__).parent


def test_exact_observer_contracts_are_reproduced_without_engine_imports():
    before = {name for name in sys.modules if name == 'vllm' or name.startswith('vllm.')}
    actual = abi.measure(DIRECTORY)
    catalog = abi.read_catalog()
    assert len(actual['contracts']) == 15
    assert actual['engine_executed'] is actual['producer_registered'] is False
    assert before == {name for name in sys.modules if name == 'vllm' or name.startswith('vllm.')}
    if actual['runtime'] in catalog['runtimes']:
        assert abi.verify(DIRECTORY) == actual
    else:
        with pytest.raises(ValueError, match='OBSERVER_ABI_CONTRACT_MISMATCH'):
            abi.verify(DIRECTORY)


def test_measured_runtime_rows_are_independent_and_os_policy_is_not_approved_by_a_hash():
    catalog = abi.read_catalog()
    assert set(catalog['runtimes']) == {'cpython-3.12.3', 'cpython-3.12.8'}
    first, second = (catalog['runtimes'][key] for key in catalog['runtimes'])
    assert first['base_code_sha256'] != second['base_code_sha256']
    assert first['contracts'].keys() == second['contracts'].keys() == catalog['observers'].keys()
    assert all(first['contracts'][key] != second['contracts'][key] for key in catalog['observers'])
    assert {key for key, row in catalog['observers'].items() if row['legacy_os_policy_separate']} == {
        'file_read', 'safe_open_mapping', 'worker_generation', 'worker_initialization', 'worker_startup'}
    for row in (catalog['base'], *catalog['observers'].values()):
        assert hashlib.sha256((DIRECTORY / row['file']).read_bytes()).hexdigest() == row['sha256']


@pytest.mark.parametrize('kind', ['missing', 'contract', 'base'])
def test_no_unknown_runtime_or_changed_code_contract_can_be_approved(kind):
    catalog = deepcopy(abi.read_catalog())
    actual = abi.measure(DIRECTORY, catalog)
    # A copied row is deliberately EXAMPLE test input, never persisted as a pin.
    row = {'base_code_sha256': actual['base_code_sha256'], 'contracts': actual['contracts'].copy()}
    catalog['runtimes'] = {actual['runtime']: row}
    if kind == 'missing':
        catalog['runtimes'].clear()
    elif kind == 'contract':
        row['contracts']['attempt'] = '0' * 64
    else:
        row['base_code_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='OBSERVER_ABI_CONTRACT_MISMATCH'):
        abi.verify(DIRECTORY, catalog)


def test_compilation_does_not_use_same_cache_tag_bytecode(tmp_path):
    catalog = abi.read_catalog()
    for path in DIRECTORY.glob('*.py'):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    cache = tmp_path / '__pycache__'
    cache.mkdir()
    for row in (catalog['base'], *catalog['observers'].values()):
        (cache / (Path(row['file']).stem + '.cpython-312.pyc')).write_bytes(b'EXAMPLE_WRONG_PATCH_BYTECODE')
    assert abi.measure(tmp_path, catalog) == abi.measure(DIRECTORY, catalog)


@pytest.mark.parametrize('kind', ['changed', 'symlink', 'path'])
def test_pinned_sources_are_exact_regular_files(tmp_path, kind):
    row = dict(abi.read_catalog()['base'])
    source = tmp_path / row['file']
    if kind == 'changed':
        source.write_bytes((DIRECTORY / row['file']).read_bytes() + b'\n')
    elif kind == 'symlink':
        source.symlink_to(DIRECTORY / row['file'])
    else:
        row['file'] = '../' + row['file']
    with pytest.raises((ValueError, OSError)):
        abi._source(tmp_path, row)
