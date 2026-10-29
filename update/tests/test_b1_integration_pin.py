"""REQ-10 tool consumes a fixed common snapshot, including shadow-file gates."""
from pathlib import Path
import hashlib
import subprocess

import pytest
import tools.b1_integration_cpu as integration_tool

from tools.b1_integration_cpu import BASE_SHA, FRAMEWORK_SHA, SOURCE_COMMIT, verify_baseline, verify_snapshot, SelectedOverlayProfile, SELECTED_OVERLAY_PATHS


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


OVERLAY_BYTES = {
    'update/src/guard/executors/structured_tools.py': b'APPROVED = "selected model"\n',
    'update/src/guard/executors/structured_tools_cpu.py': b'APPROVED = "CPU producer"\n',
}
OVERLAY_HASHES = {path: hashlib.sha256(raw).hexdigest() for path, raw in OVERLAY_BYTES.items()}
PROVENANCE = 'a' * 40  # Metadata need not name an object in this synthetic repository.
OVERLAY_SCOPE = ('update/src/guard',)


def commit_fixture(repo, message):
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=Pin Test', '-c', 'user.email=pin@example.invalid', 'commit', '-qm', message)


@pytest.fixture
def overlay_repo(pinned_repo):
    repo, _ = pinned_repo
    existing = repo / 'update/src/guard/executors/structured_tools.py'
    existing.parent.mkdir(parents=True)
    existing.write_text('BASELINE = True\n')
    (existing.parent / 'common.py').write_text('COMMON = True\n')
    pack = repo / 'update/packs/PACK-BASELINE/pack.yaml'
    pack.parent.mkdir(parents=True)
    pack.write_text('pack_id: PACK-BASELINE\nrevision: 31\n')
    framework = repo / 'update/reference/framework/UPSTREAM.json'
    framework.parent.mkdir(parents=True)
    framework.write_text('{"commit": "' + FRAMEWORK_SHA + '"}')
    (repo / 'update/uv.lock').write_text('fixture lock\n')
    (repo / 'update/pyproject.toml').write_text('fixture metadata\n')
    commit_fixture(repo, 'common baseline')
    baseline = git(repo, 'rev-parse', 'HEAD').decode().strip()
    for path, raw in OVERLAY_BYTES.items():
        (repo / path).write_bytes(raw)
    commit_fixture(repo, 'selected backend')
    return repo, baseline, SelectedOverlayProfile(OVERLAY_HASHES, PROVENANCE)


def test_fixed_selected_overlay_accepts_exact_committed_bytes_only_when_explicit(overlay_repo):
    repo, baseline, profile = overlay_repo
    verify_snapshot(repo, baseline, OVERLAY_SCOPE, selected_overlay=profile)
    with pytest.raises(ValueError, match='source/index modified'):
        verify_snapshot(repo, baseline, OVERLAY_SCOPE)


@pytest.mark.parametrize('paths', [
    {'update/src/guard/executors/structured_tools.py': '0' * 64},
    {**OVERLAY_HASHES, 'update/src/guard/executors/*.py': '0' * 64},
    {**OVERLAY_HASHES, 'update/src/guard/core/run.py': '0' * 64},
    {path.replace('executors/', 'executors/../executors/'): value for path, value in OVERLAY_HASHES.items()},
])
def test_selected_overlay_rejects_incomplete_wildcard_foreign_and_noncanonical_paths(paths):
    with pytest.raises(ValueError, match='two approved B source paths'):
        SelectedOverlayProfile(paths, PROVENANCE)


@pytest.mark.parametrize('digest', ['HEAD', '', 'a' * 63, 'A' * 64, None])
def test_selected_overlay_requires_literal_sha256(digest):
    invalid = {**OVERLAY_HASHES, next(iter(OVERLAY_HASHES)): digest}
    with pytest.raises(ValueError, match='fixed SHA256'):
        SelectedOverlayProfile(invalid, PROVENANCE)


@pytest.mark.parametrize('ref', ['HEAD', 'main', 'a' * 39, 'A' * 40, None])
def test_selected_overlay_requires_explicit_full_provenance_metadata(ref):
    with pytest.raises(ValueError, match='provenance commit metadata'):
        SelectedOverlayProfile(OVERLAY_HASHES, ref)


def test_selected_overlay_profile_copies_expected_hashes():
    mutable = dict(OVERLAY_HASHES)
    profile = SelectedOverlayProfile(mutable, PROVENANCE)
    mutable[next(iter(mutable))] = '0' * 64
    assert dict(profile.expected_sha256) == OVERLAY_HASHES
    with pytest.raises(TypeError):
        profile.expected_sha256[next(iter(mutable))] = '0' * 64


@pytest.mark.parametrize('mutation', ['different_bytes', 'missing', 'symlink', 'foreign_modified', 'foreign_added'])
def test_selected_overlay_rejects_other_committed_content(overlay_repo, mutation):
    repo, baseline, profile = overlay_repo
    target = repo / 'update/src/guard/executors/structured_tools_cpu.py'
    if mutation == 'different_bytes':
        target.write_bytes(b'unapproved producer\n')
    elif mutation == 'missing':
        target.unlink()
    elif mutation == 'symlink':
        target.unlink()
        target.symlink_to('structured_tools.py')
    elif mutation == 'foreign_modified':
        (target.parent / 'common.py').write_text('COMMON = False\n')
    else:
        (target.parent / 'foreign.py').write_text('SHADOW = True\n')
    commit_fixture(repo, mutation)
    with pytest.raises(ValueError, match='overlay'):
        verify_snapshot(repo, baseline, OVERLAY_SCOPE, selected_overlay=profile)


@pytest.mark.parametrize('mutation', ['dirty', 'staged', 'index_only', 'ignored_shadow', 'assume_unchanged'])
def test_selected_overlay_keeps_dirty_and_shadow_gates(overlay_repo, mutation):
    repo, baseline, profile = overlay_repo
    target = repo / 'update/src/guard/executors/structured_tools.py'
    original = target.read_bytes()
    if mutation == 'ignored_shadow':
        (repo / '.git/info/exclude').write_text('update/src/guard/executors/shadow.py\n')
        (target.parent / 'shadow.py').write_text('SHADOW = True\n')
    else:
        if mutation == 'assume_unchanged':
            git(repo, 'update-index', '--assume-unchanged', str(target.relative_to(repo)))
        target.write_bytes(b'unapproved content\n')
        if mutation in ('staged', 'index_only'):
            git(repo, 'add', str(target.relative_to(repo)))
        if mutation == 'index_only':
            target.write_bytes(original)
    with pytest.raises(ValueError, match='source|overlay'):
        verify_snapshot(repo, baseline, OVERLAY_SCOPE, selected_overlay=profile)


def test_provenance_commit_never_substitutes_for_expected_bytes(overlay_repo):
    repo, baseline, _ = overlay_repo
    profile = SelectedOverlayProfile({path: '0' * 64 for path in SELECTED_OVERLAY_PATHS},
                                     git(repo, 'rev-parse', 'HEAD').decode().strip())
    with pytest.raises(ValueError, match='overlay bytes mismatch'):
        verify_snapshot(repo, baseline, OVERLAY_SCOPE, selected_overlay=profile)


def test_selected_overlay_cannot_be_applied_outside_its_snapshot_scope(overlay_repo):
    repo, baseline, profile = overlay_repo
    with pytest.raises(ValueError, match='outside pinned scope'):
        verify_snapshot(repo, baseline, ('source',), selected_overlay=profile)


def test_mapping_alone_cannot_select_overlay_profile(overlay_repo):
    repo, baseline, _ = overlay_repo
    with pytest.raises(ValueError, match='explicit SelectedOverlayProfile'):
        verify_snapshot(repo, baseline, OVERLAY_SCOPE, selected_overlay=OVERLAY_HASHES)


def test_baseline_default_stays_strict_and_explicit_selected_profile_is_forwarded(overlay_repo, monkeypatch):
    repo, baseline, profile = overlay_repo
    monkeypatch.setattr(integration_tool, 'BASE_SHA', baseline)
    with pytest.raises(ValueError, match='source/index modified'):
        verify_baseline(repo)
    verify_baseline(repo, selected_overlay=profile)


def test_selected_overlay_added_file_must_be_committed(overlay_repo):
    repo, baseline, profile = overlay_repo
    producer = 'update/src/guard/executors/structured_tools_cpu.py'
    git(repo, 'rm', '--cached', producer)
    git(repo, '-c', 'user.name=Pin Test', '-c', 'user.email=pin@example.invalid', 'commit', '-qm', 'untrack producer')
    with pytest.raises(ValueError, match='must be tracked'):
        verify_snapshot(repo, baseline, OVERLAY_SCOPE, selected_overlay=profile)
