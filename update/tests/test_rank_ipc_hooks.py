"""원 호출 보존·관측 실패 격리·descriptor 미실행을 검증한다. GPU 실측이 아니다."""
from hashlib import sha256
import json
from multiprocessing.connection import Pipe
import sys
import types

import pytest

from guard.adapters import vllm_rank_ipc_hooks as hooks
from guard.adapters import vllm_rank_ipc_identity as identity
from guard.adapters import vllm_rank_ipc_sources as sources

KEY = b'k' * 32
RAW = b'''calls = []
class Worker:
    def bind(self, value):
        calls.append(value)
        return value + 1
'''


def _fixture(tmp_path, monkeypatch, phase='after-original'):
    path = tmp_path / 'fixture.py'
    path.write_bytes(RAW)
    module = types.ModuleType('fixture')
    module.__file__ = str(path)
    exec(compile(RAW, str(path), 'exec'), vars(module))
    codes = sources._codes(compile(RAW, 'fixture.py', 'exec', dont_inherit=True, optimize=0))
    source = {'path': 'fixture.py', 'bytes': len(RAW), 'sha256': sha256(RAW).hexdigest(),
              'functions': ['Worker.bind'], 'body_code_sha256': {'Worker.bind': sources.code_digest(codes['Worker.bind'])},
              'hooks': {'Worker.bind': {'phase': phase, 'role': 'EXAMPLE',
                  'scalars': {'rank': {'root': 'self', 'path': ['rank']}},
                  'endpoints': {'task': {'root': 'self', 'path': ['connection'], 'kind': 'pipe'}}}}}
    profile = {'schema': 'guard-rank-ipc-source-candidates/1', 'profiles': [{'id': 'EXAMPLE',
        'image': 'fixture-image', 'runtime': sys.implementation.name + '-' + '.'.join(map(str, sys.version_info[:3])),
        'sources': {'fixture': source}}]}
    profiles = tmp_path / 'profiles.json'
    profiles.write_text(json.dumps(profile))
    monkeypatch.setattr(sources, 'PROFILES', profiles)
    return module, path


@pytest.mark.parametrize('phase', ['before-original', 'after-original'])
def test_original_call_payload_and_return_are_preserved(tmp_path, monkeypatch, phase):
    module, _ = _fixture(tmp_path, monkeypatch, phase)
    records, ordering = [], []
    reader, writer = Pipe(duplex=False)
    try:
        def emit(value):
            records.append(value)
            ordering.append(list(module.calls))
        observer = hooks.IPCObserver(image='fixture-image', key=KEY, emit=emit)
        original = module.Worker.bind
        assert observer.attach(module)['hooks_installed'] == 1
        worker = module.Worker()
        worker.rank, worker.connection = 1, reader
        writer.send_bytes(b'untouched')
        assert worker.bind(4) == 5 and module.calls == [4]
        assert module.Worker.bind.__wrapped__ is original
        assert len(records) == 1 and records[0]['gaps'] == []
        assert records[0]['scalars'] == {'rank': 1}
        assert records[0]['endpoints']['task']['identity_hmac_sha256'] == identity.pipe_endpoint(writer, key=KEY)['identity_hmac_sha256']
        assert reader.recv_bytes() == b'untouched'
        assert ordering == ([[]] if phase == 'before-original' else [[4]])
        assert records[0]['source_authenticated'] is records[0]['engine_group_verified'] is records[0]['producer_registered'] is False
        assert json.dumps(records).find(KEY.hex()) == -1
    finally:
        reader.close()
        writer.close()


def test_original_exception_is_not_replaced_or_retried(tmp_path, monkeypatch):
    module, _ = _fixture(tmp_path, monkeypatch)
    records = []
    observer = hooks.IPCObserver(image='fixture-image', key=KEY, emit=records.append)
    observer.attach(module)
    with pytest.raises(TypeError):
        module.Worker().bind(None)
    assert module.calls == [None] and records == []


@pytest.mark.parametrize('phase', ['before-original', 'after-original'])
def test_observer_failure_does_not_block_or_duplicate_original(tmp_path, monkeypatch, phase):
    module, _ = _fixture(tmp_path, monkeypatch, phase)
    def failed(value):
        raise RuntimeError('관측 sink 실패')
    observer = hooks.IPCObserver(image='fixture-image', key=KEY, emit=failed)
    observer.attach(module)
    assert module.Worker().bind(4) == 5 and module.calls == [4]


def test_descriptor_is_not_executed_or_replaced_by_stale_dict_value(tmp_path, monkeypatch):
    module, _ = _fixture(tmp_path, monkeypatch)
    records = []
    reader, writer = Pipe(duplex=False)
    try:
        worker = module.Worker()
        worker.rank, worker.connection = 1, reader
        def forbidden(self):
            raise AssertionError('engine property를 호출했다')
        module.Worker.connection = property(forbidden)
        observer = hooks.IPCObserver(image='fixture-image', key=KEY, emit=records.append)
        observer.attach(module)
        assert worker.bind(4) == 5 and module.calls == [4]
        assert records[0]['endpoints'] == {}
        assert 'RANK_IPC_ENDPOINT_UNAVAILABLE:task' in records[0]['gaps']
    finally:
        reader.close()
        writer.close()


def test_source_drift_keeps_original_and_emits_only_gap(tmp_path, monkeypatch):
    module, path = _fixture(tmp_path, monkeypatch)
    records = []
    observer = hooks.IPCObserver(image='fixture-image', key=KEY, emit=records.append)
    observer.attach(module)
    path.write_bytes(RAW + b'\n')
    assert module.Worker().bind(4) == 5 and module.calls == [4]
    assert records[0]['gaps'] == ['RANK_IPC_CAPTURE_UNAVAILABLE']
    assert 'endpoints' not in records[0]


def test_failed_qualification_installs_no_wrapper(tmp_path, monkeypatch):
    module, path = _fixture(tmp_path, monkeypatch)
    original = module.Worker.bind
    path.write_bytes(RAW + b'\n')
    observer = hooks.IPCObserver(image='fixture-image', key=KEY, emit=lambda value: None)
    with pytest.raises(ValueError, match='RANK_IPC_SOURCE_UNAVAILABLE'):
        observer.attach(module)
    assert module.Worker.bind is original


def test_missing_source_graph_preserves_partial_facts_and_gap(tmp_path, monkeypatch):
    module, _ = _fixture(tmp_path, monkeypatch)
    profile = json.loads(sources.PROFILES.read_bytes())
    profile['profiles'][0]['sources']['not_loaded'] = {}
    sources.PROFILES.write_text(json.dumps(profile))
    records = []
    observer = hooks.IPCObserver(image='fixture-image', key=KEY, emit=records.append)
    observer.attach(module)
    worker = module.Worker()
    worker.rank = 1
    assert worker.bind(4) == 5
    assert records[0]['scalars'] == {'rank': 1}
    assert 'RANK_IPC_SOURCE_GRAPH_INCOMPLETE' in records[0]['gaps']
    assert records[0]['engine_group_verified'] is False


def test_explicit_worker_dependency_does_not_claim_unloaded_parent_source(tmp_path, monkeypatch):
    module, _ = _fixture(tmp_path, monkeypatch)
    profile = json.loads(sources.PROFILES.read_bytes())
    profile['profiles'][0]['sources']['not_loaded_parent'] = {}
    profile['profiles'][0]['sources']['fixture']['hooks']['Worker.bind']['source_modules'] = ['fixture']
    sources.PROFILES.write_text(json.dumps(profile))
    records = []
    observer = hooks.IPCObserver(image='fixture-image', key=KEY, emit=records.append)
    observer.attach(module)
    worker = module.Worker()
    worker.rank = 1
    assert worker.bind(4) == 5
    assert set(records[0]['source_graph']) == {'fixture'}
    assert 'RANK_IPC_SOURCE_GRAPH_INCOMPLETE' not in records[0]['gaps']
    assert records[0]['engine_group_verified'] is False


def test_custom_instance_getter_is_not_executed(tmp_path, monkeypatch):
    module, _ = _fixture(tmp_path, monkeypatch)
    records = []
    worker = module.Worker()
    worker.rank = 1
    def forbidden(self, key):
        raise AssertionError('사용자 getter를 호출했다')
    module.Worker.__getattribute__ = forbidden
    observer = hooks.IPCObserver(image='fixture-image', key=KEY, emit=records.append)
    observer.attach(module)
    assert module.Worker.bind(worker, 4) == 5
    assert records[0]['scalars'] == {'rank': None}
    assert 'RANK_IPC_SCALAR_UNAVAILABLE:rank' in records[0]['gaps']
