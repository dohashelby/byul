"""실제 B observer 코드→consumer→원 manual/Store 연결 회귀. 표준 라이브러리 EXAMPLE 객체만 쓴다.

vLLM/GPU/worker/event/HTTP 실측이 아니며 native 자격 flag를 만들어 넣지 않는다.
"""
from copy import deepcopy
import hashlib
import json
import os

import pytest

from guard.adapters.vllm_graph_buffers import GraphBufferObserver
from guard.store import Store
from tests.test_b_req20_graph import case  # 기존 소유 EXAMPLE 입력 fixture 재사용
from tests.test_vllm_graph_buffers import Object, Tensor  # 기존 metadata 대역, 실 Torch 의존성 없음
from tools import b_req20_graph as tool


@pytest.mark.parametrize('finish_phase', ['RETURN', 'RAISE'])
def test_observer_example_metadata_survives_original_manual_and_exact_ref_restore(case, finish_phase):
    # metadata 대역에서 callback 코드를 실제 호출한다. worker/source 자격은 만들지 않는다.
    tensor = Tensor(device='cpu', shape=(4,), version=1)
    actual_owner = Object(input_batch=Object(), input_ids=Object(gpu=tensor),
                          private_note='EXAMPLE_OWNER_PRIVATE_NOT_AN_OBSERVATION')
    binding = {'pid': os.getpid(), 'call_id_sha256': tool.digest('EXAMPLE-local-prepare-call'),
        'session_id_sha256': tool.digest(case['prepared']['session']),
        'process_start_sha256': tool.digest('EXAMPLE-process-start-not-worker-proof'),
        'native_request_id_sha256': tool.digest('EXAMPLE-request-not-HTTP-proof')}
    point = 'GPUModelRunner._prepare_input_ids'
    args = (actual_owner, None, 1, 2, None)
    observer = GraphBufferObserver()
    try:
        before = observer.snapshot(point, args, {}, phase='CALL', binding=binding)
        tensor._version += 1  # EXAMPLE metadata 변화이며 실제 CPU/GPU write 증거가 아니다.
        after = observer.snapshot(point, args, {}, phase=finish_phase, binding=binding,
            exception=RuntimeError('EXAMPLE_EXCEPTION_NOT_SAVED') if finish_phase == 'RAISE' else None)
    finally:
        closed = observer.close()
    assert closed['complete'] and not closed['collection_gap']
    assert after['metadata']['call_transition']['changed_input_versions'] == ['input_ids']
    assert all('source_qualified' not in row and 'actual_worker_observed' not in row for row in (before, after))
    assert after['metadata']['buffers']['input_ids']['descriptor']['device_type'] == 'cpu'

    raw = tool._empty(case['env'], case['target'], case['prepared'], case['manifest'])
    raw.update(native_rows=[before, after], gaps=['NATIVE_GRAPH_CAPABILITY_UNAVAILABLE'],
               cleanup={'complete': False, 'worker_observer_close_verified': None})
    original_rows = deepcopy(raw['native_rows'])
    capture_calls = []
    def example_capture(*args, **kwargs):
        capture_calls.append(1)
        return raw

    result = tool.run(**case['args'], execute=True, _example_capture=example_capture)
    assert capture_calls == [1] and not result['real_gpu_execution_verified']
    output = case['args']['output']
    persisted = json.loads((output / 'graph-capture.json').read_text())
    assert [row['phase'] for row in persisted['rows']] == ['CALL', finish_phase]
    assert [row['observer_seq'] for row in persisted['rows']] == [before['seq'], after['seq']]
    for original, row in zip(original_rows, persisted['rows'], strict=True):
        expected = original['metadata']['buffers']['input_ids']
        observed = row['metadata']['buffers']['input_ids']
        assert observed['shape'] == expected['descriptor']['shape'] == [4]
        assert observed['mutation_version'] == expected['mutation_version']
        assert observed['buffer_token'] == expected['buffer_token']
        assert all(row['binding'][key] == value for key, value in original['binding'].items())
        assert row['binding']['asgi_request_id_sha256'] is None
        assert row['native_record_seq'] is None and not row['source_qualified'] and not row['qualified']
        assert row['metadata']['consumer']['event_completion_verified'] is None
    assert not persisted['confirmed_violations'] and not persisted['numerical_comparison_verified']
    assert persisted['graph_output_consumption']['verified'] is None

    store, restored = Store.read_only(output / 'store'), Store.read_only(output / 'restored')
    evidence, = store.iter_kind('EVD'); verdict, = store.iter_kind('RES'); report, = store.iter_kind('RPT')
    assert evidence.plan == case['plan'].ref() and evidence.environment == case['env'].ref()
    assert verdict.evidence == evidence.ref() and verdict.ref() in report.inputs_manifest
    assert evidence.data_origin.value == 'DECLARED'
    assert verdict.verdicts.model_dump(mode='json') == dict.fromkeys(('security', 'functional', 'performance'), 'UNKNOWN')
    assert not evidence.normal_control.attempted and all(not row.attempted for row in evidence.challenges)
    artifact, = evidence.artifacts
    capture_bytes = (output / 'graph-capture.json').read_bytes()
    assert artifact.sha256 == hashlib.sha256(capture_bytes).hexdigest()
    assert (store.root / 'artifacts' / artifact.sha256).read_bytes() == capture_bytes
    assert tool._refs(store) == tool._refs(restored) and store.verify() == restored.verify() == []
    assert result['source_unchanged'] and result['exact_refs_restored']
    assert 'EXAMPLE_OWNER_PRIVATE' not in capture_bytes.decode() and 'EXAMPLE_EXCEPTION' not in capture_bytes.decode()
