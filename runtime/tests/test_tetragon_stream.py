import json
from types import SimpleNamespace
import unittest
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch
from tools import tetragon_stream as stream_module

from tools.tetragon_stream import AgentEpochGuard, TetragonStream
from tools.k8s_observation_feed import _FeedGroup, _Child, _Supervisor


class StreamTests(unittest.TestCase):
    def test_duplicate_termination_signals_do_not_acquire_event_locks_in_handler(self):
        handlers={};event_calls=[]
        class Event:
            def is_set(self):return False
            def set(self):event_calls.append('set')
            def wait(self,timeout):return False
        class Stream:
            def __init__(self,*args):pass
            def start(self):
                callback=handlers[stream_module.signal.SIGTERM]
                callback(None,None);callback(None,None)
                self.assertion=len(event_calls)
                self.stop_requested.initial_calls=self.assertion
            def close(self):pass
        def register(sig,callback):
            handlers[sig]=callback
            return None
        with tempfile.TemporaryDirectory() as directory:
            argv=['tetragon_stream','--context','kind-test','--namespace','serving','--policy','rule','--output',directory]
            with patch.object(stream_module,'TetragonStream',Stream),patch.object(stream_module.threading,'Event',Event),patch.object(stream_module.signal,'signal',register),patch('sys.argv',argv):
                self.assertEqual(stream_module.main(),0)
        self.assertEqual(event_calls,['set'])
    def test_initial_discovery_failure_retries_without_claiming_a_running_writer(self):
        class Event:
            stopped=False
            def is_set(self):return self.stopped
            def set(self):self.stopped=True
            def wait(self,timeout):return self.stopped
        attempts=[]
        class Stream:
            def __init__(self,*args):attempts.append(self)
            def start(self):
                if len(attempts)==1:raise ValueError('source unavailable')
                self.stop_requested.set()
            def close(self):pass
        with tempfile.TemporaryDirectory() as directory:
            argv=['tetragon_stream','--context','kind-test','--namespace','serving','--policy','runtime-tg-secret-01','--output',directory]
            with patch.object(stream_module,'TetragonStream',Stream),patch.object(stream_module.threading,'Event',Event),patch('sys.argv',argv):
                self.assertEqual(stream_module.main(),0)
            self.assertEqual(len(attempts),2)
            heartbeat=json.loads((Path(directory)/'tetragon.heartbeat.json').read_text())
            self.assertEqual(heartbeat['state'],'reconnecting')
            self.assertFalse(heartbeat['processes'][0]['running'])

    def test_remote_cleanup_only_signals_the_exact_owned_uuid(self):
        calls=[]
        owned='byul-tetragon-stream-'+'a'*32
        other='byul-tetragon-stream-'+'b'*32
        def run(command):
            calls.append(command)
            return SimpleNamespace(returncode=0,stdout=f'5 1 sh -c script {owned} --policy-names rule\n6 1 sh -c script {other} --policy-names rule\n7 5 tetra getevents\n8 6 tetra getevents\n')
        stream=TetragonStream('kind-test','serving',['runtime-tg-secret-01'],'/tmp/unused-stream',runner=run)
        stream.guard.agents=lambda:{'node':{'name':'agent'}}
        stream.cleanup_remote(owned)
        kills=[c for c in calls if 'kill' in c]
        self.assertEqual([c[-1] for c in kills],['7','5'])
    def test_live_follow_child_is_reconnected_on_agent_failure_and_grpc_gap_has_no_replay_claim(self):
        class Process:
            code=None
            def poll(self):return self.code
            def terminate(self):self.code=-15
            def wait(self,timeout=None):return self.code
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with (root/'out').open('ab') as out,(root/'error').open('ab') as error:
                child=_Child('node',lambda since:['unused'],out,error)
                child.process=Process()
                group=_FeedGroup('tetragon',feed_path=root/'out',heartbeat_path=root/'heartbeat',gap_path=root/'gap',
                    children=[child],health_check=lambda:[{'name':'agent','error':'AgentEpochChanged'}],supports_backfill=False)
                group.state='running';group.last_healthy_at=datetime.now(timezone.utc)
                supervisor=_Supervisor([group],context='kind-test',stop_requested=threading.Event(),clock=lambda:0)
                supervisor.tick()
                self.assertEqual(group.state,'reconnecting')
                self.assertEqual(child.process.code,-15)
                self.assertIsNotNone(group.open_gap)
                supervisor._close_gap(group)
                self.assertEqual(group.last_gap['backfill'],'not_available')
    def test_ready_process_is_not_enough_when_agent_epoch_changes(self):
        current={"uid":"first","ready":True,"restarts":0}
        def run(command):
            return SimpleNamespace(returncode=0,stdout=json.dumps({"items":[{"metadata":{"name":"agent","uid":current["uid"]},
                "spec":{"nodeName":"node"},"status":{"containerStatuses":[{"name":"tetragon","ready":current["ready"],"restartCount":current["restarts"]}]}}]}))
        guard=AgentEpochGuard("kind-test",runner=run)
        self.assertEqual(guard.check(),[])
        current["restarts"]=1
        self.assertEqual(guard.check()[0]["error"],"AgentEpochChanged")
        self.assertEqual(guard.check(),[])
        current.update(uid="replacement",restarts=0)
        self.assertEqual(guard.check()[0]["error"],"AgentEpochChanged")
        current["ready"]=False
        self.assertEqual(guard.check()[0]["error"],"AgentUnavailable")

    def test_stream_is_explicitly_policy_scoped_and_does_not_promise_backfill(self):
        stream=TetragonStream("kind-test","serving",["runtime-tg-secret-01"],"/tmp/unused-stream")
        stream.guard.agents=lambda:{"node":{"name":"agent"}}
        command=stream.command("node",since="2026-11-06T00:00:00Z")
        self.assertEqual(command[:3],["kubectl","--context","kind-test"])
        self.assertIn("runtime-tg-secret-01",command)
        self.assertNotIn("--namespace",command[command.index("--"):])
        self.assertNotIn("--since",command)


if __name__=="__main__":unittest.main()
