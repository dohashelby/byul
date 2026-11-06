"""P2 runner-consumption 생산자의 V2 runner 경로(개발 담당, 2단계). 계약 종류는 V1과 같은 `runner-consumption`이다.

API 쪽 행은 V1 관측기(`vllm_consumption_callback`)의 InputProcessor 행을, runner 쪽 행은 V2 관측기
(`vllm_consumption_v2_callback`)의 `add_requests`·`sample` 행을 쓴다. V2 행을 V1 변환의 같은 단계
(runner 도착 = `_update_states`, sampler 적용 = `_sample`)로 옮겨 한 변환(`gpu_input_producers.runner_consumption_events`)을 쓴다.
같은 세션에 V1 runner 행이 함께 있으면 두 경로를 섞지 않도록 V1 runner 행을 버리고 진단을 남긴다.

생산자 신원은 V1 관측기·V2 관측기·V1 변환·이 모듈의 소스 bytes 해시다. V1 생산자(등록됨)와 이름이 다르며,
GPU 확인 전에는 지원 목록에 등록하지 않는다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from guard.adapters import vllm_consumption_callback as v1
from guard.adapters import vllm_consumption_v2_callback as v2
from . import gpu_common_inputs as contract
from . import gpu_input_producers as producers

NAME = 'guard-engine-callback-runner-consumption-v2'
VERSION = '1'
_STAGE = {'GPUModelRunner.add_requests': 'GPUModelRunner._update_states', 'GPUModelRunner.sample': 'GPUModelRunner._sample'}
_V1_RUNNER = frozenset(_STAGE.values())


def producer():
    digest = hashlib.sha256()
    for path in (Path(v1.__file__), Path(v2.__file__), Path(producers.__file__), Path(__file__)):
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    return {'name': NAME, 'version': VERSION, 'source_sha256': digest.hexdigest()}


def translate(processes):
    """V2 runner 행 → V1 변환 입력. (프로세스 목록, 진단 코드)."""
    out, codes = [], set()
    for process in processes:
        rows = []
        for row in process['events']:
            if row.get('schema') == v1.SCHEMA and row.get('point') in _V1_RUNNER:
                codes.add('CONSUMPTION_V1_RUNNER_ROWS_PRESENT'); continue
            if row.get('schema') == v2.SCHEMA:
                if row.get('point') not in _STAGE:
                    continue
                row = dict(row, schema=v1.SCHEMA, point=_STAGE[row['point']])
            rows.append(row)
        out.append(dict(process, events=rows))
    return out, codes


def runner_consumption_v2_record(processes, *, pod, environment_ref, target_sha256, prepared_sha256, session, truncated=False,
                                 requests=None):
    translated, extra = translate(processes)
    if not any(r.get('schema') == v2.SCHEMA for p in processes for r in p['events']):
        extra.add('CONSUMPTION_V2_RUNNER_UNOBSERVED')
    events, processes_used, codes = producers.runner_consumption_events(translated, requests=requests)
    codes = sorted(set(codes) | extra | ({'CONSUMPTION_READ_TRUNCATED'} if truncated else set()))
    bindings = [{'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'].removeprefix('containerd://')[:128],
                 'restart_count': pod['restart_count'], 'worker_pid': p['pid'], 'start_ticks': p['start_ticks'], 'rank': 0}
                for p in processes_used]
    validated = {e['request_sha256'] for e in events if e['event'] == 'api_validated'}
    closed = bool(validated) and all(all(any(e['request_sha256'] == r and e['event'] == stage for e in events)
                                       for stage in ('runner_received', 'sampler_applied')) for r in validated)
    record = {'schema': contract.SCHEMA, 'kind': 'runner-consumption', 'producer': producer(),
              'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
              'session': session, 'bindings': bindings, 'events': events,
              'completeness': {'source_ended': closed and not truncated and 'CONSUMPTION_V1_RUNNER_ROWS_PRESENT' not in codes,
                               'dropped': 0, 'diagnostic_codes': [c for c in codes if contract._ID.fullmatch(c)]}}
    return record, contract.validate(record, kind='runner-consumption', environment_ref=environment_ref,
                                     target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)
