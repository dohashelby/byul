"""#196 후속: 종료된 기록의 과거 신원과 현재 worker 신원을 구분하고, 봉인 전 capture의 미종결 공백을 따로 둔다.

EXAMPLE only. 종료 기록이 있어도 과거 신원·전체 목록·producer 인증을 참으로 올리지 않는다.
"""
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture_module
from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
from guard.executors.kubernetes_profiler_control import project_captures
from tests.test_kubernetes_lifecycle import World, group, target  # noqa: F401  (fixtures)
from tests.test_lifecycle_capture import Docker, case, external, revised  # noqa: F401  (fixtures)
from tests.test_lifecycle_sensor import writer_factory  # noqa: F401  (fixture)


def test_context_reader_reports_a_vanished_process_instead_of_failing():
    pid = 4194303 * 7  # Above every Linux pid_max; no such process anywhere.
    done = subprocess.run([sys.executable, '-I', '-c', capture_module.CONTEXT_READER, str(pid)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0 and json.loads(done.stdout) == {'schema': 'guard-lifecycle-process-absent/1', 'pid': pid}


class Gone(Docker):
    """The recorded process is gone when the recheck asks for it."""
    def __init__(self, case, marks):
        super().__init__(case); self.marks = marks

    def exec_bounded(self, container, argv, *, timeout, max_bytes):
        if argv[2] == capture_module.CONTEXT_READER:
            self.contexts += 1
            value = {'schema': 'guard-lifecycle-process-absent/1', 'pid': int(argv[3])}
            return SimpleNamespace(returncode=0, stdout=json.dumps(value),
                capture={'complete': True, 'bounded_at_source': True, 'timed_out': False, 'truncated': False})
        return super().exec_bounded(container, argv, timeout=timeout, max_bytes=max_bytes)

    def mark_collection_gap(self, pid, code):
        self.marks.append((pid, code))


@pytest.mark.parametrize('end', ['PROCESS_EXIT', 'SEALED'])
def test_exited_recording_keeps_only_its_recorded_identity(case, end):
    raw = revised(case.raw, lambda rows: rows[-1]['payload'].update(reason=end))
    marks = []
    value = lifecycle.collect(Gone(SimpleNamespace(**{**vars(case), 'raw': raw}), marks), case.env, case.target, case.source, case.prepared)
    recheck = value['process_recheck']
    assert recheck['pid'] == 42 and recheck['start_ticks'] == 99 and recheck['recording_end'] == end
    assert value['process_context_verified'] is False and value['producer_authenticated'] is False
    assert value['summary']['whole_run_coverage_verified'] is False
    if end == 'PROCESS_EXIT':
        assert recheck['result'] == 'process_exited' and marks == []
        assert 'LIFECYCLE_PROCESS_EXITED_RECORDED_IDENTITY_ONLY' in value['diagnostic_codes']
    else:
        assert recheck['result'] == 'process_absent_without_exit_record'
        assert marks == [(42, 'LIFECYCLE_PROCESS_ABSENT_WITHOUT_EXIT_RECORD')]
    assert 'LIFECYCLE_RECHECK_UNAVAILABLE' not in value['diagnostic_codes']


@pytest.mark.parametrize('mode,result', [(None, 'matched'), ('wrong-context', 'mismatch'), ('process-error', 'unavailable')])
def test_live_process_recheck_names_its_result(case, mode, result):
    value = lifecycle.collect(Docker(case, mode), case.env, case.target, case.source, case.prepared)
    assert value['process_recheck']['result'] == result
    assert value['process_context_verified'] is (mode is None)


def test_kubernetes_reads_name_the_pid_and_keep_transport_gaps(group, target):
    world = World(group, target)
    def runner(argv, **kwargs):
        if 'exec' in argv and argv[-1] == '71':
            value = {'schema': 'guard-lifecycle-process-absent/1', 'pid': 71}
            return {'returncode': 0, 'stdout_bytes': json.dumps(value).encode(), 'stderr_bytes': b'',
                'capture': {'complete': True, 'bounded_at_source': True, 'timed_out': False, 'truncated': False}}
        if 'exec' in argv and argv[-1] == '72':
            return {'returncode': 1, 'stdout_bytes': b'', 'stderr_bytes': b'',
                'capture': {'complete': True, 'bounded_at_source': True, 'timed_out': False, 'truncated': False}}
        return world.runner(argv, **kwargs)
    reader = KubernetesLifecycleReader(target, group.env, group.prepared, runner=runner)
    reader.exec_bounded('engine', ['python3', '-c', capture_module.CONTEXT_READER, '71'], timeout=5, max_bytes=4096)
    row = reader.report()['reads'][-1]
    assert row['pid'] == 71 and row['result'] == 'process_absent' and reader.report()['collection_gap'] is False
    with pytest.raises(Exception):
        reader.exec_bounded('engine', ['python3', '-c', capture_module.CONTEXT_READER, '72'], timeout=5, max_bytes=4096)
    failed = reader.report()['reads'][-1]
    assert failed['pid'] == 72 and failed['result'] == 'unavailable' and 'KUBERNETES_READ_TRANSPORT_UNAVAILABLE' in failed['diagnostic_codes']
    assert reader.report()['collection_gap'] is True
    reader.mark_collection_gap(71, 'LIFECYCLE_PROCESS_ABSENT_WITHOUT_EXIT_RECORD')
    assert 'LIFECYCLE_PROCESS_ABSENT_WITHOUT_EXIT_RECORD' in next(r for r in reader.report()['reads'] if r.get('pid') == 71)['diagnostic_codes']


def projection(missing, failures):
    return {'missing': missing, 'call_binding_failures': failures, 'conflict_candidates': [], 'samples': []}


def captures(stop_failures, final_missing):
    open_gap = ['profiler_source_process_call_binding_unavailable', 'required_profiler_worker_rank_unavailable', 'other_gap']
    return [{'phase': 'stop', 'session': 's', 'projection': projection(open_gap, stop_failures)},
        {'phase': 'next-stop', 'session': 's', 'projection': projection(open_gap, stop_failures)},
        {'phase': 'final', 'session': 's', 'projection': projection(final_missing, {})}]


def test_sealed_final_capture_sets_apart_open_recording_gaps_only():
    result = project_captures(captures({'recording_not_closed': 16}, ['other_gap']), [], sealed=True)
    assert result['missing'] == ['other_gap']
    assert result['provisional_missing'] == {phase: ['profiler_source_process_call_binding_unavailable',
        'required_profiler_worker_rank_unavailable'] for phase in ('stop', 'next-stop')}


@pytest.mark.parametrize('change', ['not-sealed', 'other-failure', 'final-still-missing', 'no-final'])
def test_open_recording_gaps_stay_when_the_final_view_does_not_settle_them(change):
    sealed, failures, final_missing = True, {'recording_not_closed': 16}, ['other_gap']
    if change == 'not-sealed': sealed = False
    elif change == 'other-failure': failures = {'recording_not_closed': 16, 'physical_process_binding_unverified': 1}
    elif change == 'final-still-missing': final_missing = ['profiler_source_process_call_binding_unavailable']
    rows = captures(failures, final_missing)
    if change == 'no-final': rows[-1]['projection'] = None
    result = project_captures(rows, [], sealed=sealed)
    assert 'profiler_source_process_call_binding_unavailable' in result['missing']
    if change != 'final-still-missing':
        assert result['provisional_missing'] == {} and 'required_profiler_worker_rank_unavailable' in result['missing']
