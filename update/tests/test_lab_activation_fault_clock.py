"""Remote request durations cannot establish overlap with controller injection."""
from types import SimpleNamespace

import pytest

from guard.executors import lab_activation as actor


@pytest.mark.parametrize('elapsed,outcome', [(600, 200), (1500, 200), (90000, 'timeout')])
@pytest.mark.parametrize('finished_before_call', [True, False])
def test_fault_latency_requires_a_shared_time_origin(monkeypatch, elapsed, outcome, finished_before_call):
    future = SimpleNamespace(done=lambda: finished_before_call,
        result=lambda: [{'elapsed_ms': elapsed, 'outcome': outcome, 'complete': outcome == 200}])

    class Pool:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def submit(self, *_args): return future

    class Lab:
        fault_restore_failed = False
        mutated = True
        now = 0.
        def __init__(self):
            self.notes = []; self.missing = []; self.violations = []; self.cleanup_errors = []
            self.signals = []; self.restore_calls = 0
        def normal_request(self, _name): return {}
        def clock(self): return self.now
        def wait(self, seconds): self.now += seconds
        def chat(self, *_args, **_kwargs): return {}
        def requests(self, _items): raise AssertionError('controlled remote Future only')
        def selected_worker(self):
            self.now += 2.  # SSH/census after the initial 500ms delay.
            return {'pid': 107, 'start_ticks': 123, 'pod': 'engine', 'pod_uid': 'owned'}
        def census(self, *_args):
            return {'processes': [{'pid': 107, 'start_ticks': 123, 'state': 'T'}]}
        def signal_worker(self, number, target): self.signals.append((number, target['pid']))
        def note(self, name, **values): self.notes.append((name, values))
        def miss(self, code): self.missing.append(code)
        def violate(self, code, **values): self.violations.append((code, values))
        def restore(self): self.restore_calls += 1; return True

    monkeypatch.setattr(actor, 'ThreadPoolExecutor', Pool)
    lab = Lab(); injected_at = []
    actor._one_fault(lab, 'delay', lambda _target: injected_at.append(lab.now), 1000, actor.POSIX_SIGSTOP)
    assert injected_at == [2.5]
    note = lab.notes[-1][1]
    assert note['injected_ms'] == 2500 and note['injection_returned_ms'] == 2500
    assert note['injection_clock_scope'] == 'controller_call'
    assert note['request_elapsed_clock_scope'] == 'remote_client'
    assert note['request_fault_overlap_verified'] is False
    assert note['fault_detection_latency_verified'] is False
    assert ('FAULT_AFTER_REQUEST_FINISHED:delay' in lab.missing) is finished_before_call
    assert 'FAULT_REQUEST_OVERLAP_UNOBSERVED:delay' in lab.missing
    assert 'FAULT_DETECTION_TIMING_UNVERIFIED:delay' in lab.missing
    assert lab.violations == []
    assert lab.signals == [(actor.POSIX_SIGCONT, 107)] and lab.restore_calls == 1
    assert lab.mutated is False
