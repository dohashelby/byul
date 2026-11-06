import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from tools.tetragon_status import StatusCollector
from serving_runtime.mvp.tetragon_state import read_tetragon_state


class CollectorTests(unittest.TestCase):
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
