"""Preserve worker-first original exception despite reverse parent consumption."""
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import importlib.util
import json
from pathlib import Path
import threading
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('base_contracts', HERE / 'test_gpu_b_capsule_transfer.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


class FirstFailure(unittest.TestCase):
    setUp = base.Transfer.setUp
    tearDown = base.Transfer.tearDown
    get = base.Transfer.get
    # Inherit the existing setup/cleanup, not its unchanged test methods.
    def test_two_workers_keep_first_object_and_traceback_when_parent_reads_second_first(self):
        first = RuntimeError('EXAMPLE-first-worker-failure')
        second = ValueError('EXAMPLE-second-worker-failure')
        first_done, second_started = threading.Event(), threading.Event()
        worker_tracebacks, consumed = {}, []
        self.sdk.content = b'x' * (self.original.CHUNK * 2)
        original_pool, original_wait = base.TRANSPORT.ThreadPoolExecutor, base.TRANSPORT.wait

        class ObservedPool(ThreadPoolExecutor):
            def submit(pool, fn, offset):
                future = super().submit(fn, offset)
                def completed(done):
                    error = done.exception()
                    tb = error.__traceback__
                    while tb is not None and tb.tb_frame.f_code.co_name != 'one':
                        tb = tb.tb_next
                    worker_tracebacks[offset] = (error, tb)
                    if offset == 0:
                        first_done.set()
                future.add_done_callback(completed)
                return future

        def fail_first():
            raise first

        def poll(cid):
            args = self.sdk.sent[cid]
            if args['op'] != 'read':
                return
            if args['offset'] == 0:
                self.assertTrue(second_started.wait(5), 'both workers must already be in flight')
                fail_first()
            second_started.set()
            self.assertTrue(first_done.wait(5), 'first worker must finish before second raises')
            raise second

        def reverse_parent(pending, *, return_when):
            done, remaining = wait(pending, timeout=10, return_when=ALL_COMPLETED)
            self.assertFalse(remaining, 'both workers must terminate')
            self.assertEqual({f.exception() for f in done}, {first, second})
            ordered = sorted(done, key=lambda f: 0 if f.exception() is second else 1)
            consumed.extend(type(f.exception()).__name__ for f in ordered)
            return ordered, remaining

        self.sdk.before_poll = poll
        base.TRANSPORT.ThreadPoolExecutor, base.TRANSPORT.wait = ObservedPool, reverse_parent
        try:
            try:
                self.get()
            except BaseException as caught:
                self.assertIs(caught, first)
                chain = []
                tb = caught.__traceback__
                while tb is not None:
                    chain.append(tb)
                    tb = tb.tb_next
                self.assertTrue(any(tb is worker_tracebacks[0][1] for tb in chain))
                self.assertEqual(chain[-1].tb_frame.f_code.co_name, 'fail_first')
            else:
                self.fail('independent worker failures must fail the get')
        finally:
            base.TRANSPORT.ThreadPoolExecutor, base.TRANSPORT.wait = original_pool, original_wait
        self.assertEqual(consumed, ['ValueError', 'RuntimeError'])
        incomplete = json.loads((self.transfer.records / 'get-incomplete.json').read_bytes())
        self.assertEqual(incomplete['message'], str(first))
        self.assertEqual(incomplete['written_ranges'], [])
        self.assertFalse((self.transfer.records / 'get-complete.json').exists())
        self.assertEqual((self.path / 'original.zip').stat().st_size, len(self.sdk.content))
        exceptions = [json.loads(p.read_bytes()) for p in self.transfer.records.glob('*.exception.json')]
        self.assertEqual({r['message'] for r in exceptions}, {str(first), str(second)})
        accepted = [json.loads(p.read_bytes()) for p in self.transfer.records.glob('*.accepted.json')]
        self.assertEqual(len(accepted), 3)  # stat and two admitted reads, never a new range after FAIL
        self.assertEqual(self.sdk.stat_count, 1)
        self.assertFalse(list(self.transfer.records.glob('*.publishing')))

