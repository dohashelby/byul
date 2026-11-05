"""SUP-03 공급 루트의 마운트가 소유 엔진 Pod 전용인지 Kubernetes API로 확인한다(개발 담당, 2단계 P2).

inotify는 연 프로세스를 알려 주지 않는다. 대신 공급 루트를 담은 볼륨을 이 엔진 Pod만 마운트한다면,
그 파일의 열기 사건은 이 Pod 안에서 일어난 것으로 범위를 좁힐 수 있다. 이 모듈은 그 전제를 확인한다.

- hostPath: 같은 노드에서 다른 Pod(또는 같은 Pod의 다른 컨테이너)가 겹치는 호스트 경로를 마운트하지 않는다.
- PVC: 다른 Pod가 같은 claim을 쓰지 않는다. 같은 Pod의 다른 컨테이너도 같은 볼륨을 마운트하지 않는다.
- emptyDir·configMap·secret·projected·downwardAPI: Pod 안 사본이다. 같은 Pod의 다른 컨테이너만 본다.
- 그 밖의 볼륨 종류와 마운트를 찾지 못한 루트는 확인 불가다.
노드의 Kubernetes 밖 프로세스(호스트 데몬 등)는 API로 보이지 않는다. 이 확인의 범위 밖이며 결과에 그렇게 적는다.
"""
from __future__ import annotations

import posixpath

POD_LOCAL = frozenset({'emptyDir', 'configMap', 'secret', 'projected', 'downwardAPI'})


def _within(path, prefix):
    path, prefix = posixpath.normpath(path), posixpath.normpath(prefix)
    return path == prefix or path.startswith(prefix.rstrip('/') + '/')


def _overlap(a, b):
    return _within(a, b) or _within(b, a)


def _mount_for(container, root):
    best = None
    for mount in container.get('volumeMounts') or []:
        path = mount.get('mountPath')
        if type(path) is str and _within(root, path) and (best is None or len(path) > len(best['mountPath'])):
            best = mount
    return best


def _source(volume):
    for kind in ('hostPath', 'persistentVolumeClaim', *sorted(POD_LOCAL)):
        if kind in volume:
            return kind, volume[kind]
    return next(iter(k for k in volume if k != 'name'), None), None


def _host_path(volume_source, mount, root):
    base = posixpath.normpath(volume_source.get('path') or '')
    sub = mount.get('subPath') or ''
    inside = posixpath.relpath(posixpath.normpath(root), posixpath.normpath(mount['mountPath']))
    parts = [base] + ([sub] if sub else []) + ([] if inside == '.' else [inside])
    return posixpath.normpath(posixpath.join(*parts))


def exclusivity(engine_pod, all_pods, container_name, roots):
    """루트마다 전용 여부. 원 경로·Pod 이름 대신 개수와 종류만 돌려준다."""
    spec = engine_pod['spec']; uid = engine_pod['metadata']['uid']; node = spec.get('nodeName')
    namespace = engine_pod['metadata']['namespace']
    volumes = {v.get('name'): v for v in spec.get('volumes') or []}
    container = next((c for c in spec.get('containers') or [] if c.get('name') == container_name), None)
    results = []
    for root in roots:
        row = {'root': root, 'source': None, 'exclusive': False, 'same_pod_sharers': 0, 'other_pod_sharers': 0,
               'reason': None, 'node_host_processes_in_scope': False}
        mount = _mount_for(container, root) if container else None
        volume = volumes.get(mount.get('name')) if mount else None
        if volume is None:
            row['reason'] = 'mount_not_found'; results.append(row); continue
        kind, source = _source(volume); row['source'] = kind
        # 같은 Pod의 다른 컨테이너가 같은 볼륨을 마운트하면 그 컨테이너의 읽기도 섞인다(완료된 init은 감시 시작 전이다).
        row['same_pod_sharers'] = sum(1 for c in spec.get('containers', []) if c.get('name') != container_name
                                      and any(m.get('name') == volume['name'] for m in c.get('volumeMounts') or []))
        if kind in POD_LOCAL:
            row['exclusive'] = row['same_pod_sharers'] == 0
            row['reason'] = 'pod_local' if row['exclusive'] else 'shared_in_pod'
        elif kind == 'hostPath':
            host = _host_path(source, mount, root)
            for pod in all_pods:
                if pod['metadata'].get('uid') == uid or pod.get('spec', {}).get('nodeName') != node:
                    continue
                if pod.get('status', {}).get('phase') in ('Succeeded', 'Failed'):
                    continue
                for other in pod['spec'].get('volumes') or []:
                    path = (other.get('hostPath') or {}).get('path')
                    if type(path) is str and _overlap(path, host):
                        row['other_pod_sharers'] += 1; break
            row['exclusive'] = row['same_pod_sharers'] == 0 and row['other_pod_sharers'] == 0
            row['reason'] = 'exclusive_host_path' if row['exclusive'] else 'shared_host_path'
        elif kind == 'persistentVolumeClaim':
            claim = (source or {}).get('claimName')
            for pod in all_pods:
                if pod['metadata'].get('uid') == uid or pod['metadata'].get('namespace') != namespace:
                    continue
                if pod.get('status', {}).get('phase') in ('Succeeded', 'Failed'):
                    continue
                if any((v.get('persistentVolumeClaim') or {}).get('claimName') == claim for v in pod['spec'].get('volumes') or []):
                    row['other_pod_sharers'] += 1
            row['exclusive'] = row['same_pod_sharers'] == 0 and row['other_pod_sharers'] == 0
            row['reason'] = 'exclusive_claim' if row['exclusive'] else 'shared_claim'
        else:
            row['reason'] = 'volume_kind_unsupported'
        results.append(row)
    return results
