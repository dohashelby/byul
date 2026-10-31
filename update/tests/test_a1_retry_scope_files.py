"""Bounded, fixed-byte local scope metadata. No actual model/credential files."""
from pathlib import Path
from types import SimpleNamespace
import stat
import pytest
from guard.executors import kubernetes_retry_records as executor
from guard.models import canonical_json, sha256_hex
from tests.test_a1_retry_record_contract import example


def fixed(tmp_path):
    scope,values,_,_=example();path=tmp_path/'scope.json';path.write_bytes(canonical_json(scope))
    binding={'policy_sha256':values['retry_policy_sha256'],'source_sha256':values['retry_record_source_sha256'],
        'image_digest':'sha256:'+'a'*64,'model_revision':'EXAMPLE-revision','model_inventory_sha256':'c'*64}
    return scope,path,sha256_hex(path.read_bytes()),binding


def test_fixed_scope_file_hash_and_stable_identity(tmp_path):
    scope,path,digest,binding=fixed(tmp_path)
    assert executor.load_scope(path,digest,**binding)==scope
    with pytest.raises(ValueError,match='RETRY_SCOPE_FILE_UNAVAILABLE'):executor.load_scope(path,'f'*64,**binding)


def test_nonregular_scope_rejected_before_open(tmp_path,monkeypatch):
    _,path,digest,binding=fixed(tmp_path);original=executor.os.lstat
    def selected(value,*args,**kwargs):
        if Path(value)==path:return SimpleNamespace(st_mode=stat.S_IFLNK)
        return original(value,*args,**kwargs)
    monkeypatch.setattr(executor.os,'lstat',selected)
    with pytest.raises(ValueError,match='RETRY_SCOPE_FILE_UNAVAILABLE'):executor.load_scope(path,digest,**binding)


def test_scope_FD_identity_change_rejected(tmp_path,monkeypatch):
    _,path,digest,binding=fixed(tmp_path);original=executor.os.fstat;calls=[]
    def observed(fd):
        value=original(fd);calls.append(fd)
        if len(calls)==2:
            return SimpleNamespace(**{k:getattr(value,k)+(1 if k=='st_mtime_ns' else 0) for k in ('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns')})
        return value
    monkeypatch.setattr(executor.os,'fstat',observed)
    with pytest.raises(ValueError,match='RETRY_SCOPE_FILE_UNAVAILABLE'):executor.load_scope(path,digest,**binding)


def test_scope_duplicate_JSON_key_is_not_normalized_into_approval(tmp_path):
    _,path,_,binding=fixed(tmp_path);raw=b'{"schema":"unsupported",'+path.read_bytes()[1:];path.write_bytes(raw)
    # This EXAMPLE invalid file is deliberately fixed before the read.
    with pytest.raises(ValueError):executor.load_scope(path,sha256_hex(raw),**binding)


def test_scope_file_bound_rejected_before_parse(tmp_path):
    _,path,_,binding=fixed(tmp_path);raw=b' '*(65536+1);path.write_bytes(raw)
    with pytest.raises(ValueError,match='RETRY_SCOPE_FILE_UNAVAILABLE'):executor.load_scope(path,sha256_hex(raw),**binding)


def test_unprovided_limits_are_explicit_null_and_not_invented(tmp_path):
    scope,path,_,binding=fixed(tmp_path)
    scope['limits']={k:None for k in scope['limits']};path.write_bytes(canonical_json(scope))
    loaded=executor.load_scope(path,sha256_hex(path.read_bytes()),**binding)
    assert loaded['limits']=={k:None for k in scope['limits']}
