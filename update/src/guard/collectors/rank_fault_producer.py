"""기존 엔진 안 관측과 별도 클라이언트 근거를 rank-fault 계약으로 변환한다."""
from pathlib import Path

from guard.adapters import vllm_rank_fault_input as adapter
from . import gpu_common_inputs as contract
from . import gpu_common_input_v4 as v4

NAME = 'guard-engine-callback-vllm-rank-fault'
VERSION = '1'


def producer():
    root = Path(adapter.__file__).parent
    paths = [Path(__file__), *[root / (name + '.py') for name in (
        'vllm_rank_fault_input', 'vllm_rank_fault_closure', 'vllm_fault_projection', 'vllm_fault_metadata', 'vllm_fault_control',
        'vllm_fault_sensor', 'vllm_fault_runtime', 'vllm_rank_failure_sensor', 'vllm_rank_failure_metadata',
        'vllm_lifecycle_sensor', 'vllm_lifecycle_profiles', 'vllm_termination_sensor',
        'vllm_termination_projection', 'vllm_termination_metadata')]]
    paths += [root.parent / 'executors' / name for name in ('lifecycle_capture.py', 'lifecycle_session.py')]
    return {'name': NAME, 'version': VERSION, 'source_sha256': v4.source_digest(paths)}


def record(packet, client, receipt, source_scope, deadlines, *, pod, environment_ref, target_sha256,
           prepared_sha256, session, recovery_client=None, closure_evidence=None, wire_schema=contract.SCHEMA):
    """외부에서 받은 예상 범위를 검증한다. packet 자신을 예상값으로 쓰지 않는다."""
    if (type(source_scope) is not dict or type(pod) is not dict
            or source_scope.get('session') != session or source_scope.get('target_sha256') != target_sha256
            or source_scope.get('pod_uid') != pod['pod_uid'] or source_scope.get('container_id') != pod['container_id']):
        raise contract.GpuInputError('GPU_INPUT_SCOPE_MISMATCH')
    projection = adapter.project(packet, client, receipt, source_scope, deadlines,
        recovery_client=recovery_client, closure_evidence=closure_evidence)
    bindings, events = [], []
    for item in projection['events']:
        context = item['context']
        binding = {'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'].removeprefix('containerd://')[:128],
            'restart_count': pod['restart_count'], 'worker_pid': context['pid'], 'start_ticks': context['start_ticks'],
            'rank': source_scope['rank']}
        if binding not in bindings:
            bindings.append(binding)
        events.append({'seq': len(events), 'binding': bindings.index(binding), 'event': item['event'],
            'at_ms': item['at_ns'] // 1_000_000, 'ack': item['ack'], 'fault_kind': item['fault_kind'],
            'request_sha256': item['request_sha256']})
    # 원 관측이 비면 가상의 PID를 채우지 않고 기존 닫힌 계약이 거부하게 둔다.
    value = {'schema': contract.SCHEMA, 'kind': 'rank-fault', 'producer': producer(),
        'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
        'session': session, 'bindings': bindings, 'events': events,
        'completeness': {'source_ended': projection['scope_complete'], 'dropped': 0,
                         'diagnostic_codes': sorted(set(projection['diagnostic_codes']))}}
    value = v4.encode(value, wire_schema=wire_schema)
    result = contract.validate(value, kind='rank-fault', environment_ref=environment_ref, target_sha256=target_sha256,
                               prepared_sha256=prepared_sha256, session=session)
    return value, result, projection
