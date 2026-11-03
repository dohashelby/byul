"""LIF11 must observe a new Pod generation before claiming next start."""
import json
import hashlib
from copy import deepcopy
import pytest

from guard.executors.lab_activation import Lab, _worker_cleanup, _restore_sensor_session


def pod(uid, sensor_root, *, foreign=False, explicit_port=None,
        mount_missing=False, shadow=False, unused_hostpath=False,
        sensor_mount_option=None):
    mounts = [] if mount_missing else [{'name': 'other' if unused_hostpath else 'sensor',
        'mountPath': '/guard-lifecycle', 'readOnly': True}]
    if sensor_mount_option and mounts:
        mounts[0][sensor_mount_option] = 'alternate'
    mounts.append({'name': 'data', 'mountPath': '/guard-lifecycle/data', 'readOnly': False})
    if shadow:
        mounts.append({'name': 'other', 'mountPath': '/guard-lifecycle/settings.json', 'readOnly': True})
    return {'metadata': {'uid': uid, 'name': 'engine', 'ownerReferences': [{'kind': 'ReplicaSet',
        'name': 'foreign-rs' if foreign else 'owned-rs',
        'uid': 'foreign-rs-uid' if foreign else 'owned-rs-uid',
        'controller': True}], 'labels': {'guard.run': 'gpu-b-20261102',
            'byul.lab/generation': 'restore' if uid == 'new' else 'normal'}},
        'spec': {'nodeName': 'node-1', 'containers': [{'name': 'engine', 'ports': [{'containerPort': 8000,
            'name': 'http', 'protocol': 'TCP'}],
            'command': [] if explicit_port is None else ['vllm', '--port', str(explicit_port)],
            'volumeMounts': mounts}],
            'volumes': [{'name': 'sensor',
                'hostPath': {'path': str(sensor_root)}}]},
        'status': {'podIP': '10.0.0.2',
        'conditions': [{'type': 'Ready', 'status': 'True'}],
        'containerStatuses': [{'name': 'engine', 'ready': True,
            'containerID': 'containerd://' + uid, 'restartCount': 0,
            'state': {'running': {'startedAt': '2026-11-03T00:00:00Z'}}}]}}


class FakeLab:
    cfg = {'deadline_ms': 1000, 'good_manifest': '/restore/new-native13.json',
        'service_url': 'http://vllm.byul-gpu-b-20261102.svc:80',
        'namespace': 'byul-gpu-b-20261102', 'deployment': 'vllm', 'container': 'engine'}

    def __init__(self, root, replaced, *, death=True, foreign=False, target_port=8000,
                 good_manifest=True, explicit_port=None, empty_old_list=False,
                 foreign_manifest=False, reused_session=False, mount_missing=False,
                 shadow=False, unused_hostpath=False, sensor_mount_option=None,
                 sensor_mount_phase=None):
        self.replaced = replaced
        self.death = death
        self.foreign = foreign
        self.target_port = target_port
        self.explicit_port = explicit_port
        self.empty_old_list = empty_old_list
        self.foreign_manifest = foreign_manifest
        self.old_list_reads = 0
        self.mount_missing = mount_missing
        self.shadow = shadow
        self.unused_hostpath = unused_hostpath
        self.sensor_mount_option = sensor_mount_option
        self.sensor_mount_phase = sensor_mount_phase
        self.root = root
        self.old_sensor = root / 'old'
        self.new_sensor = root / 'new'
        self.old_sensor.mkdir()
        self.new_sensor.mkdir()
        (self.old_sensor / 'settings.json').write_text(json.dumps({'session': 'a' * 32}))
        (self.new_sensor / 'settings.json').write_text(json.dumps({'session': 'a' * 32 if reused_session else 'b' * 32}))
        manifest = root / 'restore.json'
        desired_mount = {'name': 'sensor', 'mountPath': '/guard-lifecycle', 'readOnly': True}
        if sensor_mount_phase == 'desired':
            desired_mount[sensor_mount_option] = 'alternate'
        manifest.write_text(json.dumps({'kind': 'Deployment',
            'metadata': {'name': 'other' if foreign_manifest else 'vllm',
                'namespace': self.cfg['namespace']},
            'spec': {'template': {'metadata': {'labels': {'guard.run': 'gpu-b-20261102',
                'byul.lab/generation': 'restore'}},
                'spec': {'containers': [{'name': 'engine', 'volumeMounts': [
                    desired_mount,
                    {'name': 'data', 'mountPath': '/guard-lifecycle/data', 'readOnly': False}]}],
                    'volumes': [{'name': 'sensor',
                    'hostPath': {'path': str(self.new_sensor)}}]}}}}))
        self.cfg = dict(type(self).cfg)
        self.cfg['good_manifest'] = str(manifest)
        self.cfg['good_manifest_sha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        if not good_manifest:
            self.cfg.pop('good_manifest')
        self.t = 0.0
        self.missing = []
        self.violations = []
        self.steps = []
        self.applied = []
        self.mutated = True

    def miss(self, code):
        self.missing.append(code)

    def violate(self, code):
        self.violations.append(code)

    def note(self, step, **facts):
        self.steps.append((step, facts))

    def normal_request(self, label, *, url=None):
        if label == 'normal-after-restart':
            assert url == 'http://10.0.0.2:8000'
        return {'outcome': 200, 'complete': True}

    def selected_worker(self):
        return {'pod': 'engine', 'pod_uid': 'old', 'pid': 42, 'start_ticks': 123}

    def _pods(self):
        self.old_list_reads += 1
        if self.empty_old_list and self.old_list_reads == 1:
            return []
        replaced = self.applied and self.replaced
        return [pod('new' if replaced else 'old',
                    self.new_sensor if replaced else self.old_sensor,
                    foreign=self.foreign if self.applied else False,
                    explicit_port=self.explicit_port,
                    mount_missing=self.mount_missing if replaced else False,
                    shadow=self.shadow if replaced else False,
                    unused_hostpath=self.unused_hostpath if replaced else False,
                    sensor_mount_option=(self.sensor_mount_option
                        if self.sensor_mount_phase == ('new' if replaced else 'old') else None))]

    def _exec(self, _source, *args, pod, pod_uid, isolated=False):
        assert isolated is True
        assert pod == 'engine' and pod_uid in ('old', 'new')
        assert args[0] in ('/guard-lifecycle/settings.json', '/guard-lifecycle/engine-callbacks/settings.json')
        root = self.new_sensor if pod_uid == 'new' else self.old_sensor
        return {'session': json.loads((root / 'settings.json').read_text())['session']}

    def _kubectl(self, *args):
        if args[:2] == ('get', 'deployment'):
            value = {'metadata': {'uid': 'deployment-uid',
                'namespace': self.cfg['namespace']}}
        elif args[:2] == ('get', 'replicaset'):
            foreign = args[2] == 'foreign-rs'
            value = {'metadata': {'namespace': self.cfg['namespace'],
                'uid': 'foreign-rs-uid' if foreign else 'owned-rs-uid',
                'ownerReferences': [{'kind': 'Deployment', 'name': 'other' if foreign else 'vllm',
                    'uid': 'other-deployment-uid' if foreign else 'deployment-uid',
                    'controller': True}]}}
        elif args[:2] == ('get', 'service'):
            value = {'metadata': {'namespace': self.cfg['namespace']},
                'spec': {'ports': [{'port': 80, 'targetPort': self.target_port,
                    'protocol': 'TCP'}]}}
        else:
            raise AssertionError(args)
        return json.dumps(value).encode()

    def census(self, *_):
        self.census_calls = getattr(self, 'census_calls', 0) + 1
        processes = [{'pid': 42, 'start_ticks': 123}] if self.census_calls == 1 or not self.death else []
        return {'processes': processes, 'shm_entries': 0,
                'process_scope_complete': True, 'process_scope_errors': [],
                'process_count_before': len(processes), 'process_count_after': len(processes),
                'shm_names': []}

    def engine_container_identity(self, value):
        if value is None:
            return None
        status = value['status']['containerStatuses'][0]
        return ('running', status['containerID'], status['restartCount'],
                status['state']['running']['startedAt'])

    def signal_worker(self, *_):
        self.mutated = True

    def wait(self, interval):
        self.t += interval

    def clock(self):
        return self.t

    def apply(self, name):
        self.applied.append(name)

    def rollout(self):
        self.applied.append('rollout')


def restore_from_snapshots(instance, old, new):
    # 두 번의 실제 대상 조회에 대응하는 원본 Pod snapshots를 순서대로 제공한다.
    snapshots = iter(([deepcopy(old)], [deepcopy(new)]))
    original = instance._pods
    instance._pods = lambda: next(snapshots)
    try:
        return _restore_sensor_session(instance, old, new)
    finally:
        instance._pods = original


def test_lif11_old_ready_pod_does_not_count_as_next_start(tmp_path):
    lab = FakeLab(tmp_path, replaced=False)
    _worker_cleanup(lab, {})
    assert lab.applied == ['good_manifest']
    assert 'NEXT_START_NEW_GENERATION_UNOBSERVED' in lab.missing
    assert lab.violations == []
    assert lab.mutated is True


def test_lif11_new_ready_pod_uses_fresh_restore_manifest(tmp_path):
    lab = FakeLab(tmp_path, replaced=True)
    _worker_cleanup(lab, {})
    assert lab.applied == ['good_manifest']
    assert lab.steps[-2] == ('worker-restart-generation',
        {'old_pod_uids': ['old'], 'new_ready_pod_uids': ['new'],
         'selected_worker_death_observed': True})
    assert lab.steps[-1][0] == 'worker-next-start-direct'
    assert lab.steps[-1][1]['engine_identity_stable'] is True
    assert lab.steps[-1][1]['restore_sensor_session'] == 'b' * 32
    assert 'NEXT_START_NEW_GENERATION_UNOBSERVED' not in lab.missing
    assert 'IPC_OWNERSHIP_AND_GPU_RESOURCE_RELEASE_UNOBSERVED' in lab.missing
    assert 'NEXT_REQUEST_ENGINE_GENERATION_CHANGED' not in lab.missing
    assert lab.mutated is False


def test_lif11_warm_ready_without_observed_death_stays_incomplete(tmp_path):
    lab = FakeLab(tmp_path, replaced=True, death=False)
    _worker_cleanup(lab, {})
    assert 'SELECTED_WORKER_DEATH_UNOBSERVED' in lab.missing
    assert lab.steps[-1][1]['normal_response'] is True
    assert lab.mutated is True


def test_lif11_foreign_same_selector_pod_does_not_count(tmp_path):
    lab = FakeLab(tmp_path, replaced=True, foreign=True)
    _worker_cleanup(lab, {})
    assert 'NEXT_START_NEW_GENERATION_UNOBSERVED' in lab.missing
    assert not any(step == 'worker-next-start-direct' for step, _ in lab.steps)


def test_lif11_service_port_80_maps_only_verified_target_port(tmp_path):
    lab = FakeLab(tmp_path, replaced=True, target_port=9000)
    _worker_cleanup(lab, {})
    assert 'NEXT_START_DIRECT_POD_ADDRESS_UNAVAILABLE' in lab.missing
    assert 'NEXT_REQUEST_FAILED_AFTER_RESTART' not in lab.violations
    assert lab.mutated is True


def test_lif11_without_good_manifest_cannot_claim_native_session(tmp_path):
    lab = FakeLab(tmp_path, replaced=True, good_manifest=False)
    _worker_cleanup(lab, {})
    assert lab.applied == ['rollout']
    assert 'RESTORE_NATIVE_SESSION_UNVERIFIED' in lab.missing


def test_lif11_explicit_port_conflict_is_missing(tmp_path):
    lab = FakeLab(tmp_path, replaced=True, explicit_port=9000)
    _worker_cleanup(lab, {})
    assert 'NEXT_START_DIRECT_POD_ADDRESS_UNAVAILABLE' in lab.missing
    assert 'NEXT_REQUEST_FAILED_AFTER_RESTART' not in lab.violations


def test_lif11_named_target_port_with_explicit_conflict_is_missing(tmp_path):
    lab = FakeLab(tmp_path, replaced=True, target_port='http', explicit_port=9000)
    _worker_cleanup(lab, {})
    assert 'NEXT_START_DIRECT_POD_ADDRESS_UNAVAILABLE' in lab.missing
    assert lab.violations == []


def test_lif11_foreign_restore_manifest_and_reused_session_are_not_consumed(tmp_path):
    lab = FakeLab(tmp_path, replaced=True, foreign_manifest=True)
    _worker_cleanup(lab, {})
    assert 'RESTORE_SENSOR_SESSION_UNVERIFIED' in lab.missing
    assert lab.mutated is True


def test_lif11_reused_sensor_session_is_not_fresh(tmp_path):
    lab = FakeLab(tmp_path, replaced=True, reused_session=True)
    _worker_cleanup(lab, {})
    assert 'RESTORE_SENSOR_SESSION_UNVERIFIED' in lab.missing
    assert lab.mutated is True


@pytest.mark.parametrize('failure', ('mount_missing', 'shadow', 'unused_hostpath'))
def test_lif11_unbound_or_shadowed_sensor_mount_is_missing(tmp_path, failure):
    lab = FakeLab(tmp_path, replaced=True, **{failure: True})
    _worker_cleanup(lab, {})
    assert 'RESTORE_SENSOR_SESSION_UNVERIFIED' in lab.missing
    assert lab.mutated is True


@pytest.mark.parametrize('option', ('subPath', 'subPathExpr'))
@pytest.mark.parametrize('phase', ('old', 'new', 'desired'))
def test_lif11_sensor_subpath_cannot_bind_host_root_settings(tmp_path, option, phase):
    lab = FakeLab(tmp_path, replaced=True,
        sensor_mount_option=option, sensor_mount_phase=phase)
    for root in (lab.old_sensor, lab.new_sensor):
        alternate = root / 'alternate'
        alternate.mkdir()
        (alternate / 'settings.json').write_bytes((root / 'settings.json').read_bytes())
    _worker_cleanup(lab, {})
    assert 'RESTORE_SENSOR_SESSION_UNVERIFIED' in lab.missing
    assert lab.mutated is True


def test_lif11_empty_first_pod_list_cannot_reclassify_old_uid_as_new(tmp_path):
    lab = FakeLab(tmp_path, replaced=False, empty_old_list=True)
    _worker_cleanup(lab, {})
    assert 'NEXT_START_NEW_GENERATION_UNOBSERVED' in lab.missing


def test_lif11_changed_restore_manifest_bytes_are_rejected_before_apply(tmp_path):
    path = tmp_path / 'restore.json'
    path.write_text('{"session":"approved"}')
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text('{"session":"changed"}')
    config = {'namespace': 'byul-gpu-b-20261102', 'selector': 'app=byul-b-engine',
        'container': 'engine', 'probe_pod': 'probe', 'probe_container': 'probe',
        'service_url': 'http://vllm.byul-gpu-b-20261102.svc:80',
        'model': 'before', 'deployment': 'vllm', 'process_pattern': 'VLLM::Worker',
        'deadline_ms': 1000, 'good_manifest': str(path), 'good_manifest_sha256': expected}
    lab = Lab(config, runner=lambda *_args, **_kwargs: pytest.fail('kubectl apply attempted'))
    with pytest.raises(ValueError, match='LAB_ACTIVATION_GOOD_MANIFEST_CHANGED'):
        lab.apply('good_manifest')
    assert lab.mutated is False


@pytest.mark.parametrize('phase', ['old', 'new', 'both'])
def test_restore_sensor_session_accepts_omitted_false_default_without_mutating_raw(tmp_path, phase):
    lab = FakeLab(tmp_path, replaced=True)
    old, new = pod('old', lab.old_sensor), pod('new', lab.new_sensor)
    for name, item in [('old', old), ('new', new)]:
        if phase in (name, 'both'):
            item['spec']['containers'][0]['volumeMounts'][1].pop('readOnly')
    before = deepcopy((old, new))
    assert restore_from_snapshots(lab, old, new) == 'b' * 32
    assert (old, new) == before


@pytest.mark.parametrize('value', [0, None, '', 'false', True])
def test_restore_sensor_session_rejects_false_lookalikes_and_readonly_data(tmp_path, value):
    lab = FakeLab(tmp_path, replaced=True)
    old, new = pod('old', lab.old_sensor), pod('new', lab.new_sensor)
    new['spec']['containers'][0]['volumeMounts'][1]['readOnly'] = value
    assert restore_from_snapshots(lab, old, new) is None


def test_restore_sensor_default_normalization_does_not_admit_extra_shadow_mount(tmp_path):
    lab = FakeLab(tmp_path, replaced=True)
    old, new = pod('old', lab.old_sensor), pod('new', lab.new_sensor)
    new['spec']['containers'][0]['volumeMounts'][1].pop('readOnly')
    new['spec']['containers'][0]['volumeMounts'].append({
        'name': 'other', 'mountPath': '/guard-lifecycle/settings.json', 'readOnly': True})
    assert restore_from_snapshots(lab, old, new) is None
