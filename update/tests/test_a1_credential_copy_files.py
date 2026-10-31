"""Only the configured nonsecret inventory input is read; no credential files."""
from pathlib import Path
from types import SimpleNamespace as NS
import os,stat
import pytest
from guard.executors.kubernetes_credential_copy import load_inventory
from guard.models import canonical_json,sha256_hex
from tests.test_a1_credential_copy_contract import example


def test_fixed_nonsecret_inventory_bytes_loaded_and_wrong_pin_rejected(tmp_path):
    value,values,_,_=example();raw=canonical_json(value);path=tmp_path/'inventory.json';path.write_bytes(raw)
    assert load_inventory(path,sha256_hex(raw),values['copy_policy_sha256'])==value
    with pytest.raises(ValueError,match='COPY_INVENTORY_FILE_UNAVAILABLE'):load_inventory(path,'0'*64,values['copy_policy_sha256'])


def test_symbolic_or_nonregular_inventory_rejected_before_open(tmp_path,monkeypatch):
    from guard.executors import kubernetes_credential_copy as module
    monkeypatch.setattr(module.os,'lstat',lambda path:NS(st_mode=stat.S_IFLNK))
    monkeypatch.setattr(module.os,'open',lambda *a,**kw:pytest.fail('nonregular input opened'))
    with pytest.raises(ValueError,match='COPY_INVENTORY_FILE_UNAVAILABLE'):load_inventory(tmp_path/'inventory.json','0'*64,'c'*64)


def test_changed_fd_identity_inventory_rejected(tmp_path,monkeypatch):
    from guard.executors import kubernetes_credential_copy as module
    value,values,_,_=example();raw=canonical_json(value);path=tmp_path/'inventory.json';path.write_bytes(raw)
    original=os.fstat;calls=[]
    def changed(fd):
        info=original(fd);calls.append(fd)
        if len(calls)==1:return info
        return NS(**{name:getattr(info,name)+(1 if name=='st_mtime_ns' else 0) for name in ('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns')})
    monkeypatch.setattr(module.os,'fstat',changed)
    with pytest.raises(ValueError,match='COPY_INVENTORY_FILE_UNAVAILABLE'):load_inventory(path,sha256_hex(raw),values['copy_policy_sha256'])
