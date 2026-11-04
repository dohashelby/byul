"""REQ-14 TP2: 실제 vLLM 0.31.0 WorkerProc RPC·출력 함수와 CustomAllreduce 함수 코드를 rank 두 개로 돌린다.

각 rank는 별도 프로세스 신원(콜백 관측기)이다. 모델 실행은 대역이며 그 안에서 원 all_reduce를
부른다. 출력은 지정 출력 rank만 내보낸다(원 _execute_worker_rpc의 동작).
"""
from __future__ import annotations

from types import SimpleNamespace

from guard.adapters import vllm_distributed_callback as distributed
from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.executors import engine_callback_projection as projection
from tests.test_cache_callback import source_module
from tests.test_engine_callback_comm_buffer import Rank

SECRET = bytes(range(32))
TAGS = {'a': 'byul-cb-' + 'a' * 32}
INPUT = SimpleNamespace(device=SimpleNamespace(type='cpu'))


class Worker:
    def __init__(self, tmp_path, rank, rows, *, model='m', revision='r', closed=False, process='g'):
        self.comm_rank = Rank(tmp_path, rank, rows, process=process)
        folder = tmp_path / f'{process}-mp{rank}'; folder.mkdir()
        module = source_module(distributed.MP, folder, targets=distributed.TARGETS, sources=distributed.rank.SOURCE_ADDITIONS)
        module.AsyncModelRunnerOutput = type('AsyncModelRunnerOutput', (), {})
        module.WorkerProc.ResponseStatus = SimpleNamespace(SUCCESS='SUCCESS', FAILURE='FAILURE')
        assert self.comm_rank.callbacks.instrument(module)  # 같은 rank 프로세스의 관측기
        self.comm = self.comm_rank.start()
        if closed:
            self.comm.close()
        output = type('Output', (), {})(); output.req_ids = ['cmpl-' + TAGS['a'] + '-0']; output.sampled_token_ids = [[1]]
        def execute(scheduler):
            self.comm.all_reduce(INPUT, out=None, registered=False)  # 층 사이 TP 합산
            return output
        self.queue = []
        worker = module.WorkerProc(); worker.rank = rank; worker.local_rank = rank
        worker.worker = SimpleNamespace(execute_model=execute, model_config=SimpleNamespace(model=model, revision=revision))
        worker.use_async_scheduling = False
        worker.async_output_queue = SimpleNamespace(put=self.queue.append)
        worker.worker_response_mq = SimpleNamespace(enqueue=self.queue.append)
        self.proc = worker

    def step(self, output_rank=0):
        scheduler = type('Scheduler', (), {})(); scheduler.num_scheduled_tokens = {'cmpl-' + TAGS['a'] + '-0': 4}
        self.proc._execute_worker_rpc(('execute_model', (scheduler,), {}, output_rank))


def run(tmp_path, **options):
    rows = []
    workers = [Worker(tmp_path, r, rows, **({k: v for k, v in options.items() if k != 'rank1'} if r == 0 else options.get('rank1', {})))
               for r in range(2)]
    for w in workers:
        w.step()
    run.rows = rows
    return projection.project_distributed(rows, SECRET, TAGS), workers


def test_real_tp2_rpc_and_all_reduce_bind_both_ranks_and_the_output_rank(tmp_path):
    result, workers = run(tmp_path)
    assert result['ranks'] == 2 and result['violation'] is False
    assert workers[0].queue and not workers[1].queue  # 출력 rank 0만 응답한다
    assert 'intermediate_tensor_buffer_lineage_unobserved' not in result['missing']
    assert 'intermediate_buffer_completion_unobserved' not in result['missing']
    # 설정 지문은 가중치 소비 증명이 아니므로 범위는 끝나지 않는다.
    assert result['missing'] == ['model_generation_consumption_unobserved'] and result['full_scope'] is False


def test_rank_running_a_different_model_generation_is_a_violation(tmp_path):
    result, _ = run(tmp_path, rank1={'revision': 'other'})
    assert {'reason': 'rank_model_generation_mismatch'} in result['violations']


def test_transfer_on_a_closed_communicator_is_a_violation(tmp_path):
    result, _ = run(tmp_path, rank1={'closed': True})
    assert {'point': 'CustomAllreduce.all_reduce', 'reason': 'closed_communicator_transfer'} in result['violations']


def test_without_all_reduce_rows_the_intermediate_lineage_stays_unobserved(tmp_path):
    run(tmp_path)
    kept = [r for r in run.rows if r['point'] != 'CustomAllreduce.all_reduce']
    result = projection.project_distributed(kept, SECRET, TAGS)
    assert result['violation'] is False and 'intermediate_tensor_buffer_lineage_unobserved' in result['missing']


def test_single_rank_keeps_the_previous_projection(tmp_path):
    rows = []
    Worker(tmp_path, 0, rows).step()
    result = projection.project_distributed(rows, SECRET, TAGS)
    assert result['ranks'] == 1 and result['violation'] is False
    assert 'intermediate_tensor_buffer_lineage_unobserved' in result['missing']
