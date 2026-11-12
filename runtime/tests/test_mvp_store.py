"""Tests for durable JSON Lines MVP storage."""

import json
import os
import stat
import pytest
from pathlib import Path
import tempfile
import unittest

from serving_runtime.mvp.store import JsonlStore


class JsonlStoreTests(unittest.TestCase):
    def test_append_and_read_creates_private_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "records.jsonl"
            store = JsonlStore(path)
            expected = [{"number": 1}, {"text": "한글"}]
            for record in expected:
                store.append(record)

            records, corrupt = store.read_all()

            self.assertEqual(records, expected)
            self.assertEqual(corrupt, [])
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(
                [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()],
                expected,
            )

    def test_corrupt_lines_are_reported_and_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            path.write_bytes(
                b'{"ok": 1}\n'
                b'{not-json}\n'
                b'[1, 2]\n'
                b'\xff\n'
                b'{"ok": 2}\n'
            )

            path.chmod(0o600)
            records, corrupt = JsonlStore(path).read_all()

            self.assertEqual(records, [{"ok": 1}, {"ok": 2}])
            self.assertEqual([item["line"] for item in corrupt], [2, 3, 4])
            self.assertTrue(all(item["reason"] for item in corrupt))

    def test_missing_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            records, corrupt = JsonlStore(Path(directory) / "missing.jsonl").read_all()
        self.assertEqual((records, corrupt), ([], []))


if __name__ == "__main__":
    unittest.main()


def test_new_journal_ignores_permissive_umask_and_preserves_existing_directory(tmp_path):
    tmp_path.chmod(0o755)
    path=tmp_path/'new.jsonl';old=os.umask(0)
    try:JsonlStore(path).append({'example':True})
    finally:os.umask(old)
    assert stat.S_IMODE(path.stat().st_mode)==0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode)==0o755
    assert JsonlStore(path).read_all()[0]==[{'example':True}]


@pytest.mark.parametrize('mode',[0o644,0o666,0o620,0o604,0o700])
def test_unsafe_existing_journal_rejected_without_chmod_or_write(tmp_path,mode):
    path=tmp_path/'old.jsonl';path.write_bytes(b'{"example":1}\n');path.chmod(mode)
    store=JsonlStore(path)
    with pytest.raises(PermissionError, match=rf'^old\.jsonl: .*permissions.*{mode:04o}') as error:
        store.check_writable()
    assert str(tmp_path) not in str(error.value)
    with pytest.raises(PermissionError):store.append({'example':2})
    with pytest.raises(PermissionError):store.read_all()
    assert stat.S_IMODE(path.stat().st_mode)==mode
    assert path.read_bytes()==b'{"example":1}\n'


def test_explicit_reader_gid_required_and_retained_on_rewrite(tmp_path):
    path=tmp_path/'shared.jsonl';path.write_bytes(b'{"example":1}\n{"example":2}\n');path.chmod(0o640)
    with pytest.raises(PermissionError):JsonlStore(path).read_all()
    store=JsonlStore(path,allowed_reader_gids=[path.stat().st_gid])
    store.append({'example':3});assert len(store.read_all()[0])==3
    assert store.remove_lines({1})==(2,1)
    assert stat.S_IMODE(path.stat().st_mode)==0o640


@pytest.mark.parametrize('kind',['symlink','fifo','directory'])
def test_nonregular_journals_never_open_as_records(tmp_path,kind):
    path=tmp_path/'records.jsonl';other=tmp_path/'other';other.write_bytes(b'UNCHANGED');other.chmod(0o600)
    if kind=='symlink':path.symlink_to(other)
    elif kind=='fifo':os.mkfifo(path,0o600)
    else:path.mkdir(mode=0o700)
    store=JsonlStore(path)
    with pytest.raises(OSError):store.check_writable()
    with pytest.raises(OSError):store.read_all()
    assert other.read_bytes()==b'UNCHANGED'


@pytest.mark.parametrize('kind',['symlink','group_write','other_write'])
def test_unsafe_record_directory_is_rejected_without_changing_it(tmp_path,kind):
    parent=tmp_path/'data'
    if kind=='symlink':
        actual=tmp_path/'actual';actual.mkdir(mode=0o700);parent.symlink_to(actual,target_is_directory=True)
    else:parent.mkdir(mode=0o770 if kind=='group_write' else 0o702);parent.chmod(0o770 if kind=='group_write' else 0o702)
    mode=parent.stat().st_mode
    with pytest.raises(OSError):JsonlStore(parent/'records.jsonl').check_writable()
    assert parent.stat().st_mode==mode and not (parent/'records.jsonl').exists()


def test_request_admission_preflight_uses_same_private_store_boundary(tmp_path):
    from serving_runtime.mvp.request_records import RequestRecords
    path=tmp_path/'gateway_records.jsonl';path.write_bytes(b'EXAMPLE');path.chmod(0o644)
    records=RequestRecords(tmp_path,'session',lambda:None,'mock')
    assert records.status()['state']=='error'
    assert path.read_bytes()==b'EXAMPLE' and stat.S_IMODE(path.stat().st_mode)==0o644
    path.chmod(0o600);records.check_store()
    assert records.status()['state']=='ok'
