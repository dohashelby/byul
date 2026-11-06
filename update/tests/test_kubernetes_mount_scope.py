"""SUP-03 공급 루트 마운트 전용성: hostPath·PVC·Pod 안 볼륨, 같은 Pod 다른 컨테이너, 확인 불가."""
from copy import deepcopy

from guard.collectors import gpu_input_producers as producers
from guard.runtime.kubernetes_mount_scope import exclusivity

ROOTS = ['/models/opt-125m', '/guard-assets/chat/t.jinja']


def engine(**volumes):
    vols = [{'name': 'models', 'hostPath': {'path': '/srv/models'}},
            {'name': 'tpl', 'hostPath': {'path': '/srv/models/guard-chat/t.jinja', 'type': 'File'}}]
    mounts = [{'name': 'models', 'mountPath': '/models'}, {'name': 'tpl', 'mountPath': '/guard-assets/chat/t.jinja'}]
    pod = {'metadata': {'uid': 'u-engine', 'namespace': 'lab'},
           'spec': {'nodeName': 'n1', 'volumes': vols, 'containers': [{'name': 'engine', 'volumeMounts': mounts}]}, 'status': {'phase': 'Running'}}
    pod['spec']['volumes'] = [v if v['name'] not in volumes else {'name': v['name'], **volumes[v['name']]} for v in vols]
    return pod


def other(path, node='n1', phase='Running'):
    return {'metadata': {'uid': 'u-' + path, 'namespace': 'lab'}, 'spec': {'nodeName': node, 'volumes': [{'name': 'x', 'hostPath': {'path': path}}]},
            'status': {'phase': phase}}


def test_host_path_is_exclusive_only_without_overlapping_pods_on_the_same_node():
    pod = engine()
    rows = exclusivity(pod, [pod, other('/srv/models/other-model'), other('/srv/models', node='n2'), other('/srv', phase='Succeeded')], 'engine', ROOTS)
    assert [r['exclusive'] for r in rows] == [True, True]
    shared = exclusivity(pod, [pod, other('/srv/models')], 'engine', ROOTS)
    assert [(r['exclusive'], r['reason'], r['other_pod_sharers']) for r in shared] == [(False, 'shared_host_path', 1), (False, 'shared_host_path', 1)]
    child = exclusivity(pod, [pod, other('/srv/models/opt-125m/sub')], 'engine', ROOTS[:1])
    assert child[0]['exclusive'] is False


def test_same_pod_sidecar_pvc_pod_local_and_unknown_kinds():
    pod = engine()
    pod['spec']['containers'].append({'name': 'sidecar', 'volumeMounts': [{'name': 'models', 'mountPath': '/m'}]})
    assert exclusivity(pod, [pod], 'engine', ROOTS[:1])[0]['same_pod_sharers'] == 1
    pvc = engine(models={'persistentVolumeClaim': {'claimName': 'c1'}})
    peer = {'metadata': {'uid': 'u2', 'namespace': 'lab'}, 'spec': {'volumes': [{'name': 'y', 'persistentVolumeClaim': {'claimName': 'c1'}}]}, 'status': {'phase': 'Running'}}
    assert exclusivity(pvc, [pvc], 'engine', ROOTS[:1])[0]['reason'] == 'exclusive_claim'
    assert exclusivity(pvc, [pvc, peer], 'engine', ROOTS[:1])[0]['reason'] == 'shared_claim'
    local = engine(models={'emptyDir': {}})
    assert exclusivity(local, [local], 'engine', ROOTS[:1])[0]['reason'] == 'pod_local'
    nfs = engine(models={'nfs': {'server': 's', 'path': '/x'}})
    assert exclusivity(nfs, [nfs], 'engine', ROOTS[:1])[0]['reason'] == 'volume_kind_unsupported'
    assert exclusivity(pod, [pod], 'engine', ['/elsewhere'])[0]['reason'] == 'mount_not_found'


def test_record_drops_the_attribution_gap_only_when_both_checks_are_exclusive():
    rows = [{'schema': 'guard-vllm-supply-read-callback/1', 'phase': 'INOTIFY', 'root': 0, 'relative': 'vocab.json', 'opened_ns': 1}]
    processes = [{'pid': 1, 'start_ticks': 1, 'events': rows}]
    hashes = [{'root': 0, 'relative': 'vocab.json', 'sha256': 'a' * 64, 'size': 1, 'state': 'hashed'}]
    from tests.test_gpu_input_producers import POD, SCOPE
    ok = [{'exclusive': True, 'reason': 'exclusive_host_path'}]
    record, result = producers.supply_read_record(processes, hashes, pod=POD, **SCOPE, mount_before=ok, mount_after=ok)
    assert record['events'][0]['state'] == 'inotify_pod_exclusive' and not any('MOUNT' in g or 'UNATTRIBUTED' in g for g in result['gaps'])
    shared = [{'exclusive': False, 'reason': 'shared_host_path'}]
    _, result = producers.supply_read_record(processes, hashes, pod=POD, **SCOPE, mount_before=ok, mount_after=shared)
    assert {'GPU_INPUT_PRODUCER_DIAGNOSTIC:SUPPLY_MOUNT_SHARED', 'GPU_INPUT_PRODUCER_DIAGNOSTIC:SUPPLY_NATIVE_READ_PROCESS_UNATTRIBUTED'} <= set(result['gaps'])
    _, result = producers.supply_read_record(processes, hashes, pod=POD, **SCOPE)
    assert 'GPU_INPUT_PRODUCER_DIAGNOSTIC:SUPPLY_MOUNT_EXCLUSIVITY_UNVERIFIED' in result['gaps']
