"""LIF-09 현재 engine 로그와 조회 후 Ready 세대의 상태 결합 계약."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

from guard.executors import lab_activation as lab


_source = Path(__file__).with_name('test_lab_activation.py')
_spec = importlib.util.spec_from_file_location('_lab_activation_marker_fixture', _source)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)


@pytest.fixture(autouse=True)
def manifests(tmp_path, monkeypatch):
    for name in ('faulty', 'good'):
        (tmp_path / f'{name}.yaml').write_text(name)
    monkeypatch.chdir(tmp_path)


def _run(*, state='running', pod_ready=True, engine_ready=True, stale_ready=False,
         terminate_during_log=False, initial_pending_reads=0):
    world = _fixture.Cluster(faulty_ready=True)
    original_pods = world.pods
    counts = {'faulty': 0}

    def pods():
        result = deepcopy(original_pods())
        if world.manifest != 'faulty':
            return result
        counts['faulty'] += 1
        for pod in result:
            status = pod['status']['containerStatuses'][0]
            before_log = counts['faulty'] < 3 if terminate_during_log else True
            effective_state = 'running' if before_log else 'terminated'
            if not terminate_during_log:
                effective_state = state
            if effective_state == 'terminated':
                status['state'] = {'terminated': {'startedAt': status['state']['running']['startedAt'],
                    'finishedAt': '2026-11-02T00:01:00Z', 'exitCode': 1}}
            status['ready'] = engine_ready and effective_state == 'running'
            is_ready = pod_ready and effective_state == 'running'
            if stale_ready and counts['faulty'] >= 4:
                is_ready = False; status['ready'] = False
            pod['status']['conditions'][0]['status'] = 'True' if is_ready else 'False'
            if counts['faulty'] <= initial_pending_reads:
                status.pop('containerID', None)
                status['state'] = {'waiting': {'reason': 'ContainerCreating'}}
                status['ready'] = False
                pod['status']['conditions'][0]['status'] = 'False'
        return result

    world.pods = pods
    activation = lab.Lab(_fixture.BASE, runner=world, clock=world.clock, wait=world.wait)
    activation.verify_owned()
    lab._rejected(activation, 'PARTIAL_INITIALIZATION_REPORTED_READY',
        _fixture.BASE['failure_marker'], require_new_faulty_uid=True)
    return activation, world


@pytest.mark.parametrize('state', ['running', 'terminated'])
def test_current_unready_engine_marker_is_observable_without_ready_violation(state):
    activation, world = _run(state=state, pod_ready=False, engine_ready=False)
    step = next(row for row in activation.steps if row['step'] == 'faulty-generation')
    assert step['marker_uids']
    assert not any(row['code'] == 'PARTIAL_INITIALIZATION_REPORTED_READY'
        for row in activation.violations)
    assert any('--container=engine' in call and 'logs' in call for call in world.calls)


def test_ready_snapshot_before_marker_cannot_override_post_log_unready_snapshot():
    activation, _ = _run(stale_ready=True)
    assert not any(row['code'] == 'PARTIAL_INITIALIZATION_REPORTED_READY'
        for row in activation.violations)


def test_running_marker_does_not_bind_terminated_state_during_log():
    activation, _ = _run(terminate_during_log=True)
    assert 'FAULTY_CURRENT_ENGINE_CONTAINER_UNVERIFIED' in activation.missing
    assert not any(row['code'] == 'PARTIAL_INITIALIZATION_REPORTED_READY'
        for row in activation.violations)


def test_fresh_ready_running_same_container_marker_keeps_independent_fail():
    activation, _ = _run()
    assert any(row['code'] == 'PARTIAL_INITIALIZATION_REPORTED_READY'
        for row in activation.violations)


def test_waiting_and_last_terminated_are_not_current_marker_identity():
    world = _fixture.Cluster()
    activation = lab.Lab(_fixture.BASE, runner=world, clock=world.clock, wait=world.wait)
    pod = world.pods()[0]
    state = pod['status']['containerStatuses'][0]
    state['ready'] = False
    state['state'] = {'waiting': {'reason': 'CrashLoopBackOff'}}
    state['lastState'] = {'terminated': {'startedAt': '2026-11-02T00:00:00Z'}}
    assert activation.engine_container_identity(pod, require_ready=False) is None
    state['state'] = {'terminated': {'startedAt': '2026-11-02T00:00:00Z'}}
    assert activation.engine_container_identity(pod, require_ready=False)[0] == 'terminated'
    assert activation.engine_container_identity(pod) is None


def test_initial_container_creating_does_not_poison_later_bound_failure_marker():
    activation, _ = _run(pod_ready=False, engine_ready=False, initial_pending_reads=2)
    step = next(row for row in activation.steps if row['step'] == 'faulty-generation')
    assert step['marker_uids']
    assert 'FAULTY_CURRENT_ENGINE_CONTAINER_UNVERIFIED' not in activation.missing
    assert 'FAULTY_REQUEST_GENERATION_UNATTRIBUTED' in activation.missing
    assert activation.violations == []


def test_container_that_never_starts_remains_unverified():
    activation, _ = _run(pod_ready=False, engine_ready=False, initial_pending_reads=10000)
    step = next(row for row in activation.steps if row['step'] == 'faulty-generation')
    assert step['marker_uids'] == []
    assert 'FAULTY_CURRENT_ENGINE_CONTAINER_UNVERIFIED' in activation.missing
    assert activation.violations == []


def test_outer_pending_does_not_hide_inner_running_to_terminated_log_race():
    activation, _ = _run(pod_ready=False, engine_ready=False,
        initial_pending_reads=1, terminate_during_log=True)
    assert 'FAULTY_CURRENT_ENGINE_CONTAINER_UNVERIFIED' in activation.missing
    assert activation.violations == []


@pytest.mark.parametrize('changed', ['restart', 'container', 'last-state', 'waiting', 'missing'])
def test_initial_wait_requires_no_prior_or_unknown_container_incarnation(changed):
    world = _fixture.Cluster()
    activation = lab.Lab(_fixture.BASE, runner=world, clock=world.clock, wait=world.wait)
    pod = world.pods()[0]
    status = pod['status']['containerStatuses'][0]
    status.pop('containerID')
    status.update(ready=False, state={'waiting': {'reason': 'ContainerCreating'}})
    assert activation.initial_engine_container_wait(pod) is True
    if changed == 'restart':
        status['restartCount'] = 1
    elif changed == 'container':
        status['containerID'] = 'containerd://prior'
    elif changed == 'last-state':
        status['lastState'] = {'terminated': {'startedAt': '2026-11-02T00:00:00Z'}}
    elif changed == 'waiting':
        status['state']['waiting']['reason'] = 'CrashLoopBackOff'
    else:
        pod['status']['containerStatuses'] = []
    assert activation.initial_engine_container_wait(pod) is False


def test_unknown_current_incarnation_preserves_previous_marker_only_as_diagnostic():
    world = _fixture.Cluster(old_container_marker=True)
    original = world.pods

    def pods():
        result = deepcopy(original())
        if world.manifest == 'faulty':
            for item in result:
                item['status']['containerStatuses'][0]['state'] = {
                    'waiting': {'reason': 'CrashLoopBackOff'}}
                item['status']['containerStatuses'][0]['ready'] = False
                item['status']['conditions'][0]['status'] = 'False'
        return result

    world.pods = pods
    activation = lab.Lab(_fixture.BASE, runner=world, clock=world.clock, wait=world.wait)
    lab._rejected(activation, 'PARTIAL_INITIALIZATION_REPORTED_READY',
        _fixture.BASE['failure_marker'], require_new_faulty_uid=True)
    step = next(row for row in activation.steps if row['step'] == 'faulty-generation')
    assert step['diagnostic_marker_uids']
    assert step['marker_uids'] == []
    assert 'FAULTY_CURRENT_ENGINE_CONTAINER_UNVERIFIED' in activation.missing
    assert 'FAULTY_MARKER_ONLY_IN_PREVIOUS_OR_OTHER_CONTAINER' in activation.missing
    assert activation.violations == []
