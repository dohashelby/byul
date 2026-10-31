"""REQ-13 consumption gates use synthetic Git history, never checkout history."""
import hashlib
import subprocess

import pytest

from tools import b2_integration_cpu as tool


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def commit(root, message):
    git(root, 'add', '.')
    git(root, '-c', 'user.name=B2 Pin Test', '-c', 'user.email=pin@example.invalid', 'commit', '-qm', message)
    return git(root, 'rev-parse', 'HEAD').decode().strip()


@pytest.fixture
def consumption(tmp_path, monkeypatch):
    git(tmp_path, 'init', '-q')
    pins = {}
    for relative in tool.B2_FILE_SHA256:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'REVIEWED = True\n')
        pins[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(tool, 'BASE_SHA', commit(tmp_path, 'approved common and B2 baseline'))
    monkeypatch.setattr(tool, 'B2_FILE_SHA256', pins)
    checked = []
    monkeypatch.setattr(tool, 'verify_tool_checkout', lambda root: checked.append(root))
    return tmp_path, checked


def test_constants_preserve_common_base_and_vllm031_identity():
    assert tool.BASE_SHA == 'fc3c4c174ef9dd4b53bc966b75ec4e131bdf1f2a'
    assert tool.COMMON_BASE_SHA == 'c9af729cd1893eda14d0bb222a8fab46c2a6d7b8'
    assert tool.REFERENCE_SHA == 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
    assert set(tool.B2_FILE_SHA256) == {
        'update/tools/b1_integration_cpu.py',
        'update/src/guard/executors/response_error_contract.py',
        'update/tools/b2_reference_cpu.py', 'update/tests/builders.py',
        'update/tests/test_response_content.py', 'update/docs/b2-response/reference-source.json',
    }
    assert all(len(value) == 64 for value in tool.B2_FILE_SHA256.values())


def test_consumption_reuses_common_gate_and_accepts_unrelated_descendant(consumption):
    root, checked = consumption
    (root / 'tool-change.txt').write_text('unrelated tool/doc work\n')
    commit(root, 'unrelated descendant')
    tool.verify_consumption_checkout(root)
    assert checked == [root]


@pytest.mark.parametrize('mutation', ['dirty', 'staged', 'index_only', 'assume_unchanged', 'missing', 'symlink'])
def test_consumption_rejects_pinned_helper_changes(consumption, mutation):
    root, _ = consumption
    relative = 'update/tests/test_response_content.py'
    target = root / relative
    if mutation == 'missing':
        target.unlink()
    elif mutation == 'symlink':
        twin = root / 'same-content.py'
        twin.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(twin)
    else:
        if mutation == 'assume_unchanged':
            git(root, 'update-index', '--assume-unchanged', relative)
        target.write_bytes(b'REVIEWED = False\n')
        if mutation in ('staged', 'index_only'):
            git(root, 'add', relative)
        if mutation == 'index_only':
            target.write_bytes(b'REVIEWED = True\n')
    with pytest.raises(ValueError, match='source'):
        tool.verify_consumption_checkout(root)


def test_explicit_b2_hashes_are_independent_of_git_snapshot(consumption, monkeypatch):
    root, _ = consumption
    monkeypatch.setattr(tool, 'B2_FILE_SHA256', {path: '0' * 64 for path in tool.B2_FILE_SHA256})
    with pytest.raises(ValueError, match='B2 source/helper bytes mismatch'):
        tool.verify_consumption_checkout(root)


def test_consumption_does_not_bypass_common_gate_failure(consumption, monkeypatch):
    root, _ = consumption
    def common_rejected(repo):
        raise ValueError('common source rejected')
    monkeypatch.setattr(tool, 'verify_tool_checkout', common_rejected)
    with pytest.raises(ValueError, match='common source rejected'):
        tool.verify_consumption_checkout(root)


@pytest.fixture
def reference(tmp_path, monkeypatch):
    git(tmp_path, 'init', '-q')
    (tmp_path / 'vllm').mkdir()
    (tmp_path / 'vllm/handler.py').write_text('REVIEWED = True\n')
    (tmp_path / '.gitignore').write_text('__pycache__/\nvllm/ignored.py\n*.pyc\n')
    monkeypatch.setattr(tool, 'REFERENCE_SHA', commit(tmp_path, 'vllm reference'))
    return tmp_path


def test_reference_allows_ordinary_runtime_cache(reference):
    cache = reference / 'vllm/__pycache__'
    cache.mkdir()
    (cache / 'handler.cpython-312.pyc').write_bytes(b'runtime cache')
    tool.verify_reference_source(reference)


@pytest.mark.parametrize('mutation', ['assume_unchanged', 'ignored_source', 'untracked_source', 'legacy_pyc', 'symlink'])
def test_reference_rejects_hidden_or_shadow_source(reference, mutation):
    target = reference / 'vllm/handler.py'
    if mutation == 'assume_unchanged':
        git(reference, 'update-index', '--assume-unchanged', 'vllm/handler.py')
        target.write_text('REVIEWED = False\n')
    elif mutation == 'symlink':
        twin = reference / 'same.py'
        twin.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(twin)
    else:
        name = {'ignored_source': 'ignored.py', 'untracked_source': 'shadow.py', 'legacy_pyc': 'shadow.pyc'}[mutation]
        (reference / 'vllm' / name).write_bytes(b'shadow')
    with pytest.raises(ValueError, match='pinned source'):
        tool.verify_reference_source(reference)


def test_reference_requires_exact_head_not_only_unchanged_package(reference):
    (reference / 'unrelated.txt').write_text('new HEAD\n')
    commit(reference, 'different source commit')
    with pytest.raises(ValueError, match='B2 reference HEAD mismatch'):
        tool.verify_reference_source(reference)


def compile_function(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('def fixture(): return 1\n')
    namespace = {}
    exec(compile(path.read_text(), str(path), 'exec'), namespace)
    return namespace['fixture']


def test_helper_import_identity_accepts_checkout_alias(tmp_path):
    relative = 'update/tests/test_response_content.py'
    function = compile_function(tmp_path / 'actual' / relative)
    alias = tmp_path / 'alias'
    alias.symlink_to(tmp_path / 'actual', target_is_directory=True)
    tool.verify_helper_imports(alias, [(function, relative)])


def test_helper_import_rejects_identical_bytes_from_a_different_checkout(tmp_path):
    relative = 'update/tests/test_response_content.py'
    function = compile_function(tmp_path / 'foreign' / relative)
    compile_function(tmp_path / 'own' / relative)
    with pytest.raises(ValueError, match='outside the pinned checkout'):
        tool.verify_helper_imports(tmp_path / 'own', [(function, relative)])


def test_checkout_rejection_happens_before_output_creation(tmp_path, monkeypatch):
    def rejected(repo):
        raise ValueError('unapproved checkout')
    monkeypatch.setattr(tool, 'verify_consumption_checkout', rejected)
    output = tmp_path / 'must-not-exist'
    with pytest.raises(ValueError, match='unapproved checkout'):
        tool.run(tmp_path, output)
    assert not output.exists()
