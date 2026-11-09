"""Stateful API double exercises writes, committed timeouts, and restoration."""
import copy
import json
from pathlib import Path
import stat
import subprocess
import unittest
from unittest.mock import Mock, patch

from serving_runtime.enforcement.intent import parse_intent
from serving_runtime.enforcement.sync import sync, RESOURCES
from serving_runtime.enforcement.tetragon import render_monitor_manifests
from tests.test_operations_agent import intent_document, operation
from serving_runtime.operations.agent import Agent, Config, Rejected


class Cluster:
    def __init__(self):
        self.live = {c: {} for c in RESOURCES}
        self.commands, self.mutations = [], []
        self.crd = True
        self.fail_at = None
        self.timeout = False
        self.version = 0
        self.modes = {}
        self.fail_restore = False

    def put(self, component, obj):
        self.version += 1
        obj = copy.deepcopy(obj)
        obj['metadata'].update(uid='object-' + str(self.version), resourceVersion=str(self.version))
        self.live[component][obj['metadata']['name']] = obj
        return obj

    def mode(self, name):
        return self.modes.get(name, 'monitor') if name in self.live['tetragon'] else None

    def run(self, command):
        self.commands.append(command)
        args = command[5:]
        verb = args[0]
        def result(code=0, out=''):
            return subprocess.CompletedProcess(command, code, out, 'private diagnostic')
        if verb == 'get':
            if args[1] == 'crd':
                return result(out='{}' if self.crd else '')
            component = next(c for c, r in RESOURCES.items() if r == args[1])
            return result(out=json.dumps({'items': list(self.live[component].values())}))
        if verb == 'diff':
            return result(1, 'private diff')
        path = Path(args[args.index('-f') + 1])
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in path.parent.iterdir())
        obj = json.loads(path.read_text())
        if '--dry-run=server' in args:
            return result(out=json.dumps(obj))
        self.mutations.append(command)
        if self.fail_restore and ('restore' in path.name or verb == 'delete'):
            return result(1)
        if verb == 'delete':
            uri = args[args.index('--raw') + 1]
            name = uri.rsplit('/', 1)[1]
            component = 'tetragon' if 'tracingpoliciesnamespaced' in uri else 'cilium'
            live = self.live[component][name]
            assert obj['preconditions'] == {k: live['metadata'][k] for k in ('uid', 'resourceVersion')}
            del self.live[component][name]
        else:
            component = 'tetragon' if obj['kind'] == 'TracingPolicyNamespaced' else 'cilium'
            name = obj['metadata']['name']
            if verb == 'create':
                assert name not in self.live[component]
            else:
                assert obj['metadata']['resourceVersion'] == self.live[component][name]['metadata']['resourceVersion']
            if self.fail_at == len(self.mutations) and not self.timeout:
                return result(1)
            uid = self.live[component].get(name, {}).get("metadata", {}).get("uid")
            stored = self.put(component, obj)
            if uid:
                stored["metadata"]["uid"] = uid
        if self.fail_at == len(self.mutations) and self.timeout:
            raise subprocess.TimeoutExpired(command, 70, output='private timeout')
        return result(out=json.dumps(obj))


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.intent = parse_intent(intent_document())
        self.cluster = Cluster()
        self.gateway = Mock()

    def sync(self, **kwargs):
        return sync(self.intent, context='test', namespace=self.intent.namespace, run=self.cluster.run,
                    read_mode=self.cluster.mode, verify_gateway=self.gateway, **kwargs)

    def manifests(self):
        return list(render_monitor_manifests(self.intent).values())

    def test_render_create_replace_unchanged_extra(self):
        desired = self.manifests()
        unchanged = self.cluster.put('tetragon', desired[0])
        old = copy.deepcopy(desired[1]); old['spec']['podSelector'] = {}
        self.cluster.put('tetragon', old)
        extra = copy.deepcopy(desired[0]); extra['metadata']['name'] = 'runtime-extra'
        self.cluster.put('tetragon', extra)
        result = self.sync(execute=True)
        self.assertEqual(result['state'], 'succeeded', result)
        self.assertEqual([p['action'] for p in result['policies']], ['unchanged', 'replaced'] + ['created'] * (len(desired) - 2) + ['extra'])
        self.assertEqual(len(self.cluster.mutations), len(desired) - 1)
        self.assertEqual(self.cluster.live['tetragon'][unchanged['metadata']['name']], unchanged)
        self.assertIn('runtime-extra', self.cluster.live['tetragon'])
        for obj in self.cluster.live['tetragon'].values():
            self.assertEqual(obj['spec']['options'], [{'name': 'policy-mode', 'value': 'monitor'}])
        self.gateway.assert_called_once_with(False)
        self.assertEqual([s['stage'] for s in result['steps']], ['validation', 'render', 'dry-run', 'apply', 'verify'])

    def test_dry_run_never_mutates_even_with_cilium_opt_in(self):
        result = self.sync(components=['tetragon', 'cilium'], cilium_apply=True)
        self.assertEqual((result['state'], result['code']), ('checked', 'DRY_RUN'))
        self.assertFalse(self.cluster.mutations)
        self.assertTrue(any('diff' in c for c in self.cluster.commands))
        self.assertTrue(any('--server-side=true' in c and '--dry-run=server' in c for c in self.cluster.commands))
        self.gateway.assert_not_called()

    def test_enforce_replacement_rejected_unchanged_preserved(self):
        for obj in self.manifests():
            self.cluster.put('tetragon', obj)
        name = self.manifests()[0]['metadata']['name']
        self.cluster.modes[name] = 'enforce'
        result = self.sync(execute=True)
        self.assertEqual(result['state'], 'succeeded')
        self.assertEqual(result['policies'][0]['mode'], 'enforce')
        self.cluster.live['tetragon'][name]['spec']['podSelector'] = {}
        self.assertEqual(self.sync(execute=True)['code'], 'SAFETY_REJECTED')
        self.assertFalse(self.cluster.mutations)

    def test_unknown_live_mode_rejected(self):
        obj = self.manifests()[0]; self.cluster.put('tetragon', obj)
        self.cluster.modes[obj['metadata']['name']] = None
        self.assertEqual(self.sync(execute=True)['code'], 'SAFETY_REJECTED')
        self.assertFalse(self.cluster.mutations)

    def test_cilium_not_installed_is_skipped(self):
        self.cluster.crd = False
        result = self.sync(components=['tetragon', 'cilium'], cilium_apply=True, execute=True)
        self.assertEqual(result['state'], 'succeeded')
        self.assertIn({'component': 'cilium', 'state': 'not_installed'}, result['components'])
        self.assertTrue(all(p['action'] == 'skipped' for p in result['policies'] if p['component'] == 'cilium'))
        self.assertFalse(any('ciliumnetworkpolicies' in c for c in self.cluster.commands))

    def test_crd_error_is_not_absence(self):
        original = self.cluster.run
        def run(command):
            if 'crd' in command:
                return subprocess.CompletedProcess(command, 1, '', 'forbidden')
            return original(command)
        self.cluster.run = run
        self.assertEqual(self.sync(execute=True, components=['tetragon', 'cilium'])['state'], 'failed')
        self.assertFalse(self.cluster.mutations)

    def test_cilium_without_opt_in_only_server_dry_run_and_diff(self):
        result = self.sync(execute=True, components=['tetragon', 'cilium'])
        self.assertEqual(result['state'], 'succeeded')
        self.assertFalse(self.cluster.live['cilium'])
        self.assertIn({'component': 'cilium', 'state': 'dry_run'}, result['components'])
        self.gateway.assert_called_once_with(False)

    def test_mid_failure_rolls_back_replacement(self):
        old = self.manifests()[0]; old['spec']['podSelector'] = {}
        self.cluster.put('tetragon', old)
        self.cluster.fail_at = 2
        result = self.sync(execute=True)
        self.assertEqual(result['state'], 'rolled_back', result)
        self.assertEqual(len(self.cluster.live['tetragon']), 1)
        self.assertEqual(next(iter(self.cluster.live['tetragon'].values()))['spec'], old['spec'])
        self.assertNotIn('private', json.dumps(result))

    def test_committed_timeout_restored_without_replay(self):
        self.cluster.fail_at = 1; self.cluster.timeout = True
        result = self.sync(execute=True)
        self.assertEqual(result['state'], 'rolled_back', result)
        self.assertFalse(self.cluster.live['tetragon'])
        self.assertEqual(sum('create' in c and '--dry-run=server' not in c for c in self.cluster.commands), 1)

    def test_failed_rollback_is_unknown(self):
        self.cluster.fail_at = 2; self.cluster.fail_restore = True
        self.assertEqual(self.sync(execute=True)['state'], 'unknown')

    def test_cilium_gateway_request_failure_rolls_back_all(self):
        self.gateway.side_effect = [RuntimeError('private'), None]
        result = self.sync(execute=True, components=['tetragon', 'cilium'], cilium_apply=True)
        self.assertEqual(result['state'], 'rolled_back', result)
        self.assertFalse(any(self.cluster.live.values()))
        self.assertEqual([c.args for c in self.gateway.call_args_list], [(True,), (True,)])

    def test_cilium_previous_object_restored_after_gateway_failure(self):
        from serving_runtime.enforcement.cilium import render_manifests
        for obj in self.manifests():
            self.cluster.put('tetragon', obj)
        old = next(iter(render_manifests(self.intent).values()))
        old['spec']['endpointSelector'] = {'matchLabels': {'app': 'previous'}}
        stored = self.cluster.put('cilium', old)
        self.gateway.side_effect = [RuntimeError('private'), None]
        result = self.sync(execute=True, components=['tetragon', 'cilium'], cilium_apply=True)
        self.assertEqual(result['state'], 'rolled_back', result)
        self.assertEqual(len(self.cluster.live['cilium']), 1)
        restored = self.cluster.live['cilium'][old['metadata']['name']]
        self.assertEqual(restored['spec'], old['spec'])
        self.assertEqual(restored['metadata']['uid'], stored['metadata']['uid'])
        self.assertEqual(restored['metadata']['annotations'], old['metadata']['annotations'])

    def test_failed_dry_run_prevents_every_write(self):
        original = self.cluster.run
        def run(command):
            if '--dry-run=server' in command:
                return subprocess.CompletedProcess(command, 1, '', 'private')
            return original(command)
        self.cluster.run = run
        self.assertEqual(self.sync(execute=True)['state'], 'failed')
        self.assertFalse(self.cluster.mutations)

    def test_guard_prevents_write(self):
        def guard():
            raise RuntimeError('expired')
        self.assertEqual(self.sync(execute=True, before_apply=guard)['state'], 'failed')
        self.assertFalse(self.cluster.mutations)

    def test_concurrent_edit_prevents_write(self):
        obj = self.manifests()[0]; obj['spec']['podSelector'] = {}
        self.cluster.put('tetragon', obj)
        def guard():
            self.cluster.live['tetragon'][obj['metadata']['name']]['metadata']['resourceVersion'] = 'newer'
        self.assertEqual(self.sync(execute=True, before_apply=guard)['code'], 'STATE_CHANGED')
        self.assertFalse(self.cluster.mutations)


class AgentSyncTests(unittest.TestCase):
    def setUp(self):
        self.intent = parse_intent(intent_document())
        self.agent = Agent(Mock(), Config('server-01', '/unused', 'test', self.intent.namespace))
        self.before = operation()['expected']
        self.op = operation('enforcement.apply')
        self.op.update(approval_required=True, approvals=[{'role': 'approver', 'token_id': 'other'}])
        self.op['payload'] = {'bundle_id': 'old', 'bundle_sha256': 'a' * 64, 'components': ['tetragon']}
        self.agent.verified_bundle = Mock(return_value=Mock(sha256='a' * 64))
        self.agent.check_target = Mock()
        self.agent.current = Mock(return_value=(self.before, self.intent))

    def test_bundle_binding_approval(self):
        self.assertEqual(self.agent.validate(self.op, self.before, self.intent), self.before)
        for field, value, code in [('bundle_id', 'other', 'STATE_CHANGED'), ('bundle_sha256', 'b' * 64, 'STATE_CHANGED'),
                                   ('components', ['cilium'], 'VALIDATION_FAILED')]:
            with self.subTest(field=field), self.assertRaisesRegex(Rejected, code):
                self.agent.validate({**self.op, 'payload': {**self.op['payload'], field: value}}, self.before, self.intent)
        self.op['approvals'] = []
        with self.assertRaisesRegex(Rejected, 'APPROVAL_REQUIRED'):
            self.agent.validate(self.op, self.before, self.intent)

    def test_agent_runs_shared_dry_run(self):
        cluster = Cluster()
        self.agent.run, self.agent.sync_mode = cluster.run, cluster.mode
        result = self.agent.process(self.op)
        self.assertEqual((result['state'], result['code']), ('checked', 'DRY_RUN'), result)
        self.assertEqual(len(result['policies']), len(render_monitor_manifests(self.intent)))
        self.assertFalse(cluster.mutations)
        self.agent.client.request.assert_called_once()

    def test_execute_reports_running_once_and_final_policy_results(self):
        from dataclasses import replace
        self.agent.config = replace(self.agent.config, execute=True)
        cluster = Cluster()
        self.agent.run, self.agent.sync_mode = cluster.run, cluster.mode
        self.agent.sync_gateway = Mock()
        result = self.agent.process(self.op)
        self.assertEqual((result['state'], result['code']), ('succeeded', 'OK'), result)
        self.assertFalse(result['dry_run'])
        reports = [c.args[1] for c in self.agent.client.request.call_args_list]
        self.assertEqual([r['state'] for r in reports], ['running', 'succeeded'])
        self.assertEqual(len(cluster.mutations), len(render_monitor_manifests(self.intent)))
        self.assertNotIn('private', json.dumps(result))

    def test_self_approval_disallowed_even_in_lab(self):
        from dataclasses import replace
        self.agent.config = replace(self.agent.config, lab_self_approval=True)
        self.op.update(lab_self_approval=True, approvals=[{'role': 'approver', 'token_id': self.op['requested_by'], 'self_approval': True}])
        self.assertEqual(self.agent.process(self.op)['code'], 'APPROVAL_REQUIRED')


class SyncEntryTests(unittest.TestCase):
    def test_cli_default_dry_run_and_shared_engine(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from tempfile import TemporaryDirectory
        from serving_runtime.cli import main
        intent = parse_intent(intent_document())
        cluster = Cluster()
        with TemporaryDirectory() as temporary:
            output = StringIO()
            with patch('serving_runtime.operations.agent.Agent.current', return_value=({'bundle_id': 'current'}, intent)), \
                 patch('serving_runtime.operations.agent.Agent.verified_bundle', return_value=Mock(bundle_id='current', sha256='a' * 64)), \
                 patch('serving_runtime.operations.agent.Agent.check_target'), \
                 patch('serving_runtime.operations.agent.Agent.run', side_effect=cluster.run), \
                 redirect_stdout(output):
                code = main(['enforce', 'sync', '--existing', 'current', '--bundle-root', temporary,
                             '--context', 'test', '--namespace', intent.namespace, '--lock-file', temporary + '/lock'])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())['code'], 'DRY_RUN')
            self.assertFalse(cluster.mutations)

    def test_live_mode_requires_all_nodes_enabled_and_same_mode(self):
        agent = Agent(Mock(), Config('server', '/unused', 'test', 'test'))
        pods = [{'metadata': {'name': 'daemon-' + str(i)}, 'spec': {'nodeName': 'node-' + str(i)},
                 'status': {'containerStatuses': [{'name': 'tetragon', 'ready': True}]}} for i in range(2)]
        def result(obj):
            return subprocess.CompletedProcess([], 0, json.dumps(obj), '')
        tool = Mock()
        agent.tool = Mock(return_value=tool)
        tool.parse_policy_row.side_effect = [dict(mode='monitor', state='enabled'), dict(mode='enforce', state='enabled')]
        agent.run = Mock(side_effect=[result({'status': {'desiredNumberScheduled': 2, 'numberReady': 2}}),
                                      result({'items': pods}), result({}), result({})])
        self.assertIsNone(agent.sync_mode('runtime-test'))
        tool.parse_policy_row.side_effect = [dict(mode='monitor', state='enabled')] * 2
        agent.run.reset_mock(side_effect=True)
        agent.run.side_effect = [result({'status': {'desiredNumberScheduled': 2, 'numberReady': 2}}),
                                result({'items': pods}), result({}), result({})]
        self.assertEqual(agent.sync_mode('runtime-test'), 'monitor')

    def test_gateway_smoke_uses_current_bundle_model_and_only_fixed_prompt(self):
        from unittest.mock import patch
        from contextlib import nullcontext
        from tempfile import TemporaryDirectory
        agent = Agent(Mock(), Config('server', '/unused', 'test', 'test', status_url='http://localhost:8080'))
        agent.current = Mock(return_value=({}, None))
        with TemporaryDirectory() as temporary:
            policy = Path(temporary) / 'policy.json'
            policy.write_text(json.dumps({'allowed_models': ['test-model']}))
            agent.verified_bundle = Mock(return_value=Mock(role_paths=lambda: {'gateway_policy': str(policy)}))
            response = Mock(); response.read.return_value = b'{"choices":[{"text":"ok"}]}'
            opener = Mock(); opener.open.return_value = nullcontext(response)
            with patch('serving_runtime.operations.agent.os.readlink', return_value='current'), \
                 patch('serving_runtime.operations.agent.build_opener', return_value=opener):
                agent.sync_gateway(True)
            request = opener.open.call_args.args[0]
            self.assertEqual(json.loads(request.data), {'model': 'test-model', 'prompt': 'ping', 'max_tokens': 1, 'stream': False})
            self.assertEqual(opener.open.call_args.kwargs['timeout'], 15)


class RestoreSafetyTests(unittest.TestCase):
    setUp = SyncTests.setUp
    sync = SyncTests.sync
    manifests = SyncTests.manifests

    def test_live_monitor_with_enforce_reload_spec_is_rejected(self):
        obj = self.manifests()[-1]
        obj['spec']['options'] = [{'name': 'policy-mode', 'value': 'enforce'}]
        self.cluster.put('tetragon', obj)
        self.assertEqual(self.sync(execute=True)['code'], 'SAFETY_REJECTED')
        self.assertFalse(self.cluster.mutations)

    def test_concurrent_replacement_is_not_deleted_on_ambiguous_create(self):
        self.cluster.fail_at = 1; self.cluster.timeout = True
        original = self.cluster.run
        def run(command):
            try:
                return original(command)
            except subprocess.TimeoutExpired:
                # Another writer replaces it with an identical spec but no marker.
                obj = next(iter(self.cluster.live['tetragon'].values()))
                obj['metadata']['annotations'].pop('serving-runtime.local/sync-transaction')
                self.cluster.put('tetragon', obj)
                raise
        self.cluster.run = run
        result = self.sync(execute=True)
        self.assertEqual(result['state'], 'unknown')
        self.assertTrue(self.cluster.live['tetragon'])
        self.assertEqual(len(self.cluster.mutations), 1)
