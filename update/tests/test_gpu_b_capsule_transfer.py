"""New direct contracts only; portable fixtures, no private output or AWS."""
import base64
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest

PUBLIC = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / 'fixtures' / 'gpu-b-sup16'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAP = load(PUBLIC / 'tools/gpu_b_failure_capsule.py', 'capsule_contract')
TRANSPORT = load(PUBLIC / 'tools/gpu_b_transfer_contract.py', 'transfer_contract')
WIRE = load(FIX / 'original_wire.py', 'original_wire_fixture')


class Capsule(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='byul-capsule-')
        self.path = Path(self.temp.name)
        self.old_umask = os.umask(0o077)
        self.cli = load(FIX / 'original_cli.py', 'original_cli_fixture')
        self.observer = CAP.FirstFailure()
        self.observer.attach(self.cli)
        self.rows = []
        self.binding = dict(boot_id='EXAMPLE-CPU-ONLY', configuration_sha256='1' * 64)

    def tearDown(self):
        os.umask(self.old_umask)
        self.temp.cleanup()

    def failed_command(self):
        argv = [sys.executable, '-I', '-S', '-B', '-c',
                "import sys;print('original stdout');print('first original error',file=sys.stderr);raise SystemExit(7)"]
        try:
            self.cli.call_cli('199-resolve-capture', argv, self.path, self.path,
                              dict(os.environ), self.rows, timeout=30)
        except ValueError as error:
            return error
        self.fail('original disallowed returncode did not fail')

    def publish(self):
        original_error = self.failed_command()
        self.assertEqual(str(original_error), 'GB49_CLI_FAILED:199-resolve-capture')
        destination = self.path / 'first-failure-capsule.json'
        receipt = self.observer.write(destination, binding=self.binding, owner=os.geteuid())
        return destination, receipt

    def test_actual_call_cli_to_capsule_to_original_wire_consumer(self):
        path, receipt = self.publish()
        raw = path.read_bytes()
        capsule = CAP.consume(raw)
        self.assertEqual(capsule['cli_row'], self.rows[0])
        self.assertEqual(base64.b64decode(capsule['channels']['stderr']['raw_base64']), b'first original error\n')
        self.assertEqual(receipt['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertTrue(self.rows[0]['child_reaped'] and self.rows[0]['group_terminal'])
        wrapper = dict(self.binding, exception_type='ValueError',
                       exception_message='GB49_CLI_FAILED:199-resolve-capture',
                       actual_children=[{k: self.rows[0][k] for k in ('pid', 'returncode', 'group_terminal')}])
        wrapper_raw = json.dumps(wrapper, indent=2).encode()
        result_raw = json.dumps({'wrapper': wrapper, 'archive_receipt': None}).encode()
        sources = {WIRE.WRAPPER_PATH: dict(bytes=len(wrapper_raw), sha256=hashlib.sha256(wrapper_raw).hexdigest(),
                                          raw_base64=base64.b64encode(wrapper_raw).decode())}
        old_wire = WIRE.pack(result_raw, sources)
        wire = CAP.pack_wire(WIRE.pack, result_raw, sources, capsule_raw=raw, capsule_path='/EXAMPLE/first-failure-capsule.json')
        self.assertEqual(json.loads(wire)['original_wire'], old_wire)
        bundle, observed = CAP.unpack_wire(WIRE.unpack_bundle, wire, capsule_path='/EXAMPLE/first-failure-capsule.json')
        self.assertEqual(base64.b64decode(bundle['source_files'][WIRE.WRAPPER_PATH]['raw_base64']), wrapper_raw)
        self.assertEqual(observed['value']['cli_row'], self.rows[0])
        self.assertFalse(observed['value']['product_complete_claimed'])
        linked = CAP.bind_wire_consumer(WIRE.unpack_bundle, WIRE.WRAPPER_PATH)
        self.assertEqual(linked(wire), bundle)
        self.assertTrue(linked.first_failure_capsule['validated'])
        damaged = json.loads(wire)
        damaged['capsule_sha256'] = '0' * 64
        self.assertEqual(linked(json.dumps(damaged)), bundle)
        self.assertFalse(linked.first_failure_capsule['validated'])
        errors = []
        fallback = CAP.pack_wire(WIRE.pack, result_raw, sources, capsule_raw=b'not JSON',
                                capsule_path='/EXAMPLE/first-failure-capsule.json', diagnostic=errors.append)
        self.assertEqual(fallback, old_wire)
        self.assertEqual(len(errors), 1)

    def test_success_creates_no_failure_or_capsule(self):
        self.cli.call_cli('normal-control', [sys.executable, '-I', '-S', '-B', '-c', 'print(1)'],
                          self.path, self.path, dict(os.environ), self.rows, timeout=30)
        self.assertIsNone(self.observer.write(self.path / 'absent.json', binding=self.binding, owner=os.geteuid()))
        self.assertFalse((self.path / 'absent.json').exists())

    def test_original_exception_object_survives_observation_failure(self):
        original = RuntimeError('original error object')
        class Broken:
            @staticmethod
            def call_cli(*args, **kwargs):
                raise original
        observer = CAP.FirstFailure()
        observer.attach(Broken)
        with self.assertRaises(RuntimeError) as caught:
            Broken.call_cli('missing-row', [], self.path, self.path, {}, [])
        self.assertIs(caught.exception, original)
        self.assertIn('First failure metadata unavailable', original.__notes__[0])

    def test_same_command_raw_hash_mismatch_is_rejected(self):
        self.failed_command()
        (self.path / '199-resolve-capture.stderr').write_bytes(b'changed original')
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_CHANNEL_SHA'):
            self.observer.write(self.path / 'capsule.json', binding=self.binding, owner=os.geteuid())

    def test_nonterminal_failed_child_is_not_promoted(self):
        self.failed_command()
        self.observer.failure['row']['group_terminal'] = False
        with self.assertRaisesRegex(ValueError, 'FAILED_CHILD_NOT_TERMINAL'):
            self.observer.write(self.path / 'capsule.json', binding=self.binding, owner=os.geteuid())

    def test_private_capsule_publication_never_overwrites(self):
        path, _ = self.publish()
        original = path.read_bytes()
        with self.assertRaises(FileExistsError):
            self.observer.write(path, binding=self.binding, owner=os.geteuid())
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(list(self.path.glob('*.publishing')))

    def test_capsule_source_links_and_mode_are_rejected(self):
        self.failed_command()
        path = self.path / '199-resolve-capture.stderr'
        path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'IDENTITY_MODE_OR_BOUND'):
            self.observer.write(self.path / 'capsule.json', binding=self.binding, owner=os.geteuid())
        path.chmod(0o600)
        moved = self.path / 'private-original.stderr'
        path.rename(moved)
        path.symlink_to(moved.name)
        with self.assertRaisesRegex(ValueError, 'INPUT_LINK'):
            self.observer.write(self.path / 'capsule.json', binding=self.binding, owner=os.geteuid())

    def test_current_boot_binding_and_diagnostic_bytes_are_checked(self):
        path, _ = self.publish()
        raw = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'CURRENT_WRAPPER_BINDING'):
            CAP.consume(raw, expected_binding={'boot_id': 'different'})
        value = json.loads(raw)
        value['channels']['stderr']['raw_base64'] = base64.b64encode(b'not original').decode()
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_CHANNEL_BINDING'):
            CAP.consume(json.dumps(value).encode())

    def test_context_comes_only_from_original_pinned_failed_command_input(self):
        self.failed_command()
        raw = b'{"context":"EXAMPLE-CPU-context","source_commit":"EXAMPLE-not-approved"}'
        path = self.path / 'inputs.json'
        path.write_bytes(raw)
        path.chmod(0o600)
        self.observer.failure['row']['argv'] += ['--inputs', str(path), '--inputs-sha256', hashlib.sha256(raw).hexdigest()]
        destination = self.path / 'capsule.json'
        self.observer.write(destination, binding=self.binding, owner=os.geteuid(), input_owner=os.geteuid())
        value = CAP.consume(destination.read_bytes())
        self.assertEqual(value['binding']['context'], 'EXAMPLE-CPU-context')
        self.assertEqual(value['binding']['input_pointer']['file_sha256'], hashlib.sha256(raw).hexdigest())

    def test_bad_optional_input_does_not_erase_first_stderr(self):
        self.failed_command()
        self.observer.failure['row']['argv'] += ['--inputs', str(self.path / 'absent.json'), '--inputs-sha256', '0' * 64]
        destination = self.path / 'capsule.json'
        self.observer.write(destination, binding=self.binding, owner=os.geteuid(), input_owner=os.geteuid())
        value = CAP.consume(destination.read_bytes())
        self.assertEqual(value['binding']['input_context_status'], 'unavailable')
        self.assertEqual(base64.b64decode(value['channels']['stderr']['raw_base64']), b'first original error\n')


class Clock:
    def __init__(self):
        self.value = 0.0
        self.lock = threading.Lock()
    def now(self):
        with self.lock:
            return self.value
    def advance(self, seconds):
        with self.lock:
            self.value += seconds


class SDK:
    """Transport seam only: no remote source, engine, role or AWS is executed."""
    def __init__(self, content=b'new boundary bytes'):
        self.content, self.sent, self.polls = content, {}, []
        self.lock = threading.Lock()
        self.before_poll = None
        self.response_override = None
        self.send_error = None
        self.after_send = None
        self.after_read = None
        self.read_count = 0
        self.identity_changed = False
        self.stat_count = 0
    def send_command(self, **request):
        if self.send_error:
            raise self.send_error
        self.assert_request(request)
        args = json.loads(base64.b64decode(shlex.split(request['Parameters']['commands'][0])[-1]))
        with self.lock:
            cid = 'example-command-%d' % (len(self.sent) + 1)
            self.sent[cid] = args
        if self.after_send:
            self.after_send()
        return {'Command': {'CommandId': cid}, 'ResponseMetadata': {'RequestId': 'EXAMPLE-API-REQUEST'}}
    @staticmethod
    def assert_request(request):
        if request['TimeoutSeconds'] != 120:
            raise AssertionError('original send timeout changed')
    def get_command_invocation(self, **request):
        cid = request['CommandId']
        if self.before_poll:
            self.before_poll(cid)
        with self.lock:
            self.polls.append(cid)
            args = self.sent[cid]
        if self.response_override:
            return deepcopy(self.response_override)
        if args['op'] == 'stat':
            self.stat_count += 1
            result = {'identity': [1, 2, len(self.content), 4 + int(self.identity_changed and self.stat_count > 1)],
                      'sha256': hashlib.sha256(self.content).hexdigest()}
        else:
            self.read_count += 1
            offset, length = args['offset'], args['length']
            result = {'offset': offset, 'data': base64.b64encode(self.content[offset:offset + length]).decode()}
            if self.after_read:
                self.after_read()
        return {'Status': 'Success', 'ResponseCode': 0,
                'StandardOutputContent': json.dumps(result), 'StandardErrorContent': ''}


class Transfer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='byul-transfer-')
        self.path = Path(self.temp.name)
        self.old_umask = os.umask(0o077)
        self.original = load(FIX / 'original_transport.py', 'original_transport_fixture')
        self.sdk, self.clock = SDK(), Clock()
        self.transfer = TRANSPORT.bind_transfer(self.original)(self.sdk, self.path / 'copy.jsonl',
            deadline=900, clock=self.clock.now, sleep=self.clock.advance)
    def tearDown(self):
        self.transfer.close()
        os.umask(self.old_umask)
        self.temp.cleanup()
    def get(self):
        return self.transfer.get('GB99-original-evidence.zip', self.path / 'original.zip',
            expected_size=len(self.sdk.content), expected_sha256=hashlib.sha256(self.sdk.content).hexdigest())

    def test_actual_getter_writes_verified_ranges_and_final_identity(self):
        self.sdk.content = b'new bounded contract\n' * 1800
        result = self.get()
        self.assertEqual((self.path / 'original.zip').read_bytes(), self.sdk.content)
        final = json.loads((self.transfer.records / 'get-complete.json').read_bytes())
        self.assertTrue(final['complete'] and final['remote_identity_rechecked'])
        self.assertEqual(sum(r['length'] for r in final['written_ranges']), result['size'])
        self.assertTrue(all(r['pwrite_succeeded'] for r in final['written_ranges']))

    def test_accepted_response_is_durable_before_first_poll(self):
        def verify(cid):
            receipts = [json.loads(p.read_bytes()) for p in self.transfer.records.glob('*.accepted.json')]
            self.assertTrue(any(r['command_id'] == cid and r['response']['Command']['CommandId'] == cid for r in receipts))
        self.sdk.before_poll = verify
        self.get()

    def test_success_status_with_bad_json_preserves_exact_stdout_and_stderr(self):
        self.sdk.response_override = dict(Status='Success', ResponseCode=0,
            StandardOutputContent='{"broken":', StandardErrorContent='original diagnostic\n')
        with self.assertRaises(json.JSONDecodeError):
            self.get()
        self.assertEqual((self.transfer.records / '000001.stdout').read_bytes(), b'{"broken":')
        self.assertEqual((self.transfer.records / '000001.stderr').read_bytes(), b'original diagnostic\n')
        self.assertTrue((self.transfer.records / '000001.accepted.json').exists())

    def test_original_terminal_failure_is_not_converted_to_success(self):
        self.sdk.response_override = dict(Status='Failed', ResponseCode=7,
            StandardOutputContent='original partial', StandardErrorContent='original failure')
        with self.assertRaisesRegex(RuntimeError, 'SSM command failed'):
            self.get()
        self.assertEqual((self.transfer.records / '000001.stderr').read_bytes(), b'original failure')

    def test_send_timeout_preserves_exception_object_and_partial_bytes(self):
        error = subprocess.TimeoutExpired('EXAMPLE SDK seam', 65, output=b'partial API JSON', stderr=b'original API error')
        self.sdk.send_error = error
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            self.get()
        self.assertIs(caught.exception, error)
        self.assertEqual((self.transfer.records / '000001.exception.stdout').read_bytes(), b'partial API JSON')
        record = json.loads((self.transfer.records / '000001.exception.json').read_bytes())
        self.assertIsNone(record['command_id'])
        self.assertFalse((self.transfer.records / '000001.accepted.json').exists())

    def test_last_seconds_do_not_issue_new_send(self):
        self.clock.advance(897)
        with self.assertRaisesRegex(TimeoutError, 'NO_NEW_SEND'):
            self.get()
        self.assertFalse(self.sdk.sent)
        self.assertFalse((self.path / 'original.zip').exists())
        record = json.loads((self.transfer.records / '000001.not-issued.json').read_bytes())
        self.assertIs(record['issued'], False)

    def test_accepted_nonterminal_request_remains_unresolved_at_budget_tail(self):
        self.sdk.response_override = dict(Status='InProgress', ResponseCode=-1,
            StandardOutputContent='last actual observation', StandardErrorContent='')
        self.clock.advance(715)
        self.sdk.after_send = lambda: self.clock.advance(65)
        self.sdk.before_poll = lambda _: self.clock.advance(60)
        with self.assertRaisesRegex(TimeoutError, 'REQUIRES_RECONCILIATION'):
            self.get()
        self.assertEqual(len(self.sdk.sent), 1)
        record = json.loads((self.transfer.records / '000001.accepted.json').read_bytes())
        self.assertFalse(record['server_terminal'])
        self.assertEqual((self.transfer.records / '000001.stdout').read_bytes(), b'last actual observation')

    def test_budget_stop_retains_partial_ranges_without_enqueuing_all(self):
        self.sdk.content = b'x' * (12000 * 12)
        self.sdk.after_read = lambda: self.clock.advance(800)
        with self.assertRaises(TimeoutError):
            self.get()
        self.assertLessEqual(self.sdk.read_count, self.original.GET_WORKERS)
        self.assertLess(self.sdk.read_count, 12)
        partial = json.loads((self.transfer.records / 'get-incomplete.json').read_bytes())
        self.assertFalse(partial['complete'])
        self.assertEqual((self.path / 'original.zip').stat().st_size, len(self.sdk.content))
        self.assertLess(sum(r['length'] for r in partial['written_ranges']), len(self.sdk.content))
        self.assertFalse((self.transfer.records / 'get-complete.json').exists())

    def test_final_identity_change_retains_raw_and_original_failure(self):
        self.sdk.identity_changed = True
        with self.assertRaisesRegex(ValueError, 'source changed or copied hash mismatch'):
            self.get()
        self.assertEqual((self.path / 'original.zip').read_bytes(), self.sdk.content)
        self.assertTrue((self.transfer.records / 'get-incomplete.json').exists())
        self.assertFalse((self.transfer.records / 'get-complete.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
