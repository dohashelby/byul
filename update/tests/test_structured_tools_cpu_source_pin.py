"""Synthetic Git contracts for the live reference producer's source pin."""
import subprocess

import pytest

from guard.executors import structured_tools_cpu as producer


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def commit(repo, message):
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=Source Pin Test', '-c', 'user.email=pin@example.invalid', 'commit', '-qm', message)
    return git(repo, 'rev-parse', 'HEAD').decode().strip()


@pytest.fixture
def reference(tmp_path, monkeypatch):
    git(tmp_path, 'init', '-q')
    package = tmp_path / 'vllm/package'
    package.mkdir(parents=True)
    (package / 'protocol.py').write_text('VALUE = 1\n')
    (package / 'data.json').write_text('{"value": 1}\n')
    (tmp_path / '.gitignore').write_text('__pycache__/\n*.pyc\nvllm/ignored.py\nvllm/ignored.so\n')
    monkeypatch.setattr(producer, 'SOURCE_COMMIT', commit(tmp_path, 'reference source'))
    return tmp_path


def test_clean_package_and_ordinary_runtime_caches_are_accepted(reference):
    producer._verify_source(reference)
    cache = reference / 'vllm/package/__pycache__'
    cache.mkdir()
    (cache / 'protocol.cpython-312.pyc').write_bytes(b'runtime cache')
    producer._verify_source(reference)


@pytest.mark.parametrize('mutation', [
    'dirty', 'staged', 'index_only', 'assume_unchanged', 'skip_worktree',
    'missing', 'tracked_symlink', 'directory_symlink', 'untracked_source',
    'ignored_source', 'legacy_pyc', 'cache_source', 'cache_pyc_symlink',
    'untracked_extension', 'ignored_extension', 'tracked_data',
])
def test_source_pin_rejects_modified_bytes_index_and_import_shadows(reference, mutation):
    target = reference / 'vllm/package/protocol.py'
    if mutation in ('dirty', 'staged', 'index_only', 'assume_unchanged', 'skip_worktree'):
        if mutation == 'assume_unchanged':
            git(reference, 'update-index', '--assume-unchanged', 'vllm/package/protocol.py')
        if mutation == 'skip_worktree':
            git(reference, 'update-index', '--skip-worktree', 'vllm/package/protocol.py')
        target.write_text('VALUE = 2\n')
        if mutation in ('staged', 'index_only'):
            git(reference, 'add', 'vllm/package/protocol.py')
        if mutation == 'index_only':
            target.write_text('VALUE = 1\n')
    elif mutation == 'missing':
        target.unlink()
    elif mutation == 'tracked_symlink':
        twin = reference / 'same.py'
        twin.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(twin)
    elif mutation == 'directory_symlink':
        package = target.parent
        twin = reference / 'same-package'
        package.rename(twin)
        package.symlink_to(twin, target_is_directory=True)
    elif mutation in ('untracked_source', 'ignored_source', 'legacy_pyc', 'untracked_extension', 'ignored_extension'):
        name = {'untracked_source': 'shadow.py', 'ignored_source': 'ignored.py', 'legacy_pyc': 'shadow.pyc',
                'untracked_extension': 'shadow.so', 'ignored_extension': 'ignored.so'}[mutation]
        (reference / 'vllm' / name).write_bytes(b'shadow')
    elif mutation in ('cache_source', 'cache_pyc_symlink'):
        cache = target.parent / '__pycache__'
        cache.mkdir()
        if mutation == 'cache_source':
            (cache / 'shadow.py').write_text('SHADOW = True\n')
        else:
            (cache / 'shadow.cpython-312.pyc').symlink_to(target)
    else:
        (target.parent / 'data.json').write_text('{"value": 2}\n')
    with pytest.raises(ValueError, match='^STRUCTURED_TOOLS_REFERENCE_MISMATCH$'):
        producer._verify_source(reference)


def test_exact_reference_head_is_required_even_when_package_is_unchanged(reference):
    (reference / 'unrelated.txt').write_text('new commit\n')
    commit(reference, 'other head')
    with pytest.raises(ValueError, match='^STRUCTURED_TOOLS_REFERENCE_MISMATCH$'):
        producer._verify_source(reference)


def test_export_ignore_cannot_omit_a_tracked_file_from_bytes_verification(reference, monkeypatch):
    (reference / '.gitattributes').write_text('vllm/package/data.json export-ignore\n')
    monkeypatch.setattr(producer, 'SOURCE_COMMIT', commit(reference, 'export-ignore metadata'))
    with pytest.raises(ValueError, match='^STRUCTURED_TOOLS_REFERENCE_MISMATCH$'):
        producer._verify_source(reference)


def test_same_source_recheck_detects_mutation_after_first_check(reference):
    producer._verify_source(reference)
    git(reference, 'update-index', '--assume-unchanged', 'vllm/package/protocol.py')
    (reference / 'vllm/package/protocol.py').write_text('VALUE = 2\n')
    with pytest.raises(ValueError, match='^STRUCTURED_TOOLS_REFERENCE_MISMATCH$'):
        producer._verify_source(reference)
