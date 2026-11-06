"""Fake-only trials, including report -> status -> safety compatibility."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import tetragon_mode, tetragon_trial as trial_module
from tools.tetragon_trial import KindTrial, PROBE_PATH
from tools.tetragon_status import StatusCollector
from serving_runtime.enforcement.intent import parse_intent
from serving_runtime.enforcement.tetragon import comparable_spec
from serving_runtime.mvp.tetragon_state import read_tetragon_state
from serving_runtime.operations.safety import evaluate

AT = '2026-11-06T01:00:00+00:00'
CID = 'a' * 64
MACHINE_ID = '0123456789abcdef0123456789abcdef'
STAT = '42 (python) ' + ' '.join(['S'] + ['0'] * 18 + ['1000']) + '\n'
SAMPLE = STAT + 'pid:[123]\nNSpid:\t42\t1\n100\n'


def result(value='', code=0):
    return subprocess.CompletedProcess([], code, json.dumps(value) if isinstance(value, dict) else value,
        'sensitive diagnostic https://private.invalid token=PRIVATE' if code else '')


class FakeRunner:
    """Unexpected commands fail immediately; no subprocess or cluster is used."""
    def __init__(self, node='aws-node', fail=None, local_node=None,
                 machine_id=MACHINE_ID, local_machine_id=MACHINE_ID):
        self.node, self.fail = node, fail
        self.local_node = local_node or node
        self.machine_id, self.local_machine_id = machine_id, local_machine_id
        self.calls, self.manifests = [], {}
        self.mode, self.stream, self.policy, self.namespace = 'monitor', None, None, None

    def pod(self):
        return {'metadata': {'uid': 'pod-uid'}, 'spec': {'nodeName': self.node},
            'status': {'containerStatuses': [{'name': 'probe', 'containerID': 'containerd://' + CID,
                'restartCount': int(self.mode == 'enforce'),
                'lastState': {'terminated': {'exitCode': 137, 'signal': 9, 'reason': 'Error', 'finishedAt': AT}}}]}}

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if command[0] in ('sudo', 'docker'):
            if self.fail and self.fail in command: return result(code=1)
            args = command[2:] if command[0] == 'sudo' else command[3:]
            if args == ['uname', '-n']: return result(self.local_node + '\n')
            if args == ['cat', '/etc/machine-id']: return result(self.local_machine_id)
            if args == ['uname', '-r']: return result('test-kernel\n')
            if args == ['crictl', 'inspect', CID]: return result({'info': {'pid': 42}})
            if args[:2] == ['sh', '-c']: return result(SAMPLE)
            if args == ['cat', '/proc/42/stat']: return result(STAT)
            if args == ['readlink', '/proc/42/ns/pid']: return result('pid:[123]\n')
            if args == ['grep', '^NSpid:', '/proc/42/status']: return result('NSpid:\t42\t1\n')
            if args == ['getconf', 'CLK_TCK']: return result('100\n')
            raise AssertionError(command)
        if command[0] != 'kubectl': raise AssertionError(command)
        if command[3:] == ['get', 'node', self.node, '-o', 'jsonpath={.status.nodeInfo.machineID}']:
            return result(self.machine_id, code=int(self.fail == 'machineID'))
        args = command[5:]
        if args[:1] in (['apply'], ['create']):
            value = json.loads(Path(args[2]).read_text())
            self.manifests[value['kind']] = value
            if value['kind'] == 'Namespace': self.namespace = value['metadata']['name']
            if value['kind'] == 'TracingPolicyNamespaced': self.policy = value['metadata']['name']
            return result()
        if args[:2] == ['get', 'pods']:
            return result({'items': [{'metadata': {'uid': 'agent-uid'}, 'status': {'containerStatuses': [
                {'name': 'tetragon', 'ready': True, 'restartCount': 0}]}}]})
        if args[:2] == ['get', 'pod']: return result(self.pod())
        if args[:2] == ['get', 'tracingpolicynamespaced']: return result(self.manifests['TracingPolicyNamespaced'])
        if args[0] in ('wait', 'delete'): return result()
        if 'tracingpolicy' in args:
            return result(f'ID NAME STATE NAMESPACE MODE\n7 {self.policy} enabled {self.namespace} {self.mode}\n')
        if 'set-mode' in args:
            self.mode = args[-1]
            return result()
        if 'list' in args:
            return result({'policies': [{'name': self.policy, 'namespace': self.namespace, 'id': 7, 'state': 'TP_STATE_ENABLED'}]})
        if 'ps' in args: return result()
        if 'python' in args:
            if args[-1] == '/probe' and self.stream:
                event = {'time': AT, 'process_kprobe': {'policy_name': self.policy, 'function_name': 'fd_install',
                    'action': 'KPROBE_ACTION_SIGKILL' if self.mode == 'enforce' else 'KPROBE_ACTION_POST',
                    'process': {'pid': 42}, 'args': [{'file_arg': {'path': PROBE_PATH}}],
                    'data': [{'label': 'proof_host_pid', 'int_arg': 42},
                             {'label': 'proof_start_ns', 'size_arg': '10005000000'},
                             {'label': 'proof_pidns', 'uint_arg': 123}]}}
                self.stream.write(json.dumps(event) + '\n')
                self.stream.flush()
            if self.mode == 'enforce': return result({'transport_error': 'RemoteDisconnected'}, code=2)
            return result({'read': 'ok', 'pid': 1} if args[-1] == '/probe' else {'ready': True, 'pid': 1})
        raise AssertionError(command)

    def popen(self, command, *, stdout, stderr):
        self.calls.append(command)
        self.stream = stdout
        return SimpleNamespace(poll=lambda: None, terminate=lambda: None, wait=lambda **kw: 0)


def execute_fake(root, runner, module=trial_module, **kwargs):
    parse_row = tetragon_mode.parse_policy_row
    def stable_row(*args):
        row = parse_row(*args)
        return dict(sorted(row.items())) if row else row
    # parse_policy_row iterates a set; fix its field order across hash seeds.
    with patch.object(module, 'run', runner), patch.object(module, 'now', return_value=AT), \
         patch.object(module, 'uuid4', return_value=SimpleNamespace(hex='1234567890abcdef')), \
         patch.object(module.time, 'sleep'), patch.object(module.subprocess, 'Popen', runner.popen), \
         patch.object(tetragon_mode, 'parse_policy_row', side_effect=stable_row):
        trial = module.KindTrial(root, 'sigkill_fd_install', **kwargs)
        report = trial.execute()
    return trial, report


class TrialTests(unittest.TestCase):
    def test_local_sudo_exact_commands_and_identity(self):
        runner = FakeRunner()
        trial = KindTrial(Path('/unused'), 'sigkill_fd_install', node_access='local-sudo', runner=runner)
        identity = trial.task_identity(runner.pod(), runner.node)
        kernel = trial.node_access.kernel(runner.node)
        self.assertEqual(identity, {'pod_uid': 'pod-uid', 'namespace': trial.namespace, 'container_id': CID,
            'node_pid': 42, 'ns_pid': 1, 'pidns': 123, 'start_ticks': 1000, 'clock_ticks': 100})
        self.assertEqual(kernel.stdout.strip(), 'test-kernel')
        binding = [
            ['kubectl', '--context', 'kind-runtime-lab', 'get', 'node', runner.node,
             '-o', 'jsonpath={.status.nodeInfo.machineID}'],
            ['sudo', '-n', 'cat', '/etc/machine-id']]
        self.assertEqual(runner.calls, [
            *binding, ['sudo', '-n', 'crictl', 'inspect', CID],
            ['sudo', '-n', 'cat', '/proc/42/stat'], ['sudo', '-n', 'readlink', '/proc/42/ns/pid'],
            ['sudo', '-n', 'grep', '^NSpid:', '/proc/42/status'], ['sudo', '-n', 'getconf', 'CLK_TCK'],
            *binding, ['sudo', '-n', 'uname', '-r']])

    def test_local_machine_id_mismatch_refused_before_cri(self):
        runner = FakeRunner(local_machine_id='f' * 32)
        trial = KindTrial(Path('/unused'), 'sigkill_fd_install', node_access='local-sudo', runner=runner)
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            trial.task_identity(runner.pod(), runner.node)
        self.assertEqual(len(runner.calls), 2)
        self.assertFalse(any('inspect' in cmd for cmd in runner.calls))
        with self.assertRaisesRegex(RuntimeError, 'node changed'):
            trial.task_identity(runner.pod(), 'other-node')
        self.assertEqual(len(runner.calls), 2)

    def test_different_hostname_with_matching_machine_id_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner(node='byul-gpu-01', local_node='ip-10-77-1-29',
                                machine_id=' \n' + MACHINE_ID + '\t', local_machine_id=MACHINE_ID + '\n')
            _, report = execute_fake(Path(directory), runner, node_access='local-sudo', context='byul-aws-lab')
            self.assertEqual(report['verification']['status'], 'passed', report)
            self.assertNotIn(['sudo', '-n', 'uname', '-n'], runner.calls)
            self.assertIn(['kubectl', '--context', 'byul-aws-lab', 'get', 'node', runner.node,
                           '-o', 'jsonpath={.status.nodeInfo.machineID}'], runner.calls)

    def test_invalid_or_unreadable_machine_id_refused_without_values(self):
        for side in ('machine_id', 'local_machine_id'):
            for value in ('', 'a' * 31, 'a' * 33, MACHINE_ID.upper(), 'g' * 32, 'PRIVATE'):
                with self.subTest(side=side, value=value):
                    runner = FakeRunner(**{side: value})
                    trial = KindTrial(Path('/unused'), 'sigkill_fd_install', node_access='local-sudo', runner=runner)
                    with self.assertRaises(RuntimeError) as raised:
                        trial.task_identity(runner.pod(), runner.node)
                    self.assertNotIn(MACHINE_ID, str(raised.exception))
                    if value: self.assertNotIn(value, str(raised.exception))
                    self.assertFalse(any('inspect' in cmd for cmd in runner.calls))
        for failure in ('machineID', '/etc/machine-id'):
            runner = FakeRunner(fail=failure)
            trial = KindTrial(Path('/unused'), 'sigkill_fd_install', node_access='local-sudo', runner=runner)
            with self.subTest(failure=failure), self.assertRaises(RuntimeError):
                trial.task_identity(runner.pod(), runner.node)
            self.assertFalse(any('inspect' in cmd for cmd in runner.calls))

    def test_machine_id_read_oserror_does_not_leak(self):
        for executable in ('kubectl', 'sudo'):
            runner = FakeRunner()
            def fake(command, **kwargs):
                if command[0] == executable: raise OSError('PRIVATE')
                return runner(command, **kwargs)
            trial = KindTrial(Path('/unused'), 'sigkill_fd_install', node_access='local-sudo', runner=fake)
            with self.subTest(executable=executable), self.assertRaises(RuntimeError) as raised:
                trial.task_identity(runner.pod(), runner.node)
            self.assertNotIn('PRIVATE', str(raised.exception))

    def test_local_sudo_failures_never_pass_and_cleanup_runs(self):
        for failure in ('machineID', '/etc/machine-id', '-n', 'inspect', 'cat', 'readlink', 'grep', 'getconf', '-r'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                runner = FakeRunner(fail=failure)
                trial, report = execute_fake(Path(directory), runner, node_access='local-sudo', context='byul-aws-lab')
                self.assertIn(report['verification']['status'], ('unknown', 'failed'))
                self.assertTrue(report['facts']['cleanup_ok'])
                self.assertNotIn('token=', json.dumps(report))
                self.assertNotIn('private.invalid', json.dumps(report))
                self.assertTrue((Path(directory) / (trial.namespace + '.verification.json')).exists())
                self.assertNotIn('TracingPolicyNamespaced', runner.manifests)

    def test_local_machine_id_mismatch_report_is_incomplete_and_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner(local_machine_id='f' * 32)
            _, report = execute_fake(Path(directory), runner, node_access='local-sudo')
            self.assertEqual(report['verification']['status'], 'unknown')
            self.assertTrue(report['facts']['cleanup_ok'])
            self.assertNotIn('task_identity', report)
            self.assertNotIn('TracingPolicyNamespaced', runner.manifests)
            self.assertFalse(any('inspect' in cmd for cmd in runner.calls))

    def test_invalid_cri_pid_never_reaches_procfs(self):
        for pid in (True, 0, -1, '42; id'):
            runner = FakeRunner()
            def fake(command, **kw):
                if 'inspect' in command: return result({'info': {'pid': pid}})
                return runner(command, **kw)
            trial = KindTrial(Path('/unused'), 'sigkill_fd_install', node_access='local-sudo', runner=fake)
            with self.subTest(pid=pid), self.assertRaisesRegex(RuntimeError, 'invalid CRI init PID'):
                trial.task_identity(runner.pod(), runner.node)
            self.assertFalse(any('/proc/' in arg for cmd in runner.calls for arg in cmd))

    def test_kind_default_commands_and_legacy_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner(node='runtime-lab-control-plane')
            trial, report = execute_fake(Path(directory), runner)
            self.assertEqual(report['verification']['status'], 'passed', report)
            self.assertEqual(report['context'], 'kind-runtime-lab')
            commands = [cmd for cmd in runner.calls if cmd[0] == 'docker']
            inspect = ['docker', 'exec', runner.node, 'crictl', 'inspect', CID]
            sample = ['docker', 'exec', runner.node, 'sh', '-c',
                      "cat /proc/42/stat && readlink /proc/42/ns/pid && grep '^NSpid:' /proc/42/status && getconf CLK_TCK"]
            self.assertEqual(commands, [inspect, sample, ['docker', 'exec', runner.node, 'uname', '-r'], inspect, sample])
            self.assertFalse(any(cmd[0] == 'sudo' for cmd in runner.calls))
            # Golden hashes from e5a8ba7 with this same fake clock/UUID/runner.
            expected = {
                'byul-proof-1234567890/namespace.json': '2bb3c19afc21a4ab5fefc02e14b4a5ec9f68c4c60f44eda7c863ce3efdc8db38',
                'byul-proof-1234567890/pod.json': '03ae3201a3ef9d3fb73702039919afdc1d391fd33f49803b5205f0f0c194822c',
                'byul-proof-1234567890/policy.json': 'e36ba67c960902a814c0879e94adef0e334a35748aac535f4b34f42a628e7ea9',
                'byul-proof-1234567890/installed-spec.json': '0317d7a43cab8043a0890cdf1c6488df845234b07cc90ebde4102c22d80c5bfb',
                'byul-proof-1234567890.verification.json': '9789c5ec5fb05e826bc2c2cf57a30e8ad18ec6673b680c61137ab1cfa8fdaa5e',
            }
            for relative, digest in expected.items():
                self.assertEqual(hashlib.sha256((Path(directory) / relative).read_bytes()).hexdigest(), digest)
            for container in runner.manifests['Pod']['spec']['initContainers'] + runner.manifests['Pod']['spec']['containers']:
                self.assertEqual((container['image'], container['imagePullPolicy']), ('serving-runtime-lab:dev', 'IfNotPresent'))

    def test_cli_flags_and_legacy_alias(self):
        for flag in ('--allow-isolated-trials', '--allow-isolated-kind-trials'):
            args = ['trial', '--output', '/unused', '--methods', 'sigkill_fd_install', flag,
                    '--node-access', 'local-sudo', '--context', 'byul-aws-lab', '--image', 'serving-gateway:rt-test',
                    '--image-pull-policy', 'Never']
            with patch('sys.argv', args), patch.object(trial_module, 'KindTrial') as constructor, \
                 patch('sys.stdout', new_callable=io.StringIO):
                constructor.return_value.execute.return_value = {'id': 'proof', 'verification': {'status': 'passed'}, 'facts': {}}
                self.assertEqual(trial_module.main(), 0)
                self.assertEqual(constructor.call_args.kwargs, dict(override_only=False, node_access='local-sudo',
                    context='byul-aws-lab', image='serving-gateway:rt-test', image_pull_policy='Never'))
        with patch('sys.argv', ['trial', '--output', '/unused', '--methods', 'sigkill_fd_install',
                                '--allow-isolated-kind-trials']), patch.object(trial_module, 'KindTrial') as constructor, \
             patch('sys.stdout', new_callable=io.StringIO):
            constructor.return_value.execute.return_value = {'id': 'proof', 'verification': {'status': 'passed'}, 'facts': {}}
            self.assertEqual(trial_module.main(), 0)
            self.assertEqual(constructor.call_args.kwargs, dict(override_only=False, node_access='kind-docker',
                context='kind-runtime-lab', image='serving-runtime-lab:dev', image_pull_policy='IfNotPresent'))
        with patch('sys.argv', ['trial', '--output', '/unused']), patch('sys.stderr', new_callable=io.StringIO), \
             patch.object(trial_module, 'KindTrial') as constructor, self.assertRaises(SystemExit):
            trial_module.main()
        constructor.assert_not_called()

    def test_local_report_status_and_safety_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = FakeRunner()
            trial, report = execute_fake(root, runner, node_access='local-sudo', context='byul-aws-lab',
                image='serving-gateway:rt-test', image_pull_policy='Never')
            self.assertEqual(report['verification']['status'], 'passed', report)
            self.assertEqual(report['task_identity']['node_pid'], 42)
            self.assertEqual(report['context'], 'byul-aws-lab')
            self.assertEqual(report['data_origin'], 'real')  # simulated producer; not actual AWS evidence
            self.assertEqual(report['variant'], 'combined')
            policy = runner.manifests['TracingPolicyNamespaced']
            spec_hash = hashlib.sha256(json.dumps(comparable_spec(policy['spec']), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            self.assertEqual(report['policy_spec_sha256'], spec_hash)
            self.assertEqual((report['kernel'], report['node']), ('test-kernel', runner.node))
            self.assertFalse(any(cmd[0] == 'docker' for cmd in runner.calls))
            for container in runner.manifests['Pod']['spec']['initContainers'] + runner.manifests['Pod']['spec']['containers']:
                self.assertEqual((container['image'], container['imagePullPolicy']), ('serving-gateway:rt-test', 'Never'))
            for context in ('other-context', 'byul-aws-lab'):
                def status_run(command):
                    if 'pods' in command:
                        return result({'items': [{'metadata': {'name': 'agent', 'uid': 'agent-uid'},
                            'spec': {'nodeName': runner.node}, 'status': {'containerStatuses': [
                                {'name': 'tetragon', 'ready': True, 'restartCount': 0}]}}]})
                    if 'tracingpoliciesnamespaced' in command: return result({'items': [policy]})
                    if 'list' in command:
                        return result({'policies': [{'name': trial.policy, 'namespace': trial.namespace, 'id': 7,
                            'state': 'TP_STATE_ENABLED', 'mode': 'TP_MODE_MONITOR'}]})
                    if 'uname' in command: return result('test-kernel\n')
                    return result(code=1)
                collector = StatusCollector(context, root / 'state.json', root, runner=status_run)
                collector.collect()
                collector.spec_changed_at = time.monotonic() - 4
                state = collector.collect()
                self.assertEqual(state['state'], 'ok')
                if context != report['context']:
                    self.assertEqual(state['verifications'], [])
                    continue
                self.assertEqual(len(state['verifications']), 1)
                proof = state['verifications'][0]
                self.assertEqual(proof['source_report_sha256'], hashlib.sha256(
                    (root / (trial.namespace + '.verification.json')).read_bytes()).hexdigest())
                snapshot = read_tetragon_state(root / 'state.json', context=context)
                self.assertTrue(snapshot['fresh'])
                self.assertEqual(snapshot['verifications'][0]['verification']['status'], 'passed')
                intent = parse_intent({**json.loads((trial_module.ROOT / 'deploy/lab/intent.lab-v1.1.json').read_text()),
                                       'namespace': trial.namespace})
                def assess(value): return evaluate(value, trial.policy, intent, {}, 0)
                verdict = assess(snapshot)
                self.assertTrue(verdict['checks']['evidence'], verdict)
                self.assertTrue(verdict['checks']['capability'], verdict)
                self.assertEqual(verdict['capability_basis'], 'live_trial')
                for field in ('kernel', 'node', 'policy_spec_sha256'):
                    bad = deepcopy(snapshot)
                    bad['verifications'][0][field] = 'changed'
                    self.assertFalse(assess(bad)['checks']['evidence'])
                state['verifications'][0]['task_identity']['pidns'] = 999
                (root / 'state.json').write_text(json.dumps(state))
                altered = read_tetragon_state(root / 'state.json', context=context)
                self.assertNotEqual(altered['verifications'][0]['verification']['status'], 'passed')
                self.assertFalse(assess(altered)['checks']['evidence'])


if __name__ == '__main__': unittest.main()
