"""PLT-01·04 process-device-provider 생산자(개발 담당, 2단계 P1). 노드 관측·열기 시험·Pod 명세를 #196 계약 기록으로 만든다.

생산자 신원은 관측기(`process_device_observer.py`)와 이 변환의 소스 bytes 해시다. 기존 생산자 모듈
(`gpu_input_producers.py`)과 분리해 두어, 이 생산자를 고쳐도 이미 등록한 생산자들의 소스 SHA가 바뀌지 않는다.
등록(`SUPPORTED_PRODUCERS`)은 GPU 확인 뒤에 한다. 그 전까지 기록은 형식이 맞아도 complete가 아니다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import gpu_common_inputs as contract
from . import process_device_observer as device_observer

DEVICE_PRODUCER_NAME = 'guard-node-process-device-provider'
DEVICE_PRODUCER_VERSION = '1'


def device_producer():
    digest = hashlib.sha256()
    for path in (Path(device_observer.__file__), Path(__file__)):
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    return {'name': DEVICE_PRODUCER_NAME, 'version': DEVICE_PRODUCER_VERSION, 'source_sha256': digest.hexdigest()}


def _device_row(seq, event, binding, process, **values):
    base = {'gpu_uuid_sha256': None, 'nvml_host_pid': None, 'namespace_pid': process['nspid'][-1],
            'cgroup_sha256': process['cgroup_sha256'], 'env_name': None, 'provider_kind': None, 'receiver_sha256': None,
            'credential_generation': None, 'nonsecret_id_sha256': None, 'result': 'unknown'}
    return {'seq': seq, 'event': event, 'binding': binding, **base, **values}


def process_device_events(node, opened, candidates, *, receiver_sha256):
    """노드 관측·열기 시험·자격 후보 → (행, 프로세스, 진단 코드).

    - gpu_process: NVML host PID가 고른 프로세스 NSpid 사슬의 첫 값과 같을 때만 잇는다(같은 숫자만으로 잇지 않는다).
      잇지 못한 NVML 행은 이 컨테이너 밖 GPU 프로세스로 세고 진단으로 남긴다.
    - device_access: 프로세스 × 노드 GPU device. 그 프로세스가 연 fd가 있으면 allowed, 아니면 같은 실효 UID의 컨테이너 안 열기 시험 결과,
      컨테이너에 node가 없으면 unknown이다. GPU node가 컨테이너에 없으면 집행을 시험하지 못한 것으로 진단을 남긴다.
    - provider_access: 프로세스 × 자격 후보. 환경 변수는 이름 존재로 allowed/denied, 마운트는 그 아래 열린 파일이 있으면
      allowed, 마운트만 있으면 unknown, 없으면 denied다. envFrom은 이름을 모르므로 unknown과 진단이다.
    """
    codes = set(node['codes'])
    processes = []
    for process in node['processes']:
        codes |= {c for c in process['codes'] if c != 'PID_IDENTITY_CHANGED'}
        if 'PID_IDENTITY_CHANGED' in process['codes']:
            codes.add('PROCESS_IDENTITY_CHANGED')
        if process['stable']:
            processes.append(process)
    if not node['nvml']['available']:
        codes.add('NVML_UNAVAILABLE')
    if opened is None:
        codes.add('DEVICE_OPEN_TEST_UNAVAILABLE')
    tested = {d['path']: d['result'] for d in (opened or {}).get('devices', [])}
    events = []
    for item in node['nvml']['rows']:
        matches = [i for i, p in enumerate(processes) if len(p['nspid']) >= 2 and p['nspid'][0] == item['pid']]
        if len(matches) != 1:
            codes.add('NVML_PROCESS_OUTSIDE_CONTAINER')
            continue
        process = processes[matches[0]]
        events.append(_device_row(len(events), 'gpu_process', matches[0], process,
                                  gpu_uuid_sha256=hashlib.sha256(item['gpu_uuid'].encode()).hexdigest(), nvml_host_pid=item['pid'],
                                  result='allowed'))
    for device in node['devices']:
        gpu = device_observer.GPU_NODE.fullmatch(device['path']) is not None
        if gpu and device['path'] not in tested:
            codes.add('DEVICE_UNEXPOSED_GPU_UNTESTED')
        if gpu and device['gpu_uuid'] is None:
            codes.add('DEVICE_GPU_UUID_UNMAPPED')
    for index, process in enumerate(processes):
        if len(process['nspid']) < 2:
            codes.add('PROCESS_NAMESPACE_CHAIN_UNAVAILABLE')
        held = {fd['path'] for fd in process['device_fds'] if fd['read_capable'] or fd['write_capable']}
        same_uid = opened is not None and opened['uid_effective'] == process['uid_effective']
        if opened is not None and not same_uid:
            codes.add('DEVICE_OPEN_TEST_UID_MISMATCH')
        for device in node['devices']:
            result = 'allowed' if device['path'] in held else tested.get(device['path'], 'unknown') if same_uid else 'unknown'
            uuid = device['gpu_uuid']
            events.append(_device_row(len(events), 'device_access', index, process, result=result,
                                      gpu_uuid_sha256=hashlib.sha256(uuid.encode()).hexdigest() if uuid else None,
                                      nonsecret_id_sha256=hashlib.sha256(device['path'].encode()).hexdigest()))
        mount_index = 0
        for candidate in candidates:
            if candidate['env_name']:
                unread = {'ENVIRON_UNAVAILABLE', 'ENVIRON_TRUNCATED'} & set(process['codes'])
                result = 'allowed' if candidate['env_name'] in process['env_present'] else 'unknown' if unread else 'denied'
            elif candidate['mount_path']:
                unread = {'MOUNTINFO_UNAVAILABLE', 'MOUNTINFO_TRUNCATED'} & set(process['codes'])
                result = ('allowed' if mount_index in process['mount_fds'] else
                          'unknown' if mount_index in process['mounts_present'] or unread else 'denied')
            else:
                result = 'unknown'; codes.add('PROVIDER_ENVFROM_NAMES_UNOBSERVED')
            events.append(_device_row(len(events), 'provider_access', index, process, result=result,
                                      env_name=candidate['env_name'], provider_kind=candidate['kind'],
                                      receiver_sha256=receiver_sha256, nonsecret_id_sha256=candidate['nonsecret_id_sha256']))
            if candidate['mount_path']:
                mount_index += 1
    if 'PROCESS_SET_BOUND' in codes or len(events) > contract.MAX_EVENTS:
        codes.add('PROCESS_DEVICE_ROWS_BOUND'); events = events[:contract.MAX_EVENTS]
    return events, processes, sorted(codes)


def process_device_record(node, opened, candidates, *, pod, container, environment_ref, target_sha256, prepared_sha256,
                          session, scope_stable=True):
    """PLT-01·04 공통 입력 기록. 노드 관측은 소유 Pod 바인딩 확인 사이에서 읽은 한 번의 값이어야 한다(`scope_stable`).

    `source_ended`는 노드 관측이 고른 프로세스를 모두 안정적으로 읽고, NVML과 컨테이너 안 열기 시험이 있고,
    바인딩이 앞뒤로 같을 때만 참이다. 자격 세대(credential_generation)는 Secret을 읽지 않으므로 비워 둔다.
    """
    spec = device_observer.node_spec(pod_uid=pod['pod_uid'], container_id=pod['container_id'], candidates=candidates)
    device_observer.decode_node(node, spec)
    if opened is not None:
        device_observer.decode_open(opened)
    receiver = hashlib.sha256(f"{pod['pod_uid']}/{container}".encode()).hexdigest()
    events, processes, codes = process_device_events(node, opened, candidates, receiver_sha256=receiver)
    if not scope_stable:
        codes = sorted(set(codes) | {'PROCESS_DEVICE_BINDING_CHANGED'})
    bindings = [{'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'].removeprefix('containerd://')[:128],
                 'restart_count': pod['restart_count'], 'worker_pid': p['nspid'][-1], 'start_ticks': p['start_ticks'], 'rank': 0}
                for p in processes]
    unstable = len(processes) != len(node['processes'])
    ended = (bool(processes) and not unstable and node['nvml']['available'] and opened is not None and scope_stable
             and 'PROCESS_DEVICE_ROWS_BOUND' not in codes)
    record = {'schema': contract.SCHEMA, 'kind': 'process-device-provider', 'producer': device_producer(),
              'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
              'session': session, 'bindings': bindings, 'events': events,
              'completeness': {'source_ended': ended, 'dropped': 0,
                               'diagnostic_codes': [c for c in codes if contract._ID.fullmatch(c)]}}
    return record, contract.validate(record, kind='process-device-provider', environment_ref=environment_ref,
                                     target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)
