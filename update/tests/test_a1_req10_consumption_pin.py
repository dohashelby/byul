"""Literal reviewed consumer pin reuses the full unchanged B verification scope."""
from pathlib import Path
import hashlib
import subprocess
import pytest
from tools import verify_req10_consumption as consumer
from tools import b1_integration_cpu as original
from tests.test_b1_integration_pin import pinned_repo,overlay_repo,git,commit_fixture


def test_actual_fixed_integration_snapshot_passes_and_original_B_default_still_rejects():
    repo=Path(__file__).resolve().parents[2]
    assert consumer.REVIEWED_COMMON_COMMIT=='da6143bc08a01c57ac9750d83d1ffb35cc253576'
    assert consumer.PINNED_PATHS==original.PINNED_PATHS
    assert original.BASE_SHA=='c9af729cd1893eda14d0bb222a8fab46c2a6d7b8'
    with pytest.raises(ValueError,match='outside selected overlay'):
        original.verify_baseline(repo,selected_overlay=original.SELECTED_PROFILE)
    receipt=consumer.verify(repo)
    assert receipt['files_verified']>200 and receipt['paths_excluded']==[] and not receipt['runtime_hash_approval']
    assert not receipt['original_B_default_pin_changed']


def _synthetic(repo,commit,monkeypatch):
    monkeypatch.setattr(consumer,'REVIEWED_COMMON_COMMIT',commit)
    monkeypatch.setattr(consumer,'_helper',lambda _:original)
    monkeypatch.setattr(consumer,'_verify_imports',lambda _:None)


@pytest.mark.parametrize('mutation',['dirty','staged','index-only','ignored-shadow','cache-source','assume-unchanged'])
def test_same_whole_scope_gate_rejects_changed_source_and_import_shadows(overlay_repo,monkeypatch,mutation):
    repo,_,_=overlay_repo;commit=git(repo,'rev-parse','HEAD').decode().strip();_synthetic(repo,commit,monkeypatch)
    target=repo/'update/src/guard/executors/common.py'
    if mutation=='ignored-shadow':
        (repo/'.git/info/exclude').write_text('update/src/guard/shadow.py\n');(repo/'update/src/guard/shadow.py').write_text('SHADOW=True\n')
    elif mutation=='cache-source':
        path=target.parent/'__pycache__';path.mkdir();(path/'shadow.py').write_text('SHADOW=True\n')
    else:
        raw=target.read_bytes()
        if mutation=='assume-unchanged':git(repo,'update-index','--assume-unchanged',str(target.relative_to(repo)))
        target.write_bytes(b'COMMON=False\n')
        if mutation in {'staged','index-only'}:git(repo,'add',str(target.relative_to(repo)))
        if mutation=='index-only':target.write_bytes(raw)
    with pytest.raises(ValueError,match='pinned source'):consumer.verify(repo)


def test_fixed_B_helper_bytes_are_checked_before_import(tmp_path):
    path=tmp_path/'update/tools/b1_integration_cpu.py';path.parent.mkdir(parents=True);path.write_text('unreviewed')
    with pytest.raises(ValueError,match='REQ10_FIXED_B_HELPER_MISMATCH'):consumer._helper(tmp_path)


def test_foreign_product_import_rejected(tmp_path):
    with pytest.raises(ValueError,match='REQ10_COMMON_IMPORT_MISMATCH'):consumer._verify_imports(tmp_path)
