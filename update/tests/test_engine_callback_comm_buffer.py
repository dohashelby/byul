"""REQ-21 TP 통신 buffer 수집: 실제 vLLM 0.31.0 CustomAllreduce 함수 코드를 감싸 만든 행을 투영에 넣는다.

생성자·graph buffer 등록·all_reduce·close의 원 코드를 쓰고, 원 코드가 부르는 torch·dist·
custom op만 대역으로 둔다. rank 두 개는 서로 다른 프로세스 신원으로 기록한다.
"""
from __future__ import annotations

import itertools
from types import SimpleNamespace

from guard.adapters import vllm_comm_buffer_callback as cb
from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.executors import engine_callback_projection as projection
from tests.test_cache_callback import source_module

SECRET = bytes(range(32))
_addresses = itertools.count(0x7f0000000000, 0x100000)


class Ops:
    """custom all-reduce op 대역. 주소는 새로 할당할 때마다 바뀐다."""
    def __init__(self): self.disposed = []
    def meta_size(self): return 64
    def allocate_shared_buffer_and_handle(self, size): return next(_addresses), b'h'
    def open_mem_handle(self, handle): return next(_addresses)
    def init_custom_ar(self, meta, rank_data, rank, full): return next(_addresses)
    def register_buffer(self, ptr, buffers): pass
    def all_reduce(self, ptr, inp, out, buffer, size): pass
    def get_graph_buffer_ipc_meta(self, ptr): return [1], [0]
    def register_graph_buffers(self, ptr, handles, offsets): pass
    def dispose(self, ptr): self.disposed.append(ptr)
    def free_shared_buffer(self, ptr): pass


class Device:
    def __init__(self, spec): self.type, _, index = spec.partition(':'); self.index = int(index or 0)


def namespace(ops, rank, world):
    dist = SimpleNamespace(get_backend=lambda g: 'gloo', Backend=SimpleNamespace(NCCL='nccl'), get_rank=lambda group=None: rank,
        get_world_size=lambda group=None: world, all_gather=lambda out, t, group=None: None,
        all_gather_object=lambda out, obj, group=None: out.__setitem__(slice(None), [obj] * len(out)),
        get_process_group_ranks=lambda group: list(range(world)), broadcast_object_list=lambda *a, **k: None)
    torch = SimpleNamespace(device=Device, tensor=lambda *a, **k: SimpleNamespace(item=lambda: rank),
        empty=lambda *a, **k: object(), empty_like=lambda t: SimpleNamespace(device=SimpleNamespace(type='cpu')), int=int, uint8='u8')
    platform = SimpleNamespace(get_device_capability=lambda: None, is_cuda=lambda: True, visible_device_id_to_physical_device_id=lambda i: i,
        is_cuda_alike=lambda: True, is_fully_connected=lambda ids: True, is_rocm=lambda: False)
    logger = SimpleNamespace(info_once=lambda *a: None, warning=lambda *a: None, warning_once=lambda *a: None, debug=lambda *a: None)
    return {'custom_ar': True, 'dist': dist, 'torch': torch, 'ops': ops, 'logger': logger, 'current_platform': platform,
            'in_the_same_node_as': lambda g, source_rank=0: [True] * world, '_supports_mnnvl_multimem_reduce_scatter': lambda d, w: False,
            '_group_can_attempt_mnnvl': lambda g, d: True, 'CUSTOM_ALL_REDUCE_MAX_SIZES': {}, '_can_p2p': lambda r, w: True,
            '_MNNVL_MULTIMEM_REDUCE_SCATTER_WORLD_SIZES': (2, 4, 8), '_all_ranks_true': lambda g, v: bool(v),
            'cast': lambda t, v: v, 'is_weak_contiguous': lambda t: True}


class Rank:
    """한 rank 프로세스: 원 모듈 코드와 자기 콜백 관측기."""
    def __init__(self, tmp_path, rank, rows, *, world=2, process='p'):
        folder = tmp_path / f'{process}{rank}'; folder.mkdir()
        module = source_module(cb.MODULE, folder, targets=cb.TARGETS, sources=cb.SOURCES)
        self.ops = Ops()
        vars(module).update(namespace(self.ops, rank, world))
        cls = module.CustomAllreduce
        cls._SUPPORTED_WORLD_SIZES = [2, 4, 6, 8, 16]
        cls._DEFAULT_MNNVL_ALL_GATHER_MAX_SIZES = {2: 1, 4: 1, 6: 1, 8: 1, 16: 1}
        cls._init_mnnvl_buffer = lambda self, size: None
        cls.create_shared_buffer = staticmethod(lambda size, group=None, uncached=False: [next(_addresses) for _ in range(world)])
        cls.free_shared_buffer = staticmethod(lambda pointers, group=None, rank=None: None)
        self.callbacks = sensor.EngineCallbacks('a' * 32, SECRET, rows.append,
            context_provider=lambda: {'process': process, 'rank': rank}, kernel_checks=True)
        assert self.callbacks.instrument(module)
        self.cls = cls

    def start(self):
        # 고정 조각은 함수 기본값을 싣지 않으므로 원 기본값을 그대로 넘긴다.
        self.comm = self.cls(object(), 0, 8192 * 1024, 4 * 1024 * 1024, None, 4 * 1024 * 1024, 4 * 1024 * 1024, 4 * 1024 * 1024, False)
        return self.comm


def generation(tmp_path, rows, process, *, world=2, use=True, close=True):
    ranks = [Rank(tmp_path, r, rows, world=world, process=process) for r in range(world)]
    comms = [r.start() for r in ranks]
    for comm in comms:
        comm.register_graph_buffers()
        if use:
            comm.all_reduce(SimpleNamespace(device=SimpleNamespace(type='cpu')), out=None, registered=False)
        if close:
            comm.close()
    return comms


CASES = [{'case_id': 'b', 'role': 'before', 'prompt': 'x'}, {'case_id': 'a', 'role': 'after', 'prompt': 'x'}]


def test_real_custom_allreduce_code_across_a_restart_completes_scope(tmp_path):
    rows = []
    generation(tmp_path, rows, 'g1'); generation(tmp_path, rows, 'g2', close=False)
    result = projection.project_comm_buffer(CASES, {'b': 'o', 'a': 'o'}, rows)
    assert result['generations'] == 4 and result['violation'] is False and result['full_scope'] is True
    inits = [r for r in rows if r['point'] == 'CustomAllreduce.__init__' and r['phase'] == 'RETURN']
    assert all(r['fields']['live'] and r['fields']['world_size'] == 2 for r in inits)
    assert '0x7f' not in str(rows) and not any(type(v) is int and v > 2**40 for r in inits for v in r['fields'].values())


def test_closed_communicator_used_is_a_violation(tmp_path):
    rows = []
    comms = generation(tmp_path, rows, 'g1')
    comms[0].all_reduce(SimpleNamespace(device=SimpleNamespace(type='cpu')), out=None, registered=False)  # close 뒤 사용
    result = projection.project_comm_buffer(CASES, {'b': 'o', 'a': 'o'}, rows)
    assert {'reason': 'closed_communicator_used'} in result['violations'] and result['violation'] is True


def test_buffer_not_registered_in_this_generation_is_a_violation(tmp_path):
    rows = []
    comms = generation(tmp_path, rows, 'g1', use=False, close=False)
    comms[0].buffer_ptrs = [next(_addresses), next(_addresses)]  # 등록하지 않은 buffer로 바뀜
    comms[0].all_reduce(SimpleNamespace(device=SimpleNamespace(type='cpu')), out=None, registered=False)
    assert {'reason': 'unregistered_buffer_used'} in projection.project_comm_buffer(CASES, {}, rows)['violations']


def test_without_a_restart_scope_stays_incomplete(tmp_path):
    rows = []
    generation(tmp_path, rows, 'g1')
    result = projection.project_comm_buffer(CASES, {'b': 'o', 'a': 'o'}, rows)
    assert result['violation'] is None and 'restart_and_reregistration_unobserved' in result['missing']


def test_single_gpu_leaves_the_communicator_inactive(tmp_path):
    rows = []
    rank = Rank(tmp_path, 0, rows, world=1, process='solo'); rank.start()
    result = projection.project_comm_buffer(CASES, {'b': 'o', 'a': 'o'}, rows)
    assert 'tensor_parallel_communicator_inactive' in result['missing'] and result['violation'] is None


def test_changed_answer_after_restart_alone_stays_unknown(tmp_path):
    rows = []
    generation(tmp_path, rows, 'g1'); generation(tmp_path, rows, 'g2', close=False)
    result = projection.project_comm_buffer(CASES, {'b': 'o', 'a': 'p'}, rows)
    assert result['violation'] is None and 'post_restart_output_changed' in result['missing']


def test_completion_fence_needs_the_lab_setting(tmp_path):
    rows = []
    rank = Rank(tmp_path, 0, rows, process='nofence')
    rank.callbacks.comm.fence = False
    comm = rank.start(); comm.all_reduce(SimpleNamespace(device=SimpleNamespace(type='cpu')), out=None, registered=False)
    returned = [r for r in rows if r['point'] == 'CustomAllreduce.all_reduce' and r['phase'] == 'RETURN']
    assert returned and returned[0]['fields']['completion_fence'] is None
