"""IPC payload 보존·실제 자식/다른 객체 구분을 검증한다. GPU 증거가 아니다."""
import json
import multiprocessing
from multiprocessing.connection import Connection, Pipe
from multiprocessing.shared_memory import SharedMemory
import os
import tempfile

import pytest

from guard.adapters import vllm_rank_ipc_identity as ipc

KEY = b'k' * 32


def _child(connection, memory_name, output):
    memory = SharedMemory(name=memory_name)
    try:
        output.send((ipc.pipe_endpoint(connection, key=KEY),
                     ipc.shared_memory_endpoint(memory, key=KEY)))
    finally:
        memory.close()
        output.close()
        connection.close()


def test_same_pipe_keeps_payload_and_distinguishes_another_pipe():
    reader, writer = Pipe(duplex=False)
    other_reader, other_writer = Pipe(duplex=False)
    try:
        writer.send_bytes(b'untouched')
        left = ipc.pipe_endpoint(reader, key=KEY)
        right = ipc.pipe_endpoint(writer, key=KEY)
        other = ipc.pipe_endpoint(other_reader, key=KEY)
        assert left['identity_hmac_sha256'] == right['identity_hmac_sha256']
        assert left['identity_hmac_sha256'] != other['identity_hmac_sha256']
        assert reader.recv_bytes() == b'untouched'
        assert ipc.pipe_endpoint(reader, key=b'z' * 32)['identity_hmac_sha256'] != left['identity_hmac_sha256']
        assert left['source_authenticated'] is left['engine_group_verified'] is False
        assert left['observation_started_ns'] <= left['observation_finished_ns']
    finally:
        for connection in (reader, writer, other_reader, other_writer):
            connection.close()


def test_actual_spawned_process_matches_pipe_and_shared_memory_without_payload_access():
    context = multiprocessing.get_context('spawn')
    reader, writer = context.Pipe(duplex=False)
    output_reader, output_writer = context.Pipe(duplex=False)
    memory = SharedMemory(create=True, size=64)
    other = SharedMemory(create=True, size=64)
    child = context.Process(target=_child, args=(reader, memory.name, output_writer))
    try:
        memory.buf[:] = b'x' * 64
        parent_pipe = ipc.pipe_endpoint(writer, key=KEY)
        parent_memory = ipc.shared_memory_endpoint(memory, key=KEY)
        child.start()
        output_writer.close()
        assert output_reader.poll(15)
        child_pipe, child_memory = output_reader.recv()
        child.join(15)
        assert child.exitcode == 0
        assert child_pipe['identity_hmac_sha256'] == parent_pipe['identity_hmac_sha256']
        assert child_memory['identity_hmac_sha256'] == parent_memory['identity_hmac_sha256']
        assert ipc.shared_memory_endpoint(other, key=KEY)['identity_hmac_sha256'] != parent_memory['identity_hmac_sha256']
        assert bytes(memory.buf) == b'x' * 64
        serialized = json.dumps(parent_memory)
        assert memory.name not in serialized and '_fd' not in serialized and 'kernel_object' not in serialized
    finally:
        if child.is_alive():
            child.terminate()
        if child.pid is not None:
            child.join(15)
        for connection in (reader, writer, output_reader, output_writer):
            connection.close()
        for item in (memory, other):
            item.close()
            item.unlink()


def test_properties_and_fileno_are_never_executed():
    class Trap:
        @property
        def __dict__(self):
            raise AssertionError('property를 호출했다')

        def fileno(self):
            raise AssertionError('fileno를 호출했다')

    for capture in (ipc.pipe_endpoint, ipc.shared_memory_endpoint):
        with pytest.raises(ValueError, match='RANK_IPC_IDENTITY_UNAVAILABLE'):
            capture(Trap(), key=KEY)


def test_shared_memory_name_property_is_not_read(monkeypatch):
    memory = SharedMemory(create=True, size=64)
    try:
        def forbidden(self):
            raise AssertionError('name property를 호출했다')
        with monkeypatch.context() as patch:
            patch.setattr(SharedMemory, 'name', property(forbidden))
            assert ipc.shared_memory_endpoint(memory, key=KEY)['kind'] == 'shared-memory'
    finally:
        memory.close()
        memory.unlink()


@pytest.mark.parametrize('key', [b'', b'x' * 31, b'x' * 33, 'k' * 32])
def test_session_key_shape_is_exact(key):
    reader, writer = Pipe(duplex=False)
    try:
        with pytest.raises(ValueError, match='RANK_IPC_IDENTITY_UNAVAILABLE'):
            ipc.pipe_endpoint(reader, key=key)
    finally:
        reader.close()
        writer.close()


def test_closed_and_non_pipe_fds_are_rejected():
    reader, writer = Pipe(duplex=False)
    reader.close()
    try:
        with pytest.raises(ValueError, match='RANK_IPC_IDENTITY_UNAVAILABLE'):
            ipc.pipe_endpoint(reader, key=KEY)
        with tempfile.TemporaryFile() as file:
            connection = Connection(os.dup(file.fileno()), readable=True, writable=False)
            try:
                with pytest.raises(ValueError, match='RANK_IPC_IDENTITY_UNAVAILABLE'):
                    ipc.pipe_endpoint(connection, key=KEY)
            finally:
                connection.close()
    finally:
        writer.close()


def test_fd_replacement_during_capture_is_rejected(monkeypatch):
    reader, writer = Pipe(duplex=False)
    original = os.fstat
    count = 0
    try:
        def changed(fd):
            nonlocal count
            count += 1
            value = original(fd)
            if count == 3:
                fields = list(value)
                fields[1] += 1
                return os.stat_result(fields)
            return value
        monkeypatch.setattr(os, 'fstat', changed)
        with pytest.raises(ValueError, match='RANK_IPC_IDENTITY_UNAVAILABLE'):
            ipc.pipe_endpoint(reader, key=KEY)
    finally:
        reader.close()
        writer.close()
