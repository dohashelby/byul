"""REQ-10 tool consumes a fixed common snapshot, including shadow-file gates."""
from pathlib import Path
import subprocess

import pytest

from tools.b1_integration_cpu import BASE_SHA, FRAMEWORK_SHA, SOURCE_COMMIT, verify_baseline, verify_snapshot


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


@pytest.fixture
def pinned_repo(tmp_path):
    git(tmp_path, 'init', '-q')
    (tmp_path / 'source').mkdir()
    (tmp_path / 'source/module.py').write_text('VALUE = 1\n')
    (tmp_path / '.gitignore').write_text('source/ignored.py\n__pycache__/\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, '-c', 'user.name=Pin Test', '-c', 'user.email=pin@example.invalid', 'commit', '-qm', 'baseline')
    return tmp_path, git(tmp_path, 'rev-parse', 'HEAD').decode().strip()


def test_current_common_snapshot_and_historical_reference_pin():
    assert BASE_SHA == 'c9af729cd1893eda14d0bb222a8fab46c2a6d7b8'
    assert FRAMEWORK_SHA == '91e84c778f4fc8ee1108eaf0551bf51f2f6a2061'
    assert SOURCE_COMMIT == '5d35ce3934a7804b298559d2d99eec33792e1eb9'
    verify_baseline(Path(__file__).resolve().parents[2])


@pytest.mark.parametrize('mutation', ['dirty', 'staged', 'index_only', 'missing', 'untracked', 'ignored', 'legacy_pyc', 'symlink'])
def test_snapshot_rejects_source_changes_and_import_shadows(pinned_repo, mutation):
    repo, commit = pinned_repo
    module = repo / 'source/module.py'
    if mutation in ('dirty', 'staged', 'index_only'):
        module.write_text('VALUE = 2\n')
        if mutation != 'dirty':
            git(repo, 'add', 'source/module.py')
        if mutation == 'index_only':
            module.write_text('VALUE = 1\n')
    elif mutation == 'missing':
        module.unlink()
    elif mutation == 'symlink':
        twin = repo / 'same.py'
        twin.write_bytes(module.read_bytes())
        module.unlink()
        module.symlink_to(twin)
    else:
        name = {'untracked': 'shadow.py', 'ignored': 'ignored.py', 'legacy_pyc': 'shadow.pyc'}[mutation]
        (repo / 'source' / name).write_bytes(b'shadow')
    with pytest.raises(ValueError, match='pinned source'):
        verify_snapshot(repo, commit, ('source',))


def test_descendant_with_unrelated_changes_and_runtime_cache_is_accepted(pinned_repo):
    repo, commit = pinned_repo
    (repo / 'tool.txt').write_text('new tool\n')
    git(repo, 'add', 'tool.txt')
    git(repo, '-c', 'user.name=Pin Test', '-c', 'user.email=pin@example.invalid', 'commit', '-qm', 'tool only')
    cache = repo / 'source/__pycache__'
    cache.mkdir()
    (cache / 'module.cpython-312.pyc').write_bytes(b'runtime cache')
    verify_snapshot(repo, commit, ('source',))


def test_runtime_cache_directory_cannot_hide_python_source(pinned_repo):
    repo, commit = pinned_repo
    cache = repo / 'source/__pycache__'
    cache.mkdir()
    (cache / 'shadow.py').write_text('shadow\n')
    with pytest.raises(ValueError, match='untracked/ignored'):
        verify_snapshot(repo, commit, ('source',))


def test_unrelated_history_is_rejected(pinned_repo):
    repo, commit = pinned_repo
    git(repo, 'checkout', '--orphan', 'unrelated')
    git(repo, '-c', 'user.name=Pin Test', '-c', 'user.email=pin@example.invalid', 'commit', '-qm', 'other root')
    with pytest.raises(subprocess.CalledProcessError):
        verify_snapshot(repo, commit, ('source',))


def test_direct_bytes_check_ignores_git_assume_unchanged_hint(pinned_repo):
    repo, commit = pinned_repo
    git(repo, 'update-index', '--assume-unchanged', 'source/module.py')
    (repo / 'source/module.py').write_text('VALUE = 2\n')
    with pytest.raises(ValueError, match='bytes mismatch'):
        verify_snapshot(repo, commit, ('source',))
