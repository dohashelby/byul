"""New real FIFO contracts; no old engine/transport/suite is replayed."""
import os
from pathlib import Path

import pytest

from guard.executors import committed_cpu_support as subject
from tests.test_b_committed_cpu_support import descriptor, FIXTURE, FIXTURE_SHA


def test_initial_FIFO_is_rejected_before_open(tmp_path, monkeypatch):
    path = tmp_path / 'EXAMPLE-input-fifo'
    os.mkfifo(path, 0o600)
    item = descriptor()
    item['path'] = str(path)
    monkeypatch.setattr(subject.os, 'open', lambda *a, **k: pytest.fail('FIFO must not be opened'))
    with pytest.raises(ValueError, match='B_CPU_SUPPORT_FILE_BOUND_OR_IDENTITY'):
        subject.consume('LIF-06', item)
    assert path.is_fifo()


def test_actual_REG_to_FIFO_replacement_is_nonblocking_rejected_and_FD_closed(tmp_path, monkeypatch):
    path = tmp_path / 'EXAMPLE-replaced-input.json'
    path.write_bytes(FIXTURE.read_bytes())
    item = descriptor()
    item['path'] = str(path)
    original_open = os.open
    opened = []
    def replace_then_open(name, flags, *args, **kwargs):
        assert Path(name) == path and flags & os.O_NONBLOCK
        path.unlink()
        os.mkfifo(path, 0o600)
        fd = original_open(name, flags, *args, **kwargs)
        opened.append(fd)
        return fd
    monkeypatch.setattr(subject.os, 'open', replace_then_open)
    with pytest.raises(ValueError, match='B_CPU_SUPPORT_FILE_BOUND_OR_IDENTITY'):
        subject.consume('SUP-03', item)
    assert len(opened) == 1 and path.is_fifo()
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_nonblocking_regular_FD_keeps_exact_actual_A_record_projection():
    raw, result = subject.consume('LIF-06', descriptor())
    assert raw == FIXTURE.read_bytes() and result['record_sha256'] == FIXTURE_SHA
    assert result['accepted'] and result['projection']['reloads']
    assert not result['current_target_binding_verified']
