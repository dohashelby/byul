"""Real filesystem producer contracts; no engine, backend or GPU claim."""
import os
from pathlib import Path
import pytest

from guard.runtime.profiler_owned_trace import apply_fixture, marker


def root(tmp_path):
    path=tmp_path/'owned';path.mkdir(mode=0o700);return path


def test_actual_marker_recovery_retains_original_and_produces_owned_directory(tmp_path):
    folder=root(tmp_path);session='a'*32
    armed=apply_fixture(folder,session,'arm')
    assert (folder/session).read_bytes()==marker(session)
    recovered=apply_fixture(folder,session,'recover',armed)
    assert recovered['recovery_verified'] and recovered['original_marker_retained']
    assert (folder/session).is_dir() and not list((folder/session).iterdir())
    assert (folder/(session+'.fault-record')).read_bytes()==marker(session)
    with pytest.raises((ValueError,OSError)):apply_fixture(folder,session,'recover',armed)


@pytest.mark.parametrize('replacement',['foreign','symlink','directory','hardlink','same-bytes-new-inode'])
def test_replacement_is_rejected_without_removing_foreign_entry(tmp_path,replacement):
    folder=root(tmp_path);session='b'*32;path=folder/session
    receipt=apply_fixture(folder,session,'arm')
    path.rename(folder/'original-marker')
    if replacement=='directory':path.mkdir()
    elif replacement=='symlink':path.symlink_to(folder/'original-marker')
    elif replacement=='hardlink':os.link(folder/'original-marker',path)
    else:path.write_bytes(b'foreign' if replacement=='foreign' else marker(session))
    identity=path.lstat()
    with pytest.raises((ValueError,OSError)):apply_fixture(folder,session,'recover',receipt)
    assert path.lstat().st_ino==identity.st_ino and (folder/'original-marker').read_bytes()==marker(session)


@pytest.mark.parametrize('session',['../escape','a'*31,'g'*32])
def test_no_arbitrary_session_path(tmp_path,session):
    folder=root(tmp_path)
    with pytest.raises(ValueError):apply_fixture(folder,session,'arm')
    assert not list(folder.iterdir())


def test_existing_entry_and_foreign_receipt_preserved(tmp_path):
    folder=root(tmp_path);session='c'*32
    receipt=apply_fixture(folder,session,'arm')
    with pytest.raises(FileExistsError):apply_fixture(folder,session,'arm')
    with pytest.raises(ValueError):apply_fixture(folder,session,'recover',{**receipt,'marker_sha256':'0'*64})
    assert (folder/session).read_bytes()==marker(session)


def test_failure_after_quarantine_preserves_original_marker(tmp_path,monkeypatch):
    folder=root(tmp_path);session='d'*32
    receipt=apply_fixture(folder,session,'arm')
    original=os.mkdir
    def mkdir(path,*args,**kwargs):
        if path==session:raise OSError('PRIVATE_CLEANUP_FAILURE')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(os,'mkdir',mkdir)
    with pytest.raises(OSError):apply_fixture(folder,session,'recover',receipt)
    assert (folder/(session+'.fault-record')).read_bytes()==marker(session)
    assert not (folder/session).exists()


def test_existing_quarantine_never_overwritten(tmp_path):
    folder=root(tmp_path);session='e'*32
    receipt=apply_fixture(folder,session,'arm');saved=folder/(session+'.fault-record')
    saved.write_bytes(b'foreign quarantine')
    with pytest.raises(OSError):apply_fixture(folder,session,'recover',receipt)
    assert saved.read_bytes()==b'foreign quarantine' and (folder/session).read_bytes()==marker(session)


def test_race_replacement_is_retained_after_atomic_move(tmp_path,monkeypatch):
    from guard.runtime import profiler_owned_trace as module
    folder=root(tmp_path);session='f'*32;path=folder/session
    receipt=apply_fixture(folder,session,'arm');original=module.ctypes.CDLL
    def replace(*args,**kwargs):
        path.rename(folder/'original-marker');path.write_bytes(b'foreign replacement')
        return original(*args,**kwargs)
    monkeypatch.setattr(module.ctypes,'CDLL',replace)
    with pytest.raises(ValueError):apply_fixture(folder,session,'recover',receipt)
    assert (folder/'original-marker').read_bytes()==marker(session)
    assert path.read_bytes()==(folder/(session+'.fault-record')).read_bytes()==b'foreign replacement'
