import pytest
from serving_runtime.mvp.store import JsonlStore


def test_bounded_tail_drops_partial_boundary_and_limits_rows(tmp_path):
    store=JsonlStore(tmp_path/'events.jsonl')
    for n in range(20):store.append(dict(n=n,padding='x'*100))
    before=store.path.read_bytes()
    rows,corrupt,truncated=store.read_recent(max_bytes=500,max_records=2)
    assert truncated and not corrupt and [r['n'] for r in rows]==[18,19]
    assert store.path.read_bytes()==before
    store.path.write_bytes(before+b'{"n":')
    rows,corrupt,truncated=store.read_recent(max_bytes=500,max_records=2)
    assert truncated and corrupt and rows[-1]['n']==19


def test_bounded_tail_reuses_owner_mode_symlink_checks(tmp_path):
    store=JsonlStore(tmp_path/'events.jsonl');store.append(dict(ok=True))
    store.path.chmod(0o666)
    with pytest.raises(PermissionError):store.read_recent()
    store.path.chmod(0o600);actual=tmp_path/'actual';store.path.rename(actual);store.path.symlink_to(actual)
    with pytest.raises(OSError):store.read_recent()
    for value in (0,4*1024*1024+1,True):
        with pytest.raises(ValueError):store.read_recent(max_bytes=value)


def test_tiny_line_history_still_returns_only_bounded_row_window(tmp_path):
    store=JsonlStore(tmp_path/'events.jsonl');store.check_writable()
    store.path.write_bytes(b'{}\n'*100_000)
    rows,corrupt,truncated=store.read_recent(max_records=3)
    assert len(rows)==3 and not corrupt and truncated


def test_corrupt_tail_does_not_claim_relative_position_is_original_line(tmp_path):
    store=JsonlStore(tmp_path/'events.jsonl');store.check_writable()
    store.path.write_bytes(b'{}\n' + b'bad-json\n' + b'{}\n')
    _,corrupt,truncated=store.read_recent()
    assert not truncated and corrupt[0]['line']==2
    store.path.write_bytes(b'{}\n'*100 + b'bad-json\n'+b'{}\n')
    _,corrupt,truncated=store.read_recent(max_bytes=25)
    assert truncated and corrupt[0]['line'] is None
