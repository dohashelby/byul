"""PLT-02 ipc-ownership 생산자(개발 담당, B 의존 반환 6). 관측기 출력·Pod 신원을 #196 계약 기록으로 만든다.

생산자 신원은 관측기(`ipc_ownership_observer.py`)와 이 변환의 소스 bytes 해시다. 다른 생산자 모듈과 분리해
이미 등록한 생산자의 SHA를 바꾸지 않는다. 등록(`SUPPORTED_PRODUCERS`)은 GPU 확인 뒤에 한다.

B 소비자(`kubernetes_unix_ipc`)가 쓸 수 있도록 `summarize`가 역할별 listener와 연결 방향(연결한 역할 → listener 역할)을
낸다. 이 요약은 관측 사실이며 허용 정책 판정이 아니다. 유틸리티 프로세스를 worker로 바꿔 부르지 않는다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import gpu_common_inputs as contract
from . import ipc_ownership_observer as observer

PRODUCER_NAME = 'guard-engine-ipc-ownership'
PRODUCER_VERSION = '1'
TCP_LISTEN, TCP_ESTABLISHED = 10, 1


def producer():
    digest = hashlib.sha256()
    for path in (Path(observer.__file__), Path(__file__)):
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    return {'name': PRODUCER_NAME, 'version': PRODUCER_VERSION, 'source_sha256': digest.hexdigest()}


def _state(item):
    if item['state'] == TCP_LISTEN:
        return 'listen'
    if item['state'] == TCP_ESTABLISHED:
        return 'accepted' if item['path_sha256'] is not None else 'connected'
    return 'unconnected'


def events_from(observed):
    """관측 → (행, 안정 프로세스 목록, 진단 코드). 불안정 프로세스는 바인딩하지 않고 진단으로 남긴다."""
    codes = set(observed['codes'])
    if not observed['sock_diag_complete']:
        codes.add('SOCK_DIAG_INCOMPLETE')
    processes = [p for p in observed['processes'] if p['stable']]
    if len(processes) != len(observed['processes']):
        codes.add('PROCESS_IDENTITY_CHANGED')
    owner = {}
    for index, process in enumerate(processes):
        for sock in process['sockets']:
            owner.setdefault(sock['inode'], []).append(index)
    events = []

    def row(event, binding, **values):
        base = {'role': processes[binding]['role'], 'uid': None, 'net_ns': None, 'socket_inode': None, 'fd': None,
                'state': 'none', 'path_sha256': None, 'path_file_sha256': None, 'peer_socket_inode': None, 'peer_binding': None}
        events.append({'seq': len(events), 'event': event, 'binding': binding, **base, **values})

    for index, process in enumerate(processes):
        row('process', index, uid=process['uid_effective'], net_ns=process['net_ns'])
    for index, process in enumerate(processes):
        for sock in process['sockets']:
            item = observed['unix'].get(str(sock['inode']))
            if item is None:
                continue  # unix가 아닌 socket(TCP 등)은 이 종류의 범위 밖이다.
            peer = item['peer']
            holders = owner.get(peer, []) if peer is not None else []
            if peer is not None and not holders:
                codes.add('UNIX_PEER_UNRESOLVED')
            vfs = item['vfs']
            row('unix_socket', index, socket_inode=sock['inode'], fd=sock['fd'], state=_state(item),
                path_sha256=item['path_sha256'],
                path_file_sha256=hashlib.sha256(f'{vfs[0]}:{vfs[1]}'.encode()).hexdigest() if vfs else None,
                peer_socket_inode=peer, peer_binding=holders[0] if len(holders) == 1 else None)
            if len(holders) > 1:
                codes.add('UNIX_PEER_SHARED_BY_PROCESSES')
    if len(events) > contract.MAX_EVENTS:
        codes.add('IPC_OWNERSHIP_ROWS_BOUND'); events = events[:contract.MAX_EVENTS]
    return events, processes, sorted(codes)


def record(observed, *, pod, environment_ref, target_sha256, prepared_sha256, session, scope_stable=True):
    """PLT-02 공통 입력 기록. `scope_stable`은 관측 앞뒤 소유 Pod 바인딩이 같았는지다.

    `source_ended`는 관측한 프로세스가 모두 안정적이고, sock_diag가 끝까지 왔고, 상한·읽기 실패가 없고, 바인딩이 같을 때만 참이다.
    """
    observer.decode(observed)
    events, processes, codes = events_from(observed)
    if not processes:
        raise ValueError('IPC_OWNERSHIP_NO_STABLE_PROCESS')  # 바인딩할 실제 프로세스가 없으면 기록을 만들지 않는다
    if not scope_stable:
        codes = sorted(set(codes) | {'IPC_OWNERSHIP_BINDING_CHANGED'})
    bindings = [{'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'].removeprefix('containerd://')[:128],
                 'restart_count': pod['restart_count'], 'worker_pid': p['pid'], 'start_ticks': p['start_ticks'],
                 'rank': p['rank'] if p['rank'] is not None else 0} for p in processes]
    blocking = {'PROCESS_SET_BOUND', 'SOCKET_SET_BOUND', 'SOCK_DIAG_BOUND', 'SOCK_DIAG_ERROR', 'SOCK_DIAG_MALFORMED',
                'SOCK_DIAG_UNAVAILABLE', 'SOCK_DIAG_INCOMPLETE', 'PROCESS_READ_FAILED', 'PROCESS_IDENTITY_CHANGED',
                'IPC_OWNERSHIP_ROWS_BOUND', 'IPC_OWNERSHIP_BINDING_CHANGED'}
    ended = not (set(codes) & blocking)
    value = {'schema': contract.SCHEMA, 'kind': 'ipc-ownership', 'producer': producer(),
             'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
             'session': session, 'bindings': bindings,
             'events': events,
             'completeness': {'source_ended': ended, 'dropped': 0,
                              'diagnostic_codes': [c for c in codes if contract._ID.fullmatch(c)]}}
    return value, contract.validate(value, kind='ipc-ownership', environment_ref=environment_ref,
                                    target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)


def summarize(value):
    """기록 → 역할별 listener 수, 연결 방향(연결한 역할 → accept한 역할) 집계, 상대 미해결 수. 정책 판정이 아니다."""
    events = value['events']
    roles = {r['binding']: r['role'] for r in events if r['event'] == 'process'}
    sockets = [r for r in events if r['event'] == 'unix_socket']
    by_inode = {r['socket_inode']: r for r in sockets}
    listeners, edges, unresolved = {}, {}, 0
    for r in sockets:
        if r['state'] == 'listen':
            listeners[roles[r['binding']]] = listeners.get(roles[r['binding']], 0) + 1
        elif r['state'] == 'accepted':
            client = by_inode.get(r['peer_socket_inode'])
            if client is None or r['peer_binding'] is None:
                unresolved += 1
                continue
            key = f"{roles[client['binding']]}->{roles[r['binding']]}"
            edges[key] = edges.get(key, 0) + 1
        elif r['state'] == 'connected' and r['peer_binding'] is None:
            unresolved += 1
    return {'roles': sorted(set(roles.values())), 'listeners': dict(sorted(listeners.items())),
            'connections': dict(sorted(edges.items())), 'unresolved_peers': unresolved,
            'observation': 'unix socket ownership and peers only; message content, consumption and policy are not observed'}
