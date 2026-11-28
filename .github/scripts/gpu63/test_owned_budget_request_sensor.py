from collections import defaultdict
from dataclasses import dataclass
import os
import sys
import threading
from types import SimpleNamespace
import weakref

import pytest

from owned_budget_request_sensor import Observer, PendingCalls, block_changes, owned_block_token, snapshot, weak_block_counts


class Pool:
    pass


@dataclass(slots=True)
class Block:
    block_id: int
    ref_cnt: int
    pool: Pool
    is_null: bool = False


def test_lookup_never_creates_an_entry_and_deduplicates_the_same_physical_pool_reference():
    pool = Pool()
    block = Block(1, 1, pool)
    pool.blocks = [Block(0, 0, pool, is_null=True), block]
    mapping = defaultdict(list, {'known': [block, pool.blocks[0]]})
    first_group = SimpleNamespace(req_to_blocks=mapping)
    other_group = SimpleNamespace(req_to_blocks={'known': [block]})
    manager = SimpleNamespace(coordinator=SimpleNamespace(single_type_managers=(first_group, other_group)))
    assert snapshot(manager, 'missing') == [[], []] and 'missing' not in mapping
    key = bytes(32)
    watches = weak_block_counts(snapshot(manager, 'known'), key)
    token = owned_block_token(key, id(pool), 1, id(block))
    assert len(watches) == 1
    block.ref_cnt = 0
    assert block_changes(watches) == [{'before_refcount': 1, 'after_refcount': 0, 'block_hmac_sha256': token}]
    pool.blocks[1] = Block(1, 0, pool)
    assert block_changes(watches) == [{'before_refcount': 1, 'after_refcount': None, 'block_hmac_sha256': token}]


def test_pending_call_rejects_the_same_frame_from_another_thread():
    current = sys._getframe()
    calls = PendingCalls()
    calls.record(current.f_code, current, (1, 2, 'owned'))
    foreign = []
    worker = threading.Thread(target=lambda: foreign.append(calls.take(current.f_code, current)))
    worker.start()
    worker.join()
    assert foreign == [None]
    assert calls.take(current.f_code, current)[0] == (1, 2, 'owned')


def manager_with_reference(initial):
    pool = Pool()
    block = Block(0, initial, pool)
    pool.blocks = [block]
    group = SimpleNamespace(req_to_blocks={'owned': [block]})
    manager = SimpleNamespace(coordinator=SimpleNamespace(single_type_managers=(group,)))
    return manager, block


@pytest.mark.skipif(not hasattr(sys, 'monitoring'), reason='requires the installed Python monitoring API')
def test_same_function_nested_calls_unwind_failed_entry_and_changed_arguments():
    receipts = []
    failures = []
    outer, outer_block = manager_with_reference(1)
    inner, inner_block = manager_with_reference(2)
    failing, failed_block = manager_with_reference(3)
    request = SimpleNamespace(request_id='owned')
    other_request = SimpleNamespace(request_id='owned')
    broken = SimpleNamespace()

    def operation(self, request, mode=None):
        if mode == 'failed operation':
            raise ZeroDivisionError
        if mode == 'entry failed':
            return
        if mode == 'changed request':
            request = other_request
        if mode == 'changed owner':
            self = inner
        if mode == 'nested':
            for target, step in ((failing, 'failed operation'), (broken, 'entry failed'), (inner, None)):
                try:
                    operation(target, request, step)
                except ZeroDivisionError:
                    assert not any(entry[1][0] == id(failing) for entry in observer.calls.entries.values())
        group, = self.coordinator.single_type_managers
        group.req_to_blocks.pop(request.request_id)[0].ref_cnt = 0

    observer = Observer.__new__(Observer)
    observer.pid = os.getpid()
    observer.key = bytes(32)
    observer.emitter, observer.installer = object(), object()
    observer.calls = PendingCalls()
    observer.active = {'owned': {'case_id': 'normal', 'native_request_hmac_sha256': 'owned'}}
    observer.methods = {operation.__code__: 'free'}
    observer.member = lambda row, field, _expected: getattr(row, field)
    observer.event = lambda point, **body: receipts.append(body | {'point': point})
    observer.error = lambda code, error: failures.append((code.co_name, type(error).__name__))
    monitor = sys.monitoring
    tool = next(slot for slot in (5, 4, 3) if monitor.get_tool(slot) is None)
    events = monitor.events
    monitor.use_tool_id(tool, 'owned budget regression')
    try:
        monitor.register_callback(tool, events.PY_START, observer.enter)
        monitor.register_callback(tool, events.PY_RETURN, observer.returned)
        monitor.register_callback(tool, events.PY_UNWIND, observer.unwound)
        monitor.set_events(tool, events.PY_UNWIND)
        monitor.set_local_events(tool, operation.__code__, events.PY_START | events.PY_RETURN)
        operation(outer, request, 'nested')
        assert failures == [('operation', 'AttributeError')]
        counters = [r['before_reference_counters'][0] for r in receipts]
        assert [(c['before_refcount'], c['after_refcount']) for c in counters] == [(2, 0), (1, 0)]
        assert counters[0]['block_hmac_sha256'] != counters[1]['block_hmac_sha256']
        assert outer_block.ref_cnt == inner_block.ref_cnt == 0 and failed_block.ref_cnt == 3
        assert not observer.calls.entries

        replacement, _ = manager_with_reference(1)
        operation(replacement, request, 'changed request')
        inner, _ = manager_with_reference(1)
        operation(replacement, request, 'changed owner')
        assert len(receipts) == 2 and not observer.calls.entries
    finally:
        monitor.set_events(tool, 0)
        monitor.set_local_events(tool, operation.__code__, 0)
        for event in (events.PY_START, events.PY_RETURN, events.PY_UNWIND):
            monitor.register_callback(tool, event, None)
        monitor.free_tool_id(tool)


def test_pending_entry_metadata_does_not_keep_the_request_object_alive():
    class Request:
        pass

    request = Request()
    reference = weakref.ref(request)
    calls = PendingCalls()
    frame = sys._getframe()
    calls.record(frame.f_code, frame, (0, id(request), 'owned'))
    del request
    assert reference() is None


def test_scheduler_uses_its_receiver_and_initial_owned_set_not_loop_locals():
    observer = Observer.__new__(Observer)
    code = object()
    observer.emitter, observer.installer = object(), object()
    observer.calls = PendingCalls()
    observer.methods = {code: 'schedule'}
    observer.active = {'owned': {'native_request_hmac_sha256': 'initial'}}
    seen = []
    observer.scheduled = lambda output, point, stamp, eligible: seen.append((output, point, eligible))
    owner = object()
    frame = SimpleNamespace(f_locals={'self': owner})
    observer.observe_entry(code, frame)
    frame.f_locals['request'] = object()  # A variable in the scheduler's loop.
    observer.active['later'] = {'native_request_hmac_sha256': 'later'}
    observer.observe_return(code, frame, 'selection')
    assert seen == [('selection', 'schedule', frozenset({'initial'}))]
    observer.observe_entry(code, frame)
    frame.f_locals['self'] = object()
    observer.observe_return(code, frame, 'other receiver')
    assert len(seen) == 1 and not observer.calls.entries
