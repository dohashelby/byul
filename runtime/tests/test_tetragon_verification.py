from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from serving_runtime.enforcement.verification import KERNEL_IDENTITY_DATA, assess_effects, match_probe_events, reassess_record, task_matches_identity
from serving_runtime.enforcement.tetragon import comparable_spec
from serving_runtime.mvp.tetragon_state import read_tetragon_state, SCHEMA
from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from tools.tetragon_mode import switch_mode
from tools.tetragon_capability import classify_capabilities

ROOT=Path(__file__).resolve().parents[1]
AT=datetime(2026,11,6,tzinfo=timezone.utc)


def facts():
    return dict.fromkeys(('enforce_active','event_bound','collector_complete','sigkill_event',
                          'process_terminated','monitor_read_ok','restore_read_ok','same_policy_id',
                          'cleanup_ok','main_process','service_unavailable','container_restarted','service_recovered'),True)


def snapshot(at=AT):
    return {'schema_version':SCHEMA,'generated_at':at.isoformat(),'context':'kind-runtime-lab',
            'state':'ok','policies':[{'name':'trial','mode':'monitor'}],
            'capabilities':[],'transitions':[],'verifications':[]}


class EffectTests(unittest.TestCase):
    def test_kernel_identity_requires_exact_init_namespace_start_tick_and_pid(self):
        identity={'pod_uid':'pod-uid','namespace':'test','ns_pid':1,'pidns':123,'start_ticks':1000,'clock_ticks':100}
        task={'pid':42,'start_ns':10_005_000_000,'pidns':123}
        raw={'time':AT.isoformat(),'process_kprobe':{'policy_name':'trial','function_name':'fd_install',
            'action':'KPROBE_ACTION_SIGKILL','process':{'pid':42,'flags':'unknown'},
            'args':[{'file_arg':{'path':'/run/probe/file'}}],
            'data':[{'label':'proof_host_pid','int_arg':42},{'label':'proof_start_ns','size_arg':str(task['start_ns'])},
                    {'label':'proof_pidns','uint_arg':123}]}}
        params=dict(policy='trial',namespace='test',pod_uid='pod-uid',path='/run/probe/file',since=AT.isoformat(),main_process=True)
        self.assertEqual(match_probe_events([json.dumps(raw)],**params),[])
        event=match_probe_events([json.dumps(raw)],**params,task_identity=identity)[0]
        self.assertEqual(event['identity_source'],'kernel_task')
        self.assertEqual(event['kernel_task'],{**task,'start_ns':str(task['start_ns'])})
        self.assertEqual(event['ns_pid'],1)
        for changed in ({'pidns':124},{'start_ticks':999},{'clock_ticks':101},{'pod_uid':'other'},
                        {'namespace':'other'},{'ns_pid':2}):
            self.assertEqual(match_probe_events([json.dumps(raw)],**params,task_identity={**identity,**changed}),[])
        raw['process_kprobe']['process']['pod']={'namespace':'other','uid':'other'}
        self.assertEqual(match_probe_events([json.dumps(raw)],**params,task_identity=identity),[])
        for changed in ({'pid':0},{'pid':True},{'start_ns':0},{'start_ns':10_010_000_000}):
            self.assertFalse(task_matches_identity({**task,**changed},identity))
        self.assertTrue(task_matches_identity({**task,'start_ns':str(task['start_ns'])},identity))
        # Older servers can exceed JavaScript's exact integer range.
        old={'start_ticks':1_000_000_001,'clock_ticks':100,'pidns':123,'ns_pid':1}
        self.assertTrue(task_matches_identity({**task,'start_ns':'10000000015000001'},old))

    def test_only_known_crd_argument_defaults_are_ignored(self):
        original={'kprobes':[{'args':[{'index':0,'type':'file'}]}]}
        defaulted={'kprobes':[{'args':[{'index':0,'type':'file','maxData':False,'returnCopy':False,'resolve':''}]}]}
        self.assertEqual(comparable_spec(original),comparable_spec(defaulted))
        defaulted['kprobes'][0]['args'][0]['returnCopy']=True
        self.assertNotEqual(comparable_spec(original),comparable_spec(defaulted))

    def test_fmodret_path_keeps_lsm_as_a_load_trial_candidate(self):
        capability=classify_capabilities(kernel='test',config=None,lsm=None,injection_list='',
                                        probe='override_return: false\nfmodret: true\nlsm: false')
        self.assertIn('override_lsm',capability['unknown'])
        self.assertNotIn('override_lsm',capability['unavailable'])

    def test_sigkill_and_recovery_do_not_prove_file_denial(self):
        result=assess_effects('sigkill_fd_install',facts())
        self.assertEqual(result['process_termination'],'confirmed')
        self.assertEqual(result['service_recovery'],'confirmed')
        self.assertEqual(result['file_read_denial'],'unknown')
        self.assertEqual(result['status'],'passed')
        self.assertEqual(assess_effects('override_lsm',facts())['status'],'unknown')

    def test_errno_needs_same_event_and_complete_enforce_window(self):
        evidence={**facts(),'read_denied':True,'override_event':True}
        self.assertEqual(assess_effects('override_lsm',evidence)['file_read_denial'],'confirmed')
        for key in ('event_bound','collector_complete','enforce_active','override_event'):
            with self.subTest(key=key):
                self.assertEqual(assess_effects('override_lsm',{**evidence,key:False})['file_read_denial'],'unknown')

    def test_kernel_permission_return_is_a_separate_direct_denial_basis(self):
        evidence={**facts(),'kernel_read_denied':True,'override_configured':True}
        result=assess_effects('override_lsm',evidence)
        self.assertEqual(result['file_read_denial'],'confirmed')
        self.assertEqual(result['file_denial_basis'],'kernel_permission_return')
        for key in ('override_configured','kernel_read_denied','monitor_read_ok'):
            self.assertEqual(assess_effects('override_lsm',{**evidence,key:False})['file_read_denial'],'unknown')
        self.assertEqual(assess_effects('sigkill_fd_install',evidence)['file_read_denial'],'unknown')

    def test_a_stored_success_label_without_evidence_is_rejected(self):
        record={'method':'override_lsm','facts':facts(), 'verification':{'file_read_denial':'confirmed'}}
        result=reassess_record(record)
        self.assertEqual(result['verification']['file_read_denial'],'unknown')
        self.assertEqual(result['verification']['process_termination'],'unknown')

    def test_reassessment_binds_kernel_return_to_policy_pid_and_window(self):
        record={'method':'override_lsm','facts':facts(),'started_at':AT.isoformat(),
                'finished_at':(AT+timedelta(seconds=4)).isoformat(),'policy':'trial','pod_uid':'uid','main_pid':42,'probe_path':'/run/probe/file',
                'policy_id':'7','policy_actions':[{'action':'Override','argError':-1},{'action':'Sigkill'}],
                'events':[{'time':(AT+timedelta(seconds=1)).isoformat(),'policy':'trial','pod_uid':'uid','pid':42,
                           'ns_pid':1,'function':'security_file_permission','action':'KPROBE_ACTION_SIGKILL','return_value':-1,'path':'/run/probe/file','access_mask':4}],
                'steps':[{'at':AT.isoformat(),'target_mode':'enforce','ok':True,
                          'before':{'mode':'monitor','id':'7','state':'enabled'},'after':{'mode':'enforce','id':'7','state':'enabled'}},
                         {'at':(AT+timedelta(seconds=3)).isoformat(),'target_mode':'monitor','ok':True,
                          'before':{'mode':'enforce','id':'7','state':'enabled'},'after':{'mode':'monitor','id':'7','state':'enabled'}}],
                'termination':{'exitCode':137,'reason':'Error','finishedAt':(AT+timedelta(seconds=2)).isoformat()}}
        self.assertEqual(reassess_record(record)['verification']['file_read_denial'],'confirmed')
        self.assertEqual(reassess_record(record)['verification']['process_termination'],'confirmed')
        import copy
        kernel_record=copy.deepcopy(record)
        kernel_record.update(task_identity={'pod_uid':'uid','namespace':'test','ns_pid':1,'pidns':123,'start_ticks':1000,'clock_ticks':100},
                             namespace='test',identity_probe_data=KERNEL_IDENTITY_DATA)
        kernel_record['events'][0].update(identity_source='kernel_task',kernel_task={'pid':42,'pidns':123,'start_ns':10_005_000_000})
        self.assertEqual(reassess_record(kernel_record)['verification']['file_read_denial'],'confirmed')
        self.assertEqual(reassess_record({**kernel_record,'identity_probe_data':[]})['verification']['file_read_denial'],'unknown')
        bad=copy.deepcopy(kernel_record);bad['events'][0].pop('identity_source')
        self.assertEqual(reassess_record(bad)['verification']['file_read_denial'],'unknown')
        self.assertEqual(reassess_record({**record,'main_pid':41})['verification']['file_read_denial'],'unknown')
        self.assertEqual(reassess_record({**record,'policy_actions':[{'action':'Sigkill'}]})['verification']['file_read_denial'],'unknown')
        self.assertEqual(reassess_record({**record,'facts':{**facts(),'agent_changed':True}})['verification']['file_read_denial'],'unknown')
        for changed in ({'access_mask':2},{'return_value':0},{'path':'/run/probe/other'}):
            self.assertEqual(reassess_record({**record,'events':[{**record['events'][0],**changed}]})['verification']['file_read_denial'],'unknown')
        late_switch=[{**record['steps'][0],'at':(AT+timedelta(seconds=2)).isoformat()},record['steps'][1]]
        self.assertEqual(reassess_record({**record,'steps':late_switch})['verification']['file_read_denial'],'unknown')

    def test_main_process_recovery_and_monitor_restore_are_required(self):
        for key in ('service_unavailable','container_restarted','service_recovered'):
            self.assertEqual(assess_effects('sigkill_fd_install',{**facts(),key:None})['status'],'unknown')
        self.assertEqual(assess_effects('sigkill_fd_install',{**facts(),'cleanup_ok':False})['status'],'failed')
        self.assertEqual(assess_effects('override_syscall',{**facts(),'read_succeeded':True})['status'],'failed')

    def test_event_binding_uses_real_v171_container_pid_and_bounded_time(self):
        event={'time':AT.isoformat(),'process_kprobe':{'policy_name':'trial','function_name':'fd_install',
          'action':'KPROBE_ACTION_SIGKILL','process':{'pid':42,'exec_id':'event-id',
            'pod':{'namespace':'test','uid':'pod-uid','container':{'pid':1}}},
          'args':[{'int_arg':4},{'file_arg':{'path':'/run/probe/file'}}]}}
        params=dict(policy='trial',namespace='test',pod_uid='pod-uid',path='/run/probe/file',
                    since=(AT-timedelta(seconds=1)).isoformat(),until=(AT+timedelta(seconds=1)).isoformat(),main_process=True,pid=42)
        self.assertEqual(match_probe_events([json.dumps(event)],**params)[0]['ns_pid'],1)
        for changed in ({'pid':43},{'pod_uid':'other'},{'path':'/run/probe/other'},
                        {'since':(AT+timedelta(seconds=1)).isoformat()},{'until':(AT-timedelta(seconds=1)).isoformat()}):
            self.assertEqual(match_probe_events([json.dumps(event)],**{**params,**changed}),[])


class SnapshotTests(unittest.TestCase):
    def test_stale_and_cross_context_snapshots_never_claim_current_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json'
            path.write_text(json.dumps(snapshot()))
            self.assertTrue(read_tetragon_state(path,context='kind-runtime-lab',now=AT)['fresh'])
            old=read_tetragon_state(path,now=AT+timedelta(seconds=21))
            self.assertEqual(old['state'],'stale')
            self.assertFalse(old['fresh'])
            wrong=read_tetragon_state(path,context='other-context',now=AT)
            self.assertEqual(wrong['state'],'error')
            self.assertEqual(wrong['policies'],[])
            self.assertEqual(read_tetragon_state(path,now=AT-timedelta(seconds=6))['state'],'stale')

    def test_missing_and_malformed_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json'
            self.assertEqual(read_tetragon_state(path)['state'],'not_configured')
            path.write_text('{')
            self.assertEqual(read_tetragon_state(path)['state'],'error')

    def test_transition_history_records_rollback_without_storing_commands(self):
        class Result:
            returncode=0
            stdout='ID NAME STATE NAMESPACE MODE\n7 trial enabled test monitor\n'
        def run(command): return Result()
        with tempfile.TemporaryDirectory() as directory:
            history=Path(directory)/'history.jsonl'
            result=switch_mode('ctx','test','trial','enforce',run=run,history_path=history)
            self.assertFalse(result['ok'])
            self.assertTrue(result['rollback_ok'])
            record=json.loads(history.read_text())
            self.assertEqual(record['requested_mode'],'enforce')
            self.assertTrue(record['rollback_ok'])
            self.assertNotIn('argv',record)

    def test_gateway_serves_snapshot_without_kubernetes_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=root/'tetragon-state.json'
            path.write_text(json.dumps(snapshot(datetime.now(timezone.utc))))
            policy=ROOT/'examples/mvp/policy.json'
            engine=MockEngine(port=0,receipts_path=root/'receipts.jsonl').start()
            try:
                gateway=GatewayApp(MVPConfig(gateway_port=0,engine_port=0,policy_path=str(policy),
                    data_dir=directory),load_policy(policy),engine.address).start()
                try:
                    url=f'http://{gateway.address[0]}:{gateway.address[1]}/api/tetragon-status'
                    with urlopen(url,timeout=5) as response:
                        self.assertTrue(json.load(response)['fresh'])
                    with self.assertRaises(HTTPError) as failed:
                        urlopen(Request(url,data=b'{}',method='POST'),timeout=5)
                    self.assertEqual(failed.exception.code,405)
                finally: gateway.stop()
            finally: engine.stop()

if __name__=='__main__': unittest.main()
