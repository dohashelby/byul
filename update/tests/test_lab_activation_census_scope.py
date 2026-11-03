"""실제 CENSUS 생산자에서 health 소비자까지 완전성 계약을 확인한다."""
import builtins
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys

import pytest

from guard.executors import lab_activation as lab

_spec = importlib.util.spec_from_file_location('_census_fixture', Path(__file__).with_name('test_lab_activation.py'))
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)


def produced_census(mode):
    """명시된 /proc fixture에 실제 고정 프로그램을 실행한다. 필드를 손으로 채우지 않는다."""
    enumerations = 0; stat_reads = 0
    def listdir(path):
        nonlocal enumerations
        if path == '/dev/shm': return []
        assert path == '/proc'
        enumerations += 1
        if mode == 'enumeration-error': raise PermissionError('enumeration denied')
        if mode == 'empty': return ['1']
        if mode == 'alive': return ['1', '7', '8']
        if mode == 'scope-change' and enumerations > 1: return ['1', '9']
        return ['1', '8']
    def opened(path, mode_arg='r', *args, **kwargs):
        nonlocal stat_reads
        assert str(path).startswith(('/proc/7/', '/proc/8/'))
        if str(path).endswith('/cmdline'):
            if mode == 'permission': raise PermissionError('cmdline denied')
            if mode == 'enoent': raise FileNotFoundError('pid disappeared')
            return io.BytesIO(b'VLLM::Worker\0--rank=0')
        if str(path).endswith('/comm'): return io.StringIO('VLLM::Worker\n')
        assert str(path).endswith('/stat')
        stat_reads += 1
        fields = ['S'] + ['0'] * 21
        fields[19] = '101' if mode == 'pid-reused' and stat_reads > 1 else '100'
        fields[21] = '2'
        return io.StringIO(str(path).split('/')[2] + ' (worker) ' + ' '.join(fields))
    output = io.StringIO()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, 'listdir', listdir)
        patch.setattr(os, 'getpid', lambda: 1)
        patch.setattr(os, 'sysconf', lambda _key: 4096, raising=False)
        patch.setattr(builtins, 'open', opened)
        patch.setattr(sys, 'argv', ['fixed-census', 'VLLM::Worker'])
        with contextlib.redirect_stdout(output):
            exec(compile(lab.CENSUS, '<actual-fixed-census>', 'exec'), {})
    return json.loads(output.getvalue())


@pytest.mark.parametrize('mode,code', [
    ('empty', None), ('alive', None), ('permission', 'PID_READ_UNAVAILABLE'),
    ('enoent', 'PID_DISAPPEARED_DURING_READ'), ('pid-reused', 'PID_IDENTITY_CHANGED_DURING_READ'),
    ('enumeration-error', 'PROC_ENUMERATION_UNAVAILABLE'), ('scope-change', 'PROC_SCOPE_CHANGED_DURING_READ')])
def test_actual_census_keeps_scope_completeness_and_specific_error(mode, code):
    value = produced_census(mode)
    assert value['process_scope_complete'] is (code is None)
    assert bool(value['processes']) is (mode in ('alive', 'scope-change'))
    if code:
        assert code in {row['code'] for row in value['process_scope_errors']}
    else:
        assert value['process_scope_errors'] == []


def consume(before_mode, after_mode='empty', transform=None):
    world = fixture.Cluster(); calls = []; direct_requests = []
    def runner(argv, **options):
        if lab.CENSUS in argv:
            calls.append(argv)
            mode = 'alive' if len(calls) <= 2 else before_mode if len(calls) == 3 else after_mode
            if mode == 'command-error': raise OSError('census command unavailable')
            value = produced_census(mode)
            if transform is not None and len(calls) >= 3: transform(value)
            return world.result(value)
        if lab.CLIENT in argv:
            payload = json.loads(options['input_bytes'])
            if payload['url'].startswith('http://10.'):
                direct_requests.append(payload)
        return world(argv, **options)
    instance = lab.Lab(fixture.BASE, runner=runner, clock=world.clock, wait=world.wait)
    instance.violate('CONFIRMED_INDEPENDENT_FAIL')
    lab._record_state(instance, {})
    return instance, calls, direct_requests


def test_actual_empty_census_before_and_after_direct_health_preserves_real_violation():
    instance, calls, direct = consume('empty')
    assert len(calls) == 4 and len(direct) == 1
    assert {row['code'] for row in instance.violations} == {
        'CONFIRMED_INDEPENDENT_FAIL', 'HEALTH_REPORTED_AFTER_WORKERS_GONE'}
    note = instance.steps[-1]
    assert note['process_scope_complete_before'] is note['process_scope_complete_after'] is True
    assert note['process_scope_errors_before'] == note['process_scope_errors_after'] == []


@pytest.mark.parametrize('mode', ['permission', 'enoent', 'pid-reused', 'enumeration-error'])
def test_incomplete_actual_before_census_is_missing_without_erasing_independent_fail(mode):
    instance, calls, direct = consume(mode)
    assert len(calls) == 3 and direct == []
    assert instance.violations == [{'code': 'CONFIRMED_INDEPENDENT_FAIL'}]
    assert 'STATE_WORKER_ABSENCE_CENSUS_INCOMPLETE' in instance.missing
    assert instance.steps[-1]['process_scope_complete_before'] is False
    assert instance.steps[-1]['process_scope_errors_before']


@pytest.mark.parametrize('mode', ['permission', 'enoent', 'pid-reused', 'enumeration-error', 'scope-change'])
def test_incomplete_actual_after_census_cannot_turn_health_into_false_fail(mode):
    instance, calls, direct = consume('empty', mode)
    assert len(calls) == 4 and len(direct) == 1
    assert instance.violations == [{'code': 'CONFIRMED_INDEPENDENT_FAIL'}]
    assert 'STATE_HEALTH_POST_CENSUS_INCOMPLETE' in instance.missing
    assert instance.steps[-1]['process_scope_complete_before'] is True
    assert instance.steps[-1]['process_scope_complete_after'] is False
    assert instance.steps[-1]['process_scope_errors_after']


def test_legacy_zero_without_completeness_is_not_verified_absence(monkeypatch):
    world = fixture.Cluster(die_on_signal=True)
    instance = lab.Lab(fixture.BASE, runner=world, clock=world.clock, wait=world.wait)
    census = instance.census
    def legacy(*args):
        result = census(*args)
        result.pop('process_scope_complete', None)
        result.pop('process_scope_errors', None)
        return result
    monkeypatch.setattr(instance, 'census', legacy)
    lab._record_state(instance, {})
    assert not instance.violations
    assert 'STATE_WORKER_ABSENCE_CENSUS_INCOMPLETE' in instance.missing


def test_actual_complete_before_census_and_later_command_error_preserve_independent_fail():
    instance, calls, direct = consume('empty', 'command-error')
    assert len(calls) == 4 and len(direct) == 1
    assert instance.violations == [{'code': 'CONFIRMED_INDEPENDENT_FAIL'}]
    assert 'STATE_HEALTH_POST_CENSUS_OR_BINDING_UNAVAILABLE' in instance.missing


@pytest.mark.parametrize('field', ['process_scope_complete', 'process_scope_errors',
    'process_count_before', 'process_count_after'])
def test_missing_producer_completeness_field_cannot_verify_health_absence(field):
    instance, calls, direct = consume('empty', transform=lambda value: value.pop(field))
    assert len(calls) == 3 and direct == []
    assert instance.violations == [{'code': 'CONFIRMED_INDEPENDENT_FAIL'}]
    assert 'STATE_WORKER_ABSENCE_CENSUS_INCOMPLETE' in instance.missing


@pytest.mark.parametrize('mode', ['empty', 'permission', 'enoent', 'pid-reused', 'enumeration-error', 'scope-change'])
def test_actual_census_to_worker_cleanup_requires_complete_death_proof(tmp_path, monkeypatch, mode):
    spec = importlib.util.spec_from_file_location('_cleanup_fixture', Path(__file__).with_name('test_lab_activation_lif11_restart.py'))
    restart = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(restart)
    instance = restart.FakeLab(tmp_path, replaced=True)
    instance.violate('CONFIRMED_INDEPENDENT_FAIL')
    monkeypatch.setattr(instance, 'selected_worker', lambda: {
        'pod': 'engine', 'pod_uid': 'old', 'pid': 7, 'start_ticks': 100})
    calls = 0
    def census(*args):
        nonlocal calls
        calls += 1
        return produced_census('alive' if calls == 1 else mode)
    monkeypatch.setattr(instance, 'census', census)
    lab._worker_cleanup(instance, {})
    note = next(value for step, value in instance.steps if step == 'worker-failure')
    actual_after = produced_census(mode)
    assert note['process_identities_after'] == [(p['pid'], p['start_ticks']) for p in actual_after['processes']]
    assert note['process_scope_errors_after'] == actual_after['process_scope_errors']
    restart_note = next(value for step, value in instance.steps if step == 'worker-restart-generation')
    assert restart_note['selected_worker_death_observed'] is (mode == 'empty')
    if mode == 'empty':
        assert 'SELECTED_WORKER_DEATH_CENSUS_INCOMPLETE' not in instance.missing
        assert instance.mutated is False
    else:
        assert 'SELECTED_WORKER_DEATH_CENSUS_INCOMPLETE' in instance.missing
        assert 'SELECTED_WORKER_DEATH_UNOBSERVED' in instance.missing
        assert instance.mutated is True
    assert instance.violations == ['CONFIRMED_INDEPENDENT_FAIL']


def test_contradictory_complete_flag_does_not_override_census_scope_errors():
    def inconsistent(value):
        value['process_scope_errors'] = [{'code': 'PID_READ_UNAVAILABLE', 'pid': 8}]
    instance, calls, direct = consume('empty', transform=inconsistent)
    assert len(calls) == 3 and not direct
    assert 'STATE_WORKER_ABSENCE_CENSUS_INCOMPLETE' in instance.missing
    assert instance.violations == [{'code': 'CONFIRMED_INDEPENDENT_FAIL'}]


def test_changed_enumeration_counts_cannot_verify_health_absence():
    instance, calls, direct = consume('empty', transform=lambda value: value.update(process_count_after=1))
    assert len(calls) == 3 and not direct
    assert 'STATE_WORKER_ABSENCE_CENSUS_INCOMPLETE' in instance.missing
    assert instance.violations == [{'code': 'CONFIRMED_INDEPENDENT_FAIL'}]
