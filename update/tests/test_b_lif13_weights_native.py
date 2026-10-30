"""LIF13 stdlib EXAMPLE 회귀. 원 daemon/RPC/CUDA/torch import 실행은 0회다."""
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest

from tools import b_lif13_weights_native as native


@pytest.fixture
def cfg(tmp_path):
    def ref(kind):
        return {'kind': kind, 'id': kind + '-' + '0' * 26, 'revision': 1, 'sha256': 'a' * 64}
    return {'schema': native.CONFIG_SCHEMA, 'data_origin': 'EXAMPLE',
        'environment_ref': ref('ENV'), 'policy_ref': ref('POL'), 'plan_ref': ref('PLAN'),
        'target_sha256': 'b' * 64, 'source_commit': native.SOURCE_COMMIT, 'mode': 'zero_copy',
        'owner': '시험 담당자', 'recovery_owner': '복구 담당자', 'allow_global_release': True,
        'allow_empty_after': True, 'daemon': {'socket_path': str(tmp_path / '시험/daemon.sock'),
            'pid': 100, 'uid': 501, 'start_ticks': 200, 'boot_id_sha256': 'c' * 64,
            'pid_namespace_inode': 300, 'mount_namespace_inode': 400, 'gpu_uuid': 'EXAMPLE-GPU'},
        'cache_key': dict(checkpoint='EXAMPLE-NOT-LOADED', model_arch='EXAMPLE', tp_size=1, tp_rank=0,
            dtype='torch.float32', quantization=None, quant_config_hash='', revision=None,
            vllm_version='0.31.0', is_draft=False, dp_size=1, dp_rank=0),
        'entry_name': '시험 텐서', 'device_index': 0, 'read_elements': 16,
        'max_response_bytes': 1024, 'duration_seconds': 10, 'rpc_timeout_seconds': 1}


@pytest.fixture
def source(tmp_path, monkeypatch):
    # 승인 source인 척하는 제품 값 변경이 아니라 명시 EXAMPLE 파일 검사 fixture다.
    files = {}
    for name in ('protocol.py', 'ipc_loader.py', 'daemon.py'):
        path = tmp_path / native.PREFIX / name;path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# EXAMPLE source fixture\n')
        files[native.PREFIX + name] = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(native, 'SOURCE_FILES', files)
    return tmp_path


def test_default_preflight_imports_no_gpu_and_unicode_config_hash_matches_canonical(cfg, source, monkeypatch):
    touched = []
    monkeypatch.setattr(native, '_run_native', lambda *a: touched.append(True))
    result = native.capture(cfg, source)
    canonical = json.dumps(cfg, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()
    assert result['config_sha256'] == hashlib.sha256(canonical).hexdigest()
    assert not result['executed'] and result['rpc_attempts'] == 0 and not result['gpu_imported']
    assert not result['daemon_source_verified'] and not result['release_authorization_verified']
    assert touched == [] and 'torch' not in sys.modules and 'vllm' not in sys.modules


@pytest.mark.parametrize('field,value', [('mode', 'copy'), ('allow_empty_after', False), ('owner', ''),
    ('recovery_owner', None), ('read_elements', 17), ('read_elements', True), ('duration_seconds', 301)])
def test_invalid_explicit_scope_rejected_before_runtime(cfg, source, monkeypatch, field, value):
    cfg[field] = value
    monkeypatch.setattr(native, '_run_native', lambda *a: pytest.fail('실행 전 거부해야 함'))
    with pytest.raises(ValueError):native.capture(cfg, source, execute=True)


def test_source_mismatch_stops_before_rpc_or_backend(cfg, source, monkeypatch):
    (source / next(iter(native.SOURCE_FILES))).write_text('changed')
    with pytest.raises(ValueError, match='SOURCE_MISMATCH'):
        native.capture(cfg, source, execute=True, _example_backend=lambda *a: pytest.fail())


def test_example_backend_cannot_be_used_for_declared_capture(cfg, source):
    cfg['data_origin'] = 'DECLARED'
    with pytest.raises(ValueError, match='EXAMPLE_BACKEND_REQUIRES_EXAMPLE'):
        native.capture(cfg, source, execute=True, _example_backend=lambda *a: None)


class Socket:
    def __init__(self, chunks=()):
        self.chunks = list(chunks);self.reads = [];self.timeouts = [];self.sent = []
    def settimeout(self, value):self.timeouts.append(value)
    def recv(self, n):
        self.reads.append(n)
        value = self.chunks.pop(0)
        assert len(value) <= n
        return value
    def sendall(self, value):self.sent.append(value)


def test_oversize_prefix_is_rejected_before_payload_read_or_pickle():
    sock = Socket([struct.pack('!Q', 4096)]);checks = []
    bounded = native.BoundedSocket(sock, limit=1024, deadline=native.time.monotonic() + 1,
        timeout=1, check=lambda: checks.append(1))
    size, = struct.unpack('!Q', bounded.recv(8))
    with pytest.raises(ValueError, match='RESPONSE_BOUND'):
        # 원 recv_msg의 _recv_exact(sock, length)에서 처음 요청하는 길이다.
        bounded.recv(size)
        pytest.fail('역직렬화에 도달하면 안 됨')
    assert sock.reads == [8] and bounded.received == 8


def test_slow_fragmented_reads_obey_total_deadline(monkeypatch):
    clock = [0.0];sock = Socket([b'a', b'b'])
    original = sock.recv
    def recv(n):
        result = original(n);clock[0] += 0.6;return result
    sock.recv = recv
    monkeypatch.setattr(native.time, 'monotonic', lambda: clock[0])
    bounded = native.BoundedSocket(sock, limit=100, deadline=1, timeout=5, check=lambda: None)
    assert bounded.recv(2) == b'a'
    with pytest.raises(TimeoutError):bounded.recv(1)
    assert sock.timeouts == [1.0, 0.4]


def test_peer_change_after_read_rejects_before_bytes_reach_deserializer():
    sock = Socket([b'payload']);checks = []
    def check():
        checks.append(1)
        if len(checks) == 2:raise ValueError('LIF13_PEER_CHANGED')
    bounded = native.BoundedSocket(sock, limit=100, deadline=native.time.monotonic() + 1, timeout=1, check=check)
    with pytest.raises(ValueError, match='PEER_CHANGED'):
        bounded.recv(7)
        pytest.fail('역직렬화에 도달하면 안 됨')


@pytest.mark.parametrize('change', ['pid', 'uid', 'start_ticks', 'namespace', 'socket'])
def test_selected_peer_identity_checks_all_generation_fields(cfg, monkeypatch, change):
    monkeypatch.setattr(native.socket, 'SO_PEERCRED', 17, raising=False)
    identity = (1, 2, 501);expected = {k: cfg['daemon'][k] for k in ('pid', 'uid', 'start_ticks',
        'boot_id_sha256', 'pid_namespace_inode', 'mount_namespace_inode')}
    actual = dict(expected);pid, uid = 100, 501
    if change == 'pid':pid += 1
    if change == 'uid':uid += 1
    if change == 'start_ticks':actual['start_ticks'] += 1
    if change == 'namespace':actual['pid_namespace_inode'] += 1
    monkeypatch.setattr(native, '_socket_identity', lambda *a: (9, 9, 501) if change == 'socket' else identity)
    monkeypatch.setattr(native, '_proc_identity', lambda *a: actual)
    sock = SimpleNamespace(getsockopt=lambda *a: struct.pack('3i', pid, uid, 0))
    with pytest.raises(ValueError):native._check_peer(sock, cfg, identity)


def test_read_bound_is_applied_before_host_copy_and_uses_ephemeral_key(cfg):
    calls = []
    class Tensor:
        is_cuda = True;device = SimpleNamespace(index=0);layout = 'strided';shape = (1000000,);dtype = 'torch.float32'
        def is_contiguous(self):return True
        def dim(self):return 1
        def numel(self):return 1000000
        def detach(self):calls.append('detach');return self
        def view(self, *args):calls.append(('view', args));return self
        def __getitem__(self, value):calls.append(('slice', value.stop));return self
        def to(self, **kwargs):calls.append('host_copy');return self
        def contiguous(self):calls.append('small_host_contiguous');return self
        def numpy(self):return self
        def tobytes(self):return b'EXAMPLE_BOUNDED_BYTES'
    torch = SimpleNamespace(Tensor=Tensor, strided='strided', uint8='uint8')
    cfg['_digest_key'] = '11' * 32
    out = native._read_cuda(torch, Tensor(), cfg)
    assert calls.index(('slice', 16)) < calls.index('host_copy')
    assert out['selected_count'] == 16 and out['read_completion'] is True
    expected = hmac.new(bytes.fromhex(cfg['_digest_key']), b'EXAMPLE_BOUNDED_BYTES', hashlib.sha256).hexdigest()
    assert out['digest_sha256'] == expected
    assert 'EXAMPLE_BOUNDED_BYTES' not in json.dumps(out) and cfg['_digest_key'] not in json.dumps(out)
    cfg['_digest_key'] = '22' * 32
    assert native._read_cuda(torch, Tensor(), cfg)['digest_sha256'] != expected


def test_noncontiguous_tensor_has_no_flatten_or_copy(cfg):
    class Tensor:
        is_cuda = True;device = SimpleNamespace(index=0)
        def is_contiguous(self):return False
        def detach(self):pytest.fail('전체 tensor 복사 우회 금지')
    result = native._read_cuda(SimpleNamespace(Tensor=Tensor), Tensor(), cfg)
    assert result['status'] == 'unsupported_layout' and not result['collected']


def test_queued_after_and_cleanup_are_preserved_in_top_level_consumer_facts():
    facts = {'held_after': {'collected': True}, 'digest_equal': True,
             'followup_get_state': {'status': 'error', 'reason': 'weights_released'}}
    job = {'slot': 0, 'received': set(), 'messages': [
        {'slot': 0, 'stage': 'after', 'facts': facts}, {'slot': 0, 'stage': 'cleanup', 'facts': {'references_dropped': True}}], 'eof': True}
    record = {'consumers': [{}]}
    assert native._receive([job], 'after', native.time.monotonic() + 1, record)
    assert native._receive([job], 'cleanup', native.time.monotonic() + 1, record)
    assert record['consumers'][0]['held_after']['collected']
    assert record['consumers'][0]['followup_get_state']['reason'] == 'weights_released'
    assert record['consumers'][0]['cleanup']['references_dropped']


def test_late_error_and_source_check_do_not_clear_existing_partial_facts(cfg, source):
    def example(config, root, record):
        record['consumers'][0]['baseline'] = {'collected': True, 'digest_sha256': 'd' * 64}
        record['release'] = {'attempted': True, 'response_received': True, 'status': 'ok'}
        record['consumers'][1]['held_after'] = {'collected': True, 'digest_sha256': 'e' * 64}
        (root / next(iter(native.SOURCE_FILES))).write_text('late source mutation')
        raise RuntimeError('PRIVATE_ERROR_ENTRY_SOCKET_NOT_SAVED')
    result = native.capture(cfg, source, execute=True, _example_backend=example)
    assert result['consumers'][0]['baseline']['collected'] and result['consumers'][1]['held_after']['collected']
    assert result['release']['response_received']
    assert result['source_validation']['post']['status'] == 'unavailable'
    assert result['cleanup']['daemon_recovery_complete'] is None
    text = json.dumps(result)
    assert 'PRIVATE_ERROR' not in text and cfg['entry_name'] not in text and cfg['daemon']['socket_path'] not in text
    assert not result['release_authorization_verified'] and not result['daemon_source_verified']


@pytest.mark.parametrize('failure', [False, True])
def test_every_cuda_export_is_rebuilt_without_storage_dedup_and_only_selected_is_held(failure):
    calls = []
    shared_storage = object()
    class TensorEntry:
        cpu_tensor = None
        def __init__(self, number):self.number = number;self.ipc_args = ('EXAMPLE-HANDLE',)
        def rebuild(self, device):
            calls.append(self.number)
            if failure and self.number == 1:raise RuntimeError('EXAMPLE_REBUILD_ERROR')
            return shared_storage
    response = {'entries': {str(i): TensorEntry(i) for i in range(3)}}
    held, facts = native._consume_exports(response, SimpleNamespace(TensorEntry=TensorEntry), 0, '0', native.time.monotonic() + 1)
    assert held is shared_storage and calls == [0, 1, 2]
    assert facts['received_cuda_entries'] == 3 and facts['rebuilt_cuda_entries'] == (2 if failure else 3)
    assert facts['complete'] is (not failure)
    calls.clear()
    held, _ = native._consume_exports(response, SimpleNamespace(TensorEntry=TensorEntry), 0, None, native.time.monotonic() + 1)
    assert held is None and calls == [0, 1, 2]  # 후속 get_state의 exports도 모두 소비한다.


def test_cpu_by_value_entry_is_never_rebuilt_or_relabelled_zero_copy():
    class TensorEntry:
        ipc_args = None
        cpu_tensor = object()
        def rebuild(self, device):pytest.fail('CPU by-value 대체 금지')
    held, facts = native._consume_exports({'entries': {'PRIVATE_TENSOR': TensorEntry()}},
        SimpleNamespace(TensorEntry=TensorEntry), 0, 'PRIVATE_TENSOR', native.time.monotonic() + 1)
    assert held is None and facts['cpu_by_value_entries'] == 1 and facts['rebuilt_cuda_entries'] == 0
    assert 'PRIVATE_TENSOR' not in json.dumps(facts)


def test_hidden_child_path_still_requires_explicit_execute(monkeypatch):
    monkeypatch.setattr(native, '_child_main', lambda: pytest.fail('실행 전 차단해야 함'))
    with pytest.raises(SystemExit) as caught:native.main(['--child'])
    assert caught.value.code == 1


def test_parent_barrier_and_cleanup_are_separate_from_daemon_recovery(cfg, source, monkeypatch):
    monkeypatch.setattr(native.socket, 'SO_PEERCRED', 17, raising=False)
    processes = [];actions = [];released = []
    class Stream:
        def write(self, value):actions.append(json.loads(value))
        def flush(self):pass
        def close(self):pass
    class Process:
        def __init__(self, argv, **kwargs):
            assert argv[-2:] == ['--child', '--execute'] and kwargs['start_new_session'] is True
            self.stdin = Stream();self.stdout = Stream();self.returncode = None;self.pid = 10000 + len(processes)
            processes.append(self)
        def wait(self, timeout):self.returncode = 0
        def poll(self):return self.returncode
    def receive(jobs, stage, deadline, record):
        if stage == 'baseline':
            for row in record['consumers']:
                row['baseline'] = {'collected': True, 'is_cuda': True, 'cuda_ipc_rebuilt': True,
                    'digest_sha256': 'd' * 64, 'exports': {'complete': True}, 'source_validation': {'post': 'verified'}}
        elif stage == 'after':
            assert released == [1]
            for row in record['consumers']:
                row.update(held_after={'collected': True, 'digest_sha256': 'd' * 64}, digest_equal=True,
                    followup_get_state={'attempted': True, 'status': 'error', 'reason': 'weights_released'})
        else:
            for row in record['consumers']:row['cleanup'] = {'references_dropped': True}
        return True
    def rpc(config, modules, command, identity, deadline, note):
        assert command == 'release' and len(processes) == 2
        assert not any(action.get('action') == 'after' for action in actions)
        released.append(1)
        return {}, {'attempted': True, 'status': 'ok', 'response_received': True,
            'peer_generation_verified': True, 'daemon_generation_sha256': 'e' * 64}
    monkeypatch.setattr(native.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(native.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(native, '_socket_identity', lambda *a: (1, 2, 501))
    monkeypatch.setattr(native, '_proc_identity', lambda *a: {k: cfg['daemon'][k] for k in (
        'pid', 'uid', 'start_ticks', 'boot_id_sha256', 'pid_namespace_inode', 'mount_namespace_inode')})
    monkeypatch.setattr(native.subprocess, 'Popen', Process)
    monkeypatch.setattr(native, '_receive', receive)
    monkeypatch.setattr(native, '_load_native', lambda *a: {})
    monkeypatch.setattr(native, '_rpc', rpc)
    result = native.capture(cfg, source, execute=True, _example_backend=native._run_native)
    assert len(processes) == 2 and released == [1]
    assert [a['action'] for a in actions if 'action' in a] == ['after', 'after']
    assert result['cleanup']['owned_children_complete'] is True
    assert result['cleanup']['daemon_recovery_complete'] is result['cleanup']['daemon_native_cleanup'] is None
    assert result['data_origin'] == 'EXAMPLE' and not result['release_authorization_verified']
    assert all(row['digest_equal'] is True and row['process_cleanup']['joined'] for row in result['consumers'])
    assert '_digest_key' not in json.dumps(result) and cfg['entry_name'] not in json.dumps(result)
