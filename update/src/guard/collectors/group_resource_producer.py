"""LIF-11 failed-group-resources 생산자(개발 담당, B 의존 반환 7). 실패 전·후 관측과 trigger·정상 추론을 #196 기록으로 만든다.

생산자 신원은 관측기(`group_resource_observer.py`)와 이 변환의 소스 bytes 해시다. 등록은 GPU 확인 뒤에 한다.

자원 신원(`resource_sha256`)은 세대를 넘어 같은 자원이면 같은 값이 되도록 정한다.
- pid: (PID, 시작 ticks). 재시작 뒤 새 프로세스는 다른 자원이다.
- ipc_socket: unix socket 커널 inode.
- shm: `/dev/shm` 항목의 (dev, ino)와 이름 SHA-256. Pod 볼륨이면 재시작 뒤에도 같은 값으로 남는다.
- device: (PID, 시작 ticks, 장치 경로 SHA-256).
소유 그룹(`owner_group_sha256`)은 그 관측 세대의 (Pod UID, container ID, 재시작 횟수, PID 1 시작 ticks)다.

`released`는 실패 전에 있던 자원이 다음 관측에 없다는 사실이며, 반환 시각은 그 관측 시각을 상한으로 쓴다(정확 반환 시각이 아니다).
실패 전·후에 모두 있는 자원은 계약의 위반 후보(`resource_remaining_after_failure`)가 된다. 판정·기한은 소비자가 정책으로 정한다.
GPU 메모리 반환은 이 관측으로 확인하지 않는다(`GPU_MEMORY_RELEASE_UNOBSERVED`). 선정 FD가 사라진 것을 전역 반환으로 바꾸지 않는다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import gpu_common_inputs as contract
from . import group_resource_observer as observer

PRODUCER_NAME = 'guard-engine-failed-group-resources'
PRODUCER_VERSION = '1'


def producer():
    digest = hashlib.sha256()
    for path in (Path(observer.__file__), Path(__file__)):
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    return {'name': PRODUCER_NAME, 'version': PRODUCER_VERSION, 'source_sha256': digest.hexdigest()}


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _generation(pod, observed):
    container = pod['container_id'].removeprefix('containerd://')
    return {'owner_group_sha256': _sha(f"{pod['pod_uid']}|{container}|{pod['restart_count']}|{observed['generation']['pid1_start_ticks']}"),
            'id': f"c{container[:12]}-r{pod['restart_count']}-p{observed['generation']['pid1_start_ticks']}"}


def _resources(observed):
    """관측 → {resource_sha256: (종류, 소유 프로세스 번호 또는 None)}. 같은 자원을 여러 프로세스가 가지면 첫 소유자를 쓴다."""
    out = {}
    stable = [p for p in observed['processes'] if p['stable']]
    shm = {(s['dev'], s['ino']): s for s in observed['shm']}
    for index, p in enumerate(stable):
        out.setdefault(_sha(f"pid|{p['pid']}|{p['start_ticks']}"), ('pid', index))
        for inode in p['unix_inodes']:
            out.setdefault(_sha(f'unix|{inode}'), ('ipc_socket', index))
        for path in p['device_fds']:
            out.setdefault(_sha(f"device|{p['pid']}|{p['start_ticks']}|{path}"), ('device', index))
    owners = {}
    for index, p in enumerate(stable):
        for ref in p['shm_refs']:
            owners.setdefault(tuple(ref), index)
    for key, item in shm.items():
        out.setdefault(_sha(f"shm|{key[0]}:{key[1]}|{item['name_sha256']}"), ('shm', owners.get(key)))
    return out, stable


def record(before, after, *, pod_before, pod_after, trigger=None, normal_after=None, environment_ref, target_sha256,
           prepared_sha256, session, scope_stable=True):
    """LIF-11 공통 입력 기록.

    trigger: 실패를 일으킨 lab 동작의 {'at_ns', 'pid', 'start_ticks', 'ack'} (없으면 None — 조건부 기한을 잴 수 없다).
    normal_after: 다음 세대 정상 추론의 {'at_ns', 'ok'} (없으면 None — 다음 정상 기동 미확인).
    """
    observer.decode(before); observer.decode(after)
    codes = set(before['codes']) | set(after['codes']) | {'GPU_MEMORY_RELEASE_UNOBSERVED'}
    first, last = _generation(pod_before, before), _generation(pod_after, after)
    changed = first['owner_group_sha256'] != last['owner_group_sha256']
    if not changed:
        codes.add('NEXT_GENERATION_NOT_STARTED')
    if trigger is None:
        codes.add('FAILURE_TRIGGER_UNOBSERVED')
    elif trigger.get('ack') is not True:
        codes.add('FAILURE_TRIGGER_UNACKNOWLEDGED')
    if normal_after is None or normal_after.get('ok') is not True:
        codes.add('NEXT_NORMAL_INFERENCE_UNVERIFIED')
    old, old_procs = _resources(before)
    new, new_procs = _resources(after)
    if len(old_procs) != len(before['processes']) or len(new_procs) != len(after['processes']):
        codes.add('PROCESS_IDENTITY_CHANGED')
    if not scope_stable:
        codes.add('GROUP_RESOURCE_BINDING_CHANGED')
    bindings = []
    for pod, procs in ((pod_before, old_procs), (pod_after, new_procs)):
        for p in procs:
            item = {'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'].removeprefix('containerd://')[:128],
                    'restart_count': pod['restart_count'], 'worker_pid': p['pid'], 'start_ticks': p['start_ticks'], 'rank': 0}
            if item not in bindings:  # 재시작이 없으면 앞뒤가 같은 프로세스다
                bindings.append(item)
    if not bindings:
        raise ValueError('GROUP_RESOURCE_NO_STABLE_PROCESS')
    if len(bindings) > 64:
        raise ValueError('GROUP_RESOURCE_BINDING_BOUND')

    if any(index is None for kind, index in list(old.values()) + list(new.values()) if kind == 'shm'):
        codes.add('SHM_WITHOUT_PROCESS_REFERENCE')  # 어느 프로세스도 잡지 않은 공유 메모리. 행 바인딩은 첫 프로세스이며 소유 주장이 아니다

    def binding_of(side, procs, index):
        p = procs[index if index is not None else 0]
        key = {'pod_uid': (pod_before if side == 'before' else pod_after)['pod_uid'], 'worker_pid': p['pid'], 'start_ticks': p['start_ticks']}
        return next(i for i, b in enumerate(bindings) if all(b[k] == v for k, v in key.items()))

    events = []

    def row(event, binding, at_ns, resource, resource_sha256, owner, next_generation=None):
        events.append({'seq': len(events), 'event': event, 'binding': binding, 'at_ms': at_ns // 10**6, 'resource': resource,
                       'resource_sha256': resource_sha256, 'owner_group_sha256': owner, 'next_generation': next_generation})

    for sha, (kind, index) in sorted(old.items()):
        row('inventory_before', binding_of('before', old_procs, index), before['realtime_ns'], kind, sha, first['owner_group_sha256'])
    if trigger is not None:
        target = next((i for i, p in enumerate(old_procs) if p['pid'] == trigger['pid'] and p['start_ticks'] == trigger['start_ticks']), None)
        if target is None:
            codes.add('FAILURE_TRIGGER_TARGET_NOT_IN_GROUP')
        else:
            row('failure_trigger', binding_of('before', old_procs, target), trigger['at_ns'], 'pid',
                _sha(f"pid|{trigger['pid']}|{trigger['start_ticks']}"), first['owner_group_sha256'])
    for sha, (kind, index) in sorted(new.items()):
        row('inventory_after', binding_of('after', new_procs, index), after['realtime_ns'], kind, sha, last['owner_group_sha256'],
            last['id'] if changed else None)
    for sha, (kind, index) in sorted(old.items()):
        if sha not in new:
            row('released', binding_of('before', old_procs, index), after['realtime_ns'], kind, sha, first['owner_group_sha256'],
                last['id'] if changed else None)
    if normal_after is not None and normal_after.get('ok') is True:
        api = next((i for i, p in enumerate(new_procs) if p['role'] == 'api_server'), None)
        if api is None:
            codes.add('NEXT_API_SERVER_UNOBSERVED')
        else:
            p = new_procs[api]
            row('normal_inference', binding_of('after', new_procs, api), normal_after['at_ns'], 'pid',
                _sha(f"pid|{p['pid']}|{p['start_ticks']}"), last['owner_group_sha256'], last['id'] if changed else None)
    if len(events) > contract.MAX_EVENTS:
        codes.add('GROUP_RESOURCE_ROWS_BOUND'); events = events[:contract.MAX_EVENTS]
    blocking = {'PROCESS_SET_BOUND', 'PROCESS_READ_FAILED', 'SHM_DIRECTORY_UNAVAILABLE', 'PROCESS_IDENTITY_CHANGED',
                'GROUP_RESOURCE_BINDING_CHANGED', 'GROUP_RESOURCE_ROWS_BOUND'}
    value = {'schema': contract.SCHEMA, 'kind': 'failed-group-resources', 'producer': producer(),
             'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
             'session': session, 'bindings': bindings, 'events': events,
             'completeness': {'source_ended': not (codes & blocking), 'dropped': 0,
                              'diagnostic_codes': sorted(c for c in codes if contract._ID.fullmatch(c))}}
    result = contract.validate(value, kind='failed-group-resources', environment_ref=environment_ref,
                               target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)
    return value, result


def lineage(value):
    """기록 → 실패 trigger·반환·잔존·다음 세대 정상 추론의 사실 요약. 기한 판정은 하지 않는다."""
    events = value['events']
    trigger = next((r['at_ms'] for r in events if r['event'] == 'failure_trigger'), None)
    released = [r for r in events if r['event'] == 'released']
    before = {r['resource_sha256']: r['resource'] for r in events if r['event'] == 'inventory_before'}
    after = {r['resource_sha256'] for r in events if r['event'] == 'inventory_after'}
    remaining = {}
    for sha, kind in before.items():
        if sha in after:
            remaining[kind] = remaining.get(kind, 0) + 1
    normal = next((r for r in events if r['event'] == 'normal_inference'), None)
    nexts = {r['next_generation'] for r in events if r['event'] == 'inventory_after'}
    return {'failure_trigger_at_ms': trigger,
            'released_by_kind': {k: sum(1 for r in released if r['resource'] == k) for k in sorted({r['resource'] for r in released})},
            'released_upper_bound_ms': max((r['at_ms'] for r in released), default=None),
            'remaining_by_kind': dict(sorted(remaining.items())),
            'next_generation': next(iter(nexts)) if len(nexts) == 1 else None,
            'next_normal_inference_at_ms': normal['at_ms'] if normal else None,
            'gpu_memory_release_observed': False}
