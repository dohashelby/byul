"""Inert mount metadata and actual local file hashing; no target operations."""
import json
import os
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from guard.models import canonical_json, sha256_hex
from guard.runtime import engine_http
from tools import gpu_a_normal_tp2_callbacks as passive


@pytest.fixture
def source_reader():
    asset = Path(engine_http.__file__).parent / 'assets' / 'normal_callback_sources.py.txt'
    return runpy.run_path(str(asset))['snapshot'].__globals__


@pytest.fixture
def mounted(source_reader, tmp_path, monkeypatch):
    _, files, _, _ = passive.prepare(tmp_path / 'generated', 'worker-shards')
    code, data = tmp_path / 'code', tmp_path / 'journal'
    passive.write_assets(code, files)
    data.mkdir(mode=0o700)
    uid, gid = os.geteuid(), os.getegid()
    readonly = {code.stat().st_ino}

    class ExistingFilesystem:
        def __getattr__(self, name):
            return getattr(os, name)

        def fstatvfs(self, fd):
            return SimpleNamespace(f_flag=os.ST_RDONLY if os.fstat(fd).st_ino in readonly else 0)

    monkeypatch.setitem(source_reader, 'CODE', str(code))
    monkeypatch.setitem(source_reader, 'DATA', str(data))
    monkeypatch.setitem(source_reader, 'PRINCIPAL', (uid, gid))
    monkeypatch.setitem(source_reader, '_principal', lambda: (uid, gid))
    monkeypatch.setitem(source_reader, '_mounts', lambda: {str(code): 'a' * 64, str(data): 'b' * 64})
    monkeypatch.setitem(source_reader, 'os', ExistingFilesystem())
    want = {name: {'bytes': len(raw), 'sha256': sha256_hex(raw)} for name, raw in files.items()}
    return code, data, readonly, want


@pytest.mark.parametrize('damage', (None, 'missing-source', 'replaced-settings', 'extra-module',
                                     'source-link', 'writable-code', 'read-only-journal'))
def test_mounted_callback_reader_hashes_only_the_closed_private_tree(source_reader, mounted, damage):
    code, data, readonly, want = mounted
    settings = code / 'settings.json'
    if damage == 'missing-source':
        code.chmod(0o700)
        settings.unlink()
        code.chmod(0o500)
    elif damage == 'replaced-settings':
        source = settings.read_bytes()
        settings.chmod(0o600)
        settings.write_bytes(bytes([source[0] ^ 1]) + source[1:])
        settings.chmod(0o400)
    elif damage == 'extra-module':
        code.chmod(0o700)
        (code / 'extra.py').write_bytes(b'# inert non-shipped source')
        code.chmod(0o500)
    elif damage == 'source-link':
        replacement = data / 'EXAMPLE-same-bytes'
        replacement.write_bytes(settings.read_bytes())
        code.chmod(0o700)
        settings.unlink()
        settings.symlink_to(replacement)
        code.chmod(0o500)
    elif damage == 'writable-code':
        readonly.clear()
    elif damage == 'read-only-journal':
        readonly.add(data.stat().st_ino)
    if damage is not None:
        with pytest.raises((OSError, ValueError)):
            source_reader['snapshot'](want)
        return
    receipt = source_reader['snapshot'](want)
    assert receipt == source_reader['snapshot'](want)
    assert receipt['files_sha256'] == sha256_hex(canonical_json(want))
    assert receipt['file_count'] == len(want)
    assert set(receipt) == {'schema', 'files_sha256', 'filesystem_sha256', 'file_count', 'uid', 'gid'}


@pytest.mark.parametrize('damage', (None, 'same-id', 'writable-code', 'nested-volume', 'absent-journal'))
def test_mountinfo_requires_exact_distinct_ro_code_and_rw_journal(source_reader, monkeypatch, damage):
    code, data = source_reader['CODE'], source_reader['DATA']
    rows = [f'21 2 8:1 /one {code} ro,nosuid - ext4 /dev/example rw',
            f'22 2 8:1 /two {data} rw,nosuid - ext4 /dev/example rw']
    if damage == 'same-id':
        rows[1] = '21' + rows[1][2:]
    elif damage == 'writable-code':
        rows[0] = rows[0].replace('ro,nosuid', 'rw,nosuid')
    elif damage == 'nested-volume':
        rows.append(f'23 21 8:1 /other {code}/child ro - ext4 /dev/example rw')
    elif damage == 'absent-journal':
        rows.pop()
    monkeypatch.setitem(source_reader, '_proc_bytes', lambda _: ('\n'.join(rows) + '\n').encode())
    if damage:
        with pytest.raises(ValueError, match='NORMAL_TP2_CALLBACK_SOURCE_UNVERIFIED'):
            source_reader['_mounts']()
    else:
        assert set(source_reader['_mounts']()) == {code, data}


@pytest.mark.parametrize('uid, pid1_uid', ((2000, 2000), (0, 2000), (2000, 0)))
def test_mounted_callback_reader_checks_both_reader_and_engine_principal(source_reader, monkeypatch, uid, pid1_uid):
    monkeypatch.setitem(source_reader, 'os', SimpleNamespace(geteuid=lambda: uid, getegid=lambda: 0))
    monkeypatch.setitem(source_reader, '_proc_bytes',
                        lambda _: f'Uid:\t{pid1_uid}\t{pid1_uid}\t{pid1_uid}\t{pid1_uid}\nGid:\t0\t0\t0\t0\n'.encode())
    if uid == pid1_uid == 2000:
        assert source_reader['_principal']() == (2000, 0)
    else:
        with pytest.raises(ValueError, match='NORMAL_TP2_CALLBACK_SOURCE_UNVERIFIED'):
            source_reader['_principal']()


@pytest.mark.parametrize('changed', (False, True))
def test_attested_page_wraps_exact_callback_reader_and_withholds_changed_source(
        source_reader, mounted, monkeypatch, capsys, changed):
    _, data, _, want = mounted
    session = '1' * 32
    journal = data / session
    journal.mkdir()
    raw = canonical_json({'EXAMPLE': True}) + b'\n'
    (journal / f'callbacks-{os.getpid()}.jsonl').write_bytes(raw)
    page_source = (Path(engine_http.__file__).parent / 'assets' / 'callback_page.py.txt').read_text()
    snapshot = source_reader['snapshot']
    calls = []

    def read_sources(files):
        result = snapshot(files)
        calls.append(result)
        if changed and len(calls) == 2:
            return {**result, 'filesystem_sha256': 'c' * 64}
        return result

    spec = {'session': session, 'capture_id': '2' * 32, 'normal_callback_assets': want}
    monkeypatch.setitem(source_reader, 'snapshot', read_sources)
    if changed:
        with pytest.raises(SystemExit, match='NORMAL_TP2_CALLBACK_SOURCE_UNVERIFIED'):
            source_reader['attested_page'](page_source, json.dumps(spec))
        assert capsys.readouterr().out == ''
    else:
        source_reader['attested_page'](page_source, json.dumps(spec))
        reply = json.loads(capsys.readouterr().out)
        assert reply['normal_callback_sources'] == calls[0] == calls[1]
        assert reply['capture_id'] == spec['capture_id']
        assert [row['raw_sha256'] for row in reply['rows']] == [sha256_hex(raw)]
    assert len(calls) == 2
