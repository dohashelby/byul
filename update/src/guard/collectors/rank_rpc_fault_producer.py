"""사전 결합·정확 원 RPC 근거를 공통 rank-fault 부분 입력으로 변환하는 미등록 생산자."""
from pathlib import Path

from guard.adapters import vllm_rank_rpc_input as adapter
from guard.adapters import vllm_rank_ipc_runtime as runtime
from . import gpu_common_inputs as common
from . import gpu_common_input_v4 as v4

NAME = 'guard-engine-callback-vllm-rank-rpc-fault'
VERSION = '1'


def producer():
    source = Path(adapter.__file__).resolve().parents[2]
    paths = [Path(__file__), Path(adapter.__file__), Path(adapter.capture_code.__file__),
        *(source / name for name in sorted(runtime.ASSETS) if name != 'sitecustomize.py')]
    return {'name': NAME, 'version': VERSION, 'source_sha256': v4.source_digest(paths)}


def record(prepared, capture, *, pod, environment_ref, target_sha256, prepared_sha256, session,
           wire_schema=v4.SCHEMA):
    if type(pod) is not dict or set(pod) != {'pod_uid', 'container_id', 'restart_count'}:
        raise common.GpuInputError('GPU_INPUT_SCOPE_MISMATCH')
    expected = {'schema': 'guard-rank-rpc-product-binding/1', 'session': session,
        'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
        'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'], 'restart_count': pod['restart_count']}
    projection = adapter.project(prepared, capture, expected)
    bindings, events = [], []
    for row in projection['events']:
        context = row['context']
        binding = {**pod, 'worker_pid': context['pid'], 'start_ticks': context['start_ticks'], 'rank': row['rank']}
        if binding not in bindings:
            bindings.append(binding)
        events.append({'seq': len(events), 'binding': bindings.index(binding), 'event': row['event'],
            'at_ms': row['at_ns'] // 1_000_000, 'ack': row['ack'], 'fault_kind': row['fault_kind'],
            'request_sha256': row['request_sha256']})
    value = {'schema': common.SCHEMA, 'kind': 'rank-fault', 'producer': producer(),
        'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
        'session': session, 'bindings': bindings, 'events': events,
        'completeness': {'source_ended': False, 'dropped': 0, 'diagnostic_codes': projection['diagnostic_codes']}}
    value = v4.encode(value, wire_schema=wire_schema)
    validation = common.validate(value, kind='rank-fault', environment_ref=environment_ref,
        target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)
    return value, validation, projection
