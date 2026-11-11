"""Runtime tracking reuses bounded slots only after observed local completion."""
import os
import threading
import types
import weakref

from guard.adapters import vllm_termination_sensor as sensor


class Owner:
    def __init__(self):
        self.req_states = types.SimpleNamespace(req_id_to_index={})


class Observer(sensor.TerminationObservation):
    def __init__(self, rolling=True):
        self.term_pid = os.getpid()
        self.term_lock = threading.RLock()
        self.term_serial = 0
        self.term_runtime = "cpython-3.12.13"
        self.term_code = "e" * 64
        self.term_tracked = {}
        self.term_owners = weakref.WeakKeyDictionary()
        self.term_calls = {}
        self.term_outputs = weakref.WeakKeyDictionary()
        self.term_output_requests = weakref.WeakKeyDictionary()
        self.config = {'session': 'a' * 32}
        self.recorded = []
        self.rolling = rolling
        self.term_fences = sensor.BoundedFences(self.term_fence_result)
        self.term_fences.keep_alive = lambda: None
    def _writer(self):
        return types.SimpleNamespace(_rolling=self.rolling, context={'pid': 1})
    def emit(self, event, payload):
        self.recorded.append((event, payload))


BINDING = {'point': 'GPUModelRunner.execute_model', 'module': sensor.RUNNER, 'source': 'b' * 64, 'body': 'c' * 64}


def test_more_than_eight_sequential_completed_requests_reuse_bounded_tracking():
    observer, owner = Observer(), Owner()
    for index in range(40):
        observer.term_watch_owner(owner, BINDING, (sensor.sha(str(index).encode()),))
        assert len(next(iter(observer.term_tracked.values()))['requests']) <= sensor.MAX_IDS
    assert not any(payload['phase'] == 'OBSERVER_GAP' for _, payload in observer.recorded)
    assert any(payload['phase'] == 'STATE_SNAPSHOT' for _, payload in observer.recorded)


def test_default_finite_observer_retains_original_bound():
    observer, owner = Observer(False), Owner()
    for index in range(9):
        observer.term_watch_owner(owner, BINDING, (sensor.sha(str(index).encode()),))
    assert any(payload['phase'] == 'OBSERVER_GAP' for _, payload in observer.recorded)


def test_active_calls_fences_outputs_and_failed_fences_prevent_reclaim():
    observer, owner = Observer(), Owner()
    selected = tuple(sensor.sha(str(index).encode()) for index in range(6))
    observer.term_watch_owner(owner, BINDING, selected)
    state = next(iter(observer.term_tracked.values()))
    observer.term_calls['active'] = ('call', 0, (selected[0],), None, [], ())
    observer.term_calls['work'] = ('call', 0, (), None, [], (selected[1],))
    observer.term_fences.pending['pending'] = (None, {'selected': (selected[2],)}, 0)
    observer.term_fences.inflight['callback'] = {'selected': (selected[3],)}
    output = Owner()
    observer.term_output_requests[output] = (selected[4],)
    observer.term_mark_unresolved((selected[5],))
    observer.term_prune_owner(state, owner)
    assert state['requests'] == set(selected)
    observer.term_calls.clear()
    observer.term_fences.pending.clear()
    observer.term_fences.inflight.clear()
    observer.term_output_requests.clear()
    observer.term_prune_owner(state, owner)
    assert state['requests'] == {selected[5]}


def test_present_or_unreadable_worker_mapping_cannot_reclaim_ids():
    observer, owner = Observer(), Owner()
    rid = sensor.sha(b'present')
    owner.req_states.req_id_to_index = {'present': 1}
    observer.term_watch_owner(owner, BINDING, (rid,))
    state = next(iter(observer.term_tracked.values()))
    observer.term_prune_owner(state, owner)
    assert rid in state['requests']
    owner.req_states.req_id_to_index = None
    observer.term_prune_owner(state, owner)
    assert rid in state['requests']


def test_fence_callback_is_inflight_until_failure_receipt_is_recorded():
    held = []
    fences = sensor.BoundedFences(lambda phase, facts: held.append(dict(fences.inflight)))
    fences.pending['fence'] = (types.SimpleNamespace(query=lambda: True), {'selected': ('request',)}, 10)
    fences.poll()
    assert held == [{'fence': {'selected': ('request',)}}]
    assert fences.pending == fences.inflight == {}


# Run the reviewed native body with its real PEP669 callback, rather than
# simulating a RETURN callback's frame identity.
from tests.test_request_termination_native23 import world


def test_return_callback_keeps_call_busy_until_observation_finishes(world, monkeypatch):
    observer, modules = world
    runner = object.__new__(modules[sensor.RUNNER].GPUModelRunner)
    runner.model_state = types.SimpleNamespace(remove_request=lambda _: None)
    runner.req_states = types.SimpleNamespace(remove_request=lambda _: None)
    seen = []
    original = observer.term_emit
    def observe(binding, phase, call, started, selected, *args, **kwargs):
        if phase == 'RETURN' and binding['point'] == 'GPUModelRunner._remove_request':
            seen.append(any(state[0] == call for state in observer.term_calls.values()))
        return original(binding, phase, call, started, selected, *args, **kwargs)
    monkeypatch.setattr(observer, 'term_emit', observe)
    assert runner._remove_request('tracked-native-request') is False
    assert seen == [True]
    assert observer.term_calls == {}
