"""고정 bb47 리뷰의 장애 복원·기한·세션 회귀. CPU 계약 시험."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from guard.executors import lab_activation as lab


def fixture(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = fixture('test_lab_activation')
restart = fixture('test_lab_activation_lif11_restart')


@pytest.mark.parametrize('error', [OSError('read'), TypeError('metric'), RuntimeError('collector')])
def test_every_scenario_exception_attempts_restore_and_preserves_fail(monkeypatch, error):
    restored = []
    def broken(instance, _values):
        instance.mutated = True
        instance.violate('CONFIRMED_INDEPENDENT_FAIL')
        raise error
    monkeypatch.setitem(lab.SCENARIOS, 'LIF-08', broken)
    monkeypatch.setattr(lab.Lab, 'verify_owned', lambda _: None)
    def restore(instance):
        restored.append(instance)
        raise OSError('cleanup')
    monkeypatch.setattr(lab.Lab, 'restore', restore)
    facts, instance = lab.run_scenario('LIF-08', base.BASE, {})
    assert len(restored) == 1
    assert facts['activation_violations'] == [{'code': 'CONFIRMED_INDEPENDENT_FAIL'}]
    assert 'SCENARIO_INTERRUPTED:' + type(error).__name__ in facts['activation_missing']
    assert 'FINAL_RESTORE_FAILED:OSError' in facts['activation_missing']


@pytest.mark.parametrize('restore_result', [False, None, OSError('restore')])
def test_fault_error_restores_before_blocking_next_injection(monkeypatch, restore_result):
    world = base.Cluster()
    instance = lab.Lab(base.BASE, runner=world, clock=world.clock, wait=world.wait)
    injections = []; restores = []
    def inject(_target):
        injections.append('injected')
        instance.mutated = True
        raise OSError('after mutation')
    def restore():
        restores.append('restore')
        if isinstance(restore_result, Exception):
            raise restore_result
        return restore_result
    monkeypatch.setattr(instance, 'restore', restore)
    lab._one_fault(instance, 'death', inject, 1000, lab.POSIX_SIGKILL)
    lab._one_fault(instance, 'delay', inject, 1000, lab.POSIX_SIGSTOP)
    assert injections == ['injected'] and restores == ['restore']
    assert 'FAULT_NOT_INJECTED:death' in instance.missing
    assert 'SUBSEQUENT_FAULTS_BLOCKED_BY_RESTORE_FAILURE' in instance.missing
    assert instance.cleanup_errors


@pytest.mark.parametrize('values', [[10, None, 10], [10, 20, None], [10, 20, 20, None]])
def test_retry_none_metric_is_explicit_missing(values, monkeypatch):
    world = base.Cluster()
    instance = lab.Lab(base.BASE, runner=world, clock=world.clock, wait=world.wait)
    metrics = iter(values)
    monkeypatch.setattr(instance, 'metric', lambda _name: next(metrics))
    monkeypatch.setattr(instance, 'wait_idle', lambda _: 0)
    lab._retry(instance, {})
    assert 'ATTEMPT_COUNTER_METRIC_UNAVAILABLE' in instance.missing
    assert not instance.violations


def test_subsecond_kubectl_timeout_is_positive_and_within_budget():
    calls = []
    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return {'returncode': 0, 'stdout_bytes': b'{}'}
    instance = lab.Lab(base.BASE, runner=runner, clock=lambda: 0.)
    instance.deadline = .000000012
    instance._kubectl('get', 'pods')
    argv, options = calls[0]
    value = next(arg.split('=', 1)[1] for arg in argv if arg.startswith('--request-timeout='))
    assert value == '12ns'
    assert 0 < int(value[:-2]) / 1e9 <= options['timeout'] <= instance.deadline
    instance.diagnostic('clock-test')
    assert instance.diagnostics[-1]['recorded_monotonic'] == 0


def pin_manifest(instance, manifest):
    path = Path(instance.cfg['good_manifest'])
    path.write_text(yaml.safe_dump(manifest))
    instance.cfg['good_manifest_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()


def test_yaml_split_callbacks_and_remote_node_settings_are_supported(tmp_path):
    instance = restart.FakeLab(tmp_path, replaced=True)
    old = restart.pod('old', instance.old_sensor)
    new = restart.pod('new', instance.new_sensor)
    manifest = json.loads(Path(instance.cfg['good_manifest']).read_text())
    for spec in (new['spec'], manifest['spec']['template']['spec']):
        spec['containers'][0]['volumeMounts'][0].update(name='guard-callbacks', mountPath='/guard-lifecycle/engine-callbacks')
        spec['containers'][0]['volumeMounts'][1]['name'] = 'guard-data'
        spec['volumes'][0]['name'] = 'guard-callbacks'
        # 이 경로는 컨트롤러에 존재하지 않는다.
        spec['volumes'][0]['hostPath']['path'] = '/remote/node/approved-callbacks'
    new['spec']['nodeName'] = 'remote-node'
    pin_manifest(instance, manifest)
    original = deepcopy((old, new))
    assert restart.restore_from_snapshots(instance, old, new) == 'b' * 32
    assert (old, new) == original


def test_remote_settings_error_is_diagnosable_without_path_or_secret(tmp_path, monkeypatch):
    instance = restart.FakeLab(tmp_path, replaced=True)
    def unavailable(*_args, **_kwargs):
        raise OSError('/remote/private/settings: SECRET_CANARY')
    monkeypatch.setattr(instance, '_exec', unavailable)
    assert restart.restore_from_snapshots(instance,
        restart.pod('old', instance.old_sensor), restart.pod('new', instance.new_sensor)) is None
    assert 'RESTORE_SENSOR_SESSION_UNVERIFIED:OSError' in instance.missing
    assert 'SECRET_CANARY' not in repr(instance.missing)


@pytest.mark.parametrize('changed', [False, True])
def test_health_after_workers_gone_is_a_violation_only_for_stable_direct_pod(changed, monkeypatch):
    world = base.Cluster(die_on_signal=True)
    instance = lab.Lab(base.BASE, runner=world, clock=world.clock, wait=world.wait)
    original = instance.requests
    def requests(items, **kwargs):
        rows = original(items, **kwargs)
        if kwargs.get('url') and changed:
            world.container_epoch += 1
        return rows
    monkeypatch.setattr(instance, 'requests', requests)
    lab._record_state(instance, {})
    codes = [v['code'] for v in instance.violations]
    assert ('HEALTH_REPORTED_AFTER_WORKERS_GONE' in codes) is (not changed)
    if changed:
        assert 'STATE_SERVICE_HEALTH_WITHOUT_SELECTED_POD_ATTRIBUTION' in instance.missing


def test_ready_before_service_is_a_violation_with_direct_pod_binding(monkeypatch):
    world = base.Cluster(early_ready=True)
    instance = lab.Lab(base.BASE, runner=world, clock=world.clock, wait=world.wait)
    original = instance.requests
    def requests(items, **kwargs):
        rows = original(items, **kwargs)
        return [{**row, 'outcome': 503} for row in rows] if world.rollout_generation else rows
    monkeypatch.setattr(instance, 'requests', requests)
    lab._startup_gate(instance, {})
    assert any(v['code'] == 'READY_BEFORE_INFERENCE_AVAILABLE' for v in instance.violations)


def test_tampered_residual_is_fail_and_survives_later_collection_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ('faulty', 'good'):
        (tmp_path / (name + '.yaml')).write_text(name)
    world = base.Cluster(residual=True)
    instance = lab.Lab(base.BASE, runner=world, clock=world.clock, wait=world.wait)
    lab._restore_material(instance, {})
    assert any(v['code'] == 'TAMPERED_MATERIAL_STILL_REFERENCED' for v in instance.violations)
    instance.miss('LATER_COLLECTION_UNAVAILABLE')
    assert any(v['code'] == 'TAMPERED_MATERIAL_STILL_REFERENCED' for v in instance.violations)


def test_owned_new_generation_without_readiness_preserves_blocked_restart_fail(tmp_path):
    instance = restart.FakeLab(tmp_path, replaced=True)
    original = instance._pods
    def pods():
        result = original()
        if instance.applied:
            for pod in result:
                pod['status']['conditions'][0]['status'] = 'False'
        return result
    instance._pods = pods
    lab._worker_cleanup(instance, {})
    assert 'NEXT_START_BLOCKED_AFTER_WORKER_FAILURE' in instance.violations


def test_inflight_timeout_preserves_transition_fail_with_observed_pending_body(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ('update', 'good'):
        (tmp_path / (name + '.yaml')).write_text(name)
    class TimedOutStream(base.Cluster):
        def http(self, item):
            if item.get('body') and item['body']['messages'][0]['content'] == 'inflight':
                assert item['body']['stream'] is True and item['body']['model'] == 'm'
                return {'outcome': 'timeout', 'complete': False, 'models': ['m']}
            return super().http(item)
    world = TimedOutStream()
    cfg = {**base.BASE, 'probe_progress_directory': '/probe-progress'}
    facts, _ = lab.run_scenario('LIF-05', cfg, base.POLICY,
        runner=world, clock=world.clock, wait=world.wait)
    assert 'INFLIGHT_REQUEST_HUNG_ACROSS_TRANSITION' in {v['code'] for v in facts['activation_violations']}
    assert 'INFLIGHT_TIMEOUT_WITHOUT_SERVER_ATTRIBUTION' not in facts['activation_missing']


def test_transition_fail_survives_later_pod_collection_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ('update', 'good'):
        (tmp_path / (name + '.yaml')).write_text(name)
    class TransitionError(base.Cluster):
        def http(self, item):
            if item.get('body') and item['body']['messages'][0]['content'] == 'inflight':
                return {'outcome': 'timeout', 'complete': False, 'models': ['m']}
            return super().http(item)
        def pods(self):
            if self.manifest == 'updated':
                raise OSError('post-transition collection')
            return super().pods()
    world = TransitionError()
    facts, _ = lab.run_scenario('LIF-05', {**base.BASE, 'probe_progress_directory': '/probe-progress'},
        base.POLICY, runner=world, clock=world.clock, wait=world.wait)
    assert 'INFLIGHT_REQUEST_HUNG_ACROSS_TRANSITION' in {v['code'] for v in facts['activation_violations']}
    assert 'SCENARIO_INTERRUPTED:OSError' in facts['activation_missing']


@pytest.mark.parametrize('duration', [.6, 5])
def test_startup_optional_request_runs_after_original_service_window(duration, monkeypatch):
    world = base.Cluster(); world.clock = lambda: world.now
    instance = lab.Lab({**base.BASE, 'deadline_ms': 2000}, runner=world,
        clock=world.clock, wait=world.wait)
    original = instance.requests
    service_times = []; direct_times = []
    def requests(items, **kwargs):
        if world.rollout_generation:
            if kwargs.get('url'):
                direct_times.append(world.now)
                world.now += duration
            else:
                service_times.append(world.now)
            return [{**row, 'outcome': 503} for row in original(items, **kwargs)]
        return original(items, **kwargs)
    monkeypatch.setattr(instance, 'requests', requests)
    lab._startup_gate(instance, {})
    assert service_times == [0, .5, 1, 1.5]
    assert direct_times == [2]
    assert next(step for step in instance.steps if step['step'] == 'startup')['samples'] == 4
    assert any(v['code'] == 'READY_BEFORE_INFERENCE_AVAILABLE' for v in instance.violations)
    assert 'READY_WITHOUT_SERVICE_REQUEST_POD_UNATTRIBUTED' in instance.missing


@pytest.mark.parametrize('outcome', ['timeout', 'error', 404, 403])
def test_startup_transport_or_request_error_is_not_inference_unavailable(outcome, monkeypatch):
    world = base.Cluster(); world.clock = lambda: world.now
    instance = lab.Lab({**base.BASE, 'deadline_ms': 1000}, runner=world,
        clock=world.clock, wait=world.wait)
    original = instance.requests
    def requests(items, **kwargs):
        rows = original(items, **kwargs)
        return [{**row, 'outcome': outcome if kwargs.get('url') else 503} for row in rows] if world.rollout_generation else rows
    monkeypatch.setattr(instance, 'requests', requests)
    lab._startup_gate(instance, {})
    assert not any(v['code'] == 'READY_BEFORE_INFERENCE_AVAILABLE' for v in instance.violations)
    assert 'READY_WITHOUT_SERVICE_REQUEST_POD_UNATTRIBUTED' in instance.missing


def test_startup_ready_lost_during_direct_request_does_not_prove_ready_conflict(monkeypatch):
    world = base.Cluster(); world.clock = lambda: world.now
    world.ready = lambda: world.now < 1.2
    instance = lab.Lab({**base.BASE, 'deadline_ms': 1000}, runner=world,
        clock=world.clock, wait=world.wait)
    original = instance.requests
    def requests(items, **kwargs):
        rows = original(items, **kwargs)
        if kwargs.get('url'):
            world.now += .6
        return [{**row, 'outcome': 503} for row in rows] if world.rollout_generation else rows
    monkeypatch.setattr(instance, 'requests', requests)
    lab._startup_gate(instance, {})
    assert not any(v['code'] == 'READY_BEFORE_INFERENCE_AVAILABLE' for v in instance.violations)


@pytest.mark.parametrize('post_error', [False, True])
def test_reappearing_workers_or_lost_census_cannot_conflict_with_health(post_error, monkeypatch):
    world = base.Cluster(die_on_signal=True)
    instance = lab.Lab(base.BASE, runner=world, clock=world.clock, wait=world.wait)
    instance.violate('CONFIRMED_INDEPENDENT_FAIL')
    requests = instance.requests; census = instance.census; direct_done = False
    def wrapped_requests(items, **kwargs):
        nonlocal direct_done
        rows = requests(items, **kwargs)
        if kwargs.get('url'):
            direct_done = True
            world.modes['gone'] = False
        return rows
    def wrapped_census(*args):
        if post_error and direct_done:
            raise OSError('post census unavailable')
        return census(*args)
    monkeypatch.setattr(instance, 'requests', wrapped_requests)
    monkeypatch.setattr(instance, 'census', wrapped_census)
    lab._record_state(instance, {})
    assert instance.violations == [{'code': 'CONFIRMED_INDEPENDENT_FAIL'}]
    expected = 'STATE_HEALTH_POST_CENSUS_OR_BINDING_UNAVAILABLE' if post_error else 'STATE_WORKERS_REAPPEARED_DURING_HEALTH'
    assert expected in instance.missing


@pytest.mark.parametrize('change', ['node', 'container', 'restart', 'started', 'pod_uid'])
def test_sensor_read_rejects_identity_change_after_exec(tmp_path, monkeypatch, change):
    instance = restart.FakeLab(tmp_path, replaced=True)
    pod = restart.pod('new', instance.new_sensor)
    after = deepcopy(pod)
    status = after['status']['containerStatuses'][0]
    if change == 'node': after['spec']['nodeName'] = 'other-node'
    elif change == 'container': status['containerID'] += '-replacement'
    elif change == 'restart': status['restartCount'] += 1
    elif change == 'started': status['state']['running']['startedAt'] = '2026-11-04T00:00:00Z'
    else: after['metadata']['uid'] = 'replacement'
    monkeypatch.setattr(instance, '_pods', lambda: [after])
    with pytest.raises(ValueError, match='RESTORE_SENSOR_TARGET_IDENTITY_CHANGED'):
        lab._sensor_session(instance, pod, lab._sensor_mount(pod, 'engine'))


def test_sensor_exec_uses_isolated_python_and_closed_program_only(monkeypatch):
    world = base.Cluster()
    instance = lab.Lab(base.BASE, runner=world, clock=world.clock, wait=world.wait)
    monkeypatch.setattr(instance, '_pods', lambda: world.pods())
    calls = []
    def runner(argv, **options):
        calls.append((argv, options))
        return {'returncode': 0, 'stdout_bytes': b'{"session":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'}
    instance.runner = runner
    instance._exec(lab.SENSOR_SESSION, '/guard-lifecycle/settings.json', pod='engine-0',
        pod_uid='pod-0', isolated=True)
    argv, options = calls[0]
    position = argv.index('python3')
    assert argv[position:position + 5] == ['python3', '-I', '-S', '-B', '-c']
    assert argv[position + 5] == lab.SENSOR_SESSION
    assert 0 < float(argv[-1]) <= 60
    assert options['timeout'] <= 60
    with pytest.raises(ValueError, match='ISOLATED_PROGRAM_INVALID'):
        instance._exec('print(1)', isolated=True)
    assert len(calls) == 1


@pytest.mark.parametrize('duration', [5.5, 10])
def test_optional_startup_observation_preserves_final_restore_budget(duration, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'good.yaml').write_text('good')
    world = base.Cluster(); world.clock = lambda: world.now
    original = world.http
    def http(item):
        row = original(item)
        return {**row, 'outcome': 503} if world.rollout_generation and not world.apply_generation else row
    monkeypatch.setattr(world, 'http', http)
    cfg = {**base.BASE, 'deadline_ms': 1000, 'max_duration_seconds': duration}
    facts, instance = lab.run_scenario('LIF-07', cfg, {}, runner=world,
        clock=world.clock, wait=world.wait)
    assert 'READY_WITHOUT_SERVICE_REQUEST_POD_UNATTRIBUTED' in facts['activation_missing']
    assert instance.normal[-1] is True
    assert not instance.cleanup_errors
    assert not facts['activation_violations']


def test_overlapping_tampered_and_approved_names_are_rejected_before_mutation():
    world = base.Cluster()
    cfg = {**base.BASE, 'tampered_objects': list(base.BASE['restore_objects'])}
    instance = lab.Lab(cfg, runner=world, clock=world.clock, wait=world.wait)
    lab._restore_material(instance, {})
    assert 'RESTORE_TAMPERED_NAMES_OVERLAP_APPROVAL' in instance.missing
    assert not instance.mutated and not world.calls
    assert not instance.violations


def test_post_health_census_of_new_container_cannot_prove_prior_worker_absence(monkeypatch):
    world = base.Cluster(die_on_signal=True)
    instance = lab.Lab(base.BASE, runner=world, clock=world.clock, wait=world.wait)
    census = instance.census; calls = 0
    def wrapped_census(*args):
        nonlocal calls
        calls += 1
        value = census(*args)
        if calls == 4:
            world.container_epoch += 1
        return value
    monkeypatch.setattr(instance, 'census', wrapped_census)
    lab._record_state(instance, {})
    assert calls == 4
    assert 'STATE_HEALTH_WORKER_GENERATION_CHANGED' in instance.missing
    assert not instance.violations
