import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import tetragon_status
from tools.tetragon_status import StatusCollector
from serving_runtime.enforcement.recovery import RecoveryTracker
from serving_runtime.mvp.tetragon_state import read_tetragon_state


class CollectorTests(unittest.TestCase):
    def test_state_and_recovery_permissions_under_umask_with_stale_next(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'state.json'
            recovery_path = root / 'state.json.recovery.json'
            for path in (output, recovery_path):
                temporary = path.with_name(path.name + '.next')
                temporary.write_text('{}')
                temporary.chmod(0o664)
            recovery = RecoveryTracker('kind-test', recovery_path,
                baseline_path=root / 'missing-baseline', identities_path=root / 'identities',
                heartbeat_path=root / 'heartbeat', observations_path=root / 'observations')
            collector = StatusCollector('kind-test', output, root / 'evidence', recovery=recovery,
                runner=lambda command: SimpleNamespace(returncode=1, stdout=''))
            previous = os.umask(0o002)
            try:
                snapshot = collector.collect()
            finally:
                os.umask(previous)
            self.assertEqual(snapshot['monitoring']['state'], 'not_configured')
            for path, mode in ((output, 0o644), (recovery_path, 0o600)):
                self.assertEqual(path.stat().st_mode & 0o777, mode)
                self.assertEqual(path.stat().st_mode & 0o022, 0)
                self.assertEqual(json.loads(path.read_text())['context'], 'kind-test')
                self.assertFalse(path.with_name(path.name + '.next').exists())

    def test_baseline_permissions_under_umask_with_stale_next(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / 'baseline.json'
            temporary = baseline.with_name(baseline.name + '.next')
            temporary.write_text('{}')
            temporary.chmod(0o664)
            snapshot = {'state': 'ok', 'generated_at': '2026-11-08T00:00:00+00:00',
                'policies': [{'name': 'policy', 'node': 'node', 'namespace': 'test',
                    'mode': 'monitor_only', 'spec_sha256': 'a' * 64}], 'verifications': []}
            argv = ['tetragon_status', '--context', 'kind-test', '--output', str(root / 'state.json'),
                '--evidence-dir', str(root / 'evidence'), '--capture-baseline', str(baseline)]
            previous = os.umask(0o002)
            try:
                with patch('sys.argv', argv), patch.object(StatusCollector, 'collect', return_value=snapshot), \
                     patch('builtins.print'):
                    self.assertEqual(tetragon_status.main(), 0)
            finally:
                os.umask(previous)
            self.assertEqual(baseline.stat().st_mode & 0o777, 0o644)
            self.assertEqual(baseline.stat().st_mode & 0o022, 0)
            self.assertEqual(json.loads(baseline.read_text())['policies'], snapshot['policies'])
            self.assertFalse(temporary.exists())

    def test_queries_real_node_scope_and_never_changes_policy(self):
        calls=[]
        def run(command):
            calls.append(command)
            def result(value): return SimpleNamespace(returncode=0,stdout=json.dumps(value) if isinstance(value,dict) else value)
            if 'pods' in command:
                return result({'items':[{'metadata':{'name':'agent'},'spec':{'nodeName':'node'},'status':{'containerStatuses':[{'name':'tetragon','restartCount':2,'ready':True}]}}]})
            if 'tracingpoliciesnamespaced' in command:
                return result({'items':[]})
            if 'list' in command:
                return result({'policies':[{'name':'trial','namespace':'test','id':'7','state':'TP_STATE_ENABLED','mode':'TP_MODE_MONITOR_ONLY'}]})
            if 'uname' in command: return result('node-kernel\n')
            if 'probe' in command: return result('override_return: false\nfmodret: true\n')
            return SimpleNamespace(returncode=1,stdout='')
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'state.json'
            collector=StatusCollector('kind-test',output,Path(directory)/'evidence',runner=run)
            pending=collector.collect()
            self.assertEqual(pending['state'],'error')
            self.assertFalse(any('list' in command for command in calls))
            collector.spec_changed_at=time.monotonic()-4
            snapshot=collector.collect()
            self.assertEqual(snapshot['state'],'ok')
            self.assertEqual(snapshot['policies'][0]['mode'],'monitor_only')
            self.assertEqual(snapshot['capabilities'][0]['kernel'],'node-kernel')
            self.assertTrue(read_tetragon_state(output,context='kind-test')['fresh'])
        self.assertTrue(all(command[:3]==['kubectl','--context','kind-test'] for command in calls))
        self.assertFalse(any(any(x in command for x in ('apply','delete','set-mode')) for command in calls))

    def test_node_query_failure_cannot_be_published_as_healthy(self):
        def run(command):
            return SimpleNamespace(returncode=1,stdout='')
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'state.json'
            collector=StatusCollector('kind-test',output,directory,runner=run)
            self.assertEqual(collector.collect()['state'],'error')
            state=read_tetragon_state(output,context='kind-test')
            self.assertFalse(state['fresh'])
            self.assertEqual(state['policies'],[])

if __name__=='__main__': unittest.main()
